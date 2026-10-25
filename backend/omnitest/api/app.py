"""HTTP management API and production SPA hosting."""

import json
import logging
import time
from contextlib import asynccontextmanager
from importlib.metadata import entry_points
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy import func, select, text

from omnitest import __version__
from omnitest.adapters.base import Registry
from omnitest.config import Settings
from omnitest.core.ir import Model, RunRequest, Schedule
from omnitest.core.security import Redactor, contained
from omnitest.db import AuditEvent, Database, Resource, Run
from omnitest.services.access import AccessControl, Principal
from omnitest.services.lock import WorkerLock
from omnitest.services.orchestrator import Orchestrator, run_dict
from omnitest.services.resources import SCHEMAS, Resources, resource_dict


class ResourcePayload(Model):
    project_id: str | None = None
    body: dict


class ImportPayload(Model):
    project_id: str
    cases: list[dict]


def create_app(settings=None):
    settings = settings or Settings()
    db = Database(settings.database_url)
    registry = Registry(settings.plugins)
    resources = Resources(db, registry)
    orchestrator = Orchestrator(db, resources, settings, registry)
    access = AccessControl(settings, db)
    if set(orchestrator.nodes.tokens.values()).intersection(
        token for token, _ in access.credentials
    ):
        raise ValueError("Node credentials must be separate from management API credentials")
    worker_lock = WorkerLock(settings.data_dir)

    @asynccontextmanager
    async def lifespan(app):
        worker_lock.acquire()
        try:
            db.migrate()
            await orchestrator.start()
            yield
        finally:
            await orchestrator.stop()
            db.engine.dispose()
            worker_lock.release()

    app = FastAPI(title="OmniTest", version=__version__, lifespan=lifespan)
    app.state.db, app.state.resources, app.state.orchestrator = db, resources, orchestrator
    app.state.extensions = {}
    from omnitest.services.nodes import mount

    mount(app, orchestrator.nodes, orchestrator)
    for extension in entry_points(group="omnitest.extensions"):
        try:
            extension.load()(app, db, resources, orchestrator, settings)
        except Exception:
            logging.getLogger(__name__).exception(
                "Optional extension unavailable: %s", extension.name
            )

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        started = time.monotonic()
        from omnitest.core.ir import uid

        request_id = uid()
        request.state.principal = Principal("anonymous", "anonymous", frozenset())
        request.state.project_id = None
        try:
            response = await protected(request, call_next)
        except HTTPException as exc:
            response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        except Exception:
            logging.getLogger(__name__).exception("Request failed: %s", request_id)
            response = JSONResponse({"detail": "Internal server error"}, status_code=500)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        audited = request.method not in {"GET", "HEAD", "OPTIONS"} or response.status_code in {
            401,
            403,
        }
        if response.status_code < 400 and (
            request.url.path == "/api/worker/claim" or request.url.path.endswith("/heartbeat")
        ):
            audited = False
        if request.url.path.startswith("/api/") and audited:
            actor = request.state.principal
            try:
                with db.session.begin() as session:
                    session.add(
                        AuditEvent(
                            actor=actor.name,
                            role=actor.role,
                            method=request.method,
                            path=Redactor().clean(request.url.path[:500]),
                            project_id=request.state.project_id,
                            status_code=response.status_code,
                            request_id=request_id,
                            duration_ms=(time.monotonic() - started) * 1000,
                        )
                    )
            except Exception:
                logging.getLogger(__name__).exception(
                    "Audit persistence failed for request %s", request_id
                )
        return response

    async def protected(request, call_next):
        host = request.url.hostname
        if (
            host not in {"127.0.0.1", "localhost", "testserver", "::1"}
            and not settings.authentication
        ):
            return JSONResponse(
                {"detail": "Remote access requires OMNITEST_API_TOKEN"}, status_code=403
            )
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
            return JSONResponse(
                {"detail": "Cross-origin requests are disabled; use the Vite proxy"},
                status_code=403,
            )
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            raise HTTPException(400, "Invalid Content-Length") from None
        if length < 0 or length > 5_000_000:
            return JSONResponse({"detail": "Payload exceeds 5 MB"}, status_code=413)
        if request.url.path.startswith("/api/") and request.url.path not in {
            "/api/health",
            "/api/ready",
        }:
            worker_request = request.url.path.startswith("/api/worker/")
            principal = (
                orchestrator.nodes.authenticate(request.headers.get("authorization", ""))
                if worker_request
                else access.authenticate(request.headers.get("authorization", ""))
            )
            request.state.principal = principal
            if request.method in {"POST", "PUT", "PATCH"}:
                data = await request.body()
                if len(data) > 5_000_000:
                    raise HTTPException(413, "Payload exceeds 5 MB")
                try:
                    payload = json.loads(data)
                    if isinstance(payload, dict) and isinstance(payload.get("project_id"), str):
                        request.state.project_id = payload["project_id"]
                except ValueError:
                    pass
            parts = request.url.path.strip("/").split("/")
            try:
                if len(parts) >= 3 and parts[1] in {"runs", "evidence"}:
                    request.state.project_id = access.project_for(Run, parts[2])
                elif len(parts) == 4 and parts[1] == "resources" and parts[2] in SCHEMAS:
                    request.state.project_id = access.project_for(Resource, parts[3], parts[2])
                elif len(parts) >= 5 and parts[1:3] == ["worker", "runs"]:
                    request.state.project_id = access.project_for(Run, parts[3])
            except HTTPException:
                pass
            if not worker_request:
                await access.authorize(request, principal)
        return await call_next(request)

    @app.exception_handler(LookupError)
    async def not_found(request, exc):
        return JSONResponse({"detail": Redactor().clean(str(exc))}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        detail = exc.errors(include_context=False) if isinstance(exc, ValidationError) else str(exc)
        return JSONResponse({"detail": Redactor().clean(detail)}, status_code=422)

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "ai_enabled": app.state.extensions.get("ai", lambda: False)(),
            "version": __version__,
        }

    @app.get("/api/session")
    def current_session(request: Request):
        return request.state.principal.describe()

    @app.get("/api/ready")
    def ready():
        try:
            with db.session() as session:
                session.execute(text("SELECT 1"))
            running = orchestrator.loop_task is not None and not orchestrator.loop_task.done()
            return JSONResponse(
                {
                    "status": "ready" if running else "unavailable",
                    "database": "ok",
                    "worker": "running" if running else "stopped",
                },
                status_code=200 if running else 503,
            )
        except Exception:
            return JSONResponse({"status": "unavailable", "database": "error"}, status_code=503)

    @app.get("/api/operations")
    def operations():
        with db.session() as session:
            counts = dict(
                session.execute(select(Run.status, func.count()).group_by(Run.status)).all()
            )
            oldest = session.scalar(select(func.min(Run.created_at)).where(Run.status == "PENDING"))
            audit_count = session.scalar(select(func.count()).select_from(AuditEvent))
        return {
            "version": __version__,
            "worker": {
                "active_runs": len(orchestrator.active),
                "max_concurrency": settings.max_concurrency,
            },
            "runs": counts,
            "oldest_pending_seconds": max(0, time.time() - oldest) if oldest else None,
            "audit_events": audit_count,
            "plugin_errors": registry.errors,
        }

    @app.get("/api/audit")
    def audit(limit: int = 50, project_id: str | None = None):
        with db.session() as session:
            statement = select(AuditEvent).order_by(AuditEvent.created_at.desc())
            if project_id:
                statement = statement.where(AuditEvent.project_id == project_id)
            return [
                {column.name: getattr(row, column.name) for column in AuditEvent.__table__.columns}
                for row in session.scalars(statement.limit(max(1, min(200, limit))))
            ]

    @app.get("/api/adapters")
    def adapters():
        return registry.inventory()

    @app.get("/api/schedules/preview")
    def schedule_preview(
        schedule_type: str = "cron",
        cron: str | None = None,
        interval_seconds: int | None = None,
        timezone: str = "UTC",
        after: float | None = Query(default=None, ge=0, le=4102444800, allow_inf_nan=False),
    ):
        from omnitest.services.calendar import preview

        value = Schedule(
            name="Preview",
            suite_id="preview",
            schedule_type=schedule_type,
            cron=cron,
            interval_seconds=interval_seconds,
            timezone=timezone,
        )
        return {
            "timezone": value.timezone,
            "occurrences": preview(value.model_dump(), after if after is not None else time.time()),
        }

    @app.get("/api/settings")
    def system_settings(request: Request):
        admin = request.state.principal.admin
        return {
            "ai": {
                "enabled": app.state.extensions.get("ai", lambda: False)(),
                "providers": [],
                "message": "Manage optional providers at /api/ai",
            },
            "max_concurrency": settings.max_concurrency,
            "cli_programs": list(settings.cli_programs) if admin else [],
            "appium_profiles": list(settings.appium_profiles) if admin else [],
            "http_faults": list(settings.http_faults) if admin else [],
            "allowed_remote_hosts": sorted(settings.allowed_hosts) if admin else [],
            "authentication": settings.authentication,
            "scheduler": "persistent interval and timezone cron",
            "principal": request.state.principal.describe(),
            "schemas": {kind: schema.model_json_schema() for kind, schema in SCHEMAS.items()},
        }

    @app.get("/api/resources/{kind}")
    def list_resources(kind: str, request: Request, project_id: str | None = None):
        if kind not in SCHEMAS:
            raise LookupError("Unknown resource kind")
        principal = request.state.principal
        return [
            row
            for row in resources.list(kind, project_id)
            if principal.visible(row["id"] if kind == "projects" else row["project_id"])
        ]

    @app.post("/api/resources/{kind}", status_code=201)
    def create_resource(kind: str, payload: ResourcePayload):
        return resources.save(kind, payload.body, payload.project_id)

    @app.post("/api/cases/import", status_code=201)
    def import_cases(payload: ImportPayload):
        if not 1 <= len(payload.cases) <= 100:
            raise ValueError("Import between 1 and 100 cases")
        with db.session.begin() as s:
            return [
                resources.save("cases", body, payload.project_id, session=s)
                for body in payload.cases
            ]

    @app.get("/api/resources/{kind}/{identity}")
    def get_resource(kind: str, identity: str):
        if kind not in SCHEMAS:
            raise LookupError("Unknown resource kind")
        return resource_dict(resources.get(kind, identity))

    @app.put("/api/resources/{kind}/{identity}")
    def update_resource(kind: str, identity: str, payload: ResourcePayload):
        return resources.save(kind, payload.body, payload.project_id, identity)

    @app.delete("/api/resources/{kind}/{identity}", status_code=204)
    def delete_resource(kind: str, identity: str):
        resources.delete(kind, identity)

    @app.post("/api/runs", status_code=202)
    def submit_run(value: RunRequest, request: Request):
        return orchestrator.submit(value, actor=request.state.principal.name)

    @app.get("/api/runs")
    def runs(
        request: Request,
        project_id: str | None = None,
        status: str | None = None,
        adapter: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 100,
    ):
        from omnitest.db import CaseResult

        with db.session() as s:
            stmt = select(Run).order_by(Run.created_at.desc())
            principal = request.state.principal
            if not principal.admin and principal.project_ids is not None:
                stmt = stmt.where(Run.project_id.in_(principal.project_ids))
            if project_id:
                stmt = stmt.where(Run.project_id == project_id)
            if status:
                stmt = stmt.where(Run.status == status)
            if since:
                stmt = stmt.where(Run.created_at >= since)
            if until:
                stmt = stmt.where(Run.created_at <= until)
            if adapter:
                stmt = stmt.where(
                    Run.id.in_(select(CaseResult.run_id).where(CaseResult.adapter == adapter))
                )
            return [run_dict(r) for r in s.scalars(stmt.limit(max(1, min(limit, 1000))))]

    @app.get("/api/runs/{run_id}")
    def report(run_id: str):
        return orchestrator.report(run_id)

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel(run_id: str):
        orchestrator.cancel(run_id)
        return orchestrator.report(run_id)

    @app.get("/api/runs/{run_id}/export")
    def export(run_id: str, format: str = "json"):
        report = orchestrator.report(run_id)
        if format == "junit":
            import xml.etree.ElementTree as ET

            root = ET.Element("testsuite", name="OmniTest", tests=str(len(report["results"])))
            for result in report["results"]:
                case = ET.SubElement(
                    root,
                    "testcase",
                    name=result["name"],
                    classname=result["adapter"],
                    time=str(result["duration"]),
                )
                status = result["status"]
                tag = {
                    "FAILED": "failure",
                    "ERROR": "error",
                    "CANCELLED": "skipped",
                    "SKIPPED": "skipped",
                }.get(status)
                if tag:
                    ET.SubElement(case, tag, message=status).text = json.dumps(
                        result["detail"], ensure_ascii=False
                    )
            return Response(
                ET.tostring(root, encoding="utf-8", xml_declaration=True),
                media_type="application/xml",
                headers={"Content-Disposition": f'attachment; filename="{run_id}.xml"'},
            )
        return Response(
            json.dumps(report, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{run_id}.json"'},
        )

    @app.get("/api/evidence/{run_id}/{result_id}/{attempt}/{filename}")
    def evidence(run_id: str, result_id: str, attempt: str, filename: str):
        report = orchestrator.report(run_id)
        result = next((r for r in report["results"] if r["id"] == result_id), None)
        relative = f"{attempt}/{filename}"
        if not result or not any(
            relative in a.get("evidence", []) for a in result["detail"].get("attempts", [])
        ):
            raise LookupError("Evidence not in result manifest")
        path = contained(settings.data_dir / "evidence", f"{run_id}/{result_id}/{relative}")
        if not path.is_file():
            raise LookupError("Evidence file not found")
        return FileResponse(path, filename=filename)

    @app.get("/api/dashboard")
    def dashboard(request: Request, project_id: str | None = None):
        principal = request.state.principal
        with db.session() as s:
            stmt = select(Run).order_by(Run.created_at.desc())
            if not principal.admin and principal.project_ids is not None:
                stmt = stmt.where(Run.project_id.in_(principal.project_ids))
            if project_id:
                stmt = stmt.where(Run.project_id == project_id)
            rows = list(s.scalars(stmt.limit(30)))
        totals = {}
        for run in rows:
            for status, count in run.summary.get("counts", {}).items():
                totals[status] = totals.get(status, 0) + count
        total = sum(totals.values())
        return {
            "projects": sum(principal.visible(row["id"]) for row in resources.list("projects")),
            "cases": sum(
                principal.visible(row["project_id"]) for row in resources.list("cases", project_id)
            ),
            "runs": len(rows),
            "counts": totals,
            "pass_rate": totals.get("PASSED", 0) / total if total else None,
            "recent": [run_dict(r) for r in rows],
            "window": "latest 30 runs",
        }

    dist = Path("frontend/dist").resolve()
    if not (dist / "index.html").is_file():
        dist = Path(__file__).resolve().parents[1] / "static"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "API endpoint not found")
        if (dist / "index.html").is_file():
            return FileResponse(dist / "index.html")
        return {"message": "Build frontend with npm --prefix frontend run build", "api": "/docs"}

    return app
