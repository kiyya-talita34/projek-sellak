from __future__ import annotations

import json

from yshorts_bot.config import NotificationsConfig
from yshorts_bot.notify import Notifier


class FakeResponse:
    def __init__(self, status=200):
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


class FakePost:
    def __init__(self, status=200):
        self.calls: list[tuple[str, dict]] = []
        self.status = status

    def __call__(self, url, json=None, timeout=None):  # noqa: A002
        self.calls.append((url, json))
        return FakeResponse(self.status)


def job(**extra):
    base = {"id": 7, "niche": "Fakta hewan", "status": "done", "youtube_video_id": "abc123",
            "metadata_json": json.dumps({"title": "Judul Keren #Shorts"}), "last_error": None}
    base.update(extra)
    return base


def test_disabled_or_no_channels_sends_nothing():
    post = FakePost()
    n = Notifier(NotificationsConfig(enabled=True), post=post, webhook_url="", telegram_token="", telegram_chat_id="")
    assert not n.enabled and n.channels == []
    n.notify_job("done", job(), wait=True)
    assert post.calls == []
    n2 = Notifier(NotificationsConfig(enabled=False), post=post, webhook_url="https://example.com/hook", telegram_token="", telegram_chat_id="")
    assert not n2.enabled
    n2.notify_job("done", job(), wait=True)
    assert post.calls == []


def test_discord_slack_generic_and_telegram_payloads():
    post = FakePost()
    n = Notifier(NotificationsConfig(enabled=True), post=post,
                 webhook_url="https://discord.com/api/webhooks/1/abc", telegram_token="tok", telegram_chat_id="42")
    assert n.channels == ["webhook", "telegram"] and n.enabled
    n.notify_job("done", job(), wait=True)
    assert len(post.calls) == 2
    discord_url, discord_body = post.calls[0]
    assert "discord.com" in discord_url and "content" in discord_body
    assert "Judul Keren" in discord_body["content"] and "youtube.com/shorts/abc123" in discord_body["content"]
    tg_url, tg_body = post.calls[1]
    assert tg_url == "https://api.telegram.org/bottok/sendMessage" and tg_body["chat_id"] == "42"
    assert "job #7" in tg_body["text"]

    slack = Notifier(NotificationsConfig(enabled=True), post=post, webhook_url="https://hooks.slack.com/services/x", telegram_token="", telegram_chat_id="")
    assert slack.send_sync("halo", {"event": "test"}) == {"webhook": "ok"}
    assert post.calls[-1][1] == {"text": "halo"}

    generic = Notifier(NotificationsConfig(enabled=True), post=post, webhook_url="https://example.com/hook", telegram_token="", telegram_chat_id="")
    generic.notify_job("failed", job(status="failed", last_error="boom"), message="Tahap: uploading", wait=True)
    body = post.calls[-1][1]
    assert body["event"] == "failed" and body["error"] == "boom" and "Tahap: uploading" in body["text"]


def test_event_filters_and_failures_are_reported():
    post = FakePost()
    cfg = NotificationsConfig(enabled=True, on_done=False, on_failed=True, on_awaiting_approval=True, dashboard_url="http://localhost:8000")
    n = Notifier(cfg, post=post, webhook_url="https://example.com/hook", telegram_token="", telegram_chat_id="")
    n.notify_job("done", job(), wait=True)
    assert post.calls == []
    n.notify_job("awaiting_approval", job(status="awaiting_approval", youtube_video_id=None), wait=True)
    assert "http://localhost:8000" in post.calls[-1][1]["text"]
    failing = Notifier(cfg, post=FakePost(status=500), webhook_url="https://example.com/hook", telegram_token="", telegram_chat_id="")
    assert failing.send_sync("x")["webhook"].startswith("gagal")
