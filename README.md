# YShorts Bot Studio 🎬🤖

Otomasi pipeline **YouTube Shorts** berbasis AI: dari *niche* → ide & prompt video → video Google Flow (2×8 detik) → penggabungan FFmpeg 9:16 (+musik latar) → judul/deskripsi/hashtag → (persetujuan opsional) → upload terjadwal lewat **YouTube Data API v3** resmi. Antrean persisten (SQLite), retry otomatis, resume setelah restart, notifikasi Telegram/webhook, dan web dashboard.

```
NICHE ──► AI: ide + N prompt bersambung ──► Google Flow: video segmen 1..N (±8s)
      ──► FFmpeg: gabung → 1080×1920 MP4 (≥16s, audio dijamin, musik latar opsional)
      ──► AI: judul + deskripsi + hashtag + tags ──► [persetujuan Anda di dashboard]
      ──► YouTube Data API (OAuth 2.0) ──► notifikasi ──► tunggu jadwal berikutnya ──► ulangi
```

---

## Fitur

| Area | Detail |
| --- | --- |
| **AI Director** | Gemini / OpenAI (atau server kompatibel) / `mock` offline. Ide, hook, gaya visual, N prompt Google Flow yang saling bersambung, metadata YouTube. Output divalidasi & dinormalisasi (jumlah segmen tepat, arahan 9:16 & larangan teks/logo, judul ≤100 karakter, `#Shorts`). Bahasa & gaya bisa diarahkan (`ai.language`, `ai.style_notes`, `ai.temperature`). HTTP 429 → job ditunda, bukan gagal. |
| **Sumber video Flow** | `manual` (default, aman ToS) dengan **folder inbox otomatis** & unggah *drag & drop*; `browser` (Playwright eksperimental); `veo_api` (jalur **resmi** Veo lewat Gemini API, berbayar). Lihat *Google Flow & ToS*. |
| **FFmpeg** | Normalisasi ke 9:16 (`crop`/`pad`/`blur`), H.264 + AAC 48 kHz, audio hening otomatis, perpanjang frame akhir bila < 16 s (termasuk sumber dengan durasi tak terbaca), **musik latar** acak dari `data/music` (mode `auto`/`always`/`off`, volume & fade), output atomik. Binary: `video.ffmpeg_binary` → `FFMPEG_BINARY` → PATH → `imageio-ffmpeg`. |
| **Kontrol sebelum upload** | `youtube.require_approval`: job berhenti di **Perlu persetujuan**; Anda meninjau video, **mengedit judul/deskripsi/hashtag/tags**, mengubah jadwal, lalu *Setujui* (atau *Setujui & upload sekarang*). *Ide & prompt baru* / *Metadata baru* meminta AI menyusun ulang. |
| **Upload YouTube** | OAuth 2.0 Desktop app, upload resumable 8 MB/chunk + retry, sanitasi sesuai batas API, `privacy_status`, mode simulasi `YOUTUBE_MOCK_UPLOAD=true`, **playlist** opsional, kuota habis → ditunda 1 jam tanpa menghabiskan retry, pembersihan file segmen setelah upload (opsional). |
| **Penjadwalan** | Interval (1/3/6/12/24 jam, desimal boleh) + jam mulai / `now`, atau jam-jam tertentu; timezone bebas; jadwal per job bisa diubah dari dashboard (*Upload sekarang*). |
| **Antrean** | SQLite (WAL), state machine `queued → planning → waiting_flow_segment_N → merging → metadata → [awaiting_approval] → scheduled → uploading → done`. Non-blocking saat menunggu video. Retry eksponensial; `failed`/`cancelled` menyimpan tahap asalnya sehingga *Retry* melanjutkan dari tahap itu. Transisi memakai compare-and-swap: aksi Batalkan/Retry dari dashboard tidak pernah ditimpa worker. Tahan restart, single-instance (heartbeat + cek PID), reload config otomatis. |
| **Notifikasi** | Telegram bot dan/atau webhook (Discord, Slack, generik) saat video terupload, job gagal, atau menunggu persetujuan. |
| **Dashboard** | `http://127.0.0.1:8000`: buat antrean, preview jadwal, status worker (heartbeat), jeda/lanjut, filter & pencarian, aksi massal, ekspor CSV, salin prompt, unggah/drag-drop segmen, tes demo, pratinjau & unduh video, editor metadata & jadwal, persetujuan, editor `config.json`, log langsung. Opsional Basic Auth + proteksi CSRF. |
| **CLI** | `doctor`, `youtube-auth`, `notify-test`, `enqueue`, `run`, `run-once`, `status`, `approve`, `retry`, `cancel`, `pause/resume`, `export`, `dashboard`. |

---

## Persyaratan

- Python **3.10+** (diuji di 3.11–3.14)
- FFmpeg — otomatis tersedia lewat paket `imageio-ffmpeg`; FFmpeg sistem dipakai bila ada di `PATH`
- API key Gemini (Google AI Studio, gratis) **atau** OpenAI — opsional, tanpa ini pakai `AI_PROVIDER=mock`
- Upload sungguhan: proyek Google Cloud dengan **YouTube Data API v3** + OAuth client *Desktop app*
- Mode `browser` (opsional): `pip install -r requirements-browser.txt && playwright install chromium`

---

## Instalasi & Menjalankan

### Windows: satu klik
Klik dua kali **`start.bat`**. Skrip akan:
1. membuat `.venv` dan menginstall dependensi (sekali saja),
2. membuat `.env` dan `config.json` dari contoh bila belum ada,
3. menjalankan `doctor`, membuka **Dashboard** dan **Worker** di dua jendela terpisah, lalu membuka browser saat dashboard siap.

`run_dashboard.bat` / `run_worker.bat` menjalankan masing-masing komponen saja.

### Manual (Windows / macOS / Linux)
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                  # isi API key
cp config.example.json config.json    # atur niche, jadwal, dll.

python -m yshorts_bot doctor          # cek ffmpeg, AI, Flow, YouTube, notifikasi, jadwal
python -m yshorts_bot dashboard       # terminal 1 -> http://127.0.0.1:8000
python -m yshorts_bot run             # terminal 2 -> worker
```

Uji coba cepat tanpa API key (`.env` bawaan: `AI_PROVIDER=mock`, `YOUTUBE_MOCK_UPLOAD=true`): buat job di dashboard → **Detail** → **Tes demo** → worker menggabungkan klip uji → video 9:16 bisa diputar/diunduh dari dashboard.

---

## Alur kerja harian (mode `manual`)

1. Buat job (dashboard atau `enqueue`). Worker langsung meminta AI menyusun ide + prompt semua segmen.
2. Buka **Detail** job → **Salin prompt** segmen 1 → tempel di Google Flow (akun Ultra Anda) → generate → unduh MP4.
3. Serahkan hasilnya dengan salah satu cara:
   - **Inbox** (paling praktis): atur folder download browser ke `data/flow_downloads/inbox`. File apa pun yang masuk otomatis dipasangkan ke job/segmen yang sedang menunggu, urut job lalu segmen. File yang masih diunduh (`.crdownload`, `.part`) dan file yang ukurannya masih berubah tidak akan diambil.
   - **Drag & drop** MP4 ke kartu segmen di dashboard (atau tombol *Kirim video*).
   - Simpan manual sebagai `data/flow_downloads/job_<id>_segment_<n>.mp4`.
4. Worker menggabungkan segmen (FFmpeg), menambah musik latar bila `data/music` berisi file dan segmen bisu, lalu AI membuat metadata.
5. Bila `youtube.require_approval` aktif: job berhenti di **Perlu persetujuan** (notifikasi dikirim). Tinjau video, edit judul/deskripsi, ubah jadwal, lalu **Setujui**.
6. Upload berjalan sesuai jadwal; notifikasi *Video terupload* dikirim; segmen dibersihkan bila `maintenance.delete_segments_after_upload` aktif.

---

## Konfigurasi

### `.env` (kredensial)
```env
AI_PROVIDER=gemini                 # gemini | openai | mock
GEMINI_API_KEY=...                 # https://aistudio.google.com/apikey
GEMINI_MODEL=gemini-2.5-flash      # bila model dipensiunkan, otomatis fallback
OPENAI_API_KEY=...                 # jika AI_PROVIDER=openai (OPENAI_BASE_URL untuk server kompatibel)

YOUTUBE_CLIENT_SECRET_FILE=client_secret.json
YOUTUBE_TOKEN_FILE=secrets/token.json
YOUTUBE_MOCK_UPLOAD=false          # true = simulasi upload
YOUTUBE_INTERACTIVE_AUTH=true      # false = jangan buka browser dari worker (login via youtube-auth)

TELEGRAM_BOT_TOKEN=...             # notifikasi Telegram (opsional)
TELEGRAM_CHAT_ID=...
NOTIFY_WEBHOOK_URL=...             # Discord/Slack/webhook generik (opsional)

DASHBOARD_USERNAME=...             # Basic Auth dashboard (wajib bila --host 0.0.0.0)
DASHBOARD_PASSWORD=...
# FFMPEG_BINARY=C:\ffmpeg\bin\ffmpeg.exe
```

### `config.json` (perilaku) — juga bisa diedit dari tab **Pengaturan** di dashboard
| Kunci | Arti |
| --- | --- |
| `niche`, `total_videos`, `niche_presets` | nilai default `enqueue` / tombol *Antrekan dari config*; tombol topik cepat di dashboard |
| `segments_per_video`, `segment_duration_seconds` | 2 × 8 detik = Shorts ±16 detik |
| `ai.language`, `ai.style_notes`, `ai.temperature` | bahasa ide/judul/deskripsi (prompt video tetap Inggris), preferensi gaya, kreativitas |
| `schedule.mode` | `interval` (`interval_hours` + `start_time`, `"now"` = mulai segera) atau `specific_times` (daftar jam); `timezone` mis. `Asia/Jakarta` |
| `flow.provider` | `manual` / `browser` / `veo_api` |
| `flow.inbox_dir`, `flow.inbox_auto_assign` | folder inbox otomatis (lihat alur kerja) |
| `flow.wait_timeout_minutes`, `flow.poll_seconds`, `flow.stable_seconds` | batas menunggu video segmen (final; *Retry* memulai lagi), frekuensi cek, jeda deteksi file selesai ditulis |
| `flow.selectors`, `flow.veo.*` | kalibrasi UI mode `browser`; model/resolusi/durasi (4/6/8) mode `veo_api` |
| `youtube.privacy_status` | `private` (disarankan sampai proyek API diaudit), `unlisted`, `public` |
| `youtube.require_approval`, `youtube.playlist_id` | persetujuan manual sebelum upload; tambah ke playlist (butuh izin OAuth `youtube` saat login) |
| `video.fit_mode`, `video.min_duration_seconds`, `video.pad_to_min_duration` | `crop`/`pad`/`blur`; durasi minimal & perpanjangan frame akhir |
| `video.background_music_*` | folder musik, mode `off`/`auto`/`always`, volume 0–1, fade |
| `notifications.*` | aktif/tidak, kejadian yang dikirim, URL dashboard yang disertakan |
| `maintenance.*` | hapus segmen / output setelah upload sukses |
| `retry.*`, `worker.*` | jatah retry & backoff; jeda idle, detak heartbeat, reload config |

Worker memuat ulang `config.json` otomatis saat file berubah (kecuali `paths.*`, butuh restart). Path relatif dihitung dari folder kerja (`start.bat` selalu memakai folder proyek). `.env` dibaca dari folder `config.json`, folder kerja, lalu root proyek.

Contoh spesifikasi — *Niche: Fakta menarik dunia, 10 video, interval 3 jam, mulai 08:00*:
```bash
python -m yshorts_bot enqueue --niche "Fakta menarik dunia" --count 10 --interval-hours 3 --start-time 08:00
python -m yshorts_bot enqueue --niche "Cerita horor" --count 4 --times "08:00,12:00,18:00,21:00"
python -m yshorts_bot enqueue        # pakai niche/total_videos/schedule dari config.json
```

---

## Google Flow & Terms of Service — baca ini

**Google Flow tidak menyediakan API publik.** Program ini **tidak mengarang API** dan tidak berusaha mengakali login, CAPTCHA, limit, atau sistem antrean Flow.

| Mode | Cara kerja | Catatan |
| --- | --- | --- |
| **`manual`** (default) | Worker menyiapkan prompt; Anda generate di Google Flow dengan akun Ultra, hasilnya masuk lewat inbox / drag & drop / nama file. Sisanya otomatis. | 100% sesuai ToS, memakai kredit Ultra Anda. Worker tidak memblokir job lain saat menunggu. |
| **`veo_api`** | Jalur **resmi** Veo programatik: Gemini API `models/veo-3.1-generate-preview:predictLongRunning` (9:16, 4/6/8 detik, 720p/1080p), otomatis penuh. | Berbayar per video (billing Gemini API), **terpisah** dari langganan Ultra. Butuh `GEMINI_API_KEY`. |
| **`browser`** | Playwright membuka Chrome dengan profil persisten, **Anda login sendiri** (sekali), program menempel prompt, klik *Generate*, menunggu *Download*. Satu segmen = **satu** percobaan otomatis (marker `job_<id>_segment_<n>.browser.json`); gagal → jatuh ke manual. | Eksperimental & rapuh terhadap perubahan UI (kalibrasi `flow.selectors`, uji `python scripts/test_flow_browser.py`). Tanpa flag anti-deteksi. Otomasi UI Google berpotensi melanggar ToS — **risiko akun ditanggung pengguna**. |

---

## YouTube Data API — setup & batasan
1. [Google Cloud Console](https://console.cloud.google.com/) → proyek baru → aktifkan **YouTube Data API v3**.
2. **Credentials → Create Credentials → OAuth client ID → Desktop app** → unduh JSON → simpan sebagai `client_secret.json`. Tambahkan akun Anda sebagai *test user* bila aplikasi masih *Testing*.
3. `python -m yshorts_bot youtube-auth` → izinkan di browser → token tersimpan di `secrets/token.json`. Set `YOUTUBE_MOCK_UPLOAD=false`. (Bila `youtube.playlist_id` diisi, login akan meminta izin `youtube` yang lebih luas.)

Batasan resmi:
- **Kuota default 10.000 unit/hari; satu upload = 1.600 unit → ±6 upload/hari.** Jadwal "1 video/jam" membutuhkan [kenaikan kuota](https://support.google.com/youtube/contact/yt_api_form). Kuota habis → worker menunda 1 jam.
- Video dari proyek API yang **belum diaudit** Google bisa dipaksa *private*. Mulai dengan `privacy_status: private`, publikasikan dari YouTube Studio atau ajukan audit.
- Shorts dikenali otomatis dari video vertikal ≤ 3 menit; `#Shorts` disertakan di judul/deskripsi.

---

## Notifikasi
1. Telegram: buat bot di @BotFather → `TELEGRAM_BOT_TOKEN`; kirim pesan ke bot lalu ambil `TELEGRAM_CHAT_ID` (mis. via @userinfobot). Dan/atau isi `NOTIFY_WEBHOOK_URL` (Discord *Webhook URL*, Slack *Incoming Webhook*, atau URL Anda sendiri — menerima JSON `{event, job_id, niche, title, youtube_video_id, error, ...}`).
2. Set `notifications.enabled: true` (tab Pengaturan).
3. Uji: `python -m yshorts_bot notify-test` atau tombol *Tes notifikasi*.

---

## Perintah CLI
```bash
python -m yshorts_bot doctor                 # pemeriksaan kesiapan
python -m yshorts_bot init-db
python -m yshorts_bot enqueue [--niche X --count N --mode interval|specific_times --interval-hours H --start-time HH:MM|now --times "08:00,18:00"]
python -m yshorts_bot run                    # worker (Ctrl+C / SIGTERM aman, progres tersimpan)
python -m yshorts_bot run-once [--job-id ID] # proses satu tahap (debug)
python -m yshorts_bot status [--json]
python -m yshorts_bot approve ID [--now] | approve --all
python -m yshorts_bot retry ID | cancel ID | pause | resume
python -m yshorts_bot export --out data/jobs_export.csv
python -m yshorts_bot dashboard --host 127.0.0.1 --port 8000
python -m yshorts_bot youtube-auth | notify-test
python -m yshorts_bot --config lain.json ...  # config alternatif; -v untuk log DEBUG
```

## API dashboard (ringkas)
`GET /api/health` (tanpa auth) · `GET /api/status` · `GET /api/jobs` · `POST /api/enqueue` · `POST /api/schedule/preview` · `POST /api/jobs/{id}/approve|retry|cancel|schedule|regenerate|simulate|upload-segment` · `PUT /api/jobs/{id}/metadata` · `DELETE /api/jobs/{id}?purge=` · `POST /api/jobs/bulk` · `GET /api/export.csv` · `GET /api/jobs/{id}/video|segments/{n}|prompt/{n}` · `GET|PUT /api/config` · `POST /api/notify/test` · `POST /api/worker/pause|resume` · `GET /api/logs`. Dokumentasi interaktif di `/docs`.

---

## Struktur proyek
```
yshorts_bot/
├── ai/            provider.py (Gemini/OpenAI/mock, retry, 429 -> tunda) · planner.py (ide, prompt, metadata, sanitasi)
├── db/queue.py    antrean SQLite: state machine, CAS update, retry-resume, approve/reschedule, aksi massal, meta
├── flow/          base.py (non-blocking) · manual.py · playwright_provider.py · veo_api.py · prompt_files.py (inbox, deteksi file stabil)
├── scheduler/     schedule.py (interval / jam tertentu, timezone, aman DST)
├── video/ffmpeg.py  probe, normalisasi 9:16, gabung, padding durasi, musik latar, klip uji
├── youtube/uploader.py  OAuth 2.0, upload resumable, playlist, terjemahan error kuota/izin
├── notify.py      Telegram / webhook
├── worker.py      orkestrator per tahap, heartbeat thread, inbox, single-instance, reload config
├── dashboard.py + templates/dashboard.html  FastAPI + UI (Basic Auth & CSRF guard opsional)
├── cli.py · config.py · errors.py · models.py · logging_setup.py
tests/             pytest (~100 tes): jadwal, antrean, planner, ffmpeg, notifikasi, inbox, pipeline end-to-end, race, dashboard
data/              prompts/ · flow_downloads/ (+inbox/) · music/ · output/ · failed/ · logs/ · yshorts.sqlite3
```

## Pengujian
```bash
pip install -r requirements-dev.txt
python -m pytest
```

---

## Troubleshooting
| Gejala | Solusi |
| --- | --- |
| `ffmpeg tidak ditemukan` | `pip install imageio-ffmpeg` (sudah di requirements) atau install FFmpeg & set `FFMPEG_BINARY` |
| Nilai `.env` tidak terbaca | `.env` dibaca dari folder `config.json`, folder kerja, lalu root proyek. Jalankan `doctor` |
| Worker: `Worker lain (PID …) masih aktif` | hanya satu worker per database; tutup jendela worker lama |
| Job `failed` | buka detail → baca error → perbaiki → **Retry** melanjutkan dari tahap yang gagal (`data/failed/job_<id>.json`) |
| Job berhenti di *Perlu persetujuan* | memang menunggu Anda: tinjau lalu **Setujui**, atau matikan `youtube.require_approval` |
| File di inbox tidak dipasangkan | pastikan ekstensi video (mp4/mov/webm/mkv), unduhan selesai, dan ada job yang statusnya menunggu segmen |
| `Gemini … HTTP 429` | rate limit gratisan; job ditunda otomatis lalu dicoba lagi. Kurangi jumlah job serentak atau upgrade kuota |
| `quotaExceeded` saat upload | kuota harian YouTube habis; job ditunda 1 jam. Kurangi frekuensi atau ajukan kenaikan kuota |
| Video di YouTube jadi private | normal untuk proyek API belum diaudit; publikasikan dari YouTube Studio |
| Mode `browser` tidak menemukan kolom prompt | `python scripts/test_flow_browser.py`, lihat screenshot di `data/logs/`, sesuaikan `flow.selectors` |
| `Permintaan lintas situs ditolak` (403) | dashboard menolak permintaan dari situs lain (CSRF). Akses lewat alamat dashboard sendiri |

---

## Changelog

### 1.2.0 — audit menyeluruh + fitur pelengkap
Perbaikan (hasil audit independen):
- **Race condition**: worker bisa menimpa aksi *Batalkan/Retry* dari dashboard → semua transisi status memakai compare-and-swap; kegagalan pada job yang sudah dibatalkan tidak menghidupkannya kembali.
- Modal detail dirender ulang tiap 4 detik → pilihan file & isian form hilang; kini hanya dirender saat data berubah dan pengguna tidak sedang mengedit.
- Heartbeat berhenti berdetak selama tahap panjang (FFmpeg/upload/browser) → dashboard "offline" & worker kedua bisa lolos; heartbeat kini di thread terpisah.
- HTTP 429 Gemini/OpenAI menghabiskan jatah retry dalam 3 menit → kini job ditunda (`RetryLaterError`).
- `cancel` lalu `retry` mengembalikan job ke awal (tahap gagal hilang); `retry` pada job terjadwal mengabaikan jadwal.
- Batas tunggu Flow efektif 3× (72 jam) → kini final, *Retry* memulai penghitung dari nol.
- Reload config tidak menerapkan perubahan `flow.*` selain `provider`.
- Proteksi CSRF (same-origin) untuk endpoint yang mengubah data; `paths.*` dan `video.ffmpeg_binary` tidak bisa diubah lewat API.
- Jadwal interval dihitung di UTC (aman peralihan DST); pengecekan scope OAuth memakai scope yang tersimpan; `token.json` chmod 600 (POSIX); `pid_alive` Windows menangani ACCESS_DENIED; API key Veo tidak ikut redirect; sumber `Duration: N/A` tidak lagi ditolak; `ffmpeg_binary` boleh nama di PATH; sanitasi judul membuang tag HTML.
- Deteksi file selesai ditulis: ukuran harus stabil antar pemeriksaan **dan** mtime lama (aman untuk salinan Windows maupun unduhan browser).

Fitur baru:
- **Persetujuan sebelum upload** (`youtube.require_approval`), **editor metadata**, **jadwal ulang / upload sekarang**, **ide & prompt / metadata baru**.
- **Folder inbox otomatis** untuk hasil Google Flow; **drag & drop** segmen di dashboard.
- **Notifikasi** Telegram / Discord / Slack / webhook (`notify-test`).
- **Musik latar** dari `data/music` (auto/always/off, volume, fade).
- **Pengarah AI**: bahasa, catatan gaya, temperature; **topik cepat** dari config.
- **Playlist YouTube** opsional; **pembersihan** file setelah upload.
- Dashboard: filter & pencarian, **aksi massal** (setujui/ulangi/batalkan/hapus semua), **ekspor CSV**, kartu *Perlu persetujuan*, Basic Auth (`DASHBOARD_USERNAME/PASSWORD`), `GET /api/health`.
- CLI: `approve`, `export`, `notify-test`; `doctor` memeriksa notifikasi/musik/inbox/auth.
- `start.bat` menunggu dashboard siap sebelum membuka browser; Playwright dipisah ke `requirements-browser.txt`.

### 1.1.0 — perbaikan menyeluruh
- Worker non-blocking saat menunggu video Flow; retry cerdas (melanjutkan dari tahap gagal); status `cancelled`; `RetryLater` untuk kuota; error permanen langsung `failed`.
- Perbaikan infinite loop jadwal, validasi jam, interval menyusul slot berikutnya, `start_time: now`.
- FFmpeg via `imageio-ffmpeg` bila tidak ada di PATH, output atomik, padding durasi, mode `blur`/`pad`, audio 48 kHz.
- Dashboard: unggahan atomik & tervalidasi, Tes demo memeriksa hasil, escape XSS, status worker, jeda/lanjut, preview jadwal, editor config.
- AI: model Gemini `gemini-2.5-flash` + fallback, key via header, JSON toleran, normalisasi output. YouTube: sanitasi metadata, upload chunked, `youtube-auth`.
- Flow: flag anti-deteksi dihapus, login manual, satu percobaan per segmen, provider resmi `veo_api`.
- Log per proses, `.env` dari folder config/CWD/root, `start.bat` membuat venv, `doctor`, suite pytest.
