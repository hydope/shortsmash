"""Captions stage: word-level karaoke subtitles in the viral short style.

Primary: faster-whisper `tiny` model with word timestamps → precise alignment.
Fallback (no model/deps): even word distribution across each scene's audio
duration. The fallback is approximate and labeled as such in the ASS comments.

Output is an .ass file with three styles:
- Karaoke  — word-by-word highlight pop (the viral look)
- Minimal  — clean bottom captions
- Bold     — big centered punch words
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .schemas import EditPlan, Scene
from .voice import probe_duration


@dataclass
class Word:
    text: str
    start: float
    end: float


def _fmt_time(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    cs = int(round((s - int(s)) * 100))
    return f"{h}:{m:02d}:{sec:02d}.{cs:02d}"


def whisper_words(audio: Path, offset: float = 0.0) -> list[Word]:
    """Word timestamps from faster-whisper. Raises if deps/model unavailable.

    Compatibility shim, documented not hidden: faster-whisper 1.2.1
    calls av.open(..., metadata_errors="ignore"), which av 19 removed.
    av.open works without that kwarg, so on a TypeError mentioning it
    we retry once with a wrapper that drops the kwarg. No other
    behaviour is changed, and the shim is removed if the call succeeds
    the first time.
    """
    from faster_whisper import WhisperModel

    model = WhisperModel("tiny", device="cpu", compute_type="int8")
    try:
        segments, _ = model.transcribe(str(audio), word_timestamps=True)
        words: list[Word] = []
        for seg in segments:
            for w in seg.words or []:
                words.append(Word(w.word.strip(), offset + w.start, offset + w.end))
        return words
    except TypeError as exc:
        if "metadata_errors" not in str(exc):
            raise
        import av as _av
        _orig_open = _av.open

        def _patched_open(*args, **kwargs):
            kwargs.pop("metadata_errors", None)
            return _orig_open(*args, **kwargs)

        _av.open = _patched_open  # type: ignore[attr-defined]
        try:
            segments, _ = model.transcribe(str(audio), word_timestamps=True)
        finally:
            _av.open = _orig_open  # type: ignore[attr-defined]
        words = []
        for seg in segments:
            for w in seg.words or []:
                words.append(Word(w.word.strip(), offset + w.start, offset + w.end))
        return words


def distribute_words(scene: Scene, duration: float, offset: float) -> list[Word]:
    """Fallback aligner: spread words evenly across the scene duration."""
    tokens = scene.voiceover.split()
    if not tokens:
        return []
    per = duration / len(tokens)
    return [Word(t, offset + i * per, offset + (i + 1) * per)
            for i, t in enumerate(tokens)]


def _escape(text: str) -> str:
    return text.replace("{", "(").replace("}", ")")


# U+200F RIGHT-TO-LEFT MARK. Prepended to a dialogue line it forces the
# paragraph base direction to RTL, so libass/FriBidi lays Urdu/Arabic
# captions out right-to-left (first spoken word at the right, reading
# order matching the narration) instead of left-to-right like English.
RLM = "\u200f"


def _is_rtl(text: str) -> bool:
    """True when the text contains strong right-to-left characters
    (Arabic script incl. Urdu, Hebrew)."""
    return any(
        "\u0590" <= ch <= "\u08ff" or "\ufb50" <= ch <= "\ufeff"
        for ch in text
    )


def _rtl(text: str, fontsize: int = 64) -> str:
    if not _is_rtl(text):
        return text
    # Nastaliq is Urdu's native calligraphic style — Arial's fallback
    # shapes correctly but looks foreign to native readers. Nastaliq's
    # glyphs are small relative to its em box, so scale ~1.7x for the
    # same visual weight. The {\fn\fs} overrides must precede the RLM.
    big = int(fontsize * 1.7)
    return f"{{\\fnNoto Nastaliq Urdu\\fs{big}}}" + RLM + text


def build_ass(words: list[Word], out: Path, style: str = "karaoke",
              fontsize: int = 64, width: int = 1080, height: int = 1920,
              overlays: list[dict] | None = None) -> Path:
    """Write an ASS subtitle file with word-level karaoke timing.

    overlays: [{start, end, text, kind}] — kind "hook" (top, big) or "cta"
    (middle, yellow). Rendered by libass: no fontfile hunting, no drawtext
    quoting bugs.
    """
    out.parent.mkdir(parents=True, exist_ok=True)

    # Alignment 2 = bottom-center (MarginV measured from bottom),
    # 8 = top-center, 5 = middle-center.
    # NOTE: the RTL per-word highlight below reuses these exact colors via
    # explicit {\c} overrides (\2c alone does not apply in libass).
    PRIMARY = "&H00FFFFFF"
    SECONDARY = "&H000019FF"
    styles = {
        "karaoke": (
            f"Style: Karaoke,Arial,{fontsize},{PRIMARY},{SECONDARY},&H80000000,&H00000000,"
            "-1,0,0,0,100,100,0,0,1,3,0,2,10,10,320,1"
        ),
        "minimal": (
            f"Style: Minimal,Arial,{int(fontsize * 0.8)},&H00FFFFFF,&H000000FF,&H90000000,&H00000000,"
            "-1,0,0,0,100,100,0,0,1,2,0,2,10,10,220,1"
        ),
        "bold": (
            f"Style: Bold,Arial Black,{int(fontsize * 1.2)},&H0000FFFF,&H00FFFFFF,&H90000000,&H00000000,"
            "-1,0,0,0,100,100,0,0,1,4,0,5,10,10,10,1"
        ),
        "hook": (
            f"Style: Hook,Arial,{int(fontsize * 0.9)},&H00FFFFFF,&H000000FF,&H90000000,&H00000000,"
            "-1,0,0,0,100,100,0,0,1,3,0,8,10,10,220,1"
        ),
        "cta": (
            f"Style: Cta,Arial Black,{int(fontsize * 1.1)},&H0000FFFF,&H00FFFFFF,&H90000000,&H00000000,"
            "-1,0,0,0,100,100,0,0,1,4,0,5,10,10,10,1"
        ),
        "lyrics": (
            f"Style: Lyrics,Arial Black,{fontsize},{PRIMARY},{SECONDARY},&H90000000,&H00000000,"
            "-1,0,0,0,100,100,0,0,1,4,0,5,30,30,120,1"
        ),
    }
    style_name = style.capitalize()
    # All styles must be declared — libass falls back to a tiny default style
    # for any style name missing from this section.
    all_styles = "\n".join(styles.values())
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{all_styles}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    # Group words into caption lines of <= 4 words for readability.
    lines: list[list[Word]] = []
    for w in words:
        if not lines or len(lines[-1]) >= 4:
            lines.append([])
        lines[-1].append(w)

    events = []
    for line in lines:
        start, end = _fmt_time(line[0].start), _fmt_time(line[-1].end)
        line_text = " ".join(w.text for w in line)
        if style == "karaoke" and not _is_rtl(line_text):
            body = "".join(
                "{\\kf%d}%s " % (max(1, int((w.end - w.start) * 100)), _escape(w.text))
                for w in line
            ).strip()
            # RLM goes first so it sets the paragraph direction before any
            # karaoke override blocks; timings stay glued to their words.
            body = _rtl(body, fontsize)
            events.append(
                f"Dialogue: 0,{start},{end},{style_name},,0,0,0,,{body}"
            )
        elif style == "karaoke":
            # RTL karaoke: libass applies \kf in *visual* order, so the
            # highlight would sweep the wrong way and land on the wrong
            # words. Instead emit one event per word with the current word
            # highlighted — exact timing, correct RTL, no sweep-direction
            # bug. (Explicit {\c} colors: bare \2c does not apply in libass.)
            hi, lo = f"{{\\c{SECONDARY}&}}", f"{{\\c{PRIMARY}&}}"
            for w in line:
                parts = []
                for w2 in line:
                    t = _escape(w2.text)
                    parts.append(f"{hi}{t}{lo}" if w2 is w else t)
                body = _rtl(" ".join(parts), fontsize)
                events.append(
                    f"Dialogue: 0,{_fmt_time(w.start)},{_fmt_time(w.end)},"
                    f"{style_name},,0,0,0,,{body}"
                )
        else:
            body = _rtl(" ".join(_escape(w.text) for w in line), fontsize)
            events.append(
                f"Dialogue: 0,{start},{end},{style_name},,0,0,0,,{body}"
            )

    for ov in overlays or []:
        kind = "Hook" if ov["kind"] == "hook" else "Cta"
        wrapped = _wrap(ov["text"], 24)
        events.append(
            f"Dialogue: 0,{_fmt_time(ov['start'])},{_fmt_time(ov['end'])},"
            f"{kind},,0,0,0,,{_rtl(_escape(wrapped), fontsize)}"
        )

    out.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return out


def plan_overlays(plan: "EditPlan", total: float) -> list[dict]:
    """Hook (0-3s, top) + CTA (last 2.5s, middle) overlay events."""
    ov = [{"start": 0.0, "end": 3.0, "text": plan.hook, "kind": "hook"}]
    if plan.cta:
        ov.append({"start": max(0.0, total - 2.5), "end": total,
                   "text": plan.cta, "kind": "cta"})
    return ov


def _wrap(text: str, width: int = 24) -> str:
    """Word-wrap for ASS (\\N = newline). Max 3 lines."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\\N".join(lines[:3])


def build_plan_captions(plan: EditPlan, audio_paths: list[Path], workdir: Path,
                       cfg: Config | None = None) -> Path:
    """Caption the whole plan: whisper where possible, fallback otherwise."""
    cfg = cfg or Config()
    offset = 0.0
    all_words: list[Word] = []
    used_fallback = False
    for scene, audio in zip(plan.scenes, audio_paths):
        dur = probe_duration(audio)
        try:
            words = whisper_words(audio, offset)
            if not words:
                raise RuntimeError("empty transcript")
        except Exception:
            words = distribute_words(scene, dur, offset)
            used_fallback = True
        all_words.extend(words)
        offset += dur
    out = workdir / "captions.ass"
    build_ass(all_words, out, style=cfg.get("caption_style"),
              fontsize=cfg.get("caption_fontsize"),
              width=cfg.get("width"), height=cfg.get("height"),
              overlays=(plan_overlays(plan, offset)
                        if cfg.get("caption_overlays", False) else None))
    if used_fallback:
        with open(out, "a", encoding="utf-8") as f:
            f.write("; NOTE: word timing is approximate (whisper unavailable)\n")
    return out
