"""Small, explicitly authorized HTTP workloads with individual measured samples."""

import asyncio
import math
import statistics
import time
from collections import Counter
from urllib.parse import urljoin

import httpx
from pydantic import Field

from omnitest.core.ir import Model
from omnitest.core.security import Redactor, check_url

from .base import AdapterInfo


class Workload(Model):
    path: str = "/"
    requests: int = Field(default=10, ge=1, le=100)
    concurrency: int = Field(default=1, ge=1, le=4)
    rate_per_second: float = Field(default=5, ge=0.1, le=20)
    request_timeout: float = Field(default=5, ge=0.05, le=30)
    expected_status: int = Field(default=200, ge=100, le=599)
    headers: dict[str, str] = Field(default_factory=dict)


class StagedWorkload(Model):
    stages: list[Workload] = Field(min_length=1, max_length=5)


class HttpPerformanceAdapter:
    info = AdapterInfo(
        "http-performance",
        "1.1",
        ["load", "stages", "latency", "percentiles", "throughput", "error_rate"],
        ["httpx", "target allow_performance=true"],
        ["http"],
    )

    async def check(self, context):
        cfg = context.target["config"]
        if cfg.get("allow_performance") is not True:
            raise ValueError(
                "Performance testing requires explicit target allow_performance authorization"
            )
        self.base = cfg["base_url"].rstrip("/") + "/"
        self.origin = check_url(self.base, context.settings.allowed_hosts)
        self.client = httpx.AsyncClient(
            follow_redirects=False, trust_env=False, limits=httpx.Limits(max_connections=4)
        )

    async def execute(self, step, context):
        if step.action == "stages":
            workloads = StagedWorkload.model_validate(step.config).stages
            for workload in workloads:
                self.workload_url(workload, context)
            stages = []
            for workload in workloads:
                stages.append(await self.load(workload, context))
            requests = sum(stage["requests"] for stage in stages)
            failures = sum(stage["failures"] for stage in stages)
            return {
                "stages": stages,
                "requests": requests,
                "failures": failures,
                "successes": requests - failures,
                "error_rate": failures / requests,
            }
        if step.action != "load":
            raise ValueError("Performance adapter supports load or stages")
        cfg = Workload.model_validate(step.config)
        return await self.load(cfg, context)

    def workload_url(self, cfg, context):
        url = urljoin(self.base, cfg.path)
        if check_url(url, context.settings.allowed_hosts) != self.origin:
            raise ValueError("Workload escapes target origin")
        return url

    async def load(self, cfg, context):
        url = self.workload_url(cfg, context)
        semaphore = asyncio.Semaphore(cfg.concurrency)
        rate_lock = asyncio.Lock()
        started = time.monotonic()
        next_start = max(started, getattr(self, "next_start", started))

        async def sample(index):
            nonlocal next_start
            async with semaphore:
                async with rate_lock:
                    while (remaining := next_start - time.monotonic()) > 0:
                        await asyncio.sleep(remaining)
                    next_start = time.monotonic() + 1 / cfg.rate_per_second
                    self.next_start = next_start
                begin = time.monotonic()
                status = None
                error = None
                try:
                    async with asyncio.timeout(cfg.request_timeout):
                        async with self.client.stream(
                            "GET", url, headers=cfg.headers, timeout=cfg.request_timeout
                        ) as response:
                            status = response.status_code
                            length = 0
                            async for chunk in response.aiter_bytes():
                                length += len(chunk)
                                if length > 2_000_000:
                                    raise ValueError("Response exceeds 2 MB limit")
                except (httpx.HTTPError, TimeoutError, ValueError) as exc:
                    error = Redactor().clean(f"{type(exc).__name__}: {exc}")
                return {
                    "index": index,
                    "started_seconds": begin - started,
                    "elapsed_ms": (time.monotonic() - begin) * 1000,
                    "status_code": status,
                    "success": status == cfg.expected_status and error is None,
                    "error": error,
                }

        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(sample(i)) for i in range(cfg.requests)]
        samples = [task.result() for task in tasks]
        duration = time.monotonic() - started
        latencies = sorted(s["elapsed_ms"] for s in samples)
        failures = sum(not s["success"] for s in samples)
        return {
            "requests": len(samples),
            "successes": len(samples) - failures,
            "failures": failures,
            "error_rate": failures / len(samples),
            "elapsed_seconds": duration,
            "requests_per_second": len(samples) / duration,
            "latency_ms": {
                "min": latencies[0],
                "mean": statistics.mean(latencies),
                "p50": latencies[math.ceil(len(latencies) * 0.5) - 1],
                "p95": latencies[math.ceil(len(latencies) * 0.95) - 1],
                "max": latencies[-1],
            },
            "status_codes": dict(Counter(str(s["status_code"]) for s in samples)),
            "samples": samples,
        }

    async def close(self):
        if getattr(self, "client", None):
            await self.client.aclose()
