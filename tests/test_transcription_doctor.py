# -*- coding: utf-8 -*-
"""The transcription doctor probes the real VAD, repairs once, and caches.

`import whisperx` can pass while the VAD stack is broken. These tests pin the
executable probe, the targeted repairs, the success cache, and the rule that
error messages must use the versions actually installed.
"""
import os
import sys

import pytest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from scripts import transcription_diagnostics as td


@pytest.fixture(autouse=True)
def _isolate_health_profile(monkeypatch):
    """Never write the real health profile from tests (it would leak env)."""
    monkeypatch.setattr(td, "_publish_health", lambda *args, **kwargs: None)


def _report(tokenizers="0.22.2", hub="1.33.0", whisperx=True, fallback=True):
    return {
        "packages": {
            "torch": {"ok": True, "version": "2.8.0"},
            "whisperx": {"ok": whisperx, "version": "3.3.1" if whisperx else ""},
            "tokenizers": {"ok": True, "version": tokenizers},
            "transformers": {"ok": True, "version": "5.15.1"},
            "huggingface_hub": {"ok": True, "version": hub},
            "pyannote.audio": {"ok": True, "version": "3.3.2"},
            "asteroid_filterbanks": {"ok": True, "version": "0.4.0"},
            "ctranslate2": {"ok": True, "version": "4.4.0"},
        },
        "fallback_ready": fallback,
        "primary_ready": whisperx,
        "missing": [],
        "incompatible": [],
        "torch_runtime": {"cuda_available": False},
        "ready": whisperx or fallback,
    }


def test_probe_vad_loads_the_bundled_model(monkeypatch):
    calls = []

    class FakeWhisperx:
        @staticmethod
        def load_vad_model(device):
            calls.append(device)

    modules = {
        "torch": SimpleNamespace(device=lambda name: name),
        "whisperx": FakeWhisperx,
    }
    result = td.probe_vad(import_module=lambda name: modules[name])
    assert result["ok"] is True
    assert calls == ["cpu"]


def test_probe_vad_reports_the_real_failure():
    def broken(name):
        raise ModuleNotFoundError("No module named 'asteroid_filterbanks'")
    result = td.probe_vad(import_module=broken)
    assert result["ok"] is False
    assert "asteroid_filterbanks" in result["error"]


def test_ensure_skips_when_whisperx_is_absent(monkeypatch):
    monkeypatch.setattr(td, "diagnose", lambda base_dir=None: _report(
        whisperx=False, fallback=True))
    result = td.ensure_ready()
    assert result["ok"] is True
    assert result["skipped"] is True


def test_ensure_caches_a_successful_probe(tmp_path, monkeypatch):
    cache = tmp_path / "ready.json"
    monkeypatch.setattr(td, "_ready_cache_path", lambda: str(cache))
    monkeypatch.setattr(td, "diagnose", lambda base_dir=None: _report())
    monkeypatch.setattr(td, "probe_vad", lambda **kwargs: {"ok": True, "error": ""})
    first = td.ensure_ready()
    assert first["ok"] is True and first["cached"] is False
    assert cache.exists()

    def explode(**kwargs):
        raise AssertionError("a cached stack must not re-probe")

    monkeypatch.setattr(td, "probe_vad", explode)
    second = td.ensure_ready()
    assert second["ok"] is True and second["cached"] is True


def test_ensure_repairs_the_missing_pyannote_dependencies(tmp_path, monkeypatch):
    monkeypatch.setattr(td, "_ready_cache_path", lambda: str(tmp_path / "ready.json"))
    monkeypatch.setattr(td, "diagnose", lambda base_dir=None: _report())
    probes = iter([
        {"ok": False, "error": "ModuleNotFoundError: No module named 'asteroid_filterbanks'"},
        {"ok": True, "error": ""},
    ])
    monkeypatch.setattr(td, "probe_vad", lambda **kwargs: next(probes))
    commands = []

    def fake_install(command, timeout=1800):
        commands.append(command)
        return {"ok": True, "command": command, "error": "", "output": ""}

    monkeypatch.setattr(td, "_run_install", fake_install)
    result = td.ensure_ready()
    assert result["ok"] is True
    joined = " ".join(commands[0])
    assert "asteroid-filterbanks>=0.4" in joined
    assert "pyannote.database>=5.0.1" in joined


def test_ensure_reinstalls_the_tokenizers_native_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(td, "_ready_cache_path", lambda: str(tmp_path / "ready.json"))
    monkeypatch.setattr(td, "diagnose", lambda base_dir=None: _report())
    probes = iter([
        {"ok": False, "error": "Exception: tokenizers native extension failed"},
        {"ok": True, "error": ""},
    ])
    monkeypatch.setattr(td, "probe_vad", lambda **kwargs: next(probes))
    commands = []
    monkeypatch.setattr(td, "_run_install",
                        lambda command, timeout=1800: commands.append(command) or {
                            "ok": True, "command": command, "error": "", "output": ""})
    result = td.ensure_ready()
    assert result["ok"] is True
    joined = " ".join(commands[0])
    assert "--force-reinstall" in joined
    assert "tokenizers==0.22.2" in joined


def test_ensure_reports_failure_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(td, "_ready_cache_path", lambda: str(tmp_path / "ready.json"))
    monkeypatch.setattr(td, "diagnose", lambda base_dir=None: _report())
    monkeypatch.setattr(td, "probe_vad",
                        lambda **kwargs: {"ok": False, "error": "boom"})
    monkeypatch.setattr(td, "repair", lambda mode, base_dir=None: {
        "ok": False, "error": "repair failed"})
    result = td.ensure_ready()
    assert result["ok"] is False
    assert result["vad"]["error"] == "boom"


def test_error_message_uses_the_installed_versions(monkeypatch):
    monkeypatch.setattr(td, "diagnose", lambda base_dir=None: _report())
    message = td.build_error_message(whisperx_error="tokenizers failed to load")
    assert "0.23.1 مثبت" not in message
    assert "0.22.2" in message
    assert "0.36.2" not in message
