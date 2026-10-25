"""Outbound-only execution worker using the same adapter-independent runner."""

import asyncio
import base64
import hashlib
import logging
import os
import platform
import re
import time
from urllib.parse import urlsplit

import httpx

from omnitest import __version__
from omnitest.adapters.base import Registry
from omnitest.core.runner import Runner, annotate_result
from omnitest.core.security import Redactor, contained
from omnitest.services.lock import WorkerLock
from omnitest.services.nodes import MAX_EVIDENCE_FILE, Inventory, evidence_path

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, settings, coordinator, token_env):
        parts = urlsplit(coordinator)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or parts.path not in {"", "/"}
        ):
            raise ValueError("Coordinator must be an HTTP(S) origin without embedded credentials")
        if parts.scheme != "https" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Remote coordinators require HTTPS")
        if not re.fullmatch(r"OMNITEST_NODE_[A-Z0-9_]+", token_env):
            raise ValueError("Worker token must use an OMNITEST_NODE_ environment reference")
        token = os.getenv(token_env, "")
        if len(token) < 32:
            raise ValueError("Worker requires an environment token of at least 32 characters")
        self.settings, self.coordinator, self.token = settings, coordinator, token
        self.registry = Registry(settings.plugins)
        self.runner = Runner(settings, self.registry)
        self.inventory = Inventory(
            version=__version__,
            os=platform.system(),
            python=platform.python_version(),
            adapters={
                name: factory.info.version for name, factory in self.registry.factories.items()
            },
        )

    async def serve(self, once=False):
        lock = WorkerLock(self.settings.data_dir)
        lock.acquire()
        try:
            async with httpx.AsyncClient(
                base_url=self.coordinator,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=5,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                while True:
                    try:
                        response = await client.post(
                            "/api/worker/claim", json=self.inventory.model_dump()
                        )
                        response.raise_for_status()
                        assignment = response.json()["assignment"]
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code in {401, 403, 404, 422}:
                            raise ValueError("Coordinator rejected worker registration") from None
                        log.warning("Coordinator unavailable; waiting before polling")
                        await asyncio.sleep(2)
                        continue
                    except httpx.TransportError:
                        log.warning("Coordinator connection lost; waiting before polling")
                        await asyncio.sleep(2)
                        continue
                    if assignment:
                        await self.execute(client, assignment)
                        if once:
                            return
                    else:
                        await asyncio.sleep(1)
        finally:
            lock.release()

    async def post(self, client, path, payload):
        for attempt in range(3):
            try:
                response = await client.post(path, json=payload)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500 or attempt == 2:
                    raise
            except httpx.TransportError:
                if attempt == 2:
                    raise
            await asyncio.sleep(0.5 * (attempt + 1))

    async def execute(self, client, assignment):
        if assignment.get("protocol_version") != 1:
            raise ValueError("Unsupported coordinator execution protocol")
        run_id = assignment["run_id"]
        root = contained(self.settings.data_dir / "evidence", run_id)
        snapshot = assignment["snapshot"]
        claim = {"claim_id": assignment["claim_id"]}
        prefix = f"/api/worker/runs/{run_id}"
        allowed = set(assignment["allowed_secrets"]) & set(
            self.settings.project_secrets.get(assignment["project_id"], [])
        )
        semaphore = asyncio.Semaphore(min(snapshot["concurrency"], self.settings.max_concurrency))
        lost, cancelling = False, False
        last_contact = time.monotonic()

        async def one(item):
            try:
                async with semaphore:
                    outcome = await self.runner.execute(
                        item["case"],
                        item["target"],
                        item["variables"],
                        contained(root, item["result_id"]),
                        allowed_secrets=allowed,
                    )
                    annotate_result(outcome, item.get("selection"))
                    return item, outcome
            except asyncio.CancelledError:
                return item, {
                    "status": "CANCELLED",
                    "duration": 0,
                    "detail": {"error": "Cancelled before execution"},
                }
            except Exception as exc:
                return item, {
                    "status": "ERROR",
                    "duration": 0,
                    "detail": {"error": Redactor().clean(str(exc))},
                }

        tasks = [asyncio.create_task(one(item)) for item in snapshot["cases"]]

        async def heartbeat():
            nonlocal last_contact, lost, cancelling
            while True:
                try:
                    response = await client.post(prefix + "/heartbeat", json=claim)
                    response.raise_for_status()
                    last_contact = time.monotonic()
                    if response.json()["cancel_requested"] and not cancelling:
                        cancelling = True
                        for task in tasks:
                            if not task.cancelling():
                                task.cancel()
                except httpx.HTTPError as exc:
                    stale = (
                        isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500
                    )
                    if stale or time.monotonic() - last_contact >= assignment["lease_seconds"] - 8:
                        lost = True
                        for task in tasks:
                            if not task.cancelling():
                                task.cancel()
                        return
                await asyncio.sleep(3)

        monitor = asyncio.create_task(heartbeat())
        try:
            for completed in asyncio.as_completed(tasks):
                item, outcome = await completed
                if lost:
                    return
                result_id = item["result_id"]
                try:
                    for attempt in outcome["detail"].get("attempts", []):
                        for relative in attempt.get("evidence", []):
                            evidence_path(relative)
                            path = contained(root, f"{result_id}/{relative}")
                            if not path.is_file() or path.stat().st_size > MAX_EVIDENCE_FILE:
                                raise ValueError(
                                    "Evidence file missing or exceeds remote transfer limit"
                                )
                            content = await asyncio.to_thread(path.read_bytes)
                            await self.post(
                                client,
                                prefix + f"/evidence/{result_id}",
                                {
                                    **claim,
                                    "path": relative,
                                    "sha256": hashlib.sha256(content).hexdigest(),
                                    "content": base64.b64encode(content).decode("ascii"),
                                },
                            )
                except (ValueError, httpx.HTTPError) as exc:
                    outcome = {
                        "status": "ERROR",
                        "duration": outcome["duration"],
                        "detail": {"error": Redactor().clean(f"Evidence transfer failed: {exc}")},
                    }
                await self.post(client, prefix + f"/results/{result_id}", {**claim, **outcome})
            if not lost:
                await self.post(client, prefix + "/finish", claim)
        except httpx.HTTPError:
            log.warning("Result submission interrupted; coordinator will expire the lease")
        finally:
            for task in tasks:
                if not task.done() and not task.cancelling():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)


def run_worker(settings, coordinator, token_env, once=False):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(Worker(settings, coordinator, token_env).serve(once))
