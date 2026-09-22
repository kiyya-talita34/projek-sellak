from __future__ import annotations

import logging
import re
from typing import Any

from ..models import YOUTUBE_TITLE_MAX, Metadata, SegmentPrompt, VideoPlan
from .provider import AIProvider

log = logging.getLogger(__name__)

MANDATORY_STYLE = "Vertical 9:16 aspect ratio, cinematic lighting, photorealistic 4k detail, smooth camera movement"
NEGATIVE_DIRECTIVES = "No text, no letters, no logos, no subtitles, no watermark, no distorted faces"

MAX_HASHTAGS = 10
MAX_TAGS = 15
MAX_TAG_LENGTH = 30
MAX_TAGS_TOTAL_LENGTH = 400
DESCRIPTION_SOFT_MAX = 4500


def build_system_prompt(segments: int, segment_duration: int) -> str:
    total = segments * segment_duration
    return f"""
Anda adalah AI Creative Director spesialis konten YouTube Shorts viral sekaligus AI Video Prompter ahli Google Flow / Veo.
Tugas Anda:
1. Membaca niche/topik dan menciptakan konsep video Shorts berdurasi total sekitar {total} detik
   (terdiri dari TEPAT {segments} segmen, masing-masing {segment_duration} detik).
2. Menghasilkan prompt video untuk Google Flow dalam bahasa Inggris yang sangat deskriptif, sinematik, dan optimal
   (subjek, aksi, latar, pencahayaan, gerakan kamera, mood).
3. KONTINUITAS: jaga konsistensi karakter/subjek, palet warna, pencahayaan, dan gaya visual antar segmen.
   Segmen berikutnya adalah kelanjutan langsung segmen sebelumnya (satu cerita utuh, ada hook di awal dan payoff di akhir).
4. FORMAT: setiap prompt wajib menyertakan arahan '{MANDATORY_STYLE}'.
5. PANTANGAN: setiap prompt wajib menyertakan '{NEGATIVE_DIRECTIVES}'.
6. Kembalikan HANYA JSON valid tanpa markdown, tanpa komentar.
""".strip()


def _plan_user_prompt(niche: str, segments: int, segment_duration: int, strict_note: str = "") -> str:
    example_segments = ",\n    ".join(
        f'{{"index": {i}, "duration_seconds": {segment_duration}, "prompt": "prompt detail segmen {i}"}}'
        for i in range(1, segments + 1)
    )
    return f"""
Niche: {niche}
Jumlah segmen: {segments}
Durasi per segmen: {segment_duration} detik
Target: YouTube Shorts 9:16, total sekitar {segments * segment_duration} detik.
{strict_note}
Buat JSON dengan schema persis seperti ini:
{{
  "idea": "ide video singkat (bahasa Indonesia)",
  "style": "gaya visual",
  "hook": "kalimat pembuka yang bikin penasaran",
  "segments": [
    {example_segments}
  ],
  "metadata": {{"title": "judul <= 100 karakter", "description": "2-4 kalimat", "hashtags": ["#Shorts"], "tags": ["tag"]}}
}}
""".strip()


# ---------------------------------------------------------------------------
# Normalisasi output AI
# ---------------------------------------------------------------------------
def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def ensure_prompt_directives(prompt: str) -> str:
    text = " ".join(str(prompt).split())
    lowered = text.lower()
    if "9:16" not in lowered and "vertical" not in lowered:
        text = f"{text.rstrip('.')}. {MANDATORY_STYLE}."
    if "no text" not in lowered:
        text = f"{text.rstrip('.')}. {NEGATIVE_DIRECTIVES}."
    return text


def _continuation_prompt(niche: str, previous: str | None, index: int, total: int) -> str:
    base = previous or f"Cinematic vertical short about {niche}"
    ending = "surprising reveal and a satisfying ending" if index == total else "rising tension, camera slowly pushes in"
    return ensure_prompt_directives(
        f"Direct continuation of the previous shot ({niche}): same subject, same colour palette, "
        f"same lighting and visual style as before. {ending}. Context of previous shot: {base[:300]}"
    )


def normalize_segments(raw: Any, niche: str, segments: int, segment_duration: int) -> tuple[list[SegmentPrompt], bool]:
    """Kembalikan (daftar segmen 1..N yang valid, apakah jumlah dari AI sudah pas)."""
    items = raw.get("segments") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        items = []
    cleaned: list[tuple[int, str]] = []
    for i, item in enumerate(items):
        if isinstance(item, str):
            idx, prompt = i + 1, item.strip()
        elif isinstance(item, dict):
            prompt = str(item.get("prompt") or item.get("text") or item.get("description") or "").strip()
            idx = _as_int(item.get("index"), i + 1)
        else:
            continue
        if prompt:
            cleaned.append((idx, prompt))
    cleaned.sort(key=lambda x: x[0])
    prompts = [p for _, p in cleaned]
    exact = len(prompts) == segments
    prompts = prompts[:segments]
    while len(prompts) < segments:
        prompts.append(_continuation_prompt(niche, prompts[-1] if prompts else None, len(prompts) + 1, segments))
    result = [
        SegmentPrompt(index=i + 1, prompt=ensure_prompt_directives(p), duration_seconds=segment_duration)
        for i, p in enumerate(prompts)
    ]
    return result, exact


def create_video_plan(provider: AIProvider, niche: str, segments: int = 2, segment_duration: int = 8) -> VideoPlan:
    system = build_system_prompt(segments, segment_duration)
    data = provider.generate_json(system, _plan_user_prompt(niche, segments, segment_duration))
    segs, exact = normalize_segments(data, niche, segments, segment_duration)
    if not exact:
        log.warning(
            "AI mengembalikan %s segmen (diminta %s). Meminta ulang dengan instruksi lebih ketat...",
            len((data or {}).get("segments") or []) if isinstance(data, dict) else 0, segments,
        )
        strict = f"PENTING: array \"segments\" WAJIB berisi TEPAT {segments} objek dengan index 1..{segments}."
        try:
            data2 = provider.generate_json(system, _plan_user_prompt(niche, segments, segment_duration, strict))
            segs2, exact2 = normalize_segments(data2, niche, segments, segment_duration)
            if exact2:
                data, segs = data2, segs2
        except Exception as e:  # pragma: no cover - jalur cadangan
            log.warning("Permintaan ulang gagal (%s); memakai hasil pertama yang dilengkapi otomatis.", e)

    idea = str(data.get("idea") or f"Video Shorts tentang {niche}").strip()
    style = str(data.get("style") or "Cinematic vertical").strip()
    hook = str(data.get("hook") or idea).strip()
    return VideoPlan(idea=idea, style=style, hook=hook, segments=segs)


# ---------------------------------------------------------------------------
# Metadata YouTube
# ---------------------------------------------------------------------------
def _as_list(value: Any, split_pattern: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in re.split(split_pattern, value) if v.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if v is not None and str(v).strip()]
    return [str(value).strip()]


def clean_hashtags(value: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in _as_list(value, r"[\s,]+"):
        tag = re.sub(r"[^\w]", "", raw.replace("#", ""))
        if not tag:
            continue
        candidate = f"#{tag}"
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    if "#shorts" in seen:
        result = ["#Shorts"] + [h for h in result if h.lower() != "#shorts"]
    else:
        result.insert(0, "#Shorts")
    return result[:MAX_HASHTAGS]


def clean_tags(value: Any, fallback: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    total = 0
    for raw in _as_list(value, r"[,;\n]+") or fallback:
        tag = re.sub(r"[<>\"]", "", raw).strip().lstrip("#")[:MAX_TAG_LENGTH].strip()
        if not tag or tag.lower() in seen:
            continue
        if total + len(tag) > MAX_TAGS_TOTAL_LENGTH or len(result) >= MAX_TAGS:
            break
        seen.add(tag.lower())
        result.append(tag)
        total += len(tag)
    return result


def sanitize_title(title: Any, fallback: str) -> str:
    text = re.sub(r"[<>]", "", str(title or "")).strip()
    text = " ".join(text.split()) or fallback
    if len(text) > YOUTUBE_TITLE_MAX:
        cut = text[:YOUTUBE_TITLE_MAX]
        text = cut[: cut.rfind(" ")] if " " in cut[60:] else cut
    return text.strip() or fallback[:YOUTUBE_TITLE_MAX]


def sanitize_description(description: Any, fallback: str) -> str:
    text = re.sub(r"[<>]", "", str(description or "")).strip() or fallback
    return text[:DESCRIPTION_SOFT_MAX].rstrip()


def create_metadata(provider: AIProvider, niche: str, plan: VideoPlan) -> Metadata:
    system = build_system_prompt(len(plan.segments) or 2, plan.segments[0].duration_seconds if plan.segments else 8)
    user = f"""
Niche: {niche}
Ide: {plan.idea}
Hook: {plan.hook}
Style: {plan.style}
Prompt segmen: {[s.prompt for s in plan.segments]}

Buat metadata YouTube Shorts (bahasa Indonesia, menarik, tanpa clickbait berlebihan) dalam JSON:
{{"title":"maks 100 karakter, sertakan #Shorts", "description":"2-4 kalimat + ajakan like/subscribe", "hashtags":["#Shorts", "..."], "tags":["kata kunci", "..."]}}
""".strip()
    data = provider.generate_json(system, user)
    if isinstance(data.get("metadata"), dict):
        data = data["metadata"]
    return Metadata(
        title=sanitize_title(data.get("title"), plan.idea),
        description=sanitize_description(data.get("description"), plan.hook),
        hashtags=clean_hashtags(data.get("hashtags")),
        tags=clean_tags(data.get("tags"), [niche, "shorts"]),
    )
