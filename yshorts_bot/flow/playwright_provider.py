from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from .base import FlowProvider
from ..models import SegmentPrompt

log = logging.getLogger(__name__)


class PlaywrightFlowProvider(FlowProvider):
    """Otomasi browser Google Flow Ultra berbasis Playwright dengan persistent session Chrome.
    
    Fitur:
    - Persistent Profile (cukup login sekali, sesi tersimpan permanen di disk).
    - Membuka URL Google Flow Ultra secara otomatis.
    - Mengisi teks prompt segmen ke input generator.
    - Memicu tombol Generate dan memantau proses render hingga selesai.
    - Mengunduh file video hasil generate langsung ke data/flow_downloads/job_{id}_segment_{index}.mp4.
    - Screenshot debugging otomatis jika terjadi kendala antarmuka.
    - Fallback aman: jika terjadi timeout di browser, tetap memantau folder unduhan secara manual.
    """

    def __init__(
        self,
        prompt_dir: str,
        flow_download_dir: str,
        flow_url: str = "https://labs.google/flow",
        browser_user_data_dir: str = "data/browser_profile",
        headless: bool = False,
        generation_timeout_seconds: int = 600,
        wait_timeout_minutes: int = 1440,
        poll_seconds: int = 5,
    ):
        self.prompt_dir = Path(prompt_dir)
        self.flow_download_dir = Path(flow_download_dir)
        self.flow_url = flow_url
        self.browser_user_data_dir = Path(browser_user_data_dir)
        self.headless = headless
        self.generation_timeout = generation_timeout_seconds
        self.wait_timeout = wait_timeout_minutes * 60
        self.poll_seconds = poll_seconds

        self.prompt_dir.mkdir(parents=True, exist_ok=True)
        self.flow_download_dir.mkdir(parents=True, exist_ok=True)
        self.browser_user_data_dir.mkdir(parents=True, exist_ok=True)

    def request_segment(self, job_id: int, niche: str, segment: SegmentPrompt) -> Path:
        prompt_path = self.prompt_dir / f"job_{job_id}_segment_{segment.index}.txt"
        primary_video = self.flow_download_dir / f"job_{job_id}_segment_{segment.index}.mp4"

        # Simpan file prompt teks untuk cadangan
        prompt_path.write_text(
            f"=== GOOGLE FLOW PROMPT - JOB #{job_id} SEGMENT {segment.index} ===\n"
            f"Niche: {niche}\n"
            f"Durasi Target: {segment.duration_seconds} detik (Vertical 9:16)\n\n"
            f"--- PROMPT ---\n"
            f"{segment.prompt}\n",
            encoding="utf-8",
        )
        log.info("Prompt segmen %s siap: %s", segment.index, prompt_path)

        # Jika video sudah ada sebelumnya di folder unduhan, gunakan langsung
        if primary_video.exists() and primary_video.stat().st_size > 1024:
            log.info("Video segmen sudah ada di: %s", primary_video)
            return primary_video

        # Coba jalankan otomasi browser Playwright
        try:
            downloaded = self._run_browser_automation(job_id, segment, primary_video)
            if downloaded and downloaded.exists() and downloaded.stat().st_size > 1024:
                return downloaded
        except Exception as e:
            log.warning("Otomasi browser mengalami kendala: %s. Beralih ke pemantauan manual...", e)

        # Fallback ke polling folder jika otomasi menunggu interaksi user
        log.info("Menunggu video di: %s (timeout %ds)", primary_video, self.wait_timeout)
        start = time.time()
        while time.time() - start < self.wait_timeout:
            if primary_video.exists() and primary_video.stat().st_size > 1024:
                log.info("Video ditemukan: %s (ukuran: %s bytes)", primary_video, primary_video.stat().st_size)
                return primary_video
            time.sleep(self.poll_seconds)

        raise TimeoutError(f"Timeout menunggu video {primary_video}.")

    def _run_browser_automation(self, job_id: int, segment: SegmentPrompt, dest_path: Path) -> Path | None:
        from playwright.sync_api import sync_playwright

        log.info("[BROWSER] Membuka Chrome untuk Job #%s Segmen %s...", job_id, segment.index)
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(self.browser_user_data_dir.resolve()),
                channel="chrome",
                headless=self.headless,
                accept_downloads=True,
                args=[
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-blink-features=AutomationControlled",
                ],
                viewport={"width": 1366, "height": 900},
            )

            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(30000)

                log.info("[BROWSER] Membuka URL: %s", self.flow_url)
                page.goto(self.flow_url, wait_until="domcontentloaded", timeout=60000)
                time.sleep(3)

                # Cek apakah ada tombol Sign In / login yang terdeteksi
                sign_in = page.locator("text=/Sign in|Masuk|Log in/i").first
                if sign_in.is_visible():
                    log.info("[BROWSER] Terdeteksi tombol login. Menunggu pengguna login ke akun Google...")
                    # Berikan waktu agar user bisa login di jendela browser yang terbuka
                    for _ in range(30):
                        if not sign_in.is_visible():
                            break
                        time.sleep(2)

                # Cari elemen input prompt yang umum pada UI Google Flow / Video AI
                input_selectors = [
                    "textarea[placeholder*='prompt' i]",
                    "textarea[placeholder*='describe' i]",
                    "textarea",
                    "div[contenteditable='true']",
                    "[aria-label*='prompt' i]",
                    "input[type='text'][placeholder*='prompt' i]",
                ]

                input_el = None
                for sel in input_selectors:
                    loc = page.locator(sel).first
                    try:
                        if loc.is_visible(timeout=3000):
                            input_el = loc
                            log.info("[BROWSER] Input prompt ditemukan dengan selector: %s", sel)
                            break
                    except Exception:
                        continue

                if input_el:
                    input_el.click()
                    time.sleep(0.5)
                    try:
                        input_el.fill(segment.prompt)
                    except Exception:
                        page.keyboard.type(segment.prompt)
                    log.info("[BROWSER] Prompt berhasil dimasukkan ke form!")
                    time.sleep(1)

                    # Cari tombol Generate / Create / Submit
                    btn_selectors = [
                        "button:has-text('Generate')",
                        "button:has-text('Create')",
                        "button:has-text('Buat')",
                        "button[aria-label*='generate' i]",
                        "button[aria-label*='create' i]",
                    ]

                    for btn_sel in btn_selectors:
                        btn = page.locator(btn_sel).first
                        try:
                            if btn.is_visible(timeout=2000):
                                log.info("[BROWSER] Mengklik tombol: %s", btn_sel)
                                btn.click()
                                break
                        except Exception:
                            continue

                    log.info("[BROWSER] Menunggu proses pembuatan video dan unduhan (maks %ds)...", self.generation_timeout)

                    # Tunggu unduhan jika tombol download muncul atau dipicu
                    start_gen = time.time()
                    while time.time() - start_gen < self.generation_timeout:
                        # Cek apakah ada tombol download yang siap
                        dl_btns = page.locator("button:has-text('Download'), [aria-label*='download' i], a[download]").all()
                        for dl in dl_btns:
                            try:
                                if dl.is_visible():
                                    log.info("[BROWSER] Tombol download ditemukan! Memicu unduhan...")
                                    with page.expect_download(timeout=15000) as download_info:
                                        dl.click()
                                    download = download_info.value
                                    download.save_as(str(dest_path))
                                    log.info("[BROWSER] Video berhasil diunduh ke: %s (ukuran: %s bytes)", dest_path, dest_path.stat().st_size)
                                    return dest_path
                            except Exception:
                                continue

                        # Cek apakah file sudah muncul di folder unduhan lokal
                        if dest_path.exists() and dest_path.stat().st_size > 1024:
                            log.info("[BROWSER] File video terdeteksi di disk: %s", dest_path)
                            return dest_path

                        time.sleep(self.poll_seconds)

                else:
                    log.warning("[BROWSER] Input prompt belum terdeteksi secara otomatis di halaman %s.", self.flow_url)
                    # Simpan screenshot untuk diagnostik
                    shot_path = Path("data/logs") / f"flow_browser_job_{job_id}_seg_{segment.index}.png"
                    try:
                        page.screenshot(path=str(shot_path))
                        log.info("[BROWSER] Screenshot diagnostik disimpan di: %s", shot_path)
                    except Exception:
                        pass

            finally:
                context.close()

        return None
