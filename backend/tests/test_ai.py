import json
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from omnitest.api.app import create_app
from omnitest.config import Settings


@pytest.fixture
def provider_server():
    state = {
        "reply": {
            "summary": "Fixture advice",
            "suggestions": ["Check boundary inputs"],
            "cases": [],
        },
        "requests": [],
        "status": 200,
    }
    fixture = FastAPI()

    @fixture.post("/v1/chat/completions")
    async def complete(request: Request):
        state["requests"].append(await request.json())
        from fastapi.responses import JSONResponse

        return JSONResponse(
            {"choices": [{"message": {"content": json.dumps(state["reply"])}}]},
            status_code=state["status"],
        )

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(fixture, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.time() + 5
    while not server.started:
        assert time.time() < deadline
        time.sleep(0.01)
    yield f"http://127.0.0.1:{sock.getsockname()[1]}/v1", state
    server.should_exit = True
    thread.join(timeout=5)
    sock.close()


@pytest.fixture
def ai_client(tmp_path, provider_server):
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        resources = app.state.resources
        project = resources.save("projects", {"name": "AI integration"})["id"]
        target = resources.save(
            "targets",
            {
                "name": "Fixture program",
                "kind": "cli",
                "authorized": True,
                "config": {"program": "demo-tool"},
            },
            project,
        )["id"]
        case = {
            "name": "Candidate",
            "target": target,
            "adapter": "cli",
            "steps": [
                {
                    "action": "run",
                    "config": {"args": ["echo", "hello"]},
                    "assertions": [{"path": "$.stdout", "op": "eq", "expected": "hello\n"}],
                }
            ],
        }
        existing = resources.save(
            "cases",
            {
                **case,
                "steps": [
                    {
                        "action": "run",
                        "config": {"args": ["echo", "hello"]},
                        "assertions": [{"path": "$.stdout", "op": "eq", "expected": "wrong"}],
                    }
                ],
            },
            project,
        )
        response = client.post(
            "/api/ai/providers",
            json={
                "name": "Local fixture",
                "base_url": provider_server[0],
                "model": "fixture-model",
            },
        )
        assert response.status_code == 201, response.text
        provider = response.json()["id"]
        yield client, app, project, target, case, existing["id"], provider, provider_server[1]


def enable(client, provider):
    assert (
        client.put("/api/ai/settings", json={"enabled": True, "provider_id": provider}).status_code
        == 200
    )


def wait_run(client, identity):
    for _ in range(100):
        report = client.get("/api/runs/" + identity).json()
        if report["status"] not in {"PENDING", "RUNNING"}:
            return report
        time.sleep(0.03)
    raise AssertionError("Run timed out")


@pytest.mark.parametrize(
    "action", ["design", "generate", "analyze", "coverage", "repair", "summarize"]
)
def test_six_ai_workflows_and_deterministic_verification(ai_client, action):
    client, app, project, target, case, case_id, provider, state = ai_client
    enable(client, provider)
    run = client.post("/api/runs", json={"project_id": project, "case_ids": [case_id]}).json()
    assert wait_run(client, run["id"])["status"] == "FAILED"
    if action in {"design", "generate", "repair"}:
        state["reply"]["cases"] = [case]
    before = len(app.state.resources.list("cases", project))
    result = client.post(
        "/api/ai/tasks",
        json={
            "action": action,
            "project_id": project,
            "target_id": target,
            "case_id": case_id,
            "run_id": run["id"],
            "requirement": "Validate a controlled program",
        },
    )
    assert result.status_code == 200, result.text
    assert len(app.state.resources.list("cases", project)) == before
    record = result.json()
    assert record["status"] == "PROPOSED"
    if record["cases"]:
        accepted = client.post(f"/api/ai/history/{record['id']}/accept", json={"verify": True})
        assert accepted.status_code == 200, accepted.text
        report = wait_run(client, accepted.json()["verification_run_id"])
        assert report["status"] == "PASSED"
        assert (
            client.post(f"/api/ai/history/{record['id']}/accept", json={"verify": True}).status_code
            == 422
        )
    assert client.get("/api/runs/" + run["id"]).json()["status"] == "FAILED"
    assert state["requests"][-1]["model"] == "fixture-model"
    assert client.get("/api/ai/history", params={"project_id": project}).json()


def test_disabled_missing_key_provider_failure_and_redaction(ai_client, monkeypatch):
    client, app, project, target, case, case_id, provider, state = ai_client
    task = {"action": "coverage", "project_id": project, "requirement": "secretvalue"}
    assert client.post("/api/ai/tasks", json=task).status_code == 409
    assert state["requests"] == []
    monkeypatch.setenv("OMNITEST_SECRET_FIXTURE", "secretvalue")
    enable(client, provider)
    state["reply"]["summary"] = "Observed secretvalue"
    result = client.post("/api/ai/tasks", json=task)
    assert result.status_code == 200
    assert "secretvalue" not in result.text
    assert "secretvalue" not in json.dumps(state["requests"])
    state["status"] = 401
    assert client.post("/api/ai/tasks", json=task).status_code == 422
    config = {
        "name": "Missing key",
        "base_url": app.state.ai.resources.get("ai-provider", provider).body["base_url"],
        "model": "fixture",
        "key_env": "OMNITEST_SECRET_MISSING",
    }
    assert client.put("/api/ai/providers/" + provider, json=config).status_code == 200
    assert client.post("/api/ai/tasks", json=task).status_code == 422
    assert client.delete("/api/ai/providers/" + provider).status_code == 422
    client.put("/api/ai/settings", json={"enabled": False, "provider_id": None})
    assert client.delete("/api/ai/providers/" + provider).status_code == 204
    assert client.get("/api/health").json()["ai_enabled"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"target": "outside-project"},
        {"adapter": "uninstalled"},
        {"steps": [{"action": "exec", "config": {"python": "bad"}}]},
    ],
)
def test_unsafe_candidates_rejected(ai_client, change):
    client, app, project, target, case, case_id, provider, state = ai_client
    enable(client, provider)
    state["reply"]["cases"] = [{**case, **change}]
    assert client.post(
        "/api/ai/tasks", json={"action": "generate", "project_id": project, "requirement": "test"}
    ).status_code in {404, 422}
    assert app.state.ai.rows("ai-record", project) == []


def test_configuration_persists_and_optional_extension_can_be_removed(
    ai_client, tmp_path, monkeypatch
):
    client, app, project, target, case, case_id, provider, state = ai_client
    enable(client, provider)
    from omnitest.ai.api import AIService

    restored = AIService(
        app.state.db, app.state.resources, app.state.orchestrator, Settings(data_dir=tmp_path)
    )
    assert restored.state().enabled and restored.state().provider_id == provider
    import importlib

    app_module = importlib.import_module("omnitest.api.app")
    monkeypatch.setattr(app_module, "entry_points", lambda **_: [])
    with TestClient(create_app(Settings(data_dir=tmp_path / "without-ai"))) as core:
        assert core.get("/api/health").json()["status"] == "ok"
        assert core.get("/api/health").json()["ai_enabled"] is False
        assert core.get("/api/adapters").status_code == 200
