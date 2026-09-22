from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field, ValidationError

from . import __version__
from .config import AppConfig, ScheduleConfig, env_flag, load_config, save_config
from .db.queue import TERMINAL_STATUSES, QueueDB, is_waiting_status
from .flow.prompt_files import candidate_video_paths, expected_video_path, find_ready_video, prompt_path
from .scheduler.schedule import build_upload_schedule
from .video.ffmpeg import FFmpegError, make_test_clip, probe, resolve_ffmpeg
from .worker import worker_online

log = logging.getLogger(__name__)

TEMPLATE_PATH = Path(__file__).parent / "templates" / "dashboard.html"
SIMULATABLE_STATUSES = {"queued", "planning"}


class ScheduleRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=100)
    mode: Literal["interval", "specific_times"] = "interval"
    interval_hours: float = Field(default=3, gt=0, le=744)
    start_time: str = "08:00"
    specific_times: list[str] = Field(default_factory=list)


class EnqueueRequest(ScheduleRequest):
    niche: str = Field(min_length=1, max_length=200)


def _deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _schedule_from_request(base: ScheduleConfig, req: ScheduleRequest) -> ScheduleConfig:
    data = base.model_dump()
    data.update({"mode": req.mode, "interval_hours": req.interval_hours, "start_time": req.start_time})
    if req.specific_times:
        data["specific_times"] = req.specific_times
    try:
        return ScheduleConfig(**data)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=_validation_message(e)) from e


def _validation_message(error: ValidationError) -> str:
    parts = []
    for err in error.errors():
        loc = ".".join(str(x) for x in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
    return "; ".join(parts) or "Data tidak valid"


def create_app(config_path: str = "config.json") -> FastAPI:
    state: dict[str, Any] = {"cfg": load_config(config_path)}

    def cfg() -> AppConfig:
        return state["cfg"]

    db = QueueDB(cfg().paths.db_path)
    db.init()
    app = FastAPI(title="YShorts Bot Studio", version=__version__)

    # ------------------------------------------------------------------ helpers
    def _load_json(raw: str | None, default: Any) -> Any:
        if not raw:
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return default

    def _serialize_job(job: dict[str, Any]) -> dict[str, Any]:
        item = dict(job)
        plan = _load_json(item.get("plan_json"), None)
        item["plan"] = plan
        item["metadata"] = _load_json(item.get("metadata_json"), None)
        item["segment_paths"] = _load_json(item.get("segment_paths_json"), [])
        segments = (plan or {}).get("segments") or []
        expected = len(segments) or cfg().segments_per_video
        ready: list[bool] = []
        for i in range(1, expected + 1):
            recorded = item["segment_paths"][i - 1] if i - 1 < len(item["segment_paths"]) else None
            exists = bool(recorded and Path(recorded).exists()) or any(
                p.is_file() for p in candidate_video_paths(cfg().paths.flow_download_dir, item["id"], i)
            )
            ready.append(exists)
        item["segments_ready"] = ready
        item["output_exists"] = bool(item.get("output_path") and Path(item["output_path"]).exists())
        item["is_terminal"] = item["status"] in TERMINAL_STATUSES
        return item

    def _get_job_or_404(job_id: int) -> dict[str, Any]:
        job = db.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
        return job

    # ------------------------------------------------------------------ pages
    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return TEMPLATE_PATH.read_text(encoding="utf-8")

    # ------------------------------------------------------------------ jobs & stats
    @app.get("/api/jobs")
    def list_jobs(limit: int = Query(default=100, ge=1, le=1000)) -> list[dict[str, Any]]:
        return [_serialize_job(j) for j in db.list_jobs(limit)]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: int) -> dict[str, Any]:
        return _serialize_job(_get_job_or_404(job_id))

    @app.get("/api/stats")
    def get_stats() -> dict[str, int]:
        return db.get_stats()

    @app.get("/api/status")
    def get_status() -> dict[str, Any]:
        meta = db.all_meta()
        online = worker_online(meta)
        try:
            ffmpeg_path: str | None = resolve_ffmpeg(cfg().video)
        except FileNotFoundError:
            ffmpeg_path = None
        return {
            "version": __version__,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "worker": {
                "online": online,
                "paused": meta.get("worker_paused") == "1",
                "last_heartbeat": meta.get("worker_heartbeat"),
                "stopped_at": meta.get("worker_stopped_at"),
                "pid": meta.get("worker_pid"),
                "current_job": meta.get("worker_current_job") or None,
                "ai_provider": meta.get("worker_ai_provider"),
                "flow_provider": meta.get("worker_flow_provider"),
                "upload_mode": meta.get("worker_upload_mode"),
                "version": meta.get("worker_version"),
            },
            "config": {
                "flow_provider": cfg().flow.provider,
                "ai_provider": os.getenv("AI_PROVIDER", "mock").lower(),
                "mock_upload": env_flag("YOUTUBE_MOCK_UPLOAD", False),
                "timezone": cfg().schedule.timezone,
                "segments_per_video": cfg().segments_per_video,
                "segment_duration_seconds": cfg().segment_duration_seconds,
                "privacy_status": cfg().youtube.privacy_status,
            },
            "ffmpeg": {"available": ffmpeg_path is not None, "path": ffmpeg_path},
        }

    # ------------------------------------------------------------------ enqueue & schedule
    @app.post("/api/schedule/preview")
    def schedule_preview(req: ScheduleRequest) -> dict[str, Any]:
        sched = _schedule_from_request(cfg().schedule, req)
        try:
            times = build_upload_schedule(sched, req.count)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"times": times, "timezone": sched.timezone}

    @app.post("/api/enqueue")
    def enqueue(req: EnqueueRequest) -> dict[str, Any]:
        niche = req.niche.strip()
        if not niche:
            raise HTTPException(status_code=400, detail="Niche tidak boleh kosong.")
        sched = _schedule_from_request(cfg().schedule, req)
        try:
            times = build_upload_schedule(sched, req.count)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        job_ids = db.enqueue(niche, req.count, cfg().retry.max_attempts, times)
        log.info("Dashboard: %s job baru untuk niche '%s' -> %s", len(job_ids), niche, job_ids)
        return {"success": True, "job_ids": job_ids, "count": len(job_ids), "scheduled_times": times}

    # ------------------------------------------------------------------ job actions
    @app.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: int) -> dict[str, Any]:
        _get_job_or_404(job_id)
        db.retry_job(job_id)
        return {"success": True, "status": db.get(job_id)["status"]}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: int) -> dict[str, Any]:
        _get_job_or_404(job_id)
        if not db.cancel_job(job_id):
            raise HTTPException(status_code=409, detail="Job sudah selesai atau sudah dibatalkan.")
        return {"success": True}

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: int, purge: bool = Query(default=False)) -> dict[str, Any]:
        job = _get_job_or_404(job_id)
        removed: list[str] = []
        if purge:
            plan = _load_json(job.get("plan_json"), {}) or {}
            count = len(plan.get("segments") or []) or cfg().segments_per_video
            paths: list[Path] = [prompt_path(cfg().paths.prompt_dir, job_id, i) for i in range(1, count + 1)]
            for i in range(1, count + 1):
                paths.extend(candidate_video_paths(cfg().paths.flow_download_dir, job_id, i))
            paths.extend(Path(cfg().paths.prompt_dir).glob(f"job_{job_id}_segment_*.json"))
            if job.get("output_path"):
                paths.append(Path(job["output_path"]))
            for p in paths:
                try:
                    if p.is_file():
                        p.unlink()
                        removed.append(str(p))
                except OSError as e:
                    log.warning("Tidak bisa menghapus %s: %s", p, e)
        db.delete_job(job_id)
        return {"success": True, "removed_files": removed}

    @app.post("/api/jobs/{job_id}/upload-segment")
    def upload_segment(job_id: int, segment_index: int = Form(...), file: UploadFile = File(...)) -> dict[str, Any]:
        job = _get_job_or_404(job_id)
        plan = _load_json(job.get("plan_json"), {}) or {}
        expected = len(plan.get("segments") or []) or cfg().segments_per_video
        if not 1 <= segment_index <= expected:
            raise HTTPException(status_code=400, detail=f"segment_index harus 1..{expected}.")
        dest = expected_video_path(cfg().paths.flow_download_dir, job_id, segment_index)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        size = 0
        with open(tmp, "wb") as out:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                size += len(chunk)
        try:
            info = probe(tmp, resolve_ffmpeg(cfg().video))
            if not info.has_video:
                raise FFmpegError("tidak ada stream video")
        except (FFmpegError, FileNotFoundError) as e:
            tmp.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=f"File bukan video yang valid: {e}") from e
        os.replace(tmp, dest)  # rename atomik: worker tidak akan membaca file setengah jadi
        log.info("Dashboard: segmen %s job #%s diunggah (%s bytes, %.1fs)", segment_index, job_id, size, info.duration)
        return {"success": True, "saved_path": str(dest), "size_bytes": size, "duration": info.duration,
                "width": info.width, "height": info.height}

    @app.post("/api/jobs/{job_id}/simulate")
    def simulate_job(job_id: int) -> dict[str, Any]:
        job = _get_job_or_404(job_id)
        if not (job["status"] in SIMULATABLE_STATUSES or is_waiting_status(job["status"])):
            raise HTTPException(status_code=409, detail=f"Job berstatus '{job['status']}' tidak bisa disimulasikan.")
        plan = _load_json(job.get("plan_json"), {}) or {}
        segments = plan.get("segments") or []
        count = len(segments) or cfg().segments_per_video
        try:
            ffmpeg = resolve_ffmpeg(cfg().video)
        except FileNotFoundError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        created: list[str] = []
        for i in range(1, count + 1):
            if find_ready_video(cfg().paths.flow_download_dir, job_id, i, stable_seconds=0):
                continue
            seconds = (segments[i - 1].get("duration_seconds") if i - 1 < len(segments) else None) or cfg().segment_duration_seconds
            dest = expected_video_path(cfg().paths.flow_download_dir, job_id, i)
            try:
                make_test_clip(dest, seconds=float(seconds), hue_shift=(i - 1) * 90, with_audio=(i % 2 == 0), ffmpeg=ffmpeg)
            except FFmpegError as e:
                raise HTTPException(status_code=500, detail=f"FFmpeg gagal membuat klip uji: {e}") from e
            created.append(str(dest))
        return {
            "success": True,
            "created": created,
            "message": f"{len(created)} klip uji dibuat untuk {count} segmen. Worker akan menggabungkannya dengan FFmpeg.",
        }

    # ------------------------------------------------------------------ files
    @app.get("/api/jobs/{job_id}/video")
    def get_job_video(job_id: int) -> FileResponse:
        job = _get_job_or_404(job_id)
        if not job.get("output_path"):
            raise HTTPException(status_code=404, detail="Video belum tersedia.")
        path = Path(job["output_path"])
        if not path.exists():
            raise HTTPException(status_code=404, detail="File video tidak ditemukan di disk.")
        return FileResponse(str(path), media_type="video/mp4", filename=path.name)

    @app.get("/api/jobs/{job_id}/segments/{index}")
    def get_job_segment(job_id: int, index: int) -> FileResponse:
        _get_job_or_404(job_id)
        for candidate in candidate_video_paths(cfg().paths.flow_download_dir, job_id, index):
            if candidate.is_file():
                return FileResponse(str(candidate), media_type="video/mp4", filename=candidate.name)
        raise HTTPException(status_code=404, detail="Segmen belum tersedia.")

    @app.get("/api/logs", response_class=PlainTextResponse)
    def logs(source: Literal["worker", "dashboard", "cli"] = "worker", lines: int = Query(default=200, ge=10, le=2000)) -> str:
        path = Path(cfg().paths.log_dir) / f"{source}.log"
        if not path.exists():
            return f"Log '{source}' belum terbentuk. Jalankan worker: python -m yshorts_bot run"
        try:
            content = path.read_text(encoding="utf-8", errors="replace").splitlines()
            return "\n".join(content[-lines:])
        except OSError as e:
            return f"Error membaca log: {e}"

    # ------------------------------------------------------------------ config & worker control
    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        data = cfg().model_dump()
        data["_config_path"] = str(Path(config_path).resolve())
        return data

    @app.put("/api/config")
    def put_config(update: dict[str, Any]) -> dict[str, Any]:
        update.pop("_config_path", None)
        merged = _deep_merge(cfg().model_dump(), update)
        try:
            new_cfg = AppConfig(**merged)
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=_validation_message(e)) from e
        save_config(new_cfg, config_path)
        state["cfg"] = new_cfg
        log.info("Dashboard: config.json diperbarui (%s)", ", ".join(update.keys()))
        return {"success": True, "config": new_cfg.model_dump(),
                "note": "Worker memuat ulang config otomatis (kecuali paths.*, butuh restart)."}

    @app.post("/api/worker/pause")
    def pause_worker() -> dict[str, Any]:
        db.set_meta("worker_paused", "1")
        return {"success": True, "paused": True}

    @app.post("/api/worker/resume")
    def resume_worker() -> dict[str, Any]:
        db.set_meta("worker_paused", "0")
        return {"success": True, "paused": False}

    return app
