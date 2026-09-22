from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from ..config import VideoConfig

log = logging.getLogger(__name__)


def run(cmd: list[str]) -> None:
    log.debug("Running: %s", " ".join(cmd))
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        raise RuntimeError(proc.stdout)


import re

def get_media_info(path: str | Path) -> tuple[float, bool]:
    """Mengambil durasi (detik) dan mengecek apakah file memiliki stream audio menggunakan ffmpeg."""
    proc = subprocess.run(["ffmpeg", "-i", str(path)], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = proc.stdout
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", out)
    if match:
        h, m, s = match.groups()
        duration = int(h) * 3600 + int(m) * 60 + float(s)
    else:
        duration = 0.0
    has_audio = bool(re.search(r"Stream #\d+:\d+.*Audio:", out, re.IGNORECASE))
    return duration, has_audio


def has_audio_stream(path: str | Path) -> bool:
    _, has_audio = get_media_info(path)
    return has_audio


def get_video_duration(path: str | Path) -> float:
    duration, _ = get_media_info(path)
    return duration



def merge_for_shorts(segment_paths: list[str | Path], output_path: str | Path, cfg: VideoConfig) -> Path:
    if len(segment_paths) < 2:
        raise ValueError("Butuh minimal 2 video segmen untuk Shorts ±16 detik.")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = output.parent / f"tmp_{output.stem}"
    tmp_dir.mkdir(exist_ok=True)
    normalized: list[Path] = []

    try:
        for i, src in enumerate(segment_paths, start=1):
            norm = tmp_dir / f"norm_{i}.mp4"
            vf = (
                f"scale={cfg.width}:{cfg.height}:force_original_aspect_ratio=increase,"
                f"crop={cfg.width}:{cfg.height},fps={cfg.fps},format=yuv420p"
            )
            has_audio = has_audio_stream(src)
            if has_audio:
                cmd = [
                    "ffmpeg", "-y", "-i", str(src),
                    "-vf", vf,
                    "-c:v", "libx264", "-preset", "medium", "-b:v", cfg.video_bitrate,
                    "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", cfg.audio_bitrate,
                    "-movflags", "+faststart",
                    str(norm),
                ]
            else:
                log.info("Segmen %s tidak memiliki track audio. Menyisipkan silent audio track.", src)
                cmd = [
                    "ffmpeg", "-y", "-i", str(src),
                    "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-vf", vf,
                    "-c:v", "libx264", "-preset", "medium", "-b:v", cfg.video_bitrate,
                    "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", cfg.audio_bitrate,
                    "-shortest",
                    "-movflags", "+faststart",
                    str(norm),
                ]
            run(cmd)
            normalized.append(norm)

        concat_file = tmp_dir / "concat.txt"
        concat_file.write_text("".join([f"file '{p.resolve().as_posix()}'\n" for p in normalized]), encoding="utf-8")


        run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-c", "copy", str(output)
        ])

        duration = get_video_duration(output)
        if duration < 15.0:
            log.warning("Durasi output %.2fs kurang dari target 16s", duration)
        log.info("Output Shorts dibuat: %s (%.2fs)", output, duration)
        return output

    finally:
        # Bersihkan file sementara
        for p in normalized:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        concat_txt = tmp_dir / "concat.txt"
        if concat_txt.exists():
            try:
                concat_txt.unlink(missing_ok=True)
                tmp_dir.rmdir()
            except Exception:
                pass

