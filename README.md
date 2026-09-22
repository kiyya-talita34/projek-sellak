# YShorts Bot Studio 🎬🤖

Otomasi pipeline **YouTube Shorts** berbasis AI: dari *niche* → ide & prompt video → video Google Flow (2×8 detik) → penggabungan FFmpeg 9:16 → judul/deskripsi/hashtag → upload terjadwal lewat **YouTube Data API v3** resmi. Dilengkapi antrean persisten (SQLite), retry otomatis, resume setelah restart, dan web dashboard.

```
NICHE ──► AI: ide + N prompt bersambung ──► Google Flow: video segmen 1..N (±8s)
      ──► FFmpeg: gabung → 1080×1920 MP4 (≥16s, audio dijamin ada)
      ──► AI: judul + deskripsi + hashtag + tags ──► YouTube Data API (OAuth 2.0)
      ──► tunggu jadwal berikutnya ──► ulangi
```

---

## Fitur

| Area | Detail |
| --- | --- |
| **AI Director** | Gemini / OpenAI (atau server kompatibel) / `mock` offline. Menghasilkan ide, hook, gaya visual, N prompt Google Flow yang saling bersambung (subjek, palet warna, pencahayaan konsisten), plus metadata YouTube. Output AI divalidasi & dinormalisasi (jumlah segmen tepat, arahan 9:16 dan larangan teks/logo selalu ada, judul ≤100 karakter, hashtag `#Shorts`). |
| **Sumber video Flow** | 3 mode: `manual` (default, aman ToS), `browser` (otomasi Playwright eksperimental), `veo_api` (jalur **resmi** Veo lewat Gemini API, berbayar). Lihat bagian *Google Flow & ToS*. |
| **FFmpeg** | Normalisasi tiap segmen ke 9:16 (`crop` / `pad` / `blur`), H.264 + AAC 48 kHz, audio hening otomatis bila segmen bisu, perpanjang frame akhir bila total < 16 detik, output ditulis atomik. Binary dicari di `PATH` → paket `imageio-ffmpeg` → `video.ffmpeg_binary`. |
| **Upload YouTube** | OAuth 2.0 Desktop app, upload resumable 8 MB/chunk dengan retry, sanitasi judul/deskripsi/tags sesuai batas API, `privacy_status` private/unlisted/public, mode simulasi `YOUTUBE_MOCK_UPLOAD=true`. Kuota habis → job ditunda otomatis 1 jam tanpa menghabiskan jatah retry. |
| **Penjadwalan** | Interval (1/3/6/12/24 jam atau desimal) dengan jam mulai, atau jam-jam tertentu (`08:00, 12:00, 18:00, 21:00`), timezone bebas. Konten disiapkan lebih dulu; hanya upload yang menunggu jadwal. |
| **Antrean** | SQLite (WAL). Setiap job adalah *state machine*: `queued → planning → waiting_flow_segment_N → merging → metadata → scheduled → uploading → done`. Worker **tidak pernah memblokir** menunggu video: job lain tetap diproses. Retry eksponensial, `failed` menyimpan tahap gagal sehingga *Retry* melanjutkan dari tahap itu (bukan dari awal). Tahan restart. |
| **Dashboard** | `http://127.0.0.1:8000`: buat antrean, preview jadwal, status worker (heartbeat), jeda/lanjut, salin prompt 1-klik, unggah hasil Flow per segmen (drag & drop), tes demo dengan klip uji, pratinjau & unduh video, edit `config.json`, log worker langsung. |
| **CLI** | `doctor` (cek kesiapan), `youtube-auth`, `enqueue`, `run`, `run-once`, `status`, `retry`, `cancel`, `pause/resume`, `dashboard`. |

---

## Persyaratan

- Python **3.10+** (diuji di 3.11–3.14)
- FFmpeg — otomatis tersedia lewat paket `imageio-ffmpeg`; FFmpeg sistem dipakai bila ada di `PATH`
- Akun Google AI Studio (API key Gemini gratis) **atau** OpenAI — opsional, tanpa ini pakai `AI_PROVIDER=mock`
- Untuk upload sungguhan: proyek Google Cloud dengan **YouTube Data API v3** + OAuth client *Desktop app*
- Untuk mode `browser`: Google Chrome/Chromium + `playwright install chromium`

---

## Instalasi & Menjalankan

### Windows: satu klik
Klik dua kali **`start.bat`**. Skrip akan:
1. membuat `.venv` dan menginstall dependensi (sekali saja),
2. membuat `.env` dan `config.json` dari contoh bila belum ada,
3. menjalankan `doctor`, membuka **Dashboard** (`http://127.0.0.1:8000`) dan **Worker** di dua jendela terpisah.

`run_dashboard.bat` / `run_worker.bat` menjalankan masing-masing komponen saja.

### Manual (Windows / macOS / Linux)
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                  # isi API key
cp config.example.json config.json    # atur niche, jadwal, dll.

python -m yshorts_bot doctor          # cek ffmpeg, AI, Flow, YouTube, jadwal
python -m yshorts_bot dashboard       # terminal 1 -> http://127.0.0.1:8000
python -m yshorts_bot run             # terminal 2 -> worker
```

Uji coba cepat tanpa API key apa pun (`.env` bawaan sudah `AI_PROVIDER=mock` + `YOUTUBE_MOCK_UPLOAD=true`): buat job di dashboard → buka **Detail** → klik **Tes demo** → worker menggabungkan klip uji dengan FFmpeg → video 9:16 bisa diputar/diunduh dari dashboard.

---

## Konfigurasi

### `.env` (kredensial)
```env
AI_PROVIDER=gemini                 # gemini | openai | mock
GEMINI_API_KEY=...                 # https://aistudio.google.com/apikey
GEMINI_MODEL=gemini-2.5-flash      # bila model dipensiunkan, otomatis fallback ke model lain
OPENAI_API_KEY=...                 # jika AI_PROVIDER=openai
OPENAI_MODEL=gpt-4o-mini
# OPENAI_BASE_URL=...              # server kompatibel OpenAI (Groq/OpenRouter/Ollama)

YOUTUBE_CLIENT_SECRET_FILE=client_secret.json
YOUTUBE_TOKEN_FILE=secrets/token.json
YOUTUBE_MOCK_UPLOAD=false          # true = simulasi upload
YOUTUBE_INTERACTIVE_AUTH=true      # false = jangan buka browser dari worker (login via youtube-auth)
# FFMPEG_BINARY=C:\ffmpeg\bin\ffmpeg.exe
```

### `config.json` (perilaku) — juga bisa diedit dari tab **Pengaturan** di dashboard
| Kunci | Arti |
| --- | --- |
| `niche`, `total_videos` | nilai default untuk `enqueue` tanpa argumen / tombol *Antrekan dari config* |
| `segments_per_video`, `segment_duration_seconds` | 2 × 8 detik = Shorts ±16 detik |
| `schedule.mode` | `interval` (pakai `interval_hours` + `start_time`, `start_time: "now"` = mulai segera) atau `specific_times` (daftar jam) |
| `schedule.timezone` | mis. `Asia/Jakarta` |
| `flow.provider` | `manual` / `browser` / `veo_api` |
| `flow.wait_timeout_minutes`, `flow.poll_seconds` | berapa lama menunggu video segmen & frekuensi cek |
| `flow.selectors` | selector UI Google Flow untuk mode `browser` (bisa dikalibrasi tanpa mengubah kode) |
| `flow.veo.*` | model/resolusi/durasi untuk mode `veo_api` |
| `youtube.privacy_status` | `private` (disarankan sampai channel/app terverifikasi), `unlisted`, `public` |
| `retry.*` | `max_attempts`, backoff eksponensial `base_delay_seconds` … `max_delay_seconds` |
| `video.*` | resolusi 1080×1920, fps, bitrate, `fit_mode` (`crop`/`pad`/`blur`), `min_duration_seconds` + `pad_to_min_duration` |

Worker memuat ulang `config.json` otomatis saat file berubah (kecuali `paths.*` yang butuh restart). Path relatif di `paths.*` dihitung dari folder kerja (folder tempat perintah dijalankan; `start.bat` selalu memakai folder proyek). `.env` dibaca dari folder `config.json`, folder kerja, lalu root proyek.

Contoh yang diminta di spesifikasi — *Niche: Fakta menarik dunia, 10 video, interval 3 jam, mulai 08:00*:
```bash
python -m yshorts_bot enqueue --niche "Fakta menarik dunia" --count 10 --interval-hours 3 --start-time 08:00
# atau jam tertentu:
python -m yshorts_bot enqueue --niche "Cerita horor" --count 4 --times "08:00,12:00,18:00,21:00"
# atau cukup: python -m yshorts_bot enqueue   (pakai niche/total_videos/schedule dari config.json)
```

---

## Google Flow & Terms of Service — baca ini

**Google Flow tidak menyediakan API publik.** Program ini **tidak mengarang API** dan tidak berusaha mengakali login, CAPTCHA, limit, atau sistem antrean Flow. Tiga cara yang tersedia:

| Mode | Cara kerja | Catatan |
| --- | --- | --- |
| **`manual`** (default) | Worker menyiapkan prompt (file `data/prompts/` + tombol *Salin prompt* di dashboard). Anda tempel di Google Flow dengan akun Ultra Anda, unduh MP4, lalu **drag & drop** ke detail job (atau simpan sebagai `data/flow_downloads/job_<id>_segment_<n>.mp4`). Sisanya (gabung, metadata, upload terjadwal) otomatis. | 100% sesuai ToS, memakai kredit Ultra Anda. Worker tidak memblokir job lain saat menunggu. |
| **`veo_api`** | Jalur **resmi** untuk video Veo secara programatik: Gemini API `models/veo-3.1-generate-preview:predictLongRunning` (aspect ratio 9:16, 8 detik, 720p/1080p), otomatis penuh. | Berbayar per video (billing Gemini API), **terpisah** dari langganan Ultra/kredit Flow. Butuh `GEMINI_API_KEY`. |
| **`browser`** | Otomasi Playwright: membuka Chrome dengan profil persisten, **Anda login sendiri** (sekali), program menempel prompt, klik *Generate*, menunggu tombol *Download*. Satu segmen = **satu** percobaan otomatis (ditandai `job_<id>_segment_<n>.browser.json`); jika gagal jatuh ke mode manual. | Eksperimental & rapuh terhadap perubahan UI (kalibrasi lewat `flow.selectors`, uji dengan `python scripts/test_flow_browser.py`). Tidak ada flag anti-deteksi. Otomasi antarmuka web Google berpotensi melanggar ToS Google/Labs — **risiko akun ditanggung pengguna**; gunakan seperlunya dan hormati limit/kualitas antrean Flow. |

---

## YouTube Data API — setup & batasan
1. [Google Cloud Console](https://console.cloud.google.com/) → buat proyek → aktifkan **YouTube Data API v3**.
2. **Credentials → Create Credentials → OAuth client ID → Desktop app** → unduh JSON → simpan sebagai `client_secret.json` di root proyek. Tambahkan akun Google Anda sebagai *test user* pada OAuth consent screen bila aplikasi masih *Testing*.
3. `python -m yshorts_bot youtube-auth` → browser terbuka → izinkan → token tersimpan di `secrets/token.json`. Set `YOUTUBE_MOCK_UPLOAD=false`.

Batasan resmi yang perlu diketahui:
- **Kuota default 10.000 unit/hari; satu upload = 1.600 unit → ±6 upload/hari.** Jadwal "1 video/jam" membutuhkan [kenaikan kuota](https://support.google.com/youtube/contact/yt_api_form). Bila kuota habis worker menunda job 1 jam lalu mencoba lagi.
- Video yang diunggah lewat proyek API yang **belum diaudit** Google bisa dipaksa *private*. Mulailah dengan `privacy_status: private` dan publikasikan manual dari YouTube Studio, atau ajukan audit.
- Shorts dikenali otomatis dari video vertikal ≤ 3 menit; `#Shorts` disertakan di judul/deskripsi.

---

## Perintah CLI
```bash
python -m yshorts_bot doctor                 # pemeriksaan kesiapan
python -m yshorts_bot init-db
python -m yshorts_bot enqueue [--niche X --count N --mode interval|specific_times --interval-hours H --start-time HH:MM|now --times "08:00,18:00"]
python -m yshorts_bot run                    # worker (Ctrl+C aman, progres tersimpan)
python -m yshorts_bot run-once [--job-id ID] # proses satu tahap (debug)
python -m yshorts_bot status [--json]
python -m yshorts_bot retry ID | cancel ID | pause | resume
python -m yshorts_bot dashboard --host 127.0.0.1 --port 8000
python -m yshorts_bot youtube-auth
python -m yshorts_bot --config lain.json ...  # config alternatif; -v untuk log DEBUG
```

---

## Struktur proyek
```
yshorts_bot/
├── ai/            provider.py (Gemini/OpenAI/mock, retry, JSON toleran) · planner.py (ide, prompt, metadata + normalisasi)
├── db/queue.py    antrean SQLite: state machine, retry-resume, meta (heartbeat/pause), migrasi
├── flow/          base.py (antarmuka non-blocking) · manual.py · playwright_provider.py · veo_api.py · prompt_files.py · factory.py
├── scheduler/     schedule.py (interval / jam tertentu, timezone)
├── video/ffmpeg.py  probe, normalisasi 9:16, gabung, padding durasi, klip uji
├── youtube/uploader.py  OAuth 2.0, upload resumable, terjemahan error kuota/izin
├── worker.py      orkestrator per tahap, heartbeat, single-instance, reload config
├── dashboard.py + templates/dashboard.html  FastAPI + UI
├── cli.py · config.py · errors.py · models.py · logging_setup.py
tests/             pytest: jadwal, antrean, planner, ffmpeg, pipeline end-to-end, dashboard
data/              prompts/ · flow_downloads/ · output/ · failed/ · logs/ · yshorts.sqlite3 (dibuat otomatis, di-gitignore)
```

---

## Pengujian
```bash
pip install -r requirements-dev.txt
python -m pytest
```
Menguji perhitungan jadwal, state machine antrean (retry-resume, cancel, migrasi), normalisasi output AI, penggabungan FFmpeg (crop/pad/blur, padding durasi, audio hening), pipeline end-to-end dengan provider mock, dan seluruh endpoint dashboard.

---

## Troubleshooting
| Gejala | Solusi |
| --- | --- |
| `ffmpeg tidak ditemukan` | `pip install imageio-ffmpeg` (sudah di requirements) atau install FFmpeg & set `FFMPEG_BINARY` |
| Nilai `.env` tidak terbaca | `.env` dibaca dari folder `config.json`, folder kerja, lalu root proyek. Jalankan `doctor` untuk melihat provider/mode yang aktif |
| Worker: `Worker lain (PID …) masih aktif` | hanya satu worker per database; tutup jendela worker lama atau tunggu 90 detik |
| Job `failed` | buka detail → baca error → perbaiki → **Retry** melanjutkan dari tahap yang gagal. Ringkasan juga ada di `data/failed/job_<id>.json` |
| `Gemini menolak permintaan (HTTP 400/403)` | API key salah/tidak aktif; model tidak tersedia → ubah `GEMINI_MODEL` (fallback otomatis mencoba model lain) |
| `quotaExceeded` saat upload | kuota harian YouTube habis; job otomatis ditunda 1 jam. Kurangi frekuensi atau ajukan kenaikan kuota |
| Video di YouTube jadi private | normal untuk proyek API belum diaudit; publikasikan dari YouTube Studio |
| Mode `browser` tidak menemukan kolom prompt | `python scripts/test_flow_browser.py`, lihat screenshot di `data/logs/`, sesuaikan `flow.selectors` |
| Karakter aneh di konsol Windows | log sudah dibuat aman (`errors=replace`); gunakan Windows Terminal untuk tampilan terbaik |

---

## Changelog

### 1.1.0 — perbaikan menyeluruh
- **Worker tidak lagi memblokir**: menunggu video Flow kini non-blocking per job (sebelumnya satu job bisa menahan seluruh antrean sampai 24 jam).
- **Retry cerdas**: job gagal menyimpan tahap gagalnya dan dilanjutkan dari tahap itu (sebelumnya reset ke awal → prompt baru tetapi file video lama terpakai).
- Status `cancelled` terpisah dari `failed`; `RetryLater` (kuota) tidak menghabiskan jatah retry; error permanen (kredensial) langsung `failed` dengan pesan jelas.
- Perbaikan **infinite loop** jadwal saat `specific_times` kosong; jam divalidasi; interval menyusul slot berikutnya bila jam mulai sudah lewat; `start_time: now`.
- FFmpeg: memakai `imageio-ffmpeg` bila FFmpeg tidak ada di PATH (sebelumnya selalu error di Windows tanpa FFmpeg), pembersihan file sementara benar, output atomik, padding durasi minimal, mode `blur`/`pad`, audio 48 kHz, verifikasi resolusi hasil.
- Dashboard: unggahan segmen ditulis atomik & divalidasi (sebelumnya worker bisa mengambil file setengah jadi), *Tes demo* memeriksa hasil FFmpeg (sebelumnya "sukses" palsu), semua teks di-escape (XSS), status worker/heartbeat, jeda/lanjut, preview jadwal, mode jam tertentu, editor config, hapus job + file.
- AI: model Gemini default `gemini-1.5-flash` (sudah dipensiunkan) → `gemini-2.5-flash` + fallback otomatis, API key lewat header (bukan URL), `systemInstruction`, JSON toleran terhadap ```json, retry 429/5xx, normalisasi hashtag/tag berbentuk string, jumlah segmen dipaksa sesuai config.
- YouTube: sanitasi judul/deskripsi/tags sesuai batas API, upload chunked + retry, perintah `youtube-auth`, deteksi kuota/izin.
- Flow: flag anti-deteksi browser dihapus, login hanya manual, satu percobaan otomatis per segmen, selector dapat dikonfigurasi; provider **resmi** `veo_api` (Gemini API) ditambahkan.
- Log terpisah per proses (`worker.log`, `dashboard.log`) — mencegah konflik rotasi file di Windows; konsol aman Unicode.
- `start.bat` membuat venv + install otomatis, menyalin `.env`/`config.json`, menjalankan `doctor`; CRLF.
- Perintah baru `doctor`, `retry`, `cancel`, `pause`, `resume`; `enqueue` memakai nilai config bila tanpa argumen.
- Suite pengujian pytest (>50 tes) ditambahkan.
