from __future__ import annotations

import csv
import io
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field, ValidationError

from . import __version__
from .ai.planner import build_metadata
from .config import AppConfig, ScheduleConfig, env_flag, load_config, save_config
from .db.queue import METADATA_EDITABLE_STATUSES, TERMINAL_STATUSES, QueueDB, is_waiting_status, utcnow
from .flow.prompt_files import (
    candidate_video_paths,
    expected_video_path,
    find_ready_video,
    is_video_file,
    job_files,
    prompt_path,
)
from .models import VideoPlan
from .notify import Notifier
from .scheduler.schedule import build_upload_schedule, resolve_timezone, to_utc_iso
from .video.ffmpeg import AUDIO_EXTENSIONS, FFmpegError, make_test_clip, probe, resolve_ffmpeg
from .worker import worker_online

log = logging.getLogger(__name__)

TEMPLATE_PATH = Path(__file__).parent / "templates" / "dashboard.html"
SIMULATABLE_STATUSES = {"queued", "planning"}
REGENERATE_PLAN_BLOCKED = {"uploading", "done"}


# ---------------------------------------------------------------------------
# Model permintaan
# ---------------------------------------------------------------------------
class ScheduleRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=100)
    mode: Literal["interval", "specific_times"] = "interval"
    interval_hours: float = Field(default=3, gt=0, le=744)
    start_time: str = "08:00"
    specific_times: list[str] = Field(default_factory=list)


class EnqueueRequest(ScheduleRequest):
    niche: str = Field(min_length=1, max_length=200)


class MetadataUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    hashtags: list[str] | str | None = None
    tags: list[str] | str | None = None


class ApproveRequest(BaseModel):
    upload_now: bool = False


class RescheduleRequest(BaseModel):
    scheduled_upload_at: str = Field(min_length=1, description="ISO 8601, 'YYYY-MM-DDTHH:MM' (zona config), atau 'now'")


class RegenerateRequest(BaseModel):
    target: Literal["plan", "metadata"] = "metadata"
    purge_segments: bool = False


class BulkRequest(BaseModel):
    action: Literal["retry_failed", "approve_all", "cancel_pending", "delete_finished"]
    purge: bool = True


# ---------------------------------------------------------------------------
# Util
# ---------------------------------------------------------------------------
def _deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _validation_message(error: ValidationError) -> str:
    parts = []
    for err in error.errors():
        loc = ".".join(str(x) for x in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
    return "; ".join(parts) or "Data tidak valid"


def _schedule_from_request(base: ScheduleConfig, req: ScheduleRequest) -> ScheduleConfig:
    data = base.model_dump()
    data.update({"mode": req.mode, "interval_hours": req.interval_hours, "start_time": req.start_time})
    if req.specific_times:
        data["specific_times"] = req.specific_times
    try:
        return ScheduleConfig(**data)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=_validation_message(e)) from e


def parse_schedule_input(value: str, tz_name: str) -> str:
    """'now' | ISO 8601 (dengan/tanpa zona) | 'YYYY-MM-DDTHH:MM' (dianggap zona config) -> ISO UTC."""
    raw = (value or "").strip()
    if not raw or raw.lower() in ("now", "sekarang"):
        return utcnow()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Format waktu tidak valid. Gunakan YYYY-MM-DDTHH:MM atau 'now'.") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=resolve_timezone(tz_name))
    return to_utc_iso(dt)


def _basic_auth_dependency():
    """Aktif bila DASHBOARD_USERNAME dan DASHBOARD_PASSWORD diset di .env (disarankan bila --host bukan 127.0.0.1)."""
    username = os.getenv("DASHBOARD_USERNAME", "")
    password = os.getenv("DASHBOARD_PASSWORD", "")
    if not (username and password):
        return None
    security = HTTPBasic(realm="YShorts Bot Studio")

    def verify(credentials: HTTPBasicCredentials = Depends(security)) -> None:
        ok_user = secrets.compare_digest(credentials.username.encode("utf-8"), username.encode("utf-8"))
        ok_pass = secrets.compare_digest(credentials.password.encode("utf-8"), password.encode("utf-8"))
        if not (ok_user and ok_pass):
            raise HTTPException(status_code=401, detail="Login dashboard salah.", headers={"WWW-Authenticate": "Basic"})

    return verify


# ---------------------------------------------------------------------------
# Aplikasi
# ---------------------------------------------------------------------------
def create_app(config_path: str = "config.json") -> FastAPI:
    state: dict[str, Any] = {"cfg": load_config(config_path)}

    def cfg() -> AppConfig:
        return state["cfg"]

    db = QueueDB(cfg().paths.db_path)
    db.init()
    app = FastAPI(title="YShorts Bot Studio", version=__version__)
    verify = _basic_auth_dependency()
    router = APIRouter(dependencies=[Depends(verify)] if verify else [])
    auth_enabled = verify is not None

    @app.middleware("http")
    async def same_origin_guard(request: Request, call_next):  # noqa: ANN001
        """Tolak permintaan yang mengubah data dari situs lain (CSRF): halaman web asing di browser
        yang sama tidak boleh bisa membatalkan job/menjeda worker di dashboard lokal."""
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            site = request.headers.get("sec-fetch-site")
            if site and site not in ("same-origin", "none"):
                return JSONResponse({"detail": "Permintaan lintas situs ditolak."}, status_code=403)
            origin = request.headers.get("origin") or request.headers.get("referer")
            if origin:
                host = (request.headers.get("host") or "").lower()
                if urlsplit(origin).netloc.lower() != host:
                    return JSONResponse({"detail": "Permintaan lintas situs ditolak."}, status_code=403)
        return await call_next(request)

    # ------------------------------------------------------------------ helpers
    def _load_json(raw: str | None, default: Any) -> Any:
        if not raw:
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return default

    def _existing_download_names() -> set[str]:
        base = Path(cfg().paths.flow_download_dir)
        if not base.is_dir():
            return set()
        return {p.name for p in base.iterdir() if p.is_file()}

    def _segment_exists(job_id: int, index: int, names: set[str], recorded: str | None) -> bool:
        if recorded and Path(recorded).exists():
            return True
        base = Path(cfg().paths.flow_download_dir)
        for p in candidate_video_paths(base, job_id, index):
            if p.parent == base:
                if p.name in names:
                    return True
            elif p.is_file():
                return True
        return False

    def _serialize_job(job: dict[str, Any], names: set[str] | None = None) -> dict[str, Any]:
        names = _existing_download_names() if names is None else names
        item = dict(job)
        plan = _load_json(item.get("plan_json"), None)
        item["plan"] = plan
        item["metadata"] = _load_json(item.get("metadata_json"), None)
        item["segment_paths"] = _load_json(item.get("segment_paths_json"), [])
        segments = (plan or {}).get("segments") or []
        expected = len(segments) or cfg().segments_per_video
        item["segments_ready"] = [
            _segment_exists(item["id"], i, names, item["segment_paths"][i - 1] if i - 1 < len(item["segment_paths"]) else None)
            for i in range(1, expected + 1)
        ]
        item["output_exists"] = bool(item.get("output_path") and Path(item["output_path"]).exists())
        item["is_terminal"] = item["status"] in TERMINAL_STATUSES
        item["metadata_editable"] = item["status"] in METADATA_EDITABLE_STATUSES and bool(item["metadata"])
        return item

    def _get_job_or_404(job_id: int) -> dict[str, Any]:
        job = db.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
        return job

    def _job_segment_count(job: dict[str, Any]) -> int:
        plan = _load_json(job.get("plan_json"), {}) or {}
        return len(plan.get("segments") or []) or cfg().segments_per_video

    def _remove_job_files(job: dict[str, Any], include_output: bool, include_prompts: bool = True) -> list[str]:
        removed: list[str] = []
        paths = job_files(cfg().paths.prompt_dir, cfg().paths.flow_download_dir, job["id"], _job_segment_count(job))
        if include_output and job.get("output_path"):
            paths.append(Path(job["output_path"]))
        for p in paths:
            if not include_prompts and p.suffix.lower() == ".txt":
                continue
            try:
                if p.is_file():
                    p.unlink()
                    removed.append(str(p))
            except OSError as e:
                log.warning("Tidak bisa menghapus %s: %s", p, e)
        return removed

    def _inbox_pending() -> int:
        base = Path(cfg().flow.inbox_dir)
        if not base.is_dir():
            return 0
        return sum(1 for p in base.iterdir() if is_video_file(p))

    def _music_files() -> int:
        base = Path(cfg().video.background_music_dir or "")
        if not cfg().video.background_music_dir or not base.is_dir():
            return 0
        return sum(1 for p in base.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS)

    # ------------------------------------------------------------------ publik (tanpa auth)
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "worker_online": worker_online(db.all_meta()), "time": utcnow()}

    # ------------------------------------------------------------------ halaman
    @router.get("/", response_class=HTMLResponse)
    def index() -> str:
        return TEMPLATE_PATH.read_text(encoding="utf-8")

    # ------------------------------------------------------------------ jobs & stats
    @router.get("/api/jobs")
    def list_jobs(limit: int = Query(default=200, ge=1, le=2000)) -> list[dict[str, Any]]:
        names = _existing_download_names()
        return [_serialize_job(j, names) for j in db.list_jobs(limit)]

    @router.get("/api/jobs/{job_id}")
    def get_job(job_id: int) -> dict[str, Any]:
        return _serialize_job(_get_job_or_404(job_id))

    @router.get("/api/stats")
    def get_stats() -> dict[str, int]:
        return db.get_stats()

    @router.get("/api/status")
    def get_status() -> dict[str, Any]:
        meta = db.all_meta()
        try:
            ffmpeg_path: str | None = resolve_ffmpeg(cfg().video)
        except FileNotFoundError:
            ffmpeg_path = None
        notifier = Notifier(cfg().notifications)
        return {
            "version": __version__,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "auth_enabled": auth_enabled,
            "worker": {
                "online": worker_online(meta),
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
                "require_approval": cfg().youtube.require_approval,
                "niche_presets": cfg().niche_presets,
            },
            "inbox": {
                "enabled": cfg().flow.inbox_auto_assign,
                "dir": str(Path(cfg().flow.inbox_dir).resolve()),
                "pending_files": _inbox_pending(),
            },
            "music": {"mode": cfg().video.background_music_mode, "dir": cfg().video.background_music_dir, "files": _music_files()},
            "notifications": {"enabled": notifier.enabled, "configured_channels": notifier.channels},
            "ffmpeg": {"available": ffmpeg_path is not None, "path": ffmpeg_path},
        }

    # ------------------------------------------------------------------ enqueue & jadwal
    @router.post("/api/schedule/preview")
    def schedule_preview(req: ScheduleRequest) -> dict[str, Any]:
        sched = _schedule_from_request(cfg().schedule, req)
        try:
            times = build_upload_schedule(sched, req.count)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"times": times, "timezone": sched.timezone}

    @router.post("/api/enqueue")
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

    # ------------------------------------------------------------------ aksi job
    @router.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: int) -> dict[str, Any]:
        _get_job_or_404(job_id)
        db.retry_job(job_id)
        return {"success": True, "status": db.get(job_id)["status"]}

    @router.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: int) -> dict[str, Any]:
        _get_job_or_404(job_id)
        if not db.cancel_job(job_id):
            raise HTTPException(status_code=409, detail="Job sudah selesai atau sudah dibatalkan.")
        return {"success": True}

    @router.post("/api/jobs/{job_id}/approve")
    def approve_job(job_id: int, req: ApproveRequest | None = None) -> dict[str, Any]:
        job = _get_job_or_404(job_id)
        if job["status"] != "awaiting_approval":
            raise HTTPException(status_code=409, detail=f"Job berstatus '{job['status']}', bukan menunggu persetujuan.")
        db.approve_job(job_id, upload_now=bool(req and req.upload_now))
        job = db.get(job_id)
        return {"success": True, "status": job["status"], "scheduled_upload_at": job["scheduled_upload_at"]}

    @router.post("/api/jobs/{job_id}/schedule")
    def reschedule_job(job_id: int, req: RescheduleRequest) -> dict[str, Any]:
        job = _get_job_or_404(job_id)
        if job["status"] in ("uploading", "done"):
            raise HTTPException(status_code=409, detail="Job sedang/sudah diupload, jadwal tidak bisa diubah.")
        when = parse_schedule_input(req.scheduled_upload_at, cfg().schedule.timezone)
        db.reschedule_job(job_id, when)
        return {"success": True, "scheduled_upload_at": when, "status": db.get(job_id)["status"]}

    @router.put("/api/jobs/{job_id}/metadata")
    def update_metadata(job_id: int, req: MetadataUpdate) -> dict[str, Any]:
        job = _get_job_or_404(job_id)
        if job["status"] == "uploading" or job["status"] == "done":
            raise HTTPException(status_code=409, detail="Metadata tidak bisa diubah saat/setelah upload.")
        current = _load_json(job.get("metadata_json"), {}) or {}
        if not current and job["status"] not in METADATA_EDITABLE_STATUSES:
            raise HTTPException(status_code=409, detail="Metadata belum dibuat oleh worker.")
        plan = VideoPlan.from_dict(_load_json(job.get("plan_json"), {}) or {})
        merged = dict(current)
        for key, value in req.model_dump(exclude_none=True).items():
            merged[key] = value
        meta = build_metadata(merged, plan.idea or job["niche"], plan.hook or job["niche"], job["niche"])
        db.update(job_id, metadata_json=meta.to_dict())
        return {"success": True, "metadata": meta.to_dict()}

    @router.post("/api/jobs/{job_id}/regenerate")
    def regenerate_job(job_id: int, req: RegenerateRequest) -> dict[str, Any]:
        job = _get_job_or_404(job_id)
        if job["status"] in REGENERATE_PLAN_BLOCKED:
            raise HTTPException(status_code=409, detail="Job sedang/sudah diupload.")
        if req.target == "plan":
            removed = _remove_job_files(job, include_output=False, include_prompts=False) if req.purge_segments else []
            db.update(
                job_id, status="queued", next_run_at=utcnow(), attempts=0, last_error=None, failed_from_status=None,
                flow_waiting_since=None, segment_paths_json=[], metadata_json=None, output_path=None,
            )
            return {"success": True, "status": "queued", "removed_files": removed}
        if not job.get("output_path"):
            raise HTTPException(status_code=409, detail="Video belum digabung; metadata dibuat setelah tahap merge.")
        db.update(job_id, status="metadata", next_run_at=utcnow(), attempts=0, last_error=None, failed_from_status=None)
        return {"success": True, "status": "metadata", "removed_files": []}

    @router.delete("/api/jobs/{job_id}")
    def delete_job(job_id: int, purge: bool = Query(default=False)) -> dict[str, Any]:
        job = _get_job_or_404(job_id)
        removed = _remove_job_files(job, include_output=True) if purge else []
        db.delete_job(job_id)
        return {"success": True, "removed_files": removed}

    @router.post("/api/jobs/bulk")
    def bulk_action(req: BulkRequest) -> dict[str, Any]:
        if req.action == "retry_failed":
            ids = db.bulk_retry_failed()
        elif req.action == "approve_all":
            ids = db.bulk_approve()
        elif req.action == "cancel_pending":
            ids = db.bulk_cancel_pending()
        else:  # delete_finished
            ids = []
            for job in db.list_by_status(TERMINAL_STATUSES):
                if req.purge:
                    _remove_job_files(job, include_output=True)
                db.delete_job(job["id"])
                ids.append(job["id"])
        log.info("Dashboard: aksi massal %s -> %s job", req.action, len(ids))
        return {"success": True, "action": req.action, "job_ids": ids, "count": len(ids)}

    @router.post("/api/jobs/{job_id}/upload-segment")
    def upload_segment(job_id: int, segment_index: int = Form(...), file: UploadFile = File(...)) -> dict[str, Any]:
        job = _get_job_or_404(job_id)
        expected = _job_segment_count(job)
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
        if is_waiting_status(job["status"]):
            db.update(job_id, next_run_at=utcnow())  # proses segera
        log.info("Dashboard: segmen %s job #%s diunggah (%s bytes, %.1fs)", segment_index, job_id, size, info.duration)
        return {"success": True, "saved_path": str(dest), "size_bytes": size, "duration": info.duration,
                "width": info.width, "height": info.height}

    @router.post("/api/jobs/{job_id}/simulate")
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
        if is_waiting_status(job["status"]):
            db.update(job_id, next_run_at=utcnow())
        return {
            "success": True,
            "created": created,
            "message": f"{len(created)} klip uji dibuat untuk {count} segmen. Worker akan menggabungkannya dengan FFmpeg.",
        }

    # ------------------------------------------------------------------ file
    @router.get("/api/jobs/{job_id}/video")
    def get_job_video(job_id: int) -> FileResponse:
        job = _get_job_or_404(job_id)
        if not job.get("output_path"):
            raise HTTPException(status_code=404, detail="Video belum tersedia.")
        path = Path(job["output_path"])
        if not path.exists():
            raise HTTPException(status_code=404, detail="File video tidak ditemukan di disk.")
        return FileResponse(str(path), media_type="video/mp4", filename=path.name)

    @router.get("/api/jobs/{job_id}/segments/{index}")
    def get_job_segment(job_id: int, index: int) -> FileResponse:
        _get_job_or_404(job_id)
        for candidate in candidate_video_paths(cfg().paths.flow_download_dir, job_id, index):
            if candidate.is_file():
                return FileResponse(str(candidate), media_type="video/mp4", filename=candidate.name)
        raise HTTPException(status_code=404, detail="Segmen belum tersedia.")

    @router.get("/api/jobs/{job_id}/prompt/{index}", response_class=PlainTextResponse)
    def get_job_prompt(job_id: int, index: int) -> str:
        _get_job_or_404(job_id)
        path = prompt_path(cfg().paths.prompt_dir, job_id, index)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="File prompt belum ada.")
        return path.read_text(encoding="utf-8")

    @router.get("/api/export.csv")
    def export_csv() -> Response:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([
            "id", "status", "niche", "idea", "title", "youtube_url", "scheduled_upload_at", "uploaded_at",
            "created_at", "attempts", "output_path", "last_error",
        ])
        for job in reversed(db.list_jobs(100000)):
            meta = _load_json(job.get("metadata_json"), {}) or {}
            vid = job.get("youtube_video_id") or ""
            url = f"https://youtube.com/shorts/{vid}" if vid and not vid.startswith("demo_") else ""
            writer.writerow([
                job["id"], job["status"], job["niche"], job.get("idea") or "", meta.get("title", ""), url,
                job.get("scheduled_upload_at") or "", job.get("uploaded_at") or "", job.get("created_at") or "",
                job.get("attempts"), job.get("output_path") or "", (job.get("last_error") or "")[:500],
            ])
        filename = f"yshorts_jobs_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        return Response(
            content="﻿" + buffer.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/api/logs", response_class=PlainTextResponse)
    def logs(source: Literal["worker", "dashboard", "cli"] = "worker", lines: int = Query(default=200, ge=10, le=5000)) -> str:
        path = Path(cfg().paths.log_dir) / f"{source}.log"
        if not path.exists():
            return f"Log '{source}' belum terbentuk. Jalankan worker: python -m yshorts_bot run"
        try:
            content = path.read_text(encoding="utf-8", errors="replace").splitlines()
            return "\n".join(content[-lines:])
        except OSError as e:
            return f"Error membaca log: {e}"

    # ------------------------------------------------------------------ config, notifikasi & kontrol worker
    @router.get("/api/config")
    def get_config() -> dict[str, Any]:
        data = cfg().model_dump()
        data["_config_path"] = str(Path(config_path).resolve())
        return data

    @router.put("/api/config")
    def put_config(update: dict[str, Any]) -> dict[str, Any]:
        update.pop("_config_path", None)
        if "paths" in update:
            raise HTTPException(status_code=400, detail="paths.* hanya bisa diubah lewat file config.json (butuh restart).")
        if isinstance(update.get("video"), dict) and "ffmpeg_binary" in update["video"]:
            raise HTTPException(status_code=400, detail="video.ffmpeg_binary hanya bisa diubah lewat file config.json.")
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

    @router.post("/api/notify/test")
    def notify_test() -> dict[str, Any]:
        notifier = Notifier(cfg().notifications)
        if not notifier.channels:
            raise HTTPException(status_code=400, detail="Belum ada kanal: isi TELEGRAM_BOT_TOKEN+TELEGRAM_CHAT_ID atau NOTIFY_WEBHOOK_URL di .env (lalu restart dashboard).")
        results = notifier.send_sync("[YShorts] Tes notifikasi dari dashboard berhasil.", {"event": "test"})
        return {"success": all(v == "ok" for v in results.values()), "results": results, "enabled_in_config": cfg().notifications.enabled}

    @router.post("/api/worker/pause")
    def pause_worker() -> dict[str, Any]:
        db.set_meta("worker_paused", "1")
        return {"success": True, "paused": True}

    @router.post("/api/worker/resume")
    def resume_worker() -> dict[str, Any]:
        db.set_meta("worker_paused", "0")
        return {"success": True, "paused": False}

    app.include_router(router)
    return app
