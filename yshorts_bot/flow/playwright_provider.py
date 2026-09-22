from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import FlowConfig
from ..models import SegmentPrompt
from .base import FlowProvider
from .prompt_files import expected_video_path, find_ready_video, read_state, state_path, write_prompt_file, write_state

log = logging.getLogger(__name__)


class PlaywrightFlowProvider(FlowProvider):
    """Otomasi browser Google Flow berbasis Playwright (EKSPERIMENTAL, best-effort).

    Prinsip yang dijaga (lihat README bagian "Google Flow & ToS"):
    - Login dilakukan MANUAL oleh pengguna pada jendela Chrome dengan profil persisten.
      Tidak ada bypass login, CAPTCHA, limit, atau trik menyembunyikan otomasi.
    - Satu segmen hanya dicoba SATU kali secara otomatis (ditandai file marker) agar kredit
      Flow tidak terpakai ganda. Jika gagal, worker otomatis jatuh ke mode manual:
      unggah hasil lewat dashboard, atau hapus file marker `job_<id>_segment_<n>.browser.json`
      untuk mencoba otomatis lagi.
    - Selector UI dapat dikalibrasi lewat `flow.selectors` di config.json karena UI Flow bisa berubah.
    - Tidak memblokir worker lebih lama dari `generation_timeout_seconds`.
    """

    name = "browser"
    description = "Otomasi browser Google Flow (eksperimental, login manual, 1 percobaan/segmen) + fallback manual"

    def __init__(self, prompt_dir: str, flow_download_dir: str, flow_cfg: FlowConfig, log_dir: str = "data/logs"):
        self.prompt_dir = Path(prompt_dir)
        self.flow_download_dir = Path(flow_download_dir)
        self.cfg = flow_cfg
        self.log_dir = Path(log_dir)
        self.user_data_dir = Path(flow_cfg.browser_user_data_dir)
        for d in (self.prompt_dir, self.flow_download_dir, self.user_data_dir, self.log_dir):
            d.mkdir(parents=True, exist_ok=True)
        self._announced: set[tuple[int, int]] = set()

    # ------------------------------------------------------------------ API
    def request_segment(self, job_id: int, niche: str, segment: SegmentPrompt) -> Path | None:
        write_prompt_file(self.prompt_dir, self.flow_download_dir, job_id, niche, segment)
        ready = find_ready_video(self.flow_download_dir, job_id, segment.index, self.cfg.min_video_bytes, self.cfg.stable_seconds)
        if ready:
            return ready

        dest = expected_video_path(self.flow_download_dir, job_id, segment.index)
        marker = state_path(self.prompt_dir, job_id, segment.index, "browser")
        state = read_state(marker)
        if state:
            if (job_id, segment.index) not in self._announced:
                self._announced.add((job_id, segment.index))
                log.info(
                    "Job #%s segmen %s: otomasi browser sudah dicoba (%s). Menunggu file manual di %s "
                    "(hapus %s untuk mencoba otomatis lagi).",
                    job_id, segment.index, state.get("result"), dest, marker.name,
                )
            return None

        started = datetime.now(timezone.utc).isoformat()
        write_state(marker, {"started_at": started, "result": "running"})
        result_path: Path | None = None
        result, error = "no_download", None
        try:
            result_path = self._run_browser_automation(job_id, segment, dest)
            if result_path:
                result = "downloaded"
        except Exception as e:  # noqa: BLE001 - semua kegagalan otomasi jatuh ke mode manual
            result, error = "error", str(e)[:500]
            log.warning("Job #%s segmen %s: otomasi browser gagal: %s. Beralih ke mode manual.", job_id, segment.index, e)
        write_state(marker, {"started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(), "result": result, "error": error})

        if result_path and result_path.exists() and result_path.stat().st_size >= self.cfg.min_video_bytes:
            return result_path
        return None

    # ------------------------------------------------------------------ Playwright
    def _launch(self, playwright: Any):
        kwargs: dict[str, Any] = dict(
            user_data_dir=str(self.user_data_dir.resolve()),
            headless=self.cfg.headless,
            accept_downloads=True,
            args=["--no-first-run", "--no-default-browser-check"],
            viewport={"width": 1366, "height": 900},
        )
        channel = (self.cfg.browser_channel or "").strip()
        if channel:
            try:
                return playwright.chromium.launch_persistent_context(channel=channel, **kwargs)
            except Exception as e:  # noqa: BLE001
                log.warning("Browser channel '%s' tidak tersedia (%s). Memakai Chromium bawaan Playwright.", channel, e)
        return playwright.chromium.launch_persistent_context(**kwargs)

    @staticmethod
    def _first_visible(page: Any, selectors: list[str]):
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.count() > 0 and locator.is_visible():
                    return locator, selector
            except Exception:  # noqa: BLE001
                continue
        return None, None

    def _screenshot(self, page: Any, job_id: int, index: int, label: str) -> None:
        path = self.log_dir / f"flow_browser_job_{job_id}_seg_{index}_{label}.png"
        try:
            page.screenshot(path=str(path))
            log.info("Screenshot diagnostik: %s", path)
        except Exception:  # noqa: BLE001
            pass

    def _wait_for_login(self, page: Any) -> bool:
        def needs_login() -> bool:
            if "accounts.google.com" in (page.url or ""):
                return True
            locator, _ = self._first_visible(page, [self.cfg.selectors.sign_in])
            return locator is not None

        if not needs_login():
            return True
        if self.cfg.headless or self.cfg.login_wait_seconds <= 0:
            return False
        log.info(
            "[BROWSER] Silakan login ke akun Google Anda di jendela Chrome yang terbuka (menunggu maks %ss)...",
            self.cfg.login_wait_seconds,
        )
        deadline = time.time() + self.cfg.login_wait_seconds
        while time.time() < deadline:
            page.wait_for_timeout(2000)
            if not needs_login():
                log.info("[BROWSER] Login terdeteksi, melanjutkan.")
                return True
        return False

    def _run_browser_automation(self, job_id: int, segment: SegmentPrompt, dest: Path) -> Path | None:
        try:
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as e:
            raise RuntimeError("Paket playwright belum terinstall: pip install playwright && playwright install chromium") from e

        log.info("[BROWSER] Membuka Google Flow untuk Job #%s segmen %s...", job_id, segment.index)
        with sync_playwright() as p:
            context = self._launch(p)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(30_000)
                page.goto(self.cfg.flow_url, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(3000)

                if not self._wait_for_login(page):
                    self._screenshot(page, job_id, segment.index, "login")
                    raise RuntimeError("Belum login ke Google Flow. Login manual di jendela Chrome, lalu ulangi.")

                input_el, used = self._first_visible(page, self.cfg.selectors.prompt_input)
                if input_el is None:
                    self._screenshot(page, job_id, segment.index, "no_input")
                    raise RuntimeError("Kolom prompt tidak ditemukan. Sesuaikan flow.selectors.prompt_input di config.json.")
                log.info("[BROWSER] Kolom prompt ditemukan (%s).", used)
                input_el.click()
                page.wait_for_timeout(300)
                try:
                    input_el.fill(segment.prompt)
                except Exception:  # noqa: BLE001 - contenteditable tertentu tidak mendukung fill
                    page.keyboard.type(segment.prompt)
                page.wait_for_timeout(500)

                if self.cfg.auto_submit:
                    button, used_btn = self._first_visible(page, self.cfg.selectors.generate_button)
                    if button is not None:
                        log.info("[BROWSER] Mengklik tombol generate (%s).", used_btn)
                        button.click()
                    else:
                        log.warning("[BROWSER] Tombol generate tidak ditemukan; klik manual di jendela browser.")
                else:
                    log.info("[BROWSER] auto_submit=false: klik Generate secara manual di jendela browser.")

                log.info("[BROWSER] Menunggu video selesai & tombol download (maks %ss)...", self.cfg.generation_timeout_seconds)
                deadline = time.time() + self.cfg.generation_timeout_seconds
                while time.time() < deadline:
                    for selector in self.cfg.selectors.download_button:
                        try:
                            candidates = page.locator(selector).all()[:5]
                        except Exception:  # noqa: BLE001
                            continue
                        for el in candidates:
                            try:
                                if not el.is_visible():
                                    continue
                                with page.expect_download(timeout=20_000) as download_info:
                                    el.click()
                                download = download_info.value
                                tmp = dest.with_name(dest.name + ".part")
                                download.save_as(str(tmp))
                                os.replace(tmp, dest)
                                log.info("[BROWSER] Video terunduh: %s (%s bytes)", dest, dest.stat().st_size)
                                return dest
                            except Exception:  # noqa: BLE001 - coba elemen/selector berikutnya
                                continue
                    if dest.exists() and dest.stat().st_size >= self.cfg.min_video_bytes:
                        return dest
                    page.wait_for_timeout(max(self.cfg.poll_seconds, 2) * 1000)

                self._screenshot(page, job_id, segment.index, "timeout")
                log.warning("[BROWSER] Waktu habis menunggu unduhan. Unduh manual lalu simpan ke %s.", dest)
                return None
            finally:
                context.close()
