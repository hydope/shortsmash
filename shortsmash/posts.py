"""AI Post Studio for ShortSmash Social 0.4.

Drafts only. Nothing here posts anything.

Rules enforced in code, not just in docs:
- do-not-say phrases are removed (case-insensitive).
- The employer/company is mentioned only when employer_opt_in is True.
- No dates, prices, results, or endorsements are invented. If the supplied
  topic/notes ask for one and do not supply it, it is recorded as a fact
  gap instead of being invented.
- An empty profile invents nothing: drafts describe only the supplied
  topic/notes and are labelled inferred.
- LLM text is used for full bodies when available, via one parsed
  llm.chat_text call (see _llm_bodies). A variant is labelled "ai"
  only if that variant's body actually came from the LLM; every other
  variant is labelled "template". A hook-only fallback is kept if the
  full-body call fails but a hook succeeds. Otherwise the source is
  "template" and the draft says so.
"""
from __future__ import annotations

import re
from typing import Any

from .config import Config
from .profile import style_lock

PLATFORMS = ("x", "threads", "bluesky", "instagram", "youtube", "linkedin")

# Conservative character targets (not hard platform maxima, except X/Bluesky
# where the short form is the point). Drafts are truncated to these targets.
_LIMITS = {"x": 280, "threads": 500, "bluesky": 300,
           "instagram": 900, "youtube": 900, "linkedin": 1200}

_FACT_KEYWORDS = {
    "price": ("price", "cost", "how much", "$"),
    "date": ("date", "when", "deadline"),
    "discount": ("discount", "off", "sale"),
    "result": ("result", "won", "winner", "revenue", "growth"),
    "endorsement": ("endorsed", "recommended by", "sponsored"),
}


def _fact_gaps(topic: str, notes: str) -> list[str]:
    supplied = f"{topic} {notes}".lower()
    gaps: list[str] = []
    for name, keywords in _FACT_KEYWORDS.items():
        if any(k in supplied for k in keywords):
            # A digit or $ in the supplied text counts as the fact supplied.
            if not re.search(r"[\d$]", supplied):
                gaps.append(f"{name} not supplied - do not invent it")
    return gaps


def _remove_banned(text: str, banned: list[str]) -> str:
    for phrase in banned:
        if not phrase:
            continue
        text = re.sub(re.escape(phrase), "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" ,. ")


def _hashtags(topic: str) -> list[str]:
    words = [re.sub(r"[^a-z0-9]", "", w) for w in topic.lower().split()]
    tags = []
    for w in words:
        if len(w) >= 4 and w not in ("with", "from", "about", "this"):
            tag = "#" + w.capitalize()
            if tag not in tags:
                tags.append(tag)
        if len(tags) >= 3:
            break
    return tags


def _apply_profile(text: str, profile: dict | None) -> str:
    if not profile:
        return text
    if profile.get("does") and profile["does"].lower() not in text.lower():
        # Only append a supplied fact; never invent a job title.
        text = f"{text} - {profile['does']}."
    return text


# Openers are deliberately different per platform, so the same idea does
# not read as copy-paste at first glance. The opener is part of the draft,
# not a suffix: the first ~20 characters must differ across platforms.
_OPENERS = {
    "x": "Punchy take: ",
    "threads": "Okay, story time: ",
    "bluesky": "In short: ",
    "instagram": "Picture this: ",
    "youtube": "You need to see this: ",
    "linkedin": "A lesson I keep coming back to: ",
}


def _platform_format(platform: str, body: str, hashtags: list[str],
                     profile: dict | None) -> str:
    tags = " ".join(hashtags)
    opener = _OPENERS.get(platform, "")
    opened = f"{opener}{body}".strip()
    if platform == "x":
        text = f"{opened} {tags}".strip()
    elif platform == "threads":
        text = f"{opened}\n\n{tags}\n\nMore in the thread below.".strip()
    elif platform == "bluesky":
        text = f"{opened}\n{tags}\n\nShort version, for Bluesky.".strip()
    elif platform == "instagram":
        text = f"{opened}\n\n{tags}\n\nSee you in the next one."
    elif platform == "youtube":
        text = f"{opened}\n\n{tags}\n\nFull short version on this channel."
    elif platform == "linkedin":
        tone = profile.get("tone", "") if profile else ""
        text = f"{opened}\n\n{tags}"
        if tone:
            text += f"\n\nShared in a {tone} tone."
    else:
        text = opened
    limit = _LIMITS[platform]
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _llm_hook(topic: str, profile: dict | None, cfg: Config | None) -> str | None:
    """One short hook from the real LLM, or None. Never invents facts."""
    try:
        from . import llm as llm_mod
        if llm_mod.provider(cfg or Config()) == "template":
            return None
        hook = llm_mod.chat_text(
            "You write one short social-media hook. No dates, prices, "
            "results, or endorsements unless the user supplied them.",
            f"Write one hook under 120 characters for: {topic}",
            cfg or Config())
    except Exception:
        return None
    hook = (hook or "").strip().splitlines()[0] if hook else ""
    if not hook or len(hook) > 140:
        return None
    return hook


def _llm_bodies(topic: str, profile: dict | None,
                cfg: Config | None) -> dict[str, list[str]] | None:
    """Full distinct bodies per platform from ONE llm.chat_text call.

    The prompt asks for clearly delimited sections (``X:``, ``THREADS:``,
    etc., each with 3 numbered bodies). Parsing is strict: a platform
    is used only if at least one numbered body was found for it, and a
    body is used only if it is non-empty and under 900 characters.
    Returns None if no platform parsed, so callers fall back cleanly
    to template bodies (or the hook fallback) instead of faking ai.
    One call, not six, so free-tier rate limits are respected.
    """
    try:
        from . import llm as llm_mod
        if llm_mod.provider(cfg or Config()) == "template":
            return None
        profile_note = ""
        if profile:
            bits = [f"{k}: {v}" for k, v in profile.items()
                    if k in ("does", "niche", "tone", "audience", "language")
                    and v]
            if bits:
                profile_note = "Profile (supplied facts only): " + "; ".join(bits) + ". "
        raw = llm_mod.chat_text(
            "You write social-media post bodies. Use only the supplied "
            "topic and profile facts. No dates, prices, results, or "
            "endorsements unless the user supplied them. Keep each body "
            "under 200 words, and make each platform's structure different: "
            "X short and punchy, Threads a longer story, Bluesky concise "
            "and declarative, Instagram a visual scene with a call to "
            "action, YouTube a title-style hook plus a description, "
            "LinkedIn an insight and a lesson.",
            f"{profile_note}Topic: {topic}\n\n"
            "Reply with exactly these sections, in this order, each with "
            "3 numbered bodies:\n"
            "X:\n1. ...\n2. ...\n3. ...\n"
            "THREADS:\n1. ...\n2. ...\n3. ...\n"
            "BLUESKY:\n1. ...\n2. ...\n3. ...\n"
            "INSTAGRAM:\n1. ...\n2. ...\n3. ...\n"
            "YOUTUBE:\n1. ...\n2. ...\n3. ...\n"
            "LINKEDIN:\n1. ...\n2. ...\n3. ...",
            cfg or Config())
    except Exception:
        return None
    if not raw:
        return None
    header_for = {"x": "x", "threads": "threads", "bluesky": "bluesky",
                  "instagram": "instagram", "youtube": "youtube",
                  "linkedin": "linkedin"}
    parsed: dict[str, list[str]] = {}
    current: str | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        m = re.match(r"^(X|THREADS|BLUESKY|INSTAGRAM|YOUTUBE|LINKEDIN)\s*:\s*$",
                     stripped, flags=re.IGNORECASE)
        if m:
            current = header_for[m.group(1).lower()]
            parsed.setdefault(current, [])
            continue
        if current is None:
            continue
        bm = re.match(r"^[1-3][.)]\s*(.+)$", stripped)
        if bm and len(parsed[current]) < 3:
            body = bm.group(1).strip()
            # _platform_format adds the opener, so strip it here if
            # the LLM already used it, to avoid a doubled opener.
            for _op in _OPENERS.values():
                if body.lower().startswith(_op.lower().strip()):
                    body = body[len(_op.strip()):].strip()
                    break
            if body and len(body) <= 900:
                parsed[current].append(body)
    parsed = {k: v for k, v in parsed.items() if v}
    return parsed or None


def _template_body(platform: str, kind: str, topic: str) -> str:
    """Distinct body structure per platform, template path.

    Only the supplied topic is used; no events, results, or endorsements
    are invented. The opener in _platform_format is a prefix, the body
    below is what makes the middle of each platform's draft different.
    """
    if kind == "question":
        base = {
            "x": f"Ever wondered about {topic}? Short version, no fluff.",
            "threads": (f"Ever wondered about {topic}? I kept it simple, "
                        "tried the straightforward version, and here is "
                        "what stood out. What would you try first?"),
            "bluesky": f"Ever wondered about {topic}? Simple version, done well.",
            "instagram": (f"Ever wondered about {topic}? One clear scene, "
                          "one simple takeaway. Save this and try it."),
            "youtube": (f"Ever wondered about {topic}? Watch the short "
                        "version, then tell me what you would add."),
            "linkedin": (f"Ever wondered about {topic}? The lesson is "
                         "keeping it simple and consistent. What has "
                         "worked for you?"),
        }
    elif kind == "statement":
        base = {
            "x": f"Quick take on {topic} - no fluff, just the good part.",
            "threads": (f"Quick take on {topic}, with the longer story: "
                        "keep it simple, do the straightforward version "
                        "well, and share what you learn along the way."),
            "bluesky": f"Quick take: {topic}, done simply and well.",
            "instagram": (f"Quick take on {topic}: one scene, one idea, "
                          "one thing to try. Save this for later."),
            "youtube": (f"Quick take on {topic} in under a minute. "
                        "Full short version on this channel."),
            "linkedin": (f"Quick take on {topic}: simplicity beats "
                         "complexity when you stay consistent. Agree?"),
        }
    else:  # story
        base = {
            "x": f"Today's note on {topic}. Watch, save it, and tell me what you think.",
            "threads": (f"Today's note on {topic}. I started simple, kept "
                        "the straightforward version, and noted what "
                        "worked. Here is the story in full, in the thread."),
            "bluesky": f"Today's note: {topic}, kept simple.",
            "instagram": (f"Today's note on {topic}: picture the scene, "
                          "try the simple version, and share your take."),
            "youtube": (f"Today's note on {topic}: watch the short, "
                        "then tell me your version in the comments."),
            "linkedin": (f"Today's note on {topic}: the lesson I keep "
                         "coming back to is consistency over complexity."),
        }
    return base.get(platform, f"Note on {topic}.")


def generate_drafts(topic: str, profile: dict | None = None,
                    notes: str = "", cfg: Config | None = None,
                    use_llm: bool = True,
                    platforms: tuple[str, ...] = PLATFORMS) -> dict[str, Any]:
    """Return 3 drafts per platform. Deterministic template unless the LLM
    supplies a hook; the source label always tells the truth."""
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("topic is required - an empty topic cannot be drafted")
    profile = profile or {}
    banned = list(profile.get("do_not_say", []) or [])
    company = (profile.get("company") or "").strip()
    employer_opt_in = bool(profile.get("employer_opt_in", False))
    gaps = _fact_gaps(topic, notes or "")
    inferred = not any(profile.get(k) for k in ("name", "does", "niche", "tone"))
    # Full LLM bodies first (one call); hook fallback second. Either
    # way, a variant is labelled ai only if its body came from the LLM.
    llm_bodies: dict[str, list[str]] | None = None
    hook = None
    if use_llm:
        llm_bodies = _llm_bodies(topic, profile, cfg)
        if llm_bodies is None:
            hook = _llm_hook(topic, profile, cfg)
    hashtags = _hashtags(topic)
    if company and employer_opt_in:
        # Company is a supplied fact, used only with opt-in.
        pass  # handled below per-variant, never for other profiles

    kinds = ["question", "statement", "story"]
    out: dict[str, list[dict]] = {}
    for platform in platforms:
        if platform not in _LIMITS:
            raise ValueError(f"Unknown platform: {platform}")
        drafts = []
        for i, kind in enumerate(kinds, start=1):
            llm_body = None
            if llm_bodies and platform in llm_bodies:
                if i - 1 < len(llm_bodies[platform]):
                    llm_body = llm_bodies[platform][i - 1]
            if llm_body is not None:
                body = llm_body
                is_ai = True
            elif hook and i == 1:
                body = hook
                is_ai = True
            else:
                body = _template_body(platform, kind, topic)
                is_ai = False
            if notes:
                body = f"{body} {notes.strip()}"
            body = _apply_profile(body, profile if not inferred else None)
            if company and employer_opt_in and platform in ("linkedin", "youtube"):
                body = f"{body} Shared from {company}." if company not in body else body
            # Employer filtering: strip the company unless opt-in.
            if company and not employer_opt_in:
                body = re.sub(re.escape(company), "", body, flags=re.IGNORECASE)
            body = _remove_banned(body, banned)
            tags = [t for t in hashtags if not any(
                b.lower() in t.lower() for b in banned)]
            text = _platform_format(platform, body, tags,
                                    profile if not inferred else None)
            # Final safety pass: never let a banned phrase or an
            # un-opted-in company survive formatting.
            text = _remove_banned(text, banned)
            if company and not employer_opt_in:
                text = re.sub(re.escape(company), "", text,
                              flags=re.IGNORECASE)
            text = re.sub(r"\s+", " ", text).strip()
            drafts.append({
                "platform": platform, "variant": i, "kind": kind,
                "text": text, "hashtags": tags,
                # Honest per-variant label: ai only if this variant's
                # body came from the LLM (full body or hook fallback);
                # every other variant is template, even in an ai pack.
                "source": "ai" if is_ai else "template",
                "ai_hook": bool(is_ai),
                "ai_body": bool(llm_body is not None),
                "inferred": inferred, "fact_gaps": gaps,
                "source_labels": (
                    ["supplied topic/notes only - no profile invented"]
                    if inferred else
                    ["profile voice/style", "supplied topic/notes"]
                ),
                "style_lock": style_lock(profile) if profile else "",
            })
        out[platform] = drafts
    return {"topic": topic, "notes": notes, "platforms": out,
            "fact_gaps": gaps, "inferred": inferred}
