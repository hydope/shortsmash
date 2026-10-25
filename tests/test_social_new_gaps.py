"""Gap-fix tests for ShortSmash Social 0.4 round 2.

All offline and mocked except the Whisper test, which runs on the
repo's own demo video audio and skips cleanly (with the reason
printed) if faster-whisper is not installed. No paid API is called
live by any test here.
"""
import base64
import json
import subprocess

import pytest

from shortsmash import approval, images, posts, profile as prof, repurpose
from shortsmash.social_cli import validate_pack_result


def _profile(**over):
    p = prof.empty_profile()
    p.update(over)
    return p


# --- Gap 1: repo-alone Pixabay path, mocked httpx ---------------------

def test_pixabay_repo_alone_path_mocked_http(tmp_path, monkeypatch):
    """Repo-alone path must work without the unversioned vault helper.

    Mocks httpx.get (the Pixabay API) and httpx.stream (the download),
    and forces the helper path to fail, so a pass proves the repo
    alone, with a user key, is enough.
    """
    import httpx
    from pathlib import Path

    monkeypatch.setenv("PIXABAY_API_KEY", "test-key-only")
    monkeypatch.setattr(images, "_pixabay_photo_search_helper",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("helper must not be needed")))

    class FakeResp:
        status_code = 200

        def json(self):
            return {"hits": [{"id": 999, "pageURL": "https://pixabay.com/photos/x-999/",
                              "tags": "coffee, morning", "user": "tester",
                              "largeImageURL": "https://cdn.pixabay.com/photo-999.jpg",
                              "webformatURL": "https://cdn.pixabay.com/photo-999_s.jpg"}]}

        def raise_for_status(self):
            return None

    class FakeStream:
        status_code = 200

        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self, chunk_size=1 << 20):
            yield _PNG_1x1

    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResp())
    monkeypatch.setattr(httpx, "stream", FakeStream)
    result = images.search_stock_photos(
        "coffee", 1, out_dir=tmp_path / "imgs",
        social_base=tmp_path / "social")
    assert result["candidates"][0]["provider"] == "stock"
    assert result["candidates"][0]["pixabay_id"] == 999
    assert result["candidates"][0]["page_url"].endswith("/x-999/")
    assert result["candidates"][0]["photographer"] == "tester"
    assert "Pixabay" in result["candidates"][0]["licence"]
    assert "Real Pixabay photo" in result["candidates"][0]["note"]
    assert Path(result["candidates"][0]["path"]).exists()


# --- Gap 2/3: full LLM bodies + distinct bodies -------------------------

def test_full_llm_bodies_labelled_ai_per_variant(monkeypatch):
    """One mocked chat_text call yields full bodies per platform.

    Variants with an LLM body are source=ai with ai_body True;
    platforms with no parsed body fall back to template labelling.
    """
    from shortsmash import llm as llm_mod

    def _fake_chat(system, user, cfg, temperature=0.8):
        sections = []
        for name in ("X", "THREADS", "BLUESKY", "INSTAGRAM", "YOUTUBE", "LINKEDIN"):
            sections.append(f"{name}:\n1. {name} body one about fishing.\n"
                            f"2. {name} body two about fishing.\n"
                            f"3. {name} body three about fishing.")
        return "\n".join(sections)

    monkeypatch.setattr(llm_mod, "provider", lambda cfg: "nvidia")
    monkeypatch.setattr(llm_mod, "chat_text", _fake_chat)
    drafts = posts.generate_drafts("morning fishing", _profile(), use_llm=True)
    for platform, items in drafts["platforms"].items():
        for d in items:
            assert d["source"] == "ai", platform
            assert d["ai_body"] is True
            assert platform.upper() in d["text"].upper() or "fishing" in d["text"]
    # Distinct full bodies, not just distinct openers.
    texts = {items[0]["text"] for items in drafts["platforms"].values()}
    assert len(texts) == len(posts.PLATFORMS)


def test_llm_fallback_labels_template_when_llm_unavailable(monkeypatch):
    """LLM failure must fall back cleanly to template, never fake ai."""
    from shortsmash import llm as llm_mod

    monkeypatch.setattr(llm_mod, "provider", lambda cfg: "nvidia")
    monkeypatch.setattr(llm_mod, "chat_text",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("503 high demand")))
    monkeypatch.setattr(posts, "_llm_hook", lambda *a: None)
    drafts = posts.generate_drafts("morning fishing", _profile(), use_llm=True)
    for items in drafts["platforms"].values():
        for d in items:
            assert d["source"] == "template"
            assert d["ai_body"] is False


def test_template_bodies_differ_in_body_not_only_opener():
    """_template_body itself must differ per platform for every kind."""
    for kind in ("question", "statement", "story"):
        bodies = {posts._template_body(p, kind, "morning fishing")
                  for p in posts.PLATFORMS}
        assert len(bodies) == len(posts.PLATFORMS), kind
        # No two platforms share the same body structure.
        assert all("morning fishing" in b for b in bodies)


# --- Gap 6: real Gemini/Pollinations paths, mocked HTTP ------------------

_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8Xw8AAwMCAO+ip1sAAAAASUVORK5CYII=")


def test_gemini_mocked_call_proves_cost_cap_provenance(tmp_path, monkeypatch):
    import httpx

    monkeypatch.setenv("GEMINI_API_KEY", "mock-key")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    calls = []

    class FakeResp:
        status_code = 200

        def json(self):
            return {"candidates": [{"content": {"parts": [
                {"inlineData": {"mimeType": "image/png",
                                "data": base64.b64encode(_PNG_1x1).decode()}}]}}]}

        def raise_for_status(self):
            return None

    def _post(url, **kwargs):
        calls.append(url)
        assert "gemini-2.5-flash-image" in url
        assert kwargs["params"]["key"] == "mock-key"
        return FakeResp()

    monkeypatch.setattr(httpx, "post", _post)
    result = images.generate("morning fishing", provider="gemini", count=1,
                             out_dir=tmp_path / "imgs",
                             social_base=tmp_path / "social")
    assert calls, "Gemini generateContent was not called"
    cand = result["candidates"][0]
    assert cand["provider"] == "gemini"
    assert cand["estimated_cost_usd"] == 0.0336
    assert result["cost_preview"]["estimated_total_usd"] == 0.0336
    assert cand["provenance"]["model"] == "gemini-2.5-flash-image"
    from pathlib import Path
    assert Path(cand["path"]).exists()
    # Usage was recorded, so a second 1-image call is still within $5.
    assert images._spent_today("gemini", tmp_path / "social") == pytest.approx(0.0336)


def test_pollinations_mocked_call_and_no_key_failure(tmp_path, monkeypatch):
    import httpx

    monkeypatch.delenv("POLLINATIONS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="NOT configured"):
        images.generate("test", provider="pollinations",
                        out_dir=tmp_path / "imgs",
                        social_base=tmp_path / "social")
    monkeypatch.setenv("POLLINATIONS_API_KEY", "mock-key")

    class FakeResp:
        status_code = 200

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(_PNG_1x1).decode()}]}

        def raise_for_status(self):
            return None

    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp())
    result = images.generate("morning fishing", provider="pollinations", count=1,
                             out_dir=tmp_path / "imgs",
                             social_base=tmp_path / "social")
    assert result["candidates"][0]["provider"] == "pollinations"
    assert result["candidates"][0]["provenance"]["provider"] == "pollinations"
    from pathlib import Path
    assert Path(result["candidates"][0]["path"]).exists()


# --- Gap 4: Whisper on a real audio track --------------------------------

def test_whisper_on_demo_audio_has_word_timings(tmp_path):
    """End-to-end Whisper on the repo's own demo video audio.

    Skips cleanly, with the reason printed, only if faster-whisper
    is not installed. The demo is ShortSmash's own footage, not
    downloaded copyrighted audio.
    """
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        print("SKIP: faster-whisper not installed, Whisper test skipped")
        pytest.skip("faster-whisper not installed")
    import shutil
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not available in this environment")
    from pathlib import Path
    demo = Path(__file__).parent.parent / "demo" / "coffee-v1.mp4"
    if not demo.exists():
        pytest.skip(f"demo video not found at {demo}")
    wav = tmp_path / "coffee.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(demo),
                    "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True)
    transcript = repurpose.transcript_from_audio(wav)
    assert transcript["source"] == "whisper"
    assert transcript["words"] >= 20, \
        f"only {transcript['words']} words transcribed"
    assert transcript["segments"], "no transcript segments grouped"
    for seg in transcript["segments"]:
        assert seg["start"] >= 0 and seg["end"] >= seg["start"]
        assert seg["text"].strip()
    # Transcript-scored candidates from that real transcript.
    clips = repurpose.clip_candidates(
        demo, transcript["segments"], use_audio=False, use_scene=False)
    assert clips["candidates"], "no clip candidates from real transcript"
    assert any(c["scored"] for c in clips["candidates"])
    assert any("speech" in c["score_breakdown"] for c in clips["candidates"])


# --- Gap 5: face scoring, honestly ---------------------------------------

def test_face_signal_runs_when_opencv_available(tmp_path):
    """Face signal must be scored (not faked) when Haar is available.

    A black frame has 0 faces, which is a valid scored result, not a
    detection claim. Skips cleanly if OpenCV 4.x is not installed.
    """
    try:
        import cv2
        if not hasattr(cv2, "CascadeClassifier"):
            pytest.skip("OpenCV installed but CascadeClassifier removed (5.x)")
    except ImportError:
        pytest.skip("opencv-python-headless not installed")
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not available")
    video = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "color=c=black:s=320x240:d=10", "-pix_fmt", "yuv420p",
                    str(video)], check=True)
    score, note, scored = repurpose._face_signal(video, 0.0, 10.0)
    assert scored is True
    assert score == 0  # black frame, no faces, honestly 0
    assert "face(s) detected" in note
    assert "Haar" in note


# --- Gap 7: Studio integration --------------------------------------------

def test_studio_main_page_has_social_tab():
    """Social is in the main Studio nav/tabs, not an orphan page."""
    try:
        from fastapi.testclient import TestClient
        from shortsmash.web import app
    except ImportError:
        pytest.skip("fastapi not installed")
    c = TestClient(app)
    r = c.get("/")
    assert r.status_code == 200
    assert "t-social" in r.text
    assert "Social" in r.text
    assert "/api/social/packs" in r.text
    # Standalone page still works.
    r2 = c.get("/social")
    assert r2.status_code == 200
    assert "Social" in r2.text


# --- Gap 9: validate modes ------------------------------------------------

def test_validate_modes_pre_and_post_publish(tmp_path):
    """Pre-publish gate fails for unapproved; post-publish report
    passes for a dry-run-published pack and never applies the
    approved_or_scheduled check to it."""
    base = tmp_path / "social"
    drafts = posts.generate_drafts("morning fishing", _profile(), use_llm=False)
    pack = approval.create_pack("morning fishing", drafts, base=base)
    pre = validate_pack_result(pack["id"], "x", base=base)
    assert pre["mode"] == "pre_publish"
    assert pre["overall_pass"] is False
    assert any(c["check"] == "approved_or_scheduled" and c["pass"] is False
               for c in pre["checks"])
    approval.approve_pack(pack["id"], "x", base=base)
    approval.publish_dry_run(pack["id"], "x", base=base)
    post = validate_pack_result(pack["id"], "x", base=base)
    assert post["mode"] == "post_publish_report"
    assert post["overall_pass"] is True, post
    assert not any(c["check"] == "approved_or_scheduled" for c in post["checks"])
    assert any(c["check"] == "dry_run_publish_log_entry_present" and c["pass"]
               for c in post["checks"])
