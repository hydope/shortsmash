"""ShortSmash Social 0.4 tests. Offline only: no paid API, no live post,
no network call is made by any test here (template provider + template
drafts, with LLM explicitly disabled unless a test says otherwise)."""
import json

import pytest

from shortsmash import approval, images, posts, profile as prof, repurpose
from shortsmash.config import Config


def _profile(**over):
    p = prof.empty_profile()
    p.update(over)
    return p


def test_profile_crud_and_employer_opt_in_default_off(tmp_path):
    base = tmp_path / "social"
    assert prof.load_profile(base) is None
    saved = prof.set_profile(base, name="Demo", company="Acme Corp")
    assert saved["employer_opt_in"] is False  # default off, even with company set
    assert prof.load_profile(base)["name"] == "Demo"
    assert prof.delete_profile(base) is True
    assert prof.load_profile(base) is None


def test_employer_mentioned_only_with_opt_in(tmp_path):
    base = tmp_path / "social"
    off = _profile(company="Acme Corp", employer_opt_in=False)
    drafts = posts.generate_drafts("morning fishing", off, use_llm=False)
    assert all("Acme Corp" not in d["text"]
               for items in drafts["platforms"].values() for d in items)
    on = _profile(company="Acme Corp", employer_opt_in=True)
    drafts_on = posts.generate_drafts("morning fishing", on, use_llm=False)
    linkedin = drafts_on["platforms"]["linkedin"]
    assert any("Acme Corp" in d["text"] for d in linkedin)


def test_do_not_say_filtering():
    p = _profile(do_not_say=["Acme Corp", "secret sauce"])
    drafts = posts.generate_drafts("Acme Corp morning fishing with secret sauce",
                                   p, use_llm=False)
    for items in drafts["platforms"].values():
        for d in items:
            assert "Acme Corp" not in d["text"]
            assert "secret sauce" not in d["text"]


def test_empty_profile_invents_nothing():
    drafts = posts.generate_drafts("morning fishing catch", None, use_llm=False)
    assert drafts["inferred"] is True
    for items in drafts["platforms"].values():
        for d in items:
            assert d["inferred"] is True
            assert d["source"] == "template"
            # No person, job, or company is invented from an empty profile.
            assert "property manager" not in d["text"].lower()
            assert "Acme" not in d["text"]


def test_drafts_differ_per_platform_and_have_three_variants():
    drafts = posts.generate_drafts("morning fishing catch", _profile(tone="plain"),
                                   use_llm=False)
    assert set(drafts["platforms"]) == set(posts.PLATFORMS)
    for items in drafts["platforms"].values():
        assert len(items) == 3
        assert len({d["text"] for d in items}) == 3
    texts = {items[0]["text"] for items in drafts["platforms"].values()}
    assert len(texts) == len(posts.PLATFORMS)


def test_fact_gaps_not_invented():
    drafts = posts.generate_drafts("new course price and date announced",
                                   _profile(), use_llm=False)
    assert any("price" in g for g in drafts["fact_gaps"])
    assert any("date" in g for g in drafts["fact_gaps"])
    for items in drafts["platforms"].values():
        for d in items:
            assert "$" not in d["text"]


def test_image_cost_preview_and_daily_cap(tmp_path):
    preview = images.cost_preview("gemini", 4, 5.0)
    assert preview["per_image_usd"] == 0.0336
    assert preview["estimated_total_usd"] == pytest.approx(0.1344)
    assert preview["within_cap"] is True
    # Cap enforcement happens before any provider call.
    with pytest.raises(RuntimeError, match="cap"):
        images.generate("test", provider="gemini", daily_cap_usd=0.05,
                        out_dir=tmp_path, social_base=tmp_path / "social")


def test_gemini_and_pollinations_fail_clearly_without_spending(tmp_path):
    for provider in ("gemini", "pollinations"):
        with pytest.raises(RuntimeError, match="NOT configured"):
            images.generate("test", provider=provider, daily_cap_usd=5.0,
                            out_dir=tmp_path, social_base=tmp_path / "social")


def test_template_images_return_four_candidates(tmp_path):
    result = images.generate("morning fishing", provider="template", size="feed",
                             out_dir=tmp_path / "imgs",
                             social_base=tmp_path / "social")
    assert len(result["candidates"]) == 4
    assert result["cost_preview"]["estimated_total_usd"] == 0.0
    for c in result["candidates"]:
        assert c["dimensions"] == (1080, 1350)
        assert "not AI generated" in c["note"] or "Template" in c["note"]
        from pathlib import Path
        assert Path(c["path"]).exists()


def test_stock_fallback_is_labelled_not_a_photo(tmp_path):
    result = images.generate("morning fishing", provider="stock",
                             out_dir=tmp_path / "imgs",
                             social_base=tmp_path / "social")
    assert "stock_unavailable" in result["note"]
    assert all(c["provider"] == "template" for c in result["candidates"])


def test_unapproved_draft_cannot_publish(tmp_path):
    base = tmp_path / "social"
    drafts = posts.generate_drafts("morning fishing", _profile(), use_llm=False)
    pack = approval.create_pack("morning fishing", drafts, base=base)
    with pytest.raises(PermissionError, match="not approved"):
        approval.publish_dry_run(pack["id"], "x", base=base)
    assert approval.publish_log(base) == []


def test_idempotent_dry_run_publish(tmp_path):
    base = tmp_path / "social"
    drafts = posts.generate_drafts("morning fishing", _profile(), use_llm=False)
    pack = approval.create_pack("morning fishing", drafts, base=base)
    approval.approve_pack(pack["id"], "x", base=base)
    first = approval.publish_dry_run(pack["id"], "x", base=base)
    second = approval.publish_dry_run(pack["id"], "x", base=base)
    assert first["dry_run"] is True and first["live"] is False
    assert second["duplicate"] is True
    assert second["idempotency_key"] == first["idempotency_key"]
    assert len(approval.publish_log(base)) == 1


def test_approve_for_one_platform_only(tmp_path):
    base = tmp_path / "social"
    drafts = posts.generate_drafts("morning fishing", _profile(), use_llm=False)
    pack = approval.create_pack("morning fishing", drafts, base=base)
    approval.approve_pack(pack["id"], "bluesky", base=base)
    pack = approval.get_pack(pack["id"], base=base)
    by_platform = {d["platform"]: d["status"] for d in pack["drafts"]}
    assert by_platform["bluesky"] == "approved"
    assert by_platform["x"] == "draft"
    with pytest.raises(PermissionError):
        approval.publish_dry_run(pack["id"], "x", base=base)


def test_clip_candidates_explicitly_unscored_without_transcript(tmp_path):
    # No real video file: duration unknown, so no fake segments or scores.
    result = repurpose.clip_candidates(tmp_path / "missing.mp4")
    assert result["candidates"] == []
    assert "No transcript was invented" in result["note"]


def test_clip_candidates_with_transcript_are_scored_from_it(tmp_path):
    # Build a tiny real video with ffmpeg if available, else skip honestly.
    import shutil
    import subprocess
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe not available in this environment")
    video = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "color=c=black:s=320x240:d=90", "-pix_fmt", "yuv420p",
                    str(video)], check=True)
    transcript = [{"start": 5.0, "text": "What is the secret?"},
                  {"start": 40.0, "text": "Here is the catch."},
                  {"start": 70.0, "text": "Thanks for watching."}]
    result = repurpose.clip_candidates(video, transcript)
    assert len(result["candidates"]) == 3
    for c in result["candidates"]:
        assert c["score"] is None  # no virality score is ever claimed
        assert "unscored" in str(c["score_breakdown"]) or c["scored"] is True
    assert result["candidates"][0]["score_breakdown"]["speech_words_in_segment"] >= 3
