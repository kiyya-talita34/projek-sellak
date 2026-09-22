from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..models import SegmentPrompt


class FlowProvider(ABC):
    """Antarmuka sumber video segmen (Google Flow manual, otomasi browser, Veo API, ...).

    `request_segment` harus NON-BLOCKING: kembalikan path video bila sudah tersedia,
    atau `None` bila masih menunggu. Worker akan memanggil lagi setelah `poll_seconds`,
    sehingga job lain tetap bisa diproses sementara satu segmen menunggu.
    """

    name: str = "base"
    description: str = ""

    @abstractmethod
    def request_segment(self, job_id: int, niche: str, segment: SegmentPrompt) -> Path | None:
        raise NotImplementedError
