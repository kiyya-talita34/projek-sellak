from __future__ import annotations

import json
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from typing import Any

import requests

from ..errors import NonRetryableError

log = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
# Dicoba berurutan bila model utama sudah dipensiunkan (HTTP 404).
GEMINI_FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash"]
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


def parse_json_lenient(text: str) -> dict[str, Any]:
    """Parse JSON dari respon AI walau dibungkus ```json ... ``` atau ada teks tambahan."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Respon AI kosong")
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s)
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("{"), s.rfind("}")
        if start == -1 or end <= start:
            raise ValueError(f"Respon AI bukan JSON: {s[:200]!r}")
        data = json.loads(s[start : end + 1])
    if isinstance(data, list) and data and isinstance(data[0], dict):
        data = data[0]
    if not isinstance(data, dict):
        raise ValueError("Respon AI bukan objek JSON")
    return data


def _extract_field(user_prompt: str, label: str, default: str) -> str:
    match = re.search(rf"{re.escape(label)}\s*:\s*(.+)", user_prompt)
    return match.group(1).strip() if match else default


class AIProvider(ABC):
    name = "base"

    @abstractmethod
    def generate_json(self, system: str, user: str) -> dict[str, Any]:
        raise NotImplementedError


class MockAIProvider(AIProvider):
    """Provider offline untuk uji coba pipeline tanpa API key."""

    name = "mock"

    def generate_json(self, system: str, user: str) -> dict[str, Any]:
        niche = _extract_field(user, "Niche", "Fakta menarik dunia")
        try:
            segments = int(_extract_field(user, "Jumlah segmen", "2"))
        except ValueError:
            segments = 2
        try:
            duration = int(_extract_field(user, "Durasi per segmen", "8").split()[0])
        except (ValueError, IndexError):
            duration = 8
        segs = []
        for i in range(1, max(segments, 1) + 1):
            if i == 1:
                scene = "opening hook, dramatic lighting, clear main subject, fast but readable motion"
            elif i == segments:
                scene = "same subject and visual style, surprising reveal, satisfying ending"
            else:
                scene = "same subject and visual style, rising tension, camera slowly pushes in"
            segs.append(
                {
                    "index": i,
                    "duration_seconds": duration,
                    "prompt": (
                        f"Vertical 9:16 cinematic short about {niche}. Scene {i}: {scene}. "
                        "Cinematic lighting, photorealistic 4k detail, smooth camera movement. "
                        "No text, no letters, no logos, no subtitles, no watermark, no distorted faces."
                    ),
                }
            )
        return {
            "idea": f"Fakta mengejutkan tentang {niche}",
            "style": "Vertical cinematic, high contrast, energetic pacing",
            "hook": f"Kamu tidak akan percaya fakta tentang {niche} ini!",
            "segments": segs,
            "metadata": {
                "title": f"Fakta Mengejutkan: {niche} #Shorts",
                "description": f"Video pendek tentang {niche}. Dibuat untuk format YouTube Shorts.",
                "hashtags": ["#Shorts", "#FaktaUnik", "#Indonesia"],
                "tags": ["shorts", "fakta unik", niche],
            },
        }


class _HttpProvider(AIProvider):
    max_attempts = 3
    timeout_seconds = 120

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """HTTP request dengan retry untuk error jaringan / 429 / 5xx."""
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = requests.request(method, url, timeout=self.timeout_seconds, **kwargs)
            except requests.RequestException as e:
                last_error = e
                log.warning("[%s] Gangguan jaringan (percobaan %s/%s): %s", self.name, attempt, self.max_attempts, e)
                time.sleep(min(2 * attempt, 15))
                continue
            if response.status_code in RETRYABLE_STATUS and attempt < self.max_attempts:
                retry_after = response.headers.get("Retry-After", "")
                delay = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 3.0 * attempt
                log.warning(
                    "[%s] HTTP %s, coba lagi dalam %.0fs (percobaan %s/%s)",
                    self.name, response.status_code, delay, attempt, self.max_attempts,
                )
                time.sleep(min(delay, 60))
                continue
            return response
        raise RuntimeError(f"Permintaan ke {self.name} gagal setelah {self.max_attempts} percobaan: {last_error}")


class OpenAICompatibleProvider(_HttpProvider):
    """OpenAI atau server kompatibel (Groq, OpenRouter, Ollama, LM Studio, dll.) via OPENAI_BASE_URL."""

    name = "openai"

    def __init__(self, api_key: str | None = None, model: str | None = None, base_url: str | None = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        if not self.api_key:
            raise NonRetryableError("OPENAI_API_KEY belum diset di .env. Gunakan AI_PROVIDER=mock untuk uji coba offline.")

    def generate_json(self, system: str, user: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.8,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self.base_url}/chat/completions"
        response = self._request("POST", url, headers=headers, json=payload)
        if response.status_code == 400 and "response_format" in response.text:
            # Server kompatibel yang belum mendukung JSON mode
            payload.pop("response_format", None)
            response = self._request("POST", url, headers=headers, json=payload)
        if response.status_code in (401, 403):
            raise NonRetryableError(f"{self.name}: API key ditolak (HTTP {response.status_code}): {response.text[:300]}")
        if response.status_code == 404:
            raise NonRetryableError(f"{self.name}: model '{self.model}' tidak ditemukan (HTTP 404): {response.text[:300]}")
        response.raise_for_status()
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise RuntimeError(f"Format respon {self.name} tidak dikenal: {response.text[:300]}") from e
        return parse_json_lenient(content)


class GeminiProvider(_HttpProvider):
    """Google Gemini (Google AI Studio). API key dikirim lewat header, bukan URL."""

    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str | None = None, base_url: str | None = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.model = model or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        self.base_url = (
            base_url or os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
        ).rstrip("/")
        if not self.api_key:
            raise NonRetryableError(
                "GEMINI_API_KEY (atau GOOGLE_API_KEY) belum diset di .env. "
                "Ambil API key gratis di https://aistudio.google.com/apikey atau gunakan AI_PROVIDER=mock."
            )
        self._active_model = self.model

    def generate_json(self, system: str, user: str) -> dict[str, Any]:
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.8},
        }
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}
        models = [self._active_model] + [m for m in GEMINI_FALLBACK_MODELS if m != self._active_model]
        for i, model in enumerate(models):
            url = f"{self.base_url}/models/{model}:generateContent"
            response = self._request("POST", url, headers=headers, json=payload)
            if response.status_code == 404 and i < len(models) - 1:
                log.warning("Model Gemini '%s' tidak tersedia (404). Beralih ke '%s'.", model, models[i + 1])
                continue
            if response.status_code in (400, 401, 403):
                raise NonRetryableError(f"Gemini menolak permintaan (HTTP {response.status_code}): {response.text[:300]}")
            response.raise_for_status()
            if model != self._active_model:
                log.info("Model Gemini aktif sekarang: %s (set GEMINI_MODEL di .env untuk mengunci)", model)
                self._active_model = model
            return self._extract(response.json())
        raise RuntimeError("Tidak ada model Gemini yang bisa dipakai. Periksa GEMINI_MODEL di .env.")

    @staticmethod
    def _extract(data: dict[str, Any]) -> dict[str, Any]:
        feedback = data.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            raise RuntimeError(f"Gemini memblokir prompt: {feedback['blockReason']}")
        try:
            candidate = data["candidates"][0]
            parts = candidate["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError) as e:
            reason = (data.get("candidates") or [{}])[0].get("finishReason") if data.get("candidates") else None
            raise RuntimeError(
                f"Format respon Gemini tidak dikenal (finishReason={reason}): {json.dumps(data)[:300]}"
            ) from e
        return parse_json_lenient(text)


PROVIDERS = ("mock", "gemini", "openai")


def build_provider(name: str | None = None) -> AIProvider:
    provider = (name or os.getenv("AI_PROVIDER", "mock")).strip().lower()
    if provider == "mock":
        return MockAIProvider()
    if provider == "openai":
        return OpenAICompatibleProvider()
    if provider in ("gemini", "google"):
        return GeminiProvider()
    raise NonRetryableError(f"AI_PROVIDER tidak dikenal: '{provider}'. Pilih salah satu: {', '.join(PROVIDERS)}.")
