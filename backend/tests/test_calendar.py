import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from omnitest.api.app import create_app
from omnitest.config import Settings
from omnitest.core.ir import Schedule
from omnitest.db import Resource, Run
from omnitest.services.calendar import next_occurrence, preview


def stamp(iso):
    return datetime.fromisoformat(iso).timestamp()


def test_weekday_timezone_and_strictly_future_occurrences():
    cfg = {"schedule_type": "cron", "cron": "0 9 * * mon-fri", "timezone": "Asia/Shanghai"}
    first = next_occurrence(cfg, stamp("2026-10-23T09:00:00+08:00"))
    assert first == stamp("2026-10-26T09:00:00+08:00")
    assert preview(cfg, first, 2)[0]["local"] == "2026-10-27T09:00:00+08:00"


def test_dst_skips_nonexistent_wall_time_and_emits_both_folds_once():
    cfg = {"schedule_type": "cron", "cron": "30 2 * * *", "timezone": "Europe/Berlin"}
    spring = preview(cfg, stamp("2026-03-28T23:00:00+00:00"), 2)
    assert spring[0]["local"] == "2026-03-30T02:30:00+02:00"
    autumn = preview(cfg, stamp("2026-10-24T23:00:00+00:00"), 3)
    assert [row["local"] for row in autumn[:2]] == [
        "2026-10-25T02:30:00+02:00",
        "2026-10-25T02:30:00+01:00",
    ]
    assert autumn[1]["timestamp"] - autumn[0]["timestamp"] == 3600
    assert autumn[2]["local"] == "2026-10-26T02:30:00+01:00"


@pytest.mark.parametrize(
    "patch",
    [
        {"schedule_type": "cron", "cron": "0 0 30 2 *"},
        {"schedule_type": "cron", "cron": "* * *"},
        {"schedule_type": "cron", "cron": "61 * * * *"},
        {"schedule_type": "cron", "cron": "* * * * *", "interval_seconds": 60},
        {"interval_seconds": 60, "timezone": "Missing/Zone"},
        {"interval_seconds": 60, "cron": "* * * * *"},
        {"interval_seconds": 0},
    ],
)
def test_invalid_calendar_configuration_is_rejected(patch):
    with pytest.raises(ValueError):
        Schedule(name="Rejected", suite_id="suite", **patch)


def setup_schedule(stack, **timing):
    _, _, _, resources, _ = stack
    project = resources.save("projects", {"name": "Scheduling"})["id"]
    target = resources.save(
        "targets",
        {
            "name": "HTTP",
            "kind": "http",
            "authorized": True,
            "config": {"base_url": "http://127.0.0.1"},
        },
        project,
    )["id"]
    case = resources.save(
        "cases",
        {"name": "Health", "adapter": "api", "target": target, "steps": [{"action": "request"}]},
        project,
    )["id"]
    suite = resources.save("suites", {"name": "Scheduled suite", "case_ids": [case]}, project)["id"]
    return resources.save(
        "schedules",
        {
            "name": "Schedule",
            "suite_id": suite,
            "interval_seconds": 60,
            "next_run": time.time() - 120,
            **timing,
        },
        project,
    )


def test_overlap_skip_counts_and_counters_cannot_be_forged(stack):
    schedule = setup_schedule(stack, skipped_count=999, last_run_id="forged")
    _, db, _, resources, orchestrator = stack
    assert schedule["body"]["skipped_count"] == 0 and schedule["body"]["last_run_id"] is None
    orchestrator.tick_schedules()
    cfg = resources.get("schedules", schedule["id"]).body
    assert cfg["last_run_id"] and cfg["last_triggered_at"]
    with db.session.begin() as session:
        row = session.get(Resource, schedule["id"])
        row.body = {**row.body, "next_run": time.time() - 1}
    orchestrator.tick_schedules()
    cfg = resources.get("schedules", schedule["id"]).body
    assert cfg["skipped_count"] == 1 and "Previous" in cfg["last_skip_reason"]
    with db.session() as session:
        assert len(list(session.scalars(select(Run)))) == 1
    updated = resources.save(
        "schedules",
        {**cfg, "skipped_count": 123, "last_run_id": "forged"},
        schedule["project_id"],
        schedule["id"],
    )
    assert (
        updated["body"]["skipped_count"] == 1
        and updated["body"]["last_run_id"] == cfg["last_run_id"]
    )


@pytest.mark.parametrize("policy,expected", [("skip", 0), ("coalesce", 1)])
def test_misfire_skip_or_single_coalesced_run(stack, policy, expected):
    schedule = setup_schedule(stack, misfire_policy=policy, misfire_grace_seconds=10)
    _, db, _, resources, orchestrator = stack
    orchestrator.tick_schedules()
    with db.session() as session:
        assert len(list(session.scalars(select(Run)))) == expected
    cfg = resources.get("schedules", schedule["id"]).body
    assert cfg["next_run"] > time.time()
    assert cfg["skipped_count"] == (1 if policy == "skip" else 0)


def test_old_interval_schedule_and_enabled_edit_remain_compatible(stack):
    schedule = setup_schedule(stack, overlap="allow")
    _, db, _, resources, orchestrator = stack
    with db.session.begin() as session:
        row = session.get(Resource, schedule["id"])
        row.body = {
            key: value
            for key, value in row.body.items()
            if key not in {"schedule_type", "overlap", "misfire_policy", "timezone"}
        }
    orchestrator.tick_schedules()
    cfg = resources.get("schedules", schedule["id"]).body
    assert cfg["last_run_id"]
    updated = resources.save(
        "schedules",
        {
            **cfg,
            "schedule_type": "cron",
            "interval_seconds": None,
            "cron": "0 9 * * mon-fri",
            "timezone": "Asia/Shanghai",
        },
        schedule["project_id"],
        schedule["id"],
    )
    assert updated["body"]["next_run"] != cfg["next_run"]
    assert datetime.fromtimestamp(updated["body"]["next_run"]).second == 0


def test_preview_uses_timezone_offsets_and_never_creates_runs(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        response = client.get(
            "/api/schedules/preview",
            params={
                "cron": "0 9 * * mon-fri",
                "timezone": "Asia/Shanghai",
                "after": stamp("2026-10-23T09:00:00+08:00"),
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["occurrences"][0]["local"] == "2026-10-26T09:00:00+08:00"
        assert client.get("/api/runs").json() == []
        assert client.get("/api/schedules/preview", params={"cron": "bad"}).status_code == 422
        assert (
            client.get(
                "/api/schedules/preview", params={"cron": "* * * * *", "after": "NaN"}
            ).status_code
            == 422
        )
