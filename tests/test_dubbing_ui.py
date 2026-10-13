# -*- coding: utf-8 -*-
"""Tests for the dubbing integration (WebUI wrapper + UI wiring + consent gate).

The dubbing *pipeline* is covered in ``tests/test_dubbing.py``; this file covers
what makes it usable by a person: the ``publish_panel`` wrapper, the explicit
network-consent path (a checkbox, not only an env var), and the presence of the
controls in the Publish tab.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import tts_providers
from webui import publish_panel

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = shutil.which("ffmpeg")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _project(tmp_path, with_translation=False):
    project = tmp_path / "proj"
    (project / "subs").mkdir(parents=True)
    (project / "final").mkdir(parents=True)
    clip = project / "final" / "000_clip.mp4"
    if FFMPEG:
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15:duration=8",
            "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=44100,volume=0.4",
            "-t", "8", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(clip)], check=True)
    else:
        clip.write_bytes(b"\x00" * 4096)
    with open(project / "subs" / "000_clip_processed.json", "w", encoding="utf-8") as handle:
        json.dump({"segments": [{"start": 0.5, "end": 3.0, "text": "نص تجريبي", "words": []}]}, handle)
    if with_translation:
        with open(project / "subs" / "000_clip_processed_translated_en.json",
                  "w", encoding="utf-8") as handle:
            json.dump({"segments": [{"start": 0.5, "end": 3.0, "text": "sample text",
                                     "words": []}]}, handle)
    return project, str(clip)


def _voices(tmp_path, durations=(1.5,)):
    folder = tmp_path / "voices"
    folder.mkdir(exist_ok=True)
    for index, duration in enumerate(durations, start=1):
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency={}:sample_rate=44100,volume=0.5".format(
                500 + index * 50),
            "-t", str(duration), str(folder / "line{}.wav".format(index))], check=True)
    return folder


# ---------------------------------------------------------------------------
# tts_providers: the consent switch
# ---------------------------------------------------------------------------

def test_allow_network_false_overrides_an_enabled_env(tmp_path, monkeypatch):
    monkeypatch.setenv(tts_providers.NETWORK_OPT_IN_ENV, "1")
    result = tts_providers.synthesize("x", str(tmp_path / "a.mp3"), provider="edge",
                                      allow_network=False)
    assert result["ok"] is False
    assert tts_providers.NETWORK_OPT_IN_ENV in result["error"]


def test_allow_network_true_is_explicit_consent(tmp_path, monkeypatch):
    monkeypatch.delenv(tts_providers.NETWORK_OPT_IN_ENV, raising=False)
    result = tts_providers.synthesize("x", str(tmp_path / "a.mp3"), provider="edge",
                                      allow_network=True)
    assert result["ok"] is False
    # refused because the backend is absent / synthesis failed — never for consent
    assert tts_providers.NETWORK_OPT_IN_ENV not in (result["error"] or "")


# ---------------------------------------------------------------------------
# publish_panel wrapper
# ---------------------------------------------------------------------------

def test_dub_clip_rejects_an_unknown_provider(tmp_path):
    project, clip = _project(tmp_path)
    ok, message = publish_panel.dub_clip(str(project), clip, provider="nope")
    assert ok is False
    assert "Unknown TTS provider" in message


def test_dub_clip_requires_explicit_network_consent(tmp_path):
    project, clip = _project(tmp_path)
    ok, message = publish_panel.dub_clip(str(project), clip, provider="edge")
    assert ok is False
    assert "السماح بالشبكة" in message


def test_dub_clip_reports_a_missing_clip(tmp_path):
    project, _ = _project(tmp_path)
    ok, message = publish_panel.dub_clip(str(project), str(tmp_path / "gone.mp4"))
    assert ok is False
    assert "Clip not found" in message


def test_dub_clip_needs_a_voice_folder_for_the_file_provider(tmp_path):
    project, clip = _project(tmp_path)
    ok, message = publish_panel.dub_clip(str(project), clip, provider="file", voice="")
    assert ok is False
    assert "pre-recorded audio" in message


def test_dub_clip_reports_missing_subtitles(tmp_path):
    project, clip = _project(tmp_path)
    os.remove(project / "subs" / "000_clip_processed.json")
    ok, message = publish_panel.dub_clip(str(project), clip, provider="file",
                                         voice=str(tmp_path))
    assert ok is False
    assert "subtitle" in message.lower()


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg required")
def test_dub_clip_end_to_end_writes_final_dubbed(tmp_path):
    project, clip = _project(tmp_path)
    voices = _voices(tmp_path)

    ok, message = publish_panel.dub_clip(str(project), clip, provider="file",
                                         voice=str(voices), mode="replace")
    assert ok is True, message
    assert "final_dubbed" in message or "المقطع المدبلج" in message
    assert "مُصنَّع" in message            # disclosure reminder is always shown

    produced = project / "final_dubbed" / "000_clip_dubbed.mp4"
    assert produced.is_file()
    assert produced.stat().st_size > 0


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg required")
def test_dub_clip_prefers_the_translated_subtitles(tmp_path):
    project, clip = _project(tmp_path, with_translation=True)
    voices = _voices(tmp_path)
    ok, message = publish_panel.dub_clip(str(project), clip, provider="file",
                                         voice=str(voices))
    assert ok is True, message
    # the translated file is the one handed to the pipeline
    subs = publish_panel._translated_or_original_subs(str(project), clip)
    assert "_translated_" in os.path.basename(subs)


def test_translated_subs_fall_back_to_the_clip_subtitles(tmp_path):
    project, clip = _project(tmp_path, with_translation=False)
    subs = publish_panel._translated_or_original_subs(str(project), clip)
    assert subs and subs.endswith("000_clip_processed.json")


# ---------------------------------------------------------------------------
# UI wiring (source-level, same approach as other UI shell tests)
# ---------------------------------------------------------------------------

def test_publish_tab_exposes_the_dubbing_controls():
    source = (ROOT / "webui" / "app.py").read_text(encoding="utf-8")
    for token in ("pub_dub_provider", "pub_dub_mode", "pub_dub_voice",
                  "pub_dub_allow_network", "pub_dub_btn", "pub_dub_out",
                  "dub_publish_clip"):
        assert token in source, token
    # the click is wired to the handler
    assert "pub_dub_btn.click(" in source
    assert "publish_panel.dub_clip(" in source


def test_dubbing_controls_default_to_the_offline_provider():
    source = (ROOT / "webui" / "app.py").read_text(encoding="utf-8")
    start = source.index("pub_dub_provider = gr.Dropdown(")
    block = source[start:start + 400]
    assert 'value="file"' in block          # offline by default
    assert 'value=False' in source[source.index("pub_dub_allow_network = gr.Checkbox("):
                                  source.index("pub_dub_allow_network = gr.Checkbox(") + 200]


def test_dubbing_requirements_are_documented_as_optional():
    req = (ROOT / "requirements-dubbing.txt").read_text(encoding="utf-8")
    assert "edge-tts" in req
    assert "VIRALCUTTER_TTS_ALLOW_NETWORK" in req

    preflight = (ROOT / "scripts" / "preflight.py").read_text(encoding="utf-8")
    assert "requirements-dubbing.txt" in preflight
