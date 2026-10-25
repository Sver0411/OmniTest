"""Authenticated pull workers with durable, non-replayed run leases."""

import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from collections import Counter
from typing import Literal

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select

from omnitest import __version__
from omnitest.core.ir import Model
from omnitest.core.security import Redactor, contained
from omnitest.db import CaseResult, NodeLease, NodeState, Run
from omnitest.services.access import Principal

LEASE_SECONDS = 30
MAX_EVIDENCE_FILE = 2_000_000
MAX_EVIDENCE_BYTES = 64_000_000
MAX_EVIDENCE_COUNT = 256


class NodeConfig(Model):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    token_env: str = Field(pattern=r"^OMNITEST_NODE_[A-Z0-9_]+$")
    project_ids: list[str] | None = Field(default_factory=list, max_length=500)
    adapters: list[str] = Field(min_length=1, max_length=100)
    enabled: bool = True

    def permits(self, project_id):
        return self.enabled and (self.project_ids is None or project_id in self.project_ids)


class Inventory(Model):
    protocol_version: Literal[1] = 1
    version: str = Field(max_length=64)
    os: str = Field(max_length=64)
    python: str = Field(max_length=64)
    adapters: dict[str, str] = Field(max_length=100)


class Claim(Model):
    claim_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{32,100}$")


class Outcome(Claim):
    status: Literal["PASSED", "FAILED", "ERROR", "SKIPPED", "CANCELLED"]
    duration: float = Field(ge=0)
    detail: dict


class Evidence(Claim):
    path: str = Field(max_length=220)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content: str = Field(max_length=2_666_668)


def evidence_path(value):
    if not re.fullmatch(r"[1-4]/[a-zA-Z0-9_-][a-zA-Z0-9_.-]{0,199}", value):
        raise ValueError("Evidence must use an attempt number and a simple filename")
    stem = value.split("/")[1].split(".")[0].upper()
    if stem in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"{p}{i}" for p in ["COM", "LPT"] for i in range(10)),
    }:
        raise ValueError("Reserved evidence filename")
    if value.endswith("."):
        raise ValueError("Evidence filenames cannot end with a dot")
    return value


class NodeService:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings
        self.lock = threading.RLock()
        self.configs, self.tokens = {}, {}
        for item in settings.remote_nodes:
            config = NodeConfig.model_validate(item)
            if config.id in self.configs:
                raise ValueError("Remote node IDs must be unique")
            self.configs[config.id] = config
            if config.enabled:
                token = os.getenv(config.token_env, "")
                if len(token) < 32:
                    raise ValueError(f"Node {config.id} requires a token of at least 32 characters")
                if token in self.tokens.values():
                    raise ValueError("Remote nodes must use distinct tokens")
                self.tokens[config.id] = token

    def authenticate(self, authorization):
        supplied = (
            authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        )
        for identity, token in self.tokens.items():
            if hmac.compare_digest(supplied.encode(), token.encode()):
                config = self.configs[identity]
                projects = frozenset(config.project_ids) if config.project_ids is not None else None
                return Principal(identity, "worker", projects)
        raise HTTPException(401, "Invalid execution node token")

    def validate_assignment(self, node_id, project_id, cases):
        config = self.configs.get(node_id)
        if not config or not config.permits(project_id):
            raise ValueError("Execution node is unavailable for this project")
        if not {item["case"]["adapter"] for item in cases}.issubset(config.adapters):
            raise ValueError("Execution node is not configured for all selected adapters")

    def inventory(self, principal, project_id=None):
        with self.db.session() as session:
            states = {row.id: row for row in session.scalars(select(NodeState))}
            active = dict(
                session.execute(
                    select(NodeLease.node_id, NodeLease.run_id)
                    .join(Run, Run.id == NodeLease.run_id)
                    .where(Run.status == "RUNNING")
                ).all()
            )
        items = []
        for config in self.configs.values():
            if not principal.admin:
                if not project_id or not config.permits(project_id):
                    continue
            elif project_id and not config.permits(project_id):
                continue
            row = states.get(config.id)
            online = row is not None and time.time() - row.last_seen < LEASE_SECONDS
            item = {
                "id": config.id,
                "enabled": config.enabled,
                "status": "disabled" if not config.enabled else "online" if online else "offline",
                "adapters": config.adapters,
            }
            if principal.admin:
                item.update(
                    last_seen=row.last_seen if row else None,
                    inventory=row.inventory if row else None,
                    active_run=active.get(config.id),
                    project_ids=config.project_ids,
                )
            items.append(item)
        return items

    def claim(self, node_id, inventory):
        now = time.time()
        with self.lock, self.db.session.begin() as session:
            state = session.get(NodeState, node_id)
            if state is None:
                session.add(NodeState(id=node_id, last_seen=now, inventory=inventory.model_dump()))
            else:
                state.last_seen, state.inventory = now, inventory.model_dump()
            busy = session.scalar(
                select(NodeLease.run_id)
                .join(Run)
                .where(NodeLease.node_id == node_id, Run.status == "RUNNING")
            )
            if busy:
                return {"assignment": None}
            for lease in session.scalars(
                select(NodeLease)
                .join(Run)
                .where(NodeLease.node_id == node_id, Run.status == "PENDING")
                .order_by(Run.created_at)
            ):
                run = session.get(Run, lease.run_id)
                self.validate_assignment(node_id, run.project_id, run.snapshot["cases"])
                required = {item["case"]["adapter"] for item in run.snapshot["cases"]}
                if not required.issubset(inventory.adapters):
                    continue
                lease.claim_id = secrets.token_urlsafe(32)
                lease.heartbeat_at = now
                run.status, run.started_at = "RUNNING", now
                return {
                    "assignment": {
                        "protocol_version": 1,
                        "run_id": run.id,
                        "project_id": run.project_id,
                        "claim_id": lease.claim_id,
                        "lease_seconds": LEASE_SECONDS,
                        "snapshot": run.snapshot,
                        "allowed_secrets": self.settings.project_secrets.get(run.project_id, []),
                    }
                }
        return {"assignment": None}

    def owned(self, session, node_id, run_id, claim_id):
        lease = session.get(NodeLease, run_id)
        run = session.get(Run, run_id)
        if not lease or lease.node_id != node_id:
            raise HTTPException(404, "Node assignment not found")
        if not lease.claim_id or not hmac.compare_digest(lease.claim_id, claim_id):
            raise HTTPException(409, "Stale execution lease")
        if run.status != "RUNNING" or time.time() - lease.heartbeat_at >= LEASE_SECONDS:
            raise HTTPException(409, "Execution lease is no longer active")
        self.validate_assignment(node_id, run.project_id, run.snapshot["cases"])
        return run, lease

    def heartbeat(self, node_id, run_id, claim_id):
        with self.lock, self.db.session.begin() as session:
            _, lease = self.owned(session, node_id, run_id, claim_id)
            now = time.time()
            lease.heartbeat_at = now
            session.get(NodeState, node_id).last_seen = now
            return {"cancel_requested": lease.cancel_requested}

    def upload(self, node_id, run_id, result_id, value):
        relative = evidence_path(value.path)
        try:
            content = base64.b64decode(value.content, validate=True)
        except ValueError as exc:
            raise ValueError("Invalid evidence encoding") from exc
        if len(content) > MAX_EVIDENCE_FILE or hashlib.sha256(content).hexdigest() != value.sha256:
            raise ValueError("Evidence size or checksum mismatch")
        with self.lock, self.db.session.begin() as session:
            _, lease = self.owned(session, node_id, run_id, value.claim_id)
            self.result(session, run_id, result_id, pending=True)
            path = contained(
                self.settings.data_dir / "evidence", f"{run_id}/{result_id}/{relative}"
            )
            if path.is_file():
                if path.read_bytes() != content:
                    raise HTTPException(409, "Evidence already exists with different content")
                return {"path": relative}
            if (
                lease.evidence_count >= MAX_EVIDENCE_COUNT
                or lease.evidence_bytes + len(content) > MAX_EVIDENCE_BYTES
            ):
                raise ValueError("Run evidence quota exceeded")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            lease.evidence_count += 1
            lease.evidence_bytes += len(content)
        return {"path": relative}

    @staticmethod
    def result(session, run_id, result_id, pending=False):
        row = session.get(CaseResult, result_id)
        if not row or row.run_id != run_id:
            raise HTTPException(404, "Result not in assigned run")
        if pending and row.status not in {"PENDING", "RUNNING"}:
            raise HTTPException(409, "Result already completed")
        return row

    def save_result(self, node_id, run_id, result_id, value):
        with self.lock, self.db.session.begin() as session:
            run, _ = self.owned(session, node_id, run_id, value.claim_id)
            row = self.result(session, run_id, result_id)
            outcome = Redactor().clean(value.model_dump(exclude={"claim_id"}))
            selection = next(
                item.get("selection")
                for item in run.snapshot["cases"]
                if item["result_id"] == result_id
            )
            if selection:
                outcome["detail"]["selection"] = Redactor().clean(selection)
            attempts = outcome["detail"].get("attempts", [])
            if not isinstance(attempts, list) or len(attempts) > 4:
                raise ValueError("Invalid result attempts")
            for attempt in attempts:
                if not isinstance(attempt, dict) or not isinstance(
                    attempt.get("evidence", []), list
                ):
                    raise ValueError("Invalid evidence manifest")
                for name in attempt.get("evidence", []):
                    relative = evidence_path(name)
                    path = contained(
                        self.settings.data_dir / "evidence", f"{run_id}/{result_id}/{relative}"
                    )
                    if not path.is_file():
                        raise ValueError("Upload all manifest evidence before completing a result")
            if row.status not in {"PENDING", "RUNNING"}:
                if any(getattr(row, key) != val for key, val in outcome.items()):
                    raise HTTPException(409, "Result already completed")
            else:
                for key, val in outcome.items():
                    setattr(row, key, val)
        return {"status": outcome["status"]}

    def expire(self):
        with self.lock, self.db.session.begin() as session:
            now = time.time()
            for lease in session.scalars(
                select(NodeLease).join(Run).where(Run.status.in_(["PENDING", "RUNNING"]))
            ):
                run = session.get(Run, lease.run_id)
                config = self.configs.get(lease.node_id)
                revoked = (
                    not config
                    or not config.permits(run.project_id)
                    or not {item["case"]["adapter"] for item in run.snapshot["cases"]}.issubset(
                        config.adapters
                    )
                )
                expired = run.status == "RUNNING" and now - lease.heartbeat_at >= LEASE_SECONDS
                if not revoked and not expired:
                    continue
                error = (
                    "Execution node authorization revoked"
                    if revoked
                    else "Execution node heartbeat lost; run was not replayed"
                )
                run.status, run.finished_at = "ERROR", now
                lease.claim_id = None
                for row in session.scalars(
                    select(CaseResult).where(
                        CaseResult.run_id == run.id, CaseResult.status.in_(["PENDING", "RUNNING"])
                    )
                ):
                    row.status, row.detail = "ERROR", {"error": error}
                rows = list(session.scalars(select(CaseResult).where(CaseResult.run_id == run.id)))
                counts = dict(Counter(row.status for row in rows))
                run.summary = {
                    "error": error,
                    "node_id": lease.node_id,
                    "counts": counts,
                    "total": len(rows),
                    "pass_rate": counts.get("PASSED", 0) / len(rows) if rows else 0,
                    "duration": now - (run.started_at or run.created_at),
                }


def mount(app, nodes, orchestrator):
    from fastapi import Request

    @app.get("/api/nodes")
    def list_nodes(request: Request, project_id: str | None = None):
        principal = request.state.principal
        if project_id:
            principal.check_project(project_id)
        return nodes.inventory(principal, project_id)

    @app.post("/api/worker/claim")
    def claim(value: Inventory, request: Request):
        return nodes.claim(request.state.principal.name, value)

    @app.post("/api/worker/runs/{run_id}/heartbeat")
    def heartbeat(run_id: str, value: Claim, request: Request):
        return nodes.heartbeat(request.state.principal.name, run_id, value.claim_id)

    @app.post("/api/worker/runs/{run_id}/evidence/{result_id}")
    def upload(run_id: str, result_id: str, value: Evidence, request: Request):
        return nodes.upload(request.state.principal.name, run_id, result_id, value)

    @app.post("/api/worker/runs/{run_id}/results/{result_id}")
    def result(run_id: str, result_id: str, value: Outcome, request: Request):
        return nodes.save_result(request.state.principal.name, run_id, result_id, value)

    @app.post("/api/worker/runs/{run_id}/finish")
    def finish(run_id: str, value: Claim, request: Request):
        with nodes.lock:
            with nodes.db.session() as session:
                nodes.owned(session, request.state.principal.name, run_id, value.claim_id)
            orchestrator.finalize(run_id)
        return {"status": "finished", "version": __version__}
