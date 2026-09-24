from __future__ import annotations

import logging
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import VideoConfig

log = logging.getLogger(__name__)

_FFMPEG_CACHE: str | None = None
AUDIO_EXTENSIONS = (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac", ".opus")


class FFmpegError(RuntimeError):
    """FFmpeg keluar dengan kode error; pesan berisi ekor log FFmpeg."""


def resolve_ffmpeg(cfg: VideoConfig | None = None) -> str:
    """Cari binary ffmpeg: config -> env FFMPEG_BINARY -> PATH -> paket imageio-ffmpeg."""
    global _FFMPEG_CACHE
    configured = cfg.ffmpeg_binary if cfg else None
    if configured:
        if Path(configured).exists():
            return str(configured)
        found_configured = shutil.which(configured)
        if found_configured:
            return found_configured
        raise FileNotFoundError(f"video.ffmpeg_binary tidak ditemukan (bukan file dan tidak ada di PATH): {configured}")
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


def replace_with_retry(src: Path, dst: Path, attempts: int = 6, delay: float = 0.5) -> None:
    """os.replace dengan pengulangan: di Windows rename gagal (PermissionError) bila file tujuan sedang dibuka."""
    for attempt in range(1, attempts + 1):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts:
                raise
            time.sleep(delay * attempt)


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
            # Gambar sampul (cover art) mp3/m4a juga terdeteksi sebagai Video (attached pic); abaikan.
            if "attached pic" in line:
                continue
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
    replace_with_retry(tmp, dest)
    return dest


def make_test_audio(dest: str | Path, seconds: float = 10.0, frequency: int = 220, ffmpeg: str | None = None) -> Path:
    """Buat file audio uji (nada sinus) untuk musik latar."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part" + dest.suffix)
    codec = ["-c:a", "aac"] if dest.suffix.lower() in (".m4a", ".aac", ".mp4") else ["-c:a", "pcm_s16le"]
    run_ffmpeg(["-y", "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000", "-t", f"{seconds:.3f}", *codec, str(tmp)], ffmpeg)
    replace_with_retry(tmp, dest)
    return dest


# ---------------------------------------------------------------------------
# Musik latar
# ---------------------------------------------------------------------------
def pick_background_music(music_dir: str | Path | None, rng: random.Random | None = None) -> Path | None:
    """Pilih satu file audio secara acak dari folder musik (None bila kosong/tidak ada)."""
    if not music_dir:
        return None
    base = Path(music_dir)
    if not base.is_dir():
        return None
    files = [p for p in base.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS and p.stat().st_size > 1024]
    if not files:
        return None
    return (rng or random).choice(sorted(files))


def mix_background_music(video_in: Path, music: Path, video_out: Path, cfg: VideoConfig, duration: float, ffmpeg: str) -> None:
    """Campurkan musik (di-loop, volume rendah, fade out) di bawah audio video. Video tidak di-encode ulang."""
    fade = min(cfg.background_music_fade_seconds, max(duration / 2, 0))
    music_chain = f"[1:a]volume={cfg.background_music_volume:.3f}"
    if fade > 0.05 and duration > fade:
        music_chain += f",afade=t=out:st={duration - fade:.3f}:d={fade:.3f}"
    music_chain += "[m]"
    filter_complex = f"{music_chain};[0:a][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
    run_ffmpeg(
        [
            "-y", "-i", str(video_in), "-stream_loop", "-1", "-i", str(music),
            "-filter_complex", filter_complex,
            "-map", "0:v:0", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-ar", str(cfg.audio_sample_rate), "-ac", "2", "-b:a", cfg.audio_bitrate,
            "-t", f"{duration:.3f}", "-movflags", "+faststart",
            str(video_out),
        ],
        ffmpeg,
    )


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


def merge_for_shorts(
    segment_paths: list[str | Path],
    output_path: str | Path,
    cfg: VideoConfig,
    music_path: str | Path | None = None,
) -> Path:
    """Normalisasi setiap segmen ke 9:16 (default 1080x1920, H.264 + AAC) lalu gabungkan menjadi satu MP4.

    - Segmen tanpa audio diberi track audio hening (YouTube lebih stabil memproses file ber-audio).
    - Bila total durasi < video.min_duration_seconds dan pad_to_min_duration aktif, frame terakhir
      diperpanjang (tpad) supaya durasi minimal tercapai.
    - Musik latar (video.background_music_*) dicampur setelah penggabungan; `music_path` memaksa file tertentu.
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
            if not info.has_video:
                raise FFmpegError(f"Segmen tidak memiliki stream video yang valid: {src}")
        # Durasi bisa "N/A" (WebM/fragmented MP4) -> 0; segmen seperti itu tetap diproses, padding dicek lagi setelah concat.
        known_total = sum(i.duration for i in infos)
        durations_known = all(i.duration > 0 for i in infos)
        deficit = max(0.0, cfg.min_duration_seconds - known_total) if (cfg.pad_to_min_duration and durations_known) else 0.0
        if deficit > 0.05:
            log.info("Total durasi segmen %.2fs < %.0fs; frame akhir diperpanjang %.2fs.", known_total, cfg.min_duration_seconds, deficit)

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

        merged = tmp_dir / f"{output.stem}.merged.mp4"
        run_ffmpeg(["-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", "-movflags", "+faststart", str(merged)], ffmpeg)

        result = probe(merged, ffmpeg)
        if not result.has_video or result.duration <= 0:
            raise FFmpegError("Hasil penggabungan tidak valid (tidak ada stream video).")
        if (result.width, result.height) != (cfg.width, cfg.height):
            raise FFmpegError(f"Resolusi hasil {result.width}x{result.height} tidak sesuai target {cfg.width}x{cfg.height}.")
        late_deficit = cfg.min_duration_seconds - result.duration
        if cfg.pad_to_min_duration and late_deficit > 0.25:
            # Durasi sumber tadi tidak diketahui -> perpanjang frame akhir pada hasil gabungan (encode ulang sekali).
            log.info("Durasi gabungan %.2fs < %.0fs; frame akhir diperpanjang %.2fs.", result.duration, cfg.min_duration_seconds, late_deficit)
            padded = tmp_dir / f"{output.stem}.padded.mp4"
            run_ffmpeg(
                [
                    "-y", "-i", str(merged),
                    "-vf", f"tpad=stop_mode=clone:stop_duration={late_deficit:.3f}", "-af", f"apad=pad_dur={late_deficit:.3f}",
                    "-c:v", "libx264", "-preset", cfg.x264_preset, "-b:v", cfg.video_bitrate, "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ar", str(cfg.audio_sample_rate), "-ac", "2", "-b:a", cfg.audio_bitrate,
                    "-movflags", "+faststart", str(padded),
                ],
                ffmpeg,
            )
            merged = padded
            result = probe(merged, ffmpeg)
        elif result.duration + 0.5 < cfg.min_duration_seconds:
            log.warning("Durasi output %.2fs masih di bawah target %.0fs.", result.duration, cfg.min_duration_seconds)

        # Musik latar
        final = merged
        music: Path | None = Path(music_path) if music_path else None
        if music is None and cfg.background_music_mode != "off":
            any_audio = any(i.has_audio for i in infos)
            if cfg.background_music_mode == "always" or not any_audio:
                music = pick_background_music(cfg.background_music_dir)
        if music is not None:
            if not music.is_file():
                raise FileNotFoundError(f"File musik tidak ditemukan: {music}")
            with_music = tmp_dir / f"{output.stem}.music.mp4"
            log.info("Menambahkan musik latar %s (volume %.2f)", music.name, cfg.background_music_volume)
            mix_background_music(merged, music, with_music, cfg, result.duration, ffmpeg)
            check = probe(with_music, ffmpeg)
            if not check.has_video or not check.has_audio:
                raise FFmpegError("Hasil pencampuran musik tidak valid.")
            final = with_music

        replace_with_retry(final, output)
        log.info("Output Shorts siap: %s (%.2fs, %sx%s%s)", output, result.duration, result.width, result.height, ", +musik" if music else "")
        return output
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
