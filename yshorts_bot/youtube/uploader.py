from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from ..config import env_flag
from ..errors import NonRetryableError, RetryLaterError
from ..models import Metadata

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
CHUNK_SIZE = 8 * 1024 * 1024  # kelipatan 256 KiB, upload resumable

QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "userRateLimitExceeded", "rateLimitExceeded"}
PERMANENT_REASONS = {
    "forbidden", "insufficientPermissions", "youtubeSignupRequired", "invalidTitle", "invalidDescription",
    "invalidTags", "invalidCategoryId", "invalidFilename", "invalidVideoMetadata", "mediaBodyRequired",
    "unsupportedMediaType", "invalidPrivacyStatus",
}


def _parse_http_error(error: Any) -> tuple[int, str, str]:
    status = int(getattr(getattr(error, "resp", None), "status", 0) or 0)
    reason, message = "", str(error)
    try:
        payload = json.loads(getattr(error, "content", b"") or b"{}")
        err = payload.get("error", {})
        details = (err.get("errors") or [{}])[0]
        reason = details.get("reason", "") or ""
        message = err.get("message") or details.get("message") or message
    except (ValueError, TypeError, AttributeError):
        pass
    return status, reason, message


class YouTubeUploader:
    """Upload resmi lewat YouTube Data API v3 + OAuth 2.0 (Desktop app client)."""

    def __init__(self, client_secret_file: str | None = None, token_file: str | None = None, mock: bool | None = None):
        self.client_secret_file = client_secret_file or os.getenv("YOUTUBE_CLIENT_SECRET_FILE", "client_secret.json")
        self.token_file = token_file or os.getenv("YOUTUBE_TOKEN_FILE", "secrets/token.json")
        self.mock = env_flag("YOUTUBE_MOCK_UPLOAD", False) if mock is None else mock
        self.interactive_auth = env_flag("YOUTUBE_INTERACTIVE_AUTH", True)
        Path(self.token_file).parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ auth
    def has_token(self) -> bool:
        return Path(self.token_file).exists()

    def _credentials(self, interactive: bool = True) -> Any:
        try:
            from google.auth.exceptions import RefreshError
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ModuleNotFoundError as e:
            raise NonRetryableError("Dependensi YouTube belum terinstall. Jalankan: pip install -r requirements.txt") from e

        creds = None
        token_path = Path(self.token_file)
        if token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
            except (ValueError, json.JSONDecodeError) as e:
                log.warning("File token %s rusak (%s); login ulang diperlukan.", token_path, e)
                creds = None
        if creds is not None and not creds.has_scopes(SCOPES):
            log.info("Scope token lama berbeda; login ulang untuk memperbarui izin.")
            creds = None

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                token_path.write_text(creds.to_json(), encoding="utf-8")
            except RefreshError as e:
                raise NonRetryableError(
                    f"Refresh token YouTube gagal ({e}). Jalankan ulang: python -m yshorts_bot youtube-auth"
                ) from e

        if not creds or not creds.valid:
            if not interactive:
                raise NonRetryableError(
                    "Belum login YouTube. Jalankan: python -m yshorts_bot youtube-auth "
                    "(atau set YOUTUBE_MOCK_UPLOAD=true untuk simulasi)."
                )
            if not Path(self.client_secret_file).exists():
                raise NonRetryableError(
                    f"OAuth client secret tidak ditemukan: '{self.client_secret_file}'. "
                    "Unduh client_secret.json (OAuth client ID tipe Desktop app) dari Google Cloud Console, "
                    "atau set YOUTUBE_MOCK_UPLOAD=true di .env untuk simulasi."
                )
            flow = InstalledAppFlow.from_client_secrets_file(self.client_secret_file, SCOPES)
            log.info("Membuka browser untuk login Google / izin YouTube...")
            creds = flow.run_local_server(port=0, prompt="consent", authorization_prompt_message="")
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json(), encoding="utf-8")
            log.info("Token OAuth tersimpan di %s", token_path)
        return creds

    def _service(self, interactive: bool) -> Any:
        try:
            from googleapiclient.discovery import build
        except ModuleNotFoundError as e:
            raise NonRetryableError("Dependensi YouTube belum terinstall. Jalankan: pip install -r requirements.txt") from e
        return build("youtube", "v3", credentials=self._credentials(interactive=interactive), cache_discovery=False)

    def authorize(self) -> dict[str, Any] | None:
        """Login interaktif (sekali) lalu kembalikan info channel."""
        self._credentials(interactive=True)
        return self.channel_info()

    def channel_info(self) -> dict[str, Any] | None:
        if self.mock:
            return {"id": "mock", "title": "Mock Channel (YOUTUBE_MOCK_UPLOAD=true)"}
        youtube = self._service(interactive=False)
        response = youtube.channels().list(part="snippet,statistics", mine=True).execute()
        items = response.get("items") or []
        if not items:
            return None
        item = items[0]
        return {
            "id": item.get("id"),
            "title": item.get("snippet", {}).get("title"),
            "subscribers": item.get("statistics", {}).get("subscriberCount"),
            "videos": item.get("statistics", {}).get("videoCount"),
        }

    # ------------------------------------------------------------------ upload
    def upload(
        self,
        video_path: str,
        metadata: Metadata,
        privacy_status: str = "private",
        made_for_kids: bool = False,
        category_id: str = "22",
        default_language: str = "id",
        notify_subscribers: bool = True,
    ) -> str:
        path = Path(video_path)
        if not path.exists():
            raise NonRetryableError(f"File video tidak ditemukan: {path}")

        if self.mock:
            short_hash = hashlib.md5(f"{path}_{metadata.title}".encode()).hexdigest()[:8]
            video_id = f"demo_{short_hash}"
            log.info("[MOCK UPLOAD] %s | judul: %s | privasi: %s", path, metadata.title, privacy_status)
            log.info("[MOCK UPLOAD] ID simulasi: https://youtube.com/shorts/%s", video_id)
            return video_id

        try:
            from googleapiclient.errors import HttpError
            from googleapiclient.http import MediaFileUpload
        except ModuleNotFoundError as e:
            raise NonRetryableError("Dependensi YouTube belum terinstall. Jalankan: pip install -r requirements.txt") from e

        youtube = self._service(interactive=self.interactive_auth)
        body = {
            "snippet": {
                "title": metadata.title[:100],
                "description": metadata.description_with_hashtags(),
                "tags": metadata.tags,
                "categoryId": category_id,
                "defaultLanguage": default_language,
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": made_for_kids,
            },
        }
        media = MediaFileUpload(str(path), chunksize=CHUNK_SIZE, resumable=True, mimetype="video/mp4")
        request = youtube.videos().insert(
            part="snippet,status", body=body, media_body=media, notifySubscribers=notify_subscribers
        )
        response = None
        try:
            while response is None:
                status, response = request.next_chunk(num_retries=5)
                if status:
                    log.info("Upload %s: %.1f%%", path.name, status.progress() * 100)
        except HttpError as e:
            self._raise_translated(e)
        video_id = response["id"]
        log.info("Upload selesai: https://youtube.com/shorts/%s", video_id)
        return video_id

    @staticmethod
    def _raise_translated(error: Any) -> None:
        status, reason, message = _parse_http_error(error)
        text = f"YouTube API HTTP {status} [{reason}]: {message}"
        if reason in QUOTA_REASONS:
            # Kuota default 10.000 unit/hari; videos.insert = 1.600 unit (~6 upload/hari). Reset 00:00 Pacific Time.
            raise RetryLaterError(f"{text} - kuota YouTube API habis, dicoba lagi dalam 1 jam.", delay_seconds=3600) from error
        if reason == "uploadLimitExceeded":
            raise RetryLaterError(f"{text} - batas upload channel tercapai, dicoba lagi dalam 6 jam.", delay_seconds=6 * 3600) from error
        if status in (401, 403) or reason in PERMANENT_REASONS:
            raise NonRetryableError(text) from error
        raise RuntimeError(text) from error
