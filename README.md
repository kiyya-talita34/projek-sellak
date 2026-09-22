# YShorts Bot Studio 🎬🤖

Program otomasi pipeline pembuatan dan upload video **YouTube Shorts** berbasis AI dengan integrasi resmi, FFmpeg, Google Flow Ultra, dan Web Dashboard interaktif.

---

## 🌟 Fitur Utama

1. **Input Niche Fleksibel**:
   - Masukkan topik lewat Web Dashboard atau CLI.
   - Preset topik siap pakai: Fakta Unik Dunia, Kisah Horor, Fakta Hewan, Motivasi, Sejarah Kuno, Teknologi AI.
2. **AI Director Otomatis (Gemini / OpenAI / Mock)**:
   - Otomatis menciptakan ide video, hook, dan gaya visual.
   - Menghasilkan 2 prompt video Google Flow yang saling bersambung (*scene & subject continuity*) untuk durasi total minimal 16 detik.
   - Menghasilkan judul viral, deskripsi lengkap, hashtag (#Shorts), dan tags YouTube.
3. **Alur Kerja Google Flow Ultra yang Legal & Aman**:
   - Tanpa bypass CAPTCHA, tanpa pemalsuan API, dan tanpa trik terlarang yang melanggar ToS Google.
   - **Fitur 1-Click Copy Prompt**: Salin prompt langsung dari Dashboard dan tempel di Google Flow Ultra.
   - **Fitur Drag & Drop Video Segmen**: Unggah langsung file video hasil download dari browser ke Dashboard tanpa perlu membuka folder atau me-rename file secara manual!
4. **Engine Penggabungan FFmpeg Tangguh**:
   - Otomatis menggabungkan 2 segmen (8s + 8s) menjadi Shorts 1080×1920 (9:16) berdurasi &ge; 16 detik.
   - **Silent Audio Stream Fix**: AI video generator sering kali tidak memiliki suara. FFmpeg otomatis menyisipkan audio hening (*silent audio track*) agar YouTube tidak menolak file saat diproses.
5. **Upload Resmi YouTube Data API v3**:
   - Menggunakan OAuth 2.0 resmi (Desktop App Client).
   - Mendukung mode privasi (`private`, `unlisted`, `public`) dan opsi simulasi testing (`YOUTUBE_MOCK_UPLOAD=true`).
6. **Penjadwalan Otomatis & Antrean Persisten**:
   - Interval upload (1 jam, 3 jam, 6 jam, 12 jam, 24 jam) atau jam tertentu (`08:00`, `12:00`, `18:00`, `21:00`).
   - Database SQLite persisten: tahan restart, status tidak hilang, auto-retry eksponensial saat terjadi kendala jaringan.
7. **Web Dashboard Modern**:
   - Buka `http://127.0.0.1:8000` di browser.
   - Live stat bar, antrean interaktif, tombol detail prompt, upload segmen, live terminal logs.

---

## 🚀 Cara Menjalankan

### Cara 1: Satu Klik (Rekomendasi di Windows)
Cukup klik dua kali file:
```text
start.bat
```
Skrip ini akan otomatis membuka:
1. Web Dashboard di browser: `http://127.0.0.1:8000`
2. Background Worker di jendela command prompt terpisah.

---

### Cara 2: Menjalankan Manual Lewat Terminal

1. **Jalankan Web Dashboard**:
   ```bash
   python -m yshorts_bot dashboard --host 127.0.0.1 --port 8000
   ```
   Buka di browser: `http://127.0.0.1:8000`

2. **Jalankan Background Worker**:
   ```bash
   python -m yshorts_bot run
   ```

3. **Perintah CLI Tambahan**:
   - Inisialisasi database:
     ```bash
     python -m yshorts_bot init-db
     ```
   - Tambah antrean via CLI:
     ```bash
     python -m yshorts_bot enqueue --niche "Fakta Menarik Hewan" --count 5
     ```
   - Cek status antrean:
     ```bash
     python -m yshorts_bot status
     ```

---

## ⚙️ Konfigurasi (`.env` dan `config.json`)

Edit file `.env`:

```env
# Pilihan AI: 'gemini', 'openai', atau 'mock'
AI_PROVIDER=gemini
GEMINI_API_KEY=isi_api_key_google_ai_studio_anda
GEMINI_MODEL=gemini-1.5-flash

# Opsi OpenAI jika digunakan:
# OPENAI_API_KEY=isi_api_key_anda
# OPENAI_MODEL=gpt-4o-mini

# YouTube OAuth
YOUTUBE_CLIENT_SECRET_FILE=client_secret.json
YOUTUBE_TOKEN_FILE=secrets/token.json
YOUTUBE_DEFAULT_PRIVACY_STATUS=private

# Set true jika ingin simulasi upload sebelum memasukkan client_secret.json
YOUTUBE_MOCK_UPLOAD=false
```

### Setup YouTube Data API (Untuk Upload Asli)
1. Buka [Google Cloud Console](https://console.cloud.google.com/).
2. Buat proyek baru dan aktifkan **YouTube Data API v3**.
3. Buka **Credentials** &rarr; **Create Credentials** &rarr; **OAuth client ID**.
4. Pilih Application Type: **Desktop app**.
5. Unduh file JSON dan simpan dengan nama `client_secret.json` di folder root project.
6. Saat pertama kali upload, browser akan terbuka meminta izin login ke channel YouTube Anda. Token tersimpan aman di `secrets/token.json`.

---

## 📁 Struktur Direktori

```text
yshorts_bot/
├── yshorts_bot/
│   ├── ai/            # AI Brain: Gemini, OpenAI, Mock provider & prompt planner
│   ├── db/            # SQLite queue persisten & manajemen status
│   ├── flow/          # Adapter Google Flow Ultra (Manual Assisted & Browser)
│   ├── scheduler/     # Kalkulasi jadwal interval & specific times
│   ├── video/         # Engine FFmpeg Shorts 9:16 & silent audio fix
│   ├── youtube/       # YouTube Data API v3 OAuth uploader
│   ├── dashboard.py   # FastAPI Modern Web Dashboard & API
│   ├── worker.py      # Background worker orchestrator
│   └── cli.py         # Command Line Interface
├── data/
│   ├── prompts/       # File teks prompt Google Flow per job
│   ├── flow_downloads/# Folder unduhan segmen video dari Google Flow
│   ├── output/        # Video final Shorts (1080x1920) siap tayang
│   └── logs/          # Rotating log file yshorts.log
├── config.json        # Konfigurasi sistem
├── .env               # Kredensial API
├── start.bat          # 1-klik launcher (Dashboard + Worker)
└── requirements.txt
```
