"""Reproducible MQTT definitions for a dedicated local broker."""

from omnitest.core.ir import TestIR


def seed_mqtt(resources, host="127.0.0.1", port=18883, username=None, password_secret=None):
    existing = next(
        (p for p in resources.list("projects") if p["body"]["name"] == "OmniTest MQTT"), None
    )
    if existing:
        return {
            "project_id": existing["id"],
            "suite_id": resources.list("suites", existing["id"])[0]["id"],
            "message": "Project already exists",
        }
    project = resources.save(
        "projects",
        {
            "name": "OmniTest MQTT",
            "description": "MQTT telemetry, binary and retained-message regression",
        },
    )["id"]
    prefix = "omnitest/" + project + "/"
    target = resources.save(
        "targets",
        {
            "name": "Dedicated MQTT fixture",
            "kind": "mqtt",
            "authorized": True,
            "config": {
                "host": host,
                "port": port,
                "topic_prefix": prefix,
                "allow_publish": True,
                "allow_retained": True,
                "username": username,
                "password_secret": password_secret,
            },
        },
        project,
    )["id"]
    cases = []

    def add(name, steps, **kwargs):
        case = TestIR(
            name=name, adapter="mqtt", target=target, steps=steps, tags=["mqtt", "iot"], **kwargs
        )
        cases.append(resources.save("cases", case.model_dump(), project)["id"])

    add(
        "MQTT · telemetry matrix",
        [
            {"action": "subscribe", "config": {"topic": prefix + "sensor/${device}", "qos": 1}},
            {
                "action": "publish",
                "config": {
                    "topic": prefix + "sensor/${device}",
                    "json": {"temperature": "${temperature}", "device": "${device}"},
                    "qos": 1,
                },
            },
            {
                "action": "receive",
                "assertions": [
                    {"path": "$.json.temperature", "op": "eq", "expected": "${temperature}"},
                    {"path": "$.json.device", "op": "eq", "expected": "${device}"},
                ],
            },
        ],
        datasets=[
            {"device": "sensor-a", "temperature": 21},
            {"device": "sensor-b", "temperature": 27},
        ],
    )
    add(
        "MQTT · binary payload",
        [
            {"action": "subscribe", "config": {"topic": prefix + "binary", "qos": 0}},
            {
                "action": "publish",
                "config": {"topic": prefix + "binary", "hex": "000102ff", "qos": 0},
            },
            {
                "action": "receive",
                "assertions": [
                    {"path": "$.hex", "op": "eq", "expected": "000102ff"},
                    {"path": "$.bytes_received", "op": "eq", "expected": 4},
                ],
            },
        ],
    )
    add(
        "MQTT · retained state",
        [
            {
                "action": "publish",
                "config": {"topic": prefix + "state", "json": {"online": True}, "retain": True},
            },
            {"action": "subscribe", "config": {"topic": prefix + "state"}},
            {
                "action": "receive",
                "assertions": [
                    {"path": "$.json.online", "op": "eq", "expected": True},
                    {"path": "$.retained", "op": "eq", "expected": True},
                ],
            },
            {
                "action": "publish",
                "config": {"topic": prefix + "state", "text": "", "retain": True},
            },
        ],
    )
    suite = resources.save(
        "suites",
        {"name": "MQTT · protocol regression", "case_ids": cases, "concurrency": 2},
        project,
    )["id"]
    return {
        "project_id": project,
        "suite_id": suite,
        "cases": len(cases),
        "parameterized_results": 4,
    }
