"""Adapter contract and entry-point discovery."""

import re
from dataclasses import asdict, dataclass, field
from importlib.metadata import entry_points
from inspect import iscoroutinefunction
from pathlib import Path
from typing import Any, Protocol

from omnitest import __version__
from omnitest.config import Settings
from omnitest.core.ir import Step


@dataclass
class AdapterInfo:
    id: str
    version: str
    capabilities: list[str]
    requirements: list[str]
    target_kinds: list[str]
    api_version: int = 1


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
        self.packages = {}
        for factory in [HttpAdapter, WebAdapter, CliAdapter]:
            self.register(factory)
            self.packages[factory.info.id] = {"name": "omnitest", "version": __version__}
        if plugins:
            for entry in entry_points(group="omnitest.adapters"):
                try:
                    factory = entry.load()
                    self.register(factory)
                    distribution = getattr(entry, "dist", None)
                    if distribution is not None:
                        self.packages[factory.info.id] = {
                            "name": distribution.metadata["Name"],
                            "version": distribution.version,
                            "entry_point": entry.value,
                        }
                except Exception as exc:
                    self.errors.append(f"{entry.name}: {exc}")

    def register(self, factory):
        info = getattr(factory, "info", None)
        if not isinstance(info, AdapterInfo) or not info.id or not info.version:
            raise ValueError("Plugin must declare AdapterInfo with id and version")
        if (
            not callable(factory)
            or not isinstance(info.id, str)
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}", info.id)
            or not isinstance(info.version, str)
        ):
            raise ValueError("Plugin factory, id or version is invalid")
        if info.api_version != 1:
            raise ValueError(f"Unsupported adapter API version: {info.api_version}; expected 1")
        for name in ["capabilities", "target_kinds", "requirements"]:
            values = getattr(info, name)
            if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
                raise ValueError(f"Plugin {name} must be a list of non-empty strings")
        if not info.target_kinds or not info.capabilities:
            raise ValueError("Plugin must declare target kinds and capabilities")
        for name in ["check", "execute", "close"]:
            if not iscoroutinefunction(getattr(factory, name, None)):
                raise ValueError(f"Plugin must implement async {name}")
        if factory.info.id in self.factories:
            raise ValueError(f"Duplicate adapter: {factory.info.id}")
        self.factories[factory.info.id] = factory

    def inventory(self):
        return {
            "api_version": 1,
            "items": [
                {**asdict(factory.info), "package": self.packages.get(name)}
                for name, factory in self.factories.items()
            ],
            "errors": self.errors,
        }

    def create(self, name):
        if name not in self.factories:
            raise ValueError(f"Adapter not installed: {name}")
        return self.factories[name]()
