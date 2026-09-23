from __future__ import annotations

import pytest

from tests.conftest import requires_ffmpeg
from yshorts_bot.config import VideoConfig
from yshorts_bot.video.ffmpeg import (
    FFmpegError,
    make_test_audio,
    make_test_clip,
    merge_for_shorts,
    pick_background_music,
    probe,
    resolve_ffmpeg,
)

pytestmark = requires_ffmpeg

FAST = dict(width=540, height=960, x264_preset="ultrafast", video_bitrate="800k", background_music_mode="off")


def test_resolve_ffmpeg_finds_binary():
    assert resolve_ffmpeg()


def test_probe_reports_streams(tmp_path):
    silent = make_test_clip(tmp_path / "silent.mp4", seconds=1.0, size="320x640")
    with_audio = make_test_clip(tmp_path / "audio.mp4", seconds=1.0, size="320x640", with_audio=True)
    info = probe(silent)
    assert info.has_video and not info.has_audio and (info.width, info.height) == (320, 640)
    assert 0.9 <= info.duration <= 1.3
    assert probe(with_audio).has_audio
    assert not list(tmp_path.glob("*.part*"))
    audio = make_test_audio(tmp_path / "tone.wav", seconds=2.0)
    ainfo = probe(audio)
    assert ainfo.has_audio and not ainfo.has_video and 1.8 <= ainfo.duration <= 2.3


def test_merge_two_segments_to_vertical_with_audio(tmp_path):
    seg1 = make_test_clip(tmp_path / "s1.mp4", seconds=2.0, size="720x1280")
    seg2 = make_test_clip(tmp_path / "s2.mp4", seconds=2.0, size="720x1280", hue_shift=120, with_audio=True)
    cfg = VideoConfig(min_duration_seconds=3, **FAST)
    out = merge_for_shorts([seg1, seg2], tmp_path / "out" / "short.mp4", cfg)
    info = probe(out)
    assert info.has_video and info.has_audio
    assert (info.width, info.height) == (540, 960)
    assert info.duration >= 3.8
    assert not list((tmp_path / "out").glob("tmp_*"))


def test_merge_pads_to_minimum_duration(tmp_path):
    seg1 = make_test_clip(tmp_path / "s1.mp4", seconds=1.5, size="720x1280")
    seg2 = make_test_clip(tmp_path / "s2.mp4", seconds=1.5, size="720x1280", with_audio=True)
    cfg = VideoConfig(min_duration_seconds=5, pad_to_min_duration=True, **FAST)
    out = merge_for_shorts([seg1, seg2], tmp_path / "padded.mp4", cfg)
    assert probe(out).duration >= 4.8


@pytest.mark.parametrize("fit_mode", ["crop", "pad", "blur"])
def test_merge_fit_modes_from_landscape_source(tmp_path, fit_mode):
    seg1 = make_test_clip(tmp_path / "l1.mp4", seconds=1.0, size="640x360")
    seg2 = make_test_clip(tmp_path / "l2.mp4", seconds=1.0, size="640x360", hue_shift=60)
    cfg = VideoConfig(min_duration_seconds=0, fit_mode=fit_mode, **FAST)
    out = merge_for_shorts([seg1, seg2], tmp_path / f"{fit_mode}.mp4", cfg)
    info = probe(out)
    assert (info.width, info.height) == (540, 960) and info.has_audio


def test_merge_single_segment_allowed(tmp_path):
    seg = make_test_clip(tmp_path / "one.mp4", seconds=1.0, size="360x640")
    out = merge_for_shorts([seg], tmp_path / "one_out.mp4", VideoConfig(min_duration_seconds=0, **FAST))
    assert probe(out).duration >= 0.9


def test_merge_rejects_missing_or_invalid_files(tmp_path):
    with pytest.raises(FileNotFoundError):
        merge_for_shorts([tmp_path / "missing.mp4"], tmp_path / "x.mp4", VideoConfig(**FAST))
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video" * 200)
    with pytest.raises(FFmpegError):
        merge_for_shorts([bad], tmp_path / "y.mp4", VideoConfig(**FAST))
    with pytest.raises(ValueError):
        merge_for_shorts([], tmp_path / "z.mp4", VideoConfig(**FAST))


def test_background_music_modes(tmp_path):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    (music_dir / "readme.txt").write_text("bukan audio", encoding="utf-8")
    assert pick_background_music(music_dir) is None
    assert pick_background_music(None) is None
    assert pick_background_music(tmp_path / "tidak_ada") is None
    make_test_audio(music_dir / "lagu.wav", seconds=1.0)  # lebih pendek dari video -> di-loop
    assert pick_background_music(music_dir) == music_dir / "lagu.wav"

    silent1 = make_test_clip(tmp_path / "q1.mp4", seconds=1.5, size="360x640")
    silent2 = make_test_clip(tmp_path / "q2.mp4", seconds=1.5, size="360x640", hue_shift=90)
    voiced = make_test_clip(tmp_path / "v1.mp4", seconds=1.5, size="360x640", with_audio=True)
    base = dict(width=360, height=640, x264_preset="ultrafast", video_bitrate="500k", min_duration_seconds=0,
                background_music_dir=str(music_dir), background_music_volume=0.3)

    out_auto = merge_for_shorts([silent1, silent2], tmp_path / "auto.mp4", VideoConfig(background_music_mode="auto", **base))
    info = probe(out_auto)
    assert info.has_audio and info.has_video and 2.7 <= info.duration <= 3.4

    out_off = merge_for_shorts([silent1, silent2], tmp_path / "off.mp4", VideoConfig(background_music_mode="off", **base))
    assert probe(out_off).has_audio  # tetap ada track hening

    out_always = merge_for_shorts([voiced, silent2], tmp_path / "always.mp4", VideoConfig(background_music_mode="always", **base))
    assert probe(out_always).has_audio

    explicit = merge_for_shorts([voiced], tmp_path / "explicit.mp4", VideoConfig(background_music_mode="off", **base), music_path=music_dir / "lagu.wav")
    assert probe(explicit).has_audio
    with pytest.raises(FileNotFoundError):
        merge_for_shorts([voiced], tmp_path / "x.mp4", VideoConfig(background_music_mode="off", **base), music_path=music_dir / "hilang.mp3")
