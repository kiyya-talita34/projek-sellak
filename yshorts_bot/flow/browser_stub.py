from __future__ import annotations

from pathlib import Path
from .base import FlowProvider
from ..models import SegmentPrompt


class BrowserFlowProvider(FlowProvider):
    """Kerangka otomasi browser Google Flow.

    Sengaja belum diimplementasikan karena harus diverifikasi terhadap Terms of Service Google Flow.
    Jangan mengisi class ini dengan bypass CAPTCHA, bypass login, bypass limit, atau scraping yang melanggar aturan layanan.
    """

    def request_segment(self, job_id: int, niche: str, segment: SegmentPrompt) -> Path:
        raise NotImplementedError(
            "Browser automation Google Flow belum diaktifkan. Gunakan provider 'manual' atau implementasikan hanya jika sesuai ToS."
        )
