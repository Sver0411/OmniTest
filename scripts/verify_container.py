"""Verify the built application image and persisted history after a restart."""

import argparse
import json
import secrets
import subprocess
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="omnitest:ci")
    parser.add_argument("--port", type=int, default=18000)
    args = parser.parse_args()
    name = "omnitest-check-" + secrets.token_hex(6)
    token = secrets.token_urlsafe(32)
    address = f"http://127.0.0.1:{args.port}"

    def docker(*argv):
        return subprocess.check_output(["docker", *argv], encoding="utf-8").strip()

    def wait_ready(client):
        deadline = time.monotonic() + 60
        while True:
            try:
                if client.get("/api/ready").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("Container did not become ready")
            time.sleep(0.25)

    started = False
    try:
        docker(
            "run",
            "--name",
            name,
            "-d",
            "-p",
            f"127.0.0.1:{args.port}:8000",
            "-e",
            "OMNITEST_API_TOKEN=" + token,
            args.image,
        )
        started = True
        with httpx.Client(
            base_url=address,
            trust_env=False,
            timeout=10,
            headers={"Authorization": "Bearer " + token},
        ) as client:
            wait_ready(client)
            assert client.get("/").status_code == 200
            assert "/assets/" in client.get("/").text
            project = client.post(
                "/api/resources/projects", json={"body": {"name": "Container check"}}
            ).json()["id"]
            target = client.post(
                "/api/resources/targets",
                json={
                    "project_id": project,
                    "body": {
                        "name": "Local health",
                        "kind": "http",
                        "authorized": True,
                        "config": {"base_url": "http://127.0.0.1:8000"},
                    },
                },
            ).json()["id"]
            case = client.post(
                "/api/resources/cases",
                json={
                    "project_id": project,
                    "body": {
                        "name": "Health request",
                        "adapter": "api",
                        "target": target,
                        "steps": [
                            {
                                "action": "request",
                                "config": {"path": "/api/health"},
                                "assertions": [
                                    {"path": "$.status_code", "op": "eq", "expected": 200}
                                ],
                            }
                        ],
                    },
                },
            ).json()["id"]
            browser_case = client.post(
                "/api/resources/cases",
                json={
                    "project_id": project,
                    "body": {
                        "name": "Container Chromium",
                        "adapter": "web",
                        "target": target,
                        "steps": [
                            {
                                "action": "goto",
                                "config": {"path": "/api/health"},
                                "assertions": [
                                    {"path": "$.text", "op": "contains", "expected": "ok"}
                                ],
                            },
                            {"id": "container-screen", "action": "screenshot"},
                        ],
                    },
                },
            ).json()["id"]
            identity = client.post(
                "/api/runs", json={"project_id": project, "case_ids": [case, browser_case]}
            ).json()["id"]
            deadline = time.monotonic() + 30
            while True:
                report = client.get("/api/runs/" + identity).json()
                if report["status"] not in {"PENDING", "RUNNING"}:
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError("Container run did not finish")
                time.sleep(0.1)
            assert report["summary"]["counts"] == {"PASSED": 2}, report
            result = next(row for row in report["results"] if row["adapter"] == "web")
            relative = result["detail"]["attempts"][0]["evidence"][0]
            evidence_url = f"/api/evidence/{identity}/{result['id']}/{relative}"
            image = client.get(evidence_url)
            image.raise_for_status()
            assert image.content.startswith(b"\x89PNG\r\n\x1a\n")
            docker("restart", "--timeout", "10", name)
            wait_ready(client)
            assert client.get("/api/runs/" + identity).json()["status"] == "PASSED"
            restored_image = client.get(evidence_url)
            restored_image.raise_for_status()
            assert restored_image.content == image.content
            assert client.get("/api/audit").json()
            summary = {
                "image": args.image,
                "version": client.get("/api/health").json()["version"],
                "ui": "PASSED",
                "execution": "2 PASSED (HTTP + Chromium)",
                "screenshot_after_restart": "PASSED",
                "history_after_restart": "PASSED",
            }
            directory = Path("data/phase5")
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "container-summary.json").write_text(
                json.dumps(summary, indent=2), encoding="utf-8"
            )
            print(json.dumps(summary, indent=2))
    finally:
        if started:
            docker("rm", "-f", name)


if __name__ == "__main__":
    main()
