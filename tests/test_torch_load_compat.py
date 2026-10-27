import sys
import typing
from types import ModuleType, SimpleNamespace

import pytest

from scripts import transcribe_video


def _install_torch_load_wrapper(monkeypatch, original_load):
    fake_torch = SimpleNamespace(load=original_load, serialization=SimpleNamespace())
    monkeypatch.setattr(transcribe_video, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "omegaconf", ModuleType("omegaconf"))
    monkeypatch.setitem(sys.modules, "torchaudio", ModuleType("torchaudio"))
    transcribe_video.apply_safe_globals_hack()
    return fake_torch.load


@pytest.mark.parametrize(
    "load_kwargs",
    [
        {"map_location": "cpu"},
        {"map_location": "cpu", "weights_only": None},
    ],
)
def test_safe_load_defaults_missing_or_none_weights_only_to_true(monkeypatch, load_kwargs):
    monkeypatch.delenv("VIRALCUTTER_ALLOW_UNSAFE_LOAD", raising=False)
    calls = []

    def original_load(*args, **kwargs):
        calls.append((args, kwargs.copy()))
        return "loaded"

    safe_load = _install_torch_load_wrapper(monkeypatch, original_load)

    assert safe_load("model.ckpt", **load_kwargs) == "loaded"
    assert calls == [
        (("model.ckpt",), {"map_location": "cpu", "weights_only": True})
    ]


@pytest.mark.parametrize("weights_only", [True, False])
def test_safe_load_respects_explicit_weights_only(monkeypatch, weights_only):
    monkeypatch.delenv("VIRALCUTTER_ALLOW_UNSAFE_LOAD", raising=False)
    calls = []

    def original_load(*args, **kwargs):
        calls.append((args, kwargs.copy()))
        return "loaded"

    safe_load = _install_torch_load_wrapper(monkeypatch, original_load)

    assert safe_load("model.ckpt", weights_only=weights_only) == "loaded"
    assert calls == [(("model.ckpt",), {"weights_only": weights_only})]


def test_safe_load_keeps_unsafe_retry_opt_in(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_ALLOW_UNSAFE_LOAD", "1")
    calls = []

    def original_load(*args, **kwargs):
        calls.append(kwargs.copy())
        if kwargs["weights_only"]:
            raise ValueError("legacy checkpoint")
        return "loaded"

    safe_load = _install_torch_load_wrapper(monkeypatch, original_load)

    assert safe_load("model.ckpt", weights_only=None) == "loaded"
    assert [call["weights_only"] for call in calls] == [True, False]


def test_safe_load_auto_allows_missing_globals_then_loads(monkeypatch):
    """Real pyannote VAD checkpoints (whisperx 3.x on torch 2.6+) hit a
    chain of 'Unsupported global: GLOBAL <module>.<attr>' errors —
    omegaconf.base.Metadata, torch.torch_version.TorchVersion,
    pyannote.audio.core.model.Introspection, ... safe_load parses each
    named global out of the error, registers it, and retries — keeping
    the weights_only=True boundary while ending the whack-a-mole."""
    monkeypatch.delenv("VIRALCUTTER_ALLOW_UNSAFE_LOAD", raising=False)
    registered = []
    attempts = {"n": 0}

    class FakeSerialization:
        def add_safe_globals(self, values):
            registered.extend(values)

    def original_load(*args, **kwargs):
        if not kwargs["weights_only"]:
            return "loaded"
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ValueError(
                "WeightsUnpickler error: Unsupported global: GLOBAL "
                "omegaconf.base.Metadata was not an allowed global by default."
            )
        if attempts["n"] == 2:
            raise ValueError(
                "WeightsUnpickler error: Unsupported global: GLOBAL "
                "torch.torch_version.TorchVersion was not an allowed global by default."
            )
        return "loaded"

    fake_torch = SimpleNamespace(load=original_load, serialization=FakeSerialization())
    monkeypatch.setattr(transcribe_video, "torch", fake_torch)
    # Stub the modules named in the error messages so the auto-allowlist can
    # import them regardless of what is installed in the environment.
    fake_oc_base = ModuleType("omegaconf.base")
    fake_oc_base.Metadata = type("Metadata", (), {})
    monkeypatch.setitem(sys.modules, "omegaconf", ModuleType("omegaconf"))
    monkeypatch.setitem(sys.modules, "omegaconf.base", fake_oc_base)
    transcribe_video.apply_safe_globals_hack()

    assert fake_torch.load("model.ckpt", weights_only=None) == "loaded"
    assert attempts["n"] == 3

    import torch as real_torch

    assert fake_oc_base.Metadata in registered
    assert real_torch.torch_version.TorchVersion in registered



def test_safe_load_does_not_retry_unsafely_without_opt_in(monkeypatch):
    monkeypatch.delenv("VIRALCUTTER_ALLOW_UNSAFE_LOAD", raising=False)
    calls = []

    def original_load(*args, **kwargs):
        calls.append(kwargs.copy())
        raise ValueError("legacy checkpoint")

    safe_load = _install_torch_load_wrapper(monkeypatch, original_load)

    with pytest.raises(RuntimeError, match="VIRALCUTTER_ALLOW_UNSAFE_LOAD=1"):
        safe_load("model.ckpt", weights_only=None)
    assert calls == [{"weights_only": True}]


def test_typing_any_is_registered_as_safe_global(monkeypatch):
    monkeypatch.delenv("VIRALCUTTER_ALLOW_UNSAFE_LOAD", raising=False)
    registered = []
    serialization = SimpleNamespace(
        add_safe_globals=lambda values: registered.extend(values)
    )
    fake_torch = SimpleNamespace(load=lambda *args, **kwargs: "loaded", serialization=serialization)
    monkeypatch.setattr(transcribe_video, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "omegaconf", ModuleType("omegaconf"))
    monkeypatch.setitem(sys.modules, "torchaudio", ModuleType("torchaudio"))

    transcribe_video.apply_safe_globals_hack()

    assert (typing.Any, "typing.Any") in registered


def test_safe_load_rewinds_open_streams_before_each_attempt(monkeypatch):
    """pyannote/lightning pass OPEN FILE OBJECTS to torch.load. Without a
    seek(0) between attempts the weights_only=False retry starts reading
    mid-stream and dies with a misleading 'persistent id' unpickling error
    (observed on the whisperx bundled VAD checkpoint under torch 2.8)."""
    monkeypatch.setenv("VIRALCUTTER_ALLOW_UNSAFE_LOAD", "1")

    class FakeStream:
        def __init__(self):
            self.seeks = []

        def seek(self, position):
            self.seeks.append(position)

    stream = FakeStream()
    calls = []

    def original_load(*args, **kwargs):
        calls.append((args, kwargs.copy()))
        if kwargs["weights_only"]:
            raise ValueError("GLOBAL list was not an allowed global")
        return "loaded"

    safe_load = _install_torch_load_wrapper(monkeypatch, original_load)

    assert safe_load(stream, map_location="cpu", weights_only=None) == "loaded"
    assert [call[1]["weights_only"] for call in calls] == [True, False]
    # Rewound before the safe attempt and again before the unsafe retry.
    assert stream.seeks == [0, 0]


def test_safe_load_tolerates_unseekable_streams(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_ALLOW_UNSAFE_LOAD", "1")
    calls = []

    class ClosedStream:
        def seek(self, position):
            raise ValueError("I/O operation on closed file")

    def original_load(*args, **kwargs):
        calls.append(kwargs.copy())
        if kwargs["weights_only"]:
            raise ValueError("legacy checkpoint")
        return "loaded"

    safe_load = _install_torch_load_wrapper(monkeypatch, original_load)

    assert safe_load(ClosedStream(), weights_only=None) == "loaded"
    assert [call["weights_only"] for call in calls] == [True, False]


def test_builtins_containers_are_registered_as_safe_globals(monkeypatch):
    """pyannote VAD checkpoints legitimately reference GLOBAL builtins.list
    (and friends). torch's weights_only unpickler rejects them unless they
    are registered — its own error message recommends add_safe_globals([list]).
    These are inert data containers, not executable code."""
    monkeypatch.delenv("VIRALCUTTER_ALLOW_UNSAFE_LOAD", raising=False)
    registered = []
    serialization = SimpleNamespace(
        add_safe_globals=lambda values: registered.extend(values)
    )
    fake_torch = SimpleNamespace(load=lambda *args, **kwargs: "loaded", serialization=serialization)
    monkeypatch.setattr(transcribe_video, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "omegaconf", ModuleType("omegaconf"))
    monkeypatch.setitem(sys.modules, "torchaudio", ModuleType("torchaudio"))

    transcribe_video.apply_safe_globals_hack()

    for cls in (list, dict, set, tuple, frozenset, range,
                int, float, bool, str, bytes, complex, type(None)):
        assert cls in registered
    import collections

    assert collections.OrderedDict in registered
    assert collections.defaultdict in registered
