from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..config import ScheduleConfig


def build_upload_schedule(cfg: ScheduleConfig, count: int) -> list[str]:
    try:
        tz = ZoneInfo(cfg.timezone)
        now_local = datetime.now(tz)
    except Exception:
        now_local = datetime.now().astimezone()
        tz = now_local.tzinfo


    if cfg.mode == "interval":
        hour, minute = map(int, cfg.start_time.split(":"))
        first = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if first < now_local:
            first += timedelta(days=1)
        return [(first + timedelta(hours=cfg.interval_hours * i)).astimezone(timezone.utc).isoformat() for i in range(count)]

    times = []
    day = now_local.date()
    while len(times) < count:
        for t in cfg.specific_times:
            hour, minute = map(int, t.split(":"))
            candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
            if candidate >= now_local:
                times.append(candidate.astimezone(timezone.utc).isoformat())
                if len(times) >= count:
                    break
        day = day + timedelta(days=1)
    return times
