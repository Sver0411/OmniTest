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
    schedule_type: Literal["interval", "cron"] = "interval"
    interval_seconds: int | None = Field(default=None, ge=5, le=31536000)
    cron: str | None = Field(default=None, max_length=200)
    timezone: str = Field(default="UTC", max_length=100)
    overlap: Literal["allow", "skip"] = "skip"
    misfire_policy: Literal["coalesce", "skip"] = "coalesce"
    misfire_grace_seconds: int = Field(default=60, ge=1, le=86400)
    enabled: bool = True
    next_run: float | None = None
    node_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    last_error: str | None = Field(default=None, max_length=2000)
    last_run_id: str | None = None
    last_triggered_at: float | None = None
    skipped_count: int = Field(default=0, ge=0)
    last_skip_reason: str | None = None

    @model_validator(mode="after")
    def timing(self):
        import time
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        from omnitest.services.calendar import next_occurrence

        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unknown IANA schedule timezone") from exc
        if self.schedule_type == "interval":
            if self.interval_seconds is None or self.cron is not None:
                raise ValueError("Interval schedules require interval_seconds and no cron")
        elif self.interval_seconds is not None or not self.cron:
            raise ValueError("Cron schedules require cron and no interval_seconds")
        next_occurrence(self.model_dump(), time.time())
        return self


class RunRequest(Model):
    project_id: str
    case_ids: list[str] = Field(default_factory=list, max_length=100)
    suite_id: str | None = None
    environment_id: str | None = None
    concurrency: int = Field(default=1, ge=1, le=8)
    parameters: dict[str, Any] = Field(default_factory=dict)
    node_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")

    @model_validator(mode="after")
    def selection(self):
        if bool(self.case_ids) == bool(self.suite_id):
            raise ValueError("Select either case_ids or suite_id")
        return self
