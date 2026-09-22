from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .ai.planner import create_metadata, create_video_plan
from .ai.provider import build_provider
from .config import AppConfig
from .db.queue import QueueDB
from .flow.factory import build_flow_provider
from .models import Metadata, SegmentPrompt, VideoPlan
from .video.ffmpeg import merge_for_shorts
from .youtube.uploader import YouTubeUploader

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.db = QueueDB(cfg.paths.db_path)
        self.ai = build_provider()
        self.flow = build_flow_provider(cfg)
        self.uploader = YouTubeUploader()

    def run_forever(self, sleep_seconds: int = 5) -> None:
        self.db.init()
        log.info("Worker berjalan. Tekan Ctrl+C untuk berhenti.")
        while True:
            job = self.db.next_job()
            if not job:
                time.sleep(sleep_seconds)
                continue
            self.process_job(job["id"])

    def process_job(self, job_id: int) -> None:
        job = self.db.get(job_id)
        if not job:
            return
        try:
            log.info("Memproses job #%s status=%s niche=%s", job_id, job["status"], job["niche"])
            status = job["status"]
            if status == "queued":
                self._plan(job)
            elif status.startswith("waiting_flow_segment_"):
                self._flow_segment(job)
            elif status == "merging":
                self._merge(job)
            elif status == "metadata":
                self._metadata(job)
            elif status == "scheduled":
                self.db.update(job_id, status="uploading")
            elif status == "uploading":
                self._upload(job)
            elif status == "planning":
                self._plan(job)
            else:
                log.warning("Status tidak dikenal atau terminal: %s", status)
        except Exception as e:
            log.exception("Job #%s gagal sementara", job_id)
            self._handle_failure(job_id, str(e))

    def _plan(self, job: dict) -> None:
        self.db.update(job["id"], status="planning")
        plan = create_video_plan(self.ai, job["niche"], self.cfg.segments_per_video, self.cfg.segment_duration_seconds)
        
        # Simpan file prompt semua segmen sekaligus agar user bisa langsung copy
        prompt_dir = Path(self.cfg.paths.prompt_dir)
        prompt_dir.mkdir(parents=True, exist_ok=True)
        download_dir = Path(self.cfg.paths.flow_download_dir)
        download_dir.mkdir(parents=True, exist_ok=True)
        
        for seg in plan.segments:
            prompt_path = prompt_dir / f"job_{job['id']}_segment_{seg.index}.txt"
            video_target = download_dir / f"job_{job['id']}_segment_{seg.index}.mp4"
            prompt_path.write_text(
                f"=== GOOGLE FLOW PROMPT - JOB #{job['id']} SEGMENT {seg.index} ===\n"
                f"Niche: {job['niche']}\n"
                f"Durasi Target: {seg.duration_seconds} detik (Vertical 9:16)\n\n"
                f"--- PROMPT (Salin ke Google Flow Ultra) ---\n"
                f"{seg.prompt}\n\n"
                f"--- INSTRUKSI ---\n"
                f"1. Buka Google Flow Ultra dan paste prompt di atas.\n"
                f"2. Download video MP4 (durasi ±{seg.duration_seconds} detik).\n"
                f"3. Simpan dengan nama:\n"
                f"   {video_target.resolve()}\n"
                f"   (atau gunakan tombol Kirim Video di Web Dashboard)\n",
                encoding="utf-8",
            )
            log.info("Prompt job #%s segmen %s siap: %s", job["id"], seg.index, prompt_path)

        self.db.update(job["id"], idea=plan.idea, plan_json=plan_to_json(plan), status="waiting_flow_segment_1")


    def _flow_segment(self, job: dict) -> None:
        plan = json_to_plan(job["plan_json"])
        segment_number = int(job["status"].split("_")[-1])
        segment = next(s for s in plan.segments if s.index == segment_number)
        video_path = self.flow.request_segment(job["id"], job["niche"], segment)
        paths = json.loads(job["segment_paths_json"] or "[]")
        while len(paths) < segment_number:
            paths.append(None)
        paths[segment_number - 1] = str(video_path)
        next_status = f"waiting_flow_segment_{segment_number + 1}" if segment_number < len(plan.segments) else "merging"
        self.db.update(job["id"], segment_paths_json=paths, status=next_status)

    def _merge(self, job: dict) -> None:
        self.db.update(job["id"], status="merging")
        paths = json.loads(job["segment_paths_json"] or "[]")
        if not paths or any(not p for p in paths):
            raise RuntimeError("Belum semua segmen video tersedia.")
        out = Path(self.cfg.paths.output_dir) / f"short_job_{job['id']}.mp4"
        output = merge_for_shorts(paths, out, self.cfg.video)
        self.db.update(job["id"], output_path=str(output), status="metadata")

    def _metadata(self, job: dict) -> None:
        plan = json_to_plan(job["plan_json"])
        meta = create_metadata(self.ai, job["niche"], plan)
        scheduled_upload_at = job.get("scheduled_upload_at")
        self.db.update(
            job["id"],
            metadata_json=meta_to_json(meta),
            status="scheduled",
            next_run_at=scheduled_upload_at,
        )

    def _upload(self, job: dict) -> None:
        if not job["output_path"]:
            raise RuntimeError("output_path kosong")
        meta = json_to_meta(job["metadata_json"])
        video_id = self.uploader.upload(
            job["output_path"], meta,
            privacy_status=self.cfg.youtube.privacy_status,
            made_for_kids=self.cfg.youtube.made_for_kids,
            category_id=self.cfg.youtube.category_id,
        )
        self.db.update(job["id"], youtube_video_id=video_id, status="done")

    def _handle_failure(self, job_id: int, error: str) -> None:
        job = self.db.get(job_id)
        attempts = int(job["attempts"] if job else 0) + 1
        max_attempts = int(job["max_attempts"] if job else self.cfg.retry.max_attempts)
        if attempts >= max_attempts:
            self.db.update(job_id, status="failed", last_error=error)
            return
        delay = self.cfg.retry.base_delay_seconds * (2 ** (attempts - 1))
        next_run = (datetime.now(timezone.utc) + timedelta(seconds=delay)).replace(microsecond=0).isoformat()
        self.db.increment_attempt(job_id, error, next_run_at=next_run)


def plan_to_json(plan: VideoPlan) -> str:
    return json.dumps({
        "idea": plan.idea,
        "style": plan.style,
        "hook": plan.hook,
        "segments": [{"index": s.index, "prompt": s.prompt, "duration_seconds": s.duration_seconds} for s in plan.segments]
    }, ensure_ascii=False)


def json_to_plan(s: str) -> VideoPlan:
    data = json.loads(s)
    return VideoPlan(
        idea=data["idea"], style=data["style"], hook=data["hook"],
        segments=[SegmentPrompt(index=x["index"], prompt=x["prompt"], duration_seconds=x.get("duration_seconds", 8)) for x in data["segments"]]
    )


def meta_to_json(meta: Metadata) -> str:
    return json.dumps({"title": meta.title, "description": meta.description, "hashtags": meta.hashtags, "tags": meta.tags}, ensure_ascii=False)


def json_to_meta(s: str) -> Metadata:
    data = json.loads(s)
    return Metadata(title=data["title"], description=data["description"], hashtags=data["hashtags"], tags=data["tags"])
