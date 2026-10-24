"""Verify the Phase 2 adapter increment through real CLI and Web execution."""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime

import httpx
from acceptance import ROOT, wait_server
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data/phase2")
    parser.add_argument("--fixture-port", type=int, default=8775)
    parser.add_argument("--tcp-port", type=int, default=8776)
    parser.add_argument("--platform-port", type=int, default=8010)
    args = parser.parse_args()
    directory = (ROOT / args.data_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    for port in [args.fixture_port, args.tcp_port, args.platform_port]:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
    env = {**os.environ, "PYTHONUTF8": "1"}
    processes, logs = [], []
    fixture_url = f"http://127.0.0.1:{args.fixture_port}"
    platform_url = f"http://127.0.0.1:{args.platform_port}"

    def launch(argv, name):
        log = (directory / f"{name}.log").open("w", encoding="utf-8")
        logs.append(log)
        process = subprocess.Popen(
            [sys.executable, *argv], cwd=ROOT, env=env, stdout=log, stderr=log
        )
        processes.append(process)
        return process

    def cli(*argv):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from omnitest.cli import main; main()",
                "--data-dir",
                str(directory),
                *argv,
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            encoding="utf-8",
            timeout=60,
        )
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
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
            "http-fixture",
        )
        wait_server(fixture_url + "/health", fixture)
        tcp = launch(["examples/tcp_fixture.py", "--port", str(args.tcp_port)], "tcp-fixture")
        deadline = time.monotonic() + 10
        while True:
            try:
                with socket.create_connection(("127.0.0.1", args.tcp_port), timeout=1):
                    break
            except OSError:
                if time.monotonic() > deadline or tcp.poll() is not None:
                    raise RuntimeError("TCP fixture failed to start") from None
                time.sleep(0.1)
        demo = cli("seed-phase2", "--base-url", fixture_url, "--tcp-port", str(args.tcp_port))
        reports = []
        for index in range(2):
            report = cli("run", "--project", demo["project_id"], "--suite", demo["suite_id"])
            assert report["summary"]["counts"] == {"PASSED": 9}, report
            reports.append(report)
            (directory / f"cli-run-{index + 1}.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        platform = launch(
            [
                "-c",
                "from omnitest.cli import main; main()",
                "--data-dir",
                str(directory),
                "serve",
                "--port",
                str(args.platform_port),
            ],
            "platform",
        )
        wait_server(platform_url + "/api/health", platform)
        with httpx.Client(base_url=platform_url, trust_env=False) as client:
            adapters = client.get("/api/adapters").json()["items"]
            assert {"database", "tcp", "http-performance"}.issubset({a["id"] for a in adapters})
            assert client.get("/api/runs/" + reports[0]["id"]).json()["status"] == "PASSED"
        errors = []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(platform_url)
            page.get_by_label("筛选项目").select_option(demo["project_id"])
            page.get_by_role("button", name="测试执行", exact=True).click()
            page.get_by_role("button", name="Phase 2 · adapter acceptance").click()
            page.get_by_text("9 通过 / 9 个测试", exact=True).wait_for(timeout=30000)
            page.screenshot(path=str(directory / "phase2-report.png"), full_page=True)
            page.get_by_label("关闭报告").click()
            page.get_by_role("button", name="执行适配器", exact=True).click()
            page.get_by_role("heading", name="DATABASE Adapter", exact=True).wait_for()
            page.get_by_role("heading", name="TCP Adapter", exact=True).wait_for()
            page.get_by_role("heading", name="HTTP-PERFORMANCE Adapter", exact=True).wait_for()
            page.screenshot(path=str(directory / "phase2-adapters.png"), full_page=True)
            browser.close()
        assert not errors, errors
        summary = {
            "verified_at": datetime.now().astimezone().isoformat(),
            "project_id": demo["project_id"],
            "suite_id": demo["suite_id"],
            "cli_runs": [{"id": r["id"], "counts": r["summary"]["counts"]} for r in reports],
            "web_run": "9 PASSED",
            "adapters": ["database", "tcp", "http-performance"],
            "console_errors": errors,
        }
        (directory / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
