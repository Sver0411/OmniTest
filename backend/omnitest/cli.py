"""Command-line management and headless execution."""

import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx

from omnitest.config import Settings
from omnitest.core.ir import RunRequest


def main():
    parser = argparse.ArgumentParser(prog="omnitest")
    parser.add_argument("--data-dir", default=os.getenv("OMNITEST_DATA_DIR", "data"))
    parser.add_argument("--url", help="Use a running API instead of opening the local database")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    demo = sub.add_parser("seed-demo")
    demo.add_argument("--base-url", default="http://127.0.0.1:8765")
    run = sub.add_parser("run")
    run.add_argument("--project", required=True)
    choice = run.add_mutually_exclusive_group(required=True)
    choice.add_argument("--suite")
    choice.add_argument("--case", action="append")
    run.add_argument("--environment")
    listing = sub.add_parser("list")
    listing.add_argument(
        "kind", choices=["projects", "targets", "cases", "suites", "environments", "schedules"]
    )
    listing.add_argument("--project")
    report = sub.add_parser("report")
    report.add_argument("run_id")
    report.add_argument("--output")
    args = parser.parse_args()
    settings = Settings(data_dir=Path(args.data_dir))
    if args.command == "serve":
        if args.host not in {"127.0.0.1", "localhost", "::1"} and not settings.api_token:
            parser.error("Non-loopback binding requires OMNITEST_API_TOKEN")
        import uvicorn

        from omnitest.api.app import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port)
        return
    if args.url:
        code = asyncio.run(remote(args, settings))
    else:
        code = asyncio.run(local(args, settings))
    raise SystemExit(code)


async def local(args, settings):
    from omnitest.adapters.base import Registry
    from omnitest.db import Database
    from omnitest.services.lock import WorkerLock
    from omnitest.services.orchestrator import Orchestrator
    from omnitest.services.resources import Resources

    lock = WorkerLock(settings.data_dir)
    lock.acquire()
    db = Database(settings.database_url)
    try:
        db.migrate()
        registry = Registry()
        resources = Resources(db, registry)
        orchestrator = Orchestrator(db, resources, settings, registry)
        if args.command == "init":
            value = {"database": str(settings.data_dir / "omnitest.db")}
        elif args.command == "seed-demo":
            from omnitest.demo import seed_demo

            value = seed_demo(resources, args.base_url)
        elif args.command == "list":
            value = resources.list(args.kind, args.project)
        elif args.command == "report":
            value = orchestrator.report(args.run_id)
        elif args.command == "run":
            run = orchestrator.submit(
                RunRequest(
                    project_id=args.project,
                    suite_id=args.suite,
                    case_ids=args.case or [],
                    environment_id=args.environment,
                )
            )
            await orchestrator.execute(run["id"])
            value = orchestrator.report(run["id"])
        emit(value, getattr(args, "output", None))
        return (
            1
            if isinstance(value, dict) and value.get("status") in {"ERROR", "FAILED", "CANCELLED"}
            else 0
        )
    finally:
        db.engine.dispose()
        lock.release()


async def remote(args, settings):
    headers = {"Authorization": f"Bearer {settings.api_token}"} if settings.api_token else {}
    async with httpx.AsyncClient(base_url=args.url, headers=headers, timeout=30) as client:
        if args.command == "list":
            response = await client.get(
                f"/api/resources/{args.kind}",
                params={"project_id": args.project} if args.project else {},
            )
        elif args.command == "report":
            response = await client.get(f"/api/runs/{args.run_id}")
        elif args.command == "run":
            response = await client.post(
                "/api/runs",
                json={
                    "project_id": args.project,
                    "suite_id": args.suite,
                    "case_ids": args.case or [],
                    "environment_id": args.environment,
                },
            )
            response.raise_for_status()
            identity = response.json()["id"]
            while True:
                response = await client.get(f"/api/runs/{identity}")
                response.raise_for_status()
                if response.json()["status"] not in {"PENDING", "RUNNING"}:
                    break
                await asyncio.sleep(0.25)
        else:
            raise ValueError("This command is only supported in local mode")
        response.raise_for_status()
        value = response.json()
        emit(value, getattr(args, "output", None))
        return (
            1
            if isinstance(value, dict) and value.get("status") in {"ERROR", "FAILED", "CANCELLED"}
            else 0
        )


def emit(value, output=None):
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(text, encoding="utf-8")
    else:
        print(text)
