"""HTTP management API and production SPA hosting."""

import hmac
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from importlib.metadata import entry_points
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy import select

from omnitest.adapters.base import Registry
from omnitest.config import Settings
from omnitest.core.ir import Model, RunRequest
from omnitest.core.security import Redactor, contained
from omnitest.db import Database, Run
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

    app = FastAPI(title="OmniTest", version="0.1.0", lifespan=lifespan)
    app.state.db, app.state.resources, app.state.orchestrator = db, resources, orchestrator
    app.state.extensions = {}
    for extension in entry_points(group="omnitest.extensions"):
        try:
            extension.load()(app, db, resources, orchestrator, settings)
        except Exception:
            logging.getLogger(__name__).exception(
                "Optional extension unavailable: %s", extension.name
            )

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        host = request.headers.get("host", "").split(":")[0]
        if host not in {"127.0.0.1", "localhost", "testserver"} and not settings.api_token:
            return JSONResponse(
                {"detail": "Remote access requires OMNITEST_API_TOKEN"}, status_code=403
            )
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
            return JSONResponse(
                {"detail": "Cross-origin requests are disabled; use the Vite proxy"},
                status_code=403,
            )
        if request.url.path.startswith("/api/") and settings.api_token:
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            if not hmac.compare_digest(token, settings.api_token):
                return JSONResponse({"detail": "Invalid API token"}, status_code=401)
        if int(request.headers.get("content-length", "0")) > 5_000_000:
            return JSONResponse({"detail": "Payload exceeds 5 MB"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

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
            "version": "0.1.0",
        }

    @app.get("/api/adapters")
    def adapters():
        return {
            "items": [asdict(f.info) for f in registry.factories.values()],
            "errors": registry.errors,
        }

    @app.get("/api/settings")
    def system_settings():
        return {
            "ai": {
                "enabled": app.state.extensions.get("ai", lambda: False)(),
                "providers": [],
                "message": "Manage optional providers at /api/ai",
            },
            "max_concurrency": settings.max_concurrency,
            "cli_programs": list(settings.cli_programs),
            "appium_profiles": list(settings.appium_profiles),
            "http_faults": list(settings.http_faults),
            "allowed_remote_hosts": sorted(settings.allowed_hosts),
            "authentication": bool(settings.api_token),
            "scheduler": "persistent interval",
            "schemas": {kind: schema.model_json_schema() for kind, schema in SCHEMAS.items()},
        }

    @app.get("/api/resources/{kind}")
    def list_resources(kind: str, project_id: str | None = None):
        if kind not in SCHEMAS:
            raise LookupError("Unknown resource kind")
        return resources.list(kind, project_id)

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
        return resource_dict(resources.get(kind, identity))

    @app.put("/api/resources/{kind}/{identity}")
    def update_resource(kind: str, identity: str, payload: ResourcePayload):
        return resources.save(kind, payload.body, payload.project_id, identity)

    @app.delete("/api/resources/{kind}/{identity}", status_code=204)
    def delete_resource(kind: str, identity: str):
        resources.delete(kind, identity)

    @app.post("/api/runs", status_code=202)
    def submit_run(request: RunRequest):
        return orchestrator.submit(request)

    @app.get("/api/runs")
    def runs(
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
    def dashboard(project_id: str | None = None):
        with db.session() as s:
            stmt = select(Run).order_by(Run.created_at.desc())
            if project_id:
                stmt = stmt.where(Run.project_id == project_id)
            rows = list(s.scalars(stmt.limit(30)))
        totals = {}
        for run in rows:
            for status, count in run.summary.get("counts", {}).items():
                totals[status] = totals.get(status, 0) + count
        total = sum(totals.values())
        return {
            "projects": len(resources.list("projects")),
            "cases": len(resources.list("cases", project_id)),
            "runs": len(rows),
            "counts": totals,
            "pass_rate": totals.get("PASSED", 0) / total if total else None,
            "recent": [run_dict(r) for r in rows],
            "window": "latest 30 runs",
        }

    dist = Path("frontend/dist").resolve()
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
