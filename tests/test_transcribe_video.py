# -*- coding: utf-8 -*-
"""Direct tests for scripts/transcribe_video.py (core pipeline, no models).

Covers only pure/deterministic behaviour: error classification, the
transcription-cache round-trip and invalidation, SRT/VTT parsing, model
candidate resolution, and the backend/placeholder env switches. No model is
ever loaded, no audio is processed, and no network is used; ``transcribe()``
itself is intentionally not tested here.
"""

import os

import pytest

from scripts import transcribe_video as tv

# --- error classification ------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        MemoryError(),
        RuntimeError("bad allocation"),
        RuntimeError("CUDA out of memory"),
        RuntimeError("mkl_malloc failed"),
        RuntimeError("Unable to allocate 123 MiB"),
        RuntimeError("cudnn error: something broke"),
    ],
)
def test_is_memory_error_true(exc):
    assert tv._is_memory_error(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("random failure"),
        RuntimeError("file not found"),
        RuntimeError("cudnn64_8.dll missing"),
    ],
)
def test_is_memory_error_false(exc):
    assert tv._is_memory_error(exc) is False


@pytest.mark.parametrize(
    "text",
    [
        "cudnn64_8.dll not found",
        "cudnn_ops_infer64_9.dll load failed",
        "CUDA driver version is insufficient",
        "dll load failed",
    ],
)
def test_is_cuda_runtime_error_true(text):
    assert tv._is_cuda_runtime_error(RuntimeError(text)) is True


@pytest.mark.parametrize("text", ["out of memory", "bad allocation", "plain error"])
def test_is_cuda_runtime_error_false(text):
    assert tv._is_cuda_runtime_error(RuntimeError(text)) is False


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("tokenizers failed to load"),
        ImportError("No module named 'transformers'"),
        RuntimeError("pyannote pipeline crashed"),
        RuntimeError("huggingface_hub timeout"),
    ],
)
def test_is_transcription_stack_error_true(exc):
    assert tv._is_transcription_stack_error(exc) is True


def test_is_transcription_stack_error_false():
    assert tv._is_transcription_stack_error(RuntimeError("plain error")) is False
    assert (
        tv._is_transcription_stack_error(RuntimeError("faster-whisper failed")) is False
    )


@pytest.mark.parametrize(
    "exc",
    [
        ImportError("No module named 'pyannote'"),
        ModuleNotFoundError("asteroid_filterbanks"),
        RuntimeError("cannot import name 'x' from 'pyannote'"),
    ],
)
def test_is_vad_dependency_error_true(exc):
    assert tv._is_vad_dependency_error(exc) is True


def test_is_vad_dependency_error_false():
    assert tv._is_vad_dependency_error(RuntimeError("plain error")) is False
    assert tv._is_vad_dependency_error(RuntimeError("bad allocation")) is False


# --- transcription cache ---------------------------------------------------------


def _artifacts(tmp_path, content=b"data"):
    input_file = tmp_path / "input.mp4"
    input_file.write_bytes(b"video-bytes")
    srt = tmp_path / "input.srt"
    tsv = tmp_path / "input.tsv"
    jsn = tmp_path / "input.json"
    for path in (srt, tsv, jsn):
        path.write_bytes(content)
    return input_file, srt, tsv, jsn


def test_cache_round_trip_and_match(tmp_path):
    input_file, srt, tsv, jsn = _artifacts(tmp_path)
    cache_path = tv._transcription_cache_path(tmp_path)
    assert cache_path == os.path.join(tmp_path, "transcription_cache.json")
    tv._save_transcription_cache(
        cache_path, input_file, "large-v3", srt, tsv, jsn, device="cpu"
    )
    cache = tv._read_transcription_cache(cache_path)
    assert cache["model_name"] == "large-v3"
    assert cache["device"] == "cpu"
    assert cache["input_size"] == input_file.stat().st_size
    assert (
        tv._cache_matches(cache, input_file, "large-v3", srt, tsv, jsn, device="cpu")
        is True
    )


def test_cache_read_missing_or_corrupt_returns_none(tmp_path):
    assert tv._read_transcription_cache(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert tv._read_transcription_cache(bad) is None


def test_cache_mismatch_on_model_device_mtime_and_size(tmp_path):
    input_file, srt, tsv, jsn = _artifacts(tmp_path)
    cache_path = tv._transcription_cache_path(tmp_path)
    tv._save_transcription_cache(
        cache_path, input_file, "large-v3", srt, tsv, jsn, device="cpu"
    )
    cache = tv._read_transcription_cache(cache_path)
    assert (
        tv._cache_matches(cache, input_file, "medium", srt, tsv, jsn, device="cpu")
        is False
    )
    assert (
        tv._cache_matches(cache, input_file, "large-v3", srt, tsv, jsn, device="cuda")
        is False
    )
    assert tv._cache_matches(None, input_file, "large-v3", srt, tsv, jsn) is False
    # Touch the input (new mtime, same content) -> stale.
    stat = input_file.stat()
    os.utime(input_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    assert (
        tv._cache_matches(cache, input_file, "large-v3", srt, tsv, jsn, device="cpu")
        is False
    )
    # Grow the input (new size) -> stale.
    input_file.write_bytes(b"video-bytes-longer")
    assert (
        tv._cache_matches(cache, input_file, "large-v3", srt, tsv, jsn, device="cpu")
        is False
    )


def test_cache_mismatch_when_artifact_is_empty_or_missing(tmp_path):
    input_file, srt, tsv, jsn = _artifacts(tmp_path)
    cache_path = tv._transcription_cache_path(tmp_path)
    tv._save_transcription_cache(
        cache_path, input_file, "large-v3", srt, tsv, jsn, device="cpu"
    )
    cache = tv._read_transcription_cache(cache_path)
    srt.write_bytes(b"")
    assert (
        tv._cache_matches(cache, input_file, "large-v3", srt, tsv, jsn, device="cpu")
        is False
    )
    srt.write_bytes(b"data")
    jsn.unlink()
    assert (
        tv._cache_matches(cache, input_file, "large-v3", srt, tsv, jsn, device="cpu")
        is False
    )


def test_transcript_artifacts_ok(tmp_path):
    input_file, srt, tsv, jsn = _artifacts(tmp_path)
    assert tv._transcript_artifacts_ok(srt, tsv, jsn) is True
    assert tv._transcript_artifacts_ok(srt, tsv, tmp_path / "missing.json") is False
    assert tv._transcript_artifacts_ok(srt, tsv, None) is False
    assert tv._transcript_artifacts_ok("", tsv, jsn) is False
    srt.write_bytes(b"")
    assert tv._transcript_artifacts_ok(srt, tsv, jsn) is False


def test_build_cache_records_absolute_paths_and_device_default(tmp_path):
    input_file, srt, tsv, jsn = _artifacts(tmp_path)
    cache = tv._build_transcription_cache(input_file, "large-v3", srt, tsv, jsn)
    assert cache["device"] == "auto"
    assert cache["input_file"] == os.path.abspath(input_file)
    assert cache["outputs"]["srt"] == os.path.abspath(srt)


# --- SRT / VTT parsing -----------------------------------------------------------

SRT_SAMPLE = (
    "1\r\n"
    "00:00:05,000 --> 00:00:10,500\r\n"
    "Hello <b>world</b>\r\n"
    "\r\n"
    "2\r\n"
    "00:01:00,000 --> 00:01:02,000\r\n"
    "Second line\r\n"
)

VTT_SAMPLE = """WEBVTT

00:00:05.000 --> 00:00:10.000 align:start position:0%
Hello <c>world</c> &amp; friends

NOTE this is a note

00:01:00.000 --> 00:01:02.000
Second line"""


def test_parse_srt_handles_crlf_and_strips_tags(tmp_path):
    path = tmp_path / "in.srt"
    path.write_text(SRT_SAMPLE, encoding="utf-8")
    segments = tv.parse_srt(str(path))
    assert segments == [
        {"start": 5.0, "end": 10.5, "text": "Hello world"},
        {"start": 60.0, "end": 62.0, "text": "Second line"},
    ]


def test_parse_srt_empty_and_missing_file(tmp_path):
    empty = tmp_path / "empty.srt"
    empty.write_text("", encoding="utf-8")
    assert tv.parse_srt(str(empty)) == []
    assert tv.parse_srt(str(tmp_path / "missing.srt")) is None


def test_parse_srt_two_part_timestamps(tmp_path):
    path = tmp_path / "short.srt"
    path.write_text("1\n00:05,000 --> 00:07,500\nHi\n", encoding="utf-8")
    segments = tv.parse_srt(str(path))
    assert segments == [{"start": 5.0, "end": 7.5, "text": "Hi"}]


def test_parse_vtt_skips_headers_notes_and_settings(tmp_path):
    path = tmp_path / "in.vtt"
    path.write_text(VTT_SAMPLE, encoding="utf-8")
    segments = tv.parse_vtt(str(path))
    assert segments == [
        {"start": 5.0, "end": 10.0, "text": "Hello world  friends"},
        {"start": 60.0, "end": 62.0, "text": "Second line"},
    ]


def test_parse_vtt_missing_file_returns_none(tmp_path):
    assert tv.parse_vtt(str(tmp_path / "missing.vtt")) is None


def test_parse_vtt_last_block_without_trailing_blank_line(tmp_path):
    path = tmp_path / "tail.vtt"
    path.write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nTail text", encoding="utf-8"
    )
    assert tv.parse_vtt(str(path)) == [{"start": 1.0, "end": 2.0, "text": "Tail text"}]


# --- model candidates / backend / placeholder ------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("large-v3-turbo", ["large-v3-turbo", "large-v3", "medium"]),
        ("turbo", ["turbo", "large-v3", "medium"]),
        ("large-v3", ["large-v3"]),
        ("medium", ["medium", "large-v3"]),
        ("", ["large-v3"]),
        (None, ["large-v3"]),
    ],
)
def test_resolve_model_candidates(name, expected):
    assert tv.resolve_model_candidates(name) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, False),
        ("0", False),
        ("no", False),
        ("", False),
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
    ],
)
def test_placeholder_allowed(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("VIRALCUTTER_ALLOW_PLACEHOLDER", raising=False)
    else:
        monkeypatch.setenv("VIRALCUTTER_ALLOW_PLACEHOLDER", value)
    assert tv._placeholder_allowed() is expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, "auto"),
        ("auto", "auto"),
        ("whisperx", "whisperx"),
        ("faster-whisper", "faster-whisper"),
        ("bogus", "auto"),
        (" WHISPERX ", "whisperx"),
    ],
)
def test_transcription_backend_preference(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("VIRALCUTTER_TRANSCRIPTION_BACKEND", raising=False)
    else:
        monkeypatch.setenv("VIRALCUTTER_TRANSCRIPTION_BACKEND", value)
    assert tv._transcription_backend_preference() == expected
