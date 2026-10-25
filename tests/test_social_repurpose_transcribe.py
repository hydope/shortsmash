"""Repurpose auto-transcription wiring (2026-10-25 fix).

When no --transcript file is given, `social repurpose` must auto-transcribe
the video via Whisper so clips get scored — not silently leave every
candidate unscored. A supplied --transcript file always wins, and any
transcription failure must fall back to unscored candidates, never crash
or invent a transcript.
"""
from pathlib import Path

import pytest
from typer.testing import CliRunner

from shortsmash import approval, images, profile
from shortsmash import social_cli
from shortsmash.cli import app

runner = CliRunner()

FAKE_SEGMENTS = [{"start": 0.0, "end": 2.5, "text": "hello world"}]


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, "HOME", tmp_path)
    monkeypatch.setattr(approval, "HOME", tmp_path)
    monkeypatch.setattr(images, "HOME", tmp_path)
    return tmp_path


def _run_repurpose(tmp_path, args, monkeypatch, transcribe=None, transcribe_raises=False):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"fake video bytes")
    calls = {}

    def fake_stage(path):
        return {"kind": "video", "staged": str(path)}

    def fake_clips(video, transcript_segments=None, transcript_path=None, **kwargs):
        calls["segments"] = transcript_segments
        calls["path"] = transcript_path
        return {"candidates": [], "note": "mocked"}

    def fake_transcribe(path):
        calls["transcribed"] = True
        if transcribe_raises:
            raise RuntimeError("no whisper here")
        return {"segments": transcribe, "source": "whisper", "words": 2}

    monkeypatch.setattr(social_cli.repurpose, "stage_input", fake_stage)
    monkeypatch.setattr(social_cli.repurpose, "clip_candidates", fake_clips)
    monkeypatch.setattr(social_cli.repurpose, "transcript_from_audio", fake_transcribe)
    result = runner.invoke(app, ["social", "repurpose", str(src), "--topic", "t"] + args)
    return result, calls


def test_auto_transcribes_when_no_transcript(tmp_path, isolated_home, monkeypatch):
    result, calls = _run_repurpose(tmp_path, [], monkeypatch, transcribe=FAKE_SEGMENTS)
    assert result.exit_code == 0, result.output
    assert calls.get("transcribed") is True
    assert calls["segments"] == FAKE_SEGMENTS
    assert calls["path"] is None
    assert "Auto-transcribed" in result.output


def test_transcript_file_wins_over_auto(tmp_path, isolated_home, monkeypatch):
    tfile = tmp_path / "t.txt"
    tfile.write_text("hello from file\n")
    result, calls = _run_repurpose(
        tmp_path, ["--transcript", str(tfile)], monkeypatch, transcribe=FAKE_SEGMENTS)
    assert result.exit_code == 0, result.output
    assert calls.get("transcribed") is None
    assert calls["path"] == tfile
    assert "Auto-transcribed" not in result.output


def test_transcription_failure_falls_back_to_unscored(tmp_path, isolated_home, monkeypatch):
    result, calls = _run_repurpose(tmp_path, [], monkeypatch, transcribe_raises=True)
    assert result.exit_code == 0, result.output
    assert calls["segments"] is None
    assert "Auto-transcription unavailable" in result.output


def test_no_transcribe_flag_skips_whisper(tmp_path, isolated_home, monkeypatch):
    result, calls = _run_repurpose(
        tmp_path, ["--no-transcribe"], monkeypatch, transcribe=FAKE_SEGMENTS)
    assert result.exit_code == 0, result.output
    assert calls.get("transcribed") is None
    assert calls["segments"] is None
