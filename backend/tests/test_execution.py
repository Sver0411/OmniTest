import asyncio
import json

import pytest

from omnitest.core.ir import RunRequest
from omnitest.core.ir import TestIR as IR
from omnitest.demo import seed_demo


async def run_suite(stack, demo, key="suite_id"):
    orchestrator = stack[4]
    row = orchestrator.submit(RunRequest(project_id=demo["project_id"], suite_id=demo[key]))
    await orchestrator.execute(row["id"])
    return orchestrator.report(row["id"])


async def test_three_real_adapters_repeat_and_persist(stack, fixture_url):
    demo = seed_demo(stack[3], fixture_url)
    report = await run_suite(stack, demo)
    assert report["status"] == "PASSED", json.dumps(report, ensure_ascii=False, indent=2)
    assert report["summary"]["counts"] == {"PASSED": 22}
    assert {r["adapter"] for r in report["results"]} == {"api", "web", "cli"}
    again = await run_suite(stack, demo)
    assert again["id"] != report["id"]
    assert again["summary"]["counts"] == {"PASSED": 22}
    from omnitest.db import Database
    from omnitest.services.orchestrator import Orchestrator

    db = Database(stack[0].database_url)
    restored = Orchestrator(db, stack[3], stack[0], stack[2])
    assert restored.report(report["id"])["summary"] == report["summary"]
    db.engine.dispose()


async def test_failure_error_and_fourth_entrypoint(stack, fixture_url):
    demo = seed_demo(stack[3], fixture_url)
    report = await run_suite(stack, demo, "diagnostic_suite_id")
    assert report["status"] == "ERROR"
    assert report["summary"]["counts"] == {"FAILED": 1, "ERROR": 1}
    assert (
        report["results"][0]["detail"]["attempts"][0]["steps"][0]["assertions"][0]["actual"] == 200
    )
    assert "filecheck" in stack[2].factories
    fourth = await run_suite(stack, demo, "file_suite_id")
    assert fourth["status"] == "PASSED"
    assert fourth["results"][0]["adapter"] == "filecheck"


def make_cli_case(stack, steps, **extra):
    resources = stack[3]
    project = resources.save("projects", {"name": "Controlled CLI"})["id"]
    target = resources.save(
        "targets",
        {"name": "Program", "kind": "cli", "authorized": True, "config": {"program": "demo-tool"}},
        project,
    )["id"]
    case = resources.save(
        "cases",
        IR(name="Controlled case", adapter="cli", target=target, steps=steps, **extra).model_dump(),
        project,
    )["id"]
    return RunRequest(project_id=project, case_ids=[case])


async def test_timeout_retry_and_cleanup(stack):
    request = make_cli_case(
        stack, [{"action": "run", "config": {"args": ["sleep", "5"]}}], timeout=0.2, retries=1
    )
    row = stack[4].submit(request)
    await stack[4].execute(row["id"])
    report = stack[4].report(row["id"])
    assert report["status"] == "ERROR"
    attempts = report["results"][0]["detail"]["attempts"]
    assert len(attempts) == 2
    assert all("timeout" in a["error"] for a in attempts)
    assert report["summary"]["duration"] < 4


async def test_cancel_running_and_pending(stack):
    request = make_cli_case(stack, [{"action": "run", "config": {"args": ["sleep", "5"]}}])
    pending = stack[4].submit(request)
    stack[4].cancel(pending["id"])
    assert stack[4].report(pending["id"])["status"] == "CANCELLED"
    running = stack[4].submit(request)
    await stack[4].start()
    try:
        for _ in range(100):
            if stack[4].report(running["id"])["status"] == "RUNNING":
                break
            await asyncio.sleep(0.02)
        stack[4].cancel(running["id"])
        for _ in range(100):
            report = stack[4].report(running["id"])
            if report["status"] == "CANCELLED":
                break
            await asyncio.sleep(0.02)
        assert report["status"] == "CANCELLED"
    finally:
        await stack[4].stop()


async def test_precondition_skips_and_secret_evidence(stack, monkeypatch):
    request = make_cli_case(
        stack,
        [{"action": "run", "config": {"args": ["echo", "hello"]}}],
        preconditions=[{"path": "$.parameters.enabled", "op": "eq", "expected": True}],
        parameters={"enabled": False},
    )
    run = stack[4].submit(request)
    await stack[4].execute(run["id"])
    assert stack[4].report(run["id"])["status"] == "SKIPPED"
    monkeypatch.setenv("OMNITEST_SECRET_VALUE", "privatevalue")
    request = make_cli_case(
        stack,
        [
            {
                "action": "run",
                "config": {
                    "args": ["write", "${secret.OMNITEST_SECRET_VALUE}"],
                    "files": ["output.txt"],
                },
            }
        ],
    )
    run = stack[4].submit(request)
    await stack[4].execute(run["id"])
    report = stack[4].report(run["id"])
    assert report["status"] == "PASSED"
    assert "privatevalue" not in json.dumps(report)
    path = next((stack[0].data_dir / "evidence" / run["id"]).rglob("output.txt"))
    assert path.read_text() == "[REDACTED]"


@pytest.mark.parametrize(
    "args", [["echo", "$(whoami)"], ["echo", "../../file"], ["echo", "--flag", "bad"], ["unknown"]]
)
async def test_cli_argument_policy(stack, args):
    request = make_cli_case(stack, [{"action": "run", "config": {"args": args}}])
    run = stack[4].submit(request)
    await stack[4].execute(run["id"])
    assert stack[4].report(run["id"])["status"] == "ERROR"
