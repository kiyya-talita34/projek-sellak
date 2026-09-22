from __future__ import annotations

import logging
from pathlib import Path

from ..models import SegmentPrompt
from .base import FlowProvider
from .prompt_files import expected_video_path, find_ready_video, write_prompt_file

log = logging.getLogger(__name__)


class ManualFlowProvider(FlowProvider):
    """Mode aman & sesuai ToS: program menyiapkan prompt, Anda generate di Google Flow, hasilnya
    diletakkan ke folder unduhan (atau diunggah lewat dashboard). Tidak memblokir worker.

    File yang ditunggu: flow_download_dir/job_<id>_segment_<index>.mp4
    """

    name = "manual"
    description = "Manual/assisted: salin prompt ke Google Flow, unggah hasil MP4 lewat dashboard"

    def __init__(
        self,
        prompt_dir: str,
        flow_download_dir: str,
        min_video_bytes: int = 1024,
        stable_seconds: float = 3.0,
        **_ignored: object,
    ):
        self.prompt_dir = Path(prompt_dir)
        self.flow_download_dir = Path(flow_download_dir)
        self.min_video_bytes = min_video_bytes
        self.stable_seconds = stable_seconds
        self.prompt_dir.mkdir(parents=True, exist_ok=True)
        self.flow_download_dir.mkdir(parents=True, exist_ok=True)
        self._announced: set[tuple[int, int]] = set()

    def request_segment(self, job_id: int, niche: str, segment: SegmentPrompt) -> Path | None:
        prompt_file = write_prompt_file(self.prompt_dir, self.flow_download_dir, job_id, niche, segment)
        ready = find_ready_video(self.flow_download_dir, job_id, segment.index, self.min_video_bytes, self.stable_seconds)
        if ready:
            log.info("Job #%s segmen %s: video ditemukan %s (%s bytes)", job_id, segment.index, ready, ready.stat().st_size)
            self._announced.discard((job_id, segment.index))
            return ready
        if (job_id, segment.index) not in self._announced:
            self._announced.add((job_id, segment.index))
            log.info(
                "Job #%s segmen %s: menunggu video Google Flow. Prompt: %s | Simpan hasil ke: %s",
                job_id, segment.index, prompt_file, expected_video_path(self.flow_download_dir, job_id, segment.index),
            )
        return None
