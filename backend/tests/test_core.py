import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from omnitest.core.assertions import AssertionEngine
from omnitest.core.ir import Assertion, RunRequest, Step
from omnitest.core.ir import TestIR as IR
from omnitest.core.security import Redactor, check_url, contained, render


@pytest.mark.parametrize(
    "op,actual,expected",
    [
        ("eq", 2, 2),
        ("ne", 2, 3),
        ("contains", "hello", "ell"),
        ("not_contains", [1, 2], 3),
        ("gt", 5, 4),
        ("lt", 3, 4),
        ("gte", 5, 5),
        ("lte", 5, 5),
        ("regex", "abc123", r"\d+"),
        ("exists", True, True),
        ("set_eq", [2, 1, 1], [1, 2]),
        ("exit_code", 0, 0),
        ("status_code", 200, 200),
        ("file_exists", True, True),
        ("visible", True, True),
    ],
)
def test_assertion_operators(op, actual, expected):
    result = AssertionEngine().evaluate(
        Assertion(path="$.value", op=op, expected=expected), {"value": actual}
    )
    assert result["passed"], result
    assert result["actual"] == actual


def test_jsonpath_missing_invalid_and_plugin():
    engine = AssertionEngine()
    assert engine.evaluate(
        Assertion(path="$.items[*].id", op="set_eq", expected=[1, 2]),
        {"items": [{"id": 1}, {"id": 2}]},
    )["passed"]
    assert engine.evaluate(Assertion(path="$.missing", op="exists", expected=False), {})["passed"]
    assert engine.evaluate(Assertion(path="$.missing", op="eq", expected=0), {})["error"]
    assert engine.evaluate(Assertion(op="unknown"), {})["error"]
    engine.register("even", lambda actual, _: actual % 2 == 0)
    assert engine.evaluate(Assertion(op="even"), 4)["passed"]
    with pytest.raises(ValueError):
        engine.register("eq", lambda a, b: True)


def test_ir_round_trip_and_validation():
    case = IR(name="CLI example", adapter="cli", target="target", steps=[Step(action="run")])
    assert IR.model_validate_json(case.model_dump_json()) == case
    with pytest.raises(ValidationError):
        IR(
            name="bad",
            adapter="cli",
            target="x",
            steps=[Step(id="same", action="run"), Step(id="same", action="run")],
        )
    with pytest.raises(ValidationError):
        RunRequest(project_id="x")
    with pytest.raises(ValidationError):
        RunRequest(project_id="x", case_ids=["a"], suite_id="b")


def test_interpolation_and_secret_redaction(monkeypatch):
    monkeypatch.setenv("OMNITEST_SECRET_TEST", "private-value")
    value = render(
        {"number": "${number}", "text": "n=${number}", "auth": "${secret.OMNITEST_SECRET_TEST}"},
        {"number": 3},
    )
    assert value == {"number": 3, "text": "n=3", "auth": "private-value"}
    assert "private-value" not in json.dumps(Redactor().clean(value))
    assert Redactor().clean({"password": "other", "text": "Bearer token.value"}) == {
        "password": "[REDACTED]",
        "text": "Bearer [REDACTED]",
    }
    with pytest.raises(ValueError):
        render("${secret.PATH}", {})


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://example.com",
        "http://user:pass@localhost",
        "http://169.254.169.254",
        "http://localhost.example.com",
    ],
)
def test_target_url_denied(url):
    with pytest.raises(ValueError):
        check_url(url, set())


def test_target_allowlist_and_path_scope(tmp_path):
    assert check_url("http://127.0.0.1:8000/path", set()) == "http://127.0.0.1:8000"
    assert check_url("https://example.com/api", {"example.com"}) == "https://example.com"
    with pytest.raises(ValueError):
        contained(tmp_path, "../outside.txt")


def test_core_never_imports_ai():
    for path in Path("backend/omnitest").rglob("*.py"):
        if "ai" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("omnitest.ai"), path
