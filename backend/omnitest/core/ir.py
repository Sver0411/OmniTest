"""Serializable, domain-independent test and result contracts."""

from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def uid() -> str:
    return uuid4().hex


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Status(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class Assertion(Model):
    path: str = Field(default="$", max_length=1000)
    op: str = Field(min_length=1, max_length=100)
    expected: Any = None
    description: str = ""


class Step(Model):
    id: str = Field(default_factory=uid, pattern=r"^[a-zA-Z0-9_-]+$")
    action: str = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)
    assertions: list[Assertion] = Field(default_factory=list, max_length=100)


class TestIR(Model):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=uid)
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    target: str
    adapter: str
    preconditions: list[Assertion] = Field(default_factory=list, max_length=100)
    parameters: dict[str, Any] = Field(default_factory=dict)
    datasets: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    steps: list[Step] = Field(min_length=1, max_length=100)
    assertions: list[Assertion] = Field(default_factory=list, max_length=100)
    environment: dict[str, Any] = Field(default_factory=dict)
    timeout: float = Field(default=30, ge=0.1, le=300)
    retries: int = Field(default=0, ge=0, le=3)
    tags: list[str] = Field(default_factory=list)
    expected_results: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_steps(self):
        if len({s.id for s in self.steps}) != len(self.steps):
            raise ValueError("Step IDs must be unique")
        return self


class Project(Model):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class Target(Model):
    name: str = Field(min_length=1, max_length=200)
    kind: str
    authorized: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


class Environment(Model):
    name: str = Field(min_length=1, max_length=200)
    variables: dict[str, Any] = Field(default_factory=dict)


class Suite(Model):
    name: str = Field(min_length=1, max_length=200)
    case_ids: list[str] = Field(min_length=1, max_length=100)
    concurrency: int = Field(default=1, ge=1, le=8)
    parameters: dict[str, Any] = Field(default_factory=dict)


class Schedule(Model):
    name: str = Field(min_length=1, max_length=200)
    suite_id: str
    environment_id: str | None = None
    interval_seconds: int = Field(ge=5, le=31536000)
    enabled: bool = True
    next_run: float | None = None


class RunRequest(Model):
    project_id: str
    case_ids: list[str] = Field(default_factory=list, max_length=100)
    suite_id: str | None = None
    environment_id: str | None = None
    concurrency: int = Field(default=1, ge=1, le=8)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def selection(self):
        if bool(self.case_ids) == bool(self.suite_id):
            raise ValueError("Select either case_ids or suite_id")
        return self
