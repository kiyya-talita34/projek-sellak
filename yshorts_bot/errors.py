from __future__ import annotations


class YShortsError(Exception):
    """Kelas dasar semua error aplikasi."""


class ConfigError(YShortsError):
    """Konfigurasi tidak valid atau tidak bisa dibaca."""


class NonRetryableError(YShortsError):
    """Kegagalan permanen (mis. kredensial/izin salah).

    Worker langsung menandai job sebagai `failed` tanpa retry otomatis,
    supaya pengguna segera melihat penyebabnya di dashboard.
    """


class RetryLaterError(YShortsError):
    """Gagal sementara karena kondisi luar (kuota API, rate limit).

    Job dijadwalkan ulang setelah `delay_seconds` tanpa menghabiskan jatah retry.
    """

    def __init__(self, message: str, delay_seconds: int = 3600):
        super().__init__(message)
        self.delay_seconds = int(delay_seconds)
