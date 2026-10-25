"""Asynchronous W3C WebDriver commands scoped to administrator Appium profiles."""

import asyncio
import base64
from urllib.parse import quote

import httpx
from pydantic import Field

from omnitest.core.ir import Model
from omnitest.core.security import check_url, render

from .base import AdapterInfo

PENDING_CLEANUP: set[asyncio.Task] = set()


class Target(Model):
    profile: str


class Element(Model):
    by: str = "accessibility id"
    value: str = Field(min_length=1, max_length=4096)
    text: str = Field(default="", max_length=10000)
    clear: bool = True


class AppiumAdapter:
    info = AdapterInfo(
        "appium",
        "1.0",
        ["inspect", "click", "fill", "screenshot"],
        ["Appium server and platform driver", "administrator appium_profiles", "device or desktop"],
        ["android", "ios", "windows", "macos"],
    )

    async def request(self, method, path, body=None, timeout=30):
        async with self.client.stream(
            method, self.base + path, json=body, timeout=timeout
        ) as response:
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 8_000_000:
                    raise ValueError("Appium response exceeds 8 MB limit")
            import json

            payload = json.loads(data)
            value = payload.get("value")
            if response.is_error or isinstance(value, dict) and value.get("error"):
                message = value.get("message", value) if isinstance(value, dict) else value
                raise RuntimeError(f"Appium command failed: {message}")
            return value

    async def check(self, context):
        self.session = None
        self.opening = None
        profile = Target.model_validate(context.target["config"]).profile
        if profile not in context.settings.appium_profiles:
            raise ValueError("Unknown administrator Appium profile")
        cfg = context.settings.appium_profiles[profile]
        capabilities = render(dict(cfg["capabilities"]), {}, context.allowed_secrets)
        platform = str(capabilities.get("platformName", "")).lower()
        if platform == "mac":
            platform = "macos"
        if platform != context.target["kind"]:
            raise ValueError("Appium profile platform differs from target kind")
        self.base = cfg["server_url"].rstrip("/")
        check_url(self.base, context.settings.allowed_hosts)
        capabilities["appium:newCommandTimeout"] = 30
        self.client = httpx.AsyncClient(follow_redirects=False, trust_env=False, timeout=30)

        async def open_session():
            value = await self.request(
                "POST",
                "/session",
                {"capabilities": {"alwaysMatch": capabilities, "firstMatch": [{}]}},
                timeout=90,
            )
            self.session = quote(value["sessionId"], safe="")
            await self.request("POST", f"/session/{self.session}/timeouts", {"implicit": 5000})

        self.opening = asyncio.create_task(open_session())
        await asyncio.shield(self.opening)

    async def execute(self, step, context):
        path = f"/session/{self.session}"
        if step.action == "screenshot":
            if step.config:
                raise ValueError("screenshot does not accept configuration")
            data = await self.request("GET", path + "/screenshot")
            image = base64.b64decode(data, validate=True)
            if not image.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Appium screenshot is not PNG")
            name = f"{step.id}.png"
            (context.evidence_dir / name).write_bytes(image)
            context.evidence.append(name)
            return {"screenshot": name}
        if step.action not in {"inspect", "click", "fill"}:
            raise ValueError("Unsupported Appium action")
        cfg = Element.model_validate(step.config)
        if cfg.by not in {"accessibility id", "id", "xpath", "name", "class name"}:
            raise ValueError("Unsupported element locator")
        element = await self.request(
            "POST", path + "/element", {"using": cfg.by, "value": cfg.value}
        )
        identity = element.get("element-6066-11e4-a52e-4f735466cecf")
        if not identity:
            raise ValueError("Appium server did not return a W3C element reference")
        element_path = path + "/element/" + quote(identity, safe="")
        if step.action == "click":
            await self.request("POST", element_path + "/click", {})
            return {"clicked": True}
        if step.action == "fill":
            if cfg.clear:
                await self.request("POST", element_path + "/clear", {})
            await self.request("POST", element_path + "/value", {"text": cfg.text})
            return {"filled": True}
        return {
            "text": await self.request("GET", element_path + "/text"),
            "displayed": await self.request("GET", element_path + "/displayed"),
            "enabled": await self.request("GET", element_path + "/enabled"),
        }

    async def release(self):
        try:
            if self.opening:
                try:
                    await self.opening
                except Exception:
                    pass
            if self.session:
                await self.request("DELETE", f"/session/{self.session}")
                self.session = None
        finally:
            await self.client.aclose()

    async def close(self):
        if getattr(self, "client", None):
            cleanup = asyncio.create_task(self.release())
            # Session creation can outlive cancellation; retain cleanup until it completes.
            PENDING_CLEANUP.add(cleanup)
            cleanup.add_done_callback(PENDING_CLEANUP.discard)
            cleanup.add_done_callback(
                lambda task: task.exception() if not task.cancelled() else None
            )
            await asyncio.wait_for(asyncio.shield(cleanup), 8)
