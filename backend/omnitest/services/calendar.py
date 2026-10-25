"""Next-occurrence calculations independent of the persistent queue."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger


def cron_trigger(expression, timezone, after):
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Unknown IANA schedule timezone") from exc
    fields = expression.split()
    if len(fields) != 5 or len(expression) > 200:
        raise ValueError("Cron must contain five fields: minute hour day month weekday")
    minute, hour, day, month, weekday = fields
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=weekday,
        timezone=zone,
        end_date=datetime.fromtimestamp(after, UTC) + timedelta(days=3660),
    )


def next_occurrence(config, after):
    if config.get("schedule_type", "interval") == "interval":
        return after + config["interval_seconds"]
    trigger = cron_trigger(config["cron"], config.get("timezone", "UTC"), after)
    current = datetime.fromtimestamp(after, UTC) + timedelta(microseconds=1)
    candidate = trigger.get_next_fire_time(None, current)
    for _ in range(32):
        if candidate is None:
            raise ValueError("Cron has no valid occurrence within ten years")
        stamp = candidate.timestamp()
        roundtrip = datetime.fromtimestamp(stamp, trigger.timezone)
        if stamp > after and roundtrip.replace(tzinfo=None) == candidate.replace(tzinfo=None):
            return stamp
        candidate = trigger.get_next_fire_time(
            candidate, datetime.fromtimestamp(stamp, UTC) + timedelta(seconds=1)
        )
    raise ValueError("Cron could not advance to a valid occurrence")


def preview(config, after, count=5):
    values = []
    zone = ZoneInfo(config.get("timezone", "UTC"))
    for _ in range(count):
        after = next_occurrence(config, after)
        values.append(
            {"timestamp": after, "local": datetime.fromtimestamp(after, zone).isoformat()}
        )
    return values
