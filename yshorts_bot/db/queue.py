from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Urutan tahapan pipeline (status `waiting_flow_segment_N` dinamis sesuai jumlah segmen)
STATUSES = [
    "queued",
    "planning",
    "waiting_flow",
    "merging",
    "metadata",
    "awaiting_approval",
    "scheduled",
    "uploading",
    "done",
    "failed",
    "cancelled",
]
TERMINAL_STATUSES = {"done", "failed", "cancelled"}
PROCESSING_STATUSES = {"planning", "merging", "metadata", "uploading"}
# Status yang tidak boleh diambil worker walau next_run_at kosong
INACTIVE_STATUSES = TERMINAL_STATUSES | {"awaiting_approval"}
# Status yang boleh diedit metadatanya (sudah ada metadata, belum terupload)
METADATA_EDITABLE_STATUSES = {"awaiting_approval", "scheduled", "uploading", "failed", "cancelled"}

_JOB_COLUMNS: dict[str, str] = {
    "scheduled_upload_at": "TEXT",
    "flow_waiting_since": "TEXT",
    "failed_from_status": "TEXT",
    "uploaded_at": "TEXT",
}


def waiting_status(segment_index: int) -> str:
    return f"waiting_flow_segment_{segment_index}"


def is_waiting_status(status: str | None) -> bool:
    return bool(status) and str(status).startswith("waiting_flow_segment_")


def waiting_segment_index(status: str) -> int:
    return int(str(status).rsplit("_", 1)[-1])


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _placeholders(values: list[str] | set[str]) -> str:
    return ",".join("?" for _ in values)


class QueueDB:
    """Antrean job persisten berbasis SQLite (aman untuk 1 worker + 1 dashboard bersamaan)."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    # ------------------------------------------------------------------ schema
    def init(self) -> None:
        with self.connect() as con:
            try:
                con.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    niche TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    next_run_at TEXT,
                    scheduled_upload_at TEXT,
                    flow_waiting_since TEXT,
                    failed_from_status TEXT,
                    uploaded_at TEXT,
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
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # Migrasi ringan untuk database versi lama
            cols = {r[1] for r in con.execute("PRAGMA table_info(jobs)").fetchall()}
            for col, ctype in _JOB_COLUMNS.items():
                if col not in cols:
                    con.execute(f"ALTER TABLE jobs ADD COLUMN {col} {ctype}")
            con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status_next ON jobs(status, next_run_at)")

    # ------------------------------------------------------------------ jobs
    def enqueue(
        self,
        niche: str,
        count: int,
        max_attempts: int,
        scheduled_times: list[str] | None = None,
    ) -> list[int]:
        now = utcnow()
        ids: list[int] = []
        with self.connect() as con:
            for i in range(count):
                scheduled_upload = scheduled_times[i] if scheduled_times and i < len(scheduled_times) else now
                # next_run_at = sekarang: ide/prompt/video disiapkan lebih dulu, upload menunggu jadwal.
                cur = con.execute(
                    """INSERT INTO jobs(niche,status,max_attempts,next_run_at,scheduled_upload_at,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?)""",
                    (niche, "queued", max_attempts, now, scheduled_upload, now, now),
                )
                ids.append(int(cur.lastrowid))
        return ids

    def next_job(self, now: str | None = None) -> dict[str, Any] | None:
        """Job berikutnya yang siap diproses (yang paling lama menunggu lebih dulu)."""
        now = now or utcnow()
        inactive = sorted(INACTIVE_STATUSES)
        with self.connect() as con:
            row = con.execute(
                f"""SELECT * FROM jobs
                WHERE status NOT IN ({_placeholders(inactive)})
                  AND (next_run_at IS NULL OR next_run_at <= ?)
                ORDER BY (next_run_at IS NULL) DESC, next_run_at ASC, id ASC
                LIMIT 1""",
                (*inactive, now),
            ).fetchone()
            return dict(row) if row else None

    def get(self, job_id: int) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def update(self, job_id: int, expected_status: str | None = None, **fields: Any) -> bool:
        """Perbarui kolom job. Bila `expected_status` diisi, hanya berlaku jika status masih sama
        (compare-and-swap) - mencegah worker menimpa aksi Batalkan/Retry dari dashboard."""
        if not fields:
            return False
        fields["updated_at"] = utcnow()
        keys = list(fields.keys())
        values = [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in fields.values()]
        set_clause = ", ".join(f"{k}=?" for k in keys)
        where, params = "id=?", values + [job_id]
        if expected_status is not None:
            where += " AND status=?"
            params.append(expected_status)
        with self.connect() as con:
            cur = con.execute(f"UPDATE jobs SET {set_clause} WHERE {where}", params)
            return cur.rowcount > 0

    def increment_attempt(self, job_id: int, error: str, next_run_at: str | None = None) -> None:
        with self.connect() as con:
            con.execute(
                """UPDATE jobs SET attempts=attempts+1, last_error=?, next_run_at=?, flow_waiting_since=NULL, updated_at=?
                WHERE id=?""",
                (error, next_run_at, utcnow(), job_id),
            )

    def defer(self, job_id: int, next_run_at: str, error: str | None = None) -> None:
        """Tunda job tanpa menghabiskan jatah retry (mis. kuota API habis)."""
        fields: dict[str, Any] = {"next_run_at": next_run_at}
        if error:
            fields["last_error"] = error
        self.update(job_id, **fields)

    def mark_failed(self, job_id: int, error: str) -> None:
        with self.connect() as con:
            con.execute(
                """UPDATE jobs SET failed_from_status=status, status='failed', last_error=?, next_run_at=NULL, updated_at=?
                WHERE id=?""",
                (error, utcnow(), job_id),
            )

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    def list_by_status(self, statuses: set[str] | list[str], prefix: str | None = None) -> list[dict[str, Any]]:
        """Job dengan status tertentu (dan/atau berawalan `prefix`, mis. 'waiting_flow_segment_'), urut id."""
        statuses = list(statuses)
        clauses, params = [], []
        if statuses:
            clauses.append(f"status IN ({_placeholders(statuses)})")
            params.extend(statuses)
        if prefix:
            clauses.append("status LIKE ?")
            params.append(prefix + "%")
        if not clauses:
            return []
        with self.connect() as con:
            rows = con.execute(f"SELECT * FROM jobs WHERE {' OR '.join(clauses)} ORDER BY id ASC", params).fetchall()
            return [dict(r) for r in rows]

    def retry_job(self, job_id: int) -> bool:
        """Ulangi job dari tahap tempat ia gagal (bukan dari awal), supaya hasil sebelumnya tidak terbuang."""
        job = self.get(job_id)
        if not job:
            return False
        if job["status"] in ("failed", "cancelled"):
            target = job.get("failed_from_status") or "queued"
        else:
            target = job["status"]
        if not target or target in TERMINAL_STATUSES:
            target = "queued"
        now = utcnow()
        if target == "awaiting_approval":
            next_run = None
        elif target == "scheduled" and job.get("scheduled_upload_at"):
            next_run = job["scheduled_upload_at"]  # hormati jadwal, jangan upload segera
        else:
            next_run = now
        with self.connect() as con:
            cur = con.execute(
                """UPDATE jobs SET status=?, attempts=0, last_error=NULL, failed_from_status=NULL,
                flow_waiting_since=NULL, next_run_at=?, updated_at=? WHERE id=?""",
                (target, next_run, now, job_id),
            )
            return cur.rowcount > 0

    def cancel_job(self, job_id: int) -> bool:
        with self.connect() as con:
            cur = con.execute(
                """UPDATE jobs SET
                    failed_from_status = CASE WHEN status='failed' THEN failed_from_status ELSE status END,
                    status='cancelled', last_error='Dibatalkan oleh pengguna', next_run_at=NULL, updated_at=?
                WHERE id=? AND status NOT IN ('done','cancelled')""",
                (utcnow(), job_id),
            )
            return cur.rowcount > 0

    def approve_job(self, job_id: int, upload_now: bool = False) -> bool:
        """Setujui job yang menunggu persetujuan -> 'scheduled' (upload sesuai jadwal atau sekarang)."""
        job = self.get(job_id)
        if not job or job["status"] != "awaiting_approval":
            return False
        now = utcnow()
        scheduled = job.get("scheduled_upload_at") or now
        if upload_now or scheduled < now:
            scheduled = now
        self.update(job_id, status="scheduled", scheduled_upload_at=scheduled, next_run_at=scheduled, last_error=None)
        return True

    def reschedule_job(self, job_id: int, scheduled_upload_at: str) -> bool:
        job = self.get(job_id)
        if not job:
            return False
        fields: dict[str, Any] = {"scheduled_upload_at": scheduled_upload_at}
        if job["status"] == "scheduled":
            fields["next_run_at"] = scheduled_upload_at
        self.update(job_id, **fields)
        return True

    def delete_job(self, job_id: int) -> bool:
        with self.connect() as con:
            cur = con.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            return cur.rowcount > 0

    # ------------------------------------------------------------------ aksi massal
    def bulk_retry_failed(self) -> list[int]:
        ids = [j["id"] for j in self.list_by_status({"failed"})]
        return [i for i in ids if self.retry_job(i)]

    def bulk_cancel_pending(self) -> list[int]:
        """Batalkan semua job yang belum selesai/terupload (kecuali yang sedang uploading)."""
        with self.connect() as con:
            rows = con.execute(
                "SELECT id FROM jobs WHERE status NOT IN ('done','failed','cancelled','uploading')"
            ).fetchall()
            ids = [int(r["id"]) for r in rows]
            if ids:
                con.execute(
                    f"""UPDATE jobs SET
                        failed_from_status = CASE WHEN status='failed' THEN failed_from_status ELSE status END,
                        status='cancelled', last_error='Dibatalkan massal oleh pengguna', next_run_at=NULL, updated_at=?
                    WHERE id IN ({_placeholders(ids)})""",
                    (utcnow(), *ids),
                )
        return ids

    def bulk_approve(self) -> list[int]:
        ids = [j["id"] for j in self.list_by_status({"awaiting_approval"})]
        return [i for i in ids if self.approve_job(i)]

    def get_stats(self) -> dict[str, int]:
        with self.connect() as con:
            rows = con.execute("SELECT status, COUNT(*) AS cnt FROM jobs GROUP BY status").fetchall()
        stats: dict[str, int] = {s: 0 for s in STATUSES}
        total = 0
        for r in rows:
            status, cnt = r["status"], int(r["cnt"])
            total += cnt
            if is_waiting_status(status):
                stats["waiting_flow"] += cnt
            stats[status] = stats.get(status, 0) + cnt
        stats["total"] = total
        stats["processing"] = stats["waiting_flow"] + sum(stats.get(s, 0) for s in PROCESSING_STATUSES)
        stats["failed_or_cancelled"] = stats["failed"] + stats["cancelled"]
        return stats

    # ------------------------------------------------------------------ meta (heartbeat, pause, dll.)
    def set_meta(self, key: str, value: str) -> None:
        with self.connect() as con:
            con.execute(
                """INSERT INTO meta(key,value,updated_at) VALUES(?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (key, value, utcnow()),
            )

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self.connect() as con:
            row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def all_meta(self) -> dict[str, str]:
        with self.connect() as con:
            rows = con.execute("SELECT key, value FROM meta").fetchall()
            return {r["key"]: r["value"] for r in rows}

    def delete_meta(self, key: str) -> None:
        with self.connect() as con:
            con.execute("DELETE FROM meta WHERE key=?", (key,))
