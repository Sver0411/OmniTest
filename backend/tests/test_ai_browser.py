import socket
import threading
import time

import uvicorn
from playwright.sync_api import sync_playwright

from omnitest.api.app import create_app
from omnitest.config import Settings


def test_ai_workspace_disabled_state_and_provider_editor(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        assert time.time() < deadline
        time.sleep(0.02)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/#ai")
            page.get_by_role("heading", name="智能辅助", exact=True).wait_for()
            assert not page.get_by_label("启用 AI", exact=True).is_checked()
            assert page.get_by_role("button", name="生成建议", exact=True).is_disabled()
            page.get_by_text("添加或编辑 Provider", exact=True).click()
            page.get_by_label("Provider JSON").fill(
                '{"name":"Local test config","base_url":"http://127.0.0.1:9988/v1","model":"fixture-model"}'
            )
            page.get_by_role("button", name="保存 Provider", exact=True).click()
            page.get_by_label("AI Provider", exact=True).select_option(
                label="Local test config · fixture-model"
            )
            page.get_by_label("启用 AI", exact=True).check()
            page.get_by_label("启用 AI", exact=True).uncheck()
            assert not errors
            page.screenshot(path="data/ai-workspace.png", full_page=True)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
