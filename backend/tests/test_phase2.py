import asyncio
import json
import sqlite3
import time

import pytest

from omnitest.adapters.base import ExecutionContext
from omnitest.adapters.database import DatabaseAdapter
from omnitest.adapters.performance import HttpPerformanceAdapter
from omnitest.adapters.tcp import TcpAdapter
from omnitest.core.ir import RunRequest, Step
from omnitest.phase2_demo import seed_phase2


@pytest.fixture
async def tcp_port():
    async def echo(reader, writer):
        try:
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    yield server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()


async def database_context(stack, tmp_path):
    root = stack[0].database_root
    root.mkdir(exist_ok=True)
    with sqlite3.connect(root / "fixture.db") as conn:
        conn.executescript(
            "CREATE TABLE items(id INTEGER, value TEXT); INSERT INTO items VALUES(1,'one'),(2,'two');"
        )
    context = ExecutionContext(stack[0], {"config": {"path": "fixture.db"}}, tmp_path, {})
    adapter = DatabaseAdapter()
    await adapter.check(context)
    return context, adapter


async def test_phase2_plugins_execute_without_runner_changes(stack, fixture_url, tcp_port):
    demo = seed_phase2(stack[3], stack[0], fixture_url, "127.0.0.1", tcp_port)
    assert {"database", "tcp", "http-performance"}.issubset(stack[2].factories)
    request = RunRequest(project_id=demo["project_id"], suite_id=demo["suite_id"])
    run = stack[4].submit(request)
    await stack[4].execute(run["id"])
    report = stack[4].report(run["id"])
    assert report["summary"]["counts"] == {"PASSED": 9}, json.dumps(report, indent=2)
    assert {r["adapter"] for r in report["results"]} == {"database", "tcp", "http-performance"}
    assert (
        stack[4].report(run["id"])["results"][-1]["detail"]["attempts"][0]["steps"][0]["output"][
            "requests"
        ]
        == 5
    )


async def test_database_parameter_binding_and_result_limit(stack, tmp_path):
    context, adapter = await database_context(stack, tmp_path)
    try:
        output = await adapter.execute(
            Step(
                action="query",
                config={"sql": "SELECT value FROM items WHERE id=:id", "parameters": {"id": 2}},
            ),
            context,
        )
        assert output["scalar"] == "two"
        output = await adapter.execute(
            Step(action="query", config={"sql": "SELECT * FROM items ORDER BY id", "max_rows": 1}),
            context,
        )
        assert output["truncated"] is True and output["row_count"] == 1
        output = await adapter.execute(
            Step(
                action="query",
                config={
                    "sql": "SELECT * FROM items WHERE value=:value",
                    "parameters": {"value": "' OR 1=1 --"},
                },
            ),
            context,
        )
        assert output["row_count"] == 0
    finally:
        await adapter.close()


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM items",
        "DROP TABLE items",
        "ATTACH DATABASE ':memory:' AS other",
        "PRAGMA database_list",
        "SELECT load_extension('anything')",
    ],
)
async def test_database_rejects_write_and_escape_operations(stack, tmp_path, sql):
    context, adapter = await database_context(stack, tmp_path)
    try:
        with pytest.raises(sqlite3.Error):
            await adapter.execute(Step(action="query", config={"sql": sql}), context)
        with sqlite3.connect(stack[0].database_root / "fixture.db") as conn:
            assert conn.execute("SELECT count(*) FROM items").fetchone()[0] == 2
    finally:
        await adapter.close()


async def test_database_query_deadline_and_task_cancellation(stack, tmp_path):
    sql = "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<1000000000) SELECT sum(x) FROM n"
    context, adapter = await database_context(stack, tmp_path)
    try:
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="interrupted"):
            await adapter.execute(
                Step(action="query", config={"sql": sql, "query_timeout": 0.02}), context
            )
        assert time.monotonic() - started < 2
        task = asyncio.create_task(
            adapter.execute(Step(action="query", config={"sql": sql, "query_timeout": 5}), context)
        )
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    finally:
        await adapter.close()


async def test_database_path_escape_rejected(stack, tmp_path):
    context = ExecutionContext(stack[0], {"config": {"path": "../omnitest.db"}}, tmp_path, {})
    with pytest.raises(ValueError, match="escapes"):
        await DatabaseAdapter().check(context)


async def test_tcp_remote_target_rejected_before_connect(stack, tmp_path):
    context = ExecutionContext(
        stack[0], {"config": {"host": "example.com", "port": 1234}}, tmp_path, {}
    )
    with pytest.raises(ValueError, match="allowlist"):
        await TcpAdapter().check(context)


async def test_tcp_timeout_and_cleanup(stack, tmp_path):
    async def silent(reader, writer):
        try:
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    context = ExecutionContext(
        stack[0],
        {"config": {"host": "127.0.0.1", "port": server.sockets[0].getsockname()[1]}},
        tmp_path,
        {},
    )
    adapter = TcpAdapter()
    try:
        await adapter.check(context)
        with pytest.raises(TimeoutError):
            await adapter.execute(
                Step(action="exchange", config={"text": "silence", "timeout": 0.05}), context
            )
    finally:
        await adapter.close()
        server.close()
        await server.wait_closed()


async def test_performance_authorization_metrics_and_rate_cap(stack, fixture_url, tmp_path):
    context = ExecutionContext(stack[0], {"config": {"base_url": fixture_url}}, tmp_path, {})
    adapter = HttpPerformanceAdapter()
    with pytest.raises(ValueError, match="allow_performance"):
        await adapter.check(context)
    context.target["config"]["allow_performance"] = True
    await adapter.check(context)
    try:
        output = await adapter.execute(
            Step(
                action="load",
                config={"path": "/health", "requests": 3, "rate_per_second": 10, "concurrency": 2},
            ),
            context,
        )
        assert output["requests"] == output["successes"] == 3
        assert output["error_rate"] == 0
        assert len(output["samples"]) == 3
        assert output["elapsed_seconds"] >= 0.19
        latency = output["latency_ms"]
        assert latency["min"] <= latency["p50"] <= latency["p95"] <= latency["max"]
        failure = await adapter.execute(
            Step(action="load", config={"path": "/missing", "requests": 2, "rate_per_second": 20}),
            context,
        )
        assert failure["error_rate"] == 1 and failure["status_codes"] == {"404": 2}
        with pytest.raises(ValueError):
            await adapter.execute(Step(action="load", config={"requests": 101}), context)
        with pytest.raises(ValueError):
            await adapter.execute(
                Step(action="load", config={"path": "http://127.0.0.1:1/"}), context
            )
    finally:
        await adapter.close()
