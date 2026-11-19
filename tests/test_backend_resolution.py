"""Tests for scripts/backend_resolution.py (extracted from main_improved).

The helpers are exercised through the NEW module path; identity tests
below assert the old import path re-exports the very same callables.
``torch`` is only ever imported lazily inside
``describe_transcription_device``, so the tests control it precisely via
``sys.modules``: a fake module, ``None`` (blocked import), or absent.
"""

import os
import sys
import types
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backend_resolution import (  # noqa: E402
    describe_transcription_device,
    resolve_safety_backend,
)


@pytest.fixture(scope="module")
def cli():
    """Import main_improved once, with heavy third-party deps stubbed."""
    heavy = [
        "cv2", "mediapipe", "torch", "torchaudio", "whisperx", "insightface",
        "onnxruntime", "av", "moviepy", "librosa", "soundfile",
        "tqdm", "tqdm.asyncio", "psutil",
    ]
    saved = {}
    stubs = {}
    for name in heavy:
        saved[name] = sys.modules.get(name)
        if saved[name] is None:
            stubs[name] = mock.MagicMock(name=name)
    sys.modules.update(stubs)
    try:
        import main_improved as mod
    finally:
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
    return mod


def test_reexports_are_the_same_callables(cli):
    assert cli.describe_transcription_device is describe_transcription_device
    assert cli.resolve_safety_backend is resolve_safety_backend


# ---------------------------------------------------------------------------
# describe_transcription_device
# ---------------------------------------------------------------------------

def _fake_torch(monkeypatch, cuda_available, name="GeForce RTX 4090",
                name_raises=False):
    torch_mod = types.ModuleType("torch")

    def _get_name(index):
        if name_raises:
            raise RuntimeError("no such device")
        return name

    torch_mod.cuda = types.SimpleNamespace(
        is_available=lambda: cuda_available,
        get_device_name=_get_name,
    )
    monkeypatch.setitem(sys.modules, "torch", torch_mod)


def test_device_no_torch_falls_back_to_cpu(monkeypatch):
    # None in sys.modules makes "import torch" raise ImportError.
    monkeypatch.setitem(sys.modules, "torch", None)
    assert describe_transcription_device(None) == {
        "actual": "cpu",
        "requested": "auto",
        "name": "",
        "message": "يعمل الآن بواسطة CPU (Torch/CUDA غير قابل للفحص)",
    }


def test_device_cuda_requested_but_unavailable(monkeypatch):
    _fake_torch(monkeypatch, cuda_available=False)
    assert describe_transcription_device("cuda") == {
        "actual": "unavailable",
        "requested": "cuda",
        "name": "",
        "message": "CUDA مطلوبة لكن بطاقة NVIDIA غير متاحة",
    }


def test_device_cpu_requested_ignores_cuda(monkeypatch):
    _fake_torch(monkeypatch, cuda_available=True)
    result = describe_transcription_device("cpu")
    assert result["actual"] == "cpu"
    assert result["requested"] == "cpu"
    assert result["name"] == ""
    assert result["message"] == "يعمل الآن بواسطة CPU"


def test_device_auto_selects_cuda_with_device_name(monkeypatch):
    _fake_torch(monkeypatch, cuda_available=True)
    result = describe_transcription_device("auto")
    assert result["actual"] == "cuda"
    assert result["name"] == "GeForce RTX 4090"
    assert result["message"] == "يعمل الآن بواسطة NVIDIA GPU / CUDA — GeForce RTX 4090"


def test_device_name_failure_falls_back_to_generic_label(monkeypatch):
    _fake_torch(monkeypatch, cuda_available=True, name_raises=True)
    result = describe_transcription_device("auto")
    assert result["actual"] == "cuda"
    assert result["name"] == "NVIDIA GPU"
    assert result["message"] == "يعمل الآن بواسطة NVIDIA GPU / CUDA — NVIDIA GPU"


def test_device_invalid_request_normalised_to_auto(monkeypatch):
    _fake_torch(monkeypatch, cuda_available=False)
    result = describe_transcription_device("tpu")
    assert result["requested"] == "auto"
    assert result["actual"] == "cpu"


def test_device_request_case_and_whitespace_normalised(monkeypatch):
    _fake_torch(monkeypatch, cuda_available=False)
    result = describe_transcription_device("  CUDA ")
    assert result["requested"] == "cuda"
    assert result["actual"] == "unavailable"


# ---------------------------------------------------------------------------
# resolve_safety_backend
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_env(monkeypatch):
    for var in ("OPENAI_API_KEY", "OPENAI_MODERATION_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_safety_auto_manual_prefers_openai_moderation(clean_env, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    assert resolve_safety_backend("auto", "manual") == (
        "openai-moderation", "test-openai-key")


def test_safety_auto_manual_falls_back_to_local(clean_env):
    assert resolve_safety_backend("auto", "manual") == ("local", None)


def test_safety_auto_manual_uses_moderation_key_fallback(clean_env, monkeypatch):
    monkeypatch.setenv("OPENAI_MODERATION_API_KEY", "mod-key")
    assert resolve_safety_backend("auto", "manual") == (
        "openai-moderation", "mod-key")


def test_safety_auto_openai_never_selects_moderation(clean_env, monkeypatch):
    # Documented behaviour: an "openai" backend run stays local even with a
    # plain OPENAI_API_KEY present (a custom OpenAI-compatible server is not
    # guaranteed to expose /moderations).
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    assert resolve_safety_backend("auto", "openai") == ("local", None)


def test_safety_auto_auto_with_api_key_selects_gemini(clean_env):
    assert resolve_safety_backend("auto", "auto", api_key="k") == (
        "gemini", "k")


def test_safety_auto_auto_without_key_is_local(clean_env):
    assert resolve_safety_backend("auto", "auto") == ("local", None)


def test_safety_auto_gemini_uses_api_key(clean_env):
    assert resolve_safety_backend("auto", "gemini", api_key="k") == (
        "gemini", "k")


def test_safety_auto_g4f_uses_api_key(clean_env):
    assert resolve_safety_backend("auto", "g4f", api_key="k") == (
        "g4f", "k")


def test_safety_auto_gemini_ignores_gemini_env_key(clean_env, monkeypatch):
    # With ai_backend == "gemini" the key is taken from the api_key argument,
    # not from GEMINI_API_KEY, so no key here means None even with env set.
    monkeypatch.setenv("GEMINI_API_KEY", "env-gemini-key")
    assert resolve_safety_backend("auto", "gemini") == ("gemini", None)


def test_safety_explicit_gemini_prefers_env_key(clean_env, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "env-gemini-key")
    assert resolve_safety_backend("gemini", "manual", api_key="k") == (
        "gemini", "env-gemini-key")


def test_safety_explicit_openai_moderation_uses_env_key(clean_env, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    assert resolve_safety_backend("openai-moderation", "openai") == (
        "openai-moderation", "test-openai-key")


def test_safety_explicit_openai_moderation_without_key_is_none(clean_env):
    assert resolve_safety_backend(
        "openai-moderation", "manual", api_key="k") == (
            "openai-moderation", None)


def test_safety_explicit_g4f_returns_api_key(clean_env):
    assert resolve_safety_backend("g4f", "manual", api_key="k") == (
        "g4f", "k")


def test_safety_unknown_request_is_local(clean_env):
    assert resolve_safety_backend("weird", "manual", api_key="k") == (
        "local", None)


def test_safety_request_case_and_whitespace_normalised(clean_env, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    assert resolve_safety_backend("  AUTO ", "manual") == (
        "openai-moderation", "test-openai-key")
