from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .config import AppConfig, ScheduleConfig, env_flag, load_config
from .db.queue import QueueDB
from .errors import ConfigError
from .logging_setup import setup_logging
from .scheduler.schedule import build_upload_schedule

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yshorts-bot",
        description="YShorts Bot Studio - otomasi YouTube Shorts berbasis AI (Google Flow + FFmpeg + YouTube API)",
    )
    parser.add_argument("--config", default="config.json", help="lokasi config.json (default: ./config.json)")
    parser.add_argument("-v", "--verbose", action="store_true", help="log level DEBUG")
    parser.add_argument("--version", action="version", version=f"yshorts-bot {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="buat/migrasi database antrean")

    p_enqueue = sub.add_parser("enqueue", help="tambahkan job ke antrean (default: niche & jumlah dari config.json)")
    p_enqueue.add_argument("--niche", help="niche/topik (default: config.niche)")
    p_enqueue.add_argument("--count", type=int, help="jumlah video (default: config.total_videos)")
    p_enqueue.add_argument("--mode", choices=["interval", "specific_times"], help="mode jadwal (default: config)")
    p_enqueue.add_argument("--interval-hours", type=float, help="interval jam antar upload")
    p_enqueue.add_argument("--start-time", help="jam upload pertama HH:MM atau 'now'")
    p_enqueue.add_argument("--times", help="daftar jam untuk mode specific_times, contoh '08:00,12:00,18:00'")

    sub.add_parser("run", help="jalankan worker terus-menerus")
    p_once = sub.add_parser("run-once", help="proses satu tahap dari satu job lalu keluar")
    p_once.add_argument("--job-id", type=int)

    p_status = sub.add_parser("status", help="tampilkan antrean")
    p_status.add_argument("--limit", type=int, default=50)
    p_status.add_argument("--json", action="store_true", help="keluaran JSON mentah")

    for name, help_text in (("retry", "ulangi job dari tahap gagal"), ("cancel", "batalkan job")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("job_id", type=int)
    sub.add_parser("pause", help="jeda worker (job tidak diproses)")
    sub.add_parser("resume", help="lanjutkan worker")

    p_dash = sub.add_parser("dashboard", help="jalankan web dashboard")
    p_dash.add_argument("--host", default="127.0.0.1")
    p_dash.add_argument("--port", type=int, default=8000)

    sub.add_parser("youtube-auth", help="login Google sekali untuk menyimpan token upload YouTube")
    sub.add_parser("doctor", help="periksa kesiapan sistem (ffmpeg, AI, Flow, YouTube, jadwal)")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        raise SystemExit(f"[CONFIG] {e}") from e

    log_name = {"dashboard": "dashboard", "run": "worker", "run-once": "worker"}.get(args.cmd, "cli")
    setup_logging(cfg.paths.log_dir, logging.DEBUG if args.verbose else logging.INFO, name=log_name)
    db = QueueDB(cfg.paths.db_path)

    if args.cmd == "init-db":
        db.init()
        print(f"Database siap: {cfg.paths.db_path}")
    elif args.cmd == "enqueue":
        cmd_enqueue(cfg, db, args)
    elif args.cmd == "run":
        from .worker import Worker

        try:
            Worker(cfg, config_path=args.config).run_forever()
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            raise SystemExit(f"[WORKER] Tidak bisa memulai worker: {e}") from e
    elif args.cmd == "run-once":
        from .worker import Worker

        db.init()
        worker = Worker(cfg, config_path=args.config)
        job_id = args.job_id
        if not job_id:
            job = db.next_job()
            if not job:
                print("Tidak ada job yang siap diproses.")
                return
            job_id = job["id"]
        worker.process_job(job_id)
        after = db.get(job_id)
        print(f"Job #{job_id} -> status: {after['status'] if after else '?'}")
    elif args.cmd == "status":
        db.init()
        cmd_status(db, args.limit, args.json)
    elif args.cmd in ("retry", "cancel"):
        db.init()
        ok = db.retry_job(args.job_id) if args.cmd == "retry" else db.cancel_job(args.job_id)
        job = db.get(args.job_id)
        print(f"Job #{args.job_id}: {'OK' if ok else 'tidak ada perubahan'} -> status {job['status'] if job else 'tidak ditemukan'}")
    elif args.cmd in ("pause", "resume"):
        db.init()
        db.set_meta("worker_paused", "1" if args.cmd == "pause" else "0")
        print("Worker dijeda." if args.cmd == "pause" else "Worker dilanjutkan.")
    elif args.cmd == "dashboard":
        try:
            import uvicorn

            from .dashboard import create_app
        except ModuleNotFoundError as e:
            raise SystemExit("Dashboard membutuhkan paket tambahan. Jalankan: pip install -r requirements.txt") from e
        app = create_app(args.config)
        print(f"Dashboard: http://{args.host}:{args.port}  (Ctrl+C untuk berhenti)")
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    elif args.cmd == "youtube-auth":
        cmd_youtube_auth()
    elif args.cmd == "doctor":
        sys.exit(cmd_doctor(cfg, args.config))


# ---------------------------------------------------------------------------
def cmd_enqueue(cfg: AppConfig, db: QueueDB, args: argparse.Namespace) -> None:
    db.init()
    niche = (args.niche or cfg.niche).strip()
    count = args.count or cfg.total_videos
    if not niche:
        raise SystemExit("Niche kosong. Isi --niche atau config.niche.")
    if count < 1:
        raise SystemExit("--count harus >= 1")
    sched_data = cfg.schedule.model_dump()
    if args.mode:
        sched_data["mode"] = args.mode
    if args.interval_hours:
        sched_data["interval_hours"] = args.interval_hours
    if args.start_time:
        sched_data["start_time"] = args.start_time
    if args.times:
        sched_data["specific_times"] = [t.strip() for t in args.times.split(",") if t.strip()]
        sched_data.setdefault("mode", "specific_times")
        if not args.mode:
            sched_data["mode"] = "specific_times"
    try:
        sched = ScheduleConfig(**sched_data)
        times = build_upload_schedule(sched, count)
    except (ValueError, Exception) as e:  # pydantic ValidationError adalah ValueError
        raise SystemExit(f"Jadwal tidak valid: {e}") from e
    ids = db.enqueue(niche, count, cfg.retry.max_attempts, times)
    print(f"{len(ids)} job dibuat untuk niche '{niche}': {ids}")
    for job_id, t in zip(ids, times):
        print(f"  job #{job_id} -> upload {t} (UTC)")


def cmd_status(db: QueueDB, limit: int, as_json: bool) -> None:
    jobs = db.list_jobs(limit)
    if as_json:
        print(json.dumps(jobs, indent=2, ensure_ascii=False))
        return
    from .worker import worker_online

    stats = db.get_stats()
    meta = db.all_meta()
    print(
        f"Total {stats['total']} | antrean {stats['queued']} | diproses {stats['processing']} | "
        f"terjadwal {stats['scheduled']} | selesai {stats['done']} | gagal/batal {stats['failed_or_cancelled']}"
    )
    print(
        f"Worker: {'ONLINE' if worker_online(meta) else 'offline'} | heartbeat {meta.get('worker_heartbeat') or '-'} | "
        f"paused: {meta.get('worker_paused') == '1'}"
    )
    if not jobs:
        print("(antrean kosong)")
        return
    print(f"{'ID':>4} | {'STATUS':<24} | {'JADWAL UPLOAD (UTC)':<25} | {'TRY':<5} | NICHE / IDE / ERROR")
    for j in jobs:
        idea = f" -> {j['idea']}" if j.get("idea") else ""
        err = f" !! {str(j['last_error'])[:80]}" if j.get("last_error") else ""
        print(f"{j['id']:>4} | {j['status']:<24} | {str(j.get('scheduled_upload_at') or '-'):<25} | {j['attempts']}/{j['max_attempts']:<3} | {j['niche']}{idea}{err}")


def cmd_youtube_auth() -> None:
    from .youtube.uploader import YouTubeUploader

    uploader = YouTubeUploader()
    if uploader.mock:
        print("YOUTUBE_MOCK_UPLOAD=true di .env -> upload disimulasikan, login tidak diperlukan.")
        return
    info = uploader.authorize()
    if info:
        print(f"Login berhasil. Channel: {info.get('title')} (id {info.get('id')}, subscriber {info.get('subscribers')}).")
    else:
        print("Login berhasil, tetapi akun ini belum memiliki channel YouTube.")
    print(f"Token tersimpan di {uploader.token_file}")


def cmd_doctor(cfg: AppConfig, config_path: str) -> int:
    results: list[tuple[str, str, str]] = []  # (level, nama, pesan)

    def ok(name: str, msg: str) -> None:
        results.append(("OK", name, msg))

    def warn(name: str, msg: str) -> None:
        results.append(("WARN", name, msg))

    def fail(name: str, msg: str) -> None:
        results.append(("FAIL", name, msg))

    ok("Python", f"{sys.version.split()[0]} ({sys.executable})")
    ok("Config", f"{Path(config_path).resolve()} | niche='{cfg.niche}' | {cfg.segments_per_video}x{cfg.segment_duration_seconds}s")

    from .video.ffmpeg import resolve_ffmpeg

    try:
        ok("FFmpeg", resolve_ffmpeg(cfg.video))
    except FileNotFoundError as e:
        fail("FFmpeg", str(e))

    ai_name = os.getenv("AI_PROVIDER", "mock").lower()
    if ai_name == "mock":
        warn("AI", "AI_PROVIDER=mock -> ide/prompt contoh. Set gemini/openai + API key di .env untuk konten asli.")
    elif ai_name in ("gemini", "google"):
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        (ok if key else fail)("AI", f"Gemini model={os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')} | API key {'ada' if key else 'BELUM diset (GEMINI_API_KEY)'}")
    elif ai_name == "openai":
        key = os.getenv("OPENAI_API_KEY")
        (ok if key else fail)("AI", f"OpenAI model={os.getenv('OPENAI_MODEL', 'gpt-4o-mini')} base={os.getenv('OPENAI_BASE_URL', 'api.openai.com')} | API key {'ada' if key else 'BELUM diset'}")
    else:
        fail("AI", f"AI_PROVIDER '{ai_name}' tidak dikenal (mock/gemini/openai)")

    provider = cfg.flow.provider
    if provider == "manual":
        ok("Flow", "manual/assisted: salin prompt ke Google Flow, unggah MP4 via dashboard (aman sesuai ToS)")
    elif provider in ("browser", "playwright"):
        try:
            import playwright  # noqa: F401

            warn("Flow", "browser (eksperimental): Playwright terinstall. Jalankan `python scripts/test_flow_browser.py` untuk login & kalibrasi selector.")
        except ModuleNotFoundError:
            fail("Flow", "flow.provider=browser tetapi Playwright belum terinstall: pip install playwright && playwright install chromium")
    elif provider == "veo_api":
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        (ok if key else fail)("Flow", f"veo_api model={cfg.flow.veo.model} {cfg.flow.veo.resolution} {cfg.flow.veo.aspect_ratio} | GEMINI_API_KEY {'ada' if key else 'BELUM diset'} (berbayar per video)")
    else:
        warn("Flow", f"provider '{provider}' hanya placeholder")

    if env_flag("YOUTUBE_MOCK_UPLOAD", False):
        warn("YouTube", "YOUTUBE_MOCK_UPLOAD=true -> upload disimulasikan (tidak benar-benar ke YouTube).")
    else:
        secret = Path(os.getenv("YOUTUBE_CLIENT_SECRET_FILE", "client_secret.json"))
        token = Path(os.getenv("YOUTUBE_TOKEN_FILE", "secrets/token.json"))
        if token.exists():
            ok("YouTube", f"token OAuth ada ({token}); privasi upload={cfg.youtube.privacy_status}")
        elif secret.exists():
            warn("YouTube", "client_secret ada, token belum. Jalankan: python -m yshorts_bot youtube-auth")
        else:
            fail("YouTube", f"{secret} tidak ditemukan. Unduh OAuth client (Desktop app) dari Google Cloud Console atau set YOUTUBE_MOCK_UPLOAD=true")
        warn("YouTube kuota", "Kuota default YouTube Data API 10.000 unit/hari; 1 upload = 1.600 unit (~6 upload/hari). Ajukan kenaikan kuota bila perlu.")

    try:
        times = build_upload_schedule(cfg.schedule, 3)
        ok("Jadwal", f"mode={cfg.schedule.mode} tz={cfg.schedule.timezone} | 3 slot berikutnya (UTC): {', '.join(times)}")
    except Exception as e:  # noqa: BLE001
        fail("Jadwal", str(e))

    try:
        db = QueueDB(cfg.paths.db_path)
        db.init()
        stats = db.get_stats()
        ok("Database", f"{cfg.paths.db_path} | total job {stats['total']} | worker heartbeat {db.get_meta('worker_heartbeat') or '-'}")
    except Exception as e:  # noqa: BLE001
        fail("Database", str(e))

    if cfg.planned_duration_seconds < cfg.video.min_duration_seconds and not cfg.video.pad_to_min_duration:
        warn("Durasi", f"{cfg.segments_per_video}x{cfg.segment_duration_seconds}s = {cfg.planned_duration_seconds}s < min {cfg.video.min_duration_seconds}s dan pad_to_min_duration=false")

    width = max(len(r[1]) for r in results)
    for level, name, msg in results:
        print(f"[{level:<4}] {name:<{width}} : {msg}")
    fails = sum(1 for r in results if r[0] == "FAIL")
    print(f"\n{'Semua pemeriksaan penting lolos.' if not fails else f'{fails} masalah harus diperbaiki.'}")
    return 1 if fails else 0


if __name__ == "__main__":  # pragma: no cover
    main()
