"""Bounded HTTP requests against an authorized base origin."""

from urllib.parse import urljoin

import httpx

from omnitest.core.security import check_url

from .base import AdapterInfo


class HttpAdapter:
    info = AdapterInfo("api", "1.0", ["request", "json", "headers", "query"], ["httpx"], ["http"])

    async def check(self, context):
        self.base = context.target["config"]["base_url"].rstrip("/") + "/"
        self.origin = check_url(self.base, context.settings.allowed_hosts)
        self.client = httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False)

    async def execute(self, step, context):
        if step.action != "request":
            raise ValueError(f"Unsupported API action: {step.action}")
        cfg = step.config
        url = urljoin(self.base, cfg.get("path", ""))
        if check_url(url, context.settings.allowed_hosts) != self.origin:
            raise ValueError("Request escapes target origin")
        method = cfg.get("method", "GET").upper()
        if method not in {"GET", "HEAD", "OPTIONS"} and not context.target["config"].get(
            "allow_mutations", False
        ):
            raise ValueError("Mutating requests require target allow_mutations authorization")
        async with self.client.stream(
            method,
            url,
            headers=cfg.get("headers"),
            params=cfg.get("query"),
            json=cfg.get("json"),
            content=cfg.get("body"),
        ) as response:
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 2_000_000:
                    raise ValueError("Response exceeds 2 MB limit")
            text = bytes(data).decode(response.encoding or "utf-8", errors="replace")
            import json

            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            return {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "text": text,
                "json": parsed,
                "url": str(response.url),
            }

    async def close(self):
        if hasattr(self, "client"):
            await self.client.aclose()
