"""Standalone CLI, HTTP, scheduler, restart and browser acceptance checks."""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def wait_server(url, process):
    deadline = time.time() + 30
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Server exited with {process.returncode}")
        try:
            if httpx.get(url, timeout=1, trust_env=False).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"Server startup timeout: {url}")


def wait_run(client, identity):
    deadline = time.time() + 180
    while time.time() < deadline:
        response = client.get("/api/runs/" + identity)
        response.raise_for_status()
        result = response.json()
        if result["status"] not in {"PENDING", "RUNNING"}:
            return result
        time.sleep(0.1)
    raise RuntimeError("Execution timeout")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data/acceptance")
    parser.add_argument("--fixture-port", type=int, default=8765)
    parser.add_argument("--platform-port", type=int, default=8000)
    args = parser.parse_args()
    folder = (ROOT / args.data_dir).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    for port in [args.fixture_port, args.platform_port]:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
    env = {**os.environ, "PYTHONUTF8": "1"}
    base = f"http://127.0.0.1:{args.platform_port}"
    fixture_url = f"http://127.0.0.1:{args.fixture_port}"
    processes, logs = [], []
    started = time.monotonic()

    def launch(argv, name):
        log = (folder / (name + ".log")).open("w", encoding="utf-8")
        logs.append(log)
        process = subprocess.Popen(
            [sys.executable, *argv], cwd=ROOT, env=env, stdout=log, stderr=log
        )
        processes.append(process)
        return process

    def cli(*args, expected=0):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from omnitest.cli import main; main()",
                "--data-dir",
                str(folder),
                *args,
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            encoding="utf-8",
            timeout=180,
        )
        if result.returncode != expected:
            raise AssertionError(result.stderr + result.stdout)
        return json.loads(result.stdout)

    try:
        fixture = launch(
            [
                "-m",
                "uvicorn",
                "omnitest.demo_app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.fixture_port),
            ],
            "fixture",
        )
        wait_server(fixture_url + "/health", fixture)
        demo = cli("seed-demo", "--base-url", fixture_url)
        if "suite_id" not in demo:
            suites = demo["suites"]
            demo["suite_id"] = next(s["id"] for s in suites if "all passing" in s["body"]["name"])
            demo["diagnostic_suite_id"] = next(
                s["id"] for s in suites if "diagnostics" in s["body"]["name"]
            )
            demo["file_suite_id"] = next(
                s["id"] for s in suites if "fourth adapter" in s["body"]["name"]
            )
        reports = []
        for index in range(2):
            report = cli("run", "--project", demo["project_id"], "--suite", demo["suite_id"])
            assert report["summary"]["counts"] == {"PASSED": 22}, report
            (folder / f"cli-pass-{index + 1}.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            reports.append(report)
        diagnostic = cli(
            "run",
            "--project",
            demo["project_id"],
            "--suite",
            demo["diagnostic_suite_id"],
            expected=1,
        )
        assert diagnostic["summary"]["counts"] == {"FAILED": 1, "ERROR": 1}, diagnostic
        fourth = cli("run", "--project", demo["project_id"], "--suite", demo["file_suite_id"])
        assert fourth["status"] == "PASSED"
        platform_args = [
            "-c",
            "from omnitest.cli import main; main()",
            "--data-dir",
            str(folder),
            "serve",
            "--port",
            str(args.platform_port),
        ]
        platform = launch(platform_args, "platform")
        wait_server(base + "/api/health", platform)
        with httpx.Client(base_url=base, timeout=30, trust_env=False) as client:
            assert client.get("/api/runs/" + reports[0]["id"]).json()["status"] == "PASSED"
            response = client.post(
                "/api/resources/schedules",
                json={
                    "project_id": demo["project_id"],
                    "body": {
                        "name": "Acceptance interval trigger",
                        "suite_id": demo["file_suite_id"],
                        "interval_seconds": 5,
                        "next_run": time.time() + 2,
                    },
                },
            )
            response.raise_for_status()
            schedule = response.json()
        platform.terminate()
        platform.wait(timeout=15)
        platform = launch(platform_args, "platform-restart")
        wait_server(base + "/api/health", platform)
        with httpx.Client(base_url=base, timeout=30, trust_env=False) as client:
            deadline = time.time() + 15
            scheduled = None
            while time.time() < deadline:
                for run in client.get("/api/runs").json():
                    report = client.get("/api/runs/" + run["id"]).json()
                    if report["source"] == "schedule:" + schedule["id"]:
                        scheduled = wait_run(client, run["id"])
                        break
                if scheduled:
                    break
                time.sleep(0.1)
            assert scheduled and scheduled["status"] == "PASSED"
            schedule["body"]["enabled"] = False
            client.put(
                "/api/resources/schedules/" + schedule["id"],
                json={"project_id": demo["project_id"], "body": schedule["body"]},
            ).raise_for_status()
            assert client.get("/api/runs/" + reports[0]["id"]).json()["status"] == "PASSED"
            cli_targets = client.get(
                "/api/resources/targets", params={"project_id": demo["project_id"]}
            ).json()
            cli_target = next(t["id"] for t in cli_targets if t["body"]["kind"] == "cli")
        console_errors = []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
            page.on("pageerror", lambda error: console_errors.append(str(error)))
            page.goto(base)
            page.get_by_role("heading", name="工作台", exact=False).wait_for()
            page.get_by_label("筛选项目").select_option(demo["project_id"])
            page.get_by_role("button", name="测试执行", exact=True).click()
            page.get_by_role("heading", name="启动测试运行").wait_for()
            page.get_by_role("button", name="Acceptance · all passing cases").click()
            page.get_by_role("dialog", name="运行报告").wait_for()
            page.get_by_text("22 通过 / 22 个测试", exact=True).wait_for(timeout=120000)
            page.get_by_label("关闭报告").click()
            page.get_by_role("button", name="测试用例", exact=False).first.click()
            page.get_by_role("button", name="新建用例").first.click()
            page.get_by_label("资源 JSON").fill(
                json.dumps(
                    {
                        "name": "UI acceptance temporary case",
                        "target": cli_target,
                        "adapter": "cli",
                        "steps": [
                            {
                                "action": "run",
                                "config": {"args": ["echo", "ui"]},
                                "assertions": [
                                    {"path": "$.stdout", "op": "eq", "expected": "ui\n"}
                                ],
                            }
                        ],
                    }
                )
            )
            page.get_by_role("button", name="保存配置").click()
            page.get_by_role("dialog", name="资源编辑器").wait_for(state="hidden")
            page.get_by_label("搜索资源").fill("UI acceptance temporary case")
            page.get_by_text("UI acceptance temporary case", exact=True).wait_for()
            page.get_by_label("复制用例").click()
            page.get_by_role("button", name="保存配置").click()
            page.get_by_role("dialog", name="资源编辑器").wait_for(state="hidden")
            page.get_by_label("搜索资源").fill("UI acceptance temporary case")
            page.get_by_text("UI acceptance temporary case · 副本", exact=True).wait_for()
            page.on("dialog", lambda dialog: dialog.accept())
            for _ in range(2):
                page.get_by_label("删除", exact=True).first.click()
                page.wait_for_timeout(500)
            page.get_by_text("UI acceptance temporary case", exact=True).wait_for(state="hidden")
            page.get_by_role("button", name="工作台", exact=True).click()
            page.get_by_role("heading", name="最近执行").wait_for()
            page.screenshot(path=str(folder / "dashboard-light.png"), full_page=True)
            page.get_by_label("切换主题").click()
            assert page.locator("html").get_attribute("data-theme") == "dark"
            page.screenshot(path=str(folder / "dashboard-dark.png"), full_page=True)
            page.set_viewport_size({"width": 820, "height": 1000})
            page.screenshot(path=str(folder / "dashboard-tablet.png"), full_page=True)
            browser.close()
        assert not console_errors, console_errors
        summary = {
            "verified_at": datetime.now().astimezone().isoformat(),
            "duration_seconds": round(time.monotonic() - started, 2),
            "project_id": demo["project_id"],
            "cli_runs": [{"id": r["id"], "counts": r["summary"]["counts"]} for r in reports],
            "diagnostics": {"id": diagnostic["id"], "counts": diagnostic["summary"]["counts"]},
            "fourth_adapter": fourth["status"],
            "schedule_after_restart": scheduled["status"],
            "frontend": {
                "suite_run": "22 PASSED",
                "create_copy_delete": "PASSED",
                "themes": ["light", "dark"],
                "viewports": [1440, 820],
                "console_errors": console_errors,
            },
        }
        (folder / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
