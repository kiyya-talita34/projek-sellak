from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yshorts_bot.config import AppConfig, ensure_dirs  # noqa: E402


def ffmpeg_available() -> bool:
    if shutil.which("ffmpeg"):
        return True
    try:
        import imageio_ffmpeg  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


requires_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg tidak tersedia")


@pytest.fixture
def tmp_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    data = tmp_path / "data"
    cfg = AppConfig(
        niche="Fakta hewan",
        total_videos=2,
        segments_per_video=2,
        segment_duration_seconds=2,
        paths={
            "data_dir": str(data),
            "prompt_dir": str(data / "prompts"),
            "flow_download_dir": str(data / "flow_downloads"),
            "output_dir": str(data / "output"),
            "failed_dir": str(data / "failed"),
            "log_dir": str(data / "logs"),
            "db_path": str(data / "test.sqlite3"),
        },
        flow={"provider": "manual", "poll_seconds": 1, "stable_seconds": 0, "wait_timeout_minutes": 60},
        retry={"max_attempts": 2, "base_delay_seconds": 1},
        video={"width": 540, "height": 960, "x264_preset": "ultrafast", "video_bitrate": "800k", "min_duration_seconds": 3},
        worker={"idle_sleep_seconds": 1, "heartbeat_seconds": 1},
    )
    ensure_dirs(cfg)
    monkeypatch.setenv("AI_PROVIDER", "mock")
    monkeypatch.setenv("YOUTUBE_MOCK_UPLOAD", "true")
    monkeypatch.setenv("YOUTUBE_TOKEN_FILE", str(tmp_path / "secrets" / "token.json"))
    monkeypatch.chdir(tmp_path)
    return cfg
