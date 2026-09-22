from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field
from dotenv import load_dotenv


class ScheduleConfig(BaseModel):
    mode: Literal["interval", "specific_times"] = "interval"
    interval_hours: int = 3
    start_time: str = "08:00"
    specific_times: list[str] = Field(default_factory=lambda: ["08:00", "12:00", "18:00", "21:00"])
    timezone: str = "Asia/Jakarta"


class PathsConfig(BaseModel):
    data_dir: str = "data"
    prompt_dir: str = "data/prompts"
    flow_download_dir: str = "data/flow_downloads"
    output_dir: str = "data/output"
    failed_dir: str = "data/failed"
    log_dir: str = "data/logs"
    db_path: str = "data/yshorts.sqlite3"


class FlowConfig(BaseModel):
    provider: Literal["manual", "browser_stub", "browser", "playwright"] = "browser"
    flow_url: str = "https://labs.google/flow"
    headless: bool = False
    browser_user_data_dir: str = "data/browser_profile"
    generation_timeout_seconds: int = 600
    wait_timeout_minutes: int = 1440
    poll_seconds: int = 5


class YouTubeConfig(BaseModel):
    privacy_status: Literal["private", "unlisted", "public"] = "private"
    made_for_kids: bool = False
    category_id: str = "22"


class RetryConfig(BaseModel):
    max_attempts: int = 3
    base_delay_seconds: int = 60


class VideoConfig(BaseModel):
    width: int = 1080
    height: int = 1920
    fps: int = 30
    video_bitrate: str = "8M"
    audio_bitrate: str = "192k"


class AppConfig(BaseModel):
    niche: str = "Fakta menarik dunia"
    total_videos: int = 10
    segments_per_video: int = 2
    segment_duration_seconds: int = 8
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    flow: FlowConfig = Field(default_factory=FlowConfig)
    youtube: YouTubeConfig = Field(default_factory=YouTubeConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    video: VideoConfig = Field(default_factory=VideoConfig)


def load_config(path: str = "config.json") -> AppConfig:
    load_dotenv()
    p = Path(path)
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        cfg = AppConfig(**data)
    else:
        cfg = AppConfig()
    ensure_dirs(cfg)
    return cfg


def ensure_dirs(cfg: AppConfig) -> None:
    for value in cfg.paths.model_dump().values():
        path = Path(value)
        if path.suffix:
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            path.mkdir(parents=True, exist_ok=True)
    Path("secrets").mkdir(exist_ok=True)


def env(name: str, default: str | None = None) -> str | None:
    return os.getenv(name, default)
