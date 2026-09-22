"""Util bersama semua provider Flow: file prompt, lokasi video segmen, deteksi file siap pakai."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..models import SegmentPrompt

VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".mkv", ".m4v")


def prompt_path(prompt_dir: str | Path, job_id: int, index: int) -> Path:
    return Path(prompt_dir) / f"job_{job_id}_segment_{index}.txt"


def expected_video_path(download_dir: str | Path, job_id: int, index: int) -> Path:
    return Path(download_dir) / f"job_{job_id}_segment_{index}.mp4"


def candidate_video_paths(download_dir: str | Path, job_id: int, index: int) -> list[Path]:
    base = Path(download_dir)
    stems = [
        f"job_{job_id}_segment_{index}",
        f"job_{job_id}_seg{index}",
        f"job_{job_id}_seg_{index}",
        f"job{job_id}_segment{index}",
    ]
    paths = [base / f"{stem}{ext}" for stem in stems for ext in VIDEO_EXTENSIONS]
    paths += [base / str(job_id) / f"segment_{index}{ext}" for ext in VIDEO_EXTENSIONS]
    return paths


def find_ready_video(
    download_dir: str | Path,
    job_id: int,
    index: int,
    min_bytes: int = 1024,
    stable_seconds: float = 3.0,
    now: float | None = None,
) -> Path | None:
    """Cari file video segmen yang sudah selesai ditulis.

    File dianggap siap bila ukurannya >= min_bytes dan tidak berubah selama `stable_seconds`
    (menghindari mengambil file yang masih di-download/di-copy).
    """
    now = now if now is not None else time.time()
    for candidate in candidate_video_paths(download_dir, job_id, index):
        if not candidate.is_file():
            continue
        stat = candidate.stat()
        if stat.st_size >= min_bytes and (now - stat.st_mtime) >= stable_seconds:
            return candidate
    return None


def write_prompt_file(
    prompt_dir: str | Path,
    download_dir: str | Path,
    job_id: int,
    niche: str,
    segment: SegmentPrompt,
    overwrite: bool = False,
) -> Path:
    path = prompt_path(prompt_dir, job_id, segment.index)
    if path.exists() and not overwrite:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    target = expected_video_path(download_dir, job_id, segment.index)
    text = (
        f"=== GOOGLE FLOW PROMPT - JOB #{job_id} SEGMENT {segment.index} ===\n"
        f"Niche: {niche}\n"
        f"Durasi target: {segment.duration_seconds} detik (Vertical 9:16)\n\n"
        f"--- PROMPT (salin teks di bawah ini ke Google Flow) ---\n"
        f"{segment.prompt}\n\n"
        f"--- INSTRUKSI ---\n"
        f"1. Buka Google Flow, buat project baru / lanjutkan project, tempel prompt di atas, generate.\n"
        f"2. Download hasil MP4 (durasi sekitar {segment.duration_seconds} detik).\n"
        f"3. Simpan file dengan nama berikut (atau unggah lewat tombol 'Kirim Video' di dashboard):\n"
        f"   {target.resolve()}\n"
    )
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- state kecil per segmen
def state_path(prompt_dir: str | Path, job_id: int, index: int, kind: str) -> Path:
    return Path(prompt_dir) / f"job_{job_id}_segment_{index}.{kind}.json"


def read_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_state(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
