"""Direct tests for scripts/audio_analysis.py.

Only get_audio_energy is public; ffmpeg is never run for real — the
subprocess.Popen call is stubbed to return fabricated PCM bytes.
"""

import subprocess

import numpy as np
import pytest

from scripts import audio_analysis


def _pcm_bytes(samples):
    return np.asarray(samples, dtype=np.int16).tobytes()


class _FakePopen:
    """Stub for subprocess.Popen returning canned PCM."""

    def __init__(self, raw=b"", returncode=0, calls=None):
        self._raw = raw
        self.returncode = returncode
        self.calls = calls

    def communicate(self):
        return self._raw, None


def _stub_popen(monkeypatch, raw=b"", returncode=0):
    calls = []

    def fake_popen(cmd, **kwargs):
        calls.append(cmd)
        return _FakePopen(raw, returncode, calls)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return calls


def test_invalid_fps_returns_none_without_spawning(monkeypatch):
    calls = _stub_popen(monkeypatch, _pcm_bytes([100] * 16000))
    assert audio_analysis.get_audio_energy("clip.mp4", None) is None
    assert audio_analysis.get_audio_energy("clip.mp4", 0) is None
    assert audio_analysis.get_audio_energy("clip.mp4", -5) is None
    assert audio_analysis.get_audio_energy("clip.mp4", 0.0) is None
    assert calls == []


def test_string_fps_is_accepted(monkeypatch):
    _stub_popen(monkeypatch, _pcm_bytes([100] * 16000))
    energies = audio_analysis.get_audio_energy("clip.mp4", "10")
    assert energies is not None
    assert len(energies) == 10


def test_nonzero_returncode_returns_none(monkeypatch):
    _stub_popen(monkeypatch, _pcm_bytes([100] * 16000), returncode=1)
    assert audio_analysis.get_audio_energy("clip.mp4", 10) is None


def test_empty_audio_returns_none(monkeypatch):
    _stub_popen(monkeypatch, b"")
    assert audio_analysis.get_audio_energy("clip.mp4", 10) is None


def test_odd_length_pcm_returns_none(monkeypatch):
    # np.frombuffer(int16) on 3 bytes raises; the function must fail closed.
    _stub_popen(monkeypatch, b"\x01\x02\x03")
    assert audio_analysis.get_audio_energy("clip.mp4", 10) is None


def test_popen_failure_returns_none(monkeypatch):
    def boom(cmd, **kwargs):
        raise OSError("ffmpeg not found")

    monkeypatch.setattr(subprocess, "Popen", boom)
    assert audio_analysis.get_audio_energy("clip.mp4", 10) is None


def test_ffmpeg_command_extracts_mono_16k_pcm(monkeypatch):
    calls = _stub_popen(monkeypatch, _pcm_bytes([100] * 16000))
    audio_analysis.get_audio_energy("clip.mp4", 10)
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0] == "ffmpeg"
    assert "-vn" in cmd and "-ac" in cmd and "1" in cmd
    assert "-ar" in cmd and "16000" in cmd
    assert "-f" in cmd and "s16le" in cmd
    assert "clip.mp4" in cmd


def test_frame_count_comes_from_fps(monkeypatch):
    # 16000 samples at fps 10 -> 1600 samples/frame -> 10 frames.
    _stub_popen(monkeypatch, _pcm_bytes([100] * 16000))
    energies = audio_analysis.get_audio_energy("clip.mp4", 10)
    assert len(energies) == 10
    _stub_popen(monkeypatch, _pcm_bytes([100] * 16000))
    energies = audio_analysis.get_audio_energy("clip.mp4", 20)
    assert len(energies) == 20


def test_partial_final_frame_is_dropped(monkeypatch):
    # 17000 samples at fps 10 -> 10 full frames, 1000 samples dropped.
    _stub_popen(monkeypatch, _pcm_bytes([100] * 17000))
    energies = audio_analysis.get_audio_energy("clip.mp4", 10)
    assert len(energies) == 10


def test_silence_then_speech_normalizes_to_zero_and_one(monkeypatch):
    samples = [0] * 8000 + [10000] * 8000
    _stub_popen(monkeypatch, _pcm_bytes(samples))
    energies = audio_analysis.get_audio_energy("clip.mp4", 10)
    assert energies is not None
    assert energies[0] == pytest.approx(0.0)
    assert energies[-1] == pytest.approx(1.0)
    assert energies.max() == pytest.approx(1.0)
    assert energies.min() == pytest.approx(0.0)


def test_constant_signal_normalizes_to_all_zero(monkeypatch):
    # No dynamic range: noise floor == speech scale, so every frame is 0.
    _stub_popen(monkeypatch, _pcm_bytes([5000] * 16000))
    energies = audio_analysis.get_audio_energy("clip.mp4", 10)
    assert energies is not None
    assert np.all(energies == 0.0)


def test_pure_silence_returns_all_zero(monkeypatch):
    _stub_popen(monkeypatch, _pcm_bytes([0] * 16000))
    energies = audio_analysis.get_audio_energy("clip.mp4", 10)
    assert energies is not None
    assert np.all(energies == 0.0)


def test_transient_does_not_dominate_normalization(monkeypatch):
    # Two speech levels plus one loud click frame: the 95th-percentile
    # scale keeps the mid-level speech visible instead of squashing it
    # to ~0 the way dividing by the absolute peak would.
    samples = [2000] * 6400 + [4000] * 6400 + [30000] * 3200
    _stub_popen(monkeypatch, _pcm_bytes(samples))
    energies = audio_analysis.get_audio_energy("clip.mp4", 10)
    assert energies is not None
    assert energies.max() == pytest.approx(1.0)
    assert energies[0] == pytest.approx(0.0)  # quiet floor removed
    assert energies[5] > 0.05  # mid-level speech stays visible


def test_energies_are_clipped_to_unit_range(monkeypatch):
    rng = np.random.default_rng(7)
    samples = rng.integers(-30000, 30000, size=32000)
    _stub_popen(monkeypatch, _pcm_bytes(samples))
    energies = audio_analysis.get_audio_energy("clip.mp4", 10)
    assert energies is not None
    assert energies.min() >= 0.0
    assert energies.max() <= 1.0


def test_window_size_changes_the_result(monkeypatch):
    # Silence for the first 12000 samples, loud after. A narrow window
    # centred just before the loud part sees only silence; a wide window
    # centred at the same frame mixes loud samples in.
    samples = [0] * 12000 + [10000] * 4000
    _stub_popen(monkeypatch, _pcm_bytes(samples))
    narrow = audio_analysis.get_audio_energy("clip.mp4", 10, window_size_sec=0.01)
    _stub_popen(monkeypatch, _pcm_bytes(samples))
    wide = audio_analysis.get_audio_energy("clip.mp4", 10, window_size_sec=0.5)
    assert narrow is not None and wide is not None
    assert narrow[7] == pytest.approx(0.0)
    assert wide[7] > 0.7
    assert wide[8] > 0.9
    assert wide[9] == pytest.approx(1.0)


def test_negative_samples_use_absolute_rms(monkeypatch):
    _stub_popen(monkeypatch, _pcm_bytes([-10000] * 16000))
    energies = audio_analysis.get_audio_energy("clip.mp4", 10)
    assert energies is not None
    # Constant magnitude -> all zero after normalization (see above), but
    # the function must not crash or return NaN on negative PCM.
    assert not np.isnan(energies).any()
