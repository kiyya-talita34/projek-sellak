from __future__ import annotations

import logging
import os
from pathlib import Path

from typing import Any

from ..models import Metadata

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


class YouTubeUploader:
    def __init__(self, client_secret_file: str | None = None, token_file: str | None = None):
        self.client_secret_file = client_secret_file or os.getenv("YOUTUBE_CLIENT_SECRET_FILE", "client_secret.json")
        self.token_file = token_file or os.getenv("YOUTUBE_TOKEN_FILE", "secrets/token.json")
        Path(self.token_file).parent.mkdir(parents=True, exist_ok=True)

    def _credentials(self) -> Any:
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ModuleNotFoundError as e:
            raise RuntimeError("Dependensi YouTube belum terinstall. Jalankan: pip install -r requirements.txt") from e

        creds = None
        if Path(self.token_file).exists():
            creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not Path(self.client_secret_file).exists():
                    raise FileNotFoundError(
                        f"OAuth client secret tidak ditemukan: '{self.client_secret_file}'.\n"
                        "Untuk mengunggah langsung ke YouTube, unduh 'client_secret.json' dari Google Cloud Console.\n"
                        "Atau aktifkan mode simulasi testing dengan mengisi YOUTUBE_MOCK_UPLOAD=true di file .env."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(self.client_secret_file, SCOPES)
                creds = flow.run_local_server(port=0)
            Path(self.token_file).write_text(creds.to_json(), encoding="utf-8")
        return creds

    def upload(self, video_path: str, metadata: Metadata, privacy_status: str = "private", made_for_kids: bool = False, category_id: str = "22") -> str:
        # Dukung simulasi upload untuk testing pipeline
        if os.getenv("YOUTUBE_MOCK_UPLOAD", "false").lower() in ("true", "1", "yes"):
            import hashlib
            short_hash = hashlib.md5(f"{video_path}_{metadata.title}".encode()).hexdigest()[:8]
            video_id = f"demo_{short_hash}"
            log.info("[MOCK UPLOAD] Video berhasil diverifikasi: %s", video_path)
            log.info("[MOCK UPLOAD] Judul: %s", metadata.title)
            log.info("[MOCK UPLOAD] ID Simulasi: https://youtube.com/shorts/%s", video_id)
            return video_id

        try:
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
        except ModuleNotFoundError as e:
            raise RuntimeError("Dependensi YouTube belum terinstall. Jalankan: pip install -r requirements.txt") from e

        youtube = build("youtube", "v3", credentials=self._credentials())
        body = {
            "snippet": {
                "title": metadata.title,
                "description": metadata.description_with_hashtags(),
                "tags": metadata.tags,
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": made_for_kids,
            },
        }
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                log.info("Upload progress: %.1f%%", status.progress() * 100)
        video_id = response["id"]
        log.info("Upload selesai: https://youtube.com/shorts/%s", video_id)
        return video_id
