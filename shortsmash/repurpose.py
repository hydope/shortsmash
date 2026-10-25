"""Repurposer for ShortSmash Social 0.4.

Intake reuses pipeline.stage_upload / validate_video for video files.
Image files are staged with the same timestamped naming pattern.

Clip candidates never invent a transcript or a virality score. If no
transcript is available, the candidate record says so explicitly and the
score is None, with an unscored breakdown for every signal.
With a transcript, candidates get a labelled, weighted score (0-100,
normalised over the signals that were actually scored) with a breakdown
per signal. Face detection is used only if OpenCV is actually installed;
otherwise it is explicitly unscored, never faked.
"""
from __future__ import annotations

import re
import shutil
import subprocess
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


_HOOK_WORDS = ("secret", "why", "how", "stop", "never", "first",
               "revealed", "truth", "mistake", "amazing")
_EMOTION_WORDS = ("wow", "amazing", "incredible", "love", "laugh",
                  "surprise", "catch", "reveal", "unbelievable", "best")


def _to_seconds(value: str) -> float:
    parts = value.strip().split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except Exception:
        return 0.0


def parse_transcript_file(path: Path) -> dict:
    """Parse .srt, timed .txt, or plain .txt transcripts.

    Returns {"segments": [{start, end, text}], "timed": bool}.
    Plain .txt has no timestamps: segments carry start=None, so callers
    must not invent per-segment timing from them.
    """
    text = Path(path).read_text(errors="ignore")
    suffix = Path(path).suffix.lower()
    segments: list[dict] = []
    if suffix == ".srt" or "-->" in text:
        blocks = re.split(r"\n\s*\n", text.strip())
        for block in blocks:
            lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
            if len(lines) < 2:
                continue
            # Timing may be lines[0] (no index) or lines[1] (index first).
            timing_idx = None
            m = None
            for idx, ln in enumerate(lines[:2]):
                found = re.search(
                    r"(\d+:\d+:\d+[,.]\d+)\s*-->\s*(\d+:\d+:\d+[,.]\d+)", ln)
                if found:
                    timing_idx, m = idx, found
                    break
            if not m:
                continue
            body = " ".join(lines[timing_idx + 1:])
            segments.append({
                "start": _to_seconds(m.group(1).replace(",", ".")),
                "end": _to_seconds(m.group(2).replace(",", ".")),
                "text": body})
        return {"segments": segments, "timed": True}
    # Timed .txt: "12.5<TAB>text" or "00:01:05 text" per line.
    timed = []
    untimed = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d+:\d+:\d+|\d+(?:\.\d+)?)\s*[:\t]\s*(.+)$", line)
        if m and (":" in m.group(1) or float(m.group(1)) < 10000):
            timed.append({"start": _to_seconds(m.group(1)),
                          "end": None, "text": m.group(2)})
        else:
            untimed.append(line)
    if timed and not untimed:
        for i, seg in enumerate(timed):
            seg["end"] = timed[i + 1]["start"] if i + 1 < len(timed) else None
        return {"segments": timed, "timed": True}
    if untimed:
        return {"segments": [{"start": None, "end": None,
                              "text": " ".join(untimed)}], "timed": False}
    return {"segments": timed, "timed": True}


def _audio_energy(video: Path, start: float, end: float) -> tuple[int, str, bool]:
    """ffprobe/ffmpeg volumedetect for one segment. 0-10, or unscored."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(end - start),
             "-i", str(video), "-af", "volumedetect", "-vn", "-sn", "-dn",
             "-f", "null", "/dev/null"],
            capture_output=True, text=True, timeout=30)
        out = proc.stderr or ""
        m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)", out)
        if not m:
            return 0, "unscored - no audio energy detected (silent/no audio track)", False
        mean = float(m.group(1))
        if mean > -30:
            return 10, f"mean_volume {mean} dB - loud segment", True
        if mean > -50:
            return 6, f"mean_volume {mean} dB - moderate segment", True
        return 2, f"mean_volume {mean} dB - quiet segment", True
    except Exception:
        return 0, "unscored - ffmpeg volumedetect unavailable/failed", False


def _scene_changes(video: Path, start: float, end: float) -> tuple[int, str, bool]:
    """ffmpeg scene detection for one segment. 0-5, or unscored."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "info", "-ss", str(start), "-t", str(end - start),
             "-i", str(video),
             "-vf", "select='gt(scene,0.35)',showinfo",
             "-f", "null", "/dev/null"],
            capture_output=True, text=True, timeout=30)
        count = (proc.stderr or "").count("showinfo")
        if count >= 3:
            return 5, f"{count} scene changes", True
        if count >= 1:
            return 3, f"{count} scene changes", True
        return 0, "no scene changes detected", True
    except Exception:
        return 0, "unscored - ffmpeg scene detection unavailable/failed", False


def _face_signal(video: Path, start: float, end: float) -> tuple[int, str, bool]:
    """Face presence via Haar cascade, weighted 10, only if OpenCV with
    CascadeClassifier is actually installed.

    Honest version note: opencv-python-headless 5.x removed
    CascadeClassifier, so face scoring requires the 4.x line (see
    pyproject optional dependency ``face``). If CascadeClassifier is
    absent, the signal is explicitly unscored, never faked with a
    heuristic and called face detection.
    """
    try:
        import cv2
    except Exception:
        return 0, "unscored - OpenCV not installed in this venv", False
    if not hasattr(cv2, "CascadeClassifier"):
        return 0, ("unscored - OpenCV installed but CascadeClassifier "
                   "removed in opencv 5.x; install "
                   "opencv-python-headless<5 for face scoring"), False
    try:
        import tempfile
        mid = (start + end) / 2.0
        with tempfile.TemporaryDirectory() as td:
            frame = Path(td) / "frame.jpg"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", str(mid), "-i", str(video),
                 "-frames:v", "1", str(frame)],
                capture_output=True, timeout=30, check=True)
            img = cv2.imread(str(frame))
            if img is None:
                return 0, "unscored - could not extract frame", False
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            if cascade.empty():
                return 0, "unscored - Haar cascade unavailable", False
            faces = cascade.detectMultiScale(gray, 1.1, 4)
            n = len(faces)
            return (10 if n >= 1 else 0,
                    f"{n} face(s) detected in middle frame (Haar cascade)", True)
    except Exception:
        return 0, "unscored - face detection failed", False


def _score_segment(words: list[str], excerpt: str, duration: float,
                   video: Path, start: float, end: float,
                   use_audio: bool, use_scene: bool) -> dict:
    """Weighted, labelled scoring. Total is normalised over scored signals."""
    text = excerpt.lower()
    signals: dict[str, dict] = {}
    # Hook: 25
    if "?" in excerpt:
        signals["hook"] = {"score": 25, "weight": 25, "scored": True,
                           "note": "question in transcript"}
    elif any(w in text for w in _HOOK_WORDS):
        signals["hook"] = {"score": 18, "weight": 25, "scored": True,
                           "note": "hook word in transcript"}
    elif words:
        signals["hook"] = {"score": 5, "weight": 25, "scored": True,
                           "note": "no question or hook word found"}
    else:
        signals["hook"] = {"score": 0, "weight": 25, "scored": True,
                           "note": "no transcript words in segment"}
    # Speech density: 25
    wpm = (len(words) / (duration / 60.0)) if duration > 0 else 0
    if wpm >= 120:
        s_speech = 25
    elif wpm >= 80:
        s_speech = 18
    elif wpm >= 40:
        s_speech = 12
    elif words:
        s_speech = 6
    else:
        s_speech = 0
    signals["speech"] = {"score": s_speech, "weight": 25, "scored": True,
                         "note": f"{len(words)} words, {wpm:.0f} wpm",
                         "speech_words_in_segment": len(words)}
    # Emotion/action: 20
    if "!" in excerpt or any(w in text for w in _EMOTION_WORDS):
        signals["emotion_action"] = {"score": 20, "weight": 20, "scored": True,
                                     "note": "emotion/action word or exclamation"}
    elif words:
        signals["emotion_action"] = {"score": 0, "weight": 20, "scored": True,
                                     "note": "no emotion/action word found"}
    else:
        signals["emotion_action"] = {"score": 0, "weight": 20, "scored": True,
                                     "note": "no transcript words in segment"}
    # Completeness: 15
    if excerpt.strip().endswith((".", "!", "?")):
        signals["completeness"] = {"score": 15, "weight": 15, "scored": True,
                                   "note": "excerpt ends with sentence punctuation"}
    elif len(words) >= 5:
        signals["completeness"] = {"score": 7, "weight": 15, "scored": True,
                                   "note": "excerpt does not end with punctuation"}
    else:
        signals["completeness"] = {"score": 0, "weight": 15, "scored": True,
                                   "note": "too few words to judge completeness"}
    # Audio: 10
    if use_audio:
        s, note, ok = _audio_energy(video, start, end)
        signals["audio"] = {"score": s, "weight": 10, "scored": ok, "note": note}
    else:
        signals["audio"] = {"score": 0, "weight": 10, "scored": False,
                            "note": "unscored - audio analysis disabled"}
    # Scene: 5
    if use_scene:
        s, note, ok = _scene_changes(video, start, end)
        signals["scene"] = {"score": s, "weight": 5, "scored": ok, "note": note}
    else:
        signals["scene"] = {"score": 0, "weight": 5, "scored": False,
                            "note": "unscored - scene analysis disabled"}
    # Face: weighted 10, in the total only when actually scored.
    s, note, ok = _face_signal(video, start, end)
    signals["face"] = {"score": s, "weight": 10, "scored": ok, "note": note}
    earned = sum(v["score"] for v in signals.values() if v["scored"])
    max_scored = sum(v["weight"] for v in signals.values() if v["scored"])
    total = round(100 * earned / max_scored) if max_scored else None
    return {"score": total, "score_breakdown": signals,
            "speech_words_in_segment": len(words)}


def clip_candidates(video: Path, transcript_segments: list[dict] | None = None,
                    transcript_path: Path | None = None,
                    use_audio: bool = True, use_scene: bool = True) -> dict:
    """Top 3 clip candidates, scored when a transcript is supplied.

    transcript_path may be .srt, timed .txt, or plain .txt (untimed:
    global signals only, timing explicitly unscored). Without any
    transcript, candidates are explicitly unscored and score is None.
    No virality claim is made: the score is a transparent, weighted
    combination of the labelled signals above.
    """
    parsed = None
    if transcript_path is not None:
        parsed = parse_transcript_file(Path(transcript_path))
        transcript_segments = parsed["segments"]
        timed = parsed["timed"]
    else:
        timed = True
    try:
        meta = pipeline.probe_video(Path(video))
        duration = float(meta.get("duration", 0) or 0)
    except Exception:
        duration = 0.0
    segs = _segments(duration) if duration else []
    candidates = []
    for i, (start, end) in enumerate(segs, start=1):
        seg_duration = end - start
        if transcript_segments:
            if timed:
                words, excerpt_parts = [], []
                for seg in transcript_segments:
                    s = seg.get("start")
                    if s is not None and start <= s < end:
                        excerpt_parts.append(str(seg.get("text", "")))
                        words.extend(str(seg.get("text", "")).split())
                excerpt = " ".join(excerpt_parts)
                scored = _score_segment(words, excerpt, seg_duration,
                                        Path(video), start, end,
                                        use_audio, use_scene)
                candidates.append({
                    "candidate": i, "start": start, "end": end,
                    "duration": round(seg_duration, 1), "scored": True,
                    "score": scored["score"],
                    "score_breakdown": scored["score_breakdown"],
                    "speech_words_in_segment": scored["speech_words_in_segment"],
                    "transcript_excerpt": excerpt[:200],
                    "transcript_timed": True,
                    "note": ("Weighted score from transcript, audio, "
                             "scene, and face signals (face weighted 10, "
                             "scored only when Haar detection actually ran). "
                             "This is a transparent ranking aid, not a "
                             "virality guarantee."),
                })
            else:
                # Untimed transcript: global signals only, no invented timing.
                excerpt = " ".join(str(s.get("text", "")) for s in transcript_segments)
                words = excerpt.split()
                scored = _score_segment(words, excerpt, seg_duration,
                                        Path(video), start, end,
                                        use_audio, use_scene)
                scored["score_breakdown"]["speech"] = {
                    "score": 0, "weight": 25, "scored": False,
                    "note": ("unscored - untimed transcript: segment timing "
                             "was not invented"),
                    "speech_words_in_segment": None}
                earned = sum(v["score"] for v in scored["score_breakdown"].values()
                             if v["scored"])
                max_scored = sum(v["weight"] for v in scored["score_breakdown"].values()
                                 if v["scored"])
                scored["score"] = round(100 * earned / max_scored) if max_scored else None
                candidates.append({
                    "candidate": i, "start": start, "end": end,
                    "duration": round(seg_duration, 1), "scored": True,
                    "score": scored["score"],
                    "score_breakdown": scored["score_breakdown"],
                    "speech_words_in_segment": None,
                    "transcript_excerpt": excerpt[:200],
                    "transcript_timed": False,
                    "note": ("Untimed transcript: hook/emotion/completeness "
                             "use the whole text; segment timing was not "
                             "invented."),
                })
        else:
            candidates.append({
                "candidate": i, "start": start, "end": end,
                "duration": round(seg_duration, 1), "scored": False,
                "score": None,
                "score_breakdown": {
                    "hook": {"score": 0, "weight": 25, "scored": False,
                             "note": "unscored - no transcript available"},
                    "speech": {"score": 0, "weight": 25, "scored": False,
                               "note": "unscored - no transcript available",
                               "speech_words_in_segment": None},
                    "emotion_action": {"score": 0, "weight": 20, "scored": False,
                                       "note": "unscored - no transcript available"},
                    "completeness": {"score": 0, "weight": 15, "scored": False,
                                     "note": "unscored - no transcript available"},
                    "audio": {"score": 0, "weight": 10, "scored": False,
                              "note": "unscored - no transcript available"},
                    "scene": {"score": 0, "weight": 5, "scored": False,
                              "note": "unscored - no transcript available"},
                    "face": {"score": 0, "weight": 10, "scored": False,
                             "note": "unscored - no transcript available"},
                },
                "transcript_excerpt": "",
                "note": "Transcript unavailable: Whisper was not run here, "
                        "so no transcript was invented.",
            })
    if not candidates:
        return {"duration": duration, "candidates": candidates,
                "note": "No clip candidates: duration unknown or file "
                        "could not be probed. No transcript was invented."}
    ranked = sorted(candidates, key=lambda c: (c["score"] is not None,
                                               c["score"] or 0), reverse=True)
    return {"duration": duration, "candidates": candidates,
            "ranked_candidate_numbers": [c["candidate"] for c in ranked],
            "note": ("Candidates are ranked by the labelled weighted score "
                     "when a transcript is supplied; otherwise unscored. "
                     "This is a ranking aid, not a virality guarantee.")}


def transcript_from_audio(audio: Path, video_duration: float = 0.0) -> dict:
    """Transcribe audio with existing captions.whisper_words, if available.

    Returns {"segments": [...], "source": "whisper"} or raises with a clear
    reason. Callers must not fall back to inventing a transcript.
    """
    from .captions import whisper_words
    words = whisper_words(Path(audio))
    # Group words into sentence-ish segments by punctuation / 30-word cap.
    segments, current, start = [], [], None
    for w in words:
        if start is None:
            start = w.start
        current.append(w.text)
        if w.text.strip().endswith((".", "!", "?")) or len(current) >= 30:
            segments.append({"start": start, "end": w.end,
                             "text": " ".join(current)})
            current, start = [], None
    if current and start is not None:
        segments.append({"start": start, "end": words[-1].end,
                         "text": " ".join(current)})
    return {"segments": segments, "source": "whisper", "words": len(words)}
