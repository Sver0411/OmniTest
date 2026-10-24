"""Reproducible definitions for the first Phase 2 adapter increment."""

import sqlite3
from pathlib import Path

from omnitest.core.ir import TestIR


def seed_phase2(resources, settings, base_url, tcp_host, tcp_port):
    existing = next(
        (p for p in resources.list("projects") if p["body"]["name"] == "OmniTest Phase 2"), None
    )
    if existing:
        return {
            "project_id": existing["id"],
            "suite_id": resources.list("suites", existing["id"])[0]["id"],
            "message": "Project already exists",
        }
    root = settings.database_root
    root.mkdir(parents=True, exist_ok=True)
    fixture = root / "catalog.sqlite3"
    if not fixture.exists():
        with sqlite3.connect(fixture) as connection:
            connection.executescript(Path("examples/catalog.sql").read_text(encoding="utf-8"))
    project = resources.save(
        "projects",
        {
            "name": "OmniTest Phase 2",
            "description": "Read-only database, TCP and bounded HTTP performance tests",
        },
    )["id"]
    database = resources.save(
        "targets",
        {
            "name": "Read-only catalog database",
            "kind": "database",
            "authorized": True,
            "config": {"path": "catalog.sqlite3"},
        },
        project,
    )["id"]
    tcp = resources.save(
        "targets",
        {
            "name": "Local TCP fixture",
            "kind": "tcp",
            "authorized": True,
            "config": {"host": tcp_host, "port": tcp_port},
        },
        project,
    )["id"]
    http = resources.save(
        "targets",
        {
            "name": "Authorized local performance target",
            "kind": "http",
            "authorized": True,
            "config": {"base_url": base_url, "allow_performance": True},
        },
        project,
    )["id"]
    cases = []

    def add(name, adapter, target, steps, **extra):
        body = TestIR(
            name=name, adapter=adapter, target=target, steps=steps, tags=["phase2"], **extra
        ).model_dump()
        cases.append(resources.save("cases", body, project)["id"])

    def assertion(path, expected, op="eq"):
        return {"path": path, "op": op, "expected": expected}

    def query(sql, expected, parameters=None):
        return {
            "action": "query",
            "config": {"sql": sql, "parameters": parameters or {}},
            "assertions": [assertion("$.scalar", expected), assertion("$.truncated", False)],
        }

    add(
        "Database · product count",
        "database",
        database,
        [query("SELECT count(*) FROM products", 3)],
    )
    add(
        "Database · bound parameter lookup",
        "database",
        database,
        [query("SELECT name FROM products WHERE id = :id", "banana", {"id": 2})],
    )
    add(
        "Database · category aggregate",
        "database",
        database,
        [
            query(
                "SELECT sum(price_cents) FROM products WHERE category = :category",
                500,
                {"category": "fruit"},
            )
        ],
    )
    add(
        "Database · parameterized category matrix",
        "database",
        database,
        [
            query(
                "SELECT count(*) FROM products WHERE category = :category",
                "${expected}",
                {"category": "${category}"},
            )
        ],
        datasets=[{"category": "fruit", "expected": 2}, {"category": "stationery", "expected": 1}],
    )
    add(
        "TCP · line echo",
        "tcp",
        tcp,
        [
            {
                "action": "exchange",
                "config": {"text": "hello\n"},
                "assertions": [assertion("$.text", "hello\n"), assertion("$.bytes_received", 6)],
            }
        ],
    )
    add(
        "TCP · session sequencing",
        "tcp",
        tcp,
        [
            {
                "action": "exchange",
                "config": {"text": text + "\n"},
                "assertions": [assertion("$.text", text + "\n")],
            }
            for text in ["first", "second"]
        ],
    )
    add(
        "TCP · binary echo",
        "tcp",
        tcp,
        [
            {
                "action": "exchange",
                "config": {"hex": "00017fff", "read_mode": "exact", "read_bytes": 4},
                "assertions": [assertion("$.hex", "00017fff")],
            }
        ],
    )
    add(
        "HTTP performance · health baseline",
        "http-performance",
        http,
        [
            {
                "action": "load",
                "config": {
                    "path": "health",
                    "requests": 5,
                    "concurrency": 2,
                    "rate_per_second": 10,
                },
                "assertions": [
                    assertion("$.requests", 5),
                    assertion("$.error_rate", 0),
                    assertion("$.latency_ms.p95", 2000, "lt"),
                ],
            }
        ],
    )
    suite = resources.save(
        "suites",
        {"name": "Phase 2 · adapter acceptance", "case_ids": cases, "concurrency": 2},
        project,
    )
    return {
        "project_id": project,
        "suite_id": suite["id"],
        "case_definitions": len(cases),
        "expanded_results": 9,
    }
