# -*- coding: utf-8 -*-
"""Tests for the dubbing stack (TTS providers + dubbing pipeline).

Everything here is offline: the ``file`` provider copies pre-recorded audio the
test generates with FFmpeg, so the timing math, the fail-open behaviour and the
"no gate bypass" guarantee are all verified without a network or a cloud TTS.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

from scripts import dubbing, tts_providers

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                reason="ffmpeg required")

WINDOW_RE = re.compile(r"mean_volume:\s*(-?[\d.]+) dB")
THRESHOLD = -60.0  # anything quieter than this counts as silence


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

def _clip(path, duration=12.0, frequency=300):
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15:duration={}".format(duration),
        "-f", "lavfi", "-i", "sine=frequency={}:sample_rate=44100,volume=0.4".format(frequency),
        "-t", str(duration), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        str(path)], check=True)
    return str(path)


def _subs(path, segments):
    payload = {"segments": [
        {"start": start, "end": end, "text": text, "words": []}
        for start, end, text in segments]}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    return str(path)


def _voice(folder, durations, start_index=1):
    os.makedirs(folder, exist_ok=True)
    made = []
    for index, duration in enumerate(durations, start=start_index):
        target = os.path.join(folder, "line{}.wav".format(index))
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency={}:sample_rate=44100,volume=0.5".format(
                500 + index * 60),
            "-t", str(duration), target], check=True)
        made.append(target)
    return made


def _mean_volume(path, start, end):
    """Mean volume (dBFS) of a time window — used to prove placement/fitting."""
    proc = subprocess.run([
        "ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info",
        "-ss", "{:.3f}".format(start), "-t", "{:.3f}".format(max(0.05, end - start)),
        "-i", path, "-vn", "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120)
    match = WINDOW_RE.search((proc.stderr or "") + (proc.stdout or ""))
    return float(match.group(1)) if match else -999.0


# ---------------------------------------------------------------------------
# Provider layer
# ---------------------------------------------------------------------------

def test_registry_exposes_both_providers():
    names = tts_providers.provider_names()
    assert "edge" in names and "file" in names
    assert tts_providers.provider_info("edge")["network"] is True
    assert tts_providers.provider_info("file")["network"] is False


def test_unknown_provider_fails_without_raising(tmp_path):
    result = tts_providers.synthesize("hello", str(tmp_path / "a.mp3"), provider="nope")
    assert result["ok"] is False
    assert "unknown TTS provider" in result["error"]


def test_empty_text_is_refused(tmp_path):
    result = tts_providers.synthesize("   ", str(tmp_path / "a.mp3"), provider="file",
                                      voice=str(tmp_path))
    assert result["ok"] is False
    assert "empty text" in result["error"]


def test_network_provider_requires_explicit_opt_in(tmp_path, monkeypatch):
    """The only path that sends text off the machine must be explicit."""
    monkeypatch.delenv(tts_providers.NETWORK_OPT_IN_ENV, raising=False)
    result = tts_providers.synthesize("مرحبا", str(tmp_path / "a.mp3"), provider="edge")
    assert result["ok"] is False
    assert tts_providers.NETWORK_OPT_IN_ENV in result["error"]
    assert result["network"] is True


def test_network_provider_opt_in_reaches_the_backend(tmp_path, monkeypatch):
    monkeypatch.setenv(tts_providers.NETWORK_OPT_IN_ENV, "1")
    result = tts_providers.synthesize("مرحبا", str(tmp_path / "a.mp3"), provider="edge")
    assert result["ok"] is False
    # either edge-tts is missing, or synthesis itself failed — never the opt-in gate
    assert tts_providers.NETWORK_OPT_IN_ENV not in (result["error"] or "")


def test_file_provider_needs_a_folder(tmp_path):
    result = tts_providers.synthesize("x", str(tmp_path / "a.wav"), provider="file",
                                      voice=str(tmp_path / "missing"))
    assert result["ok"] is False
    assert "folder" in result["error"]


def test_file_provider_consumes_files_in_order_then_exhausts(tmp_path):
    folder = tmp_path / "voices"
    _voice(str(folder), [1.0, 1.0])
    first = tts_providers.synthesize("a", str(tmp_path / "1.wav"), provider="file",
                                     voice=str(folder))
    second = tts_providers.synthesize("b", str(tmp_path / "2.wav"), provider="file",
                                      voice=str(folder))
    third = tts_providers.synthesize("c", str(tmp_path / "3.wav"), provider="file",
                                     voice=str(folder))
    assert first["ok"] and second["ok"]
    assert first["voice"] != second["voice"]
    assert third["ok"] is False
    assert "already used" in third["error"]


# ---------------------------------------------------------------------------
# atempo fitting (the maths that keeps the dub on the timeline)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ratio,expected", [
    (1.0, ""),
    (2.0, "atempo=2.0000"),
    (0.5, "atempo=0.5000"),
    (1.333, "atempo=1.3330"),
])
def test_atempo_filter_semantics(ratio, expected):
    chain, warnings = dubbing._atempo_filter(ratio)
    assert chain == expected
    assert warnings == []


def test_atempo_chains_and_clamps_out_of_range():
    chain, warnings = dubbing._atempo_filter(6.0)
    assert chain == "atempo=2.0,atempo=1.5000"
    assert warnings and "clamped" in warnings[0]

    chain, warnings = dubbing._atempo_filter(0.2)
    assert chain == "atempo=0.5000"
    assert warnings


def test_atempo_rejects_a_zero_ratio():
    chain, warnings = dubbing._atempo_filter(0)
    assert chain == ""
    assert warnings


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def test_plan_reports_the_work(tmp_path):
    clip = _clip(tmp_path / "clip.mp4", 10)
    subs = _subs(tmp_path / "s.json", [(0.5, 3.0, "أهلاً"), (5.0, 8.0, "شكراً")])
    plan = dubbing.plan_dub(clip, subs, provider="file", voice=str(tmp_path))
    assert plan["ok"] is True
    assert plan["segment_count"] == 2
    assert plan["mode"] == "replace"
    assert plan["provider_network"] is False
    assert plan["duration"] == pytest.approx(10.0, abs=0.3)


@pytest.mark.parametrize("kwargs,fragment", [
    ({"provider": "nope"}, "unknown TTS provider"),
    ({"mode": "sing"}, "mode must be"),
])
def test_plan_rejects_bad_configuration(tmp_path, kwargs, fragment):
    clip = _clip(tmp_path / "clip.mp4", 3)
    subs = _subs(tmp_path / "s.json", [(0.0, 1.0, "x")])
    plan = dubbing.plan_dub(clip, subs, **kwargs)
    assert plan["ok"] is False
    assert fragment in plan["error"]


def test_plan_rejects_missing_inputs(tmp_path):
    subs = _subs(tmp_path / "s.json", [(0.0, 1.0, "x")])
    assert dubbing.plan_dub(str(tmp_path / "gone.mp4"), subs)["ok"] is False
    clip = _clip(tmp_path / "clip.mp4", 3)
    assert dubbing.plan_dub(clip, str(tmp_path / "gone.json"))["ok"] is False


def test_plan_ignores_unusable_subtitle_entries(tmp_path):
    clip = _clip(tmp_path / "clip.mp4", 6)
    subs = _subs(tmp_path / "s.json", [(0.0, 0.0, "zero width"), (1.0, 2.0, "")])
    assert dubbing.plan_dub(clip, subs)["ok"] is False


# ---------------------------------------------------------------------------
# Dubbing a clip — verified by measuring the actual output audio
# ---------------------------------------------------------------------------

def test_dub_clip_places_audio_in_the_subtitle_windows(tmp_path):
    clip = _clip(tmp_path / "clip.mp4", 12)
    subs = _subs(tmp_path / "s.json", [(0.5, 3.5, "السطر الأول"), (5.0, 9.5, "السطر الثاني")])
    voices = tmp_path / "voices"
    _voice(str(voices), [2.5, 4.0])          # both fit their windows
    out = str(tmp_path / "clip_dubbed.mp4")

    result = dubbing.dub_clip(clip, subs, out, provider="file", voice=str(voices))
    assert result["ok"] is True, result.get("error")
    assert result["segments_dubbed"] == 2
    assert result["segments_failed"] == 0
    assert result["synthetic_voice"] is True
    assert result["disclosure_required"] is True

    # duration is preserved
    assert dubbing.probe_duration(out) == pytest.approx(12.0, abs=0.5)

    # the dub is audible exactly where the subtitles are...
    assert _mean_volume(out, 0.8, 3.2) > THRESHOLD
    assert _mean_volume(out, 5.3, 9.2) > THRESHOLD
    # ...and absent in the gaps and after the last line (replace mode)
    assert _mean_volume(out, 3.9, 4.7) < THRESHOLD
    assert _mean_volume(out, 10.2, 11.8) < THRESHOLD


def test_dub_clip_fits_audio_longer_than_its_window(tmp_path):
    """A 3x-too-long line must be sped up, not allowed to bleed over the next."""
    clip = _clip(tmp_path / "clip.mp4", 12)
    subs = _subs(tmp_path / "s.json", [(0.5, 2.5, "طويل"), (5.0, 8.0, "الثاني")])
    voices = tmp_path / "voices"
    _voice(str(voices), [6.0, 2.0])          # 6s into a 2s window → 3x
    out = str(tmp_path / "clip_dubbed.mp4")

    result = dubbing.dub_clip(clip, subs, out, provider="file", voice=str(voices))
    assert result["ok"] is True
    assert "atempo" in " ".join(result["command"])
    # the long line stays inside its window: the gap after it is silent
    assert _mean_volume(out, 3.0, 4.6) < THRESHOLD
    assert _mean_volume(out, 0.7, 2.3) > THRESHOLD


def test_dub_clip_mix_mode_keeps_the_original_bed(tmp_path):
    clip = _clip(tmp_path / "clip.mp4", 10)
    subs = _subs(tmp_path / "s.json", [(0.5, 3.0, "جملة")])
    voices = tmp_path / "voices"
    _voice(str(voices), [2.0])
    out = str(tmp_path / "clip_dubbed.mp4")

    result = dubbing.dub_clip(clip, subs, out, provider="file", voice=str(voices),
                              mode="mix", original_volume=0.2)
    assert result["ok"] is True
    assert result["mode"] == "mix"
    # replace mode leaves this window silent; mix keeps the original tone audible
    assert _mean_volume(out, 6.0, 8.0) > THRESHOLD


def test_dub_clip_fails_open_when_no_segment_can_be_synthesised(tmp_path):
    clip = _clip(tmp_path / "clip.mp4", 6)
    subs = _subs(tmp_path / "s.json", [(0.5, 2.0, "سطر")])
    out = str(tmp_path / "clip_dubbed.mp4")

    result = dubbing.dub_clip(clip, subs, out, provider="file",
                              voice=str(tmp_path / "empty-voices"))
    assert result["ok"] is False
    assert "no segment could be synthesised" in result["error"]
    assert not os.path.exists(out), "a failed dub must not leave a partial file"


def test_dub_clip_reports_partial_failures(tmp_path):
    clip = _clip(tmp_path / "clip.mp4", 10)
    subs = _subs(tmp_path / "s.json", [(0.5, 2.5, "أول"), (5.0, 8.0, "ثاني")])
    voices = tmp_path / "voices"
    _voice(str(voices), [1.5])               # only one line available
    out = str(tmp_path / "clip_dubbed.mp4")

    result = dubbing.dub_clip(clip, subs, out, provider="file", voice=str(voices))
    assert result["ok"] is True               # partial success still ships a clip
    assert result["segments_dubbed"] == 1
    assert result["segments_failed"] == 1
    assert result["failures"][0]["index"] == 1


def test_dub_clip_rejects_unusable_subtitles_without_touching_the_clip(tmp_path):
    clip = _clip(tmp_path / "clip.mp4", 6)
    subs = _subs(tmp_path / "s.json", [])
    out = str(tmp_path / "clip_dubbed.mp4")
    result = dubbing.dub_clip(clip, subs, out, provider="file", voice=str(tmp_path))
    assert result["ok"] is False
    assert not os.path.exists(out)


# ---------------------------------------------------------------------------
# Project level + no gate bypass
# ---------------------------------------------------------------------------

def _project(tmp_path):
    project = tmp_path / "proj"
    (project / "subs").mkdir(parents=True)
    (project / "final").mkdir(parents=True)
    _clip(project / "final" / "000_clip.mp4", 8)
    _subs(project / "subs" / "000_clip_processed.json", [(0.5, 3.0, "نص")])
    return project


def test_dub_project_writes_report_and_clips(tmp_path):
    project = _project(tmp_path)
    voices = tmp_path / "voices"
    _voice(str(voices), [2.0])

    report = dubbing.dub_project(str(project), provider="file", voice=str(voices))
    assert report["ok"] is True
    assert report["clips"] == 1 and report["dubbed"] == 1
    assert report["synthetic_voice"] is True and report["disclosure_required"] is True
    assert os.path.isfile(report["report"])
    on_disk = json.loads(open(report["report"], encoding="utf-8").read())
    assert on_disk["dubbed"] == 1
    assert os.path.isfile(os.path.join(project, "final_dubbed", "000_clip_dubbed.mp4"))


def test_dub_project_reports_clips_without_subtitles(tmp_path):
    project = _project(tmp_path)
    os.remove(project / "subs" / "000_clip_processed.json")
    report = dubbing.dub_project(str(project), provider="file", voice=str(tmp_path))
    assert report["ok"] is False
    assert "no subtitle JSON" in report["results"][0]["error"]


def test_dub_project_missing_folder(tmp_path):
    assert dubbing.dub_project(str(tmp_path / "nope"))["ok"] is False


def test_dubbing_never_touches_the_publish_gate(tmp_path, monkeypatch):
    """Dubbing writes files only: it must not reach the gate or publishing."""
    from scripts import upload_gate

    calls = []
    monkeypatch.setattr(upload_gate, "check_clip",
                        lambda *a, **k: calls.append("check_clip") or {"allowed": False})
    monkeypatch.setattr(upload_gate, "gate_upload",
                        lambda *a, **k: calls.append("gate_upload"))

    project = _project(tmp_path)
    voices = tmp_path / "voices"
    _voice(str(voices), [2.0])
    dubbing.dub_project(str(project), provider="file", voice=str(voices))
    assert calls == []
