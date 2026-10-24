"""Optional provider interface; generated tests require normal validation and authorization."""

from typing import Protocol

from omnitest.core.ir import TestIR


class AIProvider(Protocol):
    id: str

    async def propose_cases(self, requirement: str, context: dict) -> list[TestIR]: ...
    async def analyze_failure(self, report: dict) -> str: ...


class AISettings:
    enabled: bool = False
    provider: str | None = None
