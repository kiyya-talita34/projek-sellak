from __future__ import annotations

from ..config import AppConfig
from .base import FlowProvider
from .manual import ManualFlowProvider
from .browser_stub import BrowserFlowProvider
from .playwright_provider import PlaywrightFlowProvider


def build_flow_provider(cfg: AppConfig) -> FlowProvider:
    if cfg.flow.provider == "manual":
        return ManualFlowProvider(
            prompt_dir=cfg.paths.prompt_dir,
            flow_download_dir=cfg.paths.flow_download_dir,
            wait_timeout_minutes=cfg.flow.wait_timeout_minutes,
            poll_seconds=cfg.flow.poll_seconds,
        )
    if cfg.flow.provider in ("browser", "playwright"):
        return PlaywrightFlowProvider(
            prompt_dir=cfg.paths.prompt_dir,
            flow_download_dir=cfg.paths.flow_download_dir,
            flow_url=cfg.flow.flow_url,
            browser_user_data_dir=cfg.flow.browser_user_data_dir,
            headless=cfg.flow.headless,
            generation_timeout_seconds=cfg.flow.generation_timeout_seconds,
            wait_timeout_minutes=cfg.flow.wait_timeout_minutes,
            poll_seconds=cfg.flow.poll_seconds,
        )
    if cfg.flow.provider == "browser_stub":
        return BrowserFlowProvider()
    raise ValueError(f"Flow provider tidak dikenal: {cfg.flow.provider}")
