from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from .config import load_config
from .db.queue import QueueDB
from .scheduler.schedule import build_upload_schedule


class EnqueueRequest(BaseModel):
    niche: str
    count: int = 1
    mode: str = "interval"
    interval_hours: int = 3
    start_time: str = "08:00"


def create_app(config_path: str = "config.json") -> FastAPI:
    cfg = load_config(config_path)
    db = QueueDB(cfg.paths.db_path)
    db.init()
    app = FastAPI(title="YShorts Bot Studio")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return DASHBOARD_HTML

    @app.get("/api/jobs")
    def list_jobs(limit: int = 100):
        jobs = db.list_jobs(limit)
        parsed = []
        for j in jobs:
            item = dict(j)
            item["plan"] = json.loads(item["plan_json"]) if item.get("plan_json") else None
            item["metadata"] = json.loads(item["metadata_json"]) if item.get("metadata_json") else None
            item["segment_paths"] = json.loads(item["segment_paths_json"]) if item.get("segment_paths_json") else []
            parsed.append(item)
        return parsed

    @app.get("/api/stats")
    def get_stats():
        return db.get_stats()

    @app.post("/api/enqueue")
    def enqueue(req: EnqueueRequest):
        if not req.niche.strip():
            raise HTTPException(status_code=400, detail="Niche tidak boleh kosong.")
        
        sched_cfg = cfg.schedule.model_copy()
        sched_cfg.mode = req.mode  # type: ignore
        sched_cfg.interval_hours = req.interval_hours
        sched_cfg.start_time = req.start_time

        times = build_upload_schedule(sched_cfg, req.count)
        job_ids = db.enqueue(req.niche.strip(), req.count, cfg.retry.max_attempts, times)
        return {"success": True, "job_ids": job_ids, "count": len(job_ids)}

    @app.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: int):
        ok = db.retry_job(job_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
        return {"success": True}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: int):
        ok = db.cancel_job(job_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Job tidak ditemukan atau sudah selesai.")
        return {"success": True}

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: int):
        ok = db.delete_job(job_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
        return {"success": True}

    @app.post("/api/jobs/{job_id}/upload-segment")
    async def upload_segment(
        job_id: int,
        segment_index: int = Form(...),
        file: UploadFile = File(...)
    ):
        job = db.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
        
        dest_dir = Path(cfg.paths.flow_download_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / f"job_{job_id}_segment_{segment_index}.mp4"

        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        return {"success": True, "saved_path": str(dest_path), "size_bytes": dest_path.stat().st_size}

    @app.post("/api/jobs/{job_id}/simulate")
    def simulate_job(job_id: int):
        import subprocess
        job = db.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
        dest_dir = Path(cfg.paths.flow_download_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        for seg_idx in (1, 2):
            dest_path = dest_dir / f"job_{job_id}_segment_{seg_idx}.mp4"
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "testsrc=size=720x1280:rate=30",
                "-t", "8",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                str(dest_path)
            ]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return {"success": True, "message": "Video sampel untuk Segmen 1 & 2 berhasil dibuat! Worker akan langsung menggabungkan dengan FFmpeg."}


    @app.get("/api/logs", response_class=PlainTextResponse)
    def logs():
        path = Path(cfg.paths.log_dir) / "yshorts.log"
        if not path.exists():
            return "Log file belum terbentuk."
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()[-150:]
            return "\n".join(lines)
        except Exception as e:
            return f"Error membaca log: {e}"

    @app.get("/api/jobs/{job_id}/video")
    def get_job_video(job_id: int):
        job = db.get(job_id)
        if not job or not job.get("output_path"):
            raise HTTPException(status_code=404, detail="Video belum tersedia.")
        path = Path(job["output_path"])
        if not path.exists():
            raise HTTPException(status_code=404, detail="File video tidak ditemukan di disk.")
        return FileResponse(str(path), media_type="video/mp4", filename=path.name)

    return app


DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="id">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>YShorts Bot - Studio Otomasi YouTube Shorts</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
  <style>
    :root {
      --bg-dark: #0f172a;
      --card-dark: #1e293b;
      --border-dark: #334155;
      --accent: #6366f1;
      --text-muted: #94a3b8;
    }
    body {
      background-color: var(--bg-dark);
      color: #f8fafc;
      font-family: system-ui, -apple-system, sans-serif;
    }
    .card {
      background-color: var(--card-dark);
      border: 1px solid var(--border-dark);
      border-radius: 12px;
    }
    .stat-card {
      padding: 1rem;
      border-radius: 10px;
      text-align: center;
      background: rgba(255,255,255,0.03);
      border: 1px solid var(--border-dark);
    }
    .stat-val { font-size: 1.8rem; font-weight: 700; }
    .badge-status { font-size: 0.8rem; padding: 0.35rem 0.65rem; border-radius: 20px; }
    .status-queued { background-color: #3b82f6; }
    .status-planning { background-color: #8b5cf6; }
    .status-waiting_flow_segment_1 { background-color: #f59e0b; }
    .status-waiting_flow_segment_2 { background-color: #f97316; }
    .status-merging { background-color: #eab308; color: #000; }
    .status-metadata { background-color: #a855f7; }
    .status-scheduled { background-color: #06b6d4; }
    .status-uploading { background-color: #0ea5e9; }
    .status-done { background-color: #10b981; }
    .status-failed { background-color: #ef4444; }
    .preset-btn {
      font-size: 0.8rem;
      margin-right: 0.4rem;
      margin-bottom: 0.4rem;
      border-radius: 20px;
    }
    .terminal-box {
      background: #000;
      color: #38bdf8;
      font-family: 'Courier New', Courier, monospace;
      font-size: 0.8rem;
      height: 250px;
      overflow-y: auto;
      padding: 12px;
      border-radius: 8px;
    }
    .btn-action { padding: 0.2rem 0.5rem; font-size: 0.8rem; }
  </style>
</head>
<body class="p-3 p-md-4">
<div class="container-fluid max-w-7xl">

  <!-- Header -->
  <div class="d-flex justify-content-between align-items-center mb-4 pb-2 border-bottom border-secondary">
    <div>
      <h2 class="fw-bold mb-0 text-white"><i class="bi bi-youtube text-danger me-2"></i>YShorts Bot Studio</h2>
      <p class="text-muted small mb-0">Otomasi Konten YouTube Shorts AI &bull; Google Flow Ultra &bull; FFmpeg &bull; YouTube API</p>
    </div>
    <div class="d-flex align-items-center gap-2">
      <span class="badge bg-primary text-light px-2 py-1"><i class="bi bi-browser-chrome me-1"></i>Otomasi Browser Aktif</span>
      <button class="btn btn-outline-info btn-sm" onclick="fetchData()"><i class="bi bi-arrow-clockwise me-1"></i>Refresh</button>
      <a href="/docs" target="_blank" class="btn btn-outline-light btn-sm"><i class="bi bi-code-slash me-1"></i>Swagger API</a>
    </div>
  </div>

  <!-- Stat Bar -->
  <div class="row g-3 mb-4" id="statsBar">
    <div class="col-6 col-md-2">
      <div class="stat-card">
        <div class="text-muted small">Total Video</div>
        <div class="stat-val text-white" id="stat-total">0</div>
      </div>
    </div>
    <div class="col-6 col-md-2">
      <div class="stat-card">
        <div class="text-muted small">Antrean</div>
        <div class="stat-val text-primary" id="stat-queued">0</div>
      </div>
    </div>
    <div class="col-6 col-md-3">
      <div class="stat-card">
        <div class="text-muted small">Sedang Diproses / Flow</div>
        <div class="stat-val text-warning" id="stat-processing">0</div>
      </div>
    </div>
    <div class="col-6 col-md-2">
      <div class="stat-card">
        <div class="text-muted small">Terupload</div>
        <div class="stat-val text-success" id="stat-done">0</div>
      </div>
    </div>
    <div class="col-6 col-md-3">
      <div class="stat-card">
        <div class="text-muted small">Gagal / Dibatalkan</div>
        <div class="stat-val text-danger" id="stat-failed">0</div>
      </div>
    </div>
  </div>

  <div class="row g-4">
    <!-- Form Tambah Antrean -->
    <div class="col-lg-4">
      <div class="card p-3 shadow-sm h-100">
        <h5 class="fw-bold text-white mb-3"><i class="bi bi-plus-circle me-2 text-primary"></i>Buat Video Baru</h5>
        
        <div class="mb-3">
          <label class="form-label small text-light fw-semibold"><i class="bi bi-magic me-1 text-warning"></i>Pilih Topik Cepat:</label>
          <div>
            <button class="btn btn-sm btn-outline-info preset-btn" onclick="setNiche('Fakta unik dunia')">🌍 Fakta Unik</button>
            <button class="btn btn-sm btn-outline-danger preset-btn" onclick="setNiche('Cerita horor misteri')">👻 Kisah Horor</button>
            <button class="btn btn-sm btn-outline-success preset-btn" onclick="setNiche('Fakta menarik tentang hewan')">🐾 Fakta Hewan</button>
            <button class="btn btn-sm btn-outline-warning preset-btn" onclick="setNiche('Motivasi & pola pikir sukses')">💡 Motivasi</button>
            <button class="btn btn-sm btn-outline-primary preset-btn" onclick="setNiche('Misteri sejarah kuno')">🏛️ Sejarah</button>
            <button class="btn btn-sm btn-outline-light preset-btn" onclick="setNiche('Perkembangan teknologi AI masa depan')">🤖 Teknologi AI</button>
          </div>
        </div>

        <form id="enqueueForm" onsubmit="handleEnqueue(event)">
          <div class="mb-3">
            <label class="form-label small fw-semibold text-light">Niche / Topik Utama:</label>
            <input type="text" id="inputNiche" class="form-control form-control-sm bg-dark text-white border-secondary" placeholder="Contoh: Fakta mengejutkan laut dalam" required>
          </div>

          <div class="row g-2 mb-3">
            <div class="col-6">
              <label class="form-label small fw-semibold text-light">Jumlah Video:</label>
              <input type="number" id="inputCount" class="form-control form-control-sm bg-dark text-white border-secondary" value="1" min="1" max="20" required>
            </div>
            <div class="col-6">
              <label class="form-label small fw-semibold text-light">Interval Upload:</label>
              <select id="inputInterval" class="form-select form-select-sm bg-dark text-white border-secondary">
                <option value="1">Setiap 1 jam</option>
                <option value="3" selected>Setiap 3 jam</option>
                <option value="6">Setiap 6 jam</option>
                <option value="12">Setiap 12 jam</option>
                <option value="24">1 video / hari</option>
              </select>
            </div>
          </div>

          <div class="mb-3">
            <label class="form-label small fw-semibold text-light">Jam Mulai Upload Pertama:</label>
            <input type="time" id="inputStartTime" class="form-control form-control-sm bg-dark text-white border-secondary" value="08:00">
          </div>


          <button type="submit" class="btn btn-primary w-100 btn-sm fw-semibold py-2">
            <i class="bi bi-send-fill me-1"></i> Tambah ke Antrean
          </button>
        </form>

        <div class="mt-4 pt-3 border-top border-secondary">
          <h6 class="small fw-bold text-muted mb-2"><i class="bi bi-info-circle me-1"></i>Alur Kerja Otomatis:</h6>
          <ol class="small text-muted ps-3 mb-0">
            <li>AI menciptakan ide dan 2 prompt Google Flow bersambung.</li>
            <li>Browser Chrome otomatis memasukkan prompt ke Google Flow & mengunduh video.</li>
            <li>FFmpeg otomatis menggabungkan segmen menjadi Shorts 9:16 &ge; 16 detik.</li>
            <li>Otomatis upload & publish ke channel YouTube sesuai jadwal.</li>
          </ol>
        </div>
      </div>
    </div>

    <!-- Antrean & Pekerjaan -->
    <div class="col-lg-8">
      <div class="card p-3 shadow-sm mb-4">
        <div class="d-flex justify-content-between align-items-center mb-3">
          <h5 class="fw-bold text-white mb-0"><i class="bi bi-collection-play me-2 text-info"></i>Daftar Antrean Video</h5>
          <span class="badge bg-secondary" id="jobCountBadge">0 Jobs</span>
        </div>

        <div class="table-responsive" style="max-height: 480px;">
          <table class="table table-dark table-hover table-sm align-middle mb-0">
            <thead class="table-secondary text-white">
              <tr>
                <th style="width: 50px;">ID</th>
                <th>Niche & Ide</th>
                <th>Status</th>
                <th>Jadwal Upload</th>
                <th>Hasil / Link</th>
                <th class="text-end">Aksi</th>
              </tr>
            </thead>
            <tbody id="jobsTableBody">
              <tr><td colspan="6" class="text-center text-muted py-4">Memuat data antrean...</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Live Terminal Log -->
      <div class="card p-3 shadow-sm">
        <div class="d-flex justify-content-between align-items-center mb-2">
          <span class="small fw-bold text-white"><i class="bi bi-terminal me-2 text-warning"></i>Live Worker Activity Log</span>
          <button class="btn btn-sm btn-outline-secondary py-0" onclick="fetchLogs()"><i class="bi bi-arrow-repeat"></i></button>
        </div>
        <div class="terminal-box" id="logBox">Memuat log sistem...</div>
      </div>
    </div>
  </div>
</div>

<!-- Modal Detail Job & Google Flow Prompter -->
<div class="modal fade" id="jobModal" tabindex="-1" aria-hidden="true">
  <div class="modal-dialog modal-lg modal-dialog-centered">
    <div class="modal-content bg-dark text-white border-secondary">
      <div class="modal-header border-secondary">
        <h5 class="modal-title fw-bold" id="modalJobTitle">Detail Job #</h5>
        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body" id="modalJobBody">
        <!-- Dynamic content -->
      </div>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
  let cachedJobs = [];

  function setNiche(n) {
    document.getElementById('inputNiche').value = n;
  }

  function getStatusBadge(status) {
    const labels = {
      'queued': '⏳ Antrean',
      'planning': '🧠 AI Planning',
      'waiting_flow_segment_1': '🎬 Google Flow Segmen 1',
      'waiting_flow_segment_2': '🎬 Google Flow Segmen 2',
      'merging': '⚙️ FFmpeg Merging',
      'metadata': '📝 YouTube Metadata',
      'scheduled': '⏰ Menunggu Jadwal',
      'uploading': '🚀 Mengunggah ke YouTube',
      'done': '✅ Selesai & Live',
      'failed': '❌ Gagal'
    };
    const cls = 'status-' + status;
    return `<span class="badge badge-status ${cls}">${labels[status] || status}</span>`;
  }

  async function fetchData() {
    try {
      const [rJobs, rStats] = await Promise.all([fetch('/api/jobs'), fetch('/api/stats')]);
      const jobs = await rJobs.json();
      const stats = await rStats.json();
      cachedJobs = jobs;

      document.getElementById('stat-total').innerText = stats.total || 0;
      document.getElementById('stat-queued').innerText = stats.queued || 0;
      const proc = (stats.planning||0) + (stats.waiting_flow_segment_1||0) + (stats.waiting_flow_segment_2||0) + (stats.merging||0) + (stats.metadata||0) + (stats.uploading||0);
      document.getElementById('stat-processing').innerText = proc;
      document.getElementById('stat-done').innerText = stats.done || 0;
      document.getElementById('stat-failed').innerText = stats.failed || 0;
      document.getElementById('jobCountBadge').innerText = `${jobs.length} Jobs`;

      const tbody = document.getElementById('jobsTableBody');
      if (jobs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-4">Belum ada video di antrean. Buat video pertama Anda pada panel di sebelah kiri!</td></tr>';
        return;
      }

      let html = '';
      for (const j of jobs) {
        const ideaText = j.idea ? `<br><small class="text-info">${j.idea}</small>` : '';
        let ytLink = '<span class="text-muted small">-</span>';
        if (j.output_path) {
          if (j.youtube_video_id && !j.youtube_video_id.startsWith('demo_')) {
            ytLink = `<a href="https://youtube.com/shorts/${j.youtube_video_id}" target="_blank" class="btn btn-danger btn-action fw-bold"><i class="bi bi-youtube me-1"></i>Shorts</a>`;
          } else {
            ytLink = `
              <div class="d-inline-flex flex-column align-items-start">
                <div class="btn-group btn-group-sm">
                  <button class="btn btn-success btn-action fw-semibold" onclick="openJobDetail(${j.id})"><i class="bi bi-play-circle me-1"></i>Putar</button>
                  <a href="/api/jobs/${j.id}/video" download="short_job_${j.id}.mp4" class="btn btn-outline-light btn-action" title="Unduh MP4"><i class="bi bi-download"></i></a>
                </div>
                <span class="badge bg-secondary text-light mt-1" style="font-size:0.65rem;">Lokal (Simulasi)</span>
              </div>
            `;
          }
        }
        
        const scheduleDisplay = j.scheduled_upload_at 
          ? new Date(j.scheduled_upload_at).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})
          : '-';

        html += `
          <tr>
            <td><strong class="text-white">#${j.id}</strong></td>
            <td>
              <div class="fw-semibold">${j.niche}</div>
              ${ideaText}
            </td>
            <td>${getStatusBadge(j.status)}</td>
            <td><small class="text-muted">${scheduleDisplay}</small></td>
            <td>${ytLink}</td>
            <td class="text-end text-nowrap">
              ${(j.status.startsWith('waiting_flow') || j.status === 'queued') ? `<button class="btn btn-warning btn-sm me-1 fw-bold shadow-sm" onclick="simulateJob(${j.id})"><i class="bi bi-lightning-fill"></i> Tes Demo</button>` : ''}
              <button class="btn btn-outline-primary btn-action" onclick="openJobDetail(${j.id})"><i class="bi bi-eye"></i> Detail</button>
              ${j.status === 'failed' ? `<button class="btn btn-outline-warning btn-action ms-1" onclick="retryJob(${j.id})"><i class="bi bi-arrow-clockwise"></i></button>` : ''}
              ${j.status !== 'done' && j.status !== 'failed' ? `<button class="btn btn-outline-danger btn-action ms-1" onclick="cancelJob(${j.id})"><i class="bi bi-x-circle"></i></button>` : ''}
            </td>
          </tr>
        `;
      }
      tbody.innerHTML = html;
    } catch(e) {
      console.error(e);
    }
  }

  function openJobDetail(jobId) {
    const job = cachedJobs.find(x => x.id === jobId);
    if (!job) return;

    document.getElementById('modalJobTitle').innerText = `Job #${job.id} \u2022 ${job.niche}`;
    const canSimulate = !['done','uploading','merging','metadata','scheduled'].includes(job.status);
    let body = `
      ${canSimulate ? `
      <div class="alert alert-warning py-2 d-flex justify-content-between align-items-center mb-3">
        <span class="small">&#x1F9EA; <strong>Belum punya video Google Flow?</strong> Klik tombol ini untuk tes pipeline lengkap secara otomatis!</span>
        <button id="simBtn_${job.id}" class="btn btn-sm btn-dark ms-2 fw-bold" onclick="simulateJob(${job.id})">
          &#x26A1; Tes Demo Otomatis
        </button>
      </div>` : ''}
      <div class="mb-3">
        <div class="d-flex justify-content-between align-items-center mb-1">
          <span class="text-muted small">Status Pekerjaan:</span>
          ${getStatusBadge(job.status)}
        </div>
        ${job.last_error ? `<div class="alert alert-danger py-2 small mb-0"><i class="bi bi-exclamation-triangle me-1"></i>${job.last_error}</div>` : ''}
      </div>
      ${job.output_path ? `
      <div class="card p-3 bg-black border-success mb-3 text-center">
        <h6 class="fw-bold text-success mb-2"><i class="bi bi-play-btn-fill me-1"></i>Video Shorts Siap Tayang (1080×1920 Vertikal)</h6>
        <div class="d-flex justify-content-center mb-2">
          <video controls width="220" style="max-height: 380px; border-radius: 8px; background: #000; box-shadow: 0 4px 14px rgba(0,0,0,0.6);" src="/api/jobs/${job.id}/video"></video>
        </div>
        <div class="d-flex justify-content-center gap-2 mt-2 flex-wrap">
          <a href="/api/jobs/${job.id}/video" download="short_job_${job.id}.mp4" class="btn btn-sm btn-success fw-bold"><i class="bi bi-download me-1"></i>Unduh Berkas MP4</a>
          ${job.youtube_video_id && !job.youtube_video_id.startsWith('demo_') 
            ? `<a href="https://youtube.com/shorts/${job.youtube_video_id}" target="_blank" class="btn btn-sm btn-danger fw-bold"><i class="bi bi-youtube me-1"></i>Buka di YouTube</a>`
            : `<span class="badge bg-secondary align-self-center py-2"><i class="bi bi-info-circle me-1"></i>Mode Simulasi (Tersimpan di Penyimpanan Lokal)</span>`
          }
        </div>
      </div>` : ''}
    `;

    if (job.plan && job.plan.segments) {
      body += `<h6 class="fw-bold text-warning mb-2"><i class="bi bi-stars me-1"></i>Prompt Google Flow Ultra (Kontinuitas Segmen)</h6>`;
      for (const seg of job.plan.segments) {
        body += `
          <div class="card p-2 mb-3 bg-black border-secondary">
            <div class="d-flex justify-content-between align-items-center mb-1">
              <span class="badge bg-primary">Segmen ${seg.index} &bull; ${seg.duration_seconds || 8} detik</span>
              <button class="btn btn-sm btn-outline-info py-0" onclick="copyPrompt('${encodeURIComponent(seg.prompt)}')"><i class="bi bi-clipboard me-1"></i>Salin Prompt</button>
            </div>
            <p class="small text-white mb-2" style="white-space: pre-wrap;">${seg.prompt}</p>
            
            <div class="border-top border-secondary pt-2 mt-1">
              <div class="small text-muted mb-1"><i class="bi bi-upload me-1"></i>Upload Video Segmen ${seg.index} (Hasil Google Flow):</div>
              <div class="input-group input-group-sm">
                <input type="file" id="file_seg_${job.id}_${seg.index}" accept="video/mp4" class="form-control form-control-sm bg-dark text-white border-secondary">
                <button class="btn btn-outline-success" onclick="uploadSegmentFile(${job.id}, ${seg.index})">Kirim Video</button>
              </div>
            </div>
          </div>
        `;
      }
    } else {
      body += `<div class="p-3 text-center text-muted small"><i class="bi bi-hourglass-split me-1"></i>Prompt AI sedang disiapkan oleh worker...</div>`;
    }

    if (job.metadata) {
      body += `
        <h6 class="fw-bold text-info mt-3 mb-2"><i class="bi bi-youtube me-1"></i>Metadata YouTube Shorts</h6>
        <div class="card p-2 bg-black border-secondary small">
          <div><strong>Judul:</strong> ${job.metadata.title}</div>
          <div class="mt-1"><strong>Deskripsi:</strong> ${job.metadata.description}</div>
          <div class="mt-1"><strong>Hashtags:</strong> ${(job.metadata.hashtags||[]).join(' ')}</div>
        </div>
      `;
    }

    document.getElementById('modalJobBody').innerHTML = body;
    new bootstrap.Modal(document.getElementById('jobModal')).show();
  }

  function copyPrompt(encoded) {
    const text = decodeURIComponent(encoded);
    navigator.clipboard.writeText(text).then(() => {
      alert("Prompt berhasil disalin ke clipboard! Paste langsung ke Google Flow Ultra.");
    });
  }

  async function simulateJob(jobId) {
    const btn = document.getElementById('simBtn_' + jobId);
    if (btn) { btn.disabled = true; btn.innerHTML = '⏳ Membuat video...'; }
    try {
      const res = await fetch(`/api/jobs/${jobId}/simulate`, {method: 'POST'});
      const data = await res.json();
      if (res.ok) {
        alert('✅ ' + data.message);
        fetchData();
        const modal = bootstrap.Modal.getInstance(document.getElementById('jobModal'));
        if (modal) modal.hide();
      } else {
        alert('❌ Gagal: ' + (data.detail || 'Error tidak diketahui'));
      }
    } catch(e) {
      alert('❌ Error: ' + e.message);
    }
    if (btn) { btn.disabled = false; btn.innerHTML = '&#x26A1; Tes Demo Otomatis'; }
  }

  async function uploadSegmentFile(jobId, segIndex) {
    const input = document.getElementById(`file_seg_${jobId}_${segIndex}`);
    if (!input.files || input.files.length === 0) {
      alert("Silakan pilih file MP4 terlebih dahulu.");
      return;
    }
    const formData = new FormData();
    formData.append("segment_index", segIndex);
    formData.append("file", input.files[0]);

    try {
      const res = await fetch(`/api/jobs/${jobId}/upload-segment`, {
        method: "POST",
        body: formData
      });
      const data = await res.json();
      if (res.ok) {
        alert(`Video segmen ${segIndex} berhasil diunggah! Worker akan melanjutkan proses.`);
        fetchData();
        bootstrap.Modal.getInstance(document.getElementById('jobModal')).hide();
      } else {
        alert("Gagal mengunggah: " + (data.detail || "Error tidak diketahui"));
      }
    } catch(e) {
      alert("Error: " + e.message);
    }
  }

  async function handleEnqueue(e) {
    e.preventDefault();
    const niche = document.getElementById('inputNiche').value;
    const count = parseInt(document.getElementById('inputCount').value);
    const interval_hours = parseInt(document.getElementById('inputInterval').value);
    const start_time = document.getElementById('inputStartTime').value;

    const res = await fetch('/api/enqueue', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ niche, count, interval_hours, start_time })
    });

    if (res.ok) {
      document.getElementById('inputNiche').value = '';
      fetchData();
      alert(`Berhasil menambahkan ${count} video ke antrean!`);
    } else {
      alert('Gagal menambahkan job ke antrean.');
    }
  }

  async function retryJob(id) {
    await fetch(`/api/jobs/${id}/retry`, {method: 'POST'});
    fetchData();
  }

  async function cancelJob(id) {
    if (confirm(`Yakin ingin membatalkan Job #${id}?`)) {
      await fetch(`/api/jobs/${id}/cancel`, {method: 'POST'});
      fetchData();
    }
  }

  async function fetchLogs() {
    try {
      const r = await fetch('/api/logs');
      const text = await r.text();
      const el = document.getElementById('logBox');
      el.innerText = text;
      el.scrollTop = el.scrollHeight;
    } catch(e) {}
  }

  fetchData();
  fetchLogs();
  setInterval(fetchData, 4000);
  setInterval(fetchLogs, 5000);
</script>
</body>
</html>
"""
