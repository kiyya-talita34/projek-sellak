from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .ai.planner import create_metadata, create_video_plan
from .ai.provider import AIProvider, build_provider
from .config import AppConfig, load_config
from .db.queue import (
    TERMINAL_STATUSES,
    QueueDB,
    is_waiting_status,
    utcnow,
    waiting_segment_index,
    waiting_status,
)
from .errors import ConfigError, NonRetryableError, RetryLaterError
from .flow.base import FlowProvider
from .flow.factory import build_flow_provider
from .flow.prompt_files import (
    VIDEO_EXTENSIONS,
    candidate_video_paths,
    expected_video_path,
    forget_file,
    job_files,
    list_inbox_files,
    write_prompt_file,
)
from .models import Metadata, VideoPlan
from .notify import Notifier
from .video.ffmpeg import merge_for_shorts, replace_with_retry
from .youtube.uploader import YouTubeUploader

log = logging.getLogger(__name__)

HEARTBEAT_STALE_SECONDS = 90


def iso_in(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def pid_alive(pid: int) -> bool:
    """Cek apakah proses dengan PID tersebut masih hidup (Windows & POSIX, tanpa dependensi tambahan)."""
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - khusus Windows
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        error_access_denied = 5
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            # Proses ada tetapi milik user/elevasi lain -> anggap hidup (konsisten dengan PermissionError di POSIX)
            return ctypes.get_last_error() == error_access_denied
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def worker_online(meta: dict[str, str]) -> bool:
    """Worker dianggap online bila heartbeat masih segar DAN prosesnya masih hidup (mesin yang sama)."""
    heartbeat = parse_iso(meta.get("worker_heartbeat"))
    if not heartbeat:
        return False
    if (datetime.now(timezone.utc) - heartbeat).total_seconds() >= HEARTBEAT_STALE_SECONDS:
        return False
    pid = (meta.get("worker_pid") or "").strip()
    if pid.isdigit() and not pid_alive(int(pid)):
        return False
    return True


class Worker:
    """Orkestrator antrean: satu job diproses per tahap (state machine), non-blocking saat menunggu video."""

    def __init__(
        self,
        cfg: AppConfig,
        config_path: str | None = None,
        ai: AIProvider | None = None,
        flow: FlowProvider | None = None,
        uploader: YouTubeUploader | None = None,
        notifier: Notifier | None = None,
    ):
        self.cfg = cfg
        self.config_path = Path(config_path) if config_path else None
        self._config_mtime = self._stat_config()
        self.db = QueueDB(cfg.paths.db_path)
        self.db.init()
        self.ai = ai or build_provider()
        self.flow = flow or build_flow_provider(cfg)
        self.uploader = uploader or YouTubeUploader(playlist_id=cfg.youtube.playlist_id)
        self.notifier = notifier or Notifier(cfg.notifications)
        self._running = False
        self._last_heartbeat = 0.0
        self._current_job: int | None = None
        self._paused_logged = False
        self._inbox_idle_logged = False

    # ------------------------------------------------------------------ lifecycle
    def run_forever(self) -> None:
        self._ensure_single_instance()
        self._install_signal_handlers()
        self._running = True
        self._heartbeat(force=True)
        threading.Thread(target=self._heartbeat_loop, name="heartbeat", daemon=True).start()
        log.info(
            "Worker v%s berjalan | AI=%s | Flow=%s | Upload=%s | Notif=%s | DB=%s | Ctrl+C untuk berhenti",
            __version__, self.ai.name, self.flow.name, "MOCK" if self.uploader.mock else "YouTube API",
            ",".join(self.notifier.channels) if self.notifier.enabled else "off", self.cfg.paths.db_path,
        )
        try:
            while self._running:
                self._maybe_reload_config()
                if self._is_paused():
                    time.sleep(self.cfg.worker.idle_sleep_seconds)
                    continue
                self._safe(self._process_inbox, "inbox")
                job = self.db.next_job()
                if not job:
                    time.sleep(self.cfg.worker.idle_sleep_seconds)
                    continue
                self.process_job(job["id"])
        except KeyboardInterrupt:
            log.info("Worker dihentikan oleh pengguna (Ctrl+C). Progres tersimpan di database.")
        finally:
            self._running = False
            self._heartbeat(final=True)

    def stop(self) -> None:
        self._running = False

    @staticmethod
    def _safe(fn, label: str) -> None:
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - tugas sampingan tidak boleh mematikan worker
            log.warning("Tugas %s gagal: %s", label, e)

    def _stat_config(self) -> float | None:
        try:
            return self.config_path.stat().st_mtime if self.config_path else None
        except OSError:
            return None

    def _ensure_single_instance(self) -> None:
        meta = self.db.all_meta()
        pid = meta.get("worker_pid")
        if pid and pid != str(os.getpid()) and worker_online(meta):
            raise SystemExit(
                f"Worker lain (PID {pid}) masih aktif (heartbeat {meta.get('worker_heartbeat')}). "
                "Jalankan hanya satu worker per database."
            )

    @staticmethod
    def _install_signal_handlers() -> None:
        """SIGTERM/SIGBREAK/SIGHUP diperlakukan seperti Ctrl+C agar heartbeat dibersihkan saat berhenti."""

        def _stop(signum, frame):  # noqa: ANN001
            raise KeyboardInterrupt

        for name in ("SIGTERM", "SIGBREAK", "SIGHUP"):
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, _stop)
            except (ValueError, OSError):  # bukan main thread / tidak didukung
                pass

    def _heartbeat_loop(self) -> None:
        """Detak jantung di thread terpisah supaya tetap berdetak selama tahap panjang (FFmpeg, upload, browser)."""
        while self._running:
            time.sleep(self.cfg.worker.heartbeat_seconds)
            if self._running:
                self._heartbeat(force=True)

    def _heartbeat(self, final: bool = False, force: bool = False) -> None:
        now = time.time()
        if not final and not force and now - self._last_heartbeat < self.cfg.worker.heartbeat_seconds:
            return
        self._last_heartbeat = now
        try:
            if final:
                self.db.delete_meta("worker_heartbeat")
                self.db.set_meta("worker_stopped_at", utcnow())
                self.db.set_meta("worker_current_job", "")
                return
            self.db.set_meta("worker_heartbeat", utcnow())
            self.db.set_meta("worker_pid", str(os.getpid()))
            self.db.set_meta("worker_version", __version__)
            self.db.set_meta("worker_ai_provider", self.ai.name)
            self.db.set_meta("worker_flow_provider", self.flow.name)
            self.db.set_meta("worker_upload_mode", "mock" if self.uploader.mock else "youtube")
            self.db.set_meta("worker_current_job", str(self._current_job or ""))
        except Exception as e:  # noqa: BLE001 - heartbeat tidak boleh mematikan worker
            log.debug("Heartbeat gagal: %s", e)

    def _is_paused(self) -> bool:
        paused = self.db.get_meta("worker_paused") == "1"
        if paused and not self._paused_logged:
            log.info("Worker DIJEDA dari dashboard. Job tidak diproses sampai dilanjutkan.")
        elif not paused and self._paused_logged:
            log.info("Worker dilanjutkan.")
        self._paused_logged = paused
        return paused

    def _maybe_reload_config(self) -> None:
        if not self.config_path or not self.cfg.worker.reload_config:
            return
        mtime = self._stat_config()
        if mtime is None or mtime == self._config_mtime:
            return
        self._config_mtime = mtime
        try:
            new_cfg = load_config(str(self.config_path), create_if_missing=False)
        except ConfigError as e:
            log.error("config.json berubah tetapi tidak valid, tetap memakai konfigurasi lama: %s", e)
            return
        if new_cfg.flow != self.cfg.flow or new_cfg.paths != self.cfg.paths:
            try:
                self.flow = build_flow_provider(new_cfg)
                log.info("Flow provider dibangun ulang ('%s') karena pengaturan flow/paths berubah.", self.flow.name)
            except Exception as e:  # noqa: BLE001
                log.error("Gagal membangun ulang flow provider '%s': %s", new_cfg.flow.provider, e)
                return
        if new_cfg.youtube.playlist_id != self.cfg.youtube.playlist_id:
            self.uploader.playlist_id = new_cfg.youtube.playlist_id
        self.notifier.cfg = new_cfg.notifications
        self.cfg = new_cfg
        log.info("config.json berubah -> konfigurasi dimuat ulang.")

    # ------------------------------------------------------------------ inbox
    def _process_inbox(self) -> None:
        """Pasangkan file video di folder inbox ke job/segmen yang sedang menunggu (urut job, lalu segmen)."""
        if not self.cfg.flow.inbox_auto_assign:
            return
        files = list_inbox_files(self.cfg.flow.inbox_dir, self.cfg.flow.min_video_bytes, self.cfg.flow.stable_seconds)
        if not files:
            self._inbox_idle_logged = False
            return
        waiting = self.db.list_by_status(set(), prefix="waiting_flow_segment_")
        download_dir = self.cfg.paths.flow_download_dir
        for source in files:
            target: tuple[dict[str, Any], int, Path] | None = None
            for job in waiting:
                plan = VideoPlan.from_dict(json.loads(job.get("plan_json") or "{}"))
                count = len(plan.segments) or self.cfg.segments_per_video
                for index in range(waiting_segment_index(job["status"]), count + 1):
                    if not any(p.exists() for p in candidate_video_paths(download_dir, job["id"], index)):
                        target = (job, index, expected_video_path(download_dir, job["id"], index))
                        break
                if target:
                    break
            if not target:
                if not self._inbox_idle_logged:
                    log.info("Inbox: %s file menunggu tetapi tidak ada job yang membutuhkan segmen.", len(files))
                    self._inbox_idle_logged = True
                return
            job, index, dest = target
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                replace_with_retry(source, dest)
            except OSError:
                shutil.move(str(source), str(dest))  # beda drive/partisi
            forget_file(source)
            log.info("Inbox: %s -> job #%s segmen %s (%s)", source.name, job["id"], index, dest.name)
            self.db.update(job["id"], next_run_at=utcnow())

    # ------------------------------------------------------------------ dispatch
    def process_job(self, job_id: int) -> None:
        job = self.db.get(job_id)
        if not job or job["status"] in TERMINAL_STATUSES:
            return
        status = job["status"]
        self._current_job = job_id
        try:
            if status in ("queued", "planning"):
                self._plan(job)
            elif is_waiting_status(status):
                self._flow_segment(job)
            elif status == "merging":
                self._merge(job)
            elif status == "metadata":
                self._metadata(job)
            elif status == "awaiting_approval":
                log.info("Job #%s menunggu persetujuan di dashboard.", job_id)
            elif status == "scheduled":
                self.db.update(job_id, expected_status="scheduled", status="uploading", next_run_at=utcnow())
            elif status == "uploading":
                self._upload(job)
            else:
                log.error("Job #%s memiliki status tidak dikenal '%s'.", job_id, status)
                self.db.mark_failed(job_id, f"Status tidak dikenal: {status}")
        except (RetryLaterError, NonRetryableError) as e:
            self._handle_failure(job_id, e)
        except Exception as e:  # noqa: BLE001
            log.exception("Job #%s gagal pada tahap '%s'", job_id, status)
            self._handle_failure(job_id, e)
        finally:
            self._current_job = None

    # ------------------------------------------------------------------ tahapan
    def _plan(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        log.info("Job #%s: AI menyusun ide & %s prompt untuk niche '%s'...", job_id, self.cfg.segments_per_video, job["niche"])
        if not self.db.update(job_id, expected_status=job["status"], status="planning"):
            return  # status berubah (dibatalkan) sebelum mulai
        plan = create_video_plan(
            self.ai, job["niche"], self.cfg.segments_per_video, self.cfg.segment_duration_seconds, self.cfg.ai
        )
        for seg in plan.segments:
            write_prompt_file(
                self.cfg.paths.prompt_dir, self.cfg.paths.flow_download_dir, job_id, job["niche"], seg,
                overwrite=True, inbox_dir=self.cfg.flow.inbox_dir if self.cfg.flow.inbox_auto_assign else None,
            )
            if expected_video_path(self.cfg.paths.flow_download_dir, job_id, seg.index).exists():
                log.warning("Job #%s: file segmen %s sudah ada dan akan dipakai apa adanya.", job_id, seg.index)
        now = utcnow()
        updated = self.db.update(
            job_id,
            expected_status="planning",
            idea=plan.idea,
            plan_json=plan.to_dict(),
            segment_paths_json=[],
            status=waiting_status(1),
            flow_waiting_since=now,
            next_run_at=now,
        )
        if not updated:
            log.info("Job #%s berubah status saat AI menyusun rencana (dibatalkan/di-retry); hasil diabaikan.", job_id)
            return
        log.info("Job #%s: ide '%s' | %s segmen x %ss. Menunggu video segmen 1 dari Flow (%s).", job_id, plan.idea, len(plan.segments), self.cfg.segment_duration_seconds, self.flow.name)

    def _flow_segment(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        plan = VideoPlan.from_dict(json.loads(job["plan_json"] or "{}"))
        if not plan.segments:
            log.warning("Job #%s: rencana kosong, menyusun ulang.", job_id)
            self.db.update(job_id, status="queued", next_run_at=utcnow())
            return
        index = waiting_segment_index(job["status"])
        if index > len(plan.segments):
            self.db.update(job_id, status="merging", flow_waiting_since=None, next_run_at=utcnow())
            return
        segment = next((s for s in plan.segments if s.index == index), plan.segments[index - 1])
        since = job.get("flow_waiting_since")
        if not since:
            since = utcnow()
            self.db.update(job_id, flow_waiting_since=since)

        video = self.flow.request_segment(job_id, job["niche"], segment)
        if video is None:
            started = parse_iso(since)
            waited = (datetime.now(timezone.utc) - started).total_seconds() if started else 0.0
            limit = self.cfg.flow.wait_timeout_minutes * 60
            if waited > limit:
                raise NonRetryableError(
                    f"Video segmen {index} belum tersedia setelah {waited / 60:.0f} menit "
                    f"(batas flow.wait_timeout_minutes={self.cfg.flow.wait_timeout_minutes}). "
                    "Unggah video lalu klik Retry."
                )
            poll = self.cfg.flow.veo.poll_seconds if self.flow.name == "veo_api" else self.cfg.flow.poll_seconds
            self.db.update(job_id, expected_status=job["status"], next_run_at=iso_in(poll))
            return

        paths = json.loads(job["segment_paths_json"] or "[]")
        while len(paths) < index:
            paths.append(None)
        paths[index - 1] = str(video)
        if index < len(plan.segments):
            next_status, since_next = waiting_status(index + 1), utcnow()
        else:
            next_status, since_next = "merging", None
        updated = self.db.update(
            job_id, expected_status=job["status"], segment_paths_json=paths, status=next_status,
            flow_waiting_since=since_next, next_run_at=utcnow(),
        )
        if not updated:
            log.info("Job #%s berubah status saat menunggu Flow; transisi diabaikan.", job_id)
            return
        log.info("Job #%s: segmen %s/%s siap (%s) -> %s", job_id, index, len(plan.segments), Path(str(video)).name, next_status)

    def _merge(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        paths = json.loads(job["segment_paths_json"] or "[]")
        plan = VideoPlan.from_dict(json.loads(job["plan_json"] or "{}"))
        expected = len(plan.segments) or self.cfg.segments_per_video
        for i in range(expected):
            path = paths[i] if i < len(paths) else None
            if not path or not Path(path).exists():
                log.warning("Job #%s: segmen %s hilang, kembali menunggu Flow.", job_id, i + 1)
                self.db.update(job_id, status=waiting_status(i + 1), flow_waiting_since=utcnow(), next_run_at=utcnow())
                return
        log.info("Job #%s: menggabungkan %s segmen dengan FFmpeg...", job_id, expected)
        out = Path(self.cfg.paths.output_dir) / f"short_job_{job_id}.mp4"
        output = merge_for_shorts(paths[:expected], out, self.cfg.video)
        if not self.db.update(job_id, expected_status="merging", output_path=str(output), status="metadata", next_run_at=utcnow()):
            log.info("Job #%s berubah status saat FFmpeg berjalan; hasil merge disimpan di %s tetapi status tidak diubah.", job_id, output)

    def _metadata(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        plan = VideoPlan.from_dict(json.loads(job["plan_json"] or "{}"))
        log.info("Job #%s: AI menyusun judul, deskripsi, hashtag...", job_id)
        meta = create_metadata(self.ai, job["niche"], plan, self.cfg.ai)
        scheduled = job.get("scheduled_upload_at") or utcnow()
        if self.cfg.youtube.require_approval:
            if not self.db.update(job_id, expected_status="metadata", metadata_json=meta.to_dict(), status="awaiting_approval", next_run_at=None):
                return
            log.info("Job #%s: '%s' siap, MENUNGGU PERSETUJUAN di dashboard (jadwal %s UTC).", job_id, meta.title, scheduled)
            self.notifier.notify_job("awaiting_approval", self.db.get(job_id) or job, "Tinjau judul/deskripsi lalu setujui untuk upload.")
            return
        if not self.db.update(job_id, expected_status="metadata", metadata_json=meta.to_dict(), status="scheduled", next_run_at=scheduled):
            return
        log.info("Job #%s: '%s' siap. Upload dijadwalkan %s (UTC).", job_id, meta.title, scheduled)

    def _upload(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        output_path = job.get("output_path")
        if not output_path or not Path(output_path).exists():
            log.warning("Job #%s: file output hilang, mengulang penggabungan.", job_id)
            self.db.update(job_id, status="merging", output_path=None, next_run_at=utcnow())
            return
        meta = Metadata.from_dict(json.loads(job["metadata_json"] or "{}"))
        if not meta.title:
            self.db.update(job_id, status="metadata", next_run_at=utcnow())
            return
        log.info("Job #%s: mengunggah ke YouTube (%s)...", job_id, self.cfg.youtube.privacy_status)
        video_id = self.uploader.upload(
            output_path,
            meta,
            privacy_status=self.cfg.youtube.privacy_status,
            made_for_kids=self.cfg.youtube.made_for_kids,
            category_id=self.cfg.youtube.category_id,
            default_language=self.cfg.youtube.default_language,
            notify_subscribers=self.cfg.youtube.notify_subscribers,
        )
        self.db.update(job_id, youtube_video_id=video_id, status="done", next_run_at=None, last_error=None, uploaded_at=utcnow())
        log.info("Job #%s SELESAI -> https://youtube.com/shorts/%s", job_id, video_id)
        if self.cfg.youtube.playlist_id:
            self._safe(lambda: self.uploader.add_to_playlist(video_id, self.cfg.youtube.playlist_id), "playlist")
        self._safe(lambda: self._cleanup_after_upload(job), "pembersihan")
        self.notifier.notify_job("done", self.db.get(job_id) or job)

    def _cleanup_after_upload(self, job: dict[str, Any]) -> None:
        m = self.cfg.maintenance
        if not (m.delete_segments_after_upload or m.delete_output_after_upload):
            return
        job_id = job["id"]
        plan = VideoPlan.from_dict(json.loads(job.get("plan_json") or "{}"))
        count = len(plan.segments) or self.cfg.segments_per_video
        removed = 0
        if m.delete_segments_after_upload:
            for p in job_files(self.cfg.paths.prompt_dir, self.cfg.paths.flow_download_dir, job_id, count):
                if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS:
                    p.unlink()
                    removed += 1
        if m.delete_output_after_upload and job.get("output_path"):
            out = Path(job["output_path"])
            if out.is_file():
                out.unlink()
                removed += 1
        if removed:
            log.info("Job #%s: %s file dibersihkan setelah upload.", job_id, removed)

    # ------------------------------------------------------------------ kegagalan
    def _handle_failure(self, job_id: int, exc: BaseException) -> None:
        error = f"{type(exc).__name__}: {exc}"[:2000]
        if isinstance(exc, RetryLaterError):
            next_run = iso_in(exc.delay_seconds)
            self.db.defer(job_id, next_run, error)
            log.warning("Job #%s ditunda sampai %s: %s", job_id, next_run, exc)
            return
        job = self.db.get(job_id)
        if not job:
            return
        if job["status"] in TERMINAL_STATUSES:
            log.info("Job #%s sudah %s saat kegagalan terjadi; tidak diubah. (%s)", job_id, job["status"], error[:200])
            return
        attempts = int(job["attempts"] or 0) + 1
        max_attempts = int(job["max_attempts"] or self.cfg.retry.max_attempts)
        if isinstance(exc, NonRetryableError) or attempts >= max_attempts:
            self.db.mark_failed(job_id, error)
            self._record_failure(job, error)
            log.error("Job #%s GAGAL permanen pada tahap '%s': %s", job_id, job["status"], error)
            self.notifier.notify_job("failed", self.db.get(job_id) or job, f"Tahap: {job['status']}\n{error[:500]}")
            return
        delay = min(self.cfg.retry.base_delay_seconds * (2 ** (attempts - 1)), self.cfg.retry.max_delay_seconds)
        self.db.increment_attempt(job_id, error, next_run_at=iso_in(delay))
        log.warning("Job #%s gagal (percobaan %s/%s), diulang dalam %ss: %s", job_id, attempts, max_attempts, delay, error)

    def _record_failure(self, job: dict[str, Any], error: str) -> None:
        """Simpan ringkasan job gagal ke data/failed/job_<id>.json (untuk audit / pemulihan manual)."""
        try:
            failed_dir = Path(self.cfg.paths.failed_dir)
            failed_dir.mkdir(parents=True, exist_ok=True)
            record = {
                "job_id": job["id"],
                "niche": job["niche"],
                "failed_at": utcnow(),
                "stage": job["status"],
                "error": error,
                "segment_paths": json.loads(job.get("segment_paths_json") or "[]"),
                "output_path": job.get("output_path"),
                "plan": json.loads(job.get("plan_json") or "null"),
            }
            (failed_dir / f"job_{job['id']}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            log.debug("Tidak bisa menulis catatan gagal: %s", e)
