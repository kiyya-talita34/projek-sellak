from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from ..models import SegmentPrompt


class FlowProvider(ABC):
    @abstractmethod
    def request_segment(self, job_id: int, niche: str, segment: SegmentPrompt) -> Path:
        """Submit atau siapkan prompt segmen. Return path file video saat tersedia."""
        raise NotImplementedError
