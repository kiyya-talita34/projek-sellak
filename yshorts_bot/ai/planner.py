from __future__ import annotations

from .provider import AIProvider
from ..models import Metadata, SegmentPrompt, VideoPlan

SYSTEM = """
Anda adalah AI Creative Director spesialis konten YouTube Shorts viral dan AI Video Prompter ahli Google Flow / Veo.
Tugas Anda:
1. Membaca niche/topik dan menciptakan konsep video Shorts berdurasi total minimal 16 detik (terdiri dari 2 segmen berdurasi masing-masing 8 detik).
2. Menghasilkan prompt video untuk Google Flow dalam bahasa Inggris yang sangat deskriptif, sinematik, dan optimal.
3. KONTINUITAS: Wajib menjaga konsistensi karakter/subjek, palet warna, pencahayaan, dan gaya visual antara Segmen 1 dan Segmen 2 (Segmen 2 merupakan kelanjutan langsung dari Segmen 1).
4. FORMAT: Setiap prompt wajib menyertakan arahan 'Vertical 9:16 aspect ratio, cinematic lighting, 4k photorealistic, smooth camera movement'.
5. PANTANGAN: 'No text, no letters, no logos, no subtitles in video, no distorted faces'.
6. Kembalikan HANYA format JSON valid tanpa tanda markdown.
""".strip()



def create_video_plan(provider: AIProvider, niche: str, segments: int = 2, segment_duration: int = 8) -> VideoPlan:
    user = f"""
Niche: {niche}
Jumlah segmen: {segments}
Durasi per segmen: {segment_duration} detik
Target: YouTube Shorts 9:16 minimal 16 detik.

Buat JSON dengan schema:
{{
  "idea": "...",
  "style": "...",
  "hook": "...",
  "segments": [{{"index": 1, "duration_seconds": 8, "prompt": "prompt detail Google Flow"}}],
  "metadata": {{"title": "...", "description": "...", "hashtags": ["#Shorts"], "tags": ["..."]}}
}}
""".strip()
    data = provider.generate_json(SYSTEM, user)
    segs = [SegmentPrompt(index=int(s["index"]), prompt=str(s["prompt"]), duration_seconds=int(s.get("duration_seconds", segment_duration))) for s in data["segments"]]
    return VideoPlan(idea=str(data["idea"]), style=str(data["style"]), hook=str(data["hook"]), segments=segs)


def create_metadata(provider: AIProvider, niche: str, plan: VideoPlan) -> Metadata:
    user = f"""
Niche: {niche}
Ide: {plan.idea}
Hook: {plan.hook}
Style: {plan.style}
Prompt segmen: {[s.prompt for s in plan.segments]}

Buat metadata YouTube Shorts dalam JSON:
{{"title":"maks 100 karakter", "description":"2-4 kalimat", "hashtags":["#Shorts"], "tags":["tag"]}}
""".strip()
    data = provider.generate_json(SYSTEM, user)
    # Provider mock sering mengembalikan metadata nested saat prompt planning; dukung dua bentuk.
    if "metadata" in data:
        data = data["metadata"]
    hashtags = [h if str(h).startswith("#") else f"#{h}" for h in data.get("hashtags", ["#Shorts"])]
    if "#Shorts" not in hashtags:
        hashtags.insert(0, "#Shorts")
    return Metadata(
        title=str(data.get("title", plan.idea))[:100],
        description=str(data.get("description", plan.hook)),
        hashtags=hashtags[:10],
        tags=[str(t)[:30] for t in data.get("tags", [niche, "shorts"])]
    )
