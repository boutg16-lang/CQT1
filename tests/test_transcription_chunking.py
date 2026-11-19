# -*- coding: utf-8 -*-
"""Long audio must be transcribed in windows, not as one giant mel array.

The 383 MiB (1, 174490, 576) allocation failure is a ~29-minute audio being
featurised at once. Chunking keeps peak memory proportional to the window.
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import transcription_fallback as tf


def test_chunk_seconds_is_bounded(monkeypatch):
    monkeypatch.delenv("VIRALCUTTER_TRANSCRIBE_CHUNK_SECONDS", raising=False)
    assert tf._chunk_seconds() == tf.DEFAULT_CHUNK_SECONDS
    monkeypatch.setenv("VIRALCUTTER_TRANSCRIBE_CHUNK_SECONDS", "10")
    assert tf._chunk_seconds() == tf.MIN_CHUNK_SECONDS
    monkeypatch.setenv("VIRALCUTTER_TRANSCRIBE_CHUNK_SECONDS", "99999")
    assert tf._chunk_seconds() == tf.MAX_CHUNK_SECONDS
    monkeypatch.setenv("VIRALCUTTER_TRANSCRIBE_CHUNK_SECONDS", "240")
    assert tf._chunk_seconds() == 240


def test_transcribe_offsets_segments_from_each_window(monkeypatch):
    calls = []

    class FakeModel:
        def __init__(self, model_name, device, compute_type):
            self.model_name = model_name

        def transcribe(self, path, **kwargs):
            calls.append((path, kwargs.get("language")))
            if path.endswith("chunk0.wav"):
                segment = SimpleNamespace(
                    start=0.0,
                    end=10.0,
                    text="first",
                    words=[SimpleNamespace(start=0.0, end=2.0, word="first")],
                )
            else:
                segment = SimpleNamespace(start=0.0, end=8.0, text="second", words=None)
            return [segment], SimpleNamespace(language="ar")

    monkeypatch.setattr(tf, "_module", lambda: SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(
        tf,
        "_split_audio",
        lambda path, folder, seconds: [("chunk0.wav", 0.0), ("chunk1.wav", 300.0)],
    )
    result = tf.transcribe("input.mp4", "small", "cpu")
    assert result["chunks"] == 2
    assert result["language"] == "ar"
    assert [segment["start"] for segment in result["segments"]] == [0.0, 300.0]
    assert result["segments"][0]["words"][0]["start"] == 0.0
    assert calls[0][1] is None  # first window auto-detects
    assert calls[1][1] == "ar"  # later windows reuse the language


def test_transcribe_keeps_single_shot_for_short_audio(monkeypatch):
    calls = []

    class FakeModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, path, **kwargs):
            calls.append(path)
            return [SimpleNamespace(start=0.0, end=5.0, text="short")], SimpleNamespace(
                language="en"
            )

    monkeypatch.setattr(tf, "_module", lambda: SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(tf, "_split_audio", lambda path, folder, seconds: [])
    result = tf.transcribe("input.mp4", "small", "cpu")
    assert result["chunks"] == 1
    assert calls == ["input.mp4"]


def test_oom_ladder_retries_with_a_smaller_window(monkeypatch):
    calls = []

    class FakeModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, path, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise MemoryError(
                    "Unable to allocate 383. MiB for an array with shape (1, 174490, 576)"
                )
            return [SimpleNamespace(start=0.0, end=5.0, text="ok")], SimpleNamespace(
                language="ar"
            )

    monkeypatch.setattr(tf, "_module", lambda: SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(
        tf, "_split_audio", lambda path, folder, seconds: [("chunk.wav", 0.0)]
    )
    result = tf.transcribe("input.mp4", "small", "cpu")
    assert len(calls) == 2
    assert result["chunk_seconds"] < tf.DEFAULT_CHUNK_SECONDS
    assert result["segments"][0]["text"] == "ok"
