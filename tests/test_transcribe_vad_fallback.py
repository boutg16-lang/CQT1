# -*- coding: utf-8 -*-
"""WhisperX VAD dependency failures must not kill transcription.

When pyannote cannot import asteroid_filterbanks (or any VAD dependency),
the pipeline switches to the independent faster-whisper backend instead of
raising the raw ModuleNotFoundError after the user waited for model load.
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import transcribe_video as tv


def test_vad_dependency_errors_are_recognized():
    assert tv._is_vad_dependency_error(
        ModuleNotFoundError("No module named 'asteroid_filterbanks'"))
    assert tv._is_vad_dependency_error(
        ImportError("cannot import name Encoder from asteroid_filterbanks"))
    assert tv._is_vad_dependency_error(
        RuntimeError("pyannote.audio failed to load the VAD model"))
    assert not tv._is_vad_dependency_error(ValueError("Invalid model size"))


def test_transcribe_switches_to_faster_whisper_on_vad_error(tmp_path, monkeypatch):
    audio = tmp_path / "input.mp3"
    audio.write_bytes(b"fake")

    class FakeTorch:
        __version__ = "2.8.0"
        cuda = SimpleNamespace(is_available=lambda: False)

    monkeypatch.setattr(tv, "torch", FakeTorch())
    monkeypatch.setattr(tv, "whisperx",
                        SimpleNamespace(load_audio=lambda path: object()))
    monkeypatch.setattr(tv, "_cache_matches", lambda *a, **k: False)
    monkeypatch.setattr(tv, "_transcript_artifacts_ok", lambda *a, **k: False)
    monkeypatch.setattr(tv, "apply_safe_globals_hack", lambda: None)
    monkeypatch.setattr(tv, "_log_memory_state", lambda *a, **k: None)

    def broken_load(*args, **kwargs):
        raise ModuleNotFoundError("No module named 'asteroid_filterbanks'")

    monkeypatch.setattr(tv, "_load_whisperx_model", broken_load)

    called = {}

    def fake_fallback(input_file, model_name, project_folder, srt_file,
                      tsv_file, json_file, cache_path, device, reason="memory"):
        called["reason"] = reason
        called["model"] = model_name
        return srt_file, tsv_file

    monkeypatch.setattr(tv, "_transcribe_via_fallback", fake_fallback)

    srt, tsv = tv.transcribe(str(audio), "small", str(tmp_path), "cpu")
    assert called["reason"] == "vad"
    assert called["model"] == "small"
    assert srt.endswith(".srt") and tsv.endswith(".tsv")
