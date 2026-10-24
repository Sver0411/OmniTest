import json

import pytest
from fastapi.testclient import TestClient

from omnitest.api.app import create_app
from omnitest.config import Settings
from omnitest.core.ir import RunRequest
from omnitest.demo import seed_demo
from omnitest.services.access import AccessControl


@pytest.fixture
def secured(tmp_path, monkeypatch, fixture_url):
    profiles = []
    for role in ["admin", "editor", "operator", "viewer"]:
        name = "OMNITEST_ACCESS_" + role.upper()
        monkeypatch.setenv(name, role + "-credential-value-12345")
        profiles.append({"name": role, "role": role, "token_env": name})
    settings = Settings(data_dir=tmp_path, access_tokens=profiles)
    # Seed before assembling project-scoped credentials.
    bootstrap = create_app(Settings(data_dir=tmp_path))
    with TestClient(bootstrap):
        demo = seed_demo(bootstrap.state.resources, fixture_url)
        other = bootstrap.state.resources.save("projects", {"name": "Other"})["id"]
        target = bootstrap.state.resources.save(
            "targets",
            {"name": "Private target", "kind": "http", "config": {"base_url": fixture_url}},
            other,
        )["id"]
        run = bootstrap.state.orchestrator.submit(
            RunRequest(project_id=demo["project_id"], suite_id=demo["file_suite_id"])
        )
    for profile in profiles:
        if profile["role"] != "admin":
            profile["project_ids"] = [demo["project_id"]]
    app = create_app(settings)
    with TestClient(app) as client:
        yield client, app, demo, other, target, run["id"]


def headers(role):
    return {"Authorization": f"Bearer {role}-credential-value-12345"}


def test_project_scope_covers_lists_details_dashboard_and_internal_resources(secured):
    client, _, demo, other, target, _ = secured
    h = headers("viewer")
    assert client.get("/api/resources/projects", headers=h).json()[0]["id"] == demo["project_id"]
    assert len(client.get("/api/resources/projects", headers=h).json()) == 1
    assert client.get("/api/dashboard", headers=h).json()["projects"] == 1
    assert client.get(f"/api/resources/projects/{other}", headers=h).status_code == 403
    assert client.get(f"/api/resources/targets/{target}", headers=h).status_code == 403
    assert client.get(f"/api/resources/targets?project_id={other}", headers=h).status_code == 403
    assert all(
        r["project_id"] == demo["project_id"]
        for r in client.get("/api/resources/targets", headers=h).json()
    )
    assert client.get("/api/resources/ai-provider/anything", headers=h).status_code == 403
    assert client.get("/api/ai/providers", headers=h).status_code == 403
    assert client.get("/api/operations", headers=h).status_code == 403
    assert client.get("/api/session", headers=h).json()["role"] == "viewer"


def test_roles_edit_execute_and_cross_project_guards(secured):
    client, _, demo, other, _, _ = secured
    request = {"project_id": demo["project_id"], "suite_id": demo["file_suite_id"]}
    assert client.post("/api/runs", headers=headers("viewer"), json=request).status_code == 403
    assert client.post("/api/runs", headers=headers("operator"), json=request).status_code == 202
    body = {"project_id": demo["project_id"], "body": {"name": "Added", "variables": {}}}
    assert (
        client.post(
            "/api/resources/environments", headers=headers("operator"), json=body
        ).status_code
        == 403
    )
    saved = client.post("/api/resources/environments", headers=headers("editor"), json=body)
    assert saved.status_code == 201, saved.text
    assert (
        client.delete(
            "/api/resources/environments/" + saved.json()["id"], headers=headers("editor")
        ).status_code
        == 204
    )
    body["project_id"] = other
    assert (
        client.post("/api/resources/environments", headers=headers("editor"), json=body).status_code
        == 403
    )
    assert (
        client.post(
            "/api/resources/projects", headers=headers("editor"), json={"body": {"name": "No"}}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/cases/import",
            headers=headers("viewer"),
            json={"project_id": demo["project_id"], "cases": []},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/cases/import", headers=headers("editor"), json={"project_id": other, "cases": []}
        ).status_code
        == 403
    )


def test_report_export_cancel_and_evidence_scope(secured):
    client, app, demo, other, _, run_id = secured
    with app.state.db.session.begin() as session:
        from omnitest.db import Run

        run = session.get(Run, run_id)
        run.project_id = other
    h = headers("operator")
    for path in [
        f"/api/runs/{run_id}",
        f"/api/runs/{run_id}/export",
        f"/api/evidence/{run_id}/x/1/x.png",
    ]:
        assert client.get(path, headers=h).status_code == 403
    assert client.post(f"/api/runs/{run_id}/cancel", headers=h).status_code == 403
    assert all(
        row["project_id"] == demo["project_id"] for row in client.get("/api/runs", headers=h).json()
    )


def test_audit_is_persistent_and_never_contains_bodies_headers_or_query_values(secured):
    client, _, demo, _, _, _ = secured
    result = client.post(
        "/api/resources/environments?private=do-not-log",
        headers=headers("editor"),
        json={
            "project_id": demo["project_id"],
            "body": {"name": "Audit", "variables": {"secret": "private-body-value"}},
        },
    )
    assert result.status_code == 201
    assert result.headers["X-Request-ID"]
    client.post("/api/runs", headers=headers("viewer"), json={})
    rows = client.get("/api/audit", headers=headers("admin")).json()
    event = next(row for row in rows if row["request_id"] == result.headers["X-Request-ID"])
    assert event["actor"] == "editor" and event["project_id"] == demo["project_id"]
    assert any(row["status_code"] == 403 and row["actor"] == "viewer" for row in rows)
    serialized = json.dumps(rows)
    assert all(
        value not in serialized
        for value in ["do-not-log", "private-body-value", "credential-value"]
    )
    assert client.get("/api/audit", headers=headers("editor")).status_code == 403
    assert client.get("/api/ready").json()["status"] == "ready"


def test_missing_disabled_and_duplicate_tokens_fail_closed(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path,
        access_tokens=[{"name": "viewer", "role": "viewer", "token_env": "OMNITEST_ACCESS_VIEWER"}],
    )
    with pytest.raises(ValueError, match="environment token"):
        AccessControl(settings, None)
    monkeypatch.setenv("OMNITEST_ACCESS_VIEWER", "credential-value-123456")
    settings.access_tokens[0]["enabled"] = False
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/resources/projects").status_code == 401
    settings.access_tokens[0]["enabled"] = True
    settings.access_tokens.append(
        {"name": "other", "role": "operator", "token_env": "OMNITEST_ACCESS_VIEWER"}
    )
    with pytest.raises(ValueError, match="same token"):
        AccessControl(settings, None)


def test_execution_cannot_resolve_access_tokens_or_other_project_secrets(secured, monkeypatch):
    import time

    from omnitest.core.ir import TestIR
    from omnitest.core.security import Redactor, render

    client, app, demo, _, _, _ = secured
    monkeypatch.setenv("OMNITEST_SECRET_ALPHA", "project-alpha-private-value")
    monkeypatch.setenv("OMNITEST_SECRET_BETA", "project-beta-private-value")
    app.state.orchestrator.settings.project_secrets[demo["project_id"]] = ["OMNITEST_SECRET_ALPHA"]
    target = app.state.resources.list("targets", demo["project_id"])[0]["id"]
    for secret, expected in [
        ("OMNITEST_SECRET_ALPHA", "PASSED"),
        ("OMNITEST_SECRET_BETA", "ERROR"),
        ("OMNITEST_ACCESS_ADMIN", "ERROR"),
    ]:
        body = TestIR(
            name="Scoped credential",
            adapter="api",
            target=target,
            steps=[
                {
                    "action": "request",
                    "config": {
                        "path": "/health",
                        "headers": {"X-Check": "${secret." + secret + "}"},
                    },
                }
            ],
        ).model_dump()
        case = app.state.resources.save("cases", body, demo["project_id"])["id"]
        run = client.post(
            "/api/runs",
            headers=headers("operator"),
            json={"project_id": demo["project_id"], "case_ids": [case]},
        ).json()["id"]
        for _ in range(200):
            report = client.get("/api/runs/" + run, headers=headers("viewer")).json()
            if report["status"] not in {"PENDING", "RUNNING"}:
                break
            time.sleep(0.02)
        assert report["status"] == expected, report
        assert "project-beta-private-value" not in json.dumps(report)
        assert "admin-credential-value-12345" not in json.dumps(report)
    with pytest.raises(ValueError, match="prefix"):
        render("${secret.OMNITEST_ACCESS_ADMIN}", {})
    assert "admin-credential-value-12345" not in Redactor().clean("admin-credential-value-12345")
