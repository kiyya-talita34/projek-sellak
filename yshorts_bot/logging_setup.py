from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def setup_logging(log_dir: str = "data/logs", level: int = logging.INFO, name: str = "worker") -> Path:
    """Konfigurasi logging ke konsol + file berputar.

    Setiap proses (worker / dashboard) memakai file log sendiri supaya rotasi file
    tidak saling bertabrakan (masalah umum di Windows saat dua proses memegang file yang sama).
    """
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(LOG_FORMAT)

    # Hindari UnicodeEncodeError di konsol Windows (cp1252) untuk karakter non-ASCII.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(errors="replace")
            except Exception:
                pass

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    log_path = Path(log_dir) / f"{name}.log"
    file_handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # Redam logger pihak ketiga yang terlalu ramai
    for noisy in ("googleapiclient.discovery_cache", "googleapiclient.discovery", "urllib3", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return log_path
