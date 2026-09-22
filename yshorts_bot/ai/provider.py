from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any

import requests

log = logging.getLogger(__name__)


class AIProvider(ABC):
    @abstractmethod
    def generate_json(self, system: str, user: str) -> dict[str, Any]:
        raise NotImplementedError


class MockAIProvider(AIProvider):
    def generate_json(self, system: str, user: str) -> dict[str, Any]:
        niche = user.split("Niche:")[-1].strip().splitlines()[0] if "Niche:" in user else "Fakta menarik dunia"
        return {
            "idea": f"Fakta mengejutkan tentang {niche}",
            "style": "Vertical cinematic, high contrast, energetic pacing, Indonesian narration style",
            "hook": f"Kamu tidak akan percaya fakta tentang {niche} ini!",
            "segments": [
                {
                    "index": 1,
                    "duration_seconds": 8,
                    "prompt": f"Vertical 9:16 cinematic short about {niche}. Scene 1: strong visual hook, dramatic lighting, fast motion, clear subject, no text overlays, suitable for YouTube Shorts."
                },
                {
                    "index": 2,
                    "duration_seconds": 8,
                    "prompt": f"Vertical 9:16 continuation about {niche}. Scene 2: same visual style and subject continuity, surprising reveal, satisfying ending, no text overlays, suitable for YouTube Shorts."
                }
            ],
            "metadata": {
                "title": f"Fakta Mengejutkan: {niche} #Shorts",
                "description": f"Video pendek tentang {niche}. Dibuat untuk format YouTube Shorts.",
                "hashtags": ["#Shorts", "#FaktaUnik", "#Indonesia"],
                "tags": ["shorts", "fakta unik", niche]
            }
        }


class OpenAICompatibleProvider(AIProvider):
    def __init__(self, api_key: str | None = None, model: str | None = None, base_url: str | None = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY belum diset. Gunakan AI_PROVIDER=mock untuk testing.")

    def generate_json(self, system: str, user: str) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.8,
        }
        r = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=90,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            log.error("AI response bukan JSON valid: %s", content)
            raise e


class GeminiProvider(AIProvider):
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY atau GOOGLE_API_KEY belum diset. Gunakan AI_PROVIDER=mock untuk testing offline.")

    def generate_json(self, system: str, user: str) -> dict[str, Any]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        combined_prompt = f"{system}\n\nPermintaan pengguna:\n{user}"
        payload = {
            "contents": [
                {
                    "parts": [{"text": combined_prompt}]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.7,
            },
        }
        r = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=90)
        r.raise_for_status()
        data = r.json()
        try:
            content = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(content)
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            log.error("Respon Gemini tidak sesuai format JSON yang diharapkan: %s", data)
            raise RuntimeError(f"Gagal mem-parsing output JSON Gemini: {e}") from e


def build_provider() -> AIProvider:
    provider = os.getenv("AI_PROVIDER", "mock").lower()
    if provider == "mock":
        return MockAIProvider()
    if provider == "openai":
        return OpenAICompatibleProvider()
    if provider in ("gemini", "google"):
        return GeminiProvider()
    raise ValueError(f"AI_PROVIDER tidak dikenal: {provider}. Pilih salah satu: 'gemini', 'openai', atau 'mock'.")

