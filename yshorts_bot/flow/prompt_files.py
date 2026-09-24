"""Util bersama semua provider Flow: file prompt, lokasi video segmen, deteksi file siap pakai, inbox."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..models import SegmentPrompt

VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".mkv", ".m4v")
# Ekstensi file yang masih di-download oleh browser / belum selesai ditulis
PARTIAL_SUFFIXES = (".crdownload", ".part", ".tmp", ".download", ".partial", ".opdownload")

# path -> (ukuran terakhir, waktu pertama kali ukuran itu terlihat). Untuk deteksi file yang masih ditulis.
_SIZE_CACHE: dict[str, tuple[int, float]] = {}
_SIZE_CACHE_LIMIT = 5000


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


def is_partial_download(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(PARTIAL_SUFFIXES) or name.startswith((".", "~$"))


def is_video_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS and not is_partial_download(path)


def file_is_stable(path: Path, min_bytes: int = 1024, stable_seconds: float = 3.0, now: float | None = None) -> bool:
    """True bila file selesai ditulis: ukuran >= min_bytes, ukuran tidak berubah selama `stable_seconds`
    (dipantau antar pemanggilan) DAN mtime sudah lewat `stable_seconds`.

    Kombinasi keduanya penting: penyalinan file di Windows bisa mempertahankan mtime lama,
    sementara download browser memperbarui mtime terus-menerus.
    """
    try:
        stat = path.stat()
    except OSError:
        return False
    size = stat.st_size
    if size < min_bytes:
        return False
    if stable_seconds <= 0:
        return True
    now = now if now is not None else time.time()
    key = str(path.resolve())
    previous = _SIZE_CACHE.get(key)
    if previous is None or previous[0] != size:
        if len(_SIZE_CACHE) >= _SIZE_CACHE_LIMIT:
            _SIZE_CACHE.clear()
        _SIZE_CACHE[key] = (size, now)
        return False
    return (now - previous[1]) >= stable_seconds and (now - stat.st_mtime) >= stable_seconds


def forget_file(path: Path) -> None:
    _SIZE_CACHE.pop(str(path.resolve()), None)


def find_ready_video(
    download_dir: str | Path,
    job_id: int,
    index: int,
    min_bytes: int = 1024,
    stable_seconds: float = 3.0,
    now: float | None = None,
) -> Path | None:
    """Cari file video segmen yang sudah selesai ditulis (lihat `file_is_stable`)."""
    for candidate in candidate_video_paths(download_dir, job_id, index):
        if candidate.is_file() and file_is_stable(candidate, min_bytes, stable_seconds, now):
            forget_file(candidate)
            return candidate
    return None


def list_inbox_files(inbox_dir: str | Path, min_bytes: int = 1024, stable_seconds: float = 3.0) -> list[Path]:
    """File video di folder inbox yang sudah selesai ditulis, urut waktu modifikasi (tertua dulu)."""
    base = Path(inbox_dir)
    if not base.is_dir():
        return []
    ready = [p for p in base.iterdir() if is_video_file(p) and file_is_stable(p, min_bytes, stable_seconds)]
    return sorted(ready, key=lambda p: (p.stat().st_mtime, p.name))


def write_prompt_file(
    prompt_dir: str | Path,
    download_dir: str | Path,
    job_id: int,
    niche: str,
    segment: SegmentPrompt,
    overwrite: bool = False,
    inbox_dir: str | Path | None = None,
) -> Path:
    path = prompt_path(prompt_dir, job_id, segment.index)
    if path.exists() and not overwrite:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    target = expected_video_path(download_dir, job_id, segment.index)
    inbox_note = f"   atau letakkan file apa adanya di folder inbox: {Path(inbox_dir).resolve()}\n" if inbox_dir else ""
    text = (
        f"=== GOOGLE FLOW PROMPT - JOB #{job_id} SEGMENT {segment.index} ===\n"
        f"Niche: {niche}\n"
        f"Durasi target: {segment.duration_seconds} detik (Vertical 9:16)\n\n"
        f"--- PROMPT (salin teks di bawah ini ke Google Flow) ---\n"
        f"{segment.prompt}\n\n"
        f"--- INSTRUKSI ---\n"
        f"1. Buka Google Flow, buat project baru / lanjutkan project, tempel prompt di atas, generate.\n"
        f"2. Download hasil MP4 (durasi sekitar {segment.duration_seconds} detik).\n"
        f"3. Simpan file dengan nama berikut (atau unggah lewat tombol 'Kirim video' di dashboard):\n"
        f"   {target.resolve()}\n"
        f"{inbox_note}"
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


def job_files(prompt_dir: str | Path, download_dir: str | Path, job_id: int, segment_count: int) -> list[Path]:
    """Semua file yang terkait job (prompt, marker state, video segmen) untuk dibersihkan."""
    files: list[Path] = []
    for index in range(1, max(segment_count, 1) + 1):
        files.append(prompt_path(prompt_dir, job_id, index))
        files.extend(candidate_video_paths(download_dir, job_id, index))
    files.extend(Path(prompt_dir).glob(f"job_{job_id}_segment_*.json"))
    return files
