import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from omnitest.api.app import create_app
from omnitest.config import Settings
from omnitest.core.security import Redactor
from omnitest.db import Member
from omnitest.services.backup import backup, restore

ADMIN = {"Authorization": "Bearer bootstrap-fixture-credential"}


def headers(token):
    return {"Authorization": "Bearer " + token}


@pytest.fixture
def team_stack(tmp_path, fixture_url):
    settings = Settings(data_dir=tmp_path, api_token=ADMIN["Authorization"][7:])
    app = create_app(settings)
    with TestClient(app) as client:
        projects, cases = [], []
        for name in ["Alpha", "Beta"]:
            project = app.state.resources.save("projects", {"name": name})["id"]
            target = app.state.resources.save(
                "targets",
                {
                    "name": name,
                    "kind": "http",
                    "authorized": True,
                    "config": {"base_url": fixture_url},
                },
                project,
            )["id"]
            case = app.state.resources.save(
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
            projects.append(project)
            cases.append(case)
        yield client, app, projects, cases


def create_member(client, **body):
    response = client.post(
        "/api/collaboration/members", headers=ADMIN, json={"username": "alice", **body}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_project_specific_roles_cannot_leak_editor_permissions(team_stack):
    client, _, (alpha, beta), cases = team_stack
    user = create_member(client, project_roles={alpha: "viewer", beta: "editor"})
    h = headers(user["token"])
    assert client.get(f"/api/settings?project_id={alpha}", headers=h).json()["principal"][
        "permissions"
    ] == {"admin": False, "manage": False, "execute": False}
    assert client.get(f"/api/settings?project_id={beta}", headers=h).json()["principal"][
        "permissions"
    ]["manage"]
    assert (
        client.post(
            "/api/runs", headers=h, json={"project_id": alpha, "case_ids": [cases[0]]}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/runs", headers=h, json={"project_id": beta, "case_ids": [cases[1]]}
        ).status_code
        == 202
    )
    for project, code in [(alpha, 403), (beta, 201)]:
        response = client.post(
            "/api/resources/environments",
            headers=h,
            json={"project_id": project, "body": {"name": "Saved", "variables": {}}},
        )
        assert response.status_code == code, response.text
    assert client.get("/api/collaboration", headers=h).status_code == 403
    assert client.get("/api/operations", headers=h).status_code == 403
    assert (
        client.post(
            "/api/collaboration/members",
            headers=h,
            json={"username": "escalation", "is_admin": True},
        ).status_code
        == 403
    )


def test_team_inheritance_disable_and_membership_removal_take_effect_immediately(team_stack):
    client, _, (alpha, beta), cases = team_stack
    user = create_member(client, project_roles={alpha: "viewer"})
    h = headers(user["token"])
    team = {
        "name": "QA",
        "member_ids": [user["member"]["id"]],
        "project_roles": {alpha: "operator", beta: "editor"},
    }
    response = client.post("/api/collaboration/teams", headers=ADMIN, json=team)
    assert response.status_code == 201, response.text
    identity = response.json()["id"]
    assert client.get(f"/api/session?project_id={alpha}", headers=h).json()["role"] == "operator"
    assert client.get(f"/api/session?project_id={beta}", headers=h).json()["role"] == "editor"
    response = client.put(
        f"/api/collaboration/teams/{identity}", headers=ADMIN, json={**team, "enabled": False}
    )
    assert response.status_code == 200
    assert client.get(f"/api/session?project_id={alpha}", headers=h).json()["role"] == "viewer"
    assert client.get(f"/api/session?project_id={beta}", headers=h).status_code == 403
    assert (
        client.post(
            "/api/runs", headers=h, json={"project_id": alpha, "case_ids": [cases[0]]}
        ).status_code
        == 403
    )
    assert client.delete(f"/api/collaboration/teams/{identity}", headers=ADMIN).status_code == 204
    assert client.get("/api/collaboration", headers=ADMIN).json()["teams"] == []


def test_token_hash_rotation_and_disabled_accounts(team_stack):
    client, app, (alpha, _), _ = team_stack
    user = create_member(client, project_roles={alpha: "viewer"})
    token, identity = user["token"], user["member"]["id"]
    with app.state.db.session() as session:
        row = session.get(Member, identity)
        assert row.token_digest == hashlib.sha256(token.encode()).hexdigest()
    listing = client.get("/api/collaboration", headers=ADMIN).text
    assert token not in listing and "token_digest" not in listing
    assert Redactor().clean("credential=" + token) == "credential=[REDACTED]"
    rotated = client.post(f"/api/collaboration/members/{identity}/rotate", headers=ADMIN)
    assert rotated.status_code == 200
    new_token = rotated.json()["token"]
    assert client.get("/api/session", headers=headers(token)).status_code == 401
    assert client.get("/api/session", headers=headers(new_token)).status_code == 200
    assert (
        client.put(
            f"/api/collaboration/members/{identity}",
            headers=ADMIN,
            json={"username": "alice", "enabled": False, "project_roles": {alpha: "viewer"}},
        ).status_code
        == 200
    )
    assert client.get("/api/session", headers=headers(new_token)).status_code == 401
    assert token not in client.get("/api/audit", headers=ADMIN).text


def test_member_admin_is_global_but_ordinary_members_cannot_modify_projects(team_stack):
    client, _, (alpha, _), _ = team_stack
    user = create_member(client, project_roles={alpha: "editor"})
    assert (
        client.post(
            "/api/resources/projects",
            headers=headers(user["token"]),
            json={"body": {"name": "Denied"}},
        ).status_code
        == 403
    )
    elevated = client.put(
        f"/api/collaboration/members/{user['member']['id']}",
        headers=ADMIN,
        json={"username": "alice", "is_admin": True},
    )
    assert elevated.status_code == 200
    assert client.get("/api/operations", headers=headers(user["token"])).status_code == 200
    assert (
        client.post(
            "/api/resources/projects",
            headers=headers(user["token"]),
            json={"body": {"name": "Allowed"}},
        ).status_code
        == 201
    )


def test_invalid_grants_and_duplicate_names_are_transactional(team_stack):
    client, _, _, _ = team_stack
    user = create_member(client)
    assert (
        client.post(
            "/api/collaboration/members", headers=ADMIN, json={"username": "ALICE"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/collaboration/members",
            headers=ADMIN,
            json={"username": "bob", "project_roles": {"unknown": "editor"}},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/collaboration/teams",
            headers=ADMIN,
            json={"name": "Invalid", "member_ids": [user["member"]["id"], "unknown"]},
        ).status_code
        == 422
    )
    assert len(client.get("/api/collaboration", headers=ADMIN).json()["members"]) == 1
    assert client.get("/api/collaboration", headers=ADMIN).json()["teams"] == []


def test_project_grants_block_project_deletion_and_empty_scope_is_empty(team_stack):
    client, app, _, _ = team_stack
    project = app.state.resources.save("projects", {"name": "Granted"})["id"]
    user = create_member(client, project_roles={project: "viewer"})
    assert client.delete(f"/api/resources/projects/{project}", headers=ADMIN).status_code == 422
    assert (
        client.put(
            f"/api/collaboration/members/{user['member']['id']}",
            headers=ADMIN,
            json={"username": "alice"},
        ).status_code
        == 200
    )
    assert client.get("/api/resources/projects", headers=headers(user["token"])).json() == []
    assert client.delete(f"/api/resources/projects/{project}", headers=ADMIN).status_code == 204


def test_local_mode_cannot_enable_members_and_removing_bootstrap_fails_closed(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        assert (
            client.post("/api/collaboration/members", json={"username": "alice"}).status_code == 422
        )
    with TestClient(
        create_app(Settings(data_dir=tmp_path, api_token=ADMIN["Authorization"][7:]))
    ) as client:
        create_member(client)
    with pytest.raises(ValueError, match="authentication must remain"):
        with TestClient(create_app(Settings(data_dir=tmp_path))):
            pass
    with TestClient(
        create_app(Settings(data_dir=tmp_path, api_token=ADMIN["Authorization"][7:]))
    ) as client:
        assert client.get("/api/collaboration", headers=ADMIN).status_code == 200


def test_backup_restores_member_digest_and_team_authorization(tmp_path):
    source, restored = tmp_path / "source", tmp_path / "restored"
    settings = Settings(data_dir=source, api_token=ADMIN["Authorization"][7:])
    with TestClient(create_app(settings)) as client:
        project = client.post(
            "/api/resources/projects", headers=ADMIN, json={"body": {"name": "Team history"}}
        ).json()["id"]
        user = create_member(client)
        client.post(
            "/api/collaboration/teams",
            headers=ADMIN,
            json={
                "name": "Restored QA",
                "member_ids": [user["member"]["id"]],
                "project_roles": {project: "operator"},
            },
        ).raise_for_status()
    archive = tmp_path / "snapshot.zip"
    backup(source, archive)
    restore(archive, restored)
    with TestClient(
        create_app(Settings(data_dir=restored, api_token=ADMIN["Authorization"][7:]))
    ) as client:
        response = client.get(f"/api/session?project_id={project}", headers=headers(user["token"]))
        assert response.status_code == 200 and response.json()["role"] == "operator"
        assert "token_digest" not in json.dumps(
            client.get("/api/collaboration", headers=ADMIN).json()
        )
