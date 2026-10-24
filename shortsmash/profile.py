"""ShortSmash Social profile memory.

Stored under ~/.shortsmash/social/profile.json (never in the repo).
Deleting a profile removes it from future generation only; it does not
retroactively edit already published posts.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import HOME, ensure_home

FIELDS = (
    "name", "handle", "does", "company", "niche", "tone", "audience",
    "language", "palette", "style", "links", "cta",
)


def social_dir(base: Path | None = None) -> Path:
    """Return the social directory. ``base`` is the full social dir in tests."""
    if base is not None:
        return Path(base)
    ensure_home()
    d = HOME / "social"
    d.mkdir(parents=True, exist_ok=True)
    return d


def profile_path(base: Path | None = None) -> Path:
    return social_dir(base) / "profile.json"


def empty_profile() -> dict:
    return {
        "name": "", "handle": "", "does": "", "company": "", "niche": "",
        "tone": "", "audience": "", "language": "en", "palette": "",
        "style": "", "links": "", "cta": "", "do_not_say": [],
        "employer_opt_in": False,
    }


def load_profile(base: Path | None = None) -> dict | None:
    p = profile_path(base)
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    merged = empty_profile()
    merged.update({k: v for k, v in data.items() if k in merged})
    return merged


def save_profile(data: dict, base: Path | None = None) -> dict:
    merged = empty_profile()
    merged.update({k: v for k, v in data.items() if k in merged})
    # Normalise do_not_say to a list of non-empty strings.
    raw = merged.get("do_not_say", [])
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.split(",")]
    merged["do_not_say"] = [str(x).strip() for x in raw if str(x).strip()]
    merged["employer_opt_in"] = bool(merged.get("employer_opt_in", False))
    d = social_dir(base)
    d.mkdir(parents=True, exist_ok=True)
    (d / "profile.json").write_text(json.dumps(merged, indent=2))
    return merged


def set_profile(base: Path | None = None, **fields) -> dict:
    current = load_profile(base) or empty_profile()
    for key, value in fields.items():
        if value is None or key not in current:
            continue
        current[key] = value
    return save_profile(current, base)


def delete_profile(base: Path | None = None) -> bool:
    p = profile_path(base)
    if p.exists():
        p.unlink()
        return True
    return False


def style_lock(profile: dict | None) -> str:
    """The style snapshot embedded in image/post provenance. Empty if none."""
    if not profile:
        return ""
    parts = [profile.get("palette", ""), profile.get("style", ""),
             profile.get("tone", "")]
    return "; ".join(p for p in parts if p)
