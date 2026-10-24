"""Run a real native application through an administrator-owned Appium profile."""

import argparse
import asyncio
import json
from pathlib import Path

from omnitest.adapters.base import Registry
from omnitest.config import Settings
from omnitest.core.ir import RunRequest, TestIR
from omnitest.db import Database
from omnitest.services.lock import WorkerLock
from omnitest.services.orchestrator import Orchestrator
from omnitest.services.resources import Resources


async def verify(args):
    settings = Settings(data_dir=Path(args.data_dir))
    lock = WorkerLock(settings.data_dir)
    lock.acquire()
    db = Database(settings.database_url)
    try:
        db.migrate()
        registry = Registry()
        resources = Resources(db, registry)
        orchestrator = Orchestrator(db, resources, settings, registry)
        project = resources.save("projects", {"name": f"Native acceptance · {args.kind}"})["id"]
        target = resources.save(
            "targets",
            {
                "name": args.profile,
                "kind": args.kind,
                "authorized": True,
                "config": {"profile": args.profile},
            },
            project,
        )["id"]
        definition = TestIR(
            name=f"{args.kind} · native input and click",
            adapter="appium",
            target=target,
            timeout=180,
            steps=[
                {"action": "fill", "config": {"value": "message", "text": "hello omnitest"}},
                {"action": "click", "config": {"value": "apply"}},
                {
                    "action": "inspect",
                    "config": {"value": "result"},
                    "assertions": [
                        {"path": "$.text", "op": "eq", "expected": "HELLO OMNITEST"},
                        {"path": "$.displayed", "op": "eq", "expected": True},
                    ],
                },
                {"id": "native-screen", "action": "screenshot"},
            ],
        )
        case = resources.save("cases", definition.model_dump(), project)["id"]
        suite = resources.save(
            "suites", {"name": "Native application acceptance", "case_ids": [case]}, project
        )["id"]
        run = orchestrator.submit(RunRequest(project_id=project, suite_id=suite))
        await orchestrator.execute(run["id"])
        report = orchestrator.report(run["id"])
        (settings.data_dir / "device-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "project_id": project,
                    "suite_id": suite,
                    "run_id": run["id"],
                    "kind": args.kind,
                    "counts": report["summary"]["counts"],
                    "report": str(settings.data_dir / "device-report.json"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        if report["summary"]["counts"] != {"PASSED": 1}:
            raise RuntimeError(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        db.engine.dispose()
        lock.release()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", required=True, choices=["android", "windows", "ios", "macos"])
    parser.add_argument("--profile", required=True)
    parser.add_argument("--data-dir", default="data/device-acceptance")
    asyncio.run(verify(parser.parse_args()))


if __name__ == "__main__":
    main()
