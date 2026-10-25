import asyncio
import json
import os
import sys
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from omnitest.adapters.appium import AppiumAdapter
from omnitest.adapters.base import AdapterInfo, ExecutionContext
from omnitest.adapters.cli import CliAdapter
from omnitest.adapters.reliability import HttpReliabilityAdapter
from omnitest.api.app import create_app
from omnitest.config import Settings
from omnitest.core.assertions import AssertionEngine
from omnitest.core.ir import Assertion, RunRequest, Step
from omnitest.core.runner import Runner
from omnitest.core.security import Redactor, check_url
from omnitest.db import CaseResult, Run
from omnitest.worker import Worker


async def test_administrator_profiles_obey_project_secret_scope(stack, monkeypatch, tmp_path):
    secret = "OMNITEST_SECRET_PROFILE_CREDENTIAL"
    monkeypatch.setenv(secret, "private-profile-credential")
    stack[0].appium_profiles["scoped"] = {
        "server_url": "http://127.0.0.1:4723",
        "capabilities": {
            "platformName": "Android",
            "appium:testCredential": "${secret." + secret + "}",
        },
    }
    context = ExecutionContext(
        stack[0],
        {"kind": "android", "config": {"profile": "scoped"}},
        tmp_path,
        {},
        allowed_secrets=set(),
    )
    with pytest.raises(ValueError, match="not authorized for this project"):
        await AppiumAdapter().check(context)
    stack[0].http_faults["scoped"] = {
        "url": "http://127.0.0.1:1/fault",
        "restore_url": "http://127.0.0.1:1/restore",
        "headers": {"X-Test-Credential": "${secret." + secret + "}"},
    }
    context.target = {
        "config": {
            "base_url": "http://127.0.0.1:1",
            "allow_reliability": True,
            "allow_faults": True,
            "fault_profiles": ["scoped"],
        }
    }
    adapter = HttpReliabilityAdapter()
    await adapter.check(context)
    try:
        with pytest.raises(ValueError, match="not authorized for this project"):
            await adapter.execute(Step(action="fault", config={"profile": "scoped"}), context)
    finally:
        adapter.active_fault = None
        await adapter.close()


@pytest.mark.parametrize(
    "url", ["http://:password@localhost/", "http://@localhost/", "http://user:password@localhost/"]
)
def test_target_urls_reject_all_embedded_credentials(url):
    with pytest.raises(ValueError, match="embedded credentials"):
        check_url(url, set())


@pytest.mark.parametrize("parent_exits", [False, True])
async def test_cli_timeout_stops_descendant_processes(stack, tmp_path, parent_exits):
    if parent_exits and os.name == "nt":
        pytest.skip("Windows tree termination requires the profile's parent to remain alive")
    marker = tmp_path / "descendant-survived.txt"
    child = f"import time;from pathlib import Path;time.sleep(1);Path({str(marker)!r}).write_text('alive')"
    script = f"import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',{child!r}]);"
    if not parent_exits:
        script += "time.sleep(30)"
    stack[0].cli_programs["descendant"] = {"argv": [sys.executable, "-c", script], "arguments": []}
    outcome = await Runner(stack[0], stack[2]).execute(
        {
            "name": "Tree cancellation",
            "adapter": "cli",
            "target": "t",
            "timeout": 0.3,
            "steps": [{"action": "run"}],
        },
        {"authorized": True, "config": {"program": "descendant"}},
        {},
        tmp_path / "evidence",
    )
    assert outcome["status"] == "ERROR" and "timeout" in outcome["detail"]["attempts"][0]["error"]
    await asyncio.sleep(1.1)
    assert not marker.exists()


async def test_cli_output_limit_finishes_without_leaking_pipe_readers(stack, tmp_path):
    stack[0].cli_programs["large-output"] = {
        "argv": [
            sys.executable,
            "-c",
            "import sys;sys.stdout.write('x'*2000000);sys.stdout.flush()",
        ],
        "arguments": [],
    }
    context = ExecutionContext(stack[0], {"config": {"program": "large-output"}}, tmp_path, {})
    adapter = CliAdapter()
    await adapter.check(context)
    with pytest.raises(ValueError, match="1 MB"):
        await asyncio.wait_for(adapter.execute(Step(action="run"), context), 3)
    assert adapter.process is None


async def test_chunked_request_is_rejected_before_reading_entire_body(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    sent = 0

    async def chunks():
        nonlocal sent
        for _ in range(10):
            sent += 1
            yield b" " * 1_000_000

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://127.0.0.1"
        ) as client:
            response = await client.post("/api/cases/import", content=chunks())
    assert response.status_code == 413
    assert sent == 6


def test_invalid_jsonpath_reports_assertion_error():
    result = AssertionEngine().evaluate(Assertion(path="$[invalid", op="eq", expected=1), {})
    assert not result["passed"] and result["error"]


async def test_parameter_resolution_error_identifies_step(stack):
    request = cli_request(
        stack,
        steps=[
            {"id": "resolve-input", "action": "run", "config": {"args": ["echo", "${missing}"]}}
        ],
    )
    run = stack[4].submit(request)
    await stack[4].execute(run["id"])
    step = stack[4].report(run["id"])["results"][0]["detail"]["attempts"][0]["steps"][0]
    assert step["id"] == "resolve-input" and step["status"] == "ERROR"
    assert "missing" in step["error"] and step["duration"] >= 0


async def test_cli_does_not_inherit_unrelated_host_credentials(stack, monkeypatch, tmp_path):
    settings = stack[0]
    for key in ["AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "OMNITEST_API_TOKEN"]:
        monkeypatch.setenv(key, "host-only-credential")
    monkeypatch.setenv("TEST_TOOL_REGION", "eu-test")
    settings.cli_programs["environment"] = {
        "argv": [
            sys.executable,
            "-c",
            "import os,json;print(json.dumps({k:os.getenv(k) for k in ['AWS_SECRET_ACCESS_KEY','GITHUB_TOKEN','OMNITEST_API_TOKEN','TEST_TOOL_REGION','TOOL_MODE']}))",
        ],
        "arguments": [],
        "inherit_env": ["TEST_TOOL_REGION"],
        "env": {"TOOL_MODE": "test"},
    }
    context = ExecutionContext(settings, {"config": {"program": "environment"}}, tmp_path, {})
    adapter = CliAdapter()
    await adapter.check(context)
    output = await adapter.execute(Step(action="run"), context)
    environment = json.loads(output["stdout"])
    assert "host-only-credential" not in environment.values()
    assert environment["TEST_TOOL_REGION"] == "eu-test"
    assert environment["TOOL_MODE"] == "test"


def test_redactor_covers_bootstrap_token_empty_and_overlapping_secrets(monkeypatch):
    monkeypatch.setenv("OMNITEST_API_TOKEN", "bootstrap-credential")
    monkeypatch.setenv("OMNITEST_SECRET_EMPTY", "")
    monkeypatch.setenv("OMNITEST_SECRET_SHORT", "private")
    monkeypatch.setenv("OMNITEST_SECRET_LONG", "private-complete-value")
    assert Redactor().clean("bootstrap-credential private-complete-value") == (
        "[REDACTED] [REDACTED]"
    )


def cli_request(stack, **extra):
    resources = stack[3]
    project = resources.save("projects", {"name": "Regression"})["id"]
    target = resources.save(
        "targets",
        {"name": "Program", "kind": "cli", "authorized": True, "config": {"program": "demo-tool"}},
        project,
    )["id"]
    case = resources.save(
        "cases",
        {
            "name": "Controlled execution",
            "adapter": "cli",
            "target": target,
            "steps": [{"action": "run", "config": {"args": ["echo", "hello"]}}],
            **extra,
        },
        project,
    )["id"]
    return RunRequest(project_id=project, case_ids=[case])


async def test_bootstrap_authenticated_mode_enforces_project_secret_scope(stack, monkeypatch):
    stack[0].api_token = "bootstrap-credential"
    monkeypatch.setenv("OMNITEST_SECRET_OTHER_PROJECT", "private-other-project")
    request = cli_request(
        stack,
        steps=[
            {
                "action": "run",
                "config": {"args": ["echo", "${secret.OMNITEST_SECRET_OTHER_PROJECT}"]},
            }
        ],
    )
    run = stack[4].submit(request)
    await stack[4].execute(run["id"])
    report = stack[4].report(run["id"])
    assert report["status"] == "ERROR"
    assert "not authorized for this project" in json.dumps(report)


async def test_repeated_local_cancel_does_not_interrupt_cleanup(stack):
    entered, closing, release, closed = (asyncio.Event() for _ in range(4))

    class Adapter:
        info = AdapterInfo("cleanup-test", "1", ["wait"], [], ["cli"])

        async def check(self, context):
            pass

        async def execute(self, step, context):
            entered.set()
            await asyncio.Event().wait()

        async def close(self):
            closing.set()
            await release.wait()
            closed.set()

    stack[2].register(Adapter)
    request = cli_request(stack, adapter="cleanup-test", steps=[{"action": "wait"}])
    orchestrator = stack[4]
    run = orchestrator.submit(request)
    task = asyncio.create_task(orchestrator.execute(run["id"]))
    orchestrator.active[run["id"]] = task
    try:
        await asyncio.wait_for(entered.wait(), 2)
        orchestrator.cancel(run["id"])
        await asyncio.wait_for(closing.wait(), 2)
        orchestrator.cancel(run["id"])
    finally:
        release.set()
        await asyncio.wait_for(task, 2)
        orchestrator.active.pop(run["id"], None)
    assert closed.is_set()
    report = orchestrator.report(run["id"])
    assert report["status"] == "CANCELLED"
    assert report["cancel_requested"]
    assert report["results"][0]["detail"]["attempts"][0]["status"] == "CANCELLED"


async def test_interrupted_run_preserves_completed_results_and_summary(stack):
    request = cli_request(stack, datasets=[{"row": 1}, {"row": 2}])
    orchestrator, db = stack[4], stack[1]
    run = orchestrator.submit(request)
    with db.session.begin() as session:
        session.get(Run, run["id"]).status = "RUNNING"
        results = list(session.scalars(select(CaseResult).where(CaseResult.run_id == run["id"])))
        results[0].status = "PASSED"
        results[1].status = "RUNNING"
    await orchestrator.start()
    await orchestrator.stop()
    report = orchestrator.report(run["id"])
    assert report["summary"]["counts"] == {"PASSED": 1, "ERROR": 1}
    assert report["summary"]["total"] == 2
    assert report["summary"]["pass_rate"] == 0.5
    assert "not replayed" in report["summary"]["error"]


async def test_evidence_failure_keeps_step_diagnostics_and_redacts_text(
    stack, monkeypatch, tmp_path
):
    monkeypatch.setenv("OMNITEST_SECRET_VALUE", "private-artifact-value")

    class Adapter:
        info = AdapterInfo("artifact-test", "1", ["write"], [], ["cli"])

        async def check(self, context):
            pass

        async def execute(self, step, context):
            (context.evidence_dir / "output.yaml").write_text(
                "private-artifact-value", encoding="utf-8"
            )
            context.evidence.extend(["output.yaml", "missing.log"])
            return {"actual": "useful-step-output"}

        async def close(self):
            pass

    stack[2].register(Adapter)
    outcome = await Runner(stack[0], stack[2]).execute(
        {
            "name": "Evidence",
            "adapter": "artifact-test",
            "target": "t",
            "steps": [{"action": "write"}],
        },
        {"authorized": True},
        {},
        tmp_path,
    )
    assert outcome["status"] == "ERROR"
    attempt = outcome["detail"]["attempts"][0]
    assert attempt["steps"][0]["output"]["actual"] == "useful-step-output"
    assert attempt["evidence"] == ["1/output.yaml"]
    assert "missing.log" in attempt["error"]
    assert (tmp_path / "1/output.yaml").read_text() == "[REDACTED]"


async def test_worker_submits_completed_case_before_slow_case_finishes(stack, monkeypatch):
    monkeypatch.setenv("OMNITEST_NODE_TEST", "node-test-credential-" * 3)
    worker = Worker(stack[0], "http://127.0.0.1:8000", "OMNITEST_NODE_TEST")
    release, published = asyncio.Event(), asyncio.Event()

    async def execute(case, *args, **kwargs):
        if case["name"] == "slow":
            await release.wait()
        return {"status": "PASSED", "duration": 0.1, "detail": {"attempts": []}}

    worker.runner = SimpleNamespace(execute=execute)

    async def handle(request):
        if request.url.path.endswith("/results/fast"):
            published.set()
        return httpx.Response(200, json={"cancel_requested": False})

    assignment = {
        "protocol_version": 1,
        "run_id": "stream-test",
        "project_id": "project",
        "claim_id": "claim",
        "lease_seconds": 30,
        "allowed_secrets": [],
        "snapshot": {
            "concurrency": 2,
            "cases": [
                {"result_id": name, "case": {"name": name}, "target": {}, "variables": {}}
                for name in ["slow", "fast"]
            ],
        },
    }
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(handle)
    ) as client:
        task = asyncio.create_task(worker.execute(client, assignment))
        try:
            await asyncio.wait_for(published.wait(), 1)
        finally:
            release.set()
            await asyncio.wait_for(task, 3)
