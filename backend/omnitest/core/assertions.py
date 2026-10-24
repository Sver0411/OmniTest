"""Deterministic assertion registry with JSONPath addressing."""

import operator
from collections.abc import Callable
from typing import Any

import regex as re
from jsonpath_ng.ext import parse

from .ir import Assertion


class AssertionEngine:
    def __init__(self):
        self.operators: dict[str, Callable] = {
            "eq": operator.eq,
            "ne": operator.ne,
            "contains": lambda a, b: b in a,
            "not_contains": lambda a, b: b not in a,
            "gt": operator.gt,
            "lt": operator.lt,
            "gte": operator.ge,
            "lte": operator.le,
            "regex": lambda a, b: re.search(b, str(a), timeout=0.05) is not None,
            "exists": lambda a, b: (a is not None) == b,
            "set_eq": lambda a, b: set(a) == set(b),
            "status_code": operator.eq,
            "exit_code": operator.eq,
            "visible": operator.eq,
            "file_exists": operator.eq,
        }

    def register(self, name: str, fn: Callable):
        if name in self.operators:
            raise ValueError(f"Assertion already registered: {name}")
        self.operators[name] = fn

    def evaluate(self, assertion: Assertion, context: Any) -> dict:
        result = {**assertion.model_dump(), "actual": None, "passed": False, "error": None}
        try:
            matches = [m.value for m in parse(assertion.path).find(context)]
            if not matches and assertion.op != "exists":
                raise ValueError(f"No value at {assertion.path}")
            actual = matches[0] if len(matches) == 1 else matches or None
            result["actual"] = actual
            result["passed"] = bool(self.operators[assertion.op](actual, assertion.expected))
        except (ValueError, TypeError, KeyError, re.error, TimeoutError) as exc:
            result["error"] = str(exc)
        return result

    def check(self, assertions: list[Assertion], context: Any) -> list[dict]:
        return [self.evaluate(a, context) for a in assertions]
