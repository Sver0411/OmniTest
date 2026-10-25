"""Resource validation and project-scoped reference integrity."""

import time

from sqlalchemy import select

from omnitest.core.ir import Dataset, Environment, Project, Schedule, Suite, Target, TestIR
from omnitest.db import Member, Resource, Team

SCHEMAS = {
    "projects": Project,
    "targets": Target,
    "cases": TestIR,
    "suites": Suite,
    "environments": Environment,
    "schedules": Schedule,
    "datasets": Dataset,
}


def resource_dict(row):
    return {
        "id": row.id,
        "kind": row.kind,
        "project_id": row.project_id,
        "body": row.body,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class Resources:
    def __init__(self, db, registry):
        self.db = db
        self.registry = registry
        self.nodes = None

    def get(self, kind, identity, session=None):
        if session is None:
            with self.db.session() as s:
                return self.get(kind, identity, s)
        row = session.get(Resource, identity)
        if not row or row.kind != kind:
            raise LookupError(f"{kind} not found: {identity}")
        return row

    def list(self, kind, project_id=None):
        with self.db.session() as s:
            stmt = select(Resource).where(Resource.kind == kind).order_by(Resource.created_at)
            if project_id:
                stmt = stmt.where(Resource.project_id == project_id)
            return [resource_dict(r) for r in s.scalars(stmt)]

    def same_project(self, kind, identity, project_id, session):
        row = self.get(kind, identity, session)
        if row.project_id != project_id:
            raise ValueError("Cross-project reference is not allowed")
        return row

    def save(self, kind, body, project_id=None, identity=None, session=None, dataset_source=None):
        if session is None:
            with self.db.session.begin() as s:
                return self.save(
                    kind, body, project_id, identity, session=s, dataset_source=dataset_source
                )
        if kind not in SCHEMAS:
            raise LookupError("Unknown resource kind")
        body = SCHEMAS[kind].model_validate(body).model_dump(mode="json")
        s = session
        if kind != "projects":
            self.get("projects", project_id, s)
        else:
            project_id = None
        if kind == "cases":
            target = self.same_project("targets", body["target"], project_id, s)
            adapter = self.registry.create(body["adapter"])
            if target.body["kind"] not in adapter.info.target_kinds:
                raise ValueError("Adapter does not support this target kind")
            for dataset_id in body["dataset_ids"]:
                self.same_project("datasets", dataset_id, project_id, s)
        elif kind == "datasets":
            previous = self.get(kind, identity, s).body if identity else {}
            body["source"] = dataset_source or (
                previous.get("source") if body["rows"] == previous.get("rows") else None
            )
        elif kind == "environments":
            for source, replacement in body["target_overrides"].items():
                original = self.same_project("targets", source, project_id, s)
                actual = self.same_project("targets", replacement, project_id, s)
                if original.body["kind"] != actual.body["kind"]:
                    raise ValueError("Environment replacements must have the same target kind")
        elif kind == "suites":
            for case_id in body["case_ids"]:
                self.same_project("cases", case_id, project_id, s)
        elif kind == "schedules":
            body["last_error"] = None
            previous = self.get(kind, identity, s).body if identity else {}
            for key in ["last_run_id", "last_triggered_at", "skipped_count", "last_skip_reason"]:
                body[key] = previous.get(key, 0 if key == "skipped_count" else None)
            if identity and any(
                body.get(key) != previous.get(key)
                for key in ["schedule_type", "interval_seconds", "cron", "timezone"]
            ):
                body["next_run"] = None
            suite = self.same_project("suites", body["suite_id"], project_id, s)
            if body["environment_id"]:
                self.same_project("environments", body["environment_id"], project_id, s)
            if body["next_run"] is None:
                from omnitest.services.calendar import next_occurrence

                body["next_run"] = next_occurrence(body, time.time())
            if body["node_id"]:
                if self.nodes is None:
                    raise ValueError("Configure an orchestrator before assigning schedule nodes")
                self.nodes.validate_assignment(
                    body["node_id"],
                    project_id,
                    [
                        {"case": self.same_project("cases", identity, project_id, s).body}
                        for identity in suite.body["case_ids"]
                    ],
                )
        if identity:
            row = self.get(kind, identity, s)
            if row.project_id != project_id:
                raise ValueError("Project membership is immutable")
            if kind == "targets" and row.body["kind"] != body["kind"]:
                raise ValueError("Target kind is immutable; create a new target")
            row.body, row.updated_at = body, time.time()
        else:
            row = Resource(kind=kind, project_id=project_id, body=body)
            s.add(row)
        s.flush()
        if kind == "cases":
            row.body = {**body, "id": row.id}
        return resource_dict(row)

    def delete(self, kind, identity):
        with self.db.session.begin() as s:
            row = self.get(kind, identity, s)
            if kind == "projects":
                for model in [Member, Team]:
                    if any(identity in item.project_roles for item in s.scalars(select(model))):
                        raise ValueError("Project is referenced by member or team grants")
            for other in s.scalars(select(Resource)):
                body = other.body
                linked = (
                    other.project_id == identity
                    or body.get("target") == identity
                    or identity in body.get("case_ids", [])
                    or body.get("suite_id") == identity
                    or body.get("environment_id") == identity
                    or identity in body.get("dataset_ids", [])
                    or identity in body.get("target_overrides", {})
                    or identity in body.get("target_overrides", {}).values()
                )
                if linked:
                    raise ValueError(f"Resource is referenced by {other.kind}: {body.get('name')}")
            s.delete(row)
