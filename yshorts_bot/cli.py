from __future__ import annotations

import argparse
import json


from .config import load_config
from .db.queue import QueueDB
from .logging_setup import setup_logging
from .scheduler.schedule import build_upload_schedule


def main() -> None:
    parser = argparse.ArgumentParser(description="AI YouTube Shorts automation queue")
    parser.add_argument("--config", default="config.json")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db")

    p_enqueue = sub.add_parser("enqueue")
    p_enqueue.add_argument("--niche", required=True)
    p_enqueue.add_argument("--count", type=int, default=1)

    sub.add_parser("run")
    p_once = sub.add_parser("run-once")
    p_once.add_argument("--job-id", type=int)

    p_status = sub.add_parser("status")
    p_status.add_argument("--limit", type=int, default=50)

    p_dash = sub.add_parser("dashboard")
    p_dash.add_argument("--host", default="127.0.0.1")
    p_dash.add_argument("--port", type=int, default=8000)

    args = parser.parse_args()
    cfg = load_config(args.config)
    setup_logging(cfg.paths.log_dir)
    db = QueueDB(cfg.paths.db_path)

    if args.cmd == "init-db":
        db.init()
        print(f"DB siap: {cfg.paths.db_path}")
    elif args.cmd == "enqueue":
        db.init()
        times = build_upload_schedule(cfg.schedule, args.count)
        ids = db.enqueue(args.niche, args.count, cfg.retry.max_attempts, times)
        print("Jobs dibuat:", ids)
    elif args.cmd == "run":
        from .worker import Worker
        Worker(cfg).run_forever()
    elif args.cmd == "run-once":
        from .worker import Worker
        db.init()
        worker = Worker(cfg)
        if args.job_id:
            worker.process_job(args.job_id)
        else:
            job = db.next_job()
            if job:
                worker.process_job(job["id"])
            else:
                print("Tidak ada job siap diproses.")
    elif args.cmd == "status":
        db.init()
        print(json.dumps(db.list_jobs(args.limit), indent=2, ensure_ascii=False))
    elif args.cmd == "dashboard":
        try:
            import uvicorn
            from .dashboard import create_app
        except ModuleNotFoundError as e:
            raise SystemExit("Dashboard membutuhkan paket tambahan. Jalankan: pip install -r requirements.txt") from e
        app = create_app(args.config)
        uvicorn.run(app, host=args.host, port=args.port)
