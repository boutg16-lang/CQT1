# -*- coding: utf-8 -*-
"""The doctor's conclusion must reach the app without a reinstall.

The health profile carries backend/CPU/window decisions, is applied with
setdefault (user env wins), and expires so an old failure does not pin the
app to the fallback forever.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import psutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import transcription_health as health


def _profile(tmp_path, **overrides):
    data = {
        "backend": "faster-whisper",
        "force_cpu": True,
        "chunk_seconds": 120,
        "reason": "test",
        "written_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    data.update(overrides)
    path = tmp_path / "health.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_recommended_chunk_seconds_follows_free_ram(monkeypatch):
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(
        available=1024 ** 3))
    assert health.recommended_chunk_seconds() == 120
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(
        available=2 * 1024 ** 3))
    assert health.recommended_chunk_seconds() == 180
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(
        available=8 * 1024 ** 3))
    assert health.recommended_chunk_seconds() == 300


def test_apply_profile_sets_defaults_and_user_env_wins(tmp_path, monkeypatch):
    path = _profile(tmp_path)
    for key in ("VIRALCUTTER_TRANSCRIPTION_BACKEND",
                "VIRALCUTTER_TRANSCRIPTION_FORCE_CPU",
                "VIRALCUTTER_TRANSCRIBE_CHUNK_SECONDS"):
        monkeypatch.delenv(key, raising=False)
    health.apply_profile(path)
    assert os.environ["VIRALCUTTER_TRANSCRIPTION_BACKEND"] == "faster-whisper"
    assert os.environ["VIRALCUTTER_TRANSCRIPTION_FORCE_CPU"] == "1"
    assert os.environ["VIRALCUTTER_TRANSCRIBE_CHUNK_SECONDS"] == "120"

    monkeypatch.setenv("VIRALCUTTER_TRANSCRIPTION_BACKEND", "whisperx")
    health.apply_profile(path)
    assert os.environ["VIRALCUTTER_TRANSCRIPTION_BACKEND"] == "whisperx"


def test_stale_profile_is_ignored(tmp_path, monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace(
        "+00:00", "Z")
    path = _profile(tmp_path, written_at=old)
    monkeypatch.delenv("VIRALCUTTER_TRANSCRIPTION_BACKEND", raising=False)
    assert health.apply_profile(path) == {}
    assert "VIRALCUTTER_TRANSCRIPTION_BACKEND" not in os.environ


def test_ensure_ready_publishes_a_health_profile(tmp_path, monkeypatch):
    import scripts.transcription_diagnostics as td
    profile = tmp_path / "health.json"
    monkeypatch.setattr(health, "profile_path", lambda: str(profile))
    monkeypatch.setattr(td, "_ready_cache_path", lambda: str(tmp_path / "ready.json"))
    monkeypatch.setattr(td, "diagnose", lambda base_dir=None: {
        "packages": {"whisperx": {"ok": True, "version": "3.3.1"}},
        "torch_runtime": {"cuda_available": False},
        "fallback_ready": True,
    })
    monkeypatch.setattr(td, "probe_vad", lambda **kwargs: {"ok": True, "error": ""})
    result = td.ensure_ready()
    assert result["ok"] is True
    data = json.loads(profile.read_text(encoding="utf-8"))
    assert data["backend"] == "whisperx"
    assert data["force_cpu"] is False
    assert 60 <= data["chunk_seconds"] <= 900
