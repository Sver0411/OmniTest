"""Exercise project roles, audit, browser controls and an offline restore drill."""

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import datetime

import httpx
from acceptance import ROOT, wait_server
from playwright.sync_api import expect, sync_playwright

from omnitest.adapters.base import Registry
from omnitest.core.ir import TestIR
from omnitest.db import Database
from omnitest.services.resources import Resources


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data/phase5")
    parser.add_argument("--platform-port", type=int, default=8015)
    parser.add_argument("--fixture-port", type=int, default=8798)
    args = parser.parse_args()
    directory = (ROOT / args.data_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    for port in [args.platform_port, args.fixture_port]:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
    fixture_url = f"http://127.0.0.1:{args.fixture_port}"
    platform_url = f"http://127.0.0.1:{args.platform_port}"
    stamp = secrets.token_hex(4)
    db = Database(f"sqlite:///{(directory / 'omnitest.db').as_posix()}")
    db.migrate()
    resources = Resources(db, Registry())
    project = resources.save("projects", {"name": "Phase 5 · Team Alpha " + stamp})["id"]
    other = resources.save("projects", {"name": "Phase 5 · Team Beta " + stamp})["id"]
    target = resources.save(
        "targets",
        {
            "name": "HTTP and browser fixture",
            "kind": "http",
            "authorized": True,
            "config": {"base_url": fixture_url},
        },
        project,
    )["id"]
    cases = []
    for name, adapter, steps in [
        (
            "Health",
            "api",
            [
                {
                    "action": "request",
                    "config": {"path": "/health"},
                    "assertions": [{"path": "$.status_code", "op": "eq", "expected": 200}],
                }
            ],
        ),
        (
            "Browser evidence",
            "web",
            [{"action": "goto", "config": {"path": "/"}}, {"id": "screen", "action": "screenshot"}],
        ),
    ]:
        cases.append(
            resources.save(
                "cases",
                TestIR(name=name, adapter=adapter, target=target, steps=steps).model_dump(),
                project,
            )["id"]
        )
    suite = resources.save(
        "suites", {"name": "Phase 5 · shared regression", "case_ids": cases}, project
    )["id"]
    db.engine.dispose()
    roles = ["admin", "editor", "operator", "viewer", "partner"]
    tokens = {role: secrets.token_urlsafe(32) for role in roles}
    config = directory / "administrator.json"
    config.write_text(
        json.dumps(
            {
                "access_tokens": [
                    {
                        "name": role,
                        "role": "viewer" if role == "partner" else role,
                        "token_env": "OMNITEST_ACCESS_" + role.upper(),
                        "project_ids": None
                        if role == "admin"
                        else [other if role == "partner" else project],
                    }
                    for role in roles
                ]
            }
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "OMNITEST_CONFIG": str(config),
        **{"OMNITEST_ACCESS_" + role.upper(): token for role, token in tokens.items()},
    }
    # The drill uses generated named credentials rather than a legacy administrator token.
    env.pop("OMNITEST_API_TOKEN", None)
    processes, logs = [], []

    def launch(argv, name):
        log = (directory / f"{name}.log").open("w", encoding="utf-8")
        logs.append(log)
        process = subprocess.Popen(
            [sys.executable, *argv], cwd=ROOT, env=env, stdout=log, stderr=log
        )
        processes.append(process)
        return process

    def stop(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def serve(data, name):
        process = launch(
            [
                "-c",
                "from omnitest.cli import main;main()",
                "--data-dir",
                str(data),
                "serve",
                "--port",
                str(args.platform_port),
            ],
            name,
        )
        wait_server(platform_url + "/api/ready", process)
        return process

    def headers(role):
        return {"Authorization": "Bearer " + tokens[role]}

    def cli(data, *argv):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from omnitest.cli import main;main()",
                "--data-dir",
                str(data),
                *argv,
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            encoding="utf-8",
            timeout=60,
        )
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
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
        platform = serve(directory, "platform")
        with httpx.Client(base_url=platform_url, trust_env=False) as client:
            assert client.get("/api/resources/projects").status_code == 401
            assert [
                row["id"]
                for row in client.get("/api/resources/projects", headers=headers("viewer")).json()
            ] == [project]
            assert (
                client.get(
                    f"/api/resources/projects/{other}", headers=headers("viewer")
                ).status_code
                == 403
            )
            request = {"project_id": project, "suite_id": suite}
            assert (
                client.post("/api/runs", headers=headers("viewer"), json=request).status_code == 403
            )
            run = client.post("/api/runs", headers=headers("operator"), json=request)
            run.raise_for_status()
            identity = run.json()["id"]
            deadline = time.monotonic() + 30
            while True:
                report = client.get(f"/api/runs/{identity}", headers=headers("viewer")).json()
                if report["status"] not in {"PENDING", "RUNNING"}:
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError("Run exceeded acceptance timeout")
                time.sleep(0.1)
            assert report["summary"]["counts"] == {"PASSED": 2}, report
            assert report["submitted_by"] == "operator"
            screen = next(row for row in report["results"] if row["adapter"] == "web")
            evidence = f"/api/evidence/{identity}/{screen['id']}/1/screen.png"
            assert client.get(evidence, headers=headers("viewer")).content.startswith(b"\x89PNG")
            assert client.get(evidence, headers=headers("partner")).status_code == 403
            assert (
                client.get(f"/api/runs/{identity}/export", headers=headers("partner")).status_code
                == 403
            )
            audit = client.get("/api/audit", headers=headers("admin")).json()
            assert any(
                event["actor"] == "operator" and event["status_code"] == 202 for event in audit
            )
        errors = []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(platform_url + "/#settings")

            def login(role):
                page.get_by_role("button", name="系统设置", exact=True).click()
                page.get_by_label("API 访问令牌").fill(tokens[role])
                page.get_by_role("button", name="保存到当前会话", exact=True).click()
                expect(page.get_by_label("当前访问身份")).to_contain_text(role)

            login("viewer")
            page.get_by_label("筛选项目").select_option(project)
            page.get_by_role("button", name="测试用例", exact=True).click()
            expect(page.get_by_role("button", name="编辑", exact=True).first).to_be_disabled()
            expect(page.get_by_role("button", name="新建用例", exact=True)).to_have_count(0)
            expect(page.get_by_role("button", name="运行维护", exact=True)).to_have_count(0)
            page.screenshot(path=str(directory / "viewer.png"), full_page=True)
            login("operator")
            page.get_by_label("筛选项目").select_option(project)
            page.get_by_role("button", name="测试执行", exact=True).click()
            page.get_by_role("button", name="Phase 5 · shared regression", exact=True).click()
            page.get_by_text("2 通过 / 2 个测试", exact=True).wait_for(timeout=30000)
            page.get_by_label("关闭报告").click()
            login("admin")
            page.get_by_role("button", name="运行维护", exact=True).click()
            page.get_by_role("heading", name="最近操作", exact=True).wait_for()
            expect(page.get_by_text("operator · operator", exact=True).first).to_be_visible()
            page.screenshot(path=str(directory / "operations.png"), full_page=True)
            browser.close()
        assert not errors, errors
        stop(platform)
        archive = directory / f"backup-{stamp}.zip"
        backup_result = cli(directory, "backup", "--output", str(archive))
        restored = directory / f"restored-{stamp}"
        cli(restored, "restore", "--archive", str(archive))
        serve(restored, "restored-platform")
        with httpx.Client(base_url=platform_url, trust_env=False) as client:
            assert (
                client.get(f"/api/runs/{identity}", headers=headers("viewer")).json()["status"]
                == "PASSED"
            )
            assert client.get(evidence, headers=headers("viewer")).content.startswith(b"\x89PNG")
            assert (
                client.get(f"/api/runs/{identity}", headers=headers("partner")).status_code == 403
            )
            assert client.get("/api/audit", headers=headers("admin")).json()
        summary = {
            "verified_at": datetime.now().astimezone().isoformat(),
            "project_id": project,
            "roles": "admin/editor/operator/viewer with project scopes",
            "api_run": "2 PASSED",
            "web_run": "2 PASSED",
            "report_and_evidence_isolation": "PASSED",
            "audit": "PASSED",
            "restore_with_history_and_png": "PASSED",
            "backup_sha256": backup_result["sha256"],
            "console_errors": errors,
        }
        (directory / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        for process in reversed(processes):
            stop(process)
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
