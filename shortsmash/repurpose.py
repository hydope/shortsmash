"""Repurposer for ShortSmash Social 0.4.

Intake reuses pipeline.stage_upload / validate_video for video files.
Image files are staged with the same timestamped naming pattern.

Clip candidates never invent a transcript or a virality score. If no
transcript is available, the candidate record says so explicitly and the
score is None, with an unscored breakdown for every signal.
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

from .config import UPLOADS_DIR, ensure_home
from . import pipeline

VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def stage_input(src: Path, uploads_dir: Path | None = None) -> dict:
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(f"Input not found: {src}")
    if src.suffix.lower() in VIDEO_SUFFIXES:
        staged = pipeline.stage_upload(src)
        meta = pipeline.validate_video(staged, ["youtube", "tiktok", "instagram"])
        return {"kind": "video", "staged": str(staged), "meta": meta}
    if src.suffix.lower() in IMAGE_SUFFIXES:
        ensure_home()
        dest_dir = Path(uploads_dir) if uploads_dir else UPLOADS_DIR
        dest_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        clean = re.sub(r"[^a-z0-9]+", "-", src.stem.lower()).strip("-")[:40] or "image"
        dest = dest_dir / f"{stamp}-{clean}{src.suffix.lower()}"
        shutil.copy2(src, dest)
        return {"kind": "image", "staged": str(dest), "meta": {}}
    raise ValueError(f"Unsupported input {src.suffix} - use a video or image file.")


def _segments(duration: float) -> list[tuple[float, float]]:
    if duration <= 0:
        return []
    if duration <= 45:
        return [(0.0, duration)]
    third = duration / 3.0
    segs = []
    for i in range(3):
        start = round(i * third, 1)
        end = round(min(duration, start + 30.0), 1)
        if end - start >= 5:
            segs.append((start, end))
    return segs[:3]


def clip_candidates(video: Path, transcript_segments: list[dict] | None = None) -> dict:
    """Top 3 clip candidates. With a transcript, score from word counts and
    question hooks; without one, explicitly unscored - never invented."""
    try:
        meta = pipeline.probe_video(Path(video))
        duration = float(meta.get("duration", 0) or 0)
    except Exception:
        duration = 0.0
    segs = _segments(duration) if duration else []
    candidates = []
    for i, (start, end) in enumerate(segs, start=1):
        if transcript_segments:
            words = []
            for seg in transcript_segments:
                if seg.get("start", 0) >= start and seg.get("start", 0) < end:
                    words.extend(str(seg.get("text", "")).split())
            hook = "question in transcript" if any(
                "?" in str(s.get("text", "")) for s in transcript_segments
                if s.get("start", 0) >= start and s.get("start", 0) < end) else "no question hook found"
            candidates.append({
                "candidate": i, "start": start, "end": end,
                "duration": round(end - start, 1), "scored": True,
                "score": None,  # No single virality score is claimed.
                "score_breakdown": {
                    "hook": hook,
                    "speech_words_in_segment": len(words),
                    "action": "unscored - no action detection in this MVP",
                    "face": "unscored - no face tracking in this MVP",
                    "audio": "unscored - no audio-peak analysis in this MVP",
                },
                "transcript_excerpt": " ".join(words)[:200],
                "note": "Scored only from the supplied transcript segments; "
                        "no virality score is claimed.",
            })
        else:
            candidates.append({
                "candidate": i, "start": start, "end": end,
                "duration": round(end - start, 1), "scored": False,
                "score": None,
                "score_breakdown": {
                    "hook": "unscored - no transcript available",
                    "speech": "unscored - no transcript available",
                    "action": "unscored - no action detection in this MVP",
                    "face": "unscored - no face tracking in this MVP",
                    "audio": "unscored - no audio-peak analysis in this MVP",
                },
                "transcript_excerpt": "",
                "note": "Transcript unavailable: Whisper was not run in this "
                        "MVP, so no transcript was invented.",
            })
    if not candidates:
        return {"duration": duration, "candidates": candidates,
                "note": "No clip candidates: duration unknown or file "
                        "could not be probed. No transcript was invented."}
    return {"duration": duration, "candidates": candidates,
            "note": "Candidates are time segments only, not a virality ranking."}
