from __future__ import annotations

import json
import os

import pytest

from yshorts_bot.config import AppConfig, load_config, load_env_files, save_config
from yshorts_bot.errors import ConfigError


def test_env_loaded_from_config_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("YSHORTS_TEST_FLAG", raising=False)
    other = tmp_path / "proyek"
    other.mkdir()
    (other / ".env").write_text("YSHORTS_TEST_FLAG=dari_config_dir\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)  # folder kerja berbeda dari folder config
    loaded = load_env_files(other / "config.json")
    assert other / ".env" in loaded
    assert os.environ["YSHORTS_TEST_FLAG"] == "dari_config_dir"


def test_env_does_not_override_existing_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("YSHORTS_TEST_FLAG", "asli")
    (tmp_path / ".env").write_text("YSHORTS_TEST_FLAG=dari_file\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    load_env_files(tmp_path / "config.json")
    assert os.environ["YSHORTS_TEST_FLAG"] == "asli"


def test_load_config_creates_file_from_example_or_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load_config(str(tmp_path / "config.json"))
    assert (tmp_path / "config.json").exists()
    assert cfg.flow.provider == "manual" and cfg.segments_per_video == 2

    example = tmp_path / "sub" / "config.example.json"
    example.parent.mkdir()
    example.write_text(json.dumps({"niche": "Dari contoh", "schedule": {"interval_hours": 6}}), encoding="utf-8")
    cfg2 = load_config(str(tmp_path / "sub" / "config.json"))
    assert cfg2.niche == "Dari contoh" and cfg2.schedule.interval_hours == 6


def test_load_config_reports_invalid_json_and_values(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bad = tmp_path / "config.json"
    bad.write_text("{ ini bukan json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(str(bad))
    bad.write_text(json.dumps({"schedule": {"start_time": "99:99"}}), encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        load_config(str(bad))
    assert "start_time" in str(info.value)
    bad.write_text(json.dumps({"flow": {"provider": "ngawur"}}), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(str(bad))


def test_save_config_roundtrip(tmp_path):
    cfg = AppConfig(niche="Uji", schedule={"mode": "specific_times", "specific_times": ["21:00", "8:00"]})
    path = save_config(cfg, str(tmp_path / "c.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["niche"] == "Uji" and data["schedule"]["specific_times"] == ["08:00", "21:00"]
    assert not list(tmp_path.glob("*.tmp"))
    assert AppConfig(**data) == cfg


def test_planned_duration_property():
    assert AppConfig(segments_per_video=3, segment_duration_seconds=8).planned_duration_seconds == 24
