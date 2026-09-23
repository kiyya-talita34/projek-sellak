from __future__ import annotations

from typing import Any

import pytest

from yshorts_bot.ai.planner import (
    build_metadata,
    build_system_prompt,
    clean_hashtags,
    clean_tags,
    create_metadata,
    create_video_plan,
    sanitize_title,
)
from yshorts_bot.ai.provider import AIProvider, MockAIProvider, parse_json_lenient
from yshorts_bot.config import AIConfig
from yshorts_bot.models import Metadata, VideoPlan


class FakeProvider(AIProvider):
    name = "fake"

    def __init__(self, *responses: dict[str, Any]):
        self.responses = list(responses)
        self.calls = 0
        self.systems: list[str] = []
        self.temperatures: list[float | None] = []

    def generate_json(self, system: str, user: str, temperature: float | None = None) -> dict[str, Any]:
        self.calls += 1
        self.systems.append(system)
        self.temperatures.append(temperature)
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


def test_mock_provider_produces_requested_segments():
    plan = create_video_plan(MockAIProvider(), "Fakta hewan", segments=3, segment_duration=8)
    assert [s.index for s in plan.segments] == [1, 2, 3]
    assert all(s.duration_seconds == 8 for s in plan.segments)
    assert all("9:16" in s.prompt for s in plan.segments)
    assert plan.total_duration_seconds == 24


def test_plan_retries_once_then_pads_missing_segments():
    one_segment = {"idea": "i", "style": "s", "hook": "h", "segments": [{"index": 1, "prompt": "Opening shot of a whale"}]}
    provider = FakeProvider(one_segment, one_segment)
    plan = create_video_plan(provider, "laut", segments=2, segment_duration=8)
    assert provider.calls == 2
    assert len(plan.segments) == 2
    assert plan.segments[1].index == 2
    assert "continuation" in plan.segments[1].prompt.lower()
    assert "No text" in plan.segments[1].prompt and "9:16" in plan.segments[1].prompt


def test_plan_truncates_extra_segments_and_reindexes():
    data = {"idea": "i", "style": "s", "hook": "h", "segments": [{"index": 0, "prompt": "a"}, {"index": 1, "prompt": "b"}, "c"]}
    plan = create_video_plan(FakeProvider(data), "x", segments=2, segment_duration=8)
    assert [s.index for s in plan.segments] == [1, 2]
    assert plan.segments[0].prompt.startswith("a") and plan.segments[1].prompt.startswith("b")


def test_plan_missing_fields_fall_back():
    data = {"segments": [{"prompt": "p1"}, {"prompt": "p2"}]}
    plan = create_video_plan(FakeProvider(data), "hewan", 2, 8)
    assert plan.idea and plan.hook and plan.style


def test_ai_config_steers_language_style_and_temperature():
    data = {"idea": "i", "style": "s", "hook": "h", "segments": [{"prompt": "a"}, {"prompt": "b"}]}
    provider = FakeProvider(data)
    ai_cfg = AIConfig(language="en", style_notes="Nada dramatis, akhiri dengan pertanyaan", temperature=0.3)
    create_video_plan(provider, "x", 2, 8, ai_cfg)
    assert provider.temperatures == [0.3]
    assert "English" in provider.systems[0] and "Nada dramatis" in provider.systems[0]
    system_default = build_system_prompt(2, 8)
    assert "Bahasa Indonesia" in system_default and "PREFERENSI" not in system_default
    assert "xx-custom" in build_system_prompt(2, 8, AIConfig(language="xx-custom"))


def test_metadata_is_sanitized():
    data = {
        "title": "<b>Judul</b> " + "kata " * 40,
        "description": "Deskripsi <script>alert(1)</script> bagus",
        "hashtags": "#shorts #Fakta, unik",
        "tags": "a, b, a, <x>",
    }
    plan = VideoPlan(idea="ide", style="s", hook="hook", segments=[])
    meta = create_metadata(FakeProvider(data), "niche", plan)
    assert "<" not in meta.title and ">" not in meta.title
    assert meta.title.startswith("Judul kata")
    assert len(meta.title) <= 100
    assert meta.hashtags == ["#Shorts", "#Fakta", "#unik"]
    assert meta.tags == ["a", "b", "x"]
    assert "<" not in meta.description and "script" not in meta.description
    assert meta.description_with_hashtags().endswith("#Shorts #Fakta #unik")


def test_build_metadata_from_user_input():
    meta = build_metadata({"title": "", "description": None, "hashtags": ["#A", "a"], "tags": None}, "Fallback judul", "Fallback deskripsi", "niche")
    assert meta.title == "Fallback judul" and meta.description == "Fallback deskripsi"
    assert meta.hashtags == ["#Shorts", "#A"] and meta.tags == ["niche", "shorts"]
    nested = build_metadata({"metadata": {"title": "Nested"}}, "f", "f", "n")
    assert nested.title == "Nested"


def test_metadata_nested_and_defaults():
    plan = create_video_plan(MockAIProvider(), "Fakta hewan", 2, 8)
    meta = create_metadata(MockAIProvider(), "Fakta hewan", plan)
    assert meta.hashtags[0] == "#Shorts"
    assert meta.title.startswith("Fakta Mengejutkan")
    assert Metadata.from_dict(meta.to_dict()) == meta


def test_clean_helpers():
    assert clean_hashtags(["shorts", "#Shorts", "Fakta Unik", None]) == ["#Shorts", "#FaktaUnik"]
    assert clean_hashtags(None) == ["#Shorts"]
    assert clean_tags(None, ["fallback"]) == ["fallback"]
    assert clean_tags(["x" * 50], []) == ["x" * 30]
    assert sanitize_title("   ", "fallback") == "fallback"
    assert len(sanitize_title("kata " * 50, "f")) <= 100


def test_description_limit():
    meta = Metadata(title="t", description="d" * 6000, hashtags=["#Shorts"], tags=[])
    text = meta.description_with_hashtags()
    assert len(text) <= 5000 and text.endswith("#Shorts")


@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        'Berikut hasilnya:\n{"a": 1}\nSemoga membantu',
        '[{"a": 1}]',
    ],
)
def test_parse_json_lenient_variants(raw):
    assert parse_json_lenient(raw) == {"a": 1}


def test_parse_json_lenient_rejects_garbage():
    with pytest.raises(ValueError):
        parse_json_lenient("tidak ada json di sini")
    with pytest.raises(ValueError):
        parse_json_lenient("")
