"""Skrip pengujian & kalibrasi otomasi browser Google Flow Ultra.

Jalankan perintah:
    python scripts/test_flow_browser.py [--url URL_FLOW] [--headless]

Skrip ini akan membuka Chrome dengan profil persisten di data/browser_profile,
sehingga Anda dapat login akun Google Anda sekali saja dan memastikan
elemen prompt terdeteksi dengan tepat.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Pastikan folder project ada di sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yshorts_bot.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Test Google Flow Ultra Browser Automation")
    parser.add_argument("--url", default=None, help="URL Google Flow Ultra (default dari config.json)")
    parser.add_argument("--headless", action="store_true", help="Jalankan browser tanpa GUI")
    args = parser.parse_args()

    cfg = load_config("config.json")
    flow_url = args.url or cfg.flow.flow_url
    profile_dir = Path(cfg.flow.browser_user_data_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("  PENGUJIAN OTOMASI BROWSER GOOGLE FLOW ULTRA")
    print("=" * 60)
    print(f"URL Target   : {flow_url}")
    print(f"User Data Dir: {profile_dir.resolve()}")
    print(f"Mode GUI     : {'Headless (tanpa GUI)' if args.headless else 'Headed (Jendela Terbuka)'}")
    print("=" * 60)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        print("\n[1/4] Meluncurkan Google Chrome...")
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir.resolve()),
            channel="chrome",
            headless=args.headless,
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
            print(f"[2/4] Membuka halaman: {flow_url}")
            page.goto(flow_url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)

            title = page.title()
            print(f"      Judul Halaman: '{title}'")

            print("\n[3/4] Memeriksa status login...")
            sign_in = page.locator("text=/Sign in|Masuk|Log in/i").first
            try:
                if sign_in.is_visible(timeout=2000):
                    print("      [INFO] Tombol login terdeteksi. Silakan login pada jendela browser yang terbuka.")
                    print("      Menunggu hingga login selesai (tekan Enter di terminal jika sudah selesai)...")
                    if not args.headless:
                        input("      Tekan Enter setelah Anda berhasil login di browser >> ")
                else:
                    print("      [OK] Sesi browser aktif / sudah dalam kondisi login.")
            except Exception:
                print("      [OK] Tidak terdeteksi halaman login.")

            print("\n[4/4] Memeriksa elemen prompt input...")
            input_selectors = [
                "textarea[placeholder*='prompt' i]",
                "textarea[placeholder*='describe' i]",
                "textarea",
                "div[contenteditable='true']",
                "[aria-label*='prompt' i]",
                "input[type='text'][placeholder*='prompt' i]",
            ]

            found_input = None
            for sel in input_selectors:
                loc = page.locator(sel).first
                try:
                    if loc.is_visible(timeout=2000):
                        found_input = sel
                        print(f"      [OK] Input prompt ditemukan dengan selector: {sel}")
                        break
                except Exception:
                    continue

            if not found_input:
                print("      [PERHATIAN] Input prompt belum terdeteksi otomatis dengan selector umum.")
                screenshot_path = Path("data/logs/test_flow_page.png")
                screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(screenshot_path))
                print(f"      Screenshot halaman disimpan di: {screenshot_path}")

            print("\n" + "=" * 60)
            print("  PENGUJIAN SELESAI - Browser akan ditutup dalam 5 detik...")
            print("=" * 60)
            time.sleep(5)

        finally:
            context.close()


if __name__ == "__main__":
    main()
