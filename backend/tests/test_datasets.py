import json

import pytest
from fastapi.testclient import TestClient

from omnitest.adapters.base import Registry
from omnitest.api.app import create_app
from omnitest.config import Settings
from omnitest.core.ir import RunRequest
from omnitest.db import Database
from omnitest.services.datasets import ImportDataset, export_dataset, parse_dataset
from omnitest.services.resources import Resources


def test_csv_preserves_strings_and_explicit_column_types():
    payload = ImportDataset(
        project_id="p",
        filename="matrix.csv",
        format="csv",
        content='code,n,ok,value\n001,2,true,"{""a"":1}"\n',
        column_types={"n": "integer", "ok": "boolean", "value": "json"},
    )
    body, columns = parse_dataset(payload)
    assert body["rows"] == [{"code": "001", "n": 2, "ok": True, "value": {"a": 1}}]
    assert columns == ["code", "n", "ok", "value"] and len(body["source"]["sha256"]) == 64
    content, _ = export_dataset(body, "csv")
    assert parse_dataset(payload.model_copy(update={"content": content}))[0]["rows"] == body["rows"]


@pytest.mark.parametrize(
    "format,content",
    [
        ("csv", "a,a\n1,2"),
        ("csv", "a,b\n1"),
        ("csv", "a\n"),
        ("csv", "\n1"),
        ("json", '[{"a":1,"a":2}]'),
        ("json", '[{"a":NaN}]'),
        ("json", '[{"a":Infinity}]'),
        ("json", '[{"a":1},{"b":2}]'),
        ("json", "{}"),
        ("json", "[{}]"),
        ("json", '[{"secret.PASSWORD":"x"}]'),
    ],
)
def test_bad_datasets_are_rejected(format, content):
    with pytest.raises(ValueError):
        parse_dataset(ImportDataset(project_id="p", format=format, content=content))


def test_dataset_limits_and_json_native_types():
    rows = [{"number": 1, "flag": False, "object": {"a": [1, None]}}]
    body, _ = parse_dataset(ImportDataset(project_id="p", format="json", content=json.dumps(rows)))
    assert body["rows"] == rows
    with pytest.raises(ValueError, match="100"):
        parse_dataset(ImportDataset(project_id="p", format="json", content=json.dumps(rows * 101)))
    with pytest.raises(ValueError, match="1 MB"):
        parse_dataset(ImportDataset(project_id="p", format="csv", content="列\n" + "汉" * 400000))
    with pytest.raises(ValueError, match="typed"):
        parse_dataset(
            ImportDataset(
                project_id="p", format="csv", content="n\ntext", column_types={"n": "integer"}
            )
        )


def setup_graph(stack, fixture_url):
    resources = stack[3]
    project = resources.save("projects", {"name": "Matrix"})["id"]
    logical = resources.save(
        "targets",
        {
            "name": "Logical endpoint",
            "kind": "http",
            "authorized": True,
            "config": {"base_url": "http://127.0.0.1:1"},
        },
        project,
    )["id"]
    actual = resources.save(
        "targets",
        {
            "name": "Actual fixture",
            "kind": "http",
            "authorized": True,
            "config": {"base_url": fixture_url},
        },
        project,
    )["id"]
    data = resources.save(
        "datasets",
        {
            "name": "Queries",
            "rows": [{"q": "apple", "expected": 1}, {"q": "missing", "expected": 0}],
        },
        project,
    )["id"]
    case = resources.save(
        "cases",
        {
            "name": "Data driven HTTP",
            "adapter": "api",
            "target": logical,
            "dataset_ids": [data],
            "steps": [
                {
                    "action": "request",
                    "config": {"path": "/items", "query": {"q": "${q}"}},
                    "assertions": [{"path": "$.json.count", "op": "eq", "expected": "${expected}"}],
                }
            ],
        },
        project,
    )["id"]
    environment = resources.save(
        "environments",
        {
            "name": "Fixture environment",
            "variables": {"q": "default"},
            "target_overrides": {logical: actual},
        },
        project,
    )["id"]
    return project, logical, actual, data, case, environment


async def test_environment_and_dataset_snapshots_drive_real_http_execution(stack, fixture_url):
    project, logical, actual, data, case, environment = setup_graph(stack, fixture_url)
    resources, orchestrator = stack[3], stack[4]
    request = RunRequest(project_id=project, case_ids=[case], environment_id=environment)
    run = orchestrator.submit(request)
    resources.save(
        "datasets", {"name": "Changed", "rows": [{"q": "missing", "expected": 99}]}, project, data
    )
    resources.save(
        "environments", {"name": "Changed environment", "variables": {}}, project, environment
    )
    await orchestrator.execute(run["id"])
    report = orchestrator.report(run["id"])
    assert report["summary"]["counts"] == {"PASSED": 2}
    assert report["environment"]["name"] == "Fixture environment"
    for index, result in enumerate(report["results"]):
        selected = result["detail"]["selection"]
        assert selected["logical_target"] == logical and selected["target"] == actual
        assert selected["dataset"]["id"] == data and selected["dataset"]["row"] == index + 1
    later = orchestrator.submit(request)
    await orchestrator.execute(later["id"])
    assert orchestrator.report(later["id"])["status"] == "ERROR"


def test_references_and_target_kinds_cannot_cross_projects(stack, fixture_url):
    project, logical, actual, data, case, environment = setup_graph(stack, fixture_url)
    resources = stack[3]
    other = resources.save("projects", {"name": "Other"})["id"]
    for kind, body in [
        ("environments", {"name": "Cross scope", "target_overrides": {logical: actual}}),
        ("cases", {**resources.get("cases", case).body, "target": actual}),
    ]:
        with pytest.raises(ValueError, match="Cross-project"):
            resources.save(kind, body, other)
    wrong = resources.save("targets", {"name": "CLI", "kind": "cli"}, project)["id"]
    with pytest.raises(ValueError, match="same target kind"):
        resources.save(
            "environments", {"name": "Wrong kind", "target_overrides": {logical: wrong}}, project
        )
    for kind, identity in [("datasets", data), ("targets", actual), ("targets", logical)]:
        with pytest.raises(ValueError, match="referenced"):
            resources.delete(kind, identity)
    with pytest.raises(ValueError, match="either inline"):
        resources.save(
            "cases", {**resources.get("cases", case).body, "datasets": [{"q": "a"}]}, project
        )


def test_import_export_permissions_provenance_and_transaction(tmp_path, monkeypatch):
    bootstrap = Settings(data_dir=tmp_path)
    db = Database(bootstrap.database_url)
    db.migrate()
    resources = Resources(db, Registry())
    project = resources.save("projects", {"name": "Allowed"})["id"]
    other = resources.save("projects", {"name": "Other"})["id"]
    db.engine.dispose()
    profiles = []
    for role in ["editor", "viewer"]:
        monkeypatch.setenv("OMNITEST_ACCESS_" + role.upper(), role + "-fixture-token-1234567")
        profiles.append(
            {
                "name": role,
                "role": role,
                "token_env": "OMNITEST_ACCESS_" + role.upper(),
                "project_ids": [project],
            }
        )
    app = create_app(Settings(data_dir=tmp_path, api_token="admin-fixture", access_tokens=profiles))

    def h(role):
        return {"Authorization": "Bearer " + role + "-fixture-token-1234567"}

    payload = {
        "project_id": project,
        "filename": "matrix.csv",
        "format": "csv",
        "content": "n\n001",
    }
    with TestClient(app) as client:
        assert (
            client.post("/api/datasets/preview", headers=h("viewer"), json=payload).status_code
            == 403
        )
        assert (
            client.post(
                "/api/datasets/import", headers=h("editor"), json={**payload, "project_id": other}
            ).status_code
            == 403
        )
        preview = client.post("/api/datasets/preview", headers=h("editor"), json=payload)
        assert preview.json()["rows"] == [{"n": "001"}]
        imported = client.post("/api/datasets/import", headers=h("editor"), json=payload)
        assert imported.status_code == 201, imported.text
        row = imported.json()
        exported = client.get(f"/api/datasets/{row['id']}/export", headers=h("viewer"))
        assert exported.json() == [{"n": "001"}]
        assert client.get(
            f"/api/datasets/{row['id']}/export?format=csv", headers=h("viewer")
        ).text.startswith("n\r\n")
        bad = client.post(
            "/api/datasets/import", headers=h("editor"), json={**payload, "content": "n,n\n1,2"}
        )
        assert bad.status_code == 422
        assert (
            len(
                client.get(
                    f"/api/resources/datasets?project_id={project}", headers=h("viewer")
                ).json()
            )
            == 1
        )
        body = {**row["body"], "name": "Renamed"}
        saved = client.put(
            f"/api/resources/datasets/{row['id']}",
            headers=h("editor"),
            json={"project_id": project, "body": body},
        )
        assert saved.json()["body"]["source"] == row["body"]["source"]
        body["rows"] = [{"n": "changed"}]
        saved = client.put(
            f"/api/resources/datasets/{row['id']}",
            headers=h("editor"),
            json={"project_id": project, "body": body},
        )
        assert saved.json()["body"]["source"] is None
