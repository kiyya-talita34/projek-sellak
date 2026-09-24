from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.conftest import requires_ffmpeg
from yshorts_bot.ai.provider import MockAIProvider
from yshorts_bot.config import NotificationsConfig
from yshorts_bot.db.queue import QueueDB, utcnow
from yshorts_bot.errors import NonRetryableError, RetryLaterError
from yshorts_bot.flow.base import FlowProvider
from yshorts_bot.flow.factory import build_flow_provider
from yshorts_bot.flow.prompt_files import expected_video_path, prompt_path
from yshorts_bot.models import Metadata
from yshorts_bot.notify import Notifier
from yshorts_bot.video.ffmpeg import make_test_clip
from yshorts_bot.worker import Worker
from yshorts_bot.youtube.uploader import YouTubeUploader

PAST = "2000-01-01T00:00:00+00:00"


class BoomFlow(FlowProvider):
    name = "boom"

    def __init__(self, exc: Exception):
        self.exc = exc

    def request_segment(self, job_id, niche, segment):
        raise self.exc


class BoomUploader(YouTubeUploader):
    def __init__(self, exc: Exception):
        super().__init__(mock=True)
        self.exc = exc

    def upload(self, *args, **kwargs):
        raise self.exc


class RecordingNotifier(Notifier):
    def __init__(self):
        super().__init__(NotificationsConfig(enabled=True), post=lambda *a, **k: None, webhook_url="https://example.com/hook", telegram_token="", telegram_chat_id="")
        self.events: list[tuple[str, int]] = []

    def notify_job(self, event, job, message="", wait=False):
        self.events.append((event, job["id"]))


class RecordingUploader(YouTubeUploader):
    def __init__(self, playlist_id=""):
        super().__init__(mock=True, playlist_id=playlist_id)
        self.playlist_calls: list[tuple[str, str]] = []

    def add_to_playlist(self, video_id, playlist_id=None):
        self.playlist_calls.append((video_id, playlist_id or self.playlist_id))
        return True


def make_worker(cfg, flow=None, uploader=None, notifier=None) -> Worker:
    return Worker(
        cfg,
        ai=MockAIProvider(),
        flow=flow or build_flow_provider(cfg),
        uploader=uploader or YouTubeUploader(mock=True),
        notifier=notifier or RecordingNotifier(),
    )


def run_until(worker: Worker, db: QueueDB, job_id: int, status: str, max_steps: int = 20) -> dict:
    for _ in range(max_steps):
        job = db.get(job_id)
        if job["status"] == status:
            return job
        db.update(job_id, next_run_at=PAST)
        worker.process_job(job_id)
    return db.get(job_id)


@requires_ffmpeg
def test_full_pipeline_with_mock_services(tmp_cfg):
    db = QueueDB(tmp_cfg.paths.db_path)
    db.init()
    (job_id,) = db.enqueue("Fakta hewan", 1, 3, [PAST])
    notifier = RecordingNotifier()
    worker = make_worker(tmp_cfg, notifier=notifier)

    worker.process_job(job_id)  # queued -> planning -> waiting segmen 1
    job = db.get(job_id)
    assert job["status"] == "waiting_flow_segment_1"
    assert job["idea"] and json.loads(job["plan_json"])["segments"][1]["index"] == 2
    assert prompt_path(tmp_cfg.paths.prompt_dir, job_id, 1).exists()
    assert prompt_path(tmp_cfg.paths.prompt_dir, job_id, 2).exists()

    worker.process_job(job_id)  # belum ada video -> tetap menunggu, tidak memblokir
    job = db.get(job_id)
    assert job["status"] == "waiting_flow_segment_1"
    assert job["next_run_at"] >= utcnow()[:16]
    assert job["attempts"] == 0

    download_dir = tmp_cfg.paths.flow_download_dir
    make_test_clip(expected_video_path(download_dir, job_id, 1), seconds=2.0, size="720x1280")
    make_test_clip(expected_video_path(download_dir, job_id, 2), seconds=2.0, size="720x1280", hue_shift=90, with_audio=True)

    db.update(job_id, next_run_at=PAST)
    worker.process_job(job_id)
    assert db.get(job_id)["status"] == "waiting_flow_segment_2"
    worker.process_job(job_id)
    assert db.get(job_id)["status"] == "merging"
    worker.process_job(job_id)
    job = db.get(job_id)
    assert job["status"] == "metadata" and Path(job["output_path"]).exists()

    worker.process_job(job_id)
    job = db.get(job_id)
    assert job["status"] == "scheduled" and job["next_run_at"] == PAST
    meta = Metadata.from_dict(json.loads(job["metadata_json"]))
    assert meta.title and meta.hashtags[0] == "#Shorts"

    worker.process_job(job_id)
    assert db.get(job_id)["status"] == "uploading"
    worker.process_job(job_id)
    job = db.get(job_id)
    assert job["status"] == "done" and job["youtube_video_id"].startswith("demo_") and job["uploaded_at"]
    assert db.next_job() is None
    assert notifier.events == [("done", job_id)]

    worker.process_job(job_id)  # job selesai tidak diproses ulang
    assert db.get(job_id)["status"] == "done"


@requires_ffmpeg
def test_approval_flow_playlist_and_cleanup(tmp_cfg):
    tmp_cfg.youtube.require_approval = True
    tmp_cfg.youtube.playlist_id = "PL123"
    tmp_cfg.maintenance.delete_segments_after_upload = True
    db = QueueDB(tmp_cfg.paths.db_path)
    db.init()
    (job_id,) = db.enqueue("Sejarah", 1, 3, [PAST])
    notifier = RecordingNotifier()
    uploader = RecordingUploader(playlist_id="PL123")
    worker = make_worker(tmp_cfg, uploader=uploader, notifier=notifier)
    worker.process_job(job_id)
    download_dir = tmp_cfg.paths.flow_download_dir
    seg1 = make_test_clip(expected_video_path(download_dir, job_id, 1), seconds=1.5, size="360x640")
    seg2 = make_test_clip(expected_video_path(download_dir, job_id, 2), seconds=1.5, size="360x640", with_audio=True)

    job = run_until(worker, db, job_id, "awaiting_approval")
    assert job["status"] == "awaiting_approval" and job["next_run_at"] is None
    assert ("awaiting_approval", job_id) in notifier.events
    assert db.next_job() is None  # worker tidak menyentuh job sampai disetujui
    worker.process_job(job_id)  # dipanggil manual -> hanya log, tidak berubah
    assert db.get(job_id)["status"] == "awaiting_approval"

    # pengguna mengedit metadata lalu menyetujui
    db.update(job_id, metadata_json=Metadata("Judul saya #Shorts", "Deskripsi saya", ["#Shorts"], ["a"]).to_dict())
    assert db.approve_job(job_id, upload_now=True)
    job = run_until(worker, db, job_id, "done")
    assert job["status"] == "done"
    assert uploader.playlist_calls == [(job["youtube_video_id"], "PL123")]
    assert ("done", job_id) in notifier.events
    assert not seg1.exists() and not seg2.exists()  # dibersihkan setelah upload
    assert Path(job["output_path"]).exists()  # output dipertahankan


def test_multiple_jobs_do_not_block_each_other(tmp_cfg):
    db = QueueDB(tmp_cfg.paths.db_path)
    db.init()
    ids = db.enqueue("Fakta hewan", 2, 3, [PAST, PAST])
    worker = make_worker(tmp_cfg)
    for _ in range(4):
        job = db.next_job()
        if job:
            worker.process_job(job["id"])
    statuses = {db.get(i)["status"] for i in ids}
    assert statuses == {"waiting_flow_segment_1"}  # kedua job sudah punya prompt, tidak ada yang menunggu 24 jam


@requires_ffmpeg
def test_inbox_assigns_files_in_order(tmp_cfg):
    db = QueueDB(tmp_cfg.paths.db_path)
    db.init()
    ids = db.enqueue("Fakta hewan", 2, 3, [PAST, PAST])
    worker = make_worker(tmp_cfg)
    for job_id in ids:
        worker.process_job(job_id)  # keduanya menunggu segmen 1
    inbox = Path(tmp_cfg.flow.inbox_dir)
    make_test_clip(inbox / "flow_download_a.mp4", seconds=1.0, size="360x640")
    make_test_clip(inbox / "flow_download_b.mp4", seconds=1.0, size="360x640")
    make_test_clip(inbox / "flow_download_c.mp4", seconds=1.0, size="360x640")
    (inbox / "sisa.mp4.crdownload").write_bytes(b"0" * 5000)

    worker._process_inbox()
    download_dir = tmp_cfg.paths.flow_download_dir
    assert expected_video_path(download_dir, ids[0], 1).exists()
    assert expected_video_path(download_dir, ids[0], 2).exists()  # file kedua -> segmen 2 job pertama
    assert expected_video_path(download_dir, ids[1], 1).exists()  # file ketiga -> job kedua
    assert not expected_video_path(download_dir, ids[1], 2).exists()
    assert [p.name for p in inbox.iterdir()] == ["sisa.mp4.crdownload"]  # file parsial dibiarkan

    # worker melanjutkan job pertama sampai merging tanpa campur tangan
    job = run_until(worker, db, ids[0], "merging")
    assert job["status"] == "merging"


def test_inbox_disabled_leaves_files(tmp_cfg):
    tmp_cfg.flow.inbox_auto_assign = False
    db = QueueDB(tmp_cfg.paths.db_path)
    db.init()
    db.enqueue("x", 1, 3, [PAST])
    worker = make_worker(tmp_cfg)
    worker.process_job(1)
    inbox = Path(tmp_cfg.flow.inbox_dir)
    (inbox / "file.mp4").write_bytes(b"0" * 5000)
    worker._process_inbox()
    assert (inbox / "file.mp4").exists()


def test_transient_failure_retries_then_fails_permanently(tmp_cfg):
    db = QueueDB(tmp_cfg.paths.db_path)
    db.init()
    (job_id,) = db.enqueue("x", 1, tmp_cfg.retry.max_attempts, [PAST])
    notifier = RecordingNotifier()
    worker = make_worker(tmp_cfg, flow=BoomFlow(RuntimeError("flow down")), notifier=notifier)
    worker.process_job(job_id)  # planning ok -> waiting
    worker.process_job(job_id)  # gagal 1
    job = db.get(job_id)
    assert job["status"] == "waiting_flow_segment_1" and job["attempts"] == 1
    assert "flow down" in job["last_error"] and job["next_run_at"] > utcnow()
    assert notifier.events == []
    db.update(job_id, next_run_at=PAST)
    worker.process_job(job_id)  # gagal 2 = max_attempts -> failed
    job = db.get(job_id)
    assert job["status"] == "failed" and job["failed_from_status"] == "waiting_flow_segment_1"
    assert (Path(tmp_cfg.paths.failed_dir) / f"job_{job_id}.json").exists()
    assert notifier.events == [("failed", job_id)]
    assert db.retry_job(job_id) and db.get(job_id)["status"] == "waiting_flow_segment_1"


def test_non_retryable_error_fails_immediately(tmp_cfg):
    db = QueueDB(tmp_cfg.paths.db_path)
    db.init()
    (job_id,) = db.enqueue("x", 1, 3, [PAST])
    worker = make_worker(tmp_cfg, flow=BoomFlow(NonRetryableError("kredensial salah")))
    worker.process_job(job_id)
    worker.process_job(job_id)
    job = db.get(job_id)
    assert job["status"] == "failed" and "kredensial" in job["last_error"]


def test_retry_later_defers_without_consuming_attempts(tmp_cfg):
    db = QueueDB(tmp_cfg.paths.db_path)
    db.init()
    (job_id,) = db.enqueue("x", 1, 3, [PAST])
    out = Path(tmp_cfg.paths.output_dir) / "fake.mp4"
    out.write_bytes(b"0" * 2048)
    db.update(job_id, status="uploading", output_path=str(out), metadata_json=Metadata("t", "d", ["#Shorts"], []).to_dict())
    worker = make_worker(tmp_cfg, uploader=BoomUploader(RetryLaterError("kuota habis", delay_seconds=3600)))
    worker.process_job(job_id)
    job = db.get(job_id)
    assert job["status"] == "uploading" and job["attempts"] == 0
    assert job["next_run_at"] > utcnow() and "kuota" in job["last_error"]


def test_flow_wait_timeout_fails_permanently_until_retry(tmp_cfg):
    db = QueueDB(tmp_cfg.paths.db_path)
    db.init()
    (job_id,) = db.enqueue("x", 1, 3, [PAST])
    worker = make_worker(tmp_cfg)
    worker.process_job(job_id)
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(microsecond=0).isoformat()
    db.update(job_id, flow_waiting_since=long_ago, next_run_at=PAST)
    worker.process_job(job_id)
    job = db.get(job_id)
    # Batas tunggu Flow bersifat final (tidak diulang 3x = 72 jam); Retry melanjutkan menunggu dari nol.
    assert job["status"] == "failed" and "belum tersedia" in job["last_error"]
    assert job["failed_from_status"] == "waiting_flow_segment_1"
    assert db.retry_job(job_id)
    job = db.get(job_id)
    assert job["status"] == "waiting_flow_segment_1" and job["flow_waiting_since"] is None
    worker.process_job(job_id)
    assert db.get(job_id)["flow_waiting_since"] is not None  # penghitung mulai lagi


def test_merge_self_heals_when_segment_missing(tmp_cfg):
    db = QueueDB(tmp_cfg.paths.db_path)
    db.init()
    (job_id,) = db.enqueue("x", 1, 3, [PAST])
    worker = make_worker(tmp_cfg)
    worker.process_job(job_id)
    db.update(job_id, status="merging", segment_paths_json=["/tidak/ada/1.mp4", None])
    worker.process_job(job_id)
    job = db.get(job_id)
    assert job["status"] == "waiting_flow_segment_1" and job["attempts"] == 0


def test_heartbeat_and_pause_flags(tmp_cfg):
    db = QueueDB(tmp_cfg.paths.db_path)
    db.init()
    worker = make_worker(tmp_cfg)
    worker._heartbeat()
    meta = db.all_meta()
    assert meta["worker_heartbeat"] and meta["worker_flow_provider"] == "manual"
    db.set_meta("worker_paused", "1")
    assert worker._is_paused()
    worker._heartbeat(final=True)
    assert "worker_heartbeat" not in db.all_meta() and db.get_meta("worker_stopped_at")
