from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import requires_ffmpeg
from yshorts_bot.config import save_config
from yshorts_bot.dashboard import create_app
from yshorts_bot.db.queue import QueueDB
from yshorts_bot.video.ffmpeg import make_test_clip


@pytest.fixture
def client(tmp_cfg, tmp_path):
    config_path = tmp_path / "config.json"
    save_config(tmp_cfg, str(config_path))
    app = create_app(str(config_path))
    with TestClient(app) as c:
        c.config_path = config_path  # type: ignore[attr-defined]
        c.cfg = tmp_cfg  # type: ignore[attr-defined]
        yield c


def enqueue(client, count=1, **extra):
    body = {"niche": "Fakta hewan", "count": count, "mode": "interval", "interval_hours": 1, "start_time": "now"}
    body.update(extra)
    res = client.post("/api/enqueue", json=body)
    assert res.status_code == 200, res.text
    return res.json()["job_ids"]


def test_index_status_and_stats(client):
    assert "YShorts Bot Studio" in client.get("/").text
    status = client.get("/api/status").json()
    assert status["worker"]["online"] is False
    assert status["config"]["flow_provider"] == "manual"
    assert client.get("/api/stats").json()["total"] == 0


def test_schedule_preview_and_enqueue(client):
    res = client.post("/api/schedule/preview", json={"count": 3, "mode": "interval", "interval_hours": 2, "start_time": "08:00"})
    assert res.status_code == 200 and len(res.json()["times"]) == 3
    res = client.post("/api/schedule/preview", json={"count": 2, "mode": "specific_times", "specific_times": ["21:00", "07:30"]})
    assert res.status_code == 200 and res.json()["times"][0] < res.json()["times"][1]

    ids = enqueue(client, count=2)
    assert ids == [1, 2]
    jobs = client.get("/api/jobs").json()
    assert len(jobs) == 2 and jobs[0]["segments_ready"] == [False, False]
    assert client.get("/api/jobs/1").json()["status"] == "queued"
    assert client.get("/api/jobs/999").status_code == 404


def test_enqueue_validation_errors(client):
    assert client.post("/api/enqueue", json={"niche": "   ", "count": 1}).status_code == 400
    assert client.post("/api/enqueue", json={"niche": "x", "count": 0}).status_code == 422
    res = client.post("/api/enqueue", json={"niche": "x", "count": 1, "start_time": "99:99"})
    assert res.status_code == 400 and "jam" in res.json()["detail"].lower()
    res = client.post("/api/enqueue", json={"niche": "x", "count": 1, "mode": "specific_times", "specific_times": ["25:00"]})
    assert res.status_code == 400


@requires_ffmpeg
def test_upload_segment_validates_and_is_atomic(client, tmp_path):
    (job_id,) = enqueue(client)
    res = client.post(
        f"/api/jobs/{job_id}/upload-segment",
        data={"segment_index": 1},
        files={"file": ("bad.mp4", b"bukan video" * 100, "video/mp4")},
    )
    assert res.status_code == 400
    download_dir = Path(client.cfg.paths.flow_download_dir)
    assert not list(download_dir.glob("*.part"))
    assert not (download_dir / f"job_{job_id}_segment_1.mp4").exists()

    clip = make_test_clip(tmp_path / "clip.mp4", seconds=1.0, size="360x640")
    res = client.post(
        f"/api/jobs/{job_id}/upload-segment",
        data={"segment_index": 2},
        files={"file": ("clip.mp4", clip.read_bytes(), "video/mp4")},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert (download_dir / f"job_{job_id}_segment_2.mp4").exists() and data["width"] == 360
    assert client.post(f"/api/jobs/{job_id}/upload-segment", data={"segment_index": 5}, files={"file": ("c.mp4", b"x", "video/mp4")}).status_code == 400
    assert client.get("/api/jobs").json()[0]["segments_ready"] == [False, True]
    assert client.get(f"/api/jobs/{job_id}/segments/2").status_code == 200
    assert client.get(f"/api/jobs/{job_id}/segments/1").status_code == 404


@requires_ffmpeg
def test_simulate_creates_test_clips_once(client):
    (job_id,) = enqueue(client)
    res = client.post(f"/api/jobs/{job_id}/simulate")
    assert res.status_code == 200, res.text
    assert len(res.json()["created"]) == 2
    assert client.post(f"/api/jobs/{job_id}/simulate").json()["created"] == []
    db = QueueDB(client.cfg.paths.db_path)
    db.update(job_id, status="done")
    assert client.post(f"/api/jobs/{job_id}/simulate").status_code == 409


def test_config_get_and_put(client):
    cfg = client.get("/api/config").json()
    assert cfg["niche"] == "Fakta hewan" and cfg["schedule"]["interval_hours"] == 3
    res = client.put("/api/config", json={"schedule": {"interval_hours": 6, "start_time": "9:00"}, "niche": "Sejarah"})
    assert res.status_code == 200, res.text
    saved = json.loads(client.config_path.read_text(encoding="utf-8"))
    assert saved["schedule"]["interval_hours"] == 6 and saved["schedule"]["start_time"] == "09:00" and saved["niche"] == "Sejarah"
    assert client.get("/api/config").json()["niche"] == "Sejarah"
    res = client.put("/api/config", json={"schedule": {"start_time": "99:99"}})
    assert res.status_code == 400 and "start_time" in res.json()["detail"]
    assert client.put("/api/config", json={"flow": {"provider": "tidak_ada"}}).status_code == 400


def test_pause_resume_retry_cancel_delete(client):
    assert client.post("/api/worker/pause").json()["paused"] is True
    assert client.get("/api/status").json()["worker"]["paused"] is True
    assert client.post("/api/worker/resume").json()["paused"] is False

    (job_id,) = enqueue(client)
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "cancelled"
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 409
    assert client.post(f"/api/jobs/{job_id}/retry").json()["status"] == "queued"
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    res = client.delete(f"/api/jobs/{job_id}?purge=true")
    assert res.status_code == 200
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert client.delete(f"/api/jobs/{job_id}").status_code == 404


def test_logs_endpoint(client):
    res = client.get("/api/logs?source=worker")
    assert res.status_code == 200 and "worker" in res.text.lower()
    assert client.get("/api/logs?source=lainnya").status_code == 422
