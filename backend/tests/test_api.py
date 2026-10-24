import asyncio
import time
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient
from omnitest.api.app import create_app
from omnitest.config import Settings
from omnitest.core.ir import RunRequest
from omnitest.db import Database, Run
from omnitest.demo import seed_demo
from omnitest.services.orchestrator import Orchestrator


def wait_report(client, identity):
    for _ in range(300):
        response = client.get(f"/api/runs/{identity}")
        assert response.status_code == 200
        report = response.json()
        if report["status"] not in {"PENDING", "RUNNING"}:
            return report
        time.sleep(0.03)
    raise AssertionError("Run did not finish")


def test_api_crud_execution_exports_and_restart(tmp_path, fixture_url):
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        demo = seed_demo(app.state.resources, fixture_url)
        assert client.get("/api/health").json()["ai_enabled"] is False
        assert client.get("/api/dashboard").json()["pass_rate"] is None
        cases = client.get("/api/resources/cases", params={"project_id": demo["project_id"]}).json()
        assert len(cases) == 24
        request = {"project_id": demo["project_id"], "suite_id": demo["file_suite_id"]}
        submitted = client.post("/api/runs", json=request)
        assert submitted.status_code == 202
        identity = submitted.json()["id"]
        assert wait_report(client, identity)["status"] == "PASSED"
        assert client.get("/api/runs", params={"adapter": "filecheck"}).json()[0]["id"] == identity
        xml = client.get(f"/api/runs/{identity}/export?format=junit")
        assert len(ET.fromstring(xml.content).findall("testcase")) == 1
        assert client.get(f"/api/runs/{identity}/export").json()["status"] == "PASSED"
        case = cases[0]
        case["body"]["name"] += " edited"
        assert client.put("/api/resources/cases/" + case["id"], json=case).status_code == 422
        assert (
            client.put(
                "/api/resources/cases/" + case["id"],
                json={"project_id": demo["project_id"], "body": case["body"]},
            ).status_code
            == 200
        )
        invalid = {**case["body"], "target": "missing"}
        before = len(app.state.resources.list("cases"))
        assert (
            client.post(
                "/api/cases/import",
                json={"project_id": demo["project_id"], "cases": [case["body"], invalid]},
            ).status_code
            == 404
        )
        assert len(app.state.resources.list("cases")) == before
        assert client.delete("/api/resources/targets/" + case["body"]["target"]).status_code == 422
        imported = client.post(
            "/api/cases/import", json={"project_id": demo["project_id"], "cases": [case["body"]]}
        )
        assert imported.status_code == 201
        copied = imported.json()[0]
        assert copied["id"] != case["id"]
        assert client.delete("/api/resources/cases/" + copied["id"]).status_code == 204
        assert client.get("/api/runs/not-found").status_code == 404
        assert client.post("/api/resources/projects", json={}).status_code == 422
        assert client.get("/api/evidence/no/no/1/no.txt").status_code == 404
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f"/api/runs/{identity}").json()["status"] == "PASSED"
        assert len(restarted.get("/api/resources/cases").json()) == 24


def test_access_boundary(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, api_token="test-token"))) as client:
        assert client.get("/api/settings").status_code == 401
        headers = {"Authorization": "Bearer test-token"}
        assert client.get("/api/settings", headers=headers).status_code == 200
        assert (
            client.post(
                "/api/runs", headers={**headers, "Origin": "https://evil.test"}, json={}
            ).status_code
            == 403
        )
        assert client.post("/api/runs", headers=headers, json={}).status_code == 422


async def test_schedule_recovers_after_database_reopen(stack, fixture_url):
    settings, db, registry, resources, orchestrator = stack
    demo = seed_demo(resources, fixture_url)
    schedule = resources.save(
        "schedules",
        {
            "name": "Persistent schedule",
            "suite_id": demo["file_suite_id"],
            "interval_seconds": 5,
            "next_run": time.time() - 1,
        },
        demo["project_id"],
    )
    reopened = Database(settings.database_url)
    restored = Orchestrator(reopened, resources, settings, registry)
    await restored.start()
    try:
        for _ in range(200):
            with reopened.session() as s:
                runs = list(s.query(Run).all())
            if runs and runs[0].status == "PASSED":
                break
            await asyncio.sleep(0.02)
        assert len(runs) == 1 and runs[0].status == "PASSED"
        assert restored.report(runs[0].id)["source"] == f"schedule:{schedule['id']}"
        assert resources.get("schedules", schedule["id"]).body["next_run"] > time.time()
    finally:
        await restored.stop()
        reopened.engine.dispose()


async def test_interrupted_run_is_not_replayed(stack, fixture_url):
    demo = seed_demo(stack[3], fixture_url)
    row = stack[4].submit(RunRequest(project_id=demo["project_id"], suite_id=demo["file_suite_id"]))
    with stack[1].session.begin() as s:
        s.get(Run, row["id"]).status = "RUNNING"
    await stack[4].start()
    try:
        report = stack[4].report(row["id"])
        assert report["status"] == "ERROR"
        assert "interrupted" in report["summary"]["error"]
    finally:
        await stack[4].stop()
