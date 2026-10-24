import time

import pytest

from omnitest.core.assertions import AssertionEngine
from omnitest.core.ir import Assertion, RunRequest
from omnitest.demo import seed_demo
from omnitest.services.lock import WorkerLock


def test_regular_expression_has_bounded_execution():
    started = time.monotonic()
    result = AssertionEngine().evaluate(
        Assertion(op="regex", expected="(a+)+$"), "a" * 100000 + "!"
    )
    assert result["passed"] is False
    assert result["error"]
    assert time.monotonic() - started < 2


def test_worker_lock_rejects_second_scheduler(tmp_path):
    first, second = WorkerLock(tmp_path), WorkerLock(tmp_path)
    first.acquire()
    try:
        with pytest.raises(RuntimeError):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()


async def test_submitted_snapshot_survives_later_case_edits(stack, fixture_url):
    resources, orchestrator = stack[3], stack[4]
    demo = seed_demo(resources, fixture_url)
    request = RunRequest(project_id=demo["project_id"], suite_id=demo["file_suite_id"])
    run = orchestrator.submit(request)
    case_id = resources.get("suites", demo["file_suite_id"]).body["case_ids"][0]
    case = resources.get("cases", case_id)
    changed = {**case.body, "steps": [{"id": "changed", "action": "unsupported"}]}
    resources.save("cases", changed, demo["project_id"], case_id)
    await orchestrator.execute(run["id"])
    report = orchestrator.report(run["id"])
    assert report["status"] == "PASSED"
    assert report["results"][0]["detail"]["test_ir"]["steps"][0]["action"] == "inspect"
    later = orchestrator.submit(request)
    await orchestrator.execute(later["id"])
    assert orchestrator.report(later["id"])["status"] == "ERROR"


@pytest.mark.parametrize(
    "config",
    [
        {"path": "http://127.0.0.1:1/"},
        {"path": "echo", "method": "POST", "json": {"value": 1}},
    ],
)
async def test_http_scope_and_mutations_are_enforced(stack, fixture_url, config):
    resources, orchestrator = stack[3], stack[4]
    project = resources.save("projects", {"name": "HTTP scope"})["id"]
    target = resources.save(
        "targets",
        {
            "name": "Read only",
            "kind": "http",
            "authorized": True,
            "config": {"base_url": fixture_url},
        },
        project,
    )["id"]
    case = resources.save(
        "cases",
        {
            "name": "Rejected action",
            "adapter": "api",
            "target": target,
            "steps": [{"action": "request", "config": config}],
        },
        project,
    )["id"]
    run = orchestrator.submit(RunRequest(project_id=project, case_ids=[case]))
    await orchestrator.execute(run["id"])
    report = orchestrator.report(run["id"])
    assert report["status"] == "ERROR"
    assert "ValueError" in report["results"][0]["detail"]["attempts"][0]["error"]


async def test_web_assertion_failure_records_real_screenshot(stack, fixture_url):
    resources, orchestrator = stack[3], stack[4]
    project = resources.save("projects", {"name": "Browser failure"})["id"]
    target = resources.save(
        "targets",
        {
            "name": "Fixture",
            "kind": "http",
            "authorized": True,
            "config": {"base_url": fixture_url},
        },
        project,
    )["id"]
    case = resources.save(
        "cases",
        {
            "name": "Heading mismatch",
            "adapter": "web",
            "target": target,
            "steps": [
                {
                    "id": "open",
                    "action": "goto",
                    "assertions": [{"path": "$.title", "op": "eq", "expected": "Wrong title"}],
                }
            ],
        },
        project,
    )["id"]
    run = orchestrator.submit(RunRequest(project_id=project, case_ids=[case]))
    await orchestrator.execute(run["id"])
    report = orchestrator.report(run["id"])
    assert report["status"] == "FAILED"
    result = report["results"][0]
    evidence = result["detail"]["attempts"][0]["evidence"]
    assert evidence == ["1/open-assertion.png"]
    path = stack[0].data_dir / "evidence" / run["id"] / result["id"] / evidence[0]
    assert path.read_bytes().startswith(b"\x89PNG")


def test_cross_project_references_rejected(stack, fixture_url):
    resources = stack[3]
    demo = seed_demo(resources, fixture_url)
    other = resources.save("projects", {"name": "Other"})["id"]
    case = resources.list("cases", demo["project_id"])[0]
    with pytest.raises(ValueError, match="Cross-project"):
        resources.save("cases", case["body"], other)
    with pytest.raises(ValueError, match="referenced"):
        resources.delete("projects", demo["project_id"])
