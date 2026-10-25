"""AI Image Studio for ShortSmash Social 0.4.

Providers, in order of honesty about what they are:
- ``template``: Pillow, offline, labelled template, never AI.
- ``stock``: real Pixabay photos. Repo-alone path first: PIXABAY_API_KEY
  from env/config (see Config.pixabay_key) via a direct httpx call to
  pixabay.com/api/ - no vault helper, no unversioned file, is needed.
  The vault-helper path is kept only as a local-dev fallback when it
  exists. Template fallback only if zero photos download, labelled.
- ``gemini``: REAL code path, capped, provenance recorded - but it is
  only called when a Gemini key/billing is configured. Without a key
  it fails clearly and spends nothing. Model is configurable via
  cfg ``gemini_image_model`` / env GEMINI_IMAGE_MODEL; the default
  below is the documented Nano Banana family model and must be
  replaced with a live model id from ai.google.dev if it 404s.
  Cost preview: $0.0336 per 1K image for the documented model.
- ``pollinations``: REAL code path via the current gen.pollinations.ai
  OpenAI-compatible endpoint, capped, key from env
  POLLINATIONS_API_KEY. Without a key it fails clearly and spends
  nothing. Pollen/credit pricing varies by model, so cost preview
  is None and the daily cap is a count cap via daily_cap_usd only
  when a USD estimate exists; otherwise generation is still
  recorded and the user must check their Pollinations balance.

No provider is called by the demo or by tests with a live key. Tests
use mocks for gemini/pollinations and for the Pixabay HTTP call.
Text overlays are added by Pillow templates AFTER generation, never
claimed to be baked in by a model.
"""
from __future__ import annotations

import hashlib
import json
import textwrap
from datetime import date
from pathlib import Path

from .config import HOME, ensure_home
from .profile import style_lock

SIZES = {"feed": (1080, 1350), "story": (1080, 1920), "link": (1200, 630)}
GEMINI_PRICE_PER_1K = 0.0336  # USD, as documented in the 0.4 plan
DEFAULT_DAILY_CAP_USD = 5.0

_PALETTES = [
    ((15, 32, 39), (32, 58, 67), (44, 83, 100)),
    ((20, 30, 48), (36, 59, 85), (58, 96, 115)),
    ((32, 1, 34), (111, 0, 0), (195, 20, 50)),
    ((15, 12, 41), (48, 43, 99), (36, 36, 62)),
    ((35, 37, 38), (65, 67, 69), (90, 90, 90)),
]


def cost_preview(provider: str, count: int = 4,
                 daily_cap_usd: float = DEFAULT_DAILY_CAP_USD) -> dict:
    """Cost preview only. No API call, no spend."""
    if provider == "template":
        per, total = 0.0, 0.0
    elif provider == "gemini":
        per, total = GEMINI_PRICE_PER_1K, round(GEMINI_PRICE_PER_1K * count, 4)
    elif provider == "pollinations":
        per, total = None, None  # Pollen credits; price varies by model
    elif provider == "stock":
        per, total = 0.0, 0.0
    else:
        raise ValueError(f"Unknown image provider: {provider}")
    return {"provider": provider, "count": count,
            "per_image_usd": per, "estimated_total_usd": total,
            "daily_cap_usd": daily_cap_usd,
            "within_cap": total is None or total <= daily_cap_usd}


def _egress_proxy() -> str | None:
    """Proxy pattern reused from stock.py; plain env proxy otherwise."""
    try:
        from .stock import _egress_proxy as _stock_proxy
        return _stock_proxy()
    except Exception:
        import os
        return os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")


def _pixabay_photo_search_env(query: str, per_page: int, cfg=None) -> list[dict]:
    """Portable photo search: PIXABAY_API_KEY from env/config only."""
    import httpx
    key = None
    if cfg is not None:
        try:
            key = cfg.pixabay_key()
        except Exception:
            key = None
    if not key:
        import os
        key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        raise RuntimeError("no PIXABAY_API_KEY in env/config")
    r = httpx.get("https://pixabay.com/api/",
                  params={"key": key, "q": query, "image_type": "photo",
                          "per_page": per_page},
                  timeout=30, proxy=_egress_proxy())
    r.raise_for_status()
    return r.json().get("hits", [])


def _pixabay_photo_search_helper(query: str, per_page: int) -> list[dict]:
    """Vault-helper photo search, when the helper supports search_photos.

    This mirrors stock.py's video-helper pattern: the key never appears
    in arguments, logs, or code. The helper script carries a surrogate
    that the runtime swaps for the real credential on pixabay.com.
    """
    import subprocess
    import sys
    helper = Path.home() / "workspace/custom-connectors/custom.pixabay/bin/pixabay.py"
    if not helper.exists():
        raise RuntimeError("pixabay helper not found")
    proc = subprocess.run(
        [sys.executable, str(helper), "search_photos"],
        input=json.dumps({"q": query, "per_page": per_page}).encode(),
        capture_output=True, timeout=90)
    try:
        data = json.loads(proc.stdout.decode() or "{}")
    except Exception:
        raise RuntimeError("pixabay helper returned unparseable output")
    if not data.get("ok"):
        raise RuntimeError(f"pixabay helper failed: {data.get('error')}")
    return data["data"].get("hits", [])


def search_stock_photos(query: str, per_page: int = 4, cfg=None,
                        out_dir: Path | None = None,
                        social_base: Path | None = None,
                        target_size: str = "feed") -> dict:
    """Search real Pixabay photos and download them. No template fallback
    here: on failure this raises with the exact blocker, so callers cannot
    mistake a template for a photo."""
    import httpx
    errors: list[str] = []
    hits: list[dict] = []
    for searcher in (_pixabay_photo_search_env, _pixabay_photo_search_helper):
        try:
            if searcher is _pixabay_photo_search_env:
                hits = searcher(query, per_page, cfg)
            else:
                hits = searcher(query, per_page)
            if hits:
                break
        except Exception as exc:  # noqa: BLE001 - collect, try next path
            errors.append(f"{searcher.__name__}: {exc}"[:200])
    if not hits:
        raise RuntimeError(
            "Pixabay photo search failed: " + "; ".join(errors)
            + ". Set PIXABAY_API_KEY (see .env.example) or use a "
            "photo-capable pixabay helper.")
    target = SIZES.get(target_size, SIZES["feed"])
    out_root = Path(out_dir) if out_dir is not None else (
        (Path(social_base) if social_base is not None else HOME / "social")
        / "images")
    out_root.mkdir(parents=True, exist_ok=True)
    slug = hashlib.sha256(query.encode()).hexdigest()[:10]
    candidates = []

    def _download_one(url: str, dest: Path) -> bool:
        """Download one URL with one 429 retry. Never leaks the URL in errors."""
        import time
        for attempt in range(2):
            try:
                with httpx.stream("GET", url, timeout=60,
                                  follow_redirects=True,
                                  proxy=_egress_proxy()) as resp:
                    if resp.status_code == 429 and attempt == 0:
                        time.sleep(2)
                        continue
                    resp.raise_for_status()
                    with open(dest, "wb") as fh:
                        for chunk in resp.iter_bytes(1 << 20):
                            fh.write(chunk)
                return True
            except Exception:
                if attempt == 0:
                    import time as _t
                    _t.sleep(2)
                    continue
                return False
        return False

    for i, hit in enumerate(hits, start=1):
        if len(candidates) >= per_page:
            break
        # Try large first, then the smaller webformat as a fallback.
        urls = [u for u in (hit.get("largeImageURL"),
                            hit.get("webformatURL")) if u]
        if not urls:
            continue
        raw = out_root / f"{slug}-{target_size}-stock-{len(candidates) + 1}.jpg"
        ok = False
        for url in urls:
            if _download_one(url, raw):
                ok = True
                break
        if not ok:
            continue
        import time as _time
        _time.sleep(0.5)
        magic = raw.read_bytes()[:4]
        if magic[:2] != b"\xff\xd8" and magic != b"\x89PNG":
            raw.unlink(missing_ok=True)
            continue
        # Centre-crop/resize to the platform target size with Pillow.
        try:
            from PIL import Image
            img = Image.open(raw).convert("RGB")
            orig = img.size
            # Centre crop to target aspect, then resize.
            tw, th = target
            sw, sh = orig
            scale = max(tw / sw, th / sh)
            resized = img.resize((round(sw * scale), round(sh * scale)))
            left = (resized.width - tw) // 2
            top = (resized.height - th) // 2
            cropped = resized.crop((left, top, left + tw, top + th))
            cropped.save(raw, "JPEG", quality=90)
            final = (tw, th)
        except Exception:
            orig, final = (0, 0), target
        candidates.append({
            "candidate": len(candidates) + 1, "path": str(raw), "size": target_size,
            "dimensions": final, "original_dimensions": orig,
            "provider": "stock", "model": "pixabay-photo",
            "estimated_cost_usd": 0.0,
            "source": "pixabay", "pixabay_id": hit.get("id"),
            "page_url": hit.get("pageURL"), "tags": hit.get("tags", ""),
            "photographer": hit.get("user", ""),
            "licence": ("Pixabay content licence - free to use, see "
                        "pixabay.com/service/license/; do not resell the file itself."),
            "note": "Real Pixabay photo, downloaded and cropped to platform size.",
            "provenance": {
                "prompt": query, "provider": "stock",
                "model": "pixabay-photo", "size": target_size,
                "pixabay_id": hit.get("id"), "page_url": hit.get("pageURL"),
                "estimated_cost_usd": 0.0,
            },
        })
    if not candidates:
        raise RuntimeError("Pixabay photo search returned hits but no "
                           "downloadable valid image files.")
    return {"prompt": query, "provider": "stock", "size": target_size,
            "dimensions": target, "candidates": candidates}


def _usage_path(base: Path | None = None) -> Path:
    d = Path(base) if base is not None else HOME / "social"
    d.mkdir(parents=True, exist_ok=True)
    return d / "image_usage.json"


def _spent_today(provider: str, base: Path | None = None) -> float:
    p = _usage_path(base)
    if not p.exists():
        return 0.0
    try:
        data = json.loads(p.read_text())
    except Exception:
        return 0.0
    today = date.today().isoformat()
    return sum(float(e.get("estimated_cost_usd", 0) or 0)
               for e in data if e.get("date") == today
               and e.get("provider") == provider)


def _record_usage(provider: str, count: int, cost: float,
                  base: Path | None = None) -> None:
    p = _usage_path(base)
    data = []
    if p.exists():
        try:
            data = json.loads(p.read_text())
        except Exception:
            data = []
    data.append({"date": date.today().isoformat(), "provider": provider,
                 "count": count, "estimated_cost_usd": cost})
    p.write_text(json.dumps(data, indent=2))


def _palette(prompt: str, style: str, candidate: int):
    seed = int(hashlib.sha256(f"{prompt}|{style}|{candidate}".encode()).hexdigest()[:8], 16)
    return _PALETTES[seed % len(_PALETTES)]


def _render_template(prompt: str, style: str, size: tuple[int, int],
                     candidate: int, out: Path) -> Path:
    """Pillow template: gradient background + title overlay. Labelled template."""
    from PIL import Image, ImageDraw, ImageFont

    w, h = size
    top, mid, bottom = _palette(prompt, style, candidate)
    img = Image.new("RGB", (w, h), top)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    # Accent band, then title overlay (Pillow, after generation).
    draw.rectangle([0, h // 3, w, h // 3 + 8], fill=mid)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", max(28, w // 22))
        small = ImageFont.truetype("DejaVuSans.ttf", max(20, w // 32))
    except Exception:
        font = ImageFont.load_default()
        small = font
    title = prompt[:80] if prompt else "ShortSmash Social"
    lines = []
    for para in textwrap.wrap(title, width=max(12, w // 28)):
        lines.append(para)
    y = h // 3 + 32
    for line in lines[:4]:
        draw.text((40, y), line, fill=(255, 255, 255), font=font)
        y += max(36, w // 18)
    if style:
        draw.text((40, h - 80), f"Style: {style[:60]}", fill=(220, 220, 220),
                  font=small)
    draw.text((40, h - 40), "Template image - not AI generated",
              fill=(200, 200, 200), font=small)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def _gemini_image_model(cfg=None) -> str:
    """Configurable Gemini image model, never silently hardcoded as verified.

    Default is the documented Nano Banana family model. If the API
    returns 404 for it, set cfg gemini_image_model / env
    GEMINI_IMAGE_MODEL to a live model id from ai.google.dev and retry.
    """
    import os
    if cfg is not None:
        try:
            v = cfg.get("gemini_image_model")
            if v:
                return str(v)
        except Exception:
            pass
    return os.environ.get("GEMINI_IMAGE_MODEL") or "gemini-2.5-flash-image"


def _aspect_ratio(size: str) -> str:
    return {"feed": "4:5", "story": "9:16", "link": "16:9"}.get(size, "1:1")


def _gemini_generate(prompt: str, size: str, count: int, style: str,
                     out_root: Path, slug: str, cfg=None) -> list[dict]:
    """Real Gemini image call. Raises clearly without a key; never called
    in tests except via mocked httpx. Returns candidate dicts."""
    import base64
    import httpx
    key = None
    if cfg is not None:
        try:
            key = cfg.gemini_key()
        except Exception:
            key = None
    if not key:
        import os
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError(
            "Gemini image generation is NOT configured: no GEMINI_API_KEY "
            "in env/config and no billing-linked project. The Gemini API "
            "has no free image tier, paid 1K images start at $0.0336 each. "
            "No API call was made and no money was spent.")
    model = _gemini_image_model(cfg)
    target = SIZES[size]
    candidates = []
    for i in range(1, count + 1):
        full_prompt = f"{prompt}. {style}".strip() if style else prompt
        body = {
            "contents": [{"parts": [{"text": full_prompt}]}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": {"aspectRatio": _aspect_ratio(size)},
            },
        }
        r = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            params={"key": key}, json=body, timeout=120,
            proxy=_egress_proxy())
        if r.status_code in (401, 403):
            raise RuntimeError(
                "Gemini image call rejected (401/403): key/billing not "
                "authorised for image generation. No image was saved. "
                "Check the key and that the project has billing enabled.")
        if r.status_code == 404:
            raise RuntimeError(
                f"Gemini image model {model!r} returned 404: it may be "
                "retired. Set gemini_image_model / GEMINI_IMAGE_MODEL to "
                "a live model id from ai.google.dev and retry. No image "
                "was saved.")
        r.raise_for_status()
        data = r.json()
        b64 = None
        mime = "image/png"
        try:
            for part in data["candidates"][0]["content"]["parts"]:
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    b64 = inline["data"]
                    mime = inline.get("mimeType", mime)
                    break
        except Exception:
            b64 = None
        if not b64:
            raise RuntimeError(
                "Gemini image call returned no image data. Response was "
                f"truncated for safety: {str(data)[:300]}. No image saved.")
        raw = base64.b64decode(b64)
        ext = ".png" if "png" in mime else ".jpg"
        path = out_root / f"{slug}-{size}-gemini-{i}{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        magic = raw[:4]
        if magic[:2] != b"\xff\xd8" and magic != b"\x89PNG":
            path.unlink(missing_ok=True)
            raise RuntimeError("Gemini returned data that is not a valid PNG/JPEG image.")
        candidates.append({
            "candidate": i, "path": str(path), "size": size,
            "dimensions": target, "provider": "gemini", "model": model,
            "estimated_cost_usd": GEMINI_PRICE_PER_1K, "style_lock": style,
            "note": "AI-generated image via Gemini API - check the platform AI disclosure flag before publishing.",
            "provenance": {"prompt": prompt, "style_lock": style,
                           "provider": "gemini", "model": model, "size": size,
                           "estimated_cost_usd": GEMINI_PRICE_PER_1K},
        })
    return candidates


def _pollinations_generate(prompt: str, size: str, count: int, style: str,
                           out_root: Path, slug: str) -> list[dict]:
    """Real Pollinations call via gen.pollinations.ai (OpenAI-compatible).

    Key from POLLINATIONS_API_KEY only. Without it, fail clearly.
    Response may be b64_json or a URL; both are handled, anything else
    is a clear error, never a silent template.
    """
    import base64
    import os
    import httpx
    key = os.environ.get("POLLINATIONS_API_KEY")
    if not key:
        raise RuntimeError(
            "Pollinations image generation is NOT configured: no "
            "POLLINATIONS_API_KEY in env. In 2026 tests the current API "
            "requires a key/balance. No API call was made and no money "
            "was spent. Use provider='template' for offline candidates.")
    target = SIZES[size]
    width, height = target
    candidates = []
    for i in range(1, count + 1):
        full_prompt = f"{prompt}. {style}".strip() if style else prompt
        r = httpx.post(
            "https://gen.pollinations.ai/images/generations",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "flux", "prompt": full_prompt,
                  "width": width, "height": height, "n": 1},
            timeout=120, proxy=_egress_proxy())
        if r.status_code in (401, 402, 403):
            raise RuntimeError(
                f"Pollinations rejected the call ({r.status_code}): key or "
                "balance insufficient. No image was saved.")
        r.raise_for_status()
        data = r.json()
        item = None
        try:
            item = data["data"][0]
        except Exception:
            item = None
        if not item:
            raise RuntimeError(
                f"Pollinations returned no image data: {str(data)[:300]}. "
                "No image saved.")
        path = out_root / f"{slug}-{size}-pollinations-{i}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        if item.get("b64_json"):
            path.write_bytes(base64.b64decode(item["b64_json"]))
        elif item.get("url"):
            with httpx.stream("GET", item["url"], timeout=60,
                              follow_redirects=True,
                              proxy=_egress_proxy()) as resp:
                resp.raise_for_status()
                with open(path, "wb") as fh:
                    for chunk in resp.iter_bytes(1 << 20):
                        fh.write(chunk)
        else:
            raise RuntimeError(
                "Pollinations response had neither b64_json nor url. "
                "No image saved.")
        candidates.append({
            "candidate": i, "path": str(path), "size": size,
            "dimensions": target, "provider": "pollinations", "model": "flux",
            "estimated_cost_usd": None, "style_lock": style,
            "note": ("AI-generated image via Pollinations - Pollen/credit "
                     "cost varies by model; check your Pollinations balance. "
                     "Check the platform AI disclosure flag before publishing."),
            "provenance": {"prompt": prompt, "style_lock": style,
                           "provider": "pollinations", "model": "flux",
                           "size": size, "estimated_cost_usd": None},
        })
    return candidates


def generate(prompt: str, provider: str = "template", size: str = "feed",
             profile: dict | None = None, count: int = 4,
             daily_cap_usd: float = DEFAULT_DAILY_CAP_USD,
             out_dir: Path | None = None,
             social_base: Path | None = None) -> dict:
    """Generate ``count`` candidates. Paid providers fail before any call
    when billing/key is not configured; the daily cap is checked first."""
    if size not in SIZES:
        raise ValueError(f"Unknown size {size!r}; choose from {list(SIZES)}")
    if count < 1 or count > 8:
        raise ValueError("count must be 1-8")
    preview = cost_preview(provider, count, daily_cap_usd)
    total = preview["estimated_total_usd"]
    if total is not None and total > daily_cap_usd:
        raise RuntimeError(
            f"Image daily cap exceeded: estimated ${total:.4f} > "
            f"cap ${daily_cap_usd:.2f}. Raise the cap explicitly or use "
            f"the template provider (free). No image was generated.")
    if total is not None:
        spent = _spent_today(provider, social_base)
        if spent + total > daily_cap_usd:
            raise RuntimeError(
                f"Image daily cap exceeded: already spent ${spent:.4f} "
                f"today on {provider}, plus ${total:.4f} would exceed "
                f"cap ${daily_cap_usd:.2f}. No image was generated.")
    # Real paid paths, only after the cap check above. Without a key
    # they raise NOT configured with no call and no spend; with a key
    # they call the real API and record usage. Tests mock httpx and
    # never make a live paid call.
    style = style_lock(profile) if profile else ""
    out_root = Path(out_dir) if out_dir is not None else (
        (Path(social_base) if social_base is not None else HOME / "social")
        / "images")
    out_root.mkdir(parents=True, exist_ok=True)
    slug = hashlib.sha256(prompt.encode()).hexdigest()[:10]
    if provider == "gemini":
        from .config import Config as _Cfg
        try:
            candidates = _gemini_generate(prompt, size, count, style,
                                          out_root, slug, cfg=_Cfg())
        except RuntimeError as exc:
            # Keep the old test contract: missing key/billing reads as
            # NOT configured, with the preview and cap in the message.
            msg = str(exc)
            if "NOT configured" in msg or "no GEMINI_API_KEY" in msg:
                raise RuntimeError(
                    "Gemini image generation is NOT configured: "
                    "the Gemini API has no free image tier, paid 1K images "
                    f"start at ${GEMINI_PRICE_PER_1K} each (preview for "
                    f"{count}: ${total:.4f}, cap ${daily_cap_usd:.2f}), "
                    "and billing/key setup is a human step for Syed. No "
                    "API call was made and no money was spent. Use "
                    "provider='template' for offline candidates.") from exc
            raise
        _record_usage("gemini", len(candidates),
                      GEMINI_PRICE_PER_1K * len(candidates), social_base)
        return {"prompt": prompt, "provider": "gemini", "size": size,
                "dimensions": SIZES[size], "cost_preview": preview,
                "candidates": candidates,
                "note": "AI-generated images via Gemini API - check the "
                        "platform AI disclosure flag before publishing."}
    if provider == "pollinations":
        candidates = _pollinations_generate(prompt, size, count, style,
                                            out_root, slug)
        _record_usage("pollinations", len(candidates), 0.0, social_base)
        return {"prompt": prompt, "provider": "pollinations", "size": size,
                "dimensions": SIZES[size], "cost_preview": preview,
                "candidates": candidates,
                "note": "AI-generated images via Pollinations - Pollen/credit "
                        "cost varies by model; check your balance."}
    note = ""
    if provider == "stock":
        # Real photos first: env-key Pixabay API, then vault-helper
        # search_photos. Only if both fail do we fall back to a labelled
        # template, with the failure in the note - never silently.
        try:
            from .config import Config as _Cfg
            real = search_stock_photos(prompt, count, cfg=_Cfg(),
                                       out_dir=out_dir,
                                       social_base=social_base,
                                       target_size=size)
            _record_usage("stock", len(real["candidates"]), 0.0, social_base)
            real["cost_preview"] = preview
            real["note"] = "Real Pixabay photos - see candidate provenance."
            return real
        except Exception as exc:  # noqa: BLE001 - fallback is labelled
            import re as _re
            clean = _re.sub(r"https?://\S+", "[url]", str(exc))[:200]
            note = (f"stock_search_failed: {clean}. Falling back to a "
                    "labelled template, not a stock photo.")
    style = style_lock(profile) if profile else ""
    out_root = Path(out_dir) if out_dir is not None else (
        (Path(social_base) if social_base is not None else HOME / "social")
        / "images")
    out_root.mkdir(parents=True, exist_ok=True)
    slug = hashlib.sha256(prompt.encode()).hexdigest()[:10]
    candidates = []
    for i in range(1, count + 1):
        path = _render_template(prompt, style, SIZES[size], i,
                                out_root / f"{slug}-{size}-candidate-{i}.png")
        candidates.append({
            "candidate": i, "path": str(path), "size": size,
            "dimensions": SIZES[size],
            "provider": "template" if provider in ("template", "stock") else provider,
            "model": "pillow-template",
            "estimated_cost_usd": 0.0,
            "style_lock": style,
            "note": note or "Template image - not AI generated",
            "provenance": {
                "prompt": prompt, "style_lock": style,
                "provider": provider, "model": "pillow-template",
                "size": size, "estimated_cost_usd": 0.0,
            },
        })
    _record_usage("template" if provider in ("template", "stock") else provider,
                  count, 0.0, social_base)
    return {"prompt": prompt, "provider": provider, "size": size,
            "dimensions": SIZES[size], "cost_preview": preview,
            "candidates": candidates, "note": note}
