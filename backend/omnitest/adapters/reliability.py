"""Bounded HTTP recovery observations without arbitrary fault commands."""

import asyncio
import time
from urllib.parse import urljoin

import httpx
from pydantic import Field

from omnitest.core.ir import Model
from omnitest.core.security import Redactor, check_url, render

from .base import AdapterInfo


class Observe(Model):
    path: str = "/health"
    samples: int = Field(default=10, ge=1, le=100)
    interval_seconds: float = Field(default=1, ge=0.1, le=30)
    request_timeout: float = Field(default=2, ge=0.05, le=10)
    expected_status: int = Field(default=200, ge=100, le=599)
    stable_samples: int = Field(default=2, ge=1, le=100)


class Fault(Model):
    profile: str


class HttpReliabilityAdapter:
    info = AdapterInfo(
        "http-reliability",
        "1.0",
        ["observe", "fault", "restore", "recovery", "availability"],
        ["httpx", "target allow_reliability=true"],
        ["http"],
    )

    async def check(self, context):
        cfg = context.target["config"]
        if cfg.get("allow_reliability") is not True:
            raise ValueError("Reliability testing requires target allow_reliability authorization")
        self.base = cfg["base_url"].rstrip("/") + "/"
        self.origin = check_url(self.base, context.settings.allowed_hosts)
        self.client = httpx.AsyncClient(follow_redirects=False, trust_env=False)
        self.active_fault = None

    async def control_fault(self, profile, restore=False):
        url = profile["restore_url"] if restore else profile["url"]
        check_url(url, self.settings.allowed_hosts)
        body = profile.get("restore_body", {}) if restore else profile.get("body", {})
        headers = render(profile.get("headers", {}), {})
        async with (
            asyncio.timeout(3),
            self.client.stream(
                "POST", url, json=render(body, {}), headers=headers, timeout=3
            ) as response,
        ):
            response.raise_for_status()
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > 65536:
                    raise ValueError("Fault control response exceeds 64 KB")

    async def execute(self, step, context):
        if step.action == "fault":
            cfg = Fault.model_validate(step.config)
            target = context.target["config"]
            if target.get("allow_faults") is not True or cfg.profile not in target.get(
                "fault_profiles", []
            ):
                raise ValueError(
                    "Fault injection requires explicit target and profile authorization"
                )
            if self.active_fault:
                raise ValueError("Restore the active fault before enabling another")
            profile = context.settings.http_faults.get(cfg.profile)
            if not profile or not profile.get("restore_url"):
                raise ValueError("Administrator fault profile requires a restore_url")
            for url in [profile["url"], profile["restore_url"]]:
                check_url(url, context.settings.allowed_hosts)
            self.settings = context.settings
            self.active_fault = profile
            await self.control_fault(profile)
            context.logs.append(f"Enabled fault profile: {cfg.profile}")
            return {"fault_enabled": True, "profile": cfg.profile}
        if step.action == "restore":
            if step.config:
                raise ValueError("restore does not accept configuration")
            if self.active_fault:
                await self.control_fault(self.active_fault, restore=True)
                self.active_fault = None
            return {"restored": True}
        if step.action != "observe":
            raise ValueError("Reliability adapter supports observe, fault or restore")
        cfg = Observe.model_validate(step.config)
        if cfg.stable_samples > cfg.samples:
            raise ValueError("stable_samples must not exceed samples")
        url = urljoin(self.base, cfg.path)
        if check_url(url, context.settings.allowed_hosts) != self.origin:
            raise ValueError("Observation escapes target origin")
        started = time.monotonic()
        timeline = []
        failure_at = recovery_at = None
        streak = 0
        streak_start = None
        for index in range(cfg.samples):
            if index:
                await asyncio.sleep(cfg.interval_seconds)
            status = None
            error = None
            try:
                async with asyncio.timeout(cfg.request_timeout):
                    async with self.client.stream(
                        "GET", url, timeout=cfg.request_timeout
                    ) as response:
                        status = response.status_code
                        size = 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > 2_000_000:
                                raise ValueError("Response exceeds 2 MB limit")
            except (httpx.HTTPError, TimeoutError, ValueError) as exc:
                error = Redactor().clean(f"{type(exc).__name__}: {exc}")
            elapsed = time.monotonic() - started
            success = status == cfg.expected_status and error is None
            timeline.append(
                {
                    "index": index,
                    "elapsed_seconds": elapsed,
                    "status_code": status,
                    "success": success,
                    "error": error,
                }
            )
            if not success:
                failure_at = elapsed if failure_at is None else failure_at
                streak = 0
                streak_start = None
                recovery_at = None
            else:
                if streak == 0:
                    streak_start = elapsed
                streak += 1
                if failure_at is not None and streak >= cfg.stable_samples and recovery_at is None:
                    recovery_at = streak_start
        successes = sum(s["success"] for s in timeline)
        return {
            "samples": len(timeline),
            "successes": successes,
            "failures": len(timeline) - successes,
            "availability": successes / len(timeline),
            "failure_observed": failure_at is not None,
            "recovered": recovery_at is not None,
            "stable": streak >= cfg.stable_samples,
            "recovery_seconds": recovery_at - failure_at if recovery_at is not None else None,
            "timeline": timeline,
        }

    async def close(self):
        if getattr(self, "client", None):
            try:
                if self.active_fault:
                    await self.control_fault(self.active_fault, restore=True)
                    self.active_fault = None
            finally:
                await self.client.aclose()
