"""ShortSmash Studio — web UI.

`shortsmash serve` → open http://localhost:8000

Tabs: Studio (prompt → video), Trends (what's hot), Library (preview,
publish, stats). Generation runs in background threads; the UI polls.
"""
from __future__ import annotations

import shutil
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from . import pipeline
from . import trends as trends_mod
from .config import PROJECTS_DIR, Config
from creator_publish.publish import ADAPTERS, platform_status, publish as publish_adapter

app = FastAPI(title="ShortSmash Studio")

_jobs: dict[str, dict] = {}


class GenRequest(BaseModel):
    topic: str
    duration: int = 30
    language: str = "en"
    ratio: str = "9:16"


class TrendRequest(BaseModel):
    niche: str = "general"
    limit: int = 10


class PublishRequest(BaseModel):
    slug: str
    platforms: list[str]
    dry_run: bool = True


class PostVideoRequest(BaseModel):
    upload_id: str
    title: str = ""
    description: str = ""
    hashtags: list[str] = []
    platforms: list[str]
    dry_run: bool = True
    at: str | None = None  # natural language, e.g. "Friday 6pm" — schedules


def _run_generation(job_id: str, topic: str, duration: int, language: str = "en",
                    ratio: str = "9:16"):
    _jobs[job_id] = {"status": "running", "topic": topic, "started": time.time()}
    try:
        from shortsmash.edit import ASPECT_RATIOS
        overrides = {}
        if ratio in ASPECT_RATIOS:
            overrides["width"] = ASPECT_RATIOS[ratio]["w"]
            overrides["height"] = ASPECT_RATIOS[ratio]["h"]
        cfg = Config(overrides=overrides)
        workdir = pipeline.create_project(topic, duration, cfg)
        slug = workdir.name
        if language and language != "en":
            _jobs[job_id]["status"] = "dubbing"
            from shortsmash import dub as dub_mod
            dub_mod.dub_project(slug, language, cfg)
            slug = f"{slug}-dub-{language}"
        _jobs[job_id] = {"status": "done", "slug": slug,
                         "topic": topic,
                         "video_url": f"/api/video/{slug}"}
    except Exception as e:  # noqa: BLE001
        _jobs[job_id] = {"status": "failed", "topic": topic, "error": str(e)}


@app.post("/api/generate")
def generate(req: GenRequest):
    job_id = uuid.uuid4().hex[:8]
    t = threading.Thread(target=_run_generation,
                         args=(job_id, req.topic, req.duration, req.language,
                               req.ratio),
                         daemon=True)
    t.start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, "unknown job")
    return _jobs[job_id]


_SETTING_KEYS = [
    ("PIXABAY_API_KEY", "Pixabay", "Real stock footage (free key at pixabay.com/api)"),
    ("GEMINI_API_KEY", "Gemini", "AI scripts + footage checks (free) — also powers Veo 3.1 video (paid, needs billing)"),
    ("NVIDIA_API_KEY", "NVIDIA NIM", "Backup AI scripts (free at build.nvidia.com)"),
    ("XAI_API_KEY", "Grok (xAI)", "Alternative AI scripts (key at console.x.ai)"),
    ("AGNES_API_KEY", "Agnes AI", "AI-generated video clips (free at platform.agnes-ai.com)"),
    ("KLING_ACCESS_KEY", "Kling Access Key", "Kling AI video (paid, at kling.ai/dev/api-key)"),
    ("KLING_SECRET_KEY", "Kling Secret Key", "Kling signing secret (shown once — save it)"),
]

# Step-by-step connection guides shown in Settings. Top-in-world = no doc-hunting.
_CONNECT_GUIDES = {
    "grok": [
        "Go to <b>console.x.ai</b> → sign in → API keys → create one.",
        "Paste it below as Grok (xAI). Then set <b>llm_provider: grok</b> in config.",
        "Optional: <b>grok_model</b> picks the model (default grok-4-1-fast).",
    ],
    "veo": [
        "Veo 3.1 runs on your <b>Gemini API key</b> — no separate key exists.",
        "Create the key at aistudio.google.com in a project <b>with billing enabled</b> (Veo is paid-only; no free tier).",
        "Paste it as Gemini below, then set <b>visual_provider: veo</b> in config.",
        "Optional: <b>veo_model</b> — veo-3.1-fast-generate-preview (default), veo-3.1-generate-preview (best), or veo-3.1-lite-generate-preview (cheapest).",
    ],
    "kling": [
        "Sign up at <b>kling.ai</b> → open <b>kling.ai/dev/api-key</b>.",
        "Click <b>Create a new API Key</b> — copy the Access Key <b>and</b> the Secret Key (secret shows only once).",
        "Paste both below, then set <b>visual_provider: kling</b> in config.",
        "Optional: <b>kling_model</b> (default kling-v2-1), <b>kling_duration</b> 5 or 10.",
    ],
    "facebook": [
        "Go to developers.facebook.com → create an app (Business type).",
        "Add the Facebook Login product; request <b>publish_video</b> permission.",
        "Open Graph API Explorer, select your app, get a User Token.",
        "Paste it below as Facebook Token. Extend to long-lived in the Explorer.",
    ],
    "facebook_page": [
        "You need a Facebook Page you admin + a Meta app (see Facebook guide above).",
        "In Graph API Explorer: get a User Token, then query <b>/me/accounts</b>.",
        "Copy your Page's <b>id</b> → Facebook Page ID below.",
        "Copy the Page's <b>access_token</b> → Facebook Page Token below.",
        "Extend it to long-lived (never expires) via the Access Token Debugger.",
    ],
    "youtube": [
        "Go to console.cloud.google.com → create a project → enable YouTube Data API v3.",
        "Create OAuth credentials (Desktop app), download the JSON.",
        "Save it as <b>~/.shortsmash/youtube_client.json</b>.",
        "Run any publish once — a browser window asks you to grant access, then it's saved.",
    ],
    "tiktok": [
        "Apply at developers.tiktok.com (Content Posting API, needs review).",
        "Create an app, get the client key/secret, complete OAuth.",
        "Paste the resulting access token below as TikTok token.",
    ],
    "instagram": [
        "You need an Instagram Business/Creator account linked to a Facebook Page.",
        "Meta app with <b>instagram_basic</b> + <b>instagram_content_publish</b>.",
        "Get a long-lived Page token → query <b>/me/accounts</b> → find the IG user id.",
        "Paste token + user ID below. Videos also need a <b>public URL</b>.",
    ],
    "x": [
        "Apply at developer.x.com → create a project + app.",
        "Generate API key/secret + access token/secret with read+write.",
        "Paste all four below.",
    ],
    "threads": [
        "Meta app with Threads API access; create a Threads use case.",
        "Complete OAuth to get a user token + your Threads user ID.",
        "Paste both below. Videos need a <b>public URL</b>.",
    ],
    "bluesky": [
        "Log in at bsky.app → Settings → App passwords → create one.",
        "Paste your handle (you.bsky.social) + the app password below. Free, instant.",
    ],
    "linkedin": [
        "Create an app at developer.linkedin.com, request <b>w_member_social</b>.",
        "Complete OAuth, paste the access token below.",
    ],
    "pinterest": [
        "Create an app at developers.pinterest.com, request write scopes.",
        "Complete OAuth, paste the token + your board ID below.",
    ],
}

_SETTING_GROUPS = [
    ("🎬 Video Generation", "APIs needed to MAKE videos", [
        ("PIXABAY_API_KEY", "Pixabay", "Real stock footage (free key at pixabay.com/api)"),
        ("GEMINI_API_KEY", "Gemini", "AI scripts + footage checks (free) — also powers Veo 3.1 video (paid, needs billing)"),
        ("NVIDIA_API_KEY", "NVIDIA NIM", "Backup AI scripts (free at build.nvidia.com)"),
        ("XAI_API_KEY", "Grok (xAI)", "Alternative AI scripts (key at console.x.ai)"),
        ("AGNES_API_KEY", "Agnes AI", "AI-generated video clips (free at platform.agnes-ai.com)"),
        ("KLING_ACCESS_KEY", "Kling Access Key", "Kling AI video (paid, at kling.ai/dev/api-key)"),
        ("KLING_SECRET_KEY", "Kling Secret Key", "Kling signing secret (shown once — save it)"),
    ]),
    ("📢 Publishing", "APIs needed to POST videos (connect your socials)", [
        ("TIKTOK_ACCESS_TOKEN", "TikTok", "Access token (see docs/setup-tiktok.md)"),
        ("IG_ACCESS_TOKEN", "Instagram", "Access token (see docs/setup-instagram.md)"),
        ("IG_USER_ID", "Instagram User ID", "Your Instagram business user ID"),
        ("X_API_KEY", "X (Twitter) API Key", "Consumer key (see docs/setup-x.md)"),
        ("X_API_SECRET", "X API Secret", "Consumer secret"),
        ("X_ACCESS_TOKEN", "X Access Token", "Access token"),
        ("X_ACCESS_SECRET", "X Access Secret", "Access token secret"),
        ("THREADS_USER_ID", "Threads User ID", "Your Threads user ID"),
        ("THREADS_ACCESS_TOKEN", "Threads Token", "Access token (needs a public video URL)"),
        ("BSKY_HANDLE", "Bluesky Handle", "e.g. you.bsky.social"),
        ("BSKY_APP_PASSWORD", "Bluesky App Password", "App password from bsky.app settings"),
        ("LINKEDIN_ACCESS_TOKEN", "LinkedIn Token", "Access token"),
        ("PINTEREST_ACCESS_TOKEN", "Pinterest Token", "Access token"),
        ("PINTEREST_BOARD_ID", "Pinterest Board ID", "Target board for pins"),
        ("FB_ACCESS_TOKEN", "Facebook Token", "User token — posts to your personal timeline"),
        ("FB_PAGE_ID", "Facebook Page ID", "Your Page's numeric ID"),
        ("FB_PAGE_ACCESS_TOKEN", "Facebook Page Token", "Page token — posts to your Page"),
    ]),
]


def _env_path() -> Path:
    return Path.cwd() / ".env"


def _read_env_file() -> dict:
    vals = {}
    p = _env_path()
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip("'\"")
    return vals


@app.get("/api/languages")
def list_languages():
    from shortsmash.voice import DUB_LANGUAGES
    return {"languages": [{"code": c, "name": n} for c, n in DUB_LANGUAGES.items()]}


@app.get("/api/limits")
def api_limits():
    from shortsmash import api_limits as lim
    return {"apis": lim.status(), "videos_possible": lim.videos_possible_today()}


@app.get("/api/ratios")
def list_ratios():
    from shortsmash.edit import ASPECT_RATIOS
    return {"ratios": [{"key": k, **v} for k, v in ASPECT_RATIOS.items()]}


_AUTO_CFG = Path.home() / ".shortsmash" / "auto.json"


def _load_auto() -> dict:
    import json as _json
    if _AUTO_CFG.exists():
        return _json.loads(_AUTO_CFG.read_text())
    return {"enabled": False, "niche": "", "per_day": 1, "platforms": [],
            "language": "en", "post_time": "18:00"}


class AutoRequest(BaseModel):
    enabled: bool = False
    niche: str = ""
    per_day: int = 1
    platforms: list = []
    language: str = "en"
    post_time: str = "18:00"


@app.get("/api/auto")
def get_auto():
    from creator_publish import schedule as sched
    cfg = _load_auto()
    cfg["queued"] = len(sched.list_jobs("queued"))
    return cfg


@app.post("/api/auto")
def set_auto(req: AutoRequest):
    import json as _json
    from datetime import datetime, timedelta
    from creator_publish import schedule as sched
    cfg = {"enabled": req.enabled, "niche": req.niche,
           "per_day": max(1, min(5, req.per_day)),
           "platforms": req.platforms, "language": req.language,
           "post_time": req.post_time}
    _AUTO_CFG.parent.mkdir(parents=True, exist_ok=True)
    _AUTO_CFG.write_text(_json.dumps(cfg, indent=2))
    # Queue today's generation jobs immediately when enabling
    queued = 0
    if req.enabled and req.niche.strip():
        from shortsmash import api_limits as lim
        possible = lim.videos_possible_today()
        n = min(cfg["per_day"], max(0, possible))
        now = datetime.now()
        for i in range(n):
            run_at = now + timedelta(minutes=2 + i * 5)
            sched.enqueue(run_at, "auto_generate", {
                "niche": req.niche.strip(), "language": req.language,
                "platforms": req.platforms, "post_time": req.post_time,
            })
            queued += 1
    return {"ok": True, "queued": queued}


class LyricsRequest(BaseModel):
    lyrics: str
    title: str = ""
    duration: int = 30


@app.post("/api/lyrics")
def api_lyrics(req: LyricsRequest):
    from shortsmash import pipeline as pipe_mod
    cfg = Config()
    workdir = pipe_mod.create_lyrics_project(req.lyrics, req.title,
                                             max(5, min(180, req.duration)), cfg)
    import json as _json
    meta = _json.loads((workdir / "meta.json").read_text())
    return {"ok": True, "slug": workdir.name, "final": meta["final"]}


_KEY_GUIDE = {
    "XAI_API_KEY": "grok",
    "KLING_ACCESS_KEY": "kling", "KLING_SECRET_KEY": "kling",
    "GEMINI_API_KEY": "veo",
    "FB_ACCESS_TOKEN": "facebook",
    "FB_PAGE_ID": "facebook_page", "FB_PAGE_ACCESS_TOKEN": "facebook_page",
    "TIKTOK_ACCESS_TOKEN": "tiktok",
    "IG_ACCESS_TOKEN": "instagram", "IG_USER_ID": "instagram",
    "X_API_KEY": "x", "X_API_SECRET": "x", "X_ACCESS_TOKEN": "x", "X_ACCESS_SECRET": "x",
    "THREADS_USER_ID": "threads", "THREADS_ACCESS_TOKEN": "threads",
    "BSKY_HANDLE": "bluesky", "BSKY_APP_PASSWORD": "bluesky",
    "LINKEDIN_ACCESS_TOKEN": "linkedin",
    "PINTEREST_ACCESS_TOKEN": "pinterest", "PINTEREST_BOARD_ID": "pinterest",
}


@app.get("/api/settings")
def get_settings():
    import os as _os
    file_vals = _read_env_file()
    groups = []
    for gname, gdesc, keys in _SETTING_GROUPS:
        items = []
        for k, label, hint in keys:
            guide = _CONNECT_GUIDES.get(_KEY_GUIDE.get(k, ""), [])
            items.append({"key": k, "label": label, "hint": hint,
                          "guide": guide,
                          "set": bool(_os.environ.get(k) or file_vals.get(k))})
        groups.append({"name": gname, "desc": gdesc, "keys": items})
    return {"groups": groups}


class SettingsRequest(BaseModel):
    keys: dict = {}


@app.post("/api/settings")
def save_settings(req: SettingsRequest):
    allowed = {k for _, _, keys in _SETTING_GROUPS for k, _, _ in keys}
    vals = _read_env_file()
    for k, v in (req.keys or {}).items():
        if k not in allowed:
            continue
        v = (v or "").strip()
        if v:
            vals[k] = v
        elif k in vals:
            del vals[k]
    lines = [f"{k}={v}" for k, v in vals.items()]
    _env_path().write_text("\n".join(lines) + "\n")
    # make them live for this process too
    import os as _os
    for k, v in vals.items():
        _os.environ[k] = v
    return {"ok": True, "saved": sorted(vals)}


@app.get("/api/voice-sample")
def voice_sample(lang: str = "en"):
    """Generate a short voice sample so users can hear the quality."""
    import tempfile
    from shortsmash import voice as voice_mod
    samples = {
        "en": "Hello! This is how your videos will sound.",
        "ur": "السلام علیکم! آپ کی ویڈیوز ایسی آواز میں ہوں گی۔",
        "hi": "नमस्ते! आपके वीडियो ऐसे सुनाई देंगे।",
        "zh": "你好！你的视频听起来就是这样的。",
        "ar": "مرحباً! هكذا ستبدو مقاطع الفيديو الخاصة بك.",
        "es": "¡Hola! Así sonarán tus videos.",
    }
    text = samples.get(lang, samples["en"])
    out = Path(tempfile.gettempdir()) / f"shortsmash-voice-{lang}.mp3"
    v = (voice_mod.SAMPLE_VOICES.get(lang) or voice_mod.SAMPLE_VOICES["en"])[0]
    cfg = Config(overrides={"voice": v})
    voice_mod.synthesize_scene(text, out, cfg)
    from fastapi.responses import FileResponse
    return FileResponse(out, media_type="audio/mpeg", filename=f"voice-{lang}.mp3")


@app.get("/api/status")
def api_status():
    """Which external services are configured. Never leaks key values."""
    import os as _os
    def has(*names):
        return any(_os.environ.get(n) for n in names)
    return {"services": [
        {"name": "Stock footage", "ok": has("PIXABAY_API_KEY"),
         "hint": "Pixabay key → real footage (else placeholders)"},
        {"name": "AI script", "ok": has("GEMINI_API_KEY", "NVIDIA_API_KEY"),
         "hint": "Gemini/NVIDIA key → AI scripts (else templates)"},
        {"name": "AI visuals", "ok": has("AGNES_API_KEY"),
         "hint": "Agnes key → AI-generated clips (else stock)"},
    ]}


@app.post("/api/trends")
def trends(req: TrendRequest):
    ts = trends_mod.harvest(req.niche, req.limit)
    return [{"title": t.title, "source": t.source, "score": t.score,
             "url": t.url} for t in ts]


@app.get("/api/projects")
def projects():
    return pipeline.list_projects()


@app.get("/api/video/{slug}")
def video(slug: str):
    f = Path(PROJECTS_DIR) / slug / "final.mp4"
    if not f.exists():
        raise HTTPException(404, "no video yet")
    return FileResponse(f, media_type="video/mp4")


@app.post("/api/publish")
def publish(req: PublishRequest):
    from .pipeline import load_plan
    plan = load_plan(req.slug)
    video_path = Path(PROJECTS_DIR) / req.slug / "final.mp4"
    results = []
    for p in req.platforms:
        if p not in ADAPTERS:
            results.append({"platform": p, "error": "unknown platform"})
            continue
        try:
            r = publish_adapter(p, video_path, plan.title or req.slug,
                                plan.description or "",
                                plan.hashtags or [], dry_run=req.dry_run)
            results.append(r)
        except Exception as e:  # noqa: BLE001
            results.append({"platform": p, "error": str(e)})
    return results


@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)):
    """Stage a user video for manual posting. Returns validation info."""
    from .config import UPLOADS_DIR, ensure_home
    from .pipeline import stage_upload, validate_video

    ensure_home()
    suffix = Path(file.filename or "video.mp4").suffix.lower()
    if suffix not in {".mp4", ".mov", ".webm", ".mkv", ".m4v"}:
        raise HTTPException(400, f"Unsupported format {suffix} — use MP4/MOV/WebM.")
    tmp = UPLOADS_DIR / f"incoming-{uuid.uuid4().hex[:8]}{suffix}"
    with open(tmp, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        staged = stage_upload(tmp, Config())
    except (FileNotFoundError, ValueError) as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, str(e))
    tmp.unlink(missing_ok=True)
    info = validate_video(staged, [])
    return {"upload_id": staged.name, **info}


@app.post("/api/suggest-caption")
def suggest_caption_api(req: dict):
    """AI-generate title/description/hashtags for an uploaded video."""
    from .config import UPLOADS_DIR

    video = UPLOADS_DIR / req.get("upload_id", "")
    if not video.exists():
        raise HTTPException(404, "upload not found — upload the video first")
    try:
        return pipeline.suggest_caption(video, Config())
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@app.post("/api/post-video")
def post_video(req: PostVideoRequest):
    """Post an uploaded video now, or schedule it with natural language."""
    from creator_publish import schedule as sched_mod
    from .config import UPLOADS_DIR

    video = UPLOADS_DIR / req.upload_id
    if not video.exists():
        raise HTTPException(404, "upload not found — upload the video first")
    hashtags = [t if t.startswith("#") else f"#{t}"
                for t in req.hashtags if t.strip()]
    title = req.title.strip() or video.stem.replace("-", " ").title()
    if req.at:
        run_at = sched_mod.parse_when(req.at, Config().get("timezone"))
        job_id = sched_mod.enqueue(run_at, "publish_video", {
            "video": str(video), "title": title,
            "description": req.description, "hashtags": hashtags,
            "platforms": req.platforms, "dry_run": req.dry_run})
        return {"scheduled": True, "job_id": job_id,
                "run_at": run_at.isoformat()}
    results = []
    for p in req.platforms:
        if p not in ADAPTERS:
            results.append({"platform": p, "error": "unknown platform"})
            continue
        try:
            r = pipeline.publish_video(video, title, req.description,
                                       hashtags, [p], dry_run=req.dry_run)
            results.append(r[p])
        except Exception as e:  # noqa: BLE001
            results.append({"platform": p, "error": str(e)})
    return {"scheduled": False, "results": results}


@app.get("/api/platforms")
def platforms():
    return platform_status()


@app.get("/api/stats")
def stats():
    projects = pipeline.list_projects()
    videos = [p for p in projects if p["has_video"]]
    total_bytes = sum(p.get("size", 0) for p in videos)
    return {
        "projects": len(projects),
        "videos": len(videos),
        "total_mb": round(total_bytes / 1e6, 1),
        "platforms": list(ADAPTERS),
    }


# --- ShortSmash Social 0.4 (minimal, no new dependencies) ---
# Approval is human-only: these endpoints approve/reject drafts in the
# Studio UI. There is deliberately NO live-publish endpoint here, and
# the MCP tools below deliberately have no approve/publish tools.
class SocialActionRequest(BaseModel):
    pack_id: str
    platform: str | None = None


@app.get("/api/social/packs")
def social_packs():
    from . import approval
    return approval.list_packs()


@app.post("/api/social/approve")
def social_approve(req: SocialActionRequest):
    from . import approval
    try:
        return approval.approve_pack(req.pack_id, req.platform)
    except KeyError as exc:
        raise HTTPException(404, str(exc))


@app.post("/api/social/reject")
def social_reject(req: SocialActionRequest):
    from . import approval
    try:
        return approval.reject_pack(req.pack_id, req.platform)
    except KeyError as exc:
        raise HTTPException(404, str(exc))


_SOCIAL_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>ShortSmash Social - Approval Queue</title>
<style>body{font-family:system-ui;background:#0a0d13;color:#eef1f7;margin:0;padding:24px}
button{background:#f97316;border:none;color:#fff;padding:8px 16px;border-radius:8px;cursor:pointer;margin-right:8px}
.pack{border:1px solid #222a3d;border-radius:12px;padding:16px;margin-bottom:16px}
.draft{border-top:1px solid #222a3d;padding:8px 0;font-size:14px}
small{color:#8f99ae}</style></head><body>
<h1>ShortSmash Social - Approval Queue</h1>
<p><small>Human approval only. No live publishing from this page.
An agent must not approve its own post.</small></p>
<div id="packs">Loading…</div>
<script>
async function load(){
  const packs = await (await fetch('/api/social/packs')).json();
  const el = document.getElementById('packs');
  if(!packs.length){ el.innerHTML = '<p>No packs yet. Create one with: shortsmash social repurpose &lt;file&gt;</p>'; return; }
  el.innerHTML = packs.map(p => `<div class="pack"><h3>${p.topic}</h3><small>Pack ${p.id}</small>
    ${p.drafts.map(d => `<div class="draft"><b>${d.platform}</b> [${d.status}] ${d.text.slice(0,180)}</div>`).join('')}
    <button onclick="act('${p.id}','approve')">Approve all</button>
    <button onclick="act('${p.id}','reject')">Reject all</button></div>`).join('');
}
async function act(id, kind){
  await fetch('/api/social/' + kind, {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({pack_id: id})});
  load();
}
load();
</script></body></html>"""


@app.get("/social", response_class=HTMLResponse)
def social_page():
    return _SOCIAL_PAGE


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML_PAGE


HTML_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ShortSmash Studio ⚒️</title>
<style>
:root{--bg:#0a0d13;--panel:#12161f;--panel2:#0d1119;--line:#222a3d;--line2:#2c3650;
--txt:#eef1f7;--dim:#8f99ae;--dim2:#6b7488;
--acc:#f97316;--acc2:#fb923c;--acc-dim:#f9731626;
--grn:#22c55e;--red:#ef4444;--blu:#60a5fa;
--grad:linear-gradient(135deg,#f97316,#ec4899);
--shadow:0 8px 32px rgba(0,0,0,.45);
--r:14px}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);
font-family:"Inter",system-ui,-apple-system,"Segoe UI",sans-serif;
-webkit-font-smoothing:antialiased;line-height:1.55}
::selection{background:var(--acc-dim)}
header{padding:20px 32px;border-bottom:1px solid var(--line);display:flex;
align-items:center;gap:12px;background:linear-gradient(180deg,#10141d,#0a0d13);
position:sticky;top:0;z-index:50;backdrop-filter:blur(12px)}
header h1{margin:0;font-size:22px;font-weight:800;letter-spacing:-.02em}
header h1 span{background:var(--grad);-webkit-background-clip:text;
background-clip:text;-webkit-text-fill-color:transparent}
header .tag{font-size:11px;color:var(--dim);border:1px solid var(--line);
padding:3px 10px;border-radius:20px;margin-left:8px}
nav{display:flex;gap:4px;padding:14px 32px 0;overflow-x:auto}
nav button{background:none;border:none;color:var(--dim);font-size:14.5px;
font-weight:600;padding:11px 18px;cursor:pointer;border-radius:10px 10px 0 0;
border-bottom:2px solid transparent;transition:all .18s;white-space:nowrap}
nav button:hover{color:var(--txt);background:#ffffff08}
nav button.on{color:var(--txt);border-bottom-color:var(--acc);
background:linear-gradient(180deg,transparent,var(--acc-dim))}
main{padding:26px 32px;max-width:1180px;margin:0 auto}
.tab{display:none}.tab.on{display:block;animation:fadein .25s ease}
@keyframes fadein{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);
padding:24px;margin-bottom:20px;box-shadow:var(--shadow)}
.panel h3{margin:0 0 16px;font-size:18px;font-weight:700;letter-spacing:-.01em}
.panel h4{font-size:15px;font-weight:700;margin:20px 0 6px;color:var(--txt)}
input,select,textarea{background:var(--panel2);border:1px solid var(--line);
color:var(--txt);border-radius:10px;padding:11px 14px;font-size:14px;width:100%;
transition:border-color .18s,box-shadow .18s;font-family:inherit}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--acc);
box-shadow:0 0 0 3px var(--acc-dim)}
textarea{resize:vertical;min-height:90px;line-height:1.6}
label{display:block;font-size:13px;font-weight:600;color:var(--dim);margin-bottom:6px}
button.go{background:var(--grad);border:none;color:#fff;font-weight:700;
border-radius:10px;padding:12px 26px;font-size:15px;cursor:pointer;
transition:transform .15s,box-shadow .15s;box-shadow:0 4px 16px #f9731640}
button.go:hover:not(:disabled){transform:translateY(-1px);box-shadow:0 6px 22px #f9731660}
button.go:disabled{opacity:.5;cursor:default;transform:none}
button.ghost{background:none;border:1px solid var(--line);color:var(--dim);
border-radius:10px;padding:9px 18px;font-size:13px;font-weight:600;cursor:pointer;transition:all .18s}
button.ghost:hover{color:var(--txt);border-color:var(--acc)}
.row{display:flex;gap:14px;align-items:end;flex-wrap:wrap}.row>*{flex:1;min-width:140px}
.trend{padding:14px 16px;border-bottom:1px solid var(--line);cursor:pointer;
border-radius:8px;transition:background .15s}
.trend:hover{background:#1a2130}.trend small{color:var(--dim)}
video{width:280px;border-radius:var(--r);border:1px solid var(--line);box-shadow:var(--shadow)}
.proj{display:flex;gap:18px;align-items:center;padding:16px;
border-bottom:1px solid var(--line);border-radius:10px;transition:background .15s}
.proj:hover{background:#ffffff06}
.badge{font-size:11.5px;font-weight:600;padding:4px 11px;border-radius:20px;
background:#1a2130;color:var(--dim);letter-spacing:.02em}
.badge.ok{background:#052e16;color:var(--grn)}
.log{font-family:ui-monospace,monospace;font-size:13px;color:var(--dim);
white-space:pre-wrap;line-height:1.7}
.log b{color:var(--txt)}
.stat{display:inline-block;margin:0 34px 8px 0}.stat b{font-size:30px;
display:block;font-weight:800;letter-spacing:-.02em;
background:linear-gradient(180deg,#fff,#aab4c8);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.stat span{color:var(--dim);font-size:13px}
.platgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px}
.pcard{background:var(--panel2);border:1px solid var(--line);border-radius:12px;
padding:18px;cursor:pointer;transition:all .18s}
.pcard:hover:not(.off){border-color:var(--line2);transform:translateY(-2px)}
.pcard.sel{border-color:var(--acc);box-shadow:0 0 0 1px var(--acc),0 4px 16px #f9731630}
.pcard.off{opacity:.5;cursor:not-allowed}
.pcard h4{margin:0 0 8px;font-size:15px;display:flex;align-items:center;gap:8px}
.pcard .st{font-size:11.5px;font-weight:600;padding:3px 10px;border-radius:12px}
.st.ready{background:#052e16;color:var(--grn)}
.st.needs_auth{background:#451a03;color:#fdba74}
.st.needs_setup{background:#1a2130;color:var(--dim)}
.pcard small{color:var(--dim);font-size:12.5px;line-height:1.6}
.pcard input{width:auto;margin-right:8px}
.res{padding:12px 14px;border-radius:10px;margin-bottom:10px;font-size:14px}
.res.ok{background:#052e16;border:1px solid #14532d}
.res.err{background:#2a0a0a;border:1px solid #7f1d1d}
.res a{color:#7dd3fc}
.hero{background:linear-gradient(135deg,#1c1030 0%,#0f1f2e 60%,#0a0d13 100%);
border:1px solid #f9731640;position:relative;overflow:hidden}
.hero::before{content:"";position:absolute;inset:0;
background:radial-gradient(600px 200px at 20% 0%,#f9731620,transparent);pointer-events:none}
.hero h2{margin:0 0 12px;font-size:26px;font-weight:800;letter-spacing:-.02em;position:relative}
.hero h2 span{background:var(--grad);-webkit-background-clip:text;
background-clip:text;-webkit-text-fill-color:transparent}
.hero p{color:var(--dim);line-height:1.7;margin:0 0 16px;max-width:660px;position:relative}
.hero p b{color:var(--txt)}
.tgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:14px}
.tcard{background:var(--panel2);border:1px solid var(--line);border-radius:12px;
padding:18px;cursor:pointer;transition:all .2s;position:relative;overflow:hidden}
.tcard::after{content:"";position:absolute;inset:0;opacity:0;transition:opacity .2s;
background:radial-gradient(300px 120px at 50% 0%,#f9731618,transparent)}
.tcard:hover{border-color:var(--acc);transform:translateY(-3px);
box-shadow:0 8px 24px rgba(0,0,0,.4)}
.tcard:hover::after{opacity:1}
.tcard .emo{font-size:30px;margin-bottom:10px}
.tcard h4{margin:0 0 6px;font-size:15px}
.tcard p{margin:0;color:var(--dim);font-size:12.5px;line-height:1.55}
.tcard .use{margin-top:12px;font-size:13px;color:var(--acc2);font-weight:700}
.apistat{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}
.apistat .badge{cursor:default}
h3 .sub{font-weight:500;font-size:13px;color:var(--dim);margin-left:10px}
@media(max-width:640px){header,nav,main{padding-left:16px;padding-right:16px}
.hero h2{font-size:21px}.tgrid{grid-template-columns:1fr 1fr}}
</style></head><body>
<header><h1>⚒️ Short<span>Smash</span> Studio</h1><span class="tag">v0.2.0</span></header>
<nav>
<button class="on" onclick="tab('studio')">🎬 Studio</button>
<button onclick="tab('trends')">📈 Trends</button>
<button onclick="tab('library')">📚 Library</button>
<button onclick="tab('publish')">🚀 Publish</button>
<button onclick="tab('stats')">📊 Stats</button>
<button onclick="tab('settings')">⚙️ Settings</button>
<button onclick="tab('lyrics')">🎵 Lyrics</button>
<button onclick="tab('auto')">🤖 Auto</button>
</nav><main>
<div id="t-studio" class="tab on">
 <div class="panel hero" id="welcome">
  <h2>Turn any idea into a viral short. <span>Free, forever.</span></h2>
  <p>Pick a template below, or describe your own topic. ShortSmash writes the script,
  narrates it, finds the footage, adds karaoke captions, and renders a 1080×1920
  video — then publishes it to 10 platforms in one click. <b>50 languages</b> supported.</p>
  <button class="ghost" onclick="document.getElementById('welcome').style.display='none'">Got it — hide this</button>
 </div>
 <div class="panel"><h3>🎨 Start from a template</h3>
  <div class="tgrid" id="templates"></div>
 </div>
 <div class="panel"><h3>✏️ Or describe your own video</h3>
  <div class="row">
   <div><label>Topic</label><input id="topic" placeholder="hyderabadi biryani"></div>
   <div style="max-width:120px"><label>Seconds</label>
    <input id="dur" type="number" value="30" min="5" max="180"></div>
   <div style="max-width:150px"><label>Language</label>
    <select id="lang">
     <option value="en">English</option>
    </select></div>
   <div style="max-width:170px"><label>Aspect ratio</label>
    <select id="ratio">
     <option value="9:16">9:16 Vertical</option>
    </select></div>
   <div style="max-width:150px"><label>&nbsp;</label><br>
    <button class="go" id="genbtn" onclick="generate()">Smash ⚒️</button></div>
  </div>
  <p class="log" id="genlog"></p>
  <p class="log" style="margin-top:8px">🌍 <b>50 languages, one click.</b> Pick Urdu, Hindi, Chinese, Arabic, Spanish… and ShortSmash
  dubs the entire video — native human-like voice, translated captions, RTL-correct. No other free tool does this.</p>
  <div class="apistat" id="apistat"></div>
 </div>
</div>
<div id="t-trends" class="tab">
 <div class="panel"><h3>What's hot</h3>
  <div class="row"><div><select id="niche">
   <option>food</option><option>ai</option><option>tech</option>
   <option>fitness</option><option>finance</option><option>travel</option>
   <option>motivation</option><option>health</option><option>general</option>
  </select></div>
  <div style="max-width:170px"><button class="go" onclick="loadTrends()">Refresh</button></div></div>
  <div id="trendlist" style="margin-top:12px"></div>
 </div>
</div>
<div id="t-library" class="tab"><div class="panel"><h3>Projects</h3>
 <div id="projlist"></div></div></div>
<div id="t-publish" class="tab">
 <div class="panel"><h3>🚀 Publish to platforms</h3>
  <div class="row" style="margin-bottom:14px"><div><label>Video</label>
   <select id="pubslug" onchange="updateCount()"></select></div>
   <div style="max-width:220px"><label>Mode</label><select id="pubmode">
    <option value="dry">🧪 Dry run (safe test)</option>
    <option value="live">🚨 Go live (posts for real)</option>
   </select></div></div>
  <div id="platgrid" class="platgrid"></div>
  <div style="margin-top:16px;display:flex;gap:10px;align-items:center">
   <button class="go" id="pubbtn" onclick="doPublish()">Publish to selected</button>
   <span class="log" id="pubcount"></span></div>
  <div id="pubresults" style="margin-top:14px"></div>
 </div>
 <div class="panel"><h3>📤 Post your own video</h3>
  <p class="log">Upload any video — ShortSmash validates it, warns about platform
  limits, can AI-write your title + hashtags, and posts or schedules it.</p>
  <div class="row"><div><label>Video file (MP4/MOV/WebM)</label>
   <input type="file" id="upfile" accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm,.mkv,.m4v"></div>
   <div><label>&nbsp;</label><button class="go" onclick="doUpload()">Upload & validate</button></div></div>
  <div id="upinfo" class="log" style="margin-top:10px"></div>
  <div id="upform" style="display:none;margin-top:14px">
   <div class="row"><div><label>Title</label><input id="uptitle" type="text" style="width:100%"></div>
    <div style="max-width:200px"><label>&nbsp;</label>
     <button onclick="doCaption()">✨ AI caption</button></div></div>
   <div class="row" style="margin-top:10px"><div><label>Description</label>
    <input id="updesc" type="text" style="width:100%"></div></div>
   <div class="row" style="margin-top:10px"><div><label>Hashtags (comma-separated)</label>
    <input id="uptags" type="text" style="width:100%" placeholder="travel, viral"></div>
    <div style="max-width:260px"><label>Schedule (optional)</label>
     <input id="upat" type="text" style="width:100%" placeholder='e.g. "Friday 6pm"'></div></div>
   <div style="margin-top:14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
    <button class="go" id="uppost" onclick="doPostVideo(false)">Post to selected</button>
    <button id="upsched" onclick="doPostVideo(true)">📅 Schedule</button>
    <span class="log">Uses the platform checkboxes above.</span></div>
   <div id="upresults" style="margin-top:14px"></div>
  </div>
 </div>
 <div class="panel"><h3>🔑 Platform setup</h3>
  <p class="log">One-time setup per platform. Keys go in your environment or
  <b>.env</b> — never in the repo. See <b>docs/setup-&lt;platform&gt;.md</b> for the
  2-minute walkthrough each.</p>
  <div id="setuphelp"></div></div>
</div>
<div id="t-stats" class="tab"><div class="panel"><h3>Studio stats</h3>
 <div id="statlist"></div></div></div>
<div id="t-auto" class="tab">
 <div class="panel hero"><h2>🤖 Autopilot. <span>Videos while you sleep.</span></h2>
  <p>Pick a niche. ShortSmash harvests trends, generates videos, dubs them, and
  publishes on schedule — <b>respecting every API's free limits automatically</b>.
  Keep <b>shortsmash worker</b> running (or the Studio server) to process the queue.</p>
 </div>
 <div class="panel"><h3>⚙️ Autopilot settings</h3>
  <div class="row">
   <div><label>Niche</label><input id="auto_niche" placeholder="e.g. motivation, football, cooking"></div>
   <div style="max-width:110px"><label>Per day</label>
    <select id="auto_perday"><option>1</option><option>2</option><option>3</option></select></div>
   <div style="max-width:130px"><label>Post at</label><input id="auto_time" type="time" value="18:00"></div>
   <div style="max-width:170px"><label>Language</label><select id="auto_lang"><option value="en">English</option></select></div>
  </div>
  <div style="margin:14px 0"><label>Publish to (leave empty = generate only)</label>
   <div id="auto_plats" class="apistat"></div></div>
  <button class="go" id="autobtn" onclick="saveAuto()">Enable autopilot 🤖</button>
  <span class="log" id="automsg" style="margin-left:12px"></span>
  <p class="log" id="autoqueuestatus" style="margin-top:10px"></p>
 </div>
 <div class="panel"><h3>📊 API limits <span class="sub">today's free-tier usage</span></h3>
  <p class="log" id="limsummary"></p>
  <div id="limlist"></div>
 </div>
</div>
<div id="t-lyrics" class="tab">
 <div class="panel"><h3>🎵 Lyrics video <span class="sub">karaoke-style, word-by-word</span></h3>
  <p class="log">Paste lyrics — one line per screen. ShortSmash times each line across
  the video, highlights words karaoke-style, and renders over cinematic footage with music.</p>
  <div style="margin-bottom:12px"><label>Song title</label>
   <input id="lyr_title" placeholder="e.g. Midnight Dreams"></div>
  <div style="margin-bottom:12px"><label>Lyrics (one line per screen)</label>
   <textarea id="lyr_text" rows="8" placeholder="Line one of the song&#10;Line two of the song&#10;..."></textarea></div>
  <div class="row">
   <div style="max-width:120px"><label>Seconds</label>
    <input id="lyr_dur" type="number" value="30" min="5" max="180"></div>
   <div style="max-width:200px"><label>&nbsp;</label><br>
    <button class="go" id="lyrbtn" onclick="generateLyrics()">Create lyrics video 🎵</button></div>
  </div>
  <p class="log" id="lyrlog"></p>
 </div>
</div>
<div id="t-settings" class="tab">
 <div class="panel"><h3>⚙️ Settings <span class="sub">every API ShortSmash uses, in one place</span></h3>
  <p class="log">Keys are stored in a <b>.env</b> file next to the app — never shown back, never uploaded anywhere.</p>
  <div id="keyform"></div>
  <div style="margin-top:14px"><button class="go" onclick="saveSettings()">Save keys</button>
  <span class="log" id="savemsg" style="margin-left:12px"></span></div>
 </div>
 <div class="panel"><h3>🎙️ Voice</h3>
  <p class="log">ShortSmash uses the most human-like free voice available, automatically:</p>
  <p class="log" style="margin-top:8px"><b>1. Edge-TTS</b> (Microsoft neural — studio quality, needs internet, no key)<br>
  <b>2. Piper</b> (offline neural — ships with the app, 4 languages)<br>
  <b>3. gTTS</b> (last resort)</p>
  <p class="log" style="margin-top:8px">Every dub uses a <b>native speaker voice</b> for its language — Urdu dubs get a Urdu voice, Chinese dubs get a Chinese voice. Test yours:</p>
  <div class="row"><div><select id="vtestlang">
   <option value="en">English</option><option value="ur">Urdu</option>
   <option value="hi">Hindi</option><option value="zh">Chinese</option>
   <option value="ar">Arabic</option><option value="es">Spanish</option>
  </select></div>
  <div style="max-width:200px"><label>&nbsp;</label><br><button class="go" onclick="testVoice()">🔊 Play sample</button></div></div>
  <p class="log" id="voicemsg" style="margin-top:8px"></p>
  <audio id="vaudio" controls style="display:none;margin-top:8px;width:100%"></audio>
 </div>
</div>
</main>
<script>
function tab(n){document.querySelectorAll('.tab').forEach(e=>e.classList.remove('on'));
document.getElementById('t-'+n).classList.add('on');
document.querySelectorAll('nav button').forEach(e=>e.classList.remove('on'));
event.target.classList.add('on');
if(n==='library')loadProjects();if(n==='stats')loadStats();
if(n==='trends')loadTrends();if(n==='publish')loadPublish();
if(n==='settings')loadSettings();
if(n==='lyrics')loadLanguages();
async function loadRatios(){
 try{
  const r=await fetch('/api/ratios');const j=await r.json();
  const sel=document.getElementById('ratio');
  sel.innerHTML=j.ratios.map(x=>
   `<option value="${x.key}" title="${x.desc}">${x.label} — ${x.desc}</option>`).join('');
 }catch(e){}
}
loadRatios();
if(n==='auto'){loadAuto();loadLimits();}}
async function generateLyrics(){
 const lyrics=document.getElementById('lyr_text').value.trim();
 const title=document.getElementById('lyr_title').value.trim();
 const duration=parseInt(document.getElementById('lyr_dur').value)||30;
 const log=document.getElementById('lyrlog');const btn=document.getElementById('lyrbtn');
 if(!lyrics){log.textContent='❌ Paste some lyrics first.';return;}
 btn.disabled=true;log.textContent='🎵 Rendering your lyrics video… (a minute or two)';
 try{
  const r=await fetch('/api/lyrics',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({lyrics,title,duration})});
  const j=await r.json();
  log.innerHTML=j.ok?('✅ Done! Find it in the <b>Library</b> tab.'):'❌ '+friendlyError(j);
 }catch(e){log.textContent='❌ '+friendlyError(e);}
 btn.disabled=false;
}
const TEMPLATES=[
 {emo:"🔥",name:"Motivation",topic:"5 brutal truths about success nobody tells you",dur:30,desc:"Punchy mindset content. High retention, high shares."},
 {emo:"🍜",name:"Food",topic:"hyderabadi biryani recipe secrets",dur:35,desc:"Recipe reveals and food stories that make mouths water."},
 {emo:"🤖",name:"AI & Tech",topic:"how AI video generation actually works",dur:30,desc:"Explainers on tech trends. Great for curious audiences."},
 {emo:"💪",name:"Fitness",topic:"7 minute morning routine that changes everything",dur:30,desc:"Quick health wins. Huge save-and-share category."},
 {emo:"✈️",name:"Travel",topic:"hidden gems in istanbul most tourists miss",dur:35,desc:"Wanderlust fuel. Aspirational and highly shareable."},
 {emo:"💰",name:"Money",topic:"how compound interest makes you rich slowly",dur:30,desc:"Finance basics that everyone wishes they learned earlier."},
];
function renderTemplates(){
 document.getElementById('templates').innerHTML=TEMPLATES.map((t,i)=>
  `<div class="tcard" onclick="useTemplate(${i})"><div class="emo">${t.emo}</div><h4>${t.name}</h4><p>${t.desc}</p><div class="use">Use template →</div></div>`).join('');
}
function useTemplate(i){
 const t=TEMPLATES[i];
 document.getElementById('topic').value=t.topic;
 document.getElementById('dur').value=t.dur;
 document.getElementById('topic').scrollIntoView({behavior:'smooth',block:'center'});
 document.getElementById('topic').focus();
}
function friendlyError(e){
 if(!e) return 'Something went wrong.';
 if(/non-zero exit status|ffmpeg/i.test(e)){
  if(/fontfile|drawtext|vignette|gradients/i.test(e))
   return 'Video renderer crashed (usually a font or filter issue on Windows). Try again — if it repeats, run `shortsmash doctor`.';
  return 'Video rendering failed. Run `shortsmash doctor` to check ffmpeg.';
 }
 if(/pixabay|pexels|api key|401|403/i.test(e))
  return 'Stock footage API issue — check your API key, or the video will use placeholder visuals.';
 if(/edge-tts|piper|tts|voice/i.test(e))
  return 'Voice generation failed — check your internet or voice setup.';
 if(/gemini|llm|nvidia|429|quota/i.test(e))
  return 'AI script service is busy or out of quota — try again in a few minutes.';
 return e.length>200 ? e.slice(0,200)+'…' : e;
}
async function generate(){
 const topic=document.getElementById('topic').value.trim();
 if(!topic){alert('Enter a topic');return;}
 const btn=document.getElementById('genbtn');btn.disabled=true;
 const log=document.getElementById('genlog');log.textContent='Forging… (script → voice → verified footage → captions → render)';
 const lang=document.getElementById('lang').value;
 const ratio=document.getElementById('ratio').value||'9:16';
 const r=await fetch('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({topic,duration:+document.getElementById('dur').value,language:lang,ratio})});
 const {job_id}=await r.json();
 const iv=setInterval(async()=>{
  const s=await (await fetch('/api/jobs/'+job_id)).json();
  if(s.status==='done'){clearInterval(iv);btn.disabled=false;
   log.innerHTML='✅ Done: <b>'+s.slug+'</b> — see Library to preview & publish.';
   loadProjects();}
  else if(s.status==='failed'){clearInterval(iv);btn.disabled=false;
   log.innerHTML='❌ '+friendlyError(s.error);}
  else if(s.status==='dubbing'){log.textContent='Dubbing into '+document.getElementById('lang').selectedOptions[0].text+'…';}
 },3000);}
async function loadTrends(){
 const niche=document.getElementById('niche').value;
 const r=await fetch('/api/trends',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({niche,limit:12})});
 const ts=await r.json();
 document.getElementById('trendlist').innerHTML=ts.map((t,i)=>
  `<div class="trend" onclick="useTrend(${JSON.stringify(t.title).replace(/"/g,'&quot;')})">
   <b>${i+1}. ${t.title}</b><br><small>${t.source} · score ${t.score}</small></div>`).join('')
  ||'<p class="log">No trends — check connection.</p>';}
function useTrend(t){document.getElementById('topic').value=t;
 document.querySelector('nav button').click();tab('studio');}
async function loadProjects(){
 const ps=await (await fetch('/api/projects')).json();
 document.getElementById('projlist').innerHTML=ps.map(p=>
  `<div class="proj"><div style="flex:1"><b>${p.slug}</b><br>
   <span class="badge ${p.has_video?'ok':''}">${p.has_video?'🎬 video ready':'📝 in progress'}</span>
   ${p.has_video?` <span class="badge">${Math.round(p.size/1024)}KB</span>`:''}</div>
  ${p.has_video?`<video src="/api/video/${p.slug}" controls preload="none"></video>
   <button class="go" onclick="publish('${p.slug}')">Publish</button>`:''}</div>`).join('')
  ||'<p class="log">No projects yet.</p>';}
async function publish(slug){
 document.querySelectorAll('nav button')[3].click();tab('publish');
 document.getElementById('pubslug').value=slug;}
let _plats=[];
async function loadPublish(){
 const ps=await (await fetch('/api/projects')).json();
 const vids=ps.filter(p=>p.has_video);
 document.getElementById('pubslug').innerHTML=vids.map(p=>
  `<option value="${p.slug}">${p.slug}</option>`).join('')
  ||'<option value="">— no videos yet —</option>';
 _plats=await (await fetch('/api/platforms')).json();
 const grid=document.getElementById('platgrid');
 grid.innerHTML=_plats.map(p=>{
  const dis=p.state==='needs_setup';
  const badge=p.state==='ready'?'✅ Ready':p.state==='needs_auth'?'🔑 Needs one-time auth':'⚠️ Needs setup';
  return `<div class="pcard ${dis?'off':'sel'}" id="pc-${p.platform}"
   ${dis?'':`onclick="togglePlat('${p.platform}')"`}>
   <h4>${dis?'':`<input type="checkbox" checked onclick="event.stopPropagation();togglePlat('${p.platform}')">`}${p.label}</h4>
   <span class="st ${p.state}">${badge}</span><br>
   <small>${p.state==='ready'?'Good to go.'
    :p.state==='needs_auth'?p.hint
    :'Missing: '+p.missing.join(', ')+'. See docs/setup-'+p.platform+'.md'}</small></div>`;}).join('');
 document.getElementById('setuphelp').innerHTML=_plats.filter(p=>p.state==='needs_setup').map(p=>
  `<p class="log"><b>${p.label}</b> — set ${p.missing.join(', ')} in your environment or .env, then refresh. <b>docs/setup-${p.platform}.md</b> has the 2-minute guide.</p>`).join('')
  ||'<p class="log">✅ Every platform is configured. Pick a video and hit Publish.</p>';
 updateCount();}
function togglePlat(p){
 const card=document.getElementById('pc-'+p);
 card.classList.toggle('sel');
 const cb=card.querySelector('input');if(cb)cb.checked=card.classList.contains('sel');
 updateCount();}
function selPlats(){return _plats.filter(p=>{const c=document.getElementById('pc-'+p.platform);
 return c&&c.classList.contains('sel')&&!c.classList.contains('off');}).map(p=>p.platform);}
function updateCount(){const n=selPlats().length;
 document.getElementById('pubcount').textContent=n?`${n} platform${n>1?'s':''} selected`:'Select at least one platform';
 document.getElementById('pubbtn').disabled=!n||!document.getElementById('pubslug').value;}
async function doPublish(){
 const slug=document.getElementById('pubslug').value;
 const platforms=selPlats();if(!slug||!platforms.length)return;
 const live=document.getElementById('pubmode').value==='live';
 if(live&&!confirm(`Post "${slug}" to ${platforms.join(', ')} FOR REAL?`))return;
 const btn=document.getElementById('pubbtn');btn.disabled=true;
 const box=document.getElementById('pubresults');
 box.innerHTML='<p class="log">Publishing…</p>';
 const r=await fetch('/api/publish',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({slug,platforms,dry_run:!live})});
 const res=await r.json();
 box.innerHTML=res.map(x=>{
  if(x.error)return `<div class="res err">❌ <b>${x.platform}</b> — ${x.error}</div>`;
  const url=x.url?` — <a href="${x.url}" target="_blank">view post ↗</a>`:'';
  const tag=x.dry_run?'🧪 dry run':'✅ posted';
  return `<div class="res ok">${tag} <b>${x.platform}</b>${url}</div>`;}).join('');
 btn.disabled=false;}
let _uploadId=null;
async function doUpload(){
 const f=document.getElementById('upfile').files[0];
 if(!f){alert('Pick a video file first.');return;}
 const box=document.getElementById('upinfo');
 box.innerHTML='Uploading & validating…';
 const fd=new FormData();fd.append('file',f);
 const r=await fetch('/api/upload',{method:'POST',body:fd});
 if(!r.ok){box.innerHTML='❌ '+(await r.text());return;}
 const info=await r.json();
 _uploadId=info.upload_id;
 const warns=(info.warnings||[]).map(w=>`<div>⚠️ ${w}</div>`).join('');
 box.innerHTML=`✅ <b>${info.width}x${info.height}</b> ${info.fps}fps, ${info.duration_str}, ${info.size_mb}MB ${warns}`;
 document.getElementById('upform').style.display='block';
 const t=document.getElementById('uptitle');
 if(!t.value)t.value=f.name.replace(/[.][^.]+$/,'').replace(/[-_]+/g,' ');}
async function doCaption(){
 if(!_uploadId)return;
 const box=document.getElementById('upresults');
 box.innerHTML='<p class="log">✨ AI is watching your video…</p>';
 const r=await fetch('/api/suggest-caption',{method:'POST',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({upload_id:_uploadId})});
 if(!r.ok){box.innerHTML='❌ '+(await r.text());return;}
 const c=await r.json();
 document.getElementById('uptitle').value=c.title||'';
 document.getElementById('updesc').value=c.description||'';
 document.getElementById('uptags').value=(c.hashtags||[]).join(', ');
 box.innerHTML='<p class="log">✅ Caption filled in — edit anything you like.</p>';}
async function doPostVideo(scheduleIt){
 if(!_uploadId)return;
 const platforms=selPlats();if(!platforms.length){alert('Select at least one platform above.');return;}
 const live=document.getElementById('pubmode').value==='live';
 const at=document.getElementById('upat').value.trim();
 if(scheduleIt&&!at){alert('Type when to post, e.g. "Friday 6pm".');return;}
 if(!scheduleIt&&live&&!confirm(`Post your video to ${platforms.join(', ')} FOR REAL?`))return;
 const box=document.getElementById('upresults');
 box.innerHTML='<p class="log">'+(scheduleIt?'Scheduling…':'Publishing…')+'</p>';
 const r=await fetch('/api/post-video',{method:'POST',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({upload_id:_uploadId,
   title:document.getElementById('uptitle').value,
   description:document.getElementById('updesc').value,
   hashtags:document.getElementById('uptags').value.split(',').map(s=>s.trim()).filter(Boolean),
   platforms,dry_run:!live,at:scheduleIt?at:null})});
 const res=await r.json();
 if(res.scheduled){box.innerHTML=`✅ <b>Scheduled</b> for ${res.run_at} (job #${res.job_id}). Run <b>shortsmash worker</b> or leave the Studio running.`;return;}
 box.innerHTML=(res.results||[]).map(x=>{
  if(x.error)return `<div class="res err">❌ <b>${x.platform}</b> — ${x.error}</div>`;
  const url=x.url?` — <a href="${x.url}" target="_blank">view post ↗</a>`:'';
  const tag=x.dry_run?'🧪 dry run':'✅ posted';
  return `<div class="res ok">${tag} <b>${x.platform}</b>${url}</div>`;}).join('');}
async function loadStats(){
 const s=await (await fetch('/api/stats')).json();
 document.getElementById('statlist').innerHTML=
  `<div class="stat"><b>${s.projects}</b><span>projects</span></div>
   <div class="stat"><b>${s.videos}</b><span>videos rendered</span></div>
   <div class="stat"><b>${s.total_mb}</b><span>MB of video</span></div>
   <div class="stat"><b>${s.platforms.length}</b><span>platforms</span></div>
   <p class="log" style="margin-top:14px">Platforms: ${s.platforms.join(', ')}</p>`;}
renderTemplates();
async function loadLanguages(){
 try{
  const r=await fetch('/api/languages');const j=await r.json();
  const sel=document.getElementById('lang');
  j.languages.forEach(l=>{
   const o=document.createElement('option');o.value=l.code;o.textContent=l.name;sel.appendChild(o);});
 }catch(e){}
}
loadLanguages();
async function loadApiStatus(){
 try{
  const r=await fetch('/api/status');const j=await r.json();
  document.getElementById('apistat').innerHTML=j.services.map(s=>
   `<span class="badge ${s.ok?'ok':''}" title="${s.hint}">${s.ok?'✅':'⚪'} ${s.name}</span>`).join('');
 }catch(e){}
}
loadApiStatus();
async function loadAuto(){
 try{
  const r=await fetch('/api/auto');const j=await r.json();
  document.getElementById('auto_niche').value=j.niche||'';
  document.getElementById('auto_perday').value=j.per_day||1;
  document.getElementById('auto_time').value=j.post_time||'18:00';
  document.getElementById('auto_lang').value=j.language||'en';
  document.getElementById('autobtn').textContent=j.enabled?'Update autopilot 🤖':'Enable autopilot 🤖';
  document.getElementById('autoqueuestatus').textContent=
   (j.enabled?'🟢 Autopilot ON':'⚪ Autopilot off')+' — '+j.queued+' jobs queued';
  // platform checkboxes
  const pr=await fetch('/api/platforms');const pj=await pr.json();
  const sel=new Set(j.platforms||[]);
  document.getElementById('auto_plats').innerHTML=pj.platforms.map(p=>
   `<label class="badge ${sel.has(p.key)?'ok':''}" style="cursor:pointer">
    <input type="checkbox" data-plat="${p.key}" ${sel.has(p.key)?'checked':''} style="width:auto;margin-right:6px">${p.label}</label>`).join('');
  // language options
  const lr=await fetch('/api/languages');const lj=await lr.json();
  const langsel=document.getElementById('auto_lang');
  if(langsel.options.length<=1)lj.languages.forEach(l=>{
   const o=document.createElement('option');o.value=l.code;o.textContent=l.name;langsel.appendChild(o);});
  langsel.value=j.language||'en';
 }catch(e){}
}
async function saveAuto(){
 const plats=[...document.querySelectorAll('#auto_plats input:checked')].map(i=>i.dataset.plat);
 const body={enabled:true,niche:document.getElementById('auto_niche').value,
  per_day:parseInt(document.getElementById('auto_perday').value)||1,
  platforms:plats,language:document.getElementById('auto_lang').value,
  post_time:document.getElementById('auto_time').value||'18:00'};
 if(!body.niche.trim()){document.getElementById('automsg').textContent='❌ Enter a niche first.';return;}
 const r=await fetch('/api/auto',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const j=await r.json();
 document.getElementById('automsg').textContent=j.ok?('✅ Autopilot on — '+j.queued+' videos queued today.'):'❌ Failed';
 loadAuto();
}
async function loadLimits(){
 try{
  const r=await fetch('/api/limits');const j=await r.json();
  document.getElementById('limsummary').innerHTML=
   `🎬 You can still generate <b>${j.videos_possible} videos</b> today within free limits.`;
  const groups={};
  j.apis.forEach(a=>{(groups[a.group]=groups[a.group]||[]).push(a);});
  document.getElementById('limlist').innerHTML=Object.entries(groups).map(([g,apis])=>
   `<h4>${g==='generation'?'🎬 Video generation':'📢 Publishing'}</h4>`+
   apis.map(a=>{
    const bar=a.pct>=90?'var(--red)':a.pct>=60?'var(--acc)':'var(--grn)';
    return `<div style="margin-bottom:10px"><div style="display:flex;justify-content:space-between;font-size:13px">
     <span><b>${a.label}</b> <span class="log">${a.used}/${a.limit} today</span></span>
     <a href="#" onclick="tab('settings');return false" style="color:var(--acc2);font-size:12px">get key →</a></div>
    <div style="height:6px;background:#1a2130;border-radius:3px;margin-top:4px">
     <div style="height:100%;width:${a.pct}%;background:${bar};border-radius:3px"></div></div>
    <p class="log" style="margin:2px 0 0;font-size:11.5px">${a.why}</p></div>`;}).join('')).join('');
 }catch(e){}
}
async function loadSettings(){
 const r=await fetch('/api/settings');const j=await r.json();
 document.getElementById('keyform').innerHTML=j.groups.map(g=>
  `<h4 style="margin:18px 0 4px">${g.name} <span class="sub">${g.desc}</span></h4>`+
  g.keys.map(k=>{
   const guide=k.guide&&k.guide.length?`<details style="margin-top:6px"><summary style="cursor:pointer;color:var(--acc2);font-size:12.5px;font-weight:600">How to connect →</summary><ol class="log" style="margin:8px 0 0;padding-left:20px;font-size:12.5px">${k.guide.map(s=>`<li style="margin-bottom:4px">${s}</li>`).join('')}</ol></details>`:'';
   return `<div style="margin-bottom:12px"><label>${k.label} ${k.set?'<span class="badge ok">set</span>':'<span class="badge">not set</span>'}</label>
   <input type="password" id="key_${k.key}" placeholder="${k.set?'•••••• (enter new to replace)':'Paste key here'}" autocomplete="off">
   <p class="log" style="margin:4px 0 0">${k.hint}</p>${guide}</div>`;}).join('')).join('');
}
async function saveSettings(){
 const keys={};
 document.querySelectorAll('#keyform input').forEach(i=>{
  if(i.value.trim()) keys[i.id.replace('key_','')]=i.value.trim();});
 const r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({keys})});
 const j=await r.json();
 document.getElementById('savemsg').textContent=j.ok?('✅ Saved ('+j.saved.length+' keys). Restart the server to apply everywhere.'):'❌ Save failed';
 loadSettings();loadApiStatus();
}
async function testVoice(){
 const lang=document.getElementById('vtestlang').value;
 const msg=document.getElementById('voicemsg');const au=document.getElementById('vaudio');
 msg.textContent='Generating sample…';au.style.display='none';
 try{
  const r=await fetch('/api/voice-sample?lang='+lang);
  if(!r.ok) throw new Error('failed');
  const blob=await r.blob();au.src=URL.createObjectURL(blob);
  au.style.display='block';au.play();msg.textContent='🔊 Playing '+lang+' sample — this is the actual voice quality.';
 }catch(e){msg.textContent='❌ Could not generate sample. Check your connection.';}
}
</script></body></html>
"""


def main(host: str = "127.0.0.1", port: int = 8000):
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
