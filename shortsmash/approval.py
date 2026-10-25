"""Approval calendar/queue for ShortSmash Social 0.4.

Stored as JSON under ~/.shortsmash/social/queue.json. This is separate
from the existing SQLite publish queue in schedule.py, which is for
rendered-video jobs; the social queue is for text/image/short drafts.

Hard rule: publish_dry_run() refuses any draft not in approved or
scheduled status, and is idempotent by idempotency key. Real live
publishing is NOT wired up in this build - publish is dry-run only.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path

from .config import HOME, ensure_home


def queue_path(base: Path | None = None) -> Path:
    d = Path(base) if base is not None else HOME / "social"
    d.mkdir(parents=True, exist_ok=True)
    return d / "queue.json"


def _load(base: Path | None = None) -> dict:
    p = queue_path(base)
    if not p.exists():
        return {"packs": [], "publish_log": []}
    try:
        data = json.loads(p.read_text())
    except Exception:
        return {"packs": [], "publish_log": []}
    data.setdefault("packs", [])
    data.setdefault("publish_log", [])
    return data


def _save(data: dict, base: Path | None = None) -> None:
    p = queue_path(base)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(p)


def create_pack(topic: str, drafts: dict | list, media: dict | None = None,
                base: Path | None = None, source: str = "prompt") -> dict:
    """Create a pack. ``drafts`` may be the posts.generate_drafts() dict
    (platform -> list) or a flat list of draft dicts."""
    pack_id = uuid.uuid4().hex[:12]
    flat: list[dict] = []
    if isinstance(drafts, dict):
        # posts module returns {"platforms": {platform: [drafts]}}
        plat_map = drafts.get("platforms", drafts) if "platforms" in drafts else drafts
        for platform, items in plat_map.items():
            for item in items:
                d = dict(item)
                d.setdefault("platform", platform)
                flat.append(d)
    else:
        flat = [dict(d) for d in drafts]
    for d in flat:
        d.setdefault("id", uuid.uuid4().hex[:8])
        d.setdefault("status", "draft")
        d.setdefault("format", d.get("format", "text"))
    pack = {"id": pack_id, "topic": topic, "source": source,
            "created_at": datetime.now().isoformat(),
            "media": media or {}, "drafts": flat}
    data = _load(base)
    data["packs"].append(pack)
    _save(data, base)
    return pack


def list_packs(base: Path | None = None) -> list[dict]:
    return _load(base)["packs"]


def get_pack(pack_id: str, base: Path | None = None) -> dict:
    for pack in _load(base)["packs"]:
        if pack["id"] == pack_id:
            return pack
    raise KeyError(f"Pack not found: {pack_id}")


def _set_status(pack_id: str, status: str, platform: str | None,
                base: Path | None = None, scheduled_at: str | None = None) -> dict:
    data = _load(base)
    for pack in data["packs"]:
        if pack["id"] != pack_id:
            continue
        changed = 0
        for d in pack["drafts"]:
            if platform is None or d.get("platform") == platform:
                d["status"] = status
                if scheduled_at:
                    d["scheduled_at"] = scheduled_at
                changed += 1
        _save(data, base)
        if not changed:
            raise KeyError(f"No draft for platform {platform!r} in pack {pack_id}")
        return pack
    raise KeyError(f"Pack not found: {pack_id}")


def approve_pack(pack_id: str, platform: str | None = None,
                 base: Path | None = None) -> dict:
    return _set_status(pack_id, "approved", platform, base)


def reject_pack(pack_id: str, platform: str | None = None,
                base: Path | None = None) -> dict:
    return _set_status(pack_id, "rejected", platform, base)


def schedule_pack(pack_id: str, at: str, platform: str | None = None,
                  base: Path | None = None) -> dict:
    pack = get_pack(pack_id, base)
    targets = [d for d in pack["drafts"]
               if platform is None or d.get("platform") == platform]
    if not targets:
        raise KeyError(f"No draft for platform {platform!r} in pack {pack_id}")
    if any(d.get("status") not in ("approved", "scheduled") for d in targets):
        raise PermissionError(
            "Only approved drafts can be scheduled. Approve first.")
    return _set_status(pack_id, "scheduled", platform, base, scheduled_at=at)


def edit_draft(pack_id: str, draft_id: str, new_text: str,
               base: Path | None = None) -> dict:
    data = _load(base)
    for pack in data["packs"]:
        if pack["id"] != pack_id:
            continue
        for d in pack["drafts"]:
            if d.get("id") == draft_id:
                d["text"] = new_text
                d["status"] = "edited"
                _save(data, base)
                return pack
    raise KeyError(f"Draft not found: {draft_id} in pack {pack_id}")


def publish_dry_run(pack_id: str, platform: str,
                    base: Path | None = None) -> dict:
    """Dry-run publish only. Idempotent: a second call with the same
    pack+platform returns the original log entry and adds nothing."""
    data = _load(base)
    pack = None
    for p in data["packs"]:
        if p["id"] == pack_id:
            pack = p
            break
    if pack is None:
        raise KeyError(f"Pack not found: {pack_id}")
    drafts = [d for d in pack["drafts"] if d.get("platform") == platform]
    if not drafts:
        raise KeyError(f"No draft for platform {platform!r} in pack {pack_id}")
    draft = drafts[0]
    key = hashlib.sha256(f"{pack_id}:{platform}:{draft.get('id', '')}".encode()).hexdigest()[:24]
    for entry in data["publish_log"]:
        if entry.get("idempotency_key") == key:
            return {**entry, "duplicate": True}
    if draft.get("status") not in ("approved", "scheduled"):
        raise PermissionError(
            f"Refused: draft for {platform} is {draft.get('status')!r}, "
            "not approved. Approve it first. No live publish was attempted.")
    entry = {"idempotency_key": key, "pack_id": pack_id,
             "draft_id": draft.get("id"), "platform": platform,
             "text": draft.get("text", ""), "media": draft.get("media", ""),
             "timestamp": datetime.now().isoformat(),
             "dry_run": True, "live": False, "duplicate": False}
    data["publish_log"].append(entry)
    draft["status"] = "published_dry_run"
    _save(data, base)
    return entry


def publish_log(base: Path | None = None) -> list[dict]:
    return _load(base)["publish_log"]
