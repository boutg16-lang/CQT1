# -*- coding: utf-8 -*-
"""Tests for the Windows + NVIDIA acceptance harness.

The core pipeline test runs the REAL modules (FFmpeg-generated clip, reframe,
audio QC, jump-cut detection, thumbnail) whenever ffmpeg is available — the
same checks the harness performs on the user's RTX 3060 machine. GPU-dependent
paths are exercised through stubbed torch / faster-whisper modules.
"""
import json
import shutil
import sys
import types

import pytest

from scripts import windows_acceptance as wa

# ---------------------------------------------------------------------------
# Report assembly and exit codes
# ---------------------------------------------------------------------------

def test_build_report_verdicts():
    ok = wa._check("a", wa.OK, "fine", critical=True)
    warn = wa._check("b", wa.WARN, "meh")
    fail = wa._check("c", wa.FAIL, "bad")
    critical_fail = wa._check("d", wa.FAIL, "very bad", critical=True)

    assert wa.build_report([ok])["verdict"] == "pass"
    assert wa.build_report([ok, warn])["verdict"] == "warn"
    # a non-critical fail does not sink the run but still yields "warn"
    assert wa.build_report([ok, fail])["verdict"] == "warn"
    assert wa.build_report([ok, critical_fail])["verdict"] == "fail"

    report = wa.build_report([ok, warn, fail, critical_fail])
    assert report["summary"] == {"ok": 1, "warn": 1, "fail": 2, "critical_fail": 1}


def test_render_console_lists_every_check():
    report = wa.build_report([
        wa._check("خطوة", wa.OK, "تفاصيل", elapsed=1.25),
        wa._check("أخرى", wa.FAIL, "خطأ", critical=True),
    ])
    text = wa.render_console(report)
    assert "خطوة" in text and "أخرى" in text
    assert "الحكم: fail" in text
    assert "(1.2s)" in text or "(1.3s)" in text


def test_atomic_json_write(tmp_path):
    target = tmp_path / "report.json"
    wa._atomic_write_json(str(target), {"verdict": "pass"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"verdict": "pass"}


# ---------------------------------------------------------------------------
# GPU probing with a stubbed torch
# ---------------------------------------------------------------------------

def _install_fake_torch(monkeypatch, *, available, name="NVIDIA GeForce RTX 3060",
                        vram=12 * (1024 ** 3)):
    torch = types.ModuleType("torch")
    torch.__version__ = "2.5.1+cu121"

    class _Props:
        total_memory = vram

    class _Cuda:
        @staticmethod
        def is_available():
            return available

        @staticmethod
        def get_device_name(index):
            return name

        @staticmethod
        def get_device_properties(index):
            return _Props()

    torch.cuda = _Cuda
    monkeypatch.setitem(sys.modules, "torch", torch)
    return torch


def test_gpu_probe_reports_cuda_device(monkeypatch):
    _install_fake_torch(monkeypatch, available=True)
    check = wa.check_gpu()
    assert check["status"] == wa.OK
    assert "RTX 3060" in check["detail"]
    assert "12" in check["detail"]


def test_gpu_probe_warns_for_a_different_gpu(monkeypatch):
    _install_fake_torch(monkeypatch, available=True, name="NVIDIA GeForce GTX 1080")
    check = wa.check_gpu()
    assert check["status"] == wa.WARN
    assert "expected an RTX 3060" in check["detail"]


def test_gpu_probe_require_gpu_fails_without_cuda(monkeypatch):
    _install_fake_torch(monkeypatch, available=False)
    check = wa.check_gpu(require=True)
    assert check["status"] == wa.FAIL
    assert check["critical"] is True


def test_gpu_probe_warns_without_torch(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)
    check = wa.check_gpu()
    assert check["status"] == wa.WARN
    assert "could not be imported" in check["detail"]


# ---------------------------------------------------------------------------
# Transcription test with a stubbed faster-whisper
# ---------------------------------------------------------------------------

def test_gpu_transcription_uses_cuda_when_available(monkeypatch, tmp_path):
    _install_fake_torch(monkeypatch, available=True)

    fw = types.ModuleType("faster_whisper")

    class _Info:
        language = "ar"

    class _Model:
        def __init__(self, model_size, device=None, compute_type=None):
            self.device = device
            self.compute = compute_type

        def transcribe(self, path, language=None, beam_size=1):
            return ([], _Info())

    fw.WhisperModel = _Model
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)

    check = wa.check_gpu_transcription(str(tmp_path), "tiny")
    assert check["status"] == wa.OK
    assert "device=cuda" in check["detail"]
    assert "int8_float16" in check["detail"]


def test_gpu_transcription_warns_when_backend_missing(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    check = wa.check_gpu_transcription(str(tmp_path), "tiny")
    assert check["status"] == wa.WARN
    assert "not installed" in check["detail"]


def test_gpu_transcription_fails_when_model_load_raises(monkeypatch, tmp_path):
    fw = types.ModuleType("faster_whisper")

    class _Model:
        def __init__(self, model_size, device=None, compute_type=None):
            raise RuntimeError("CUDA out of memory")

    fw.WhisperModel = _Model
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)

    check = wa.check_gpu_transcription(str(tmp_path), "small")
    assert check["status"] == wa.FAIL
    assert "failed to load" in check["detail"]


# ---------------------------------------------------------------------------
# CLI behaviour
# ---------------------------------------------------------------------------

def test_main_warn_exit_code_without_gpu(monkeypatch, tmp_path):
    # No torch available in this environment → warning, exit code 2.
    monkeypatch.setitem(sys.modules, "torch", None)
    rc = wa.main(["--json", str(tmp_path / "out.json")])
    assert rc in (0, 2)
    report = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert report["verdict"] in {"pass", "warn"}
    assert report["summary"]["critical_fail"] == 0


def test_main_missing_clip_is_a_critical_fail(tmp_path, capsys):
    rc = wa.main(["--clip", str(tmp_path / "does_not_exist.mp4")])
    assert rc == 1
    out = capsys.readouterr().out
    assert "clip not found" in out


# ---------------------------------------------------------------------------
# Real pipeline (the same checks run on the RTX 3060 machine)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_core_pipeline_real_ffmpeg(tmp_path):
    checks = wa.run_core_pipeline(str(tmp_path))
    failures = [c for c in checks if c["status"] == wa.FAIL]
    assert not failures, failures
    names = {c["name"] for c in checks}
    assert {
        "Test media generation",
        "Media probe & validation",
        "Vertical reframe (9:16)",
        "Audio QC (loudnorm + silence)",
        "Silence detection (jump cuts)",
        "Thumbnail generation",
    } <= names


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_generated_clip_carries_the_planted_silence(tmp_path):
    made = wa.generate_test_clip(str(tmp_path / "clip.mp4"), duration=12,
                                 silence_start=4.0, silence_end=7.0)
    assert made["ok"]

    from scripts import jump_cuts
    silences = jump_cuts.detect_silences(str(tmp_path / "clip.mp4"),
                                         threshold_db=-35.0, min_duration=1.0)
    assert silences, "the planted silence gap was not detected"
    start, end, dur = silences[0]
    assert abs(start - 4.0) < 0.6
    assert abs(end - 7.0) < 0.6
