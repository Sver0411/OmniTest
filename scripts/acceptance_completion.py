"""Verify data import, environment remapping and team access through real UI and workers."""

import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import datetime

import httpx
from acceptance import ROOT, wait_run, wait_server
from playwright.sync_api import expect, sync_playwright

from omnitest.services.backup import backup, restore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data/completion")
    parser.add_argument("--platform-port", type=int, default=8019)
    parser.add_argument("--fixture-port", type=int, default=8802)
    args = parser.parse_args()
    folder = (ROOT / args.data_dir).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = secrets.token_hex(4)
    data, worker_data = folder / ("platform-" + stamp), folder / ("worker-" + stamp)
    base = f"http://127.0.0.1:{args.platform_port}"
    for port in [args.platform_port, args.fixture_port, args.fixture_port + 1]:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
    env = {key: value for key, value in os.environ.items() if not key.startswith("OMNITEST_")}
    env["PYTHONUTF8"] = "1"
    admin_token, worker_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    config = folder / ("administrator-" + stamp + ".json")
    config.write_text(
        json.dumps(
            {
                "remote_nodes": [
                    {
                        "id": "completion-node",
                        "token_env": "OMNITEST_NODE_COMPLETION",
                        "project_ids": None,
                        "adapters": ["api"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    platform_env = {
        **env,
        "OMNITEST_CONFIG": str(config),
        "OMNITEST_API_TOKEN": admin_token,
        "OMNITEST_NODE_COMPLETION": worker_token,
    }
    processes, logs = [], []

    def launch(argv, name, variables=env):
        log = (folder / (name + "-" + stamp + ".log")).open("w", encoding="utf-8")
        logs.append(log)
        process = subprocess.Popen(
            [sys.executable, *argv], cwd=ROOT, env=variables, stdout=log, stderr=log
        )
        processes.append(process)
        return process

    def stop(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(5)

    def serve(directory, name):
        process = launch(
            [
                "-c",
                "from omnitest.cli import main;main()",
                "--data-dir",
                str(directory),
                "serve",
                "--port",
                str(args.platform_port),
            ],
            name,
            platform_env,
        )
        wait_server(base + "/api/ready", process)
        return process

    def resource(client, kind, body, project=None):
        response = client.post("/api/resources/" + kind, json={"project_id": project, "body": body})
        response.raise_for_status()
        return response.json()["id"]

    try:
        for index in range(2):
            fixture = launch(
                [
                    "-m",
                    "uvicorn",
                    "omnitest.demo_app:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.fixture_port + index),
                ],
                "fixture-" + str(index),
            )
            wait_server(f"http://127.0.0.1:{args.fixture_port + index}/health", fixture)
        platform = serve(data, "platform")
        launch(
            [
                "-c",
                "from omnitest.cli import main;main()",
                "--data-dir",
                str(worker_data),
                "worker",
                "--coordinator",
                base,
            ],
            "worker",
            {**env, "OMNITEST_NODE_TOKEN": worker_token},
        )
        with httpx.Client(
            base_url=base,
            headers={"Authorization": "Bearer " + admin_token},
            timeout=10,
            trust_env=False,
        ) as client:
            alpha = resource(client, "projects", {"name": "Completion Alpha " + stamp})
            beta = resource(client, "projects", {"name": "Completion Beta " + stamp})
            logical = resource(
                client,
                "targets",
                {
                    "name": "Logical endpoint",
                    "kind": "http",
                    "authorized": True,
                    "config": {"base_url": f"http://127.0.0.1:{args.fixture_port}"},
                },
                alpha,
            )
            actual = resource(
                client,
                "targets",
                {
                    "name": "Environment endpoint",
                    "kind": "http",
                    "authorized": True,
                    "config": {"base_url": f"http://127.0.0.1:{args.fixture_port + 1}"},
                },
                alpha,
            )
            case = resource(
                client,
                "cases",
                {
                    "name": "CSV matrix query",
                    "target": logical,
                    "adapter": "api",
                    "steps": [
                        {
                            "action": "request",
                            "config": {"path": "/items", "query": {"q": "${q}"}},
                            "assertions": [
                                {"path": "$.json.count", "op": "eq", "expected": "${expected}"}
                            ],
                        }
                    ],
                },
                alpha,
            )
            suite = resource(
                client, "suites", {"name": "Completion data regression", "case_ids": [case]}, alpha
            )
            target_b = resource(
                client,
                "targets",
                {
                    "name": "Beta",
                    "kind": "http",
                    "authorized": True,
                    "config": {"base_url": f"http://127.0.0.1:{args.fixture_port}"},
                },
                beta,
            )
            resource(
                client,
                "cases",
                {
                    "name": "Readonly health",
                    "target": target_b,
                    "adapter": "api",
                    "steps": [{"action": "request", "config": {"path": "/health"}}],
                },
                beta,
            )
            matrix = folder / "matrix.csv"
            matrix.write_bytes(b"q,expected\r\napple,1\r\nmissing,0\r\n")
            errors = []
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/#settings")

                def login(token, name):
                    page.get_by_role("button", name="系统设置", exact=True).click()
                    page.get_by_label("API 访问令牌").fill(token)
                    page.get_by_role("button", name="保存到当前会话", exact=True).click()
                    expect(page.get_by_label("当前访问身份")).to_contain_text(name)

                login(admin_token, "administrator")
                page.get_by_role("button", name="成员与团队", exact=True).click()
                page.get_by_role("button", name="新建成员", exact=True).click()
                editor = page.get_by_role("dialog", name="成员编辑器")
                username = "qa-" + stamp
                editor.get_by_label("成员用户名", exact=True).fill(username)
                editor.get_by_label("成员显示名称", exact=True).fill("Acceptance QA")
                editor.get_by_label(
                    "Completion Alpha " + stamp + " 权限", exact=True
                ).select_option("viewer")
                editor.get_by_label("Completion Beta " + stamp + " 权限", exact=True).select_option(
                    "viewer"
                )
                editor.get_by_role("button", name="保存", exact=True).click()
                token_dialog = page.get_by_role("dialog", name="成员访问令牌")
                user_token = token_dialog.get_by_label("新成员令牌").input_value()
                token_dialog.get_by_role("button", name="已保存，关闭", exact=True).click()
                page.get_by_role("button", name="新建团队", exact=True).click()
                editor = page.get_by_role("dialog", name="团队编辑器")
                editor.get_by_label("团队名称").fill("Acceptance team " + stamp)
                editor.get_by_role(
                    "checkbox", name="Acceptance QA (" + username + ")", exact=True
                ).check()
                editor.get_by_label(
                    "Completion Alpha " + stamp + " 权限", exact=True
                ).select_option("editor")
                editor.get_by_role("button", name="保存", exact=True).click()
                expect(editor).to_have_count(0)
                page.screenshot(path=str(folder / "members.png"), full_page=True)
                login(user_token, "user:" + username)
                page.get_by_label("筛选项目").select_option(alpha)
                page.get_by_role("button", name="项目管理", exact=True).click()
                expect(
                    page.get_by_role("row")
                    .filter(has_text="Completion Beta " + stamp)
                    .get_by_role("button", name="编辑", exact=True)
                ).to_be_disabled()
                page.get_by_role("button", name="数据集", exact=True).click()
                page.get_by_label("导入数据集文件").set_input_files(str(matrix))
                editor = page.get_by_role("dialog", name="导入数据集")
                editor.get_by_label("数据集名称").fill("Imported CSV")
                editor.get_by_label("expected 类型").select_option("integer")
                expect(editor.get_by_role("button", name="确认导入")).to_be_enabled()
                page.screenshot(path=str(folder / "dataset-preview.png"), full_page=True)
                editor.get_by_role("button", name="确认导入").click()
                expect(editor).to_have_count(0)
                page.get_by_role("button", name="测试用例", exact=True).click()
                try:
                    page.get_by_role("row").filter(has_text="CSV matrix query").get_by_role(
                        "button", name="数据集", exact=True
                    ).click(timeout=8000)
                except Exception:
                    page.screenshot(path=str(folder / "case-debug.png"), full_page=True)
                    print("Browser errors:", errors)
                    raise
                editor = page.get_by_role("dialog", name="用例数据集")
                editor.get_by_label("数据来源", exact=True).select_option("external")
                editor.get_by_role("checkbox").check()
                editor.get_by_role("button", name="保存关联", exact=True).click()
                expect(editor).to_have_count(0)
                page.get_by_role("button", name="测试环境", exact=True).click()
                page.get_by_role("button", name="新建环境", exact=True).first.click()
                editor = page.get_by_role("dialog", name="资源编辑器")
                editor.get_by_label("环境名称").fill("Mapped fixture")
                editor.get_by_label("环境变量 JSON").fill('{"q":"ignored-by-dataset"}')
                editor.get_by_role("button", name="添加替换").click()
                editor.get_by_label("逻辑目标 1").select_option(logical)
                editor.get_by_label("实际目标 1").select_option(actual)
                page.screenshot(path=str(folder / "environment.png"), full_page=True)
                editor.get_by_role("button", name="保存配置").click()
                expect(editor).to_have_count(0)
                page.get_by_role("button", name="测试执行", exact=True).click()
                environment = client.get(
                    "/api/resources/environments", params={"project_id": alpha}
                ).json()[0]["id"]
                page.get_by_label("执行环境").select_option(environment)
                page.get_by_label("执行节点", exact=True).select_option("completion-node")
                page.get_by_role("button", name="Completion data regression", exact=True).click()
                page.get_by_text("2 通过 / 2 个测试", exact=True).wait_for(timeout=30000)
                page.get_by_label("关闭报告").click()
                page.get_by_label("筛选项目").select_option(beta)
                page.get_by_role("button", name="测试用例", exact=True).click()
                expect(page.get_by_role("button", name="编辑", exact=True)).to_be_disabled()
                expect(page.get_by_role("button", name="新建用例", exact=True)).to_have_count(0)
                login(admin_token, "administrator")
                page.get_by_role("button", name="成员与团队", exact=True).click()
                page.get_by_role("button", name="轮换令牌", exact=True).click()
                token_dialog = page.get_by_role("dialog", name="成员访问令牌")
                rotated_token = token_dialog.get_by_label("新成员令牌").input_value()
                token_dialog.get_by_role("button", name="已保存，关闭", exact=True).click()
                browser.close()
            assert not errors, errors
            old_h = {"Authorization": "Bearer " + user_token}
            user_h = {"Authorization": "Bearer " + rotated_token}
            assert client.get("/api/session", headers=old_h).status_code == 401
            assert (
                client.get(f"/api/settings?project_id={beta}", headers=user_h).json()["principal"][
                    "permissions"
                ]["execute"]
                is False
            )
            runs = client.get("/api/runs", params={"project_id": alpha}).json()
            report = client.get("/api/runs/" + runs[0]["id"]).json()
            assert (
                report["summary"]["counts"] == {"PASSED": 2}
                and report["submitted_by"] == "user:" + username
            )
            assert report["node_id"] == "completion-node"
            for index, result in enumerate(report["results"]):
                selected = result["detail"]["selection"]
                assert selected["target"] == actual and selected["logical_target"] == logical
                assert selected["dataset"]["row"] == index + 1
                assert result["detail"]["target"]["config"]["base_url"].endswith(
                    str(args.fixture_port + 1)
                )
            team = client.get("/api/collaboration").json()["teams"][0]
            team_body = {key: value for key, value in team.items() if key != "id"}
            client.put(
                f"/api/collaboration/teams/{team['id']}", json={**team_body, "enabled": False}
            ).raise_for_status()
            assert (
                client.get(f"/api/settings?project_id={alpha}", headers=user_h).json()["principal"][
                    "permissions"
                ]["manage"]
                is False
            )
            client.put(f"/api/collaboration/teams/{team['id']}", json=team_body).raise_for_status()
            cli_env = {**env, "OMNITEST_API_TOKEN": rotated_token}
            cli = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from omnitest.cli import main;main()",
                    "--url",
                    base,
                    "import-dataset",
                    "--project",
                    alpha,
                    "--file",
                    str(matrix),
                    "--name",
                    "CLI matrix",
                    "--column-types",
                    '{"expected":"integer"}',
                ],
                cwd=ROOT,
                env=cli_env,
                capture_output=True,
                encoding="utf-8",
                timeout=30,
            )
            assert cli.returncode == 0, cli.stdout + cli.stderr
            imported = json.loads(cli.stdout)
            assert (
                imported["body"]["source"]["sha256"]
                == hashlib.sha256(matrix.read_bytes()).hexdigest()
            )
            assert client.get(f"/api/datasets/{imported['id']}/export", headers=user_h).json() == [
                {"q": "apple", "expected": 1},
                {"q": "missing", "expected": 0},
            ]
            before = report["id"]
            stop(platform)
            from omnitest.services.lock import WorkerLock

            deadline = time.monotonic() + 10
            while True:
                lock = WorkerLock(data)
                try:
                    lock.acquire()
                    lock.release()
                    break
                except RuntimeError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)
            archive = folder / ("backup-" + stamp + ".zip")
            backup(data, archive)
            restored = folder / ("restored-" + stamp)
            restore(archive, restored)
            platform = serve(restored, "restored-platform")
            assert client.get("/api/runs/" + before, headers=user_h).json()["status"] == "PASSED"
            response = client.post(
                "/api/runs",
                headers=user_h,
                json={
                    "project_id": alpha,
                    "suite_id": suite,
                    "environment_id": environment,
                    "node_id": "completion-node",
                },
            )
            response.raise_for_status()
            restored_report = wait_run(client, response.json()["id"])
            assert restored_report["summary"]["counts"] == {"PASSED": 2}, restored_report
            audit = client.get("/api/audit?limit=200").json()
            assert any(
                row["actor"] == "user:" + username and row["role"] == "editor" for row in audit
            )
            assert user_token not in json.dumps(audit) and rotated_token not in json.dumps(audit)
        summary = {
            "verified_at": datetime.now().astimezone().isoformat(),
            "project_id": alpha,
            "ui_member_and_team": "PASSED",
            "csv_preview_types_and_binding": "PASSED",
            "environment_target_override": "PASSED",
            "remote_run": "2 PASSED",
            "project_specific_readonly_ui": "PASSED",
            "team_revocation_and_token_rotation": "PASSED",
            "cli_import_and_source_hash": "PASSED",
            "restored_members_data_environment_history": "2 PASSED",
            "console_errors": errors,
        }
        (folder / "summary.json").write_text(
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
