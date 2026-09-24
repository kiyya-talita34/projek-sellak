"""Kondisi balapan: aksi pengguna (Batalkan/Retry) saat worker sedang menjalankan tahap yang lama."""
from __future__ import annotations

from typing import Any

from yshorts_bot.ai.provider import AIProvider, MockAIProvider
from yshorts_bot.config import NotificationsConfig
from yshorts_bot.db.queue import QueueDB
from yshorts_bot.flow.base import FlowProvider
from yshorts_bot.flow.factory import build_flow_provider
from yshorts_bot.notify import Notifier
from yshorts_bot.worker import Worker
from yshorts_bot.youtube.uploader import YouTubeUploader

PAST = "2000-01-01T00:00:00+00:00"


class CancelDuringCall(AIProvider):
    """Provider AI yang 'membatalkan' job dari dashboard di tengah panggilan AI."""

    name = "cancel-mid-call"

    def __init__(self, db: QueueDB, job_id: int):
        self.db, self.job_id, self.inner = db, job_id, MockAIProvider()

    def generate_json(self, system: str, user: str, temperature: float | None = None) -> dict[str, Any]:
        assert self.db.cancel_job(self.job_id)
        return self.inner.generate_json(system, user, temperature)


class CancelDuringFlow(FlowProvider):
    name = "cancel-flow"

    def __init__(self, db: QueueDB, job_id: int, exc: Exception | None = None):
        self.db, self.job_id, self.exc = db, job_id, exc

    def request_segment(self, job_id, niche, segment):
        self.db.cancel_job(self.job_id)
        if self.exc:
            raise self.exc
        return None


def silent_notifier() -> Notifier:
    return Notifier(NotificationsConfig(enabled=False), post=lambda *a, **k: None, webhook_url="", telegram_token="", telegram_chat_id="")


def test_cancel_during_planning_is_not_overwritten(tmp_cfg):
    db = QueueDB(tmp_cfg.paths.db_path)
    db.init()
    (job_id,) = db.enqueue("x", 1, 3, [PAST])
    worker = Worker(tmp_cfg, ai=CancelDuringCall(db, job_id), flow=build_flow_provider(tmp_cfg), uploader=YouTubeUploader(mock=True), notifier=silent_notifier())
    worker.process_job(job_id)
    job = db.get(job_id)
    assert job["status"] == "cancelled" and job["failed_from_status"] == "planning"
    assert job["plan_json"] is None  # hasil AI diabaikan


def test_failure_after_cancel_does_not_resurrect_job(tmp_cfg):
    db = QueueDB(tmp_cfg.paths.db_path)
    db.init()
    (job_id,) = db.enqueue("x", 1, 3, [PAST])
    worker = Worker(tmp_cfg, ai=MockAIProvider(), flow=build_flow_provider(tmp_cfg), uploader=YouTubeUploader(mock=True), notifier=silent_notifier())
    worker.process_job(job_id)  # -> waiting_flow_segment_1
    worker.flow = CancelDuringFlow(db, job_id, RuntimeError("flow meledak"))
    worker.process_job(job_id)
    job = db.get(job_id)
    assert job["status"] == "cancelled" and job["attempts"] == 0
    assert job["last_error"] == "Dibatalkan oleh pengguna"


def test_cancel_during_flow_poll_keeps_cancelled(tmp_cfg):
    db = QueueDB(tmp_cfg.paths.db_path)
    db.init()
    (job_id,) = db.enqueue("x", 1, 3, [PAST])
    worker = Worker(tmp_cfg, ai=MockAIProvider(), flow=build_flow_provider(tmp_cfg), uploader=YouTubeUploader(mock=True), notifier=silent_notifier())
    worker.process_job(job_id)
    worker.flow = CancelDuringFlow(db, job_id)
    worker.process_job(job_id)
    job = db.get(job_id)
    assert job["status"] == "cancelled" and job["next_run_at"] is None


def test_cancel_then_retry_resumes_original_failed_stage(tmp_path):
    db = QueueDB(str(tmp_path / "q.sqlite3"))
    db.init()
    db.enqueue("a", 1, 3)
    db.update(1, status="uploading")
    db.mark_failed(1, "boom")
    assert db.cancel_job(1)
    job = db.get(1)
    assert job["status"] == "cancelled" and job["failed_from_status"] == "uploading"
    assert db.retry_job(1) and db.get(1)["status"] == "uploading"


def test_retry_on_scheduled_job_keeps_its_schedule(tmp_path):
    db = QueueDB(str(tmp_path / "q.sqlite3"))
    db.init()
    db.enqueue("a", 1, 3, ["2999-01-01T00:00:00+00:00"])
    db.update(1, status="scheduled", next_run_at="2999-01-01T00:00:00+00:00", attempts=1, last_error="x")
    assert db.retry_job(1)
    job = db.get(1)
    assert job["status"] == "scheduled" and job["next_run_at"] == "2999-01-01T00:00:00+00:00" and job["attempts"] == 0
    db.mark_failed(1, "boom")
    assert db.retry_job(1)
    assert db.get(1)["next_run_at"] == "2999-01-01T00:00:00+00:00"


def test_cas_update_only_applies_when_status_matches(tmp_path):
    db = QueueDB(str(tmp_path / "q.sqlite3"))
    db.init()
    db.enqueue("a", 1, 3)
    assert db.update(1, expected_status="queued", status="planning")
    assert not db.update(1, expected_status="queued", status="merging")
    assert db.get(1)["status"] == "planning"
    assert db.update(1, idea="tanpa syarat")
    assert not db.update(1)
