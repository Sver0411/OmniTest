import base64
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from omnitest.api.app import create_app
from omnitest.config import Settings
from omnitest.core.ir import RunRequest
from omnitest.db import Database, NodeLease
from omnitest.services.nodes import Inventory, NodeService
from omnitest.worker import Worker

ADMIN = {"Authorization": "Bearer management-credential-value-12345"}
ALPHA = {"Authorization": "Bearer alpha-node-credential-value-12345678"}
BETA = {"Authorization": "Bearer beta-node-credential-value-123456789"}
INVENTORY = {"version": "test", "os": "test", "python": "3.12", "adapters": {"api": "1"}}


@pytest.fixture
def nodes(tmp_path, monkeypatch, fixture_url):
    monkeypatch.setenv("OMNITEST_NODE_ALPHA", ALPHA["Authorization"][7:])
    monkeypatch.setenv("OMNITEST_NODE_BETA", BETA["Authorization"][7:])
    settings = Settings(
        data_dir=tmp_path,
        api_token=ADMIN["Authorization"][7:],
        remote_nodes=[
            {
                "id": identity,
                "token_env": "OMNITEST_NODE_" + identity.upper(),
                "project_ids": None,
                "adapters": ["api"],
            }
            for identity in ["alpha", "beta"]
        ],
    )
    app = create_app(settings)
    with TestClient(app) as client:
        resources = app.state.resources
        project = resources.save("projects", {"name": "Allowed"})["id"]
        other = resources.save("projects", {"name": "Other"})["id"]
        target = resources.save(
            "targets",
            {
                "name": "Fixture",
                "kind": "http",
                "authorized": True,
                "config": {"base_url": fixture_url},
            },
            project,
        )["id"]
        case = resources.save(
            "cases",
            {
                "name": "Health",
                "adapter": "api",
                "target": target,
                "steps": [
                    {
                        "action": "request",
                        "config": {"path": "/health"},
                        "assertions": [{"path": "$.status_code", "op": "eq", "expected": 200}],
                    }
                ],
            },
            project,
        )["id"]
        app.state.orchestrator.nodes.configs["alpha"].project_ids = [project]
        app.state.orchestrator.nodes.configs["beta"].project_ids = [other]
        yield client, app, project, other, case, settings


def submit(nodes, node_id="alpha"):
    client, _, project, _, case, _ = nodes
    response = client.post(
        "/api/runs",
        headers=ADMIN,
        json={"project_id": project, "case_ids": [case], "node_id": node_id},
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


def claim(nodes):
    response = nodes[0].post("/api/worker/claim", headers=ALPHA, json=INVENTORY)
    assert response.status_code == 200, response.text
    return response.json()["assignment"]


def result_url(assignment):
    return f"/api/worker/runs/{assignment['run_id']}/results/{assignment['snapshot']['cases'][0]['result_id']}"


def outcome(assignment):
    return {
        "claim_id": assignment["claim_id"],
        "status": "PASSED",
        "duration": 0.1,
        "detail": {"attempts": []},
    }


def test_management_and_worker_credentials_are_separate(nodes):
    client, _, project, other, _, _ = nodes
    assert client.get("/api/resources/projects", headers=ALPHA).status_code == 401
    assert client.post("/api/worker/claim", headers=ADMIN, json=INVENTORY).status_code == 401
    assert client.post("/api/worker/claim", json=INVENTORY).status_code == 401
    assert {
        n["id"] for n in client.get(f"/api/nodes?project_id={project}", headers=ADMIN).json()
    } == {"alpha"}
    assert {
        n["id"] for n in client.get(f"/api/nodes?project_id={other}", headers=ADMIN).json()
    } == {"beta"}
    assert "token_env" not in client.get("/api/nodes", headers=ADMIN).text
    assert (
        client.post(
            "/api/runs",
            headers=ADMIN,
            json={"project_id": project, "case_ids": [nodes[4]], "node_id": "beta"},
        ).status_code
        == 422
    )


def test_one_claim_per_node_under_concurrent_pollers(nodes):
    first, second = submit(nodes), submit(nodes)
    service = nodes[1].state.orchestrator.nodes
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(lambda _: service.claim("alpha", Inventory(**INVENTORY)), range(4)))
    assignments = [reply["assignment"] for reply in replies if reply["assignment"]]
    assert len(assignments) == 1 and assignments[0]["run_id"] == first
    assert nodes[0].get(f"/api/runs/{second}", headers=ADMIN).json()["status"] == "PENDING"


def test_missing_adapter_does_not_claim_and_local_queue_is_not_starved(nodes):
    for _ in range(9):
        submit(nodes)
    client = nodes[0]
    assert (
        client.post("/api/worker/claim", headers=ALPHA, json={**INVENTORY, "adapters": {}}).json()[
            "assignment"
        ]
        is None
    )
    identity = submit(nodes, None)
    deadline = time.time() + 10
    while time.time() < deadline:
        report = client.get(f"/api/runs/{identity}", headers=ADMIN).json()
        if report["status"] == "PASSED":
            break
        time.sleep(0.05)
    assert report["status"] == "PASSED", report


def test_lease_fences_foreign_stale_and_duplicate_results(nodes):
    client = nodes[0]
    submit(nodes)
    assignment = claim(nodes)
    url, value = result_url(assignment), outcome(assignment)
    assert client.post(url, headers=BETA, json=value).status_code == 404
    assert client.post(url, headers=ALPHA, json={**value, "claim_id": "x" * 40}).status_code == 409
    assert client.post(url, headers=ALPHA, json={**value, "status": "RUNNING"}).status_code == 422
    assert client.post(url, headers=ALPHA, json=value).status_code == 200
    assert client.post(url, headers=ALPHA, json=value).status_code == 200
    assert client.post(url, headers=ALPHA, json={**value, "status": "FAILED"}).status_code == 409
    prefix = f"/api/worker/runs/{assignment['run_id']}"
    assert (
        client.post(
            prefix + "/finish", headers=ALPHA, json={"claim_id": assignment["claim_id"]}
        ).status_code
        == 200
    )
    assert (
        client.get(f"/api/runs/{assignment['run_id']}", headers=ADMIN).json()["status"] == "PASSED"
    )
    assert client.post(url, headers=ALPHA, json=value).status_code == 409


@pytest.mark.parametrize(
    "relative",
    [
        "../escape.txt",
        "1/../escape.txt",
        "1/C:foo",
        "1/a\\b",
        "1/NUL.txt",
        "1/end.",
        "5/screen.png",
    ],
)
def test_remote_evidence_paths_are_portable_and_contained(nodes, relative):
    submit(nodes)
    assignment = claim(nodes)
    url = result_url(assignment).replace("/results/", "/evidence/")
    value = {
        "claim_id": assignment["claim_id"],
        "path": relative,
        "sha256": hashlib.sha256(b"test").hexdigest(),
        "content": base64.b64encode(b"test").decode(),
    }
    assert nodes[0].post(url, headers=ALPHA, json=value).status_code == 422


def test_evidence_hash_manifest_quota_and_immutable_upload(nodes, monkeypatch):
    client = nodes[0]
    submit(nodes)
    assignment = claim(nodes)
    url = result_url(assignment).replace("/results/", "/evidence/")
    value = {
        "claim_id": assignment["claim_id"],
        "path": "1/log.txt",
        "content": base64.b64encode(b"test").decode(),
        "sha256": hashlib.sha256(b"test").hexdigest(),
    }
    result = outcome(assignment)
    result["detail"] = {"attempts": [{"evidence": ["1/log.txt"]}]}
    assert client.post(result_url(assignment), headers=ALPHA, json=result).status_code == 422
    assert client.post(url, headers=ALPHA, json={**value, "sha256": "0" * 64}).status_code == 422
    assert client.post(url, headers=BETA, json=value).status_code == 404
    assert client.post(url, headers=ALPHA, json=value).status_code == 200
    assert client.post(url, headers=ALPHA, json=value).status_code == 200
    changed = {
        **value,
        "content": base64.b64encode(b"different").decode(),
        "sha256": hashlib.sha256(b"different").hexdigest(),
    }
    assert client.post(url, headers=ALPHA, json=changed).status_code == 409
    monkeypatch.setattr("omnitest.services.nodes.MAX_EVIDENCE_COUNT", 1)
    assert (
        client.post(url, headers=ALPHA, json={**value, "path": "1/second.txt"}).status_code == 422
    )
    assert client.post(result_url(assignment), headers=ALPHA, json=result).status_code == 200
    assert client.post(url, headers=ALPHA, json=value).status_code == 409


def test_cancellation_reaches_node_and_pending_run_never_claims(nodes):
    client = nodes[0]
    pending = submit(nodes)
    client.post(f"/api/runs/{pending}/cancel", headers=ADMIN)
    assert claim(nodes) is None
    running = submit(nodes)
    assignment = claim(nodes)
    client.post(f"/api/runs/{running}/cancel", headers=ADMIN)
    assert client.get(f"/api/runs/{running}", headers=ADMIN).json()["cancel_requested"]
    prefix = f"/api/worker/runs/{running}"
    assert client.post(
        prefix + "/heartbeat", headers=ALPHA, json={"claim_id": assignment["claim_id"]}
    ).json()["cancel_requested"]
    assert (
        client.post(
            prefix + "/finish", headers=ALPHA, json={"claim_id": assignment["claim_id"]}
        ).status_code
        == 200
    )
    assert client.get(f"/api/runs/{running}", headers=ADMIN).json()["status"] == "CANCELLED"


def test_lost_node_is_error_without_replay_and_late_result_is_rejected(nodes):
    client, app, *_ = nodes
    identity = submit(nodes)
    assignment = claim(nodes)
    with app.state.db.session.begin() as session:
        session.get(NodeLease, identity).heartbeat_at = time.time() - 60
    app.state.orchestrator.nodes.expire()
    report = client.get(f"/api/runs/{identity}", headers=ADMIN).json()
    assert report["status"] == "ERROR" and report["summary"]["counts"] == {"ERROR": 1}
    assert "not replayed" in report["summary"]["error"]
    assert claim(nodes) is None
    assert (
        client.post(result_url(assignment), headers=ALPHA, json=outcome(assignment)).status_code
        == 409
    )


def test_project_and_adapter_revocation_fail_queued_runs(nodes):
    identity = submit(nodes)
    nodes[1].state.orchestrator.nodes.configs["alpha"].adapters = ["web"]
    nodes[1].state.orchestrator.nodes.expire()
    assert nodes[0].get(f"/api/runs/{identity}", headers=ADMIN).json()["status"] == "ERROR"


def test_remote_schedule_preserves_assignment_and_snapshot(nodes):
    _, app, project, _, case, _ = nodes
    resources = app.state.resources
    suite = resources.save("suites", {"name": "Scheduled", "case_ids": [case]}, project)["id"]
    resources.save(
        "schedules",
        {
            "name": "Node schedule",
            "suite_id": suite,
            "node_id": "alpha",
            "interval_seconds": 60,
            "next_run": 0,
        },
        project,
    )
    app.state.orchestrator.tick_schedules()
    assignment = claim(nodes)
    assert assignment["snapshot"]["node_id"] == "alpha"
    assert assignment["snapshot"]["source"].startswith("schedule:")
    with pytest.raises(ValueError, match="unavailable"):
        resources.save(
            "schedules",
            {"name": "Wrong node", "suite_id": suite, "node_id": "beta", "interval_seconds": 60},
            project,
        )


def test_revoked_node_schedule_is_disabled_without_blocking_other_schedules(nodes):
    _, app, project, _, case, _ = nodes
    resources = app.state.resources
    suite = resources.save("suites", {"name": "Scheduled", "case_ids": [case]}, project)["id"]
    invalid = resources.save(
        "schedules",
        {
            "name": "Revoked",
            "suite_id": suite,
            "node_id": "alpha",
            "interval_seconds": 60,
            "next_run": time.time() + 60,
        },
        project,
    )["id"]
    local = resources.save(
        "schedules",
        {"name": "Local", "suite_id": suite, "interval_seconds": 60, "next_run": time.time() + 60},
        project,
    )["id"]
    from omnitest.db import Resource

    with app.state.db.session.begin() as session:
        for identity in [invalid, local]:
            row = session.get(Resource, identity)
            row.body = {**row.body, "next_run": 0}
    app.state.orchestrator.nodes.configs["alpha"].adapters = ["web"]
    app.state.orchestrator.tick_schedules()
    cfg = resources.get("schedules", invalid).body
    assert cfg["enabled"] is False and "adapters" in cfg["last_error"]
    assert resources.get("schedules", local).body["next_run"] > time.time()


def test_remote_secret_names_only_and_double_allowlist(nodes, monkeypatch):
    _, app, project, _, _, settings = nodes
    monkeypatch.setenv("OMNITEST_SECRET_NODE_ONLY", "private-node-test-value")
    settings.project_secrets[project] = ["OMNITEST_SECRET_NODE_ONLY"]
    submit(nodes)
    assignment = claim(nodes)
    assert assignment["allowed_secrets"] == ["OMNITEST_SECRET_NODE_ONLY"]
    assert "private-node-test-value" not in str(assignment)
    assert settings.remote_nodes
    assert app.state.orchestrator.nodes.tokens


def test_node_credentials_fail_closed_and_coordinator_requires_https(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="management API"):
        Settings(data_dir=tmp_path, remote_nodes=[{}], api_token="")
    settings = Settings(
        data_dir=tmp_path,
        api_token="management",
        remote_nodes=[{"id": "n", "token_env": "OMNITEST_NODE_N", "adapters": ["api"]}],
    )
    db = Database(settings.database_url)
    with pytest.raises(ValueError, match="32 characters"):
        NodeService(db, settings)
    monkeypatch.setenv("OMNITEST_NODE_N", "n" * 40)
    with pytest.raises(ValueError, match="HTTPS"):
        Worker(settings, "http://remote.example", "OMNITEST_NODE_N")
    with pytest.raises(ValueError, match="origin"):
        Worker(settings, "https://user:pass@example.com", "OMNITEST_NODE_N")
    settings.api_token = "n" * 40
    with pytest.raises(ValueError, match="separate"):
        create_app(settings)
    db.engine.dispose()


async def test_remote_runs_cannot_be_executed_by_local_orchestrator(nodes):
    identity = submit(nodes)
    with pytest.raises(ValueError, match="claimed"):
        await nodes[1].state.orchestrator.execute(identity)


async def test_worker_requires_both_project_secret_allowlists(nodes, tmp_path, monkeypatch):
    submit(nodes)
    assignment = claim(nodes)
    project = nodes[2]
    name = "OMNITEST_SECRET_NODE_TEST"
    secret_value = "node-side-test-secret-value"
    monkeypatch.setenv(name, secret_value)
    assignment["allowed_secrets"] = [name]
    assignment["snapshot"]["cases"][0]["case"]["steps"][0]["config"]["headers"] = {
        "x-test": "${secret." + name + "}"
    }
    settings = Settings(data_dir=tmp_path / "isolated-worker")
    worker = Worker(settings, "http://127.0.0.1:8000", "OMNITEST_NODE_ALPHA")
    outcomes = []

    def respond(request):
        if "/results/" in request.url.path:
            import json

            outcomes.append(json.loads(request.content))
        return httpx.Response(200, json={"cancel_requested": False})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://127.0.0.1:8000"
    ) as client:
        await worker.execute(client, assignment)
        assert outcomes[-1]["status"] == "ERROR" and "not authorized" in str(outcomes[-1])
        settings.project_secrets[project] = [name]
        await worker.execute(client, assignment)
        assert outcomes[-1]["status"] == "PASSED"
        assert secret_value not in str(outcomes)


def test_restart_rejects_inflight_worker_without_replay(tmp_path, monkeypatch, fixture_url):
    monkeypatch.setenv("OMNITEST_NODE_ALPHA", ALPHA["Authorization"][7:])
    settings = Settings(
        data_dir=tmp_path,
        api_token=ADMIN["Authorization"][7:],
        remote_nodes=[
            {
                "id": "alpha",
                "token_env": "OMNITEST_NODE_ALPHA",
                "project_ids": None,
                "adapters": ["api"],
            }
        ],
    )
    first = create_app(settings)
    with TestClient(first) as client:
        resources = first.state.resources
        project = resources.save("projects", {"name": "Restart"})["id"]
        target = resources.save(
            "targets",
            {
                "name": "Fixture",
                "kind": "http",
                "authorized": True,
                "config": {"base_url": fixture_url},
            },
            project,
        )["id"]
        case = resources.save(
            "cases",
            {
                "name": "Health",
                "adapter": "api",
                "target": target,
                "steps": [{"action": "request", "config": {"path": "/health"}}],
            },
            project,
        )["id"]
        identity = first.state.orchestrator.submit(
            RunRequest(project_id=project, case_ids=[case], node_id="alpha")
        )["id"]
        assignment = client.post("/api/worker/claim", headers=ALPHA, json=INVENTORY).json()[
            "assignment"
        ]
    with TestClient(create_app(settings)) as client:
        report = client.get(f"/api/runs/{identity}", headers=ADMIN).json()
        assert report["status"] == "ERROR" and "interrupted" in report["summary"]["error"].lower()
        assert (
            client.post(result_url(assignment), headers=ALPHA, json=outcome(assignment)).status_code
            == 409
        )
        assert (
            client.post("/api/worker/claim", headers=ALPHA, json=INVENTORY).json()["assignment"]
            is None
        )
