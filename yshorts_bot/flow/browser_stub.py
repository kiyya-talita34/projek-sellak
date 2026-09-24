from __future__ import annotations

from pathlib import Path

from ..models import SegmentPrompt
from .base import FlowProvider


class BrowserFlowProvider(FlowProvider):
    """Kerangka kosong untuk integrasi lain di masa depan.

    Jangan mengisi class ini dengan bypass CAPTCHA, bypass login, bypass limit,
    atau scraping yang melanggar aturan layanan. Untuk otomasi browser yang sudah ada,
    gunakan provider 'browser' (PlaywrightFlowProvider); untuk jalur resmi gunakan 'veo_api'.
    """

    name = "browser_stub"
    description = "Placeholder (tidak diimplementasikan)"

    def request_segment(self, job_id: int, niche: str, segment: SegmentPrompt) -> Path | None:
        raise NotImplementedError(
            "Provider 'browser_stub' hanya placeholder. Gunakan flow.provider = 'manual', 'browser', atau 'veo_api'."
        )
