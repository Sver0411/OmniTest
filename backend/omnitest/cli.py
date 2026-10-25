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
    from omnitest import __version__

    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    backup_parser = sub.add_parser("backup")
    backup_parser.add_argument("--output", required=True)
    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("--archive", required=True)
    sub.add_parser("plugins")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    worker = sub.add_parser("worker")
    worker.add_argument("--coordinator", required=True)
    worker.add_argument("--token-env", default="OMNITEST_NODE_TOKEN")
    worker.add_argument("--once", action="store_true", help="Exit after processing one assignment")
    demo = sub.add_parser("seed-demo")
    demo.add_argument("--base-url", default="http://127.0.0.1:8765")
    phase2 = sub.add_parser("seed-phase2")
    phase2.add_argument("--base-url", default="http://127.0.0.1:8765")
    phase2.add_argument("--tcp-host", default="127.0.0.1")
    phase2.add_argument("--tcp-port", type=int, default=8766)
    mqtt_demo = sub.add_parser("seed-mqtt")
    mqtt_demo.add_argument("--host", default="127.0.0.1")
    mqtt_demo.add_argument("--port", type=int, default=18883)
    mqtt_demo.add_argument("--username")
    mqtt_demo.add_argument("--password-secret")
    phase4 = sub.add_parser("seed-phase4")
    phase4.add_argument("--base-url", default="http://127.0.0.1:8785")
    phase4.add_argument("--grpc-host", default="127.0.0.1")
    phase4.add_argument("--grpc-port", type=int, default=8786)
    run = sub.add_parser("run")
    run.add_argument("--project", required=True)
    choice = run.add_mutually_exclusive_group(required=True)
    choice.add_argument("--suite")
    choice.add_argument("--case", action="append")
    run.add_argument("--environment")
    run.add_argument("--node", help="Execution node ID (requires --url)")
    listing = sub.add_parser("list")
    listing.add_argument(
        "kind", choices=["projects", "targets", "cases", "suites", "environments", "schedules"]
    )
    listing.add_argument("--project")
    report = sub.add_parser("report")
    report.add_argument("run_id")
    report.add_argument("--output")
    args = parser.parse_args()
    if args.command in {"backup", "restore"}:
        from omnitest.services.backup import backup, restore

        if args.url:
            parser.error("Backup and restore require local filesystem access")
        value = (
            backup(args.data_dir, args.output)
            if args.command == "backup"
            else restore(args.archive, args.data_dir)
        )
        emit(value)
        return
    if args.command == "plugins" and not args.url:
        from omnitest.adapters.base import Registry

        emit(Registry().inventory())
        return
    settings = Settings(data_dir=Path(args.data_dir))
    if args.command == "worker":
        from omnitest.worker import run_worker

        if args.url:
            parser.error("Use worker --coordinator instead of --url")
        run_worker(settings, args.coordinator, args.token_env, args.once)
        return
    if args.command == "run" and args.node and not args.url:
        parser.error("Remote node execution requires --url")
    if args.command == "serve":
        if args.host not in {"127.0.0.1", "localhost", "::1"} and not settings.authentication:
            parser.error("Non-loopback binding requires configured API credentials")
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
        elif args.command == "seed-phase2":
            from omnitest.phase2_demo import seed_phase2

            value = seed_phase2(resources, settings, args.base_url, args.tcp_host, args.tcp_port)
        elif args.command == "seed-mqtt":
            from omnitest.mqtt_demo import seed_mqtt

            value = seed_mqtt(resources, args.host, args.port, args.username, args.password_secret)
        elif args.command == "list":
            value = resources.list(args.kind, args.project)
        elif args.command == "seed-phase4":
            from omnitest.phase4_demo import seed_phase4

            value = seed_phase4(resources, args.base_url, args.grpc_host, args.grpc_port)
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
    async with httpx.AsyncClient(
        base_url=args.url, headers=headers, timeout=30, trust_env=False
    ) as client:
        if args.command == "list":
            response = await client.get(
                f"/api/resources/{args.kind}",
                params={"project_id": args.project} if args.project else {},
            )
        elif args.command == "report":
            response = await client.get(f"/api/runs/{args.run_id}")
        elif args.command == "plugins":
            response = await client.get("/api/adapters")
        elif args.command == "run":
            response = await client.post(
                "/api/runs",
                json={
                    "project_id": args.project,
                    "suite_id": args.suite,
                    "case_ids": args.case or [],
                    "environment_id": args.environment,
                    "node_id": args.node,
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
