"""Provider RESMI: Veo lewat Gemini API (Google AI Studio).

Google Flow sendiri tidak menyediakan API publik. Jalur resmi untuk membuat video Veo secara
programatik adalah Gemini API (`models/<veo-model>:predictLongRunning`) - berbayar per detik video
dan TERPISAH dari langganan Google AI Ultra / kredit Flow.

Dokumentasi: https://ai.google.dev/gemini-api/docs/veo
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from ..config import VeoApiConfig
from ..errors import NonRetryableError, RetryLaterError
from ..models import SegmentPrompt
from .base import FlowProvider
from .prompt_files import expected_video_path, find_ready_video, read_state, state_path, write_prompt_file, write_state

log = logging.getLogger(__name__)


class VeoApiFlowProvider(FlowProvider):
    name = "veo_api"
    description = "Resmi: Veo via Gemini API (berbayar per video, tidak memakai kredit Flow Ultra)"

    def __init__(self, prompt_dir: str, flow_download_dir: str, veo_cfg: VeoApiConfig, api_key: str | None = None):
        self.prompt_dir = Path(prompt_dir)
        self.flow_download_dir = Path(flow_download_dir)
        self.cfg = veo_cfg
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.base_url = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        if not self.api_key:
            raise NonRetryableError("flow.provider=veo_api membutuhkan GEMINI_API_KEY di .env (https://aistudio.google.com/apikey).")
        self.prompt_dir.mkdir(parents=True, exist_ok=True)
        self.flow_download_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ HTTP
    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.api_key or "", "Content-Type": "application/json"}

    def _check(self, response: requests.Response, what: str) -> dict[str, Any]:
        if response.status_code in (401, 403):
            raise NonRetryableError(f"Veo API menolak {what} (HTTP {response.status_code}): {response.text[:300]}")
        if response.status_code == 429:
            raise RetryLaterError(f"Veo API rate limit/kuota (HTTP 429): {response.text[:200]}", delay_seconds=180)
        if response.status_code == 400:
            raise NonRetryableError(f"Veo API: permintaan tidak valid (HTTP 400): {response.text[:300]}")
        response.raise_for_status()
        return response.json()

    def _submit(self, prompt: str) -> str:
        parameters: dict[str, Any] = {"aspectRatio": self.cfg.aspect_ratio, "resolution": self.cfg.resolution}
        if self.cfg.duration_seconds:
            parameters["durationSeconds"] = int(self.cfg.duration_seconds)
        if self.cfg.negative_prompt:
            parameters["negativePrompt"] = self.cfg.negative_prompt
        payload = {"instances": [{"prompt": prompt}], "parameters": parameters}
        url = f"{self.base_url}/models/{self.cfg.model}:predictLongRunning"
        data = self._check(requests.post(url, headers=self._headers(), json=payload, timeout=120), "pembuatan video")
        name = data.get("name")
        if not name:
            raise RuntimeError(f"Veo API tidak mengembalikan nama operation: {data}")
        return str(name)

    def _poll(self, operation: str) -> dict[str, Any]:
        url = f"{self.base_url}/{operation}"
        return self._check(requests.get(url, headers=self._headers(), timeout=60), "status operation")

    def _download(self, uri: str, dest: Path) -> None:
        tmp = dest.with_name(dest.name + ".part")
        with requests.get(uri, headers={"x-goog-api-key": self.api_key or ""}, stream=True, allow_redirects=True, timeout=600) as r:
            if r.status_code in (401, 403):
                raise NonRetryableError(f"Veo API menolak download (HTTP {r.status_code})")
            r.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        fh.write(chunk)
        os.replace(tmp, dest)

    @staticmethod
    def _extract_uri(data: dict[str, Any]) -> str:
        response = data.get("response") or {}
        gen = response.get("generateVideoResponse") or response
        samples = gen.get("generatedSamples") or gen.get("generatedVideos") or []
        if not samples:
            reasons = gen.get("raiMediaFilteredReasons") or []
            filtered = gen.get("raiMediaFilteredCount")
            raise RuntimeError(f"Veo tidak menghasilkan video (filtered={filtered}, alasan={reasons}). Ubah prompt lalu retry.")
        video = samples[0].get("video") or {}
        uri = video.get("uri")
        if not uri:
            raise RuntimeError(f"Veo API tidak mengembalikan URI video: {samples[0]}")
        return str(uri)

    # ------------------------------------------------------------------ API provider
    def request_segment(self, job_id: int, niche: str, segment: SegmentPrompt) -> Path | None:
        write_prompt_file(self.prompt_dir, self.flow_download_dir, job_id, niche, segment)
        ready = find_ready_video(self.flow_download_dir, job_id, segment.index, stable_seconds=0)
        if ready:
            return ready

        dest = expected_video_path(self.flow_download_dir, job_id, segment.index)
        marker = state_path(self.prompt_dir, job_id, segment.index, "veo")
        state = read_state(marker) or {}
        operation = state.get("operation")

        if not operation:
            operation = self._submit(segment.prompt)
            write_state(marker, {"operation": operation, "model": self.cfg.model, "submitted_at": datetime.now(timezone.utc).isoformat()})
            log.info("Job #%s segmen %s: permintaan Veo dikirim (%s). Menunggu render...", job_id, segment.index, operation)
            return None

        data = self._poll(operation)
        if not data.get("done"):
            submitted = state.get("submitted_at")
            try:
                elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(submitted)).total_seconds() if submitted else 0
            except ValueError:
                elapsed = 0
            if elapsed > self.cfg.timeout_seconds:
                marker.unlink(missing_ok=True)
                raise RuntimeError(f"Veo operation {operation} belum selesai setelah {elapsed:.0f}s; akan dikirim ulang saat retry.")
            return None

        if data.get("error"):
            marker.unlink(missing_ok=True)
            raise RuntimeError(f"Veo gagal: {data['error'].get('message', data['error'])}")

        uri = self._extract_uri(data)
        self._download(uri, dest)
        write_state(marker, {**state, "downloaded_at": datetime.now(timezone.utc).isoformat(), "uri": uri})
        log.info("Job #%s segmen %s: video Veo terunduh -> %s (%s bytes)", job_id, segment.index, dest, dest.stat().st_size)
        return dest
