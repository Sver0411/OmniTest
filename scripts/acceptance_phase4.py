"""Verify protobuf, staged load and fault recovery via CLI, API and Web."""

import argparse
import json
import os
import socket
import subprocess
import sys
from datetime import datetime

import httpx
from acceptance import ROOT, wait_server
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data/phase4")
    parser.add_argument("--fixture-port", type=int, default=8785)
    parser.add_argument("--grpc-port", type=int, default=8786)
    parser.add_argument("--platform-port", type=int, default=8011)
    args = parser.parse_args()
    directory = (ROOT / args.data_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    for port in [args.fixture_port, args.grpc_port, args.platform_port]:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
    fixture_url = f"http://127.0.0.1:{args.fixture_port}"
    platform_url = f"http://127.0.0.1:{args.platform_port}"
    config = directory / "administrator.json"
    config.write_text(
        json.dumps(
            {
                "http_faults": {
                    "local-recovery": {
                        "url": fixture_url + "/faults/recovery",
                        "body": {"failures": 2},
                        "restore_url": fixture_url + "/faults/recovery",
                        "restore_body": {"failures": 0},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONUTF8": "1", "OMNITEST_CONFIG": str(config)}
    processes, logs = [], []

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
            timeout=90,
        )
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return json.loads(result.stdout)

    try:
        fixture = launch(
            [
                "-m",
                "omnitest.phase4_fixture",
                "--http-port",
                str(args.fixture_port),
                "--grpc-port",
                str(args.grpc_port),
                "--descriptor-file",
                str(directory / "descriptors/echo.pb"),
            ],
            "fixture",
        )
        wait_server(fixture_url + "/health", fixture)
        demo = cli("seed-phase4", "--base-url", fixture_url, "--grpc-port", str(args.grpc_port))
        reports = []
        for index in range(2):
            report = cli("run", "--project", demo["project_id"], "--suite", demo["suite_id"])
            assert report["summary"]["counts"] == {"PASSED": 5}, report
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
            assert client.get("/api/runs/" + reports[0]["id"]).json()["status"] == "PASSED"
            assert client.get("/api/health").json()["ai_enabled"] is False
            xml = client.get(f"/api/runs/{reports[0]['id']}/export?format=junit")
            xml.raise_for_status()
            (directory / "acceptance.xml").write_text(xml.text, encoding="utf-8")
        errors = []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(platform_url)
            page.get_by_label("筛选项目").select_option(demo["project_id"])
            page.get_by_role("button", name="测试执行", exact=True).click()
            page.get_by_role("button", name="Phase 4 · protocol and resilience").click()
            page.get_by_text("5 通过 / 5 个测试", exact=True).wait_for(timeout=30000)
            page.screenshot(path=str(directory / "phase4-report.png"), full_page=True)
            page.get_by_label("关闭报告").click()
            page.get_by_role("button", name="执行适配器", exact=True).click()
            for name in ["GRPC", "APPIUM", "HTTP-RELIABILITY"]:
                page.get_by_role("heading", name=name + " Adapter", exact=True).wait_for()
            page.screenshot(path=str(directory / "phase4-adapters.png"), full_page=True)
            browser.close()
        assert not errors, errors
        summary = {
            "verified_at": datetime.now().astimezone().isoformat(),
            "project_id": demo["project_id"],
            "suite_id": demo["suite_id"],
            "cli_runs": [{"id": r["id"], "counts": r["summary"]["counts"]} for r in reports],
            "web_run": "5 PASSED",
            "console_errors": errors,
            "device_verification": "Run acceptance_devices.py separately on a configured device host",
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
