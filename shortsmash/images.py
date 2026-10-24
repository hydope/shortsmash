"""AI Image Studio provider interface for ShortSmash Social 0.4.

Offline-safe by default. Only the ``template`` provider (Pillow) and the
``stock`` fallback (template when no stock key is available) generate files
in this build. ``gemini`` and ``pollinations`` are interfaces only: they
return a cost preview and fail clearly without billing/key, and are NEVER
called by the demo or the tests. No money is spent by this module.

Text overlays are added by Pillow templates AFTER generation, never claimed
to be baked in by a model.
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
    if provider == "gemini":
        raise RuntimeError(
            "Gemini image generation is NOT configured in this local build: "
            "the Gemini API has no free image tier, paid 1K images start at "
            f"${GEMINI_PRICE_PER_1K} each (preview for {count}: "
            f"${total:.4f}, cap ${daily_cap_usd:.2f}), and billing/key setup "
            "is a human step for Syed. No API call was made and no money "
            "was spent. Use provider='template' for offline candidates.")
    if provider == "pollinations":
        raise RuntimeError(
            "Pollinations image generation is NOT configured in this local "
            "build: it is experimental, capped, and requires a key/balance "
            "in 2026 tests. No API call was made and no money was spent. "
            "Use provider='template' for offline candidates.")
    style = style_lock(profile) if profile else ""
    out_root = Path(out_dir) if out_dir is not None else (
        (Path(social_base) if social_base is not None else HOME / "social")
        / "images")
    out_root.mkdir(parents=True, exist_ok=True)
    slug = hashlib.sha256(prompt.encode()).hexdigest()[:10]
    candidates = []
    note = ""
    if provider == "stock":
        # stock.py is video-only (Pixabay/Pexels clips). For still images,
        # this build does not pretend a video clip is a photo: fall back
        # to a labelled template unless a real stock photo was downloaded
        # by a future stock-photo path. The note makes that explicit.
        note = ("stock_unavailable_for_still_images: stock.py provides "
                "video clips, not photos, so this candidate is a labelled "
                "template fallback, not a stock photo.")
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
