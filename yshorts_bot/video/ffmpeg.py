from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..config import VideoConfig

log = logging.getLogger(__name__)

_FFMPEG_CACHE: str | None = None


class FFmpegError(RuntimeError):
    """FFmpeg keluar dengan kode error; pesan berisi ekor log FFmpeg."""


def resolve_ffmpeg(cfg: VideoConfig | None = None) -> str:
    """Cari binary ffmpeg: config -> env FFMPEG_BINARY -> PATH -> paket imageio-ffmpeg."""
    global _FFMPEG_CACHE
    configured = cfg.ffmpeg_binary if cfg else None
    if configured:
        if Path(configured).exists():
            return str(configured)
        raise FileNotFoundError(f"video.ffmpeg_binary menunjuk ke file yang tidak ada: {configured}")
    if _FFMPEG_CACHE and Path(_FFMPEG_CACHE).exists():
        return _FFMPEG_CACHE
    env_bin = os.getenv("FFMPEG_BINARY")
    if env_bin and Path(env_bin).exists():
        _FFMPEG_CACHE = env_bin
        return env_bin
    found = shutil.which("ffmpeg")
    if found:
        _FFMPEG_CACHE = found
        return found
    try:
        import imageio_ffmpeg  # type: ignore

        _FFMPEG_CACHE = imageio_ffmpeg.get_ffmpeg_exe()
        return _FFMPEG_CACHE
    except Exception as e:  # pragma: no cover - tergantung environment
        raise FileNotFoundError(
            "ffmpeg tidak ditemukan. Install FFmpeg (https://ffmpeg.org/download.html) dan pastikan ada di PATH, "
            "atau jalankan `pip install imageio-ffmpeg`, atau isi video.ffmpeg_binary di config.json."
        ) from e


def run_ffmpeg(args: list[str], ffmpeg: str | None = None) -> str:
    cmd = [ffmpeg or resolve_ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error", *args]
    log.debug("ffmpeg: %s", " ".join(cmd))
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = (proc.stdout or "").strip()[-1500:]
        raise FFmpegError(f"ffmpeg gagal (exit {proc.returncode}): {tail}")
    return proc.stdout or ""


@dataclass
class MediaInfo:
    duration: float = 0.0
    width: int = 0
    height: int = 0
    has_video: bool = False
    has_audio: bool = False


def probe(path: str | Path, ffmpeg: str | None = None) -> MediaInfo:
    """Baca durasi/resolusi/stream dari `ffmpeg -i` (tidak butuh ffprobe, yang tidak ikut di imageio-ffmpeg)."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"File video tidak ditemukan: {p}")
    cmd = [ffmpeg or resolve_ffmpeg(), "-hide_banner", "-nostdin", "-i", str(p)]
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
    out = proc.stdout or ""
    info = MediaInfo()
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out)
    if m:
        h, mi, s = m.groups()
        info.duration = int(h) * 3600 + int(mi) * 60 + float(s)
    for line in out.splitlines():
        if "Stream #" not in line:
            continue
        if re.search(r":\s*Video:", line):
            info.has_video = True
            dims = re.search(r"\b(\d{2,5})x(\d{2,5})\b", line.split("Video:", 1)[1])
            if dims and not info.width:
                info.width, info.height = int(dims.group(1)), int(dims.group(2))
        elif re.search(r":\s*Audio:", line):
            info.has_audio = True
    if not info.has_video and not info.has_audio:
        raise FFmpegError(f"File bukan media yang valid: {p} -> {out.strip()[-300:]}")
    return info


def has_audio_stream(path: str | Path) -> bool:
    return probe(path).has_audio


def get_video_duration(path: str | Path) -> float:
    return probe(path).duration


def make_test_clip(
    dest: str | Path,
    seconds: float = 8.0,
    size: str = "720x1280",
    hue_shift: int = 0,
    with_audio: bool = False,
    ffmpeg: str | None = None,
) -> Path:
    """Buat klip uji (pola testsrc) untuk mensimulasikan hasil Google Flow."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part.mp4")
    args = ["-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30"]
    if with_audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
    args += ["-t", f"{seconds:.3f}", "-vf", f"hue=h={hue_shift},format=yuv420p", "-c:v", "libx264", "-preset", "ultrafast"]
    if with_audio:
        args += ["-c:a", "aac", "-shortest"]
    args += ["-movflags", "+faststart", str(tmp)]
    run_ffmpeg(args, ffmpeg)
    os.replace(tmp, dest)
    return dest


# ---------------------------------------------------------------------------
# Normalisasi + penggabungan
# ---------------------------------------------------------------------------
def _fit_filter(cfg: VideoConfig) -> str:
    w, h = cfg.width, cfg.height
    if cfg.fit_mode == "pad":
        return f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"


def _normalize_segment(src: Path, dst: Path, cfg: VideoConfig, ffmpeg: str, info: MediaInfo, extra_tail: float) -> None:
    w, h = cfg.width, cfg.height
    tail = f",tpad=stop_mode=clone:stop_duration={extra_tail:.3f}" if extra_tail > 0.05 else ""
    common_tail = f"fps={cfg.fps},format=yuv420p{tail}"

    args: list[str] = ["-y", "-i", str(src)]
    if not info.has_audio:
        args += ["-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate={cfg.audio_sample_rate}"]

    if cfg.fit_mode == "blur":
        chain = (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=luma_radius=30:luma_power=2[bgb];"
            f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,{common_tail}[v]"
        )
        args += ["-filter_complex", chain, "-map", "[v]"]
    else:
        args += ["-vf", f"{_fit_filter(cfg)},{common_tail}", "-map", "0:v:0"]

    if info.has_audio:
        args += ["-map", "0:a:0"]
        if extra_tail > 0.05:
            args += ["-af", f"apad=pad_dur={extra_tail:.3f}"]
    else:
        args += ["-map", "1:a:0", "-shortest"]

    args += [
        "-c:v", "libx264", "-preset", cfg.x264_preset, "-b:v", cfg.video_bitrate, "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", str(cfg.audio_sample_rate), "-ac", "2", "-b:a", cfg.audio_bitrate,
        "-movflags", "+faststart",
        str(dst),
    ]
    run_ffmpeg(args, ffmpeg)


def merge_for_shorts(segment_paths: list[str | Path], output_path: str | Path, cfg: VideoConfig) -> Path:
    """Normalisasi setiap segmen ke 9:16 (default 1080x1920, H.264 + AAC) lalu gabungkan menjadi satu MP4.

    - Segmen tanpa audio diberi track audio hening (YouTube lebih stabil memproses file ber-audio).
    - Bila total durasi < video.min_duration_seconds dan pad_to_min_duration aktif, frame terakhir
      diperpanjang (tpad) supaya durasi minimal tercapai.
    - Ditulis ke file sementara lalu di-rename atomik ke output akhir.
    """
    if not segment_paths:
        raise ValueError("Tidak ada segmen video untuk digabung.")
    ffmpeg = resolve_ffmpeg(cfg)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"tmp_{output.stem}_", dir=output.parent))
    try:
        sources = [Path(p) for p in segment_paths]
        infos = [probe(p, ffmpeg) for p in sources]
        for src, info in zip(sources, infos):
            if not info.has_video or info.duration <= 0:
                raise FFmpegError(f"Segmen tidak memiliki stream video yang valid: {src}")
        total = sum(i.duration for i in infos)
        deficit = max(0.0, cfg.min_duration_seconds - total) if cfg.pad_to_min_duration else 0.0
        if deficit > 0.05:
            log.info("Total durasi segmen %.2fs < %.0fs; frame akhir diperpanjang %.2fs.", total, cfg.min_duration_seconds, deficit)

        normalized: list[Path] = []
        for i, (src, info) in enumerate(zip(sources, infos), start=1):
            dst = tmp_dir / f"norm_{i}.mp4"
            extra = deficit if i == len(sources) else 0.0
            log.info("Normalisasi segmen %s/%s: %s (%.1fs, %sx%s, audio=%s)", i, len(sources), src.name, info.duration, info.width, info.height, info.has_audio)
            _normalize_segment(src, dst, cfg, ffmpeg, info, extra)
            normalized.append(dst)

        concat_file = tmp_dir / "concat.txt"
        lines = []
        for p in normalized:
            escaped = p.resolve().as_posix().replace("'", "'\\''")
            lines.append(f"file '{escaped}'\n")
        concat_file.write_text("".join(lines), encoding="utf-8")

        tmp_out = tmp_dir / f"{output.stem}.merged.mp4"
        run_ffmpeg(["-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", "-movflags", "+faststart", str(tmp_out)], ffmpeg)

        result = probe(tmp_out, ffmpeg)
        if not result.has_video or result.duration <= 0:
            raise FFmpegError("Hasil penggabungan tidak valid (tidak ada stream video).")
        if (result.width, result.height) != (cfg.width, cfg.height):
            raise FFmpegError(f"Resolusi hasil {result.width}x{result.height} tidak sesuai target {cfg.width}x{cfg.height}.")
        if result.duration + 0.5 < cfg.min_duration_seconds:
            log.warning("Durasi output %.2fs masih di bawah target %.0fs.", result.duration, cfg.min_duration_seconds)

        os.replace(tmp_out, output)
        log.info("Output Shorts siap: %s (%.2fs, %sx%s)", output, result.duration, result.width, result.height)
        return output
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
