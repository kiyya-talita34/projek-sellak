from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SegmentPrompt:
    index: int
    prompt: str
    duration_seconds: int = 8


@dataclass
class VideoPlan:
    idea: str
    style: str
    hook: str
    segments: list[SegmentPrompt]


@dataclass
class Metadata:
    title: str
    description: str
    hashtags: list[str]
    tags: list[str]

    def description_with_hashtags(self) -> str:
        tags = " ".join(self.hashtags)
        return f"{self.description}\n\n{tags}".strip()


JsonDict = dict[str, Any]
