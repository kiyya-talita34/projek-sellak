from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from yshorts_bot.config import ScheduleConfig, parse_hhmm
from yshorts_bot.scheduler.schedule import build_upload_schedule

TZ = ZoneInfo("Asia/Jakarta")


def test_interval_starts_today_when_start_time_in_future():
    now = datetime(2026, 9, 22, 7, 0, tzinfo=TZ)
    cfg = ScheduleConfig(mode="interval", interval_hours=3, start_time="08:00", timezone="Asia/Jakarta")
    assert build_upload_schedule(cfg, 3, now=now) == [
        "2026-09-22T01:00:00+00:00",
        "2026-09-22T04:00:00+00:00",
        "2026-09-22T07:00:00+00:00",
    ]


def test_interval_catches_up_to_next_slot_when_start_time_passed():
    now = datetime(2026, 9, 22, 9, 30, tzinfo=TZ)
    cfg = ScheduleConfig(mode="interval", interval_hours=3, start_time="08:00", timezone="Asia/Jakarta")
    times = build_upload_schedule(cfg, 2, now=now)
    assert times[0] == "2026-09-22T04:00:00+00:00"  # 11:00 WIB
    assert times[1] == "2026-09-22T07:00:00+00:00"  # 14:00 WIB


def test_interval_daily_rolls_to_tomorrow():
    now = datetime(2026, 9, 22, 9, 30, tzinfo=TZ)
    cfg = ScheduleConfig(mode="interval", interval_hours=24, start_time="08:00", timezone="Asia/Jakarta")
    assert build_upload_schedule(cfg, 1, now=now) == ["2026-09-23T01:00:00+00:00"]


def test_interval_start_now():
    now = datetime(2026, 9, 22, 9, 30, 15, tzinfo=TZ)
    cfg = ScheduleConfig(mode="interval", interval_hours=1, start_time="now", timezone="Asia/Jakarta")
    times = build_upload_schedule(cfg, 2, now=now)
    assert times == ["2026-09-22T02:30:15+00:00", "2026-09-22T03:30:15+00:00"]


def test_start_time_blank_means_now():
    assert ScheduleConfig(start_time="").start_time == "now"
    assert ScheduleConfig(start_time="8:5").start_time == "08:05"


def test_specific_times_sorted_and_roll_over_days():
    now = datetime(2026, 9, 22, 20, 0, tzinfo=TZ)
    cfg = ScheduleConfig(mode="specific_times", specific_times=["21:00", "08:00"], timezone="Asia/Jakarta")
    assert cfg.specific_times == ["08:00", "21:00"]
    times = build_upload_schedule(cfg, 3, now=now)
    assert times == [
        "2026-09-22T14:00:00+00:00",  # 21:00 hari ini
        "2026-09-23T01:00:00+00:00",  # 08:00 besok
        "2026-09-23T14:00:00+00:00",  # 21:00 besok
    ]


def test_specific_times_empty_is_rejected():
    with pytest.raises(ValueError):
        ScheduleConfig(mode="specific_times", specific_times=[])


@pytest.mark.parametrize("bad", ["25:00", "08:60", "abc", "8:xx"])
def test_invalid_time_rejected(bad):
    with pytest.raises(ValueError):
        parse_hhmm(bad)
    with pytest.raises(ValueError):
        ScheduleConfig(start_time=bad)


def test_invalid_timezone_rejected():
    with pytest.raises(ValueError):
        ScheduleConfig(timezone="Mars/Olympus")


def test_zero_count_returns_empty():
    assert build_upload_schedule(ScheduleConfig(), 0) == []
