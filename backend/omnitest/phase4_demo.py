"""Create executable Phase 4 definitions without fabricating execution history."""

from omnitest.core.ir import TestIR


def seed_phase4(resources, base_url, grpc_host, grpc_port):
    existing = next(
        (p for p in resources.list("projects") if p["body"]["name"] == "OmniTest Phase 4"), None
    )
    if existing:
        return {
            "project_id": existing["id"],
            "suite_id": resources.list("suites", existing["id"])[0]["id"],
        }
    project = resources.save(
        "projects",
        {
            "name": "OmniTest Phase 4",
            "description": "Protobuf RPC, staged load and controlled fault recovery",
        },
    )["id"]
    grpc = resources.save(
        "targets",
        {
            "name": "Local protobuf fixture",
            "kind": "grpc",
            "authorized": True,
            "config": {
                "host": grpc_host,
                "port": grpc_port,
                "descriptor_set": "echo.pb",
                "allow_rpc": True,
                "allowed_methods": [
                    f"/omnitest.fixture.EchoService/{method}"
                    for method in ["Echo", "Reject", "Slow"]
                ],
            },
        },
        project,
    )["id"]
    http = resources.save(
        "targets",
        {
            "name": "Local fault and load fixture",
            "kind": "http",
            "authorized": True,
            "config": {
                "base_url": base_url,
                "allow_performance": True,
                "allow_reliability": True,
                "allow_faults": True,
                "fault_profiles": ["local-recovery"],
            },
        },
        project,
    )["id"]
    cases = []

    def add(name, adapter, target, steps):
        case = TestIR(name=name, adapter=adapter, target=target, steps=steps, tags=["phase4"])
        cases.append(resources.save("cases", case.model_dump(), project)["id"])

    def assertion(path, expected, op="eq"):
        return {"path": path, "op": op, "expected": expected}

    for method, status, config in [
        ("Echo", "OK", {"request": {"message": "hello", "number": 42}}),
        ("Reject", "INVALID_ARGUMENT", {}),
        ("Slow", "DEADLINE_EXCEEDED", {"timeout": 0.1}),
    ]:
        checks = [assertion("$.status", status)]
        if method == "Echo":
            checks.append(assertion("$.response.message", "hello"))
        add(
            f"gRPC · {method}",
            "grpc",
            grpc,
            [
                {
                    "action": "unary",
                    "config": {"method": f"/omnitest.fixture.EchoService/{method}", **config},
                    "assertions": checks,
                }
            ],
        )
    add(
        "HTTP · staged workload",
        "http-performance",
        http,
        [
            {
                "action": "stages",
                "config": {
                    "stages": [
                        {
                            "path": "/health",
                            "requests": 3,
                            "rate_per_second": rate,
                            "concurrency": concurrency,
                        }
                        for rate, concurrency in [(5, 1), (10, 2), (20, 4)]
                    ]
                },
                "assertions": [assertion("$.requests", 9), assertion("$.error_rate", 0)],
            }
        ],
    )
    add(
        "HTTP · transient failure recovery",
        "http-reliability",
        http,
        [
            {"action": "fault", "config": {"profile": "local-recovery"}},
            {
                "action": "observe",
                "config": {
                    "path": "/service/recovery",
                    "samples": 5,
                    "interval_seconds": 0.1,
                    "stable_samples": 3,
                },
                "assertions": [
                    assertion("$.failure_observed", True),
                    assertion("$.failures", 2),
                    assertion("$.recovered", True),
                    assertion("$.stable", True),
                    assertion("$.recovery_seconds", 2, "lt"),
                ],
            },
            {"action": "restore", "assertions": [assertion("$.restored", True)]},
        ],
    )
    suite = resources.save(
        "suites",
        {"name": "Phase 4 · protocol and resilience", "case_ids": cases, "concurrency": 1},
        project,
    )["id"]
    return {"project_id": project, "suite_id": suite, "case_definitions": len(cases)}
