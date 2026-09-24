"""Skrip login & kalibrasi otomasi browser Google Flow (provider `browser`).

    python scripts/test_flow_browser.py [--url URL_FLOW] [--headless]

Membuka Chrome/Chromium dengan profil persisten (config: flow.browser_user_data_dir) supaya Anda
login akun Google SEKALI secara manual, lalu memeriksa apakah selector di `flow.selectors`
(config.json) menemukan kolom prompt & tombol generate. Tidak ada bypass login/CAPTCHA.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yshorts_bot.config import load_config  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Login & kalibrasi otomasi browser Google Flow")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--url", default=None, help="URL Google Flow (default dari config.json)")
    parser.add_argument("--headless", action="store_true", help="tanpa jendela (tidak bisa login manual)")
    args = parser.parse_args()

    cfg = load_config(args.config)
    flow_url = args.url or cfg.flow.flow_url
    profile_dir = Path(cfg.flow.browser_user_data_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    selectors = cfg.flow.selectors

    print("=" * 64)
    print("  LOGIN & KALIBRASI BROWSER GOOGLE FLOW")
    print("=" * 64)
    print(f"URL            : {flow_url}")
    print(f"Profil browser : {profile_dir.resolve()}")
    print(f"Channel        : {cfg.flow.browser_channel or 'chromium bawaan'}")
    print(f"Mode           : {'headless' if args.headless else 'jendela terbuka'}")
    print("=" * 64)

    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        raise SystemExit("Playwright belum terinstall: pip install playwright && playwright install chromium")

    with sync_playwright() as p:
        kwargs = dict(
            user_data_dir=str(profile_dir.resolve()),
            headless=args.headless,
            accept_downloads=True,
            args=["--no-first-run", "--no-default-browser-check"],
            viewport={"width": 1366, "height": 900},
        )
        print("\n[1/4] Meluncurkan browser...")
        try:
            context = p.chromium.launch_persistent_context(channel=cfg.flow.browser_channel, **kwargs) if cfg.flow.browser_channel else p.chromium.launch_persistent_context(**kwargs)
        except Exception as e:  # noqa: BLE001
            print(f"      Channel '{cfg.flow.browser_channel}' tidak tersedia ({e}); memakai Chromium bawaan.")
            context = p.chromium.launch_persistent_context(**kwargs)

        try:
            page = context.pages[0] if context.pages else context.new_page()
            print(f"[2/4] Membuka {flow_url}")
            page.goto(flow_url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(3000)
            print(f"      Judul halaman: '{page.title()}'")

            print("\n[3/4] Memeriksa status login...")
            needs_login = "accounts.google.com" in page.url or page.locator(selectors.sign_in).first.count() > 0
            if needs_login and not args.headless:
                print("      Belum login. Silakan login di jendela browser (sesi tersimpan di profil).")
                input("      Tekan Enter setelah login selesai >> ")
                page.goto(flow_url, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(3000)
            elif needs_login:
                print("      [PERHATIAN] Belum login dan mode headless: jalankan tanpa --headless untuk login manual.")
            else:
                print("      [OK] Sesi login aktif.")

            print("\n[4/4] Memeriksa selector...")
            for label, candidates in (("prompt_input", selectors.prompt_input), ("generate_button", selectors.generate_button), ("download_button", selectors.download_button)):
                found = None
                for sel in candidates:
                    try:
                        loc = page.locator(sel).first
                        if loc.count() > 0 and loc.is_visible():
                            found = sel
                            break
                    except Exception:  # noqa: BLE001
                        continue
                status = f"[OK] {found}" if found else "[BELUM] tidak ada yang cocok (tombol download wajar belum ada sebelum generate)"
                print(f"      {label:<16}: {status}")

            shot = Path(cfg.paths.log_dir) / "test_flow_page.png"
            shot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shot))
            print(f"\nScreenshot halaman: {shot}")
            print("Bila selector belum cocok, klik kanan elemen di Chrome -> Inspect, lalu isi flow.selectors di config.json.")
            print("Browser ditutup dalam 5 detik...")
            time.sleep(5)
        finally:
            context.close()


if __name__ == "__main__":
    main()
