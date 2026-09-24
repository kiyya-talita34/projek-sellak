from __future__ import annotations

import os
import time
from pathlib import Path

from yshorts_bot.flow.prompt_files import (
    candidate_video_paths,
    expected_video_path,
    file_is_stable,
    find_ready_video,
    is_partial_download,
    is_video_file,
    job_files,
    list_inbox_files,
    write_prompt_file,
)
from yshorts_bot.models import SegmentPrompt


def _write(path: Path, size: int, age_seconds: float = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if age_seconds:
        old = time.time() - age_seconds
        os.utime(path, (old, old))
    return path


def test_partial_download_and_video_detection(tmp_path):
    assert is_partial_download(Path("a.mp4.crdownload"))
    assert is_partial_download(Path("a.part"))
    assert is_partial_download(Path(".hidden.mp4"))
    assert not is_partial_download(Path("job_1_segment_1.mp4"))
    good = _write(tmp_path / "ok.mp4", 10)
    assert is_video_file(good)
    assert not is_video_file(_write(tmp_path / "doc.txt", 10))
    assert not is_video_file(_write(tmp_path / "x.mp4.crdownload", 10))


def test_file_is_stable_requires_two_observations_and_old_mtime(tmp_path):
    f = _write(tmp_path / "v.mp4", 5000, age_seconds=60)
    now = time.time()
    assert not file_is_stable(f, stable_seconds=3, now=now)  # observasi pertama: catat ukuran
    assert not file_is_stable(f, stable_seconds=3, now=now + 1)  # belum 3 detik
    assert file_is_stable(f, stable_seconds=3, now=now + 4)  # ukuran tetap & mtime lama -> siap
    # ukuran berubah -> hitung ulang
    f.write_bytes(b"y" * 9000)
    os.utime(f, (now - 60, now - 60))
    assert not file_is_stable(f, stable_seconds=3, now=now + 5)
    assert file_is_stable(f, stable_seconds=3, now=now + 9)


def test_file_is_stable_rejects_fresh_mtime_and_small_files(tmp_path):
    fresh = _write(tmp_path / "fresh.mp4", 5000)  # mtime = sekarang (masih ditulis)
    now = time.time()
    assert not file_is_stable(fresh, stable_seconds=3, now=now)  # observasi pertama
    assert not file_is_stable(fresh, stable_seconds=3, now=now + 1)  # ukuran tetap tetapi mtime baru 1 detik
    assert file_is_stable(fresh, stable_seconds=3, now=now + 4)  # 4 detik tanpa perubahan -> siap
    small = _write(tmp_path / "small.mp4", 10, age_seconds=60)
    assert not file_is_stable(small, min_bytes=1024, stable_seconds=0)
    assert file_is_stable(_write(tmp_path / "zero.mp4", 5000), stable_seconds=0)


def test_find_ready_video_accepts_alternate_names(tmp_path):
    _write(tmp_path / "job_7_seg2.mov", 5000, age_seconds=30)
    assert find_ready_video(tmp_path, 7, 2, stable_seconds=0) == tmp_path / "job_7_seg2.mov"
    assert find_ready_video(tmp_path, 7, 1, stable_seconds=0) is None
    assert expected_video_path(tmp_path, 7, 1) == tmp_path / "job_7_segment_1.mp4"
    assert (tmp_path / "7" / "segment_1.mp4") in candidate_video_paths(tmp_path, 7, 1)


def test_list_inbox_files_sorted_and_filtered(tmp_path):
    inbox = tmp_path / "inbox"
    _write(inbox / "b.mp4", 5000, age_seconds=10)
    _write(inbox / "a.mp4", 5000, age_seconds=50)
    _write(inbox / "c.mp4.crdownload", 5000, age_seconds=50)
    _write(inbox / "notes.txt", 5000, age_seconds=50)
    names = [p.name for p in list_inbox_files(inbox, stable_seconds=0)]
    assert names == ["a.mp4", "b.mp4"]
    assert list_inbox_files(tmp_path / "tidak_ada") == []


def test_write_prompt_file_once_and_job_files(tmp_path):
    seg = SegmentPrompt(index=1, prompt="Prompt uji", duration_seconds=8)
    p = write_prompt_file(tmp_path / "prompts", tmp_path / "dl", 3, "niche", seg, inbox_dir=tmp_path / "inbox")
    text = p.read_text(encoding="utf-8")
    assert "Prompt uji" in text and "inbox" in text
    p.write_text("diubah", encoding="utf-8")
    write_prompt_file(tmp_path / "prompts", tmp_path / "dl", 3, "niche", seg)  # tidak menimpa
    assert p.read_text(encoding="utf-8") == "diubah"
    write_prompt_file(tmp_path / "prompts", tmp_path / "dl", 3, "niche", seg, overwrite=True)
    assert "Prompt uji" in p.read_text(encoding="utf-8")
    files = job_files(tmp_path / "prompts", tmp_path / "dl", 3, 2)
    assert p in files and expected_video_path(tmp_path / "dl", 3, 2) in files
