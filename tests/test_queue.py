from __future__ import annotations

import sqlite3
from pathlib import Path

from yshorts_bot.db.queue import QueueDB, utcnow, waiting_status


def make_db(tmp_path: Path) -> QueueDB:
    db = QueueDB(str(tmp_path / "q.sqlite3"))
    db.init()
    return db


def test_enqueue_and_next_job_order(tmp_path):
    db = make_db(tmp_path)
    ids = db.enqueue("niche", 2, 3, ["2030-01-01T00:00:00+00:00", "2030-01-01T03:00:00+00:00"])
    assert ids == [1, 2]
    job = db.next_job()
    assert job["id"] == 1 and job["status"] == "queued"
    assert job["scheduled_upload_at"] == "2030-01-01T00:00:00+00:00"
    db.update(1, next_run_at="2999-01-01T00:00:00+00:00")
    assert db.next_job()["id"] == 2
    db.update(2, status="done")
    assert db.next_job() is None


def test_next_job_prefers_longest_waiting(tmp_path):
    db = make_db(tmp_path)
    db.enqueue("a", 2, 3)
    db.update(1, next_run_at="2020-01-01T00:00:10+00:00")
    db.update(2, next_run_at="2020-01-01T00:00:00+00:00")
    assert db.next_job()["id"] == 2


def test_update_serializes_json_fields(tmp_path):
    db = make_db(tmp_path)
    db.enqueue("a", 1, 3)
    db.update(1, plan_json={"idea": "x"}, segment_paths_json=["a.mp4", None])
    job = db.get(1)
    assert job["plan_json"] == '{"idea": "x"}'
    assert job["segment_paths_json"] == '["a.mp4", null]'


def test_retry_resumes_from_failed_stage(tmp_path):
    db = make_db(tmp_path)
    db.enqueue("a", 1, 3)
    db.update(1, status="uploading", output_path="x.mp4", attempts=2)
    db.mark_failed(1, "boom")
    job = db.get(1)
    assert job["status"] == "failed" and job["failed_from_status"] == "uploading" and job["next_run_at"] is None
    assert db.retry_job(1)
    job = db.get(1)
    assert job["status"] == "uploading"
    assert job["attempts"] == 0 and job["last_error"] is None and job["failed_from_status"] is None
    assert job["next_run_at"] <= utcnow()


def test_retry_unknown_job_and_legacy_failed(tmp_path):
    db = make_db(tmp_path)
    assert not db.retry_job(99)
    db.enqueue("a", 1, 3)
    db.update(1, status="failed")  # baris lama tanpa failed_from_status
    assert db.retry_job(1)
    assert db.get(1)["status"] == "queued"


def test_cancel_and_stats(tmp_path):
    db = make_db(tmp_path)
    db.enqueue("a", 3, 3)
    db.update(1, status="done")
    db.update(3, status=waiting_status(3))
    assert db.cancel_job(2)
    assert not db.cancel_job(1)  # sudah selesai
    job = db.get(2)
    assert job["status"] == "cancelled" and job["failed_from_status"] == "queued"
    stats = db.get_stats()
    assert stats["total"] == 3
    assert stats["done"] == 1 and stats["cancelled"] == 1 and stats["failed_or_cancelled"] == 1
    assert stats["waiting_flow"] == 1 and stats["processing"] == 1
    assert stats[waiting_status(3)] == 1


def test_increment_attempt_and_defer(tmp_path):
    db = make_db(tmp_path)
    db.enqueue("a", 1, 3)
    db.update(1, flow_waiting_since="2020-01-01T00:00:00+00:00")
    db.increment_attempt(1, "err", next_run_at="2999-01-01T00:00:00+00:00")
    job = db.get(1)
    assert job["attempts"] == 1 and job["last_error"] == "err" and job["flow_waiting_since"] is None
    db.defer(1, "2998-01-01T00:00:00+00:00", "kuota")
    job = db.get(1)
    assert job["attempts"] == 1 and job["next_run_at"] == "2998-01-01T00:00:00+00:00" and job["last_error"] == "kuota"


def test_delete_and_list(tmp_path):
    db = make_db(tmp_path)
    db.enqueue("a", 2, 3)
    assert [j["id"] for j in db.list_jobs()] == [2, 1]
    assert db.delete_job(1) and not db.delete_job(1)
    assert [j["id"] for j in db.list_jobs()] == [2]


def test_meta_roundtrip(tmp_path):
    db = make_db(tmp_path)
    assert db.get_meta("x") is None and db.get_meta("x", "d") == "d"
    db.set_meta("worker_paused", "1")
    db.set_meta("worker_paused", "0")
    assert db.get_meta("worker_paused") == "0"
    assert db.all_meta() == {"worker_paused": "0"}
    db.delete_meta("worker_paused")
    assert db.all_meta() == {}


def test_migration_from_old_schema(tmp_path):
    path = tmp_path / "old.sqlite3"
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, niche TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
        attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3, next_run_at TEXT, idea TEXT, plan_json TEXT,
        metadata_json TEXT, segment_paths_json TEXT, output_path TEXT, youtube_video_id TEXT, last_error TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""
    )
    con.execute("INSERT INTO jobs(niche,status,created_at,updated_at) VALUES('lama','failed','x','x')")
    con.commit()
    con.close()
    db = QueueDB(str(path))
    db.init()
    job = db.get(1)
    assert job["scheduled_upload_at"] is None and job["failed_from_status"] is None
    assert db.retry_job(1) and db.get(1)["status"] == "queued"
    assert db.enqueue("baru", 1, 3) == [2]
