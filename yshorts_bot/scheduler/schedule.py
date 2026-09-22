from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

from ..config import ScheduleConfig, parse_hhmm


def resolve_timezone(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return datetime.now().astimezone().tzinfo or timezone.utc


def to_utc_iso(dt: datetime) -> str:
    """Format ISO UTC yang seragam dengan kolom waktu di database (detik, offset +00:00)."""
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def build_upload_schedule(cfg: ScheduleConfig, count: int, now: datetime | None = None) -> list[str]:
    """Hitung `count` waktu upload (ISO UTC) sesuai konfigurasi jadwal.

    - mode interval: slot pertama = `start_time` hari ini; jika sudah lewat, digeser per `interval_hours`
      sampai berada di masa depan (contoh: mulai 08:00, interval 3 jam, sekarang 09:30 -> 11:00, 14:00, ...).
      `start_time = "now"` berarti slot pertama = sekarang.
    - mode specific_times: slot diambil berurutan dari daftar jam, berlanjut ke hari berikutnya.
    """
    if count <= 0:
        return []

    tz = resolve_timezone(cfg.timezone)
    now_local = (now or datetime.now(timezone.utc)).astimezone(tz)

    if cfg.mode == "interval":
        step = timedelta(hours=float(cfg.interval_hours))
        if cfg.start_time == "now":
            first = now_local
        else:
            hour, minute = parse_hhmm(cfg.start_time)
            first = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            while first <= now_local:
                first += step
        return [to_utc_iso(first + step * i) for i in range(count)]

    times = sorted({parse_hhmm(t) for t in cfg.specific_times})
    if not times:
        raise ValueError("Daftar jam (specific_times) kosong; isi minimal satu jam, contoh ['08:00', '18:00'].")

    result: list[str] = []
    day = now_local.date()
    guard = 0
    while len(result) < count and guard < 3700:  # ~10 tahun, pengaman loop
        for hour, minute in times:
            candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
            if candidate > now_local:
                result.append(to_utc_iso(candidate))
                if len(result) >= count:
                    break
        day += timedelta(days=1)
        guard += 1
    return result
