"""Optional provider interface; generated tests require normal validation and authorization."""

from typing import Protocol


class AIProvider(Protocol):
    async def complete(self, system: str, context: dict) -> dict: ...


class AISettings:
    enabled: bool = False
    provider: str | None = None
