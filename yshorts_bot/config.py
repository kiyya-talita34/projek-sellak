from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .errors import ConfigError

log = logging.getLogger(__name__)

EXAMPLE_CONFIG_NAME = "config.example.json"

DEFAULT_NICHE_PRESETS = [
    "Fakta unik dunia",
    "Cerita horor misteri",
    "Fakta menarik tentang hewan",
    "Motivasi & pola pikir sukses",
    "Misteri sejarah kuno",
    "Perkembangan teknologi AI masa depan",
]


# ---------------------------------------------------------------------------
# Util waktu
# ---------------------------------------------------------------------------
def parse_hhmm(value: str) -> tuple[int, int]:
    """Parse 'HH:MM' (menerima juga 'H:MM', 'HH.MM', atau 'HH')."""
    s = str(value).strip().replace(".", ":")
    if not s:
        raise ValueError("Waktu kosong")
    parts = s.split(":")
    if len(parts) == 1:
        parts.append("0")
    if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
        raise ValueError(f"Format jam tidak valid: '{value}'. Gunakan HH:MM, contoh 08:00")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Jam di luar rentang 00:00-23:59: '{value}'")
    return hour, minute


def normalize_hhmm(value: str) -> str:
    hour, minute = parse_hhmm(value)
    return f"{hour:02d}:{minute:02d}"


# ---------------------------------------------------------------------------
# Model konfigurasi
# ---------------------------------------------------------------------------
class ScheduleConfig(BaseModel):
    mode: Literal["interval", "specific_times"] = "interval"
    interval_hours: float = Field(default=3, gt=0, le=24 * 31)
    # "now" berarti slot pertama = saat job dibuat
    start_time: str = "08:00"
    specific_times: list[str] = Field(default_factory=lambda: ["08:00", "12:00", "18:00", "21:00"])
    timezone: str = "Asia/Jakarta"

    @field_validator("start_time")
    @classmethod
    def _validate_start_time(cls, v: str) -> str:
        v = (v or "").strip()
        if v.lower() in ("", "now", "sekarang"):
            return "now"
        return normalize_hhmm(v)

    @field_validator("specific_times")
    @classmethod
    def _validate_specific_times(cls, v: list[str]) -> list[str]:
        return sorted({normalize_hhmm(t) for t in v if str(t).strip()})

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except Exception as e:  # ZoneInfoNotFoundError / ValueError
            raise ValueError(
                f"Timezone tidak dikenal: '{v}' (contoh: Asia/Jakarta). "
                "Di Windows pastikan paket 'tzdata' terinstall."
            ) from e
        return v

    @model_validator(mode="after")
    def _validate_mode(self) -> ScheduleConfig:
        if self.mode == "specific_times" and not self.specific_times:
            raise ValueError("schedule.specific_times tidak boleh kosong saat mode='specific_times'")
        return self


class PathsConfig(BaseModel):
    data_dir: str = "data"
    prompt_dir: str = "data/prompts"
    flow_download_dir: str = "data/flow_downloads"
    output_dir: str = "data/output"
    failed_dir: str = "data/failed"
    log_dir: str = "data/logs"
    db_path: str = "data/yshorts.sqlite3"


class FlowSelectors(BaseModel):
    """Selector CSS/Playwright untuk UI Google Flow. Bisa dikalibrasi dari config.json bila UI berubah."""

    prompt_input: list[str] = Field(
        default_factory=lambda: [
            "textarea[placeholder*='prompt' i]",
            "textarea[placeholder*='describe' i]",
            "[aria-label*='prompt' i]",
            "textarea",
            "div[contenteditable='true']",
        ]
    )
    generate_button: list[str] = Field(
        default_factory=lambda: [
            "button:has-text('Generate')",
            "button:has-text('Create')",
            "button:has-text('Buat')",
            "button[aria-label*='generate' i]",
            "button[aria-label*='create' i]",
            "button[type='submit']",
        ]
    )
    download_button: list[str] = Field(
        default_factory=lambda: [
            "button:has-text('Download')",
            "[aria-label*='download' i]",
            "a[download]",
        ]
    )
    sign_in: str = "text=/Sign in|Masuk|Log in/i"


class VeoApiConfig(BaseModel):
    """Provider resmi: Veo melalui Gemini API (berbayar per video, terpisah dari langganan Flow/Ultra)."""

    model: str = "veo-3.1-generate-preview"
    resolution: Literal["720p", "1080p", "4k"] = "720p"
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    duration_seconds: Literal[4, 6, 8] | None = 8
    negative_prompt: str = ""
    poll_seconds: int = Field(default=15, ge=5)
    timeout_seconds: int = Field(default=1200, ge=60)


class FlowConfig(BaseModel):
    provider: Literal["manual", "browser", "playwright", "veo_api", "browser_stub"] = "manual"
    flow_url: str = "https://labs.google/flow"
    headless: bool = False
    browser_channel: str = "chrome"
    browser_user_data_dir: str = "data/browser_profile"
    auto_submit: bool = True
    login_wait_seconds: int = Field(default=180, ge=0)
    generation_timeout_seconds: int = Field(default=600, ge=30)
    wait_timeout_minutes: int = Field(default=1440, ge=1)
    poll_seconds: int = Field(default=10, ge=1)
    min_video_bytes: int = Field(default=1024, ge=1)
    stable_seconds: int = Field(default=3, ge=0)
    # Folder "inbox": file video apa pun yang diletakkan di sini otomatis dipasangkan
    # ke job/segmen yang sedang menunggu (urut job & segmen). Cocok sebagai folder download browser.
    inbox_dir: str = "data/flow_downloads/inbox"
    inbox_auto_assign: bool = True
    selectors: FlowSelectors = Field(default_factory=FlowSelectors)
    veo: VeoApiConfig = Field(default_factory=VeoApiConfig)


class YouTubeConfig(BaseModel):
    privacy_status: Literal["private", "unlisted", "public"] = "private"
    made_for_kids: bool = False
    category_id: str = "22"
    default_language: str = "id"
    notify_subscribers: bool = True
    # true = job berhenti di 'awaiting_approval' setelah metadata siap; Anda meninjau/mengedit
    # judul-deskripsi di dashboard lalu klik Setujui sebelum upload.
    require_approval: bool = False
    # Opsional: ID playlist tujuan (butuh izin OAuth 'youtube' penuh saat login).
    playlist_id: str = ""


class RetryConfig(BaseModel):
    max_attempts: int = Field(default=3, ge=1)
    base_delay_seconds: int = Field(default=60, ge=1)
    max_delay_seconds: int = Field(default=3600, ge=1)


class VideoConfig(BaseModel):
    width: int = Field(default=1080, ge=16)
    height: int = Field(default=1920, ge=16)
    fps: int = Field(default=30, ge=1, le=60)
    video_bitrate: str = "8M"
    audio_bitrate: str = "192k"
    audio_sample_rate: int = 48000
    x264_preset: Literal[
        "ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"
    ] = "medium"
    # crop = isi penuh 9:16 (potong sisi), pad = bar hitam, blur = latar blur
    fit_mode: Literal["crop", "pad", "blur"] = "crop"
    min_duration_seconds: float = Field(default=16.0, ge=0)
    pad_to_min_duration: bool = True
    ffmpeg_binary: str | None = None
    # Musik latar: file audio (mp3/m4a/wav/ogg/flac) dipilih acak dari folder ini.
    # off = tidak pernah, auto = hanya bila semua segmen tanpa suara, always = selalu dicampur di bawah audio asli
    background_music_dir: str | None = "data/music"
    background_music_mode: Literal["off", "auto", "always"] = "auto"
    background_music_volume: float = Field(default=0.15, ge=0.0, le=1.0)
    background_music_fade_seconds: float = Field(default=1.5, ge=0.0)


class WorkerConfig(BaseModel):
    idle_sleep_seconds: int = Field(default=5, ge=1)
    # Heartbeat dikirim dari thread terpisah; dashboard menganggap worker mati bila > 90 detik tanpa detak.
    heartbeat_seconds: int = Field(default=15, ge=1, le=30)
    reload_config: bool = True


class AIConfig(BaseModel):
    """Pengarah gaya AI. Prompt video tetap bahasa Inggris (paling akurat untuk Veo/Flow)."""

    language: str = "id"  # bahasa untuk ide, hook, judul, deskripsi (kode ISO: id, en, ms, ...)
    style_notes: str = ""  # preferensi tambahan, mis. "nada dramatis, akhiri dengan pertanyaan"
    temperature: float = Field(default=0.8, ge=0.0, le=2.0)


class NotificationsConfig(BaseModel):
    """Notifikasi Telegram / webhook (Discord, Slack, generik). Kredensial di .env:
    TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID, dan/atau NOTIFY_WEBHOOK_URL."""

    enabled: bool = False
    on_done: bool = True
    on_failed: bool = True
    on_awaiting_approval: bool = True
    dashboard_url: str = "http://127.0.0.1:8000"


class MaintenanceConfig(BaseModel):
    delete_segments_after_upload: bool = False
    delete_output_after_upload: bool = False


class AppConfig(BaseModel):
    niche: str = "Fakta menarik dunia"
    total_videos: int = Field(default=10, ge=1)
    segments_per_video: int = Field(default=2, ge=1, le=6)
    segment_duration_seconds: int = Field(default=8, ge=1)
    niche_presets: list[str] = Field(default_factory=lambda: list(DEFAULT_NICHE_PRESETS))
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    flow: FlowConfig = Field(default_factory=FlowConfig)
    youtube: YouTubeConfig = Field(default_factory=YouTubeConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    video: VideoConfig = Field(default_factory=VideoConfig)
    worker: WorkerConfig = Field(default_factory=WorkerConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    maintenance: MaintenanceConfig = Field(default_factory=MaintenanceConfig)

    @field_validator("niche_presets")
    @classmethod
    def _validate_presets(cls, v: list[str]) -> list[str]:
        cleaned = [str(x).strip() for x in v if str(x).strip()]
        return cleaned[:12]

    @property
    def planned_duration_seconds(self) -> int:
        return self.segments_per_video * self.segment_duration_seconds


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------
def load_env_files(config_path: str | Path | None = None) -> list[Path]:
    """Muat file .env dari: folder config.json -> folder kerja -> root proyek (tanpa menimpa env yang sudah ada).

    `load_dotenv()` bawaan mencari .env relatif terhadap file modul, sehingga .env di folder kerja
    pengguna bisa terlewat bila program dijalankan dari direktori lain.
    """
    candidates: list[Path] = []
    if config_path:
        candidates.append(Path(config_path).resolve().parent / ".env")
    candidates.append(Path.cwd() / ".env")
    candidates.append(Path(__file__).resolve().parent.parent / ".env")
    loaded: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        load_dotenv(candidate, override=False)
        loaded.append(candidate)
    return loaded


def load_config(path: str = "config.json", create_if_missing: bool = True) -> AppConfig:
    """Baca config.json (+ .env). Bila belum ada, salin dari config.example.json atau buat default."""
    load_env_files(path)
    p = Path(path)
    if not p.exists() and create_if_missing:
        example = p.parent / EXAMPLE_CONFIG_NAME
        p.parent.mkdir(parents=True, exist_ok=True)
        if example.exists():
            shutil.copyfile(example, p)
            log.info("config.json belum ada -> disalin dari %s", example)
        else:
            p.write_text(json.dumps(AppConfig().model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
            log.info("config.json belum ada -> dibuat dengan nilai default")

    data: dict = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig") or "{}")
        except json.JSONDecodeError as e:
            raise ConfigError(f"File konfigurasi '{p}' bukan JSON valid: {e}") from e
    try:
        cfg = AppConfig(**data)
    except ValidationError as e:
        raise ConfigError(f"Konfigurasi '{p}' tidak valid:\n{e}") from e

    ensure_dirs(cfg)
    return cfg


def save_config(cfg: AppConfig, path: str = "config.json") -> Path:
    """Simpan konfigurasi secara atomik (tulis ke file sementara lalu rename)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg.model_dump(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


def ensure_dirs(cfg: AppConfig) -> None:
    for value in cfg.paths.model_dump().values():
        path = Path(value)
        if path.suffix:
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            path.mkdir(parents=True, exist_ok=True)
    Path(cfg.flow.inbox_dir).mkdir(parents=True, exist_ok=True)
    if cfg.video.background_music_dir:
        Path(cfg.video.background_music_dir).mkdir(parents=True, exist_ok=True)


def env(name: str, default: str | None = None) -> str | None:
    return os.getenv(name, default)


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")
