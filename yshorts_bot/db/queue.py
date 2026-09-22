from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATUSES = {
    "queued", "planning", "waiting_flow_segment_1", "waiting_flow_segment_2",
    "merging", "metadata", "scheduled", "uploading", "done", "failed"
}


class QueueDB:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def init(self) -> None:
        with self.connect() as con:
            con.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                niche TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                next_run_at TEXT,
                scheduled_upload_at TEXT,
                idea TEXT,
                plan_json TEXT,
                metadata_json TEXT,
                segment_paths_json TEXT,
                output_path TEXT,
                youtube_video_id TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """)
            # Migration untuk versi lama yang belum punya scheduled_upload_at
            cols = {r[1] for r in con.execute("PRAGMA table_info(jobs)").fetchall()}
            if "scheduled_upload_at" not in cols:
                con.execute("ALTER TABLE jobs ADD COLUMN scheduled_upload_at TEXT")
            con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status_next ON jobs(status, next_run_at)")

    def enqueue(self, niche: str, count: int, max_attempts: int, scheduled_times: list[str] | None = None) -> list[int]:
        now = utcnow()
        ids: list[int] = []
        with self.connect() as con:
            for i in range(count):
                scheduled_upload = scheduled_times[i] if scheduled_times and i < len(scheduled_times) else now
                # next_run_at mengontrol kapan worker boleh memproses tahap berikutnya.
                # Job baru diproses segera agar prompt/video bisa disiapkan sebelum jadwal upload.
                cur = con.execute(
                    """INSERT INTO jobs(niche,status,max_attempts,next_run_at,scheduled_upload_at,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?)""",
                    (niche, "queued", max_attempts, now, scheduled_upload, now, now),
                )
                ids.append(int(cur.lastrowid))
        return ids

    def next_job(self) -> dict[str, Any] | None:
        now = utcnow()
        with self.connect() as con:
            row = con.execute(
                """SELECT * FROM jobs
                WHERE status NOT IN ('done','failed')
                AND (next_run_at IS NULL OR next_run_at <= ?)
                ORDER BY id ASC LIMIT 1""",
                (now,),
            ).fetchone()
            return dict(row) if row else None

    def get(self, job_id: int) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def update(self, job_id: int, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = utcnow()
        keys = list(fields.keys())
        values = [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in fields.values()]
        set_clause = ", ".join([f"{k}=?" for k in keys])
        with self.connect() as con:
            con.execute(f"UPDATE jobs SET {set_clause} WHERE id=?", values + [job_id])

    def increment_attempt(self, job_id: int, error: str, next_run_at: str | None = None) -> None:
        with self.connect() as con:
            con.execute(
                "UPDATE jobs SET attempts=attempts+1,last_error=?,next_run_at=?,updated_at=? WHERE id=?",
                (error, next_run_at, utcnow(), job_id),
            )

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    def retry_job(self, job_id: int) -> bool:
        now = utcnow()
        with self.connect() as con:
            cur = con.execute(
                """UPDATE jobs SET status='queued', attempts=0, last_error=NULL, next_run_at=?, updated_at=?
                WHERE id=?""",
                (now, now, job_id)
            )
            return cur.rowcount > 0

    def cancel_job(self, job_id: int) -> bool:
        with self.connect() as con:
            cur = con.execute(
                """UPDATE jobs SET status='failed', last_error='Dibatalkan oleh pengguna', updated_at=?
                WHERE id=? AND status != 'done'""",
                (utcnow(), job_id)
            )
            return cur.rowcount > 0

    def delete_job(self, job_id: int) -> bool:
        with self.connect() as con:
            cur = con.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            return cur.rowcount > 0

    def get_stats(self) -> dict[str, int]:
        with self.connect() as con:
            rows = con.execute("SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status").fetchall()
            stats = {s: 0 for s in STATUSES}
            for r in rows:
                stats[r["status"]] = r["cnt"]
            stats["total"] = sum(stats.values())
            return stats


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

