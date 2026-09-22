from __future__ import annotations

from ..config import AppConfig
from .base import FlowProvider
from .browser_stub import BrowserFlowProvider
from .manual import ManualFlowProvider
from .playwright_provider import PlaywrightFlowProvider
from .veo_api import VeoApiFlowProvider

FLOW_PROVIDERS = ("manual", "browser", "veo_api")


def build_flow_provider(cfg: AppConfig) -> FlowProvider:
    provider = cfg.flow.provider
    if provider == "manual":
        return ManualFlowProvider(
            prompt_dir=cfg.paths.prompt_dir,
            flow_download_dir=cfg.paths.flow_download_dir,
            min_video_bytes=cfg.flow.min_video_bytes,
            stable_seconds=cfg.flow.stable_seconds,
        )
    if provider in ("browser", "playwright"):
        return PlaywrightFlowProvider(
            prompt_dir=cfg.paths.prompt_dir,
            flow_download_dir=cfg.paths.flow_download_dir,
            flow_cfg=cfg.flow,
            log_dir=cfg.paths.log_dir,
        )
    if provider == "veo_api":
        return VeoApiFlowProvider(
            prompt_dir=cfg.paths.prompt_dir,
            flow_download_dir=cfg.paths.flow_download_dir,
            veo_cfg=cfg.flow.veo,
        )
    if provider == "browser_stub":
        return BrowserFlowProvider()
    raise ValueError(f"Flow provider tidak dikenal: {provider}. Pilihan: {', '.join(FLOW_PROVIDERS)}")
