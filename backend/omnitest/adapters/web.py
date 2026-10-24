"""Playwright browser actions with origin enforcement and screenshots."""

from urllib.parse import urljoin, urlsplit

from playwright.async_api import async_playwright

from omnitest.core.security import Redactor, check_url

from .base import AdapterInfo


class WebAdapter:
    info = AdapterInfo(
        "web",
        "1.0",
        ["goto", "fill", "click", "wait", "inspect", "screenshot"],
        ["playwright", "Chromium browser"],
        ["http"],
    )

    async def check(self, context):
        self.base = context.target["config"]["base_url"].rstrip("/") + "/"
        self.origin = check_url(self.base, context.settings.allowed_hosts)
        self.context = context
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(
            headless=True, args=["--disable-dev-shm-usage"]
        )
        self.browser_context = await self.browser.new_context(
            accept_downloads=False, service_workers="block"
        )
        await self.browser_context.route("**/*", self.route)
        await self.browser_context.route_web_socket("**/*", lambda ws: ws.close())
        self.page = await self.browser_context.new_page()
        self.page.set_default_timeout(5000)

    async def route(self, route):
        url = route.request.url
        try:
            if urlsplit(url).scheme in {"data", "about"}:
                await route.continue_()
                return
            if check_url(url, self.context.settings.allowed_hosts) != self.origin:
                raise ValueError("Browser request escapes target origin")
            if route.request.method not in {"GET", "HEAD", "OPTIONS"} and not self.context.target[
                "config"
            ].get("allow_mutations", False):
                raise ValueError("Browser mutation is not authorized")
            await route.continue_()
        except ValueError as exc:
            self.context.logs.append(str(exc))
            await route.abort()

    async def execute(self, step, context):
        cfg = step.config
        locator = self.page.locator(cfg.get("selector", "body"))
        try:
            if step.action == "goto":
                url = urljoin(self.base, cfg.get("path", ""))
                if check_url(url, context.settings.allowed_hosts) != self.origin:
                    raise ValueError("Navigation escapes target origin")
                await self.page.goto(url, wait_until="domcontentloaded")
            elif step.action == "fill":
                await locator.fill(str(cfg["value"]))
                if Redactor().clean(str(cfg["value"])) != str(cfg["value"]):
                    await locator.evaluate("el => el.setAttribute('data-omnitest-secret', 'true')")
            elif step.action == "click":
                await locator.click()
            elif step.action == "wait":
                await locator.wait_for(state=cfg.get("state", "visible"))
            elif step.action == "screenshot":
                await self.capture(step.id, context)
            elif step.action != "inspect":
                raise ValueError(f"Unsupported Web action: {step.action}")
            count = await locator.count()
            return {
                "url": self.page.url,
                "title": await self.page.title(),
                "count": count,
                "visible": await locator.first.is_visible() if count else False,
                "text": (await locator.first.inner_text())[:100_000] if count else "",
                "value": await locator.first.input_value()
                if count and cfg.get("read_value")
                else None,
            }
        except Exception:
            await self.capture(f"{step.id}-error", context)
            raise

    async def capture(self, name, context):
        path = f"{name}.png"
        selectors = [
            "input[type=password]",
            "[data-omnitest-secret]",
            *context.target["config"].get("mask_selectors", []),
        ]
        await self.page.screenshot(
            path=str(context.evidence_dir / path),
            full_page=True,
            mask=[self.page.locator(s) for s in selectors],
        )
        context.evidence.append(path)

    async def close(self):
        if hasattr(self, "browser"):
            await self.browser.close()
        if hasattr(self, "pw"):
            await self.pw.stop()
