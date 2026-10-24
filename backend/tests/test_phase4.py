import asyncio
import base64
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI

from omnitest.adapters.appium import AppiumAdapter
from omnitest.adapters.base import ExecutionContext
from omnitest.adapters.grpc import GrpcAdapter
from omnitest.adapters.performance import HttpPerformanceAdapter
from omnitest.adapters.reliability import HttpReliabilityAdapter
from omnitest.core.ir import RunRequest, Step
from omnitest.phase4_demo import seed_phase4
from omnitest.phase4_fixture import descriptor_bytes, grpc_server, http_app


def serve_http(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("Fixture failed to start")
        time.sleep(0.01)
    return server, thread, sock, f"http://127.0.0.1:{sock.getsockname()[1]}"


@pytest.fixture
def recovery_url():
    server, thread, sock, url = serve_http(http_app())
    yield url
    server.should_exit = True
    thread.join(10)
    sock.close()


@pytest.fixture
async def rpc(stack, tmp_path):
    server, port = await grpc_server()
    stack[0].grpc_descriptor_root = tmp_path
    (tmp_path / "echo.pb").write_bytes(descriptor_bytes())
    context = ExecutionContext(
        stack[0],
        {
            "kind": "grpc",
            "config": {
                "host": "127.0.0.1",
                "port": port,
                "allow_rpc": True,
                "descriptor_set": "echo.pb",
                "allowed_methods": [
                    f"/omnitest.fixture.EchoService/{m}" for m in ["Echo", "Reject", "Slow"]
                ],
            },
        },
        tmp_path,
        {},
    )
    yield context, port
    await server.stop(0)


async def test_phase4_suite_real_protocol_load_and_faults(stack, recovery_url, rpc):
    stack[0].http_faults["local-recovery"] = {
        "url": recovery_url + "/faults/recovery",
        "body": {"failures": 2},
        "restore_url": recovery_url + "/faults/recovery",
        "restore_body": {"failures": 0},
    }
    demo = seed_phase4(stack[3], recovery_url, "127.0.0.1", rpc[1])
    assert {"grpc", "http-reliability", "appium"}.issubset(stack[2].factories)
    run = stack[4].submit(RunRequest(project_id=demo["project_id"], suite_id=demo["suite_id"]))
    await stack[4].execute(run["id"])
    report = stack[4].report(run["id"])
    assert report["summary"]["counts"] == {"PASSED": 5}, report


async def test_grpc_protobuf_errors_deadline_and_cancel(rpc):
    context, _ = rpc
    adapter = GrpcAdapter()
    await adapter.check(context)
    try:
        for method, status in [
            ("Echo", "OK"),
            ("Reject", "INVALID_ARGUMENT"),
            ("Slow", "DEADLINE_EXCEEDED"),
        ]:
            output = await adapter.execute(
                Step(
                    action="unary",
                    config={
                        "method": f"/omnitest.fixture.EchoService/{method}",
                        "request": {"message": "nested", "list": [1, 2], "object": {"a": True}},
                        "timeout": 0.1,
                    },
                ),
                context,
            )
            assert output["status"] == status
            if method == "Echo":
                assert output["response"]["object"] == {"a": True}
        task = asyncio.create_task(
            adapter.execute(
                Step(
                    action="unary",
                    config={"method": "/omnitest.fixture.EchoService/Slow", "timeout": 5},
                ),
                context,
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await adapter.close()
    assert adapter.channel is None


@pytest.mark.parametrize(
    "patch,match",
    [
        ({"host": "example.com"}, "allowlist"),
        ({"descriptor_set": "../outside.pb"}, "escapes"),
        ({"allow_rpc": False}, "allow_rpc"),
    ],
)
async def test_grpc_endpoint_boundaries(rpc, patch, match):
    context, _ = rpc
    context.target["config"].update(patch)
    with pytest.raises(ValueError, match=match):
        await GrpcAdapter().check(context)


async def test_grpc_method_and_metadata_boundaries(rpc):
    context, _ = rpc
    adapter = GrpcAdapter()
    await adapter.check(context)
    try:
        with pytest.raises(ValueError, match="allowlist"):
            await adapter.execute(
                Step(action="unary", config={"method": "/other.Service/Destroy"}), context
            )
        with pytest.raises(ValueError, match="Metadata"):
            await adapter.execute(
                Step(
                    action="unary",
                    config={
                        "method": "/omnitest.fixture.EchoService/Echo",
                        "metadata": {":authority": "example.com"},
                    },
                ),
                context,
            )
    finally:
        await adapter.close()


def reliability_context(stack, tmp_path, url):
    return ExecutionContext(
        stack[0],
        {
            "kind": "http",
            "config": {
                "base_url": url,
                "allow_reliability": True,
                "allow_faults": True,
                "fault_profiles": ["test"],
            },
        },
        tmp_path,
        {},
    )


async def test_fault_rollback_after_cancel(stack, tmp_path, recovery_url):
    stack[0].http_faults["test"] = {
        "url": recovery_url + "/faults/test",
        "restore_url": recovery_url + "/faults/test",
        "body": {"failures": 100},
    }
    context = reliability_context(stack, tmp_path, recovery_url)
    adapter = HttpReliabilityAdapter()
    await adapter.check(context)
    await adapter.execute(Step(action="fault", config={"profile": "test"}), context)
    task = asyncio.create_task(
        adapter.execute(
            Step(
                action="observe",
                config={"path": "/service/test", "samples": 100, "interval_seconds": 1},
            ),
            context,
        )
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await adapter.close()
    import httpx

    async with httpx.AsyncClient(trust_env=False) as client:
        assert (await client.get(recovery_url + "/service/test")).status_code == 200


async def test_reliability_failure_and_healthy_baseline_are_distinct(stack, tmp_path, recovery_url):
    context = reliability_context(stack, tmp_path, recovery_url)
    adapter = HttpReliabilityAdapter()
    await adapter.check(context)
    try:
        healthy = await adapter.execute(
            Step(
                action="observe", config={"path": "/health", "samples": 2, "interval_seconds": 0.1}
            ),
            context,
        )
        assert healthy["stable"] and not healthy["failure_observed"] and not healthy["recovered"]
        assert healthy["recovery_seconds"] is None
        unavailable = await adapter.execute(
            Step(
                action="observe", config={"path": "/missing", "samples": 2, "interval_seconds": 0.1}
            ),
            context,
        )
        assert unavailable["availability"] == 0 and not unavailable["recovered"]
        with pytest.raises(ValueError, match="authorization"):
            context.target["config"]["allow_faults"] = False
            await adapter.execute(Step(action="fault", config={"profile": "test"}), context)
        with pytest.raises(ValueError, match="origin"):
            await adapter.execute(
                Step(action="observe", config={"path": "http://127.0.0.1:1"}), context
            )
    finally:
        await adapter.close()


async def test_stages_validate_all_phases_before_sending_and_obey_rate(
    stack, fixture_url, tmp_path
):
    context = ExecutionContext(
        stack[0], {"config": {"base_url": fixture_url, "allow_performance": True}}, tmp_path, {}
    )
    adapter = HttpPerformanceAdapter()
    await adapter.check(context)
    try:
        stage = {"path": "/health", "requests": 2, "rate_per_second": 10}
        output = await adapter.execute(
            Step(action="stages", config={"stages": [stage, stage]}), context
        )
        assert output["requests"] == 4 and output["error_rate"] == 0
        assert len(output["stages"]) == 2
        assert sum(stage["elapsed_seconds"] for stage in output["stages"]) >= 0.299
        with pytest.raises(ValueError, match="origin"):
            await adapter.execute(
                Step(action="stages", config={"stages": [stage, {"path": "http://127.0.0.1:1/"}]}),
                context,
            )
        with pytest.raises(ValueError):
            await adapter.execute(Step(action="stages", config={"stages": [stage] * 6}), context)
    finally:
        await adapter.close()


@pytest.fixture
def webdriver_protocol():
    app = FastAPI()
    state = {"sessions": set(), "text": "", "clicks": 0}

    @app.post("/session")
    async def session():
        await asyncio.sleep(state.get("delay", 0))
        state["sessions"].add("session1")
        return {"value": {"sessionId": "session1", "capabilities": {}}}

    @app.delete("/session/session1")
    async def close():
        state["sessions"].remove("session1")
        return {"value": None}

    @app.post("/session/session1/timeouts")
    async def timeouts():
        if state.get("fail_timeouts"):
            return {"value": {"error": "unknown error", "message": "Initialization failed"}}
        return {"value": None}

    @app.post("/session/session1/element")
    async def element():
        return {"value": {"element-6066-11e4-a52e-4f735466cecf": "element1"}}

    @app.api_route("/session/session1/element/element1/{action}", methods=["GET", "POST"])
    async def command(action: str, body: dict | None = None):
        if action == "clear":
            state["text"] = ""
        if action == "value":
            state["text"] += body["text"]
        if action == "click":
            state["clicks"] += 1
        return {"value": state["text"] if action == "text" else True}

    @app.get("/session/session1/screenshot")
    async def screenshot():
        return {"value": base64.b64encode(b"\x89PNG\r\n\x1a\nprotocol-test").decode()}

    server, thread, sock, url = serve_http(app)
    yield state, url
    server.should_exit = True
    thread.join(10)
    sock.close()


async def test_appium_w3c_lifecycle_not_device_acceptance(stack, tmp_path, webdriver_protocol):
    state, url = webdriver_protocol
    stack[0].appium_profiles["fixture"] = {
        "server_url": url,
        "capabilities": {"platformName": "Windows", "appium:automationName": "Wincore"},
    }
    context = ExecutionContext(
        stack[0], {"kind": "windows", "config": {"profile": "fixture"}}, tmp_path, {}
    )
    adapter = AppiumAdapter()
    await adapter.check(context)
    try:
        await adapter.execute(
            Step(action="fill", config={"value": "input", "text": "hello"}), context
        )
        output = await adapter.execute(Step(action="inspect", config={"value": "input"}), context)
        assert output == {"text": "hello", "displayed": True, "enabled": True}
        await adapter.execute(Step(action="click", config={"value": "button"}), context)
        await adapter.execute(Step(id="screen", action="screenshot"), context)
        assert state["clicks"] == 1 and (tmp_path / "screen.png").is_file()
        with pytest.raises(ValueError):
            await adapter.execute(
                Step(action="execute_script", config={"script": "anything"}), context
            )
    finally:
        await adapter.close()
    assert state["sessions"] == set()


async def test_appium_requires_admin_profile(stack, tmp_path):
    context = ExecutionContext(
        stack[0], {"kind": "android", "config": {"profile": "missing"}}, tmp_path, {}
    )
    with pytest.raises(ValueError, match="administrator"):
        await AppiumAdapter().check(context)


@pytest.mark.parametrize("scenario", ["cancel_opening", "fail_timeouts"])
async def test_appium_partial_initialization_releases_session(
    stack, tmp_path, webdriver_protocol, scenario
):
    state, url = webdriver_protocol
    stack[0].appium_profiles["fixture"] = {
        "server_url": url,
        "capabilities": {"platformName": "Windows"},
    }
    context = ExecutionContext(
        stack[0], {"kind": "windows", "config": {"profile": "fixture"}}, tmp_path, {}
    )
    adapter = AppiumAdapter()
    if scenario == "cancel_opening":
        state["delay"] = 0.1
        task = asyncio.create_task(adapter.check(context))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        state["fail_timeouts"] = True
        with pytest.raises(RuntimeError, match="Initialization failed"):
            await adapter.check(context)
    await adapter.close()
    assert not state["sessions"]
