"""Verify two outbound worker processes, UI selection, evidence and interruption handling."""

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

from omnitest.adapters.base import Registry
from omnitest.db import Database
from omnitest.services.resources import Resources


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data/nodes")
    parser.add_argument("--platform-port", type=int, default=8016)
    parser.add_argument("--fixture-port", type=int, default=8799)
    args = parser.parse_args()
    directory = (ROOT / args.data_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    for port in [args.platform_port, args.fixture_port]:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
    base = f"http://127.0.0.1:{args.platform_port}"
    fixture = f"http://127.0.0.1:{args.fixture_port}"
    stamp = secrets.token_hex(4)
    data = directory / ("coordinator-" + stamp)
    data.mkdir()
    db = Database(f"sqlite:///{(data / 'omnitest.db').as_posix()}")
    db.migrate()
    resources = Resources(db, Registry())
    project = resources.save("projects", {"name": "Remote execution " + stamp})["id"]
    other = resources.save("projects", {"name": "Unassigned project " + stamp})["id"]
    target = resources.save(
        "targets",
        {
            "name": "HTTP fixture",
            "kind": "http",
            "authorized": True,
            "config": {"base_url": fixture, "allow_mutations": True},
        },
        project,
    )["id"]

    def case(name, adapter, target_id, steps):
        return resources.save(
            "cases",
            {"name": name, "adapter": adapter, "target": target_id, "timeout": 45, "steps": steps},
            project,
        )["id"]

    health = case(
        "Remote HTTP",
        "api",
        target,
        [
            {
                "action": "request",
                "config": {"path": "/health"},
                "assertions": [{"path": "$.status_code", "op": "eq", "expected": 200}],
            }
        ],
    )
    screen = case(
        "Remote screenshot",
        "web",
        target,
        [{"action": "goto", "config": {"path": "/"}}, {"id": "screen", "action": "screenshot"}],
    )
    secret_ref = "${secret.OMNITEST_SECRET_WORKER_TEST}"
    secret_case = case(
        "Node-local credential",
        "api",
        target,
        [
            {
                "action": "request",
                "config": {
                    "path": "/echo",
                    "method": "POST",
                    "headers": {"x-test": secret_ref},
                    "json": {},
                },
                "assertions": [{"path": "$.json.header", "op": "eq", "expected": secret_ref}],
            }
        ],
    )
    suite = resources.save(
        "suites",
        {"name": "Remote regression", "case_ids": [health, screen], "concurrency": 2},
        project,
    )["id"]
    local_target = resources.save(
        "targets",
        {
            "name": "Node-local file",
            "kind": "file",
            "authorized": True,
            "config": {"path": "proof.txt"},
        },
        project,
    )["id"]
    proof = "beta-only-" + stamp
    file_case = case(
        "Node-local proof",
        "filecheck",
        local_target,
        [{"action": "inspect", "assertions": [{"path": "$.text", "op": "eq", "expected": proof}]}],
    )
    cli_target = resources.save(
        "targets",
        {
            "name": "Bounded wait",
            "kind": "cli",
            "authorized": True,
            "config": {"program": "demo-tool"},
        },
        project,
    )["id"]
    slow = case(
        "Cancellation and heartbeat",
        "cli",
        cli_target,
        [
            {"action": "run", "config": {"args": ["sleep", "10"]}},
            {"action": "run", "config": {"args": ["sleep", "10"]}},
        ],
    )
    db.engine.dispose()
    tokens = {name: secrets.token_urlsafe(32) for name in ["admin", "operator", "alpha", "beta"]}
    test_secret = secrets.token_urlsafe(24)
    clean_env = {key: value for key, value in os.environ.items() if not key.startswith("OMNITEST_")}
    clean_env["PYTHONUTF8"] = "1"
    profiles = [
        {
            "id": name,
            "token_env": "OMNITEST_NODE_" + name.upper(),
            "project_ids": [project],
            "adapters": adapters,
        }
        for name, adapters in [("alpha", ["api", "web", "cli"]), ("beta", ["filecheck"])]
    ]
    coordinator_config = directory / ("coordinator-" + stamp + ".json")
    coordinator_config.write_text(
        json.dumps(
            {
                "access_tokens": [
                    {
                        "name": "operator",
                        "role": "operator",
                        "token_env": "OMNITEST_ACCESS_OPERATOR",
                        "project_ids": [project],
                    }
                ],
                "remote_nodes": profiles,
                "project_secrets": {project: ["OMNITEST_SECRET_WORKER_TEST"]},
            }
        ),
        encoding="utf-8",
    )
    coordinator_env = {
        **clean_env,
        "OMNITEST_CONFIG": str(coordinator_config),
        "OMNITEST_API_TOKEN": tokens["admin"],
        "OMNITEST_ACCESS_OPERATOR": tokens["operator"],
        **{"OMNITEST_NODE_" + name.upper(): tokens[name] for name in ["alpha", "beta"]},
    }
    worker_envs, worker_dirs = {}, {}
    for name in ["alpha", "beta"]:
        worker_dir = directory / (name + "-" + stamp)
        worker_dir.mkdir()
        fixtures = worker_dir / "fixtures"
        fixtures.mkdir()
        (fixtures / "proof.txt").write_text(proof if name == "beta" else "alpha", encoding="utf-8")
        config = worker_dir / "worker.json"
        config.write_text(
            json.dumps(
                {
                    "file_root": str(fixtures),
                    "project_secrets": {project: ["OMNITEST_SECRET_WORKER_TEST"]},
                }
            ),
            encoding="utf-8",
        )
        worker_envs[name] = {
            **clean_env,
            "OMNITEST_CONFIG": str(config),
            "OMNITEST_NODE_TOKEN": tokens[name],
            "OMNITEST_SECRET_WORKER_TEST": test_secret,
        }
        worker_dirs[name] = worker_dir
    processes, logs = [], []

    def launch(argv, name, env):
        log = (directory / (name + "-" + stamp + ".log")).open("w", encoding="utf-8")
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

    def start_platform(name):
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

    def start_worker(name, suffix=""):
        return launch(
            [
                "-c",
                "from omnitest.cli import main;main()",
                "--data-dir",
                str(worker_dirs[name]),
                "worker",
                "--coordinator",
                base,
            ],
            name + suffix,
            worker_envs[name],
        )

    def wait_status(client, identity, status):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            report = client.get("/api/runs/" + identity).json()
            if report["status"] == status:
                return report
            time.sleep(0.1)
        raise RuntimeError(f"Expected {status}: {report}")

    def submit(client, node, **selection):
        response = client.post(
            "/api/runs", json={"project_id": project, "node_id": node, **selection}
        )
        response.raise_for_status()
        return response.json()["id"]

    try:
        fixture_process = launch(
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
            clean_env,
        )
        wait_server(fixture + "/health", fixture_process)
        platform = start_platform("platform")
        with httpx.Client(
            base_url=base,
            headers={"Authorization": "Bearer " + tokens["admin"]},
            trust_env=False,
            timeout=10,
        ) as client:
            first = submit(client, "alpha", suite_id=suite)
            second = submit(client, "beta", case_ids=[file_case])
            assert client.get("/api/runs/" + first).json()["status"] == "PENDING"
            alpha = start_worker("alpha")
            start_worker("beta")
            reports = [wait_run(client, identity) for identity in [first, second]]
            assert [r["summary"]["counts"] for r in reports] == [{"PASSED": 2}, {"PASSED": 1}], (
                reports
            )
            web = next(r for r in reports[0]["results"] if r["adapter"] == "web")
            relative = web["detail"]["attempts"][0]["evidence"][0]
            evidence = f"/api/evidence/{first}/{web['id']}/{relative}"
            image = client.get(evidence).content
            assert image.startswith(b"\x89PNG")
            assert (
                worker_dirs["alpha"] / "evidence" / first / web["id"] / relative
            ).read_bytes() == image
            assert not (worker_dirs["beta"] / "evidence" / first).exists()
            operator = {"Authorization": "Bearer " + tokens["operator"]}
            assert {
                n["id"]
                for n in client.get(f"/api/nodes?project_id={project}", headers=operator).json()
            } == {"alpha", "beta"}
            assert client.get(f"/api/nodes?project_id={other}", headers=operator).status_code == 403
            assert client.get("/api/nodes", headers=operator).json() == []
            assert client.get("/api/operations", headers=operator).status_code == 403
            secret_run = wait_run(client, submit(client, "alpha", case_ids=[secret_case]))
            assert secret_run["status"] == "PASSED", secret_run
            assert test_secret not in json.dumps(secret_run)
            # A node-local secret alone grants nothing when the coordinator omits its name.
            body = client.get(f"/api/resources/cases/{secret_case}").json()["body"]
            denied_ref = "${secret.OMNITEST_SECRET_UNASSIGNED}"
            body["steps"][0]["config"]["headers"]["x-test"] = denied_ref
            client.put(
                f"/api/resources/cases/{secret_case}", json={"project_id": project, "body": body}
            ).raise_for_status()
            denied = wait_run(client, submit(client, "alpha", case_ids=[secret_case]))
            assert denied["status"] == "ERROR" and "not authorized" in json.dumps(denied), denied
            cli_env = {**clean_env, "OMNITEST_API_TOKEN": tokens["operator"]}
            cli = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from omnitest.cli import main;main()",
                    "--url",
                    base,
                    "run",
                    "--project",
                    project,
                    "--case",
                    health,
                    "--node",
                    "alpha",
                ],
                cwd=ROOT,
                env=cli_env,
                capture_output=True,
                encoding="utf-8",
                timeout=45,
            )
            assert cli.returncode == 0, cli.stdout + cli.stderr
            assert json.loads(cli.stdout)["node_id"] == "alpha"
            cancelled = submit(client, "alpha", case_ids=[slow])
            wait_status(client, cancelled, "RUNNING")
            client.post(f"/api/runs/{cancelled}/cancel").raise_for_status()
            assert wait_run(client, cancelled)["status"] == "CANCELLED"
            lost = submit(client, "alpha", case_ids=[slow])
            wait_status(client, lost, "RUNNING")
            stop(alpha)
            lost_report = wait_run(client, lost)
            assert (
                lost_report["status"] == "ERROR"
                and "not replayed" in lost_report["summary"]["error"]
            ), lost_report
            alpha = start_worker("alpha", "-reconnect")
            after_loss = wait_run(client, submit(client, "alpha", case_ids=[health]))
            assert after_loss["status"] == "PASSED"
            errors = []
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/#settings")
                page.get_by_label("API 访问令牌").fill(tokens["admin"])
                page.get_by_role("button", name="保存到当前会话", exact=True).click()
                expect(page.get_by_label("当前访问身份")).to_contain_text("administrator")
                page.get_by_label("筛选项目").select_option(project)
                page.get_by_role("button", name="测试执行", exact=True).click()
                page.get_by_label("执行节点", exact=True).select_option("alpha")
                page.screenshot(path=str(directory / "execution.png"), full_page=True)
                page.get_by_role("button", name="Remote regression", exact=True).click()
                page.get_by_text("2 通过 / 2 个测试", exact=True).wait_for(timeout=30000)
                page.get_by_label("关闭报告").click()
                page.get_by_role("button", name="运行维护", exact=True).click()
                expect(page.get_by_role("heading", name="远程执行节点", exact=True)).to_be_visible()
                expect(page.get_by_role("cell", name="在线", exact=True)).to_have_count(2)
                page.screenshot(path=str(directory / "operations.png"), full_page=True)
                browser.close()
            assert not errors, errors
            interrupted = submit(client, "alpha", case_ids=[slow])
            wait_status(client, interrupted, "RUNNING")
            stop(platform)
            platform = start_platform("restarted-platform")
            restarted = wait_run(client, interrupted)
            assert (
                restarted["status"] == "ERROR"
                and "interrupted" in restarted["summary"]["error"].lower()
            )
            assert client.get("/api/runs/" + first).json()["status"] == "PASSED"
            assert (
                hashlib.sha256(client.get(evidence).content).digest()
                == hashlib.sha256(image).digest()
            )
            assert (
                wait_run(client, submit(client, "alpha", case_ids=[health]))["status"] == "PASSED"
            )
            audit = client.get("/api/audit?limit=200").json()
            assert any(e["actor"] == "alpha" and e["role"] == "worker" for e in audit)
        summary = {
            "verified_at": datetime.now().astimezone().isoformat(),
            "project_id": project,
            "coordinator_data": str(data),
            "worker_roots": {k: str(v) for k, v in worker_dirs.items()},
            "independent_workers": "2 processes with distinct credentials and roots",
            "alpha_run": "2 PASSED (HTTP + browser)",
            "beta_run": "1 PASSED (node-local file)",
            "remote_cli": "1 PASSED",
            "browser_selected_node": "2 PASSED",
            "node_local_secret_and_redaction": "PASSED",
            "unassigned_secret": "ERROR as expected",
            "cancellation": "CANCELLED as expected",
            "worker_loss": "ERROR without replay",
            "coordinator_restart": "in-flight ERROR; history and PNG preserved; new task PASSED",
            "evidence_sha256": hashlib.sha256(image).hexdigest(),
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
