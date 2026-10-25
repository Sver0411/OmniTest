"""Run real MQTT suites, create a calendar in the UI and execute it on a worker."""

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
from acceptance import ROOT, wait_run, wait_server
from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data/protocols")
    parser.add_argument("--platform-port", type=int, default=8018)
    parser.add_argument("--broker-port", type=int, default=18884)
    args = parser.parse_args()
    folder = (ROOT / args.data_dir).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = secrets.token_hex(4)
    data = folder / ("coordinator-" + stamp)
    worker_dir = folder / ("worker-" + stamp)
    base = f"http://127.0.0.1:{args.platform_port}"
    for port in [args.platform_port, args.broker_port]:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
    env = {key: value for key, value in os.environ.items() if not key.startswith("OMNITEST_")}
    env["PYTHONUTF8"] = "1"
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
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def cli(*argv):
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
            timeout=45,
        )
        if result.returncode:
            raise RuntimeError(result.stdout[-5000:] + result.stderr[-5000:])
        return json.loads(result.stdout)

    try:
        broker = launch(["-m", "omnitest.mqtt_fixture", "--port", str(args.broker_port)], "broker")
        deadline = time.monotonic() + 15
        while True:
            try:
                with socket.create_connection(("127.0.0.1", args.broker_port), timeout=0.5):
                    break
            except OSError:
                if broker.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("MQTT broker startup failed") from None
                time.sleep(0.1)
        demo = cli("seed-mqtt", "--port", str(args.broker_port))
        project, suite = demo["project_id"], demo["suite_id"]
        cli_reports = []
        for index in range(2):
            report = cli("run", "--project", project, "--suite", suite)
            assert report["summary"]["counts"] == {"PASSED": 4}, report
            cli_reports.append(report["id"])
            (folder / f"cli-{index + 1}.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        admin_token, node_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        config = folder / ("administrator-" + stamp + ".json")
        config.write_text(
            json.dumps(
                {
                    "remote_nodes": [
                        {
                            "id": "mqtt-worker",
                            "token_env": "OMNITEST_NODE_MQTT",
                            "project_ids": [project],
                            "adapters": ["mqtt"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        coordinator_env = {
            **env,
            "OMNITEST_CONFIG": str(config),
            "OMNITEST_API_TOKEN": admin_token,
            "OMNITEST_NODE_MQTT": node_token,
        }

        def serve(name):
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
                coordinator_env,
            )
            wait_server(base + "/api/ready", process)
            return process

        platform = serve("platform")
        launch(
            [
                "-c",
                "from omnitest.cli import main;main()",
                "--data-dir",
                str(worker_dir),
                "worker",
                "--coordinator",
                base,
            ],
            "worker",
            {**env, "OMNITEST_NODE_TOKEN": node_token},
        )
        with httpx.Client(
            base_url=base,
            headers={"Authorization": "Bearer " + admin_token},
            timeout=10,
            trust_env=False,
        ) as client:
            errors = []
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/#settings")
                page.get_by_label("API 访问令牌").fill(admin_token)
                page.get_by_role("button", name="保存到当前会话", exact=True).click()
                expect(page.get_by_label("当前访问身份")).to_contain_text("administrator")
                page.get_by_label("筛选项目").select_option(project)
                page.get_by_role("button", name="定时任务", exact=True).click()
                page.get_by_role("button", name="新建定时任务", exact=True).first.click()
                editor = page.get_by_role("dialog", name="资源编辑器")
                editor.get_by_label("名称", exact=True).fill("MQTT calendar " + stamp)
                editor.get_by_label("测试套件", exact=True).select_option(suite)
                editor.get_by_label("调度方式", exact=True).select_option("cron")
                editor.get_by_label("Cron 表达式", exact=True).fill("* * * * *")
                editor.get_by_label("时区", exact=True).fill("Asia/Shanghai")
                editor.get_by_label("执行节点", exact=True).select_option("mqtt-worker")
                editor.get_by_label("上次任务未结束", exact=True).select_option("skip")
                editor.get_by_role("listitem").filter(has_text="+08:00").first.wait_for()
                expect(editor.get_by_role("listitem")).to_have_count(5)
                page.screenshot(path=str(folder / "schedule-form.png"), full_page=True)
                editor.get_by_role("button", name="保存配置", exact=True).click()
                expect(editor).to_have_count(0)
                expect(page.get_by_text("MQTT calendar " + stamp, exact=True)).to_be_visible()
                page.screenshot(path=str(folder / "schedules.png"), full_page=True)
                browser.close()
            assert not errors, errors
            schedules = client.get(
                "/api/resources/schedules", params={"project_id": project}
            ).json()
            calendar = next(
                item for item in schedules if item["body"]["name"] == "MQTT calendar " + stamp
            )
            assert (
                calendar["body"]["schedule_type"] == "cron"
                and calendar["body"]["node_id"] == "mqtt-worker"
            )
            due = calendar["body"]["next_run"]
            assert datetime.fromtimestamp(due).second == 0
            deadline = time.monotonic() + 80
            while True:
                cfg = client.get(f"/api/resources/schedules/{calendar['id']}").json()["body"]
                if cfg["last_run_id"]:
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError("Calendar did not trigger at its actual minute boundary")
                time.sleep(0.2)
            scheduled = wait_run(client, cfg["last_run_id"])
            assert (
                scheduled["summary"]["counts"] == {"PASSED": 4}
                and scheduled["node_id"] == "mqtt-worker"
            ), scheduled
            assert scheduled["source"] == "schedule:" + calendar["id"]
            assert scheduled["created_at"] >= due and cfg["next_run"] > due
            # Verify persisted configuration and a real post-restart trigger without a minute-long second wait.
            interval = client.post(
                "/api/resources/schedules",
                json={
                    "project_id": project,
                    "body": {
                        "name": "Restart interval " + stamp,
                        "suite_id": suite,
                        "interval_seconds": 5,
                        "node_id": "mqtt-worker",
                    },
                },
            )
            interval.raise_for_status()
            interval_id = interval.json()["id"]
            stop(platform)
            platform = serve("restarted-platform")
            restored = client.get(f"/api/resources/schedules/{calendar['id']}").json()["body"]
            assert restored["cron"] == "* * * * *" and restored["timezone"] == "Asia/Shanghai"
            deadline = time.monotonic() + 20
            while True:
                cfg = client.get(f"/api/resources/schedules/{interval_id}").json()["body"]
                if cfg["last_run_id"]:
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError("Persisted interval did not resume")
                time.sleep(0.1)
            resumed = wait_run(client, cfg["last_run_id"])
            assert resumed["summary"]["counts"] == {"PASSED": 4}, resumed
            client.put(
                f"/api/resources/schedules/{interval_id}",
                json={"project_id": project, "body": {**cfg, "enabled": False}},
            ).raise_for_status()
            assert client.get("/api/runs/" + scheduled["id"]).json()["status"] == "PASSED"
            assert client.get(f"/api/runs/{scheduled['id']}/export?format=junit").status_code == 200
        summary = {
            "verified_at": datetime.now().astimezone().isoformat(),
            "project_id": project,
            "coordinator_data": str(data),
            "worker_data": str(worker_dir),
            "cli_runs": cli_reports,
            "cli_results": "4 PASSED twice",
            "calendar_created_in_ui": "PASSED",
            "calendar_due_at": due,
            "calendar_created_run_at": scheduled["created_at"],
            "calendar_remote_run": "4 PASSED",
            "restart_interval_remote_run": "4 PASSED",
            "cron_timezone_persisted": "Asia/Shanghai",
            "junit_export": "PASSED",
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
