from __future__ import annotations

import logging
import time
from pathlib import Path

from .base import FlowProvider
from ..models import SegmentPrompt

log = logging.getLogger(__name__)


class ManualFlowProvider(FlowProvider):
    """Adapter aman: membuat prompt, lalu menunggu file hasil download Google Flow.

    File yang ditunggu:
      flow_download_dir/job_<id>_segment_<index>.mp4
    """

    def __init__(self, prompt_dir: str, flow_download_dir: str, wait_timeout_minutes: int = 1440, poll_seconds: int = 10):
        self.prompt_dir = Path(prompt_dir)
        self.flow_download_dir = Path(flow_download_dir)
        self.wait_timeout = wait_timeout_minutes * 60
        self.poll_seconds = poll_seconds
        self.prompt_dir.mkdir(parents=True, exist_ok=True)
        self.flow_download_dir.mkdir(parents=True, exist_ok=True)

    def request_segment(self, job_id: int, niche: str, segment: SegmentPrompt) -> Path:
        prompt_path = self.prompt_dir / f"job_{job_id}_segment_{segment.index}.txt"
        primary_video = self.flow_download_dir / f"job_{job_id}_segment_{segment.index}.mp4"
        candidate_videos = [
            primary_video,
            self.flow_download_dir / f"job_{job_id}_seg{segment.index}.mp4",
            self.flow_download_dir / f"job_{job_id}_seg_{segment.index}.mp4",
            self.flow_download_dir / str(job_id) / f"segment_{segment.index}.mp4",
        ]
        
        # Simpan prompt bersih untuk copy-paste langsung ke Google Flow
        prompt_path.write_text(
            f"=== GOOGLE FLOW PROMPT - JOB #{job_id} SEGMENT {segment.index} ===\n"
            f"Niche: {niche}\n"
            f"Durasi Target: {segment.duration_seconds} detik (Vertical 9:16)\n\n"
            f"--- PROMPT (Salin teks di bawah ini ke Google Flow) ---\n"
            f"{segment.prompt}\n\n"
            f"--- INSTRUKSI ---\n"
            f"1. Salin prompt di atas ke Google Flow Ultra.\n"
            f"2. Generate video dan download file MP4 hasilnya.\n"
            f"3. Simpan file video hasil download ke:\n"
            f"   {primary_video.resolve()}\n"
            f"   (atau upload langsung via Web Dashboard)\n",
            encoding="utf-8",
        )
        log.info("Prompt segmen %s disimpan: %s", segment.index, prompt_path)
        log.info("Menunggu video di: %s", primary_video)
        start = time.time()
        while time.time() - start < self.wait_timeout:
            for candidate in candidate_videos:
                if candidate.exists() and candidate.stat().st_size > 1024:
                    log.info("Video ditemukan: %s (ukuran: %s bytes)", candidate, candidate.stat().st_size)
                    return candidate
            time.sleep(self.poll_seconds)
        raise TimeoutError(f"Timeout menunggu file {primary_video}. Generate/download dari Google Flow lalu simpan dengan nama tersebut.")

