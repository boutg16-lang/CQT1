# -*- coding: utf-8 -*-
"""Direct tests for scripts/tts_providers.py.

Covers the provider registry, the network opt-in gate, the routing in
``synthesize`` (never raises), the offline ``file`` provider (tmp dirs),
and the ``edge`` provider with a stubbed edge-tts module (no network).
"""

import os
import sys
import types
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import tts_providers as tp  # noqa: E402

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_provider_names_sorted(self):
        assert tp.provider_names() == ["edge", "file"]

    def test_provider_info_case_insensitive(self):
        info = tp.provider_info("EDGE")
        assert info == {
            "name": "edge",
            "network": True,
            "description": "Microsoft Edge TTS — free, no API key, sends text to Microsoft",
        }
        assert "synthesize" not in info

    def test_provider_info_file(self):
        assert tp.provider_info("file")["network"] is False

    def test_provider_info_unknown(self):
        assert tp.provider_info("nope") is None
        assert tp.provider_info("") is None
        assert tp.provider_info(None) is None

    def test_register_custom_provider(self):
        @tp.register("dummy-test", network=False, description="test only")
        def _dummy(text, out_path, *, voice=None, rate=None, timeout=120):
            return {
                "ok": True,
                "path": out_path,
                "provider": "dummy-test",
                "voice": voice,
                "network": False,
                "duration": 1.0,
                "error": None,
            }

        try:
            assert "dummy-test" in tp.provider_names()
            assert tp.provider_info("dummy-test")["description"] == "test only"
            result = tp.synthesize("hello", "/tmp/x.wav", provider="dummy-test")
            assert result["ok"] is True
            assert result["duration"] == 1.0
        finally:
            tp._PROVIDERS.pop("dummy-test", None)


# ---------------------------------------------------------------------------
# Network gate and routing
# ---------------------------------------------------------------------------


class TestNetworkGate:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("1", True),
            (" 1 ", True),
            ("0", False),
            ("yes", False),
            ("", False),
            (None, False),
        ],
    )
    def test_network_allowed(self, monkeypatch, value, expected):
        if value is None:
            monkeypatch.delenv(tp.NETWORK_OPT_IN_ENV, raising=False)
        else:
            monkeypatch.setenv(tp.NETWORK_OPT_IN_ENV, value)
        assert tp._network_allowed() is expected


class TestSynthesizeRouting:
    def test_unknown_provider(self):
        result = tp.synthesize("hi", "/tmp/x.wav", provider="zzz")
        assert result["ok"] is False
        assert result["path"] is None
        assert "unknown TTS provider 'zzz'" in result["error"]
        assert "edge, file" in result["error"]

    def test_empty_text(self):
        for text in ("", "   ", None):
            result = tp.synthesize(text, "/tmp/x.wav", provider="file", voice="/tmp")
            assert result["ok"] is False
            assert result["error"] == "empty text"

    def test_network_provider_refused_without_opt_in(self, monkeypatch):
        monkeypatch.delenv(tp.NETWORK_OPT_IN_ENV, raising=False)
        result = tp.synthesize("hi", "/tmp/x.wav", provider="edge")
        assert result["ok"] is False
        assert "VIRALCUTTER_TTS_ALLOW_NETWORK=1" in result["error"]
        assert result["network"] is True

    def test_allow_network_false_overrides_env(self, monkeypatch):
        monkeypatch.setenv(tp.NETWORK_OPT_IN_ENV, "1")
        result = tp.synthesize("hi", "/tmp/x.wav", provider="edge", allow_network=False)
        assert result["ok"] is False
        assert "external service" in result["error"]

    def test_allow_network_true_bypasses_env(self, monkeypatch):
        monkeypatch.delenv(tp.NETWORK_OPT_IN_ENV, raising=False)
        # edge-tts is not installed here, so the provider itself fails —
        # but the gate must NOT be the reason.
        result = tp.synthesize("hi", "/tmp/x.wav", provider="edge", allow_network=True)
        assert result["ok"] is False
        assert "edge-tts is not installed" in result["error"]

    def test_provider_exception_becomes_failure(self):
        @tp.register("boom-test", network=False)
        def _boom(text, out_path, *, voice=None, rate=None, timeout=120):
            raise ValueError("kaboom")

        try:
            result = tp.synthesize("hi", "/tmp/x.wav", provider="boom-test")
            assert result["ok"] is False
            assert result["error"] == "ValueError: kaboom"
            assert result["duration"] == 0.0
        finally:
            tp._PROVIDERS.pop("boom-test", None)


class TestResultHelpers:
    def test_failure_shape(self):
        result = tp._failure("edge", "something broke")
        assert result == {
            "ok": False,
            "path": None,
            "provider": "edge",
            "voice": None,
            "network": True,
            "duration": 0.0,
            "error": "something broke",
        }

    def test_failure_truncates_long_message(self):
        result = tp._failure("file", "x" * 500)
        assert len(result["error"]) == 400

    def test_duration_parses_ffprobe_output(self, monkeypatch):
        monkeypatch.setattr(
            tp.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="3.25\n")
        )
        assert tp._duration("/tmp/x.wav") == 3.25

    def test_duration_bad_output_is_zero(self, monkeypatch):
        monkeypatch.setattr(
            tp.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(stdout="not-a-number\n"),
        )
        assert tp._duration("/tmp/x.wav") == 0.0

    def test_duration_negative_clamped(self, monkeypatch):
        monkeypatch.setattr(
            tp.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="-2.5\n")
        )
        assert tp._duration("/tmp/x.wav") == 0.0

    def test_duration_subprocess_failure_is_zero(self, monkeypatch):
        def _raise(*a, **k):
            raise OSError("no ffprobe")

        monkeypatch.setattr(tp.subprocess, "run", _raise)
        assert tp._duration("/tmp/x.wav") == 0.0


# ---------------------------------------------------------------------------
# file provider
# ---------------------------------------------------------------------------


def _make_voice_dir(base, files):
    voice = base / "voice"
    voice.mkdir()
    for name, content in files.items():
        (voice / name).write_bytes(content)
    return voice


class TestFileProvider:
    def test_picks_sorted_first_and_marks_used(self, tmp_path):
        voice = _make_voice_dir(
            tmp_path, {"b.wav": b"B", "a.wav": b"A", "notes.txt": b"ignore me"}
        )
        out = tmp_path / "out.wav"
        result = tp.synthesize("hello", str(out), provider="file", voice=str(voice))
        assert result["ok"] is True
        assert result["path"] == str(out)
        assert result["voice"] == "a.wav"
        assert result["network"] is False
        assert out.read_bytes() == b"A"
        assert (voice / ".used").read_text(encoding="utf-8") == "a.wav\n"

    def test_second_call_picks_next_unused(self, tmp_path):
        voice = _make_voice_dir(tmp_path, {"a.wav": b"A", "b.wav": b"B"})
        out = tmp_path / "out.wav"
        tp.synthesize("one", str(out), provider="file", voice=str(voice))
        second = tp.synthesize("two", str(out), provider="file", voice=str(voice))
        assert second["ok"] is True
        assert second["voice"] == "b.wav"

    def test_all_used_reports_failure(self, tmp_path):
        voice = _make_voice_dir(tmp_path, {"a.wav": b"A"})
        out = tmp_path / "out.wav"
        tp.synthesize("one", str(out), provider="file", voice=str(voice))
        result = tp.synthesize("two", str(out), provider="file", voice=str(voice))
        assert result["ok"] is False
        assert "already used" in result["error"]

    def test_missing_folder(self, tmp_path):
        result = tp.synthesize(
            "hi", str(tmp_path / "o.wav"), provider="file", voice=str(tmp_path / "nope")
        )
        assert result["ok"] is False
        assert "existing folder" in result["error"]

    def test_no_audio_files(self, tmp_path):
        voice = _make_voice_dir(tmp_path, {"notes.txt": b"x"})
        result = tp.synthesize(
            "hi", str(tmp_path / "o.wav"), provider="file", voice=str(voice)
        )
        assert result["ok"] is False
        assert "no audio files" in result["error"]

    def test_suffix_preserved_on_output(self, tmp_path):
        voice = _make_voice_dir(tmp_path, {"a.mp3": b"MP3"})
        out = tmp_path / "dub.wav"  # requested .wav, source is .mp3
        result = tp.synthesize("hi", str(out), provider="file", voice=str(voice))
        assert result["ok"] is True
        assert result["path"] == str(tmp_path / "dub.mp3")
        assert (tmp_path / "dub.mp3").read_bytes() == b"MP3"

    def test_pre_existing_used_marker_respected(self, tmp_path):
        voice = _make_voice_dir(tmp_path, {"a.wav": b"A", "b.wav": b"B"})
        (voice / ".used").write_text("a.wav\n", encoding="utf-8")
        out = tmp_path / "out.wav"
        result = tp.synthesize("hi", str(out), provider="file", voice=str(voice))
        assert result["voice"] == "b.wav"


# ---------------------------------------------------------------------------
# edge provider (stubbed edge-tts, no network)
# ---------------------------------------------------------------------------


def _install_fake_edge_tts(monkeypatch, save_behaviour="ok"):
    fake = types.ModuleType("edge_tts")

    class _Communicate:
        def __init__(self, text, voice, rate=None):
            self.text = text
            self.voice = voice
            self.rate = rate

        async def save(self, path):
            if save_behaviour == "raise":
                raise RuntimeError("network down")
            with open(path, "wb") as f:
                f.write(b"FAKEAUDIO")

    fake.Communicate = _Communicate
    monkeypatch.setitem(sys.modules, "edge_tts", fake)
    return fake


class TestEdgeProvider:
    def test_happy_path_with_stubbed_edge_tts(self, tmp_path, monkeypatch):
        _install_fake_edge_tts(monkeypatch)
        out = tmp_path / "dub.wav"
        result = tp.synthesize(
            "hello",
            str(out),
            provider="edge",
            voice="ar-EG-ShakirNeural",
            allow_network=True,
        )
        assert result["ok"] is True
        assert result["provider"] == "edge"
        assert result["voice"] == "ar-EG-ShakirNeural"
        assert result["network"] is True
        assert out.read_bytes() == b"FAKEAUDIO"

    def test_default_voice_from_env(self, tmp_path, monkeypatch):
        fake = _install_fake_edge_tts(monkeypatch)
        seen = {}

        class _Spy(fake.Communicate):
            def __init__(self, text, voice, rate=None):
                seen["voice"] = voice
                super().__init__(text, voice, rate)

        fake.Communicate = _Spy
        monkeypatch.setenv("VIRALCUTTER_TTS_VOICE", "en-US-AriaNeural")
        out = tmp_path / "dub.wav"
        result = tp.synthesize("hello", str(out), provider="edge", allow_network=True)
        assert result["ok"] is True
        assert seen["voice"] == "en-US-AriaNeural"

    def test_save_failure_becomes_failure(self, tmp_path, monkeypatch):
        _install_fake_edge_tts(monkeypatch, save_behaviour="raise")
        result = tp.synthesize(
            "hello", str(tmp_path / "dub.wav"), provider="edge", allow_network=True
        )
        assert result["ok"] is False
        assert "synthesis failed" in result["error"]
        assert "network down" in result["error"]

    def test_empty_output_file_is_failure(self, tmp_path, monkeypatch):
        fake = types.ModuleType("edge_tts")

        class _Communicate:
            def __init__(self, text, voice, rate=None):
                pass

            async def save(self, path):
                open(path, "wb").close()  # zero bytes

        fake.Communicate = _Communicate
        monkeypatch.setitem(sys.modules, "edge_tts", fake)
        result = tp.synthesize(
            "hello", str(tmp_path / "dub.wav"), provider="edge", allow_network=True
        )
        assert result["ok"] is False
        assert "no audio" in result["error"]
