"""Durable local queue, bounded execution and persistent interval scheduling."""

import asyncio
import time
from collections import Counter

from sqlalchemy import select

from omnitest.core.ir import RunRequest, uid
from omnitest.core.runner import Runner
from omnitest.db import CaseResult, NodeLease, Resource, Run
from omnitest.services.nodes import NodeService


def run_dict(row):
    return {
        "node_id": row.snapshot.get("node_id"),
        **{
            k: getattr(row, k)
            for k in [
                "id",
                "project_id",
                "status",
                "created_at",
                "started_at",
                "finished_at",
                "summary",
            ]
        },
    }


def result_dict(row):
    return {
        k: getattr(row, k)
        for k in ["id", "case_id", "name", "adapter", "status", "duration", "detail"]
    }


class Orchestrator:
    def __init__(self, db, resources, settings, registry):
        self.db, self.resources, self.settings = db, resources, settings
        self.runner = Runner(settings, registry)
        self.nodes = NodeService(db, settings)
        resources.nodes = self.nodes
        self.semaphore = asyncio.Semaphore(settings.max_concurrency)
        self.active = {}
        self.loop_task = None

    def submit(self, request: RunRequest, source="manual", session=None, actor=None):
        if session is None:
            with self.db.session.begin() as s:
                return self.submit(request, source, s, actor)
        s = session
        self.resources.get("projects", request.project_id, s)
        ids, concurrency, parameters = request.case_ids, request.concurrency, request.parameters
        if request.suite_id:
            suite = self.resources.same_project("suites", request.suite_id, request.project_id, s)
            ids, concurrency = suite.body["case_ids"], suite.body["concurrency"]
            parameters = {**suite.body["parameters"], **parameters}
        environment = {}
        if request.environment_id:
            environment = self.resources.same_project(
                "environments", request.environment_id, request.project_id, s
            ).body["variables"]
        cases = []
        for identity in ids:
            case = self.resources.same_project("cases", identity, request.project_id, s)
            target = self.resources.same_project(
                "targets", case.body["target"], request.project_id, s
            )
            for dataset in case.body["datasets"] or [{}]:
                cases.append(
                    {
                        "result_id": uid(),
                        "case": case.body,
                        "target": target.body,
                        "variables": {
                            **environment,
                            **case.body["environment"],
                            **case.body["parameters"],
                            **parameters,
                            **dataset,
                        },
                    }
                )
        if len(cases) > 500:
            raise ValueError("A run may contain at most 500 parameterized cases")
        if request.node_id:
            self.nodes.validate_assignment(request.node_id, request.project_id, cases)
        run = Run(
            id=uid(),
            project_id=request.project_id,
            status="PENDING",
            snapshot={
                "cases": cases,
                "concurrency": concurrency,
                "source": source,
                "actor": actor,
                "node_id": request.node_id,
            },
            summary={},
        )
        s.add(run)
        s.flush()
        if request.node_id:
            s.add(NodeLease(run_id=run.id, node_id=request.node_id))
        for item in cases:
            result = CaseResult(
                id=item["result_id"],
                run_id=run.id,
                case_id=item["case"]["id"],
                name=item["case"]["name"],
                adapter=item["case"]["adapter"],
                status="PENDING",
                detail={},
            )
            s.add(result)
        return run_dict(run)

    async def start(self):
        with self.db.session.begin() as s:
            for run in s.scalars(select(Run).where(Run.status == "RUNNING")):
                run.status, run.finished_at = "ERROR", time.time()
                run.summary = {
                    "error": "Service interrupted; run was not replayed to avoid duplicate side effects"
                }
                for result in s.scalars(
                    select(CaseResult).where(
                        CaseResult.run_id == run.id, CaseResult.status.in_(["PENDING", "RUNNING"])
                    )
                ):
                    result.status, result.detail = "ERROR", {"error": "Service interrupted"}
        self.loop_task = asyncio.create_task(self.poll())

    async def stop(self):
        if self.loop_task:
            self.loop_task.cancel()
            await asyncio.gather(self.loop_task, return_exceptions=True)
        tasks = list(self.active.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def poll(self):
        while True:
            try:
                self.nodes.expire()
                self.tick_schedules()
                with self.db.session() as s:
                    pending = list(
                        s.scalars(
                            select(Run)
                            .where(Run.status == "PENDING", ~Run.id.in_(select(NodeLease.run_id)))
                            .order_by(Run.created_at)
                            .limit(8)
                        )
                    )
                for run in pending:
                    if run.id not in self.active and len(self.active) < 8:
                        task = asyncio.create_task(self.execute(run.id))
                        self.active[run.id] = task
                        task.add_done_callback(lambda _, rid=run.id: self.active.pop(rid, None))
            except Exception:
                import logging

                logging.getLogger(__name__).exception("Queue tick failed")
            await asyncio.sleep(0.25)

    def tick_schedules(self):
        now = time.time()
        with self.db.session.begin() as s:
            for schedule in s.scalars(select(Resource).where(Resource.kind == "schedules")):
                cfg = schedule.body
                if cfg["enabled"] and cfg["next_run"] <= now:
                    try:
                        with s.begin_nested():
                            self.submit(
                                RunRequest(
                                    project_id=schedule.project_id,
                                    suite_id=cfg["suite_id"],
                                    environment_id=cfg.get("environment_id"),
                                    node_id=cfg.get("node_id"),
                                ),
                                source=f"schedule:{schedule.id}",
                                session=s,
                            )
                        schedule.body = {**cfg, "next_run": now + cfg["interval_seconds"]}
                    except (ValueError, LookupError) as exc:
                        from omnitest.core.security import Redactor

                        schedule.body = {
                            **cfg,
                            "enabled": False,
                            "last_error": Redactor().clean(str(exc))[:2000],
                        }

    async def execute(self, run_id):
        with self.db.session.begin() as s:
            run = s.get(Run, run_id)
            if run.status != "PENDING":
                return
            if run.snapshot.get("node_id"):
                raise ValueError("Remote runs must be claimed by their assigned execution node")
            run.status, run.started_at = "RUNNING", time.time()
            snapshot = run.snapshot
        local = asyncio.Semaphore(snapshot["concurrency"])

        async def one(item):
            result_id = item["result_id"]
            try:
                async with local, self.semaphore:
                    with self.db.session.begin() as s:
                        s.get(CaseResult, result_id).status = "RUNNING"
                    outcome = await self.runner.execute(
                        item["case"],
                        item["target"],
                        item["variables"],
                        self.settings.data_dir / "evidence" / run_id / result_id,
                        allowed_secrets=set(self.settings.project_secrets.get(run.project_id, []))
                        if self.settings.access_tokens
                        else None,
                    )
            except asyncio.CancelledError:
                outcome = {
                    "status": "CANCELLED",
                    "duration": 0,
                    "detail": {"error": "Cancelled before execution"},
                }
            except Exception as exc:
                from omnitest.core.security import Redactor

                outcome = {
                    "status": "ERROR",
                    "duration": 0,
                    "detail": {"error": Redactor().clean(str(exc))},
                }
            with self.db.session.begin() as s:
                row = s.get(CaseResult, result_id)
                for key, value in outcome.items():
                    setattr(row, key, value)

        tasks = [asyncio.create_task(one(item)) for item in snapshot["cases"]]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            self.finalize(run_id)

    def finalize(self, run_id):
        with self.db.session.begin() as s:
            run = s.get(Run, run_id)
            rows = list(s.scalars(select(CaseResult).where(CaseResult.run_id == run_id)))
            lease = s.get(NodeLease, run_id)
            for row in rows:
                if row.status in {"PENDING", "RUNNING"}:
                    row.status, row.detail = (
                        "CANCELLED" if lease and lease.cancel_requested else "ERROR",
                        {
                            "error": "Execution cancelled"
                            if lease and lease.cancel_requested
                            else "Execution ended without a final result"
                        },
                    )
            counts = dict(Counter(r.status for r in rows))
            run.summary = {
                "counts": counts,
                "total": len(rows),
                "pass_rate": counts.get("PASSED", 0) / len(rows) if rows else 0,
                "duration": time.time() - (run.started_at or run.created_at),
            }
            run.status = (
                "CANCELLED"
                if lease and lease.cancel_requested
                else next(
                    (status for status in ["CANCELLED", "ERROR", "FAILED"] if counts.get(status)),
                    "SKIPPED" if counts.get("SKIPPED") == len(rows) else "PASSED",
                )
            )
            run.finished_at = time.time()

    def cancel(self, run_id):
        with self.nodes.lock, self.db.session.begin() as s:
            run = s.get(Run, run_id)
            if not run:
                raise LookupError("Run not found")
            if run.status == "PENDING":
                run.status, run.finished_at = "CANCELLED", time.time()
                for result in s.scalars(select(CaseResult).where(CaseResult.run_id == run_id)):
                    result.status = "CANCELLED"
            elif run.status == "RUNNING" and run_id in self.active:
                self.active[run_id].cancel()
            elif run.status == "RUNNING" and (lease := s.get(NodeLease, run_id)):
                lease.cancel_requested = True
        if run.status == "CANCELLED":
            self.finalize(run_id)

    def report(self, run_id):
        with self.db.session() as s:
            run = s.get(Run, run_id)
            if not run:
                raise LookupError("Run not found")
            positions = {item["result_id"]: i for i, item in enumerate(run.snapshot["cases"])}
            rows = list(s.scalars(select(CaseResult).where(CaseResult.run_id == run_id)))
            return {
                **run_dict(run),
                "source": run.snapshot["source"],
                "submitted_by": run.snapshot.get("actor"),
                "cancel_requested": bool(
                    (lease := s.get(NodeLease, run_id)) and lease.cancel_requested
                ),
                "results": [
                    result_dict(r)
                    for r in sorted(rows, key=lambda r: positions.get(r.id, len(positions)))
                ],
            }
