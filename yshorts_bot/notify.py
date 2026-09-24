"""Notifikasi Telegram / webhook (Discord, Slack, generik) untuk kejadian penting job."""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import requests

from .config import NotificationsConfig

log = logging.getLogger(__name__)

EVENT_LABELS = {
    "done": "Video terupload",
    "failed": "Job gagal",
    "awaiting_approval": "Menunggu persetujuan",
    "test": "Tes notifikasi",
}


class Notifier:
    def __init__(
        self,
        cfg: NotificationsConfig,
        post: Callable[..., Any] | None = None,
        webhook_url: str | None = None,
        telegram_token: str | None = None,
        telegram_chat_id: str | None = None,
    ):
        self.cfg = cfg
        self._post = post or requests.post
        self.webhook_url = webhook_url if webhook_url is not None else (os.getenv("NOTIFY_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL") or "")
        self.telegram_token = telegram_token if telegram_token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = telegram_chat_id if telegram_chat_id is not None else os.getenv("TELEGRAM_CHAT_ID", "")

    # ------------------------------------------------------------------ status
    @property
    def channels(self) -> list[str]:
        out = []
        if self.webhook_url:
            out.append("webhook")
        if self.telegram_token and self.telegram_chat_id:
            out.append("telegram")
        return out

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.enabled and self.channels)

    def wants(self, event: str) -> bool:
        if event == "test":
            return True
        return {
            "done": self.cfg.on_done,
            "failed": self.cfg.on_failed,
            "awaiting_approval": self.cfg.on_awaiting_approval,
        }.get(event, True)

    # ------------------------------------------------------------------ kirim
    def notify_job(self, event: str, job: dict[str, Any], message: str = "", wait: bool = False) -> None:
        """Kirim notifikasi untuk sebuah job (di thread latar agar worker tidak terhambat)."""
        if not self.enabled or not self.wants(event):
            return
        title = ""
        try:
            import json

            meta = json.loads(job.get("metadata_json") or "{}")
            title = meta.get("title") or ""
        except (TypeError, ValueError):
            pass
        lines = [f"[YShorts] {EVENT_LABELS.get(event, event)} - job #{job.get('id')}", f"Niche: {job.get('niche', '')}"]
        if title:
            lines.append(f"Judul: {title}")
        if job.get("youtube_video_id") and not str(job["youtube_video_id"]).startswith("demo_"):
            lines.append(f"https://youtube.com/shorts/{job['youtube_video_id']}")
        if message:
            lines.append(message)
        if event == "awaiting_approval" and self.cfg.dashboard_url:
            lines.append(f"Tinjau & setujui: {self.cfg.dashboard_url}")
        payload = {
            "event": event,
            "job_id": job.get("id"),
            "niche": job.get("niche"),
            "title": title,
            "status": job.get("status"),
            "youtube_video_id": job.get("youtube_video_id"),
            "error": job.get("last_error") if event == "failed" else None,
            "message": message,
            "time": datetime.now(timezone.utc).isoformat(),
        }
        self.send("\n".join(lines), payload, wait=wait)

    def send(self, text: str, payload: dict[str, Any] | None = None, wait: bool = False) -> None:
        if not self.channels:
            return
        thread = threading.Thread(target=self._send_all, args=(text, payload or {"message": text}), daemon=True)
        thread.start()
        if wait:
            thread.join(timeout=20)

    def send_sync(self, text: str, payload: dict[str, Any] | None = None) -> dict[str, str]:
        """Kirim sekarang dan kembalikan hasil per kanal (untuk perintah tes)."""
        results: dict[str, str] = {}
        if self.webhook_url:
            try:
                self._send_webhook(text, payload or {"message": text})
                results["webhook"] = "ok"
            except Exception as e:  # noqa: BLE001
                results["webhook"] = f"gagal: {e}"
        if self.telegram_token and self.telegram_chat_id:
            try:
                self._send_telegram(text)
                results["telegram"] = "ok"
            except Exception as e:  # noqa: BLE001
                results["telegram"] = f"gagal: {e}"
        return results

    def _send_all(self, text: str, payload: dict[str, Any]) -> None:
        for channel, result in self.send_sync(text, payload).items():
            if result != "ok":
                log.warning("Notifikasi %s %s", channel, result)

    def _send_webhook(self, text: str, payload: dict[str, Any]) -> None:
        url = self.webhook_url
        if "discord.com/api/webhooks" in url or "discordapp.com/api/webhooks" in url:
            body: dict[str, Any] = {"content": text[:1900]}
        elif "hooks.slack.com" in url:
            body = {"text": text}
        else:
            body = {"text": text, **payload}
        response = self._post(url, json=body, timeout=15)
        response.raise_for_status()

    def _send_telegram(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        response = self._post(
            url,
            json={"chat_id": self.telegram_chat_id, "text": text[:4000], "disable_web_page_preview": True},
            timeout=15,
        )
        response.raise_for_status()
