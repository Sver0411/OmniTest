import asyncio
import socket
import threading
from contextlib import contextmanager
from uuid import uuid4

import pytest

from omnitest.adapters.base import ExecutionContext
from omnitest.adapters.mqtt import MqttAdapter
from omnitest.core.ir import Step
from omnitest.core.ir import TestIR as IR
from omnitest.mqtt_fixture import broker_config


@contextmanager
def running_broker(password_file=None):
    from amqtt.broker import Broker

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    ready, stop = threading.Event(), threading.Event()
    errors = []

    async def serve():
        broker = Broker(broker_config(port, password_file))
        try:
            await broker.start()
            ready.set()
            while not stop.is_set():
                await asyncio.sleep(0.05)
        except Exception as exc:
            errors.append(exc)
            ready.set()
        finally:
            await broker.shutdown()

    thread = threading.Thread(target=lambda: asyncio.run(serve()), daemon=True)
    thread.start()
    if not ready.wait(10) or errors:
        raise RuntimeError(f"MQTT broker could not start: {errors}")
    try:
        yield port
    finally:
        stop.set()
        thread.join(10)
        assert not thread.is_alive(), "MQTT broker did not shut down"


@pytest.fixture(scope="module")
def mqtt_broker():
    with running_broker() as port:
        yield port


@pytest.fixture(scope="module")
def authenticated_broker(tmp_path_factory):
    from pwdlib import PasswordHash

    password_file = tmp_path_factory.mktemp("mqtt-auth") / "passwords.txt"
    password_file.write_text(
        "qa:" + PasswordHash.recommended().hash("broker-test-password") + "\n", encoding="utf-8"
    )
    with running_broker(password_file) as port:
        yield port


async def execute(stack, port, steps, config=None, allowed_secrets=None):
    prefix = "omnitest/tests/" + uuid4().hex + "/"
    case = IR(name="MQTT protocol", target="broker", adapter="mqtt", timeout=15, steps=steps)
    target = {
        "authorized": True,
        "config": {
            "host": "127.0.0.1",
            "port": port,
            "topic_prefix": prefix,
            "allow_publish": True,
            **(config or {}),
        },
    }
    return await stack[4].runner.execute(
        case.model_dump(),
        target,
        {"topic": prefix + "telemetry"},
        stack[0].data_dir / "mqtt-evidence",
        allowed_secrets,
    )


@pytest.mark.parametrize("qos", [0, 1])
async def test_real_mqtt_publish_subscribe_and_deterministic_json_assertions(
    stack, mqtt_broker, qos
):
    result = await execute(
        stack,
        mqtt_broker,
        [
            {
                "action": "subscribe",
                "config": {"topic": "${topic}", "qos": qos},
                "assertions": [{"path": "$.acknowledged", "op": "eq", "expected": True}],
            },
            {
                "action": "publish",
                "config": {
                    "topic": "${topic}",
                    "qos": qos,
                    "json": {"temperature": 23, "online": True},
                },
            },
            {
                "action": "receive",
                "assertions": [
                    {"path": "$.json.temperature", "op": "eq", "expected": 23},
                    {"path": "$.qos", "op": "eq", "expected": qos},
                ],
            },
        ],
    )
    assert result["status"] == "PASSED", result


async def test_binary_and_retained_message_roundtrip(stack, mqtt_broker):
    result = await execute(
        stack,
        mqtt_broker,
        [
            {"action": "publish", "config": {"topic": "${topic}", "hex": "00fffe", "retain": True}},
            {"action": "subscribe", "config": {"topic": "${topic}"}},
            {
                "action": "receive",
                "assertions": [
                    {"path": "$.hex", "op": "eq", "expected": "00fffe"},
                    {"path": "$.retained", "op": "eq", "expected": True},
                ],
            },
            {"action": "publish", "config": {"topic": "${topic}", "text": "", "retain": True}},
        ],
        config={"allow_retained": True},
    )
    assert result["status"] == "PASSED", result


@pytest.mark.parametrize(
    "config,step,message",
    [
        (
            {"allow_publish": False},
            {"action": "publish", "config": {"topic": "${topic}", "text": "x"}},
            "not authorized",
        ),
        (
            {},
            {"action": "publish", "config": {"topic": "${topic}", "text": "x", "retain": True}},
            "not authorized",
        ),
        ({}, {"action": "subscribe", "config": {"topic": "outside/scope"}}, "prefix"),
        ({}, {"action": "subscribe", "config": {"topic": "${topic}/#"}}, "wildcard"),
        (
            {},
            {"action": "publish", "config": {"topic": "${topic}", "text": "x", "hex": "00"}},
            "exactly one",
        ),
        ({}, {"action": "publish", "config": {"topic": "${topic}", "text": "x" * 65537}}, "64 KB"),
        ({}, {"action": "receive", "config": {"timeout": 0.1}}, "Subscribe before"),
        ({"topic_prefix": "#/"}, {"action": "receive"}, "literal scope"),
    ],
)
async def test_mqtt_authorization_scope_and_payload_bounds(
    stack, mqtt_broker, config, step, message
):
    result = await execute(stack, mqtt_broker, [step], config)
    assert result["status"] == "ERROR" and message in str(result), result


async def test_receive_timeout_is_error_and_cancellation_closes_client(stack, mqtt_broker):
    result = await execute(
        stack,
        mqtt_broker,
        [
            {"action": "subscribe", "config": {"topic": "${topic}"}},
            {"action": "receive", "config": {"timeout": 0.1}},
        ],
    )
    assert result["status"] == "ERROR" and "MQTT operation timed out" in str(result)
    task = asyncio.create_task(
        execute(
            stack,
            mqtt_broker,
            [
                {"action": "subscribe", "config": {"topic": "${topic}"}},
                {"action": "receive", "config": {"timeout": 10}},
            ],
        )
    )
    await asyncio.sleep(0.2)
    task.cancel()
    assert (await asyncio.wait_for(task, 5))["status"] == "CANCELLED"
    assert not any(t.name.startswith("paho-mqtt-client-omnitest-") for t in threading.enumerate())


async def test_broker_credentials_obey_project_secret_scope_and_redact(
    stack, authenticated_broker, monkeypatch
):
    monkeypatch.setenv("OMNITEST_SECRET_MQTT_PASSWORD", "broker-test-password")
    config = {"username": "qa", "password_secret": "OMNITEST_SECRET_MQTT_PASSWORD"}
    step = {"action": "publish", "config": {"topic": "${topic}", "text": "check"}}
    allowed = await execute(
        stack, authenticated_broker, [step], config, {"OMNITEST_SECRET_MQTT_PASSWORD"}
    )
    assert allowed["status"] == "PASSED", allowed
    assert "broker-test-password" not in str(allowed)
    denied = await execute(stack, authenticated_broker, [step], config, set())
    assert denied["status"] == "ERROR" and "not authorized" in str(denied)
    monkeypatch.setenv("OMNITEST_SECRET_MQTT_PASSWORD", "wrong-test-password")
    rejected = await execute(stack, authenticated_broker, [step], config)
    assert rejected["status"] == "ERROR" and "rejected connection" in str(rejected), rejected


async def test_connection_failure_and_missing_publish_assertion_distinction(stack, mqtt_broker):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        unused = sock.getsockname()[1]
    failed = await execute(
        stack,
        unused,
        [{"action": "subscribe", "config": {"topic": "${topic}"}}],
        {"connect_timeout": 0.2},
    )
    assert failed["status"] == "ERROR", failed
    assertion = await execute(
        stack,
        mqtt_broker,
        [
            {"action": "subscribe", "config": {"topic": "${topic}"}},
            {"action": "publish", "config": {"topic": "${topic}", "text": "actual"}},
            {
                "action": "receive",
                "assertions": [{"path": "$.text", "op": "eq", "expected": "different"}],
            },
        ],
    )
    assert assertion["status"] == "FAILED", assertion


@pytest.mark.parametrize("oversize", [True, False])
async def test_inbound_message_and_buffer_limits_use_real_broker(stack, mqtt_broker, oversize):
    import paho.mqtt.client as mqtt

    prefix = "omnitest/bounded/" + uuid4().hex + "/"
    target = {"config": {"host": "127.0.0.1", "port": mqtt_broker, "topic_prefix": prefix}}
    context = ExecutionContext(stack[0], target, stack[0].data_dir, {})
    adapter = MqttAdapter()
    await adapter.check(context)
    try:
        await adapter.execute(Step(action="subscribe", config={"topic": prefix + "data"}), context)

        def publish():
            connected = threading.Event()
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            client.on_connect = lambda *args: connected.set()
            client.connect("127.0.0.1", mqtt_broker)
            client.loop_start()
            try:
                assert connected.wait(3)
                for _ in range(1 if oversize else 110):
                    client.publish(
                        prefix + "data", b"x" * (65537 if oversize else 1), qos=1
                    ).wait_for_publish(3)
            finally:
                client.disconnect()
                client.loop_stop()

        await asyncio.to_thread(publish)
        await asyncio.wait_for(adapter.failed.wait(), 5)
        with pytest.raises(ValueError, match="buffer or message size"):
            await adapter.execute(Step(action="receive"), context)
    finally:
        await adapter.close()
