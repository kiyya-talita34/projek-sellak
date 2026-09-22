from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

JsonDict = dict[str, Any]

# Batas resmi YouTube Data API v3
YOUTUBE_TITLE_MAX = 100
YOUTUBE_DESCRIPTION_MAX = 5000


@dataclass
class SegmentPrompt:
    index: int
    prompt: str
    duration_seconds: int = 8

    def to_dict(self) -> JsonDict:
        return {"index": self.index, "prompt": self.prompt, "duration_seconds": self.duration_seconds}

    @classmethod
    def from_dict(cls, data: JsonDict, default_duration: int = 8) -> "SegmentPrompt":
        return cls(
            index=int(data["index"]),
            prompt=str(data["prompt"]),
            duration_seconds=int(data.get("duration_seconds") or default_duration),
        )


@dataclass
class VideoPlan:
    idea: str
    style: str
    hook: str
    segments: list[SegmentPrompt] = field(default_factory=list)

    @property
    def total_duration_seconds(self) -> int:
        return sum(s.duration_seconds for s in self.segments)

    def to_dict(self) -> JsonDict:
        return {
            "idea": self.idea,
            "style": self.style,
            "hook": self.hook,
            "segments": [s.to_dict() for s in self.segments],
        }

    @classmethod
    def from_dict(cls, data: JsonDict) -> "VideoPlan":
        return cls(
            idea=str(data.get("idea", "")),
            style=str(data.get("style", "")),
            hook=str(data.get("hook", "")),
            segments=[SegmentPrompt.from_dict(s) for s in data.get("segments", [])],
        )


@dataclass
class Metadata:
    title: str
    description: str
    hashtags: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def description_with_hashtags(self) -> str:
        """Deskripsi final untuk YouTube: deskripsi + baris hashtag, dipotong ke batas resmi."""
        tags = " ".join(self.hashtags)
        text = f"{self.description}\n\n{tags}".strip()
        if len(text) > YOUTUBE_DESCRIPTION_MAX:
            room = YOUTUBE_DESCRIPTION_MAX - len(tags) - 2
            text = f"{self.description[: max(room, 0)].rstrip()}\n\n{tags}".strip()[:YOUTUBE_DESCRIPTION_MAX]
        return text

    def to_dict(self) -> JsonDict:
        return {"title": self.title, "description": self.description, "hashtags": self.hashtags, "tags": self.tags}

    @classmethod
    def from_dict(cls, data: JsonDict) -> "Metadata":
        return cls(
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            hashtags=[str(h) for h in data.get("hashtags", [])],
            tags=[str(t) for t in data.get("tags", [])],
        )
