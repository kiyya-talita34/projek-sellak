from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.conftest import requires_ffmpeg
from yshorts_bot.ai.provider import MockAIProvider
from yshorts_bot.db.queue import QueueDB, utcnow
from yshorts_bot.errors import NonRetryableError, RetryLaterError
from yshorts_bot.flow.base import FlowProvider
from yshorts_bot.flow.factory import build_flow_provider
from yshorts_bot.flow.prompt_files import expected_video_path, prompt_path
from yshorts_bot.models import Metadata
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


def make_worker(cfg, flow=None, uploader=None) -> Worker:
    return Worker(
        cfg,
        ai=MockAIProvider(),
        flow=flow or build_flow_provider(cfg),
        uploader=uploader or YouTubeUploader(mock=True),
    )


@requires_ffmpeg
def test_full_pipeline_with_mock_services(tmp_cfg):
    db = QueueDB(tmp_cfg.paths.db_path)
    db.init()
    (job_id,) = db.enqueue("Fakta hewan", 1, 3, [PAST])
    worker = make_worker(tmp_cfg)

    worker.process_job(job_id)  # queued -> planning -> waiting segmen 1
    job = db.get(job_id)
    assert job["status"] == "waiting_flow_segment_1"
    assert job["idea"] and json.loads(job["plan_json"])["segments"][1]["index"] == 2
    assert prompt_path(tmp_cfg.paths.prompt_dir, job_id, 1).exists()
    assert prompt_path(tmp_cfg.paths.prompt_dir, job_id, 2).exists()

    worker.process_job(job_id)  # belum ada video -> tetap menunggu, tidak memblokir
    job = db.get(job_id)
    assert job["status"] == "waiting_flow_segment_1"
    assert job["next_run_at"] > utcnow()[:-6] or job["next_run_at"] >= utcnow()
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
    assert job["status"] == "done" and job["youtube_video_id"].startswith("demo_")
    assert db.next_job() is None

    worker.process_job(job_id)  # job selesai tidak diproses ulang
    assert db.get(job_id)["status"] == "done"


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


def test_transient_failure_retries_then_fails_permanently(tmp_cfg):
    db = QueueDB(tmp_cfg.paths.db_path)
    db.init()
    (job_id,) = db.enqueue("x", 1, tmp_cfg.retry.max_attempts, [PAST])
    worker = make_worker(tmp_cfg, flow=BoomFlow(RuntimeError("flow down")))
    worker.process_job(job_id)  # planning ok -> waiting
    worker.process_job(job_id)  # gagal 1
    job = db.get(job_id)
    assert job["status"] == "waiting_flow_segment_1" and job["attempts"] == 1
    assert "flow down" in job["last_error"] and job["next_run_at"] > utcnow()
    db.update(job_id, next_run_at=PAST)
    worker.process_job(job_id)  # gagal 2 = max_attempts -> failed
    job = db.get(job_id)
    assert job["status"] == "failed" and job["failed_from_status"] == "waiting_flow_segment_1"
    assert (Path(tmp_cfg.paths.failed_dir) / f"job_{job_id}.json").exists()
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


def test_flow_wait_timeout_counts_as_failure(tmp_cfg):
    db = QueueDB(tmp_cfg.paths.db_path)
    db.init()
    (job_id,) = db.enqueue("x", 1, 3, [PAST])
    worker = make_worker(tmp_cfg)
    worker.process_job(job_id)
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(microsecond=0).isoformat()
    db.update(job_id, flow_waiting_since=long_ago, next_run_at=PAST)
    worker.process_job(job_id)
    job = db.get(job_id)
    assert job["attempts"] == 1 and "TimeoutError" in job["last_error"]
    assert job["flow_waiting_since"] is None


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
