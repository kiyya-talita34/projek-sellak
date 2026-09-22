from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from yshorts_bot.worker import pid_alive, worker_online


def _iso(delta_seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).replace(microsecond=0).isoformat()


def test_pid_alive_for_current_process_and_bogus_pid():
    assert pid_alive(os.getpid())
    assert not pid_alive(0)
    assert not pid_alive(2_000_000_000)


def test_worker_online_requires_fresh_heartbeat_and_live_process():
    assert not worker_online({})
    assert worker_online({"worker_heartbeat": _iso(-5), "worker_pid": str(os.getpid())})
    assert not worker_online({"worker_heartbeat": _iso(-600), "worker_pid": str(os.getpid())})
    # heartbeat segar tetapi prosesnya sudah mati (mis. jendela worker ditutup paksa)
    assert not worker_online({"worker_heartbeat": _iso(-5), "worker_pid": "2000000000"})
    assert worker_online({"worker_heartbeat": _iso(-5)})  # tanpa pid: hanya heartbeat yang dinilai
