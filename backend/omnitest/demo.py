"""Seed genuine test definitions without fabricating any execution records."""

from omnitest.core.ir import TestIR


def seed_demo(resources, base_url):
    existing = next(
        (p for p in resources.list("projects") if p["body"]["name"] == "OmniTest Local Demo"), None
    )
    if existing:
        return {
            "project_id": existing["id"],
            "message": "Demo already exists",
            "suites": resources.list("suites", existing["id"]),
        }
    project = resources.save(
        "projects",
        {
            "name": "OmniTest Local Demo",
            "description": "API / Web / CLI reproducible acceptance project",
        },
    )
    pid = project["id"]
    http = resources.save(
        "targets",
        {
            "name": "Local fixture service",
            "kind": "http",
            "authorized": True,
            "config": {"base_url": base_url, "allow_mutations": True},
        },
        pid,
    )["id"]
    cli = resources.save(
        "targets",
        {
            "name": "Controlled fixture program",
            "kind": "cli",
            "authorized": True,
            "config": {"program": "demo-tool"},
        },
        pid,
    )["id"]
    ids = []

    def assertion(path, expected, op="eq"):
        return {"path": path, "op": op, "expected": expected}

    def step(action, config=None, assertions=None, id=None):
        value = {"action": action, "config": config or {}, "assertions": assertions or []}
        if id:
            value["id"] = id
        return value

    def case(name, adapter, target, steps, **extra):
        value = TestIR(
            name=name,
            adapter=adapter,
            target=target,
            steps=steps,
            tags=["demo", "functional"],
            **extra,
        )
        row = resources.save("cases", value.model_dump(), pid)
        ids.append(row["id"])
        return row["id"]

    case(
        "API · health status",
        "api",
        http,
        [step("request", {"path": "health"}, [assertion("$.status_code", 200)])],
    )
    case(
        "API · JSON version",
        "api",
        http,
        [step("request", {"path": "health"}, [assertion("$.json.version", 1)])],
    )
    case(
        "API · query filter",
        "api",
        http,
        [
            step(
                "request",
                {"path": "items", "query": {"q": "banana"}},
                [assertion("$.json.count", 1), assertion("$.json.items[0].name", "banana")],
            )
        ],
    )
    case(
        "API · list limit",
        "api",
        http,
        [step("request", {"path": "items", "query": {"limit": 2}}, [assertion("$.json.count", 2)])],
    )
    case(
        "API · JSON body and header",
        "api",
        http,
        [
            step(
                "request",
                {
                    "path": "echo",
                    "method": "POST",
                    "headers": {"x-test": "omni"},
                    "json": {"name": "test"},
                },
                [assertion("$.json.body.name", "test"), assertion("$.json.header", "omni")],
            )
        ],
    )
    case(
        "API · missing endpoint",
        "api",
        http,
        [step("request", {"path": "missing"}, [assertion("$.status_code", 404)])],
    )
    case(
        "API · text and content type",
        "api",
        http,
        [
            step(
                "request",
                {"path": "text"},
                [
                    assertion("$.text", "automation", "contains"),
                    assertion("$.headers['content-type']", "text/plain", "contains"),
                ],
            )
        ],
    )
    case(
        "API · redirects stay explicit",
        "api",
        http,
        [step("request", {"path": "redirect"}, [assertion("$.status_code", 302)])],
    )
    case(
        "Web · page title",
        "web",
        http,
        [step("goto", {}, [assertion("$.title", "OmniTest Fixture")])],
    )
    case(
        "Web · visible heading",
        "web",
        http,
        [
            step("goto"),
            step(
                "inspect",
                {"selector": "h1"},
                [assertion("$.visible", True), assertion("$.text", "Automation fixture")],
            ),
        ],
    )
    case(
        "Web · fill input",
        "web",
        http,
        [
            step("goto"),
            step(
                "fill",
                {"selector": "#name", "value": "Ada", "read_value": True},
                [assertion("$.value", "Ada")],
            ),
        ],
    )
    case(
        "Web · greeting interaction",
        "web",
        http,
        [
            step("goto"),
            step("fill", {"selector": "#name", "value": "${name}"}),
            step("click", {"selector": "#greet"}),
            step("inspect", {"selector": "#greeting"}, [assertion("$.text", "Hello, ${name}")]),
        ],
        parameters={"name": "Ada"},
    )
    case(
        "Web · reveal and wait",
        "web",
        http,
        [
            step("goto"),
            step("click", {"selector": "#toggle"}),
            step("wait", {"selector": "#details"}, [assertion("$.visible", True)]),
        ],
    )
    case(
        "Web · navigate link",
        "web",
        http,
        [
            step("goto"),
            step("click", {"selector": "#next"}),
            step("inspect", {"selector": "h1"}, [assertion("$.text", "Second page")]),
        ],
    )
    case(
        "Web · collection count and screenshot",
        "web",
        http,
        [
            step("goto"),
            step("inspect", {"selector": "#products li"}, [assertion("$.count", 3)]),
            step("screenshot"),
        ],
    )
    case(
        "CLI · stdout",
        "cli",
        cli,
        [
            step(
                "run",
                {"args": ["echo", "hello"]},
                [assertion("$.stdout", "hello\n"), assertion("$.exit_code", 0)],
            )
        ],
    )
    case(
        "CLI · arithmetic",
        "cli",
        cli,
        [step("run", {"args": ["sum", "12", "8"]}, [assertion("$.stdout", "20\n")])],
    )
    case(
        "CLI · nonzero and stderr",
        "cli",
        cli,
        [
            step(
                "run",
                {"args": ["fail"]},
                [
                    assertion("$.exit_code", 7),
                    assertion("$.stderr", "expected diagnostic", "contains"),
                ],
            )
        ],
    )
    case(
        "CLI · output file",
        "cli",
        cli,
        [
            step(
                "run",
                {"args": ["write", "fixture data"], "files": ["output.txt"]},
                [
                    assertion("$.files['output.txt'].exists", True),
                    assertion("$.files['output.txt'].text", "fixture data"),
                ],
            )
        ],
    )
    case(
        "CLI · runtime threshold",
        "cli",
        cli,
        [step("run", {"args": ["echo", "fast"]}, [assertion("$.duration", 5, "lt")])],
    )
    case(
        "CLI · parameter matrix",
        "cli",
        cli,
        [step("run", {"args": ["sum", "${a}", "${b}"]}, [assertion("$.stdout", "${expected}")])],
        datasets=[
            {"a": "1", "b": "2", "expected": "3\n"},
            {"a": "5", "b": "7", "expected": "12\n"},
        ],
    )
    suite = resources.save(
        "suites", {"name": "Acceptance · all passing cases", "case_ids": ids, "concurrency": 2}, pid
    )
    failure = case(
        "Diagnostic · intentional assertion failure",
        "api",
        http,
        [step("request", {"path": "health"}, [assertion("$.status_code", 418)])],
        expected_results="FAILED",
    )
    error = case(
        "Diagnostic · intentional adapter error",
        "cli",
        cli,
        [step("run", {"args": ["not-allowed"]})],
        expected_results="ERROR",
    )
    diagnostic = resources.save(
        "suites",
        {"name": "Acceptance · FAILED and ERROR diagnostics", "case_ids": [failure, error]},
        pid,
    )
    file_suite = None
    if "filecheck" in resources.registry.factories:
        target = resources.save(
            "targets",
            {
                "name": "Authorized fixture file",
                "kind": "file",
                "authorized": True,
                "config": {"path": "sample.txt"},
            },
            pid,
        )["id"]
        fourth = case(
            "File · entry-point adapter",
            "filecheck",
            target,
            [
                step(
                    "inspect",
                    assertions=[
                        assertion("$.exists", True),
                        assertion("$.text", "OmniTest", "contains"),
                    ],
                )
            ],
        )
        file_suite = resources.save(
            "suites", {"name": "Acceptance · fourth adapter", "case_ids": [fourth]}, pid
        )
    environment = resources.save(
        "environments", {"name": "Local", "variables": {"name": "Ada"}}, pid
    )
    return {
        "project_id": pid,
        "suite_id": suite["id"],
        "diagnostic_suite_id": diagnostic["id"],
        "file_suite_id": file_suite["id"] if file_suite else None,
        "environment_id": environment["id"],
        "cases": len(ids),
    }
