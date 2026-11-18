# -*- coding: utf-8 -*-
"""Direct tests for scripts/oom_guard.py (GPU OOM model fallback)."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import oom_guard


class TestLooksLikeOom:
    @pytest.mark.parametrize("msg", [
        "CUDA out of memory",
        "out of memory",
        "RuntimeError: CUDA error",
        "cudnn error",
        "cublas error",
        "insufficient memory",
        "cannot allocate",
        "bad allocation",
        "bad_alloc",
        "std::bad_alloc",
        "unable to allocate",
        "numpy.core._exceptions._ArrayMemoryError",
        "memoryerror",
        "mkl_malloc: failed to allocate memory",
        "failed to allocate memory",
        "cannot allocate memory",
        "out of memory error",
    ])
    def test_markers_match(self, msg):
        assert oom_guard._looks_like_oom(RuntimeError(msg))

    @pytest.mark.parametrize("msg", [
        "file not found",
        "invalid argument",
        "connection refused",
        "index out of range",
    ])
    def test_non_oom_messages_do_not_match(self, msg):
        assert not oom_guard._looks_like_oom(ValueError(msg))

    def test_memory_error_always_matches(self):
        assert oom_guard._looks_like_oom(MemoryError())
        assert oom_guard._looks_like_oom(MemoryError(""))

    def test_matching_is_case_insensitive(self):
        assert oom_guard._looks_like_oom(RuntimeError("CUDA OUT OF MEMORY"))


class TestNextModel:
    @pytest.mark.parametrize("model, expected", [
        ("large-v3", "medium"),
        ("large-v3-turbo", "medium"),
        ("large", "medium"),
        ("medium", "small"),
        ("small", "base"),
        ("base", "tiny"),
        ("tiny", None),
        ("unknown-model", None),
        ("", None),
    ])
    def test_fallback_chain(self, model, expected):
        assert oom_guard._next_model(model) is expected

    def test_whitespace_is_stripped(self):
        assert oom_guard._next_model("  medium  ") == "small"

    def test_chain_end_constant(self):
        assert oom_guard.CHAIN_END == "tiny"
        assert oom_guard._next_model(oom_guard.CHAIN_END) is None


def _recording_transcribe(failures):
    """Return (fn, calls); fn raises RuntimeError('CUDA out of memory')
    for the first `failures` calls, then returns ('out.srt', 'out.tsv')."""
    calls = []

    def fake(input_file, model, project_folder=None, **kwargs):
        calls.append((input_file, model, project_folder, kwargs))
        if len(calls) <= failures:
            raise RuntimeError("CUDA out of memory")
        return ("out.srt", "out.tsv")

    return fake, calls


class TestTranscribeWithFallback:
    def test_success_first_try_records_used_model(self, tmp_path):
        fake, calls = _recording_transcribe(0)
        result = oom_guard.transcribe_with_fallback("in.mp4", "medium", str(tmp_path),
                                                    transcribe_fn=fake, verbose=False)
        assert result == ("out.srt", "out.tsv")
        assert len(calls) == 1
        record = json.loads((tmp_path / "transcription_model.json").read_text(encoding="utf-8"))
        assert record == {"requested": "medium", "used": "medium", "oom_fallbacks": []}

    def test_oom_falls_back_to_smaller_model(self, tmp_path, capsys):
        fake, calls = _recording_transcribe(1)
        result = oom_guard.transcribe_with_fallback("in.mp4", "large-v3-turbo",
                                                    str(tmp_path), transcribe_fn=fake)
        assert result == ("out.srt", "out.tsv")
        assert [c[1] for c in calls] == ["large-v3-turbo", "medium"]
        record = json.loads((tmp_path / "transcription_model.json").read_text(encoding="utf-8"))
        assert record["used"] == "medium"
        assert record["oom_fallbacks"] == ["large-v3-turbo"]
        assert "falling back" in capsys.readouterr().out

    def test_multiple_fallbacks_walk_the_chain(self, tmp_path):
        fake, calls = _recording_transcribe(3)
        oom_guard.transcribe_with_fallback("in.mp4", "large", str(tmp_path),
                                           transcribe_fn=fake, verbose=False)
        assert [c[1] for c in calls] == ["large", "medium", "small", "base"]

    def test_non_oom_error_raises_immediately(self, tmp_path):
        def boom(_path, _model, **_kw):
            raise ValueError("bad input file")

        with pytest.raises(ValueError, match="bad input file"):
            oom_guard.transcribe_with_fallback("in.mp4", "medium", str(tmp_path),
                                               transcribe_fn=boom, verbose=False)

    def test_oom_on_tiny_gives_up(self, tmp_path):
        fake, calls = _recording_transcribe(99)
        with pytest.raises(RuntimeError, match="out of memory"):
            oom_guard.transcribe_with_fallback("in.mp4", "large-v3-turbo",
                                               str(tmp_path), transcribe_fn=fake,
                                               verbose=False)
        assert [c[1] for c in calls] == ["large-v3-turbo", "medium", "small", "base", "tiny"]

    def test_memory_error_counts_as_oom(self, tmp_path):
        def mem_fail(_path, _model, **_kw):
            raise MemoryError()

        with pytest.raises(MemoryError):
            oom_guard.transcribe_with_fallback("in.mp4", "tiny", str(tmp_path),
                                               transcribe_fn=mem_fail, verbose=False)

    def test_device_kwarg_only_for_cpu_or_cuda(self, tmp_path):
        fake, calls = _recording_transcribe(0)
        oom_guard.transcribe_with_fallback("in.mp4", "tiny", str(tmp_path),
                                           transcribe_fn=fake, verbose=False, device="cpu")
        assert calls[0][2] == str(tmp_path)
        assert calls[0][3] == {"device": "cpu"}

    def test_device_auto_passes_no_device_kwarg(self, tmp_path):
        fake, calls = _recording_transcribe(0)
        oom_guard.transcribe_with_fallback("in.mp4", "tiny", str(tmp_path),
                                           transcribe_fn=fake, verbose=False, device="auto")
        assert calls[0][2] == str(tmp_path)
        assert calls[0][3] == {}

    def test_device_cuda_is_lowercased(self, tmp_path):
        fake, calls = _recording_transcribe(0)
        oom_guard.transcribe_with_fallback("in.mp4", "tiny", str(tmp_path),
                                           transcribe_fn=fake, verbose=False, device="CUDA")
        assert calls[0][3]["device"] == "cuda"

    def test_unknown_device_passes_no_device_kwarg(self, tmp_path):
        fake, calls = _recording_transcribe(0)
        oom_guard.transcribe_with_fallback("in.mp4", "tiny", str(tmp_path),
                                           transcribe_fn=fake, verbose=False, device="tpu")
        assert "device" not in calls[0][3]

    def test_record_file_failure_is_swallowed(self, tmp_path, monkeypatch):
        # project_folder is a file, not a directory: makedirs fails, but the
        # transcription result is still returned.
        fake, _calls = _recording_transcribe(0)
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        result = oom_guard.transcribe_with_fallback("in.mp4", "tiny", str(blocker),
                                                    transcribe_fn=fake, verbose=False)
        assert result == ("out.srt", "out.tsv")


class TestMain:
    def test_main_returns_zero_and_prints_paths(self, tmp_path, monkeypatch, capsys):
        fake, _calls = _recording_transcribe(0)
        monkeypatch.setattr(oom_guard, "transcribe_with_fallback",
                            lambda *a, **k: fake(*a, **k))
        monkeypatch.setattr(sys, "argv", ["oom_guard", "--video", "in.mp4",
                                          "--model", "small", "--project", str(tmp_path)])
        assert oom_guard.main() == 0
        out = capsys.readouterr().out
        assert "SRT: out.srt" in out
        assert "TSV: out.tsv" in out
