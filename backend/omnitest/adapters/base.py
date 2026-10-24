"""Adapter contract and entry-point discovery."""

from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Protocol

from omnitest.config import Settings
from omnitest.core.ir import Step


@dataclass
class AdapterInfo:
    id: str
    version: str
    capabilities: list[str]
    requirements: list[str]
    target_kinds: list[str]


@dataclass
class ExecutionContext:
    settings: Settings
    target: dict
    evidence_dir: Path
    variables: dict
    evidence: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)


class Adapter(Protocol):
    info: AdapterInfo

    async def check(self, context: ExecutionContext) -> None: ...
    async def execute(self, step: Step, context: ExecutionContext) -> dict[str, Any]: ...
    async def close(self) -> None: ...


class Registry:
    def __init__(self, plugins=True):
        from .cli import CliAdapter
        from .http import HttpAdapter
        from .web import WebAdapter

        self.factories = {}
        self.errors = []
        for factory in [HttpAdapter, WebAdapter, CliAdapter]:
            self.register(factory)
        if plugins:
            for entry in entry_points(group="omnitest.adapters"):
                try:
                    self.register(entry.load())
                except Exception as exc:
                    self.errors.append(f"{entry.name}: {exc}")

    def register(self, factory):
        if factory.info.id in self.factories:
            raise ValueError(f"Duplicate adapter: {factory.info.id}")
        self.factories[factory.info.id] = factory

    def create(self, name):
        if name not in self.factories:
            raise ValueError(f"Adapter not installed: {name}")
        return self.factories[name]()
