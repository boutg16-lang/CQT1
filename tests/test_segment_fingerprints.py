"""Direct tests for scripts/segment_fingerprints.py.

The module was extracted verbatim from scripts/create_viral_segments.py
(step 2 of the incremental monolith split); these tests exercise the moved
helpers through the new module path and assert the old import path still
resolves to the same objects.
"""

import hashlib
import json
import os
import re
import types

import pytest

from scripts import create_viral_segments as cvs
from scripts import segment_fingerprints as sf

MOVED_NAMES = [
    "SEGMENTS_SCHEMA_VERSION",
    "_normalize_timestamp_digits",
    "_normalize_timestamp_text",
    "_parse_segment_time",
    "_segment_window_fingerprint",
    "prompt_version_fingerprint",
    "segment_cache_staleness_reason",
    "segments_manifest_fingerprint",
    "segments_source_fingerprint",
    "selection_weights_fingerprint",
    "source_video_fingerprint",
    "transcript_fingerprint",
]

HEX16 = re.compile(r"^[0-9a-f]{16}$")


# ---------------------------------------------------------------------------
# Re-export identity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", MOVED_NAMES)
def test_reexport_is_same_object(name):
    assert getattr(cvs, name) is getattr(sf, name)


def test_schema_version_value():
    assert sf.SEGMENTS_SCHEMA_VERSION == "2.1"
    assert cvs.SEGMENTS_SCHEMA_VERSION == "2.1"


# ---------------------------------------------------------------------------
# _normalize_timestamp_digits / _normalize_timestamp_text
# ---------------------------------------------------------------------------


def test_normalize_arabic_indic_digits():
    assert sf._normalize_timestamp_text("٩:٥٠") == "9:50"
    assert sf._normalize_timestamp_text("١٢٫٥") == "12.5"
    assert sf._normalize_timestamp_text("٩٬٥") == "9.5"


def test_normalize_persian_digits():
    assert sf._normalize_timestamp_text("۰:۴۵") == "0:45"
    assert sf._normalize_timestamp_text("۴۵") == "45"


def test_normalize_ascii_unchanged():
    assert sf._normalize_timestamp_text("1:30") == "1:30"
    assert sf._normalize_timestamp_digits("12.5") == "12.5"


# ---------------------------------------------------------------------------
# _parse_segment_time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (90, 90.0),
        (45.5, 45.5),
        ("90", 90.0),
        ("90s", 90.0),
        ("٩٠", 90.0),
        ("1:30", 90.0),
        ("٩:٥٠", 590.0),
        ("1:٩٠", 150.0),
        ("١:٠٢:٠٣", 3723.0),
        ("00:01:30", 90.0),
        ("00:00:12.5", 12.5),
        ("١٢٫٥", 12.5),
        ("٩٫٥", 9.5),
        ("٩٬٥", 9.5),
        ("۴۵", 45.0),
        ("۰:۴۵", 45.0),
        ("٩٠ ثانية", 90.0),
        (0, 0.0),
    ],
)
def test_parse_segment_time_values(value, expected):
    assert sf._parse_segment_time(value) == expected


def test_parse_segment_time_missing_and_malformed_use_default():
    assert sf._parse_segment_time(None, default=3.5) == 3.5
    assert sf._parse_segment_time("", default=2.0) == 2.0
    assert sf._parse_segment_time("soon", default=7.0) == 7.0


def test_parse_segment_time_negative_clamped_to_zero():
    assert sf._parse_segment_time(-5) == 0.0
    assert sf._parse_segment_time("-1:30") == 0.0


# ---------------------------------------------------------------------------
# _segment_window_fingerprint
# ---------------------------------------------------------------------------


def test_window_fingerprint_stable_and_hex():
    fp = sf._segment_window_fingerprint(1.234, 5.678)
    assert HEX16.match(fp)
    assert sf._segment_window_fingerprint(1.234, 5.678) == fp
    # Windows are formatted to 3 decimals, so only a change past that
    # precision alters the fingerprint.
    assert sf._segment_window_fingerprint(1.2344, 5.678) == fp
    assert sf._segment_window_fingerprint(1.2346, 5.678) != fp
    assert sf._segment_window_fingerprint(1.234, 5.679) != fp


def test_window_fingerprint_ignores_title_by_construction():
    # The window identity is only the (start, end) pair.
    assert sf._segment_window_fingerprint(10.0, 20.0) == sf._segment_window_fingerprint(
        10, 20
    )


# ---------------------------------------------------------------------------
# segments_manifest_fingerprint
# ---------------------------------------------------------------------------


def test_manifest_fingerprint_deterministic():
    segments = [
        {"start_time": 1.0, "end_time": 5.0, "title": "First"},
        {"start_time": 10.0, "end_time": 12.5, "title": "Second"},
    ]
    first = sf.segments_manifest_fingerprint(segments)
    assert len(first) == 64
    assert sf.segments_manifest_fingerprint(segments) == first


def test_manifest_fingerprint_ignores_titles():
    base = [{"start_time": 1.0, "end_time": 5.0, "title": "A"}]
    renamed = [{"start_time": 1.0, "end_time": 5.0, "title": "B"}]
    assert sf.segments_manifest_fingerprint(base) == sf.segments_manifest_fingerprint(
        renamed
    )


def test_manifest_fingerprint_sensitive_to_windows_and_order():
    one = [{"start_time": 1.0, "end_time": 5.0}]
    moved = [{"start_time": 1.5, "end_time": 5.0}]
    assert sf.segments_manifest_fingerprint(one) != sf.segments_manifest_fingerprint(
        moved
    )
    pair = [one[0], {"start_time": 10.0, "end_time": 12.0}]
    assert sf.segments_manifest_fingerprint(pair) != sf.segments_manifest_fingerprint(
        list(reversed(pair))
    )


def test_manifest_fingerprint_skips_non_dicts_and_empty():
    assert sf.segments_manifest_fingerprint(
        ["nope", None, 42]
    ) == sf.segments_manifest_fingerprint([])
    assert sf.segments_manifest_fingerprint(None) == sf.segments_manifest_fingerprint(
        []
    )
    expected = hashlib.sha256(
        json.dumps(
            [], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    assert sf.segments_manifest_fingerprint([]) == expected


def test_manifest_fingerprint_parses_arabic_timestamps():
    arabic = [{"start_time": "٩:٥٠", "end_time": "١٠:٠٥"}]
    plain = [{"start_time": 590.0, "end_time": 605.0}]
    assert sf.segments_manifest_fingerprint(arabic) == sf.segments_manifest_fingerprint(
        plain
    )


# ---------------------------------------------------------------------------
# source_video_fingerprint
# ---------------------------------------------------------------------------


def test_source_video_fingerprint_stable_for_unchanged_file(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-video-bytes")
    first = sf.source_video_fingerprint(str(video))
    assert first is not None and len(first) == 40  # sha1 hex
    assert sf.source_video_fingerprint(str(video)) == first


def test_source_video_fingerprint_changes_with_size_and_mtime(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"aaa")
    first = sf.source_video_fingerprint(str(video))
    video.write_bytes(b"aaaa")
    assert sf.source_video_fingerprint(str(video)) != first
    second = sf.source_video_fingerprint(str(video))
    os.utime(video, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    assert sf.source_video_fingerprint(str(video)) != second


def test_source_video_fingerprint_missing_or_empty_path():
    assert sf.source_video_fingerprint("/definitely/not/a/real/file.mp4") is None
    assert sf.source_video_fingerprint("") is None
    assert sf.source_video_fingerprint(None) is None


def test_source_video_fingerprint_uses_name_size_mtime(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")
    stat = os.stat(video)
    payload = "{}|{}|{}".format("clip.mp4", stat.st_size, stat.st_mtime_ns)
    expected = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    assert sf.source_video_fingerprint(str(video)) == expected


# ---------------------------------------------------------------------------
# segments_source_fingerprint
# ---------------------------------------------------------------------------


def test_segments_source_fingerprint_reads_nested_value():
    data = {"source_meta": {"source_video_fp": "abc123"}}
    assert sf.segments_source_fingerprint(data) == "abc123"


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        {"source_meta": None},
        {"source_meta": {}},
        {"source_meta": "x"},
        "text",
    ],
)
def test_segments_source_fingerprint_missing_returns_none(data):
    assert sf.segments_source_fingerprint(data) is None


# ---------------------------------------------------------------------------
# segment_cache_staleness_reason
# ---------------------------------------------------------------------------


def _cache(source_fp="video-v1", config_fp="config-v1", transcript_fp="transcript-v1"):
    return {
        "source_meta": {
            "source_video_fp": source_fp,
            "config_fp": config_fp,
            "transcript_fp": transcript_fp,
        }
    }


def test_staleness_fresh_cache_returns_none():
    assert (
        sf.segment_cache_staleness_reason(
            _cache(), "video-v1", "config-v1", "transcript-v1"
        )
        is None
    )


@pytest.mark.parametrize(
    ("data", "source", "config", "transcript", "expected"),
    [
        (None, "video-v1", "config-v1", None, "invalid_cache"),
        ("text", "video-v1", "config-v1", None, "invalid_cache"),
        ({}, "video-v1", "config-v1", None, "missing_source_metadata"),
        (
            {"source_meta": None},
            "video-v1",
            "config-v1",
            None,
            "missing_source_metadata",
        ),
        (
            _cache(source_fp=None),
            "video-v1",
            "config-v1",
            None,
            "missing_source_fingerprint",
        ),
        (_cache(), None, "config-v1", None, "current_source_unavailable"),
        (_cache(), "video-v2", "config-v1", None, "source_video_changed"),
        (
            _cache(config_fp=None),
            "video-v1",
            "config-v1",
            None,
            "missing_config_fingerprint",
        ),
        (_cache(), "video-v1", None, None, "current_settings_unavailable"),
        (_cache(), "video-v1", "config-v2", None, "settings_changed"),
        (_cache(), "video-v1", "config-v1", None, "current_transcript_unavailable"),
        (
            _cache(transcript_fp=None),
            "video-v1",
            "config-v1",
            "transcript-v1",
            "missing_transcript_fingerprint",
        ),
        (_cache(), "video-v1", "config-v1", "transcript-v2", "transcript_changed"),
    ],
)
def test_staleness_reasons(data, source, config, transcript, expected):
    assert (
        sf.segment_cache_staleness_reason(data, source, config, transcript) == expected
    )


def test_staleness_transcript_from_selection_config():
    cache = {"source_meta": {"source_video_fp": "video-v1", "config_fp": "config-v1"}}
    cache["selection_config"] = {"transcript_fingerprint": "transcript-v1"}
    assert (
        sf.segment_cache_staleness_reason(
            cache, "video-v1", "config-v1", "transcript-v1"
        )
        is None
    )
    assert (
        sf.segment_cache_staleness_reason(
            cache, "video-v1", "config-v1", "transcript-v2"
        )
        == "transcript_changed"
    )


def test_staleness_no_transcript_anywhere_is_fresh():
    cache = {"source_meta": {"source_video_fp": "video-v1", "config_fp": "config-v1"}}
    assert (
        sf.segment_cache_staleness_reason(cache, "video-v1", "config-v1", None) is None
    )


# ---------------------------------------------------------------------------
# prompt_version_fingerprint
# ---------------------------------------------------------------------------


def test_prompt_version_fingerprint_stable_and_short_hex():
    first = sf.prompt_version_fingerprint()
    assert HEX16.match(first)
    assert sf.prompt_version_fingerprint() == first


def test_prompt_version_fingerprint_changes_with_scoring_version(monkeypatch):
    before = sf.prompt_version_fingerprint()
    fake = types.SimpleNamespace(SCORING_VERSION="9.9-test")
    monkeypatch.setattr(sf, "clip_scoring", fake)
    assert sf.prompt_version_fingerprint() != before
    assert HEX16.match(sf.prompt_version_fingerprint())


def test_prompt_version_fingerprint_changes_with_title_schema(monkeypatch):
    before = sf.prompt_version_fingerprint()
    fake = types.SimpleNamespace(TITLE_VALIDATION_SCHEMA_VERSION="9.9-test")
    monkeypatch.setattr(sf, "title_factual", fake)
    assert sf.prompt_version_fingerprint() != before


def test_prompt_version_fingerprint_legacy_when_optional_modules_absent(monkeypatch):
    monkeypatch.setattr(sf, "clip_scoring", None)
    monkeypatch.setattr(sf, "title_factual", None)
    fp = sf.prompt_version_fingerprint()
    assert HEX16.match(fp)
    # Legacy payload: schema version + "legacy" + "legacy" + real prompt text.
    prompt_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(sf.__file__))), "prompt.txt"
    )
    template = open(prompt_path, encoding="utf-8").read()
    payload = "{}|{}|{}|{}".format(
        sf.SEGMENTS_SCHEMA_VERSION, "legacy", "legacy", template
    )
    assert fp == hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def test_prompt_version_fingerprint_survives_unreadable_prompt(monkeypatch):
    def _boom(*args, **kwargs):
        raise OSError("cannot read")

    monkeypatch.setattr("builtins.open", _boom)
    assert HEX16.match(sf.prompt_version_fingerprint())


# ---------------------------------------------------------------------------
# selection_weights_fingerprint
# ---------------------------------------------------------------------------


def test_selection_weights_fingerprint_legacy_when_absent(monkeypatch):
    monkeypatch.setattr(sf, "clip_scoring", None)
    assert sf.selection_weights_fingerprint() == "legacy"


def test_selection_weights_fingerprint_delegates(monkeypatch):
    fake = types.SimpleNamespace(weights_fingerprint=lambda: "weights-abc")
    monkeypatch.setattr(sf, "clip_scoring", fake)
    assert sf.selection_weights_fingerprint() == "weights-abc"


def test_selection_weights_fingerprint_exception_falls_back_to_legacy(monkeypatch):
    def _boom():
        raise RuntimeError("weights unavailable")

    fake = types.SimpleNamespace(weights_fingerprint=_boom)
    monkeypatch.setattr(sf, "clip_scoring", fake)
    assert sf.selection_weights_fingerprint() == "legacy"


# ---------------------------------------------------------------------------
# transcript_fingerprint
# ---------------------------------------------------------------------------


def test_transcript_fingerprint_deterministic_and_short_hex():
    transcript = [
        {"start": 0.0, "end": 2.5, "text": "hello"},
        {"start": 2.5, "end": 5.0, "text": "world"},
    ]
    first = sf.transcript_fingerprint(transcript)
    assert HEX16.match(first)
    assert sf.transcript_fingerprint(transcript) == first


@pytest.mark.parametrize("transcript", [None, []])
def test_transcript_fingerprint_empty_returns_none(transcript):
    assert sf.transcript_fingerprint(transcript) is None


def test_transcript_fingerprint_sensitive_to_text_and_timing():
    base = [{"start": 0.0, "end": 2.5, "text": "hello"}]
    assert sf.transcript_fingerprint(base) != sf.transcript_fingerprint(
        [{"start": 0.0, "end": 2.5, "text": "goodbye"}]
    )
    assert sf.transcript_fingerprint(base) != sf.transcript_fingerprint(
        [{"start": 0.0, "end": 3.0, "text": "hello"}]
    )


def test_transcript_fingerprint_ignores_non_dict_lines():
    base = [{"start": 0.0, "end": 2.5, "text": "hello"}]
    noisy = ["junk", None, *base, 42]
    assert sf.transcript_fingerprint(noisy) == sf.transcript_fingerprint(base)


def test_transcript_fingerprint_rounds_timings():
    base = [{"start": 0.0, "end": 2.5, "text": "hello"}]
    jittered = [{"start": 0.0001, "end": 2.5004, "text": "hello"}]
    assert sf.transcript_fingerprint(jittered) == sf.transcript_fingerprint(base)
