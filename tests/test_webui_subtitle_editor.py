# -*- coding: utf-8 -*-
"""Tests for webui.subtitle_editor.

Covers timestamp helpers, atomic JSON writes, transcription loading,
video-candidate resolution, preview-clip building, editor saves,
editable-file listing, segment export and specific-video rendering.

Deliberately excluded (covered by tests/test_pro_components.py):
format_timestamp(1.9999) / format_timestamp(-2) and the basic
parse_timestamp("01:02:03,500") round-trip.
"""

import hashlib
import json
import os
import sys
import types
from pathlib import Path

import pytest

from webui import subtitle_editor
from webui.subtitle_editor import (
    _atomic_write_json,
    build_preview_clip,
    export_all_segments,
    format_timestamp,
    list_editable_files,
    load_transcription_for_editor,
    parse_timestamp,
    render_specific_video,
    resolve_video_candidate,
    save_editor_changes,
)


def _write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _transcription(segments):
    return {"language": "ar", "segments": segments}


def _segment(start, end, text, words=None):
    if words is None:
        words = [
            {"word": w, "start": start, "end": end, "score": 0.9} for w in text.split()
        ]
    return {"start": start, "end": end, "text": text, "words": words}


def _project(tmp_path, name="final-output000_processed.json", segments=None):
    """Create VIRALS/<proj>/subs/<name> plus final/ and burned_sub/ dirs."""
    project = tmp_path / "proj"
    subs = project / "subs"
    subs.mkdir(parents=True)
    json_path = subs / name
    _write_json(json_path, _transcription(segments or []))
    (project / "final").mkdir()
    (project / "burned_sub").mkdir()
    return project, json_path


# ---------------------------------------------------------------------------
# format_timestamp / parse_timestamp
# ---------------------------------------------------------------------------


def test_format_timestamp_zero_and_minutes():
    assert format_timestamp(0) == "00:00:00,000"
    assert format_timestamp(61.5) == "00:01:01,500"
    assert format_timestamp(3661.234) == "01:01:01,234"


def test_format_timestamp_accepts_numeric_string():
    assert format_timestamp("12.5") == "00:00:12,500"


def test_format_timestamp_non_numeric_and_non_finite_become_zero():
    assert format_timestamp(None) == "00:00:00,000"
    assert format_timestamp("abc") == "00:00:00,000"
    assert format_timestamp(float("nan")) == "00:00:00,000"
    assert format_timestamp(float("inf")) == "00:00:00,000"
    assert format_timestamp(-0.4) == "00:00:00,000"


def test_format_timestamp_hours_are_not_capped():
    # Quirk: hours grow past 24 instead of wrapping to days.
    assert format_timestamp(90061.001) == "25:01:01,001"


def test_parse_timestamp_two_part_and_dot_separator():
    assert parse_timestamp("01:02") == pytest.approx(62.0)
    assert parse_timestamp("00:00:01.500") == pytest.approx(1.5)
    assert parse_timestamp("00:00:01,500") == pytest.approx(1.5)


def test_parse_timestamp_invalid_inputs_return_zero():
    # Quirk: a bare seconds value ("5") is not a recognised format and
    # returns 0.0 instead of 5.0.
    assert parse_timestamp("5") == 0.0
    assert parse_timestamp("") == 0.0
    assert parse_timestamp("abc") == 0.0
    assert parse_timestamp(None) == 0.0
    assert parse_timestamp(12) == 0.0


# ---------------------------------------------------------------------------
# _atomic_write_json
# ---------------------------------------------------------------------------


def test_atomic_write_json_creates_and_replaces(tmp_path):
    path = tmp_path / "state.json"
    _atomic_write_json(str(path), {"a": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}
    _atomic_write_json(str(path), {"a": 2, "b": "مرحبا"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 2, "b": "مرحبا"}
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_json_keeps_unicode_unescaped(tmp_path):
    path = tmp_path / "state.json"
    _atomic_write_json(str(path), {"text": "مرحبا"})
    assert "مرحبا" in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# load_transcription_for_editor
# ---------------------------------------------------------------------------


def test_load_transcription_missing_file_returns_empty():
    assert load_transcription_for_editor("/nonexistent/file.json") == []


def test_load_transcription_flattens_segments(tmp_path):
    _, json_path = _project(
        tmp_path,
        segments=[
            _segment(0.0, 2.5, "مرحبا بالعالم"),
            _segment(2.5, 4.0, "  نص ثان  "),
        ],
    )
    assert load_transcription_for_editor(str(json_path)) == [
        ["00:00:00,000", "00:00:02,500", "مرحبا بالعالم"],
        ["00:00:02,500", "00:00:04,000", "نص ثان"],
    ]


def test_load_transcription_missing_segments_key_returns_empty(tmp_path):
    path = tmp_path / "empty.json"
    _write_json(path, {"language": "ar"})
    assert load_transcription_for_editor(str(path)) == []


def test_load_transcription_missing_segment_fields_use_defaults(tmp_path):
    path = tmp_path / "sparse.json"
    _write_json(path, {"segments": [{}]})
    assert load_transcription_for_editor(str(path)) == [
        ["00:00:00,000", "00:00:00,000", ""]
    ]


def test_load_transcription_corrupt_json_returns_empty(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_transcription_for_editor(str(path)) == []


def test_load_transcription_non_dict_segment_discards_whole_file(tmp_path):
    # Quirk: one malformed segment raises inside the loop, the except
    # swallows it and the whole file loads as [] instead of returning
    # the good segments.
    path = tmp_path / "mixed.json"
    _write_json(
        path,
        {"segments": [_segment(0, 1, "good"), "not-a-dict"]},
    )
    assert load_transcription_for_editor(str(path)) == []


# ---------------------------------------------------------------------------
# resolve_video_candidate
# ---------------------------------------------------------------------------


def test_resolve_video_candidate_missing_json_returns_none():
    assert resolve_video_candidate("/nonexistent/file.json") is None


def test_resolve_video_candidate_exact_match(tmp_path):
    project, json_path = _project(tmp_path)
    video = project / "final" / "final-output000_processed.mp4"
    video.write_bytes(b"fake")
    assert resolve_video_candidate(str(json_path)) == str(video)


def test_resolve_video_candidate_strips_processed_suffix(tmp_path):
    project, json_path = _project(tmp_path)
    video = project / "final" / "final-output000.mp4"
    video.write_bytes(b"fake")
    assert resolve_video_candidate(str(json_path)) == str(video)


def test_resolve_video_candidate_prefers_exact_over_stripped(tmp_path):
    project, json_path = _project(tmp_path)
    exact = project / "final" / "final-output000_processed.mp4"
    stripped = project / "final" / "final-output000.mp4"
    exact.write_bytes(b"exact")
    stripped.write_bytes(b"stripped")
    assert resolve_video_candidate(str(json_path)) == str(exact)


def test_resolve_video_candidate_burned_sub_fallback(tmp_path):
    project, json_path = _project(tmp_path)
    video = project / "burned_sub" / "final-output000_processed_subtitled.mp4"
    video.write_bytes(b"fake")
    assert resolve_video_candidate(str(json_path)) == str(video)


def test_resolve_video_candidate_index_scan_fallback(tmp_path):
    project, json_path = _project(tmp_path)
    video = project / "final" / "clip-000-other.mp4"
    video.write_bytes(b"fake")
    assert resolve_video_candidate(str(json_path)) == str(video)


def test_resolve_video_candidate_index_scan_uses_substring_match(tmp_path):
    # Quirk: the fallback matches the 3-digit index as a substring, so
    # "000" also matches a file named "...1000.mp4".
    project, json_path = _project(tmp_path)
    video = project / "final" / "clip-1000.mp4"
    video.write_bytes(b"fake")
    assert resolve_video_candidate(str(json_path)) == str(video)


def test_resolve_video_candidate_no_match_returns_none(tmp_path):
    _, json_path = _project(tmp_path)
    assert resolve_video_candidate(str(json_path)) is None


def test_resolve_video_candidate_no_video_folder_returns_none(tmp_path):
    project = tmp_path / "proj"
    subs = project / "subs"
    subs.mkdir(parents=True)
    json_path = subs / "final-output000_processed.json"
    _write_json(json_path, _transcription([]))
    assert resolve_video_candidate(str(json_path)) is None


# ---------------------------------------------------------------------------
# build_preview_clip
# ---------------------------------------------------------------------------


def test_build_preview_clip_missing_json(tmp_path):
    path, msg = build_preview_clip(str(tmp_path / "nope.json"), 0)
    assert path is None
    assert msg == "JSON not found."


def test_build_preview_clip_row_out_of_range(tmp_path):
    _, json_path = _project(tmp_path, segments=[_segment(0, 2, "hi")])
    assert build_preview_clip(str(json_path), 1) == (None, "Row out of range.")
    assert build_preview_clip(str(json_path), -1) == (None, "Row out of range.")


def test_build_preview_clip_no_source_video(tmp_path):
    _, json_path = _project(tmp_path, segments=[_segment(0, 2, "hi")])
    path, msg = build_preview_clip(str(json_path), 0)
    assert path is None
    assert msg == "Source video not found."


def test_build_preview_clip_invokes_ffmpeg_with_expected_args(tmp_path, monkeypatch):
    project, json_path = _project(tmp_path, segments=[_segment(1.0, 3.5, "hi")])
    video = project / "final" / "final-output000.mp4"
    video.write_bytes(b"fake")
    calls = []

    def fake_run(cmd, check):
        calls.append(cmd)
        (project / "preview_clips").mkdir(exist_ok=True)
        Path(cmd[-1]).write_bytes(b"preview")

    monkeypatch.setattr(subtitle_editor.subprocess, "run", fake_run)
    path, msg = build_preview_clip(str(json_path), 0)

    assert msg == "Preview ready: row 1"
    assert path.endswith(".mp4")
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-ss") + 1] == "1.000"
    assert cmd[cmd.index("-t") + 1] == "2.500"
    assert cmd[cmd.index("-i") + 1] == str(video)


def test_build_preview_clip_short_segment_gets_minimum_duration(tmp_path, monkeypatch):
    project, json_path = _project(tmp_path, segments=[_segment(1.0, 1.2, "hi")])
    video = project / "final" / "final-output000.mp4"
    video.write_bytes(b"fake")
    calls = []
    monkeypatch.setattr(
        subtitle_editor.subprocess, "run", lambda cmd, check: calls.append(cmd)
    )
    build_preview_clip(str(json_path), 0)
    # 1.2 - 1.0 = 0.2s, clamped to the 0.5s minimum.
    assert calls[0][calls[0].index("-t") + 1] == "0.500"


def test_build_preview_clip_end_before_start_extends_by_1_5s(tmp_path, monkeypatch):
    project, json_path = _project(tmp_path, segments=[_segment(3.0, 2.0, "hi")])
    video = project / "final" / "final-output000.mp4"
    video.write_bytes(b"fake")
    calls = []
    monkeypatch.setattr(
        subtitle_editor.subprocess, "run", lambda cmd, check: calls.append(cmd)
    )
    build_preview_clip(str(json_path), 0)
    assert calls[0][calls[0].index("-ss") + 1] == "3.000"
    assert calls[0][calls[0].index("-t") + 1] == "1.500"


def test_build_preview_clip_uses_cached_file_without_ffmpeg(tmp_path, monkeypatch):
    project, json_path = _project(tmp_path, segments=[_segment(1.0, 3.5, "hi")])
    video = project / "final" / "final-output000.mp4"
    video.write_bytes(b"fake")
    preview_dir = project / "preview_clips"
    preview_dir.mkdir()
    key = hashlib.md5(
        f"{json_path}|0|1.0|3.5|{os.path.getmtime(video)}".encode()
    ).hexdigest()[:12]
    cached = preview_dir / f"preview_{key}.mp4"
    cached.write_bytes(b"cached")
    monkeypatch.setattr(
        subtitle_editor.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ffmpeg called")),
    )
    path, msg = build_preview_clip(str(json_path), 0)
    assert path == str(cached)
    assert msg == "Preview ready: row 1"


def test_build_preview_clip_ffmpeg_failure_returns_error(tmp_path, monkeypatch):
    project, json_path = _project(tmp_path, segments=[_segment(0, 2, "hi")])
    video = project / "final" / "final-output000.mp4"
    video.write_bytes(b"fake")

    def failing_run(cmd, check):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(subtitle_editor.subprocess, "run", failing_run)
    path, msg = build_preview_clip(str(json_path), 0)
    assert path is None
    assert msg == "Preview error: ffmpeg exploded"


# ---------------------------------------------------------------------------
# save_editor_changes
# ---------------------------------------------------------------------------


def test_save_editor_changes_missing_file():
    assert save_editor_changes("/nonexistent.json", []) == (
        "Error: Original file not found."
    )


def test_save_editor_changes_same_word_count_keeps_timings(tmp_path):
    _, json_path = _project(
        tmp_path,
        segments=[
            _segment(
                0.0,
                2.0,
                "hello world",
                words=[
                    {"word": "hello", "start": 0.0, "end": 1.0, "score": 0.8},
                    {"word": "world", "start": 1.0, "end": 2.0, "score": 0.7},
                ],
            )
        ],
    )
    result = save_editor_changes(
        str(json_path), [["00:00:00,000", "00:00:02,000", "salut monde"]]
    )
    assert result == "Success: Subtitles updated."
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["segments"][0]["words"] == [
        {"word": "salut", "start": 0.0, "end": 1.0, "score": 0.8},
        {"word": "monde", "start": 1.0, "end": 2.0, "score": 0.7},
    ]


def test_save_editor_changes_word_count_mismatch_interpolates(tmp_path):
    _, json_path = _project(tmp_path, segments=[_segment(0.0, 3.0, "one two")])
    result = save_editor_changes(
        str(json_path), [["00:00:00,000", "00:00:03,000", "a b c"]]
    )
    assert result == "Success: Subtitles updated."
    words = json.loads(json_path.read_text(encoding="utf-8"))["segments"][0]["words"]
    assert words == [
        {"word": "a", "start": 0.0, "end": 1.0, "score": 0.99},
        {"word": "b", "start": 1.0, "end": 2.0, "score": 0.99},
        {"word": "c", "start": 2.0, "end": 3.0, "score": 0.99},
    ]


def test_save_editor_changes_empty_text_clears_words(tmp_path):
    _, json_path = _project(tmp_path, segments=[_segment(0.0, 2.0, "hello")])
    assert (
        save_editor_changes(str(json_path), [["00:00:00,000", "00:00:02,000", "   "]])
        == "Success: Subtitles updated."
    )
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["segments"][0] == {
        "start": 0.0,
        "end": 2.0,
        "text": "",
        "words": [],
    }


def test_save_editor_changes_new_row_beyond_original_interpolates(tmp_path):
    _, json_path = _project(tmp_path, segments=[_segment(0.0, 2.0, "hello")])
    result = save_editor_changes(
        str(json_path),
        [
            ["00:00:00,000", "00:00:02,000", "hello"],
            ["00:00:02,000", "00:00:04,000", "new row here"],
        ],
    )
    assert result == "Success: Subtitles updated."
    segments = json.loads(json_path.read_text(encoding="utf-8"))["segments"]
    assert len(segments) == 2
    assert segments[1]["words"][0]["start"] == pytest.approx(2.0)


def test_save_editor_changes_empty_data_clears_segments(tmp_path):
    _, json_path = _project(tmp_path, segments=[_segment(0.0, 2.0, "hello")])
    assert save_editor_changes(str(json_path), []) == "Success: Subtitles updated."
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["segments"] == []
    assert saved["language"] == "ar"


def test_save_editor_changes_none_data_clears_segments(tmp_path):
    _, json_path = _project(tmp_path, segments=[_segment(0.0, 2.0, "hello")])
    assert save_editor_changes(str(json_path), None) == "Success: Subtitles updated."
    assert json.loads(json_path.read_text(encoding="utf-8"))["segments"] == []


def test_save_editor_changes_rejects_invalid_rows(tmp_path):
    _, json_path = _project(tmp_path, segments=[_segment(0.0, 2.0, "hello")])
    for bad in (["00:00:00,000", "00:00:02,000"], "not-a-row", [1, 2]):
        assert save_editor_changes(str(json_path), [bad]) == (
            "Error: Invalid subtitle row."
        )
    # Nothing was written for the rejected rows.
    assert len(json.loads(json_path.read_text(encoding="utf-8"))["segments"]) == 1


def test_save_editor_changes_rejects_bad_timing(tmp_path):
    _, json_path = _project(tmp_path, segments=[_segment(0.0, 2.0, "hello")])
    assert (
        save_editor_changes(str(json_path), [["00:00:02,000", "00:00:02,000", "x"]])
        == "Error: Subtitle timing must have end > start."
    )
    assert (
        save_editor_changes(str(json_path), [["00:00:03,000", "00:00:01,000", "x"]])
        == "Error: Subtitle timing must have end > start."
    )
    # parse_timestamp("bad") -> 0.0, so end 0.0 <= start 0.0 is rejected too.
    assert save_editor_changes(str(json_path), [["bad", "bad", "x"]]) == (
        "Error: Subtitle timing must have end > start."
    )


def test_save_editor_changes_corrupt_json_returns_error(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{broken", encoding="utf-8")
    result = save_editor_changes(str(path), [["00:00:00,000", "00:00:01,000", "x"]])
    assert result.startswith("Error saving changes:")


def test_save_editor_changes_missing_word_fields_use_defaults(tmp_path):
    _, json_path = _project(
        tmp_path,
        segments=[
            {
                "start": 0.0,
                "end": 2.0,
                "text": "hello world",
                "words": [{"word": "h"}, {"word": "w"}],
            }
        ],
    )
    assert (
        save_editor_changes(
            str(json_path), [["00:00:00,000", "00:00:02,000", "salut monde"]]
        )
        == "Success: Subtitles updated."
    )
    words = json.loads(json_path.read_text(encoding="utf-8"))["segments"][0]["words"]
    assert words == [
        {"word": "salut", "start": 0.0, "end": 2.0, "score": 0.99},
        {"word": "monde", "start": 0.0, "end": 2.0, "score": 0.99},
    ]


# ---------------------------------------------------------------------------
# list_editable_files
# ---------------------------------------------------------------------------


def test_list_editable_files_returns_sorted_processed_json(tmp_path):
    project, _ = _project(tmp_path)
    subs = project / "subs"
    for name in ("b_processed.json", "a_processed.json", "other.json", "c.txt"):
        (subs / name).write_text("{}", encoding="utf-8")
    assert list_editable_files(str(project)) == [
        "a_processed.json",
        "b_processed.json",
        "final-output000_processed.json",
    ]


def test_list_editable_files_missing_dirs_return_empty(tmp_path):
    assert list_editable_files(str(tmp_path / "nope")) == []
    project = tmp_path / "proj"
    project.mkdir()
    assert list_editable_files(str(project)) == []


# ---------------------------------------------------------------------------
# export_all_segments
# ---------------------------------------------------------------------------


def test_export_all_segments_missing_project():
    assert export_all_segments("") == "Project not found."
    assert export_all_segments("/nonexistent") == "Project not found."


def test_export_all_segments_missing_subs_folder(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    assert export_all_segments(str(project)) == "No subtitles folder found."


def test_export_all_segments_no_processed_files(tmp_path):
    project = tmp_path / "proj"
    (project / "subs").mkdir(parents=True)
    assert export_all_segments(str(project)) == "No segment subtitles found to export."


def test_export_all_segments_file_without_index_is_skipped(tmp_path):
    project, _ = _project(tmp_path, name="final-output_processed.json", segments=[])
    assert export_all_segments(str(project)) == "Nothing exported."


def test_export_all_segments_calls_exporter_per_index(tmp_path, monkeypatch):
    from scripts.export_xml_lib import exporter

    project, _ = _project(tmp_path)
    calls = []

    def fake_export_pack(folder, idx, preset):
        calls.append((folder, idx, preset))
        return f"/out/pack_{idx}.zip"

    monkeypatch.setattr(exporter, "export_pack", fake_export_pack)
    result = export_all_segments(str(project))
    assert result == ["/out/pack_0.zip"]
    assert calls == [(str(project), 0, "premiere")]


def test_export_all_segments_exporter_failure_is_recorded(tmp_path, monkeypatch):
    from scripts.export_xml_lib import exporter

    project, _ = _project(tmp_path)
    monkeypatch.setattr(
        exporter,
        "export_pack",
        lambda folder, idx, preset: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    result = export_all_segments(str(project))
    assert result == ["final-output000_processed.json: boom"]


def test_export_all_segments_none_zip_is_not_counted(tmp_path, monkeypatch):
    from scripts.export_xml_lib import exporter

    project, _ = _project(tmp_path)
    monkeypatch.setattr(exporter, "export_pack", lambda *a: None)
    assert export_all_segments(str(project)) == "Nothing exported."


# ---------------------------------------------------------------------------
# render_specific_video
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_main_improved(monkeypatch):
    """Stand-in for the lazily imported heavy main_improved module."""
    module = types.ModuleType("main_improved")
    module.get_subtitle_config = lambda path=None: {
        "highlight_color": "#FFFF00",
        "base_color": "#FFFFFF",
    }
    monkeypatch.setitem(sys.modules, "main_improved", module)
    return module


@pytest.fixture
def stubbed_render(monkeypatch, fake_main_improved):
    """Mock adjust.generate_ass_from_file and burn.burn_video_file."""
    calls = {"ass": [], "burn": []}

    def fake_generate(json_path, ass_path, project_folder, **kwargs):
        calls["ass"].append((json_path, ass_path, project_folder, kwargs))
        Path(ass_path).parent.mkdir(parents=True, exist_ok=True)
        Path(ass_path).write_text("[Script Info]", encoding="utf-8")

    def fake_burn(video, ass_path, output_path):
        calls["burn"].append((video, ass_path, output_path))
        return True, "ok"

    monkeypatch.setattr(subtitle_editor.adjust, "generate_ass_from_file", fake_generate)
    monkeypatch.setattr(subtitle_editor.burn, "burn_video_file", fake_burn)
    return calls


def test_render_specific_video_missing_json():
    assert render_specific_video("") == "Error: JSON file not found."
    assert render_specific_video("/nonexistent.json") == ("Error: JSON file not found.")


def test_render_specific_video_success_exact_name(tmp_path, stubbed_render):
    project, json_path = _project(tmp_path, segments=[_segment(0, 2, "hi")])
    video = project / "final" / "final-output000_processed.mp4"
    video.write_bytes(b"fake")
    result = render_specific_video(str(json_path))
    assert result == "Success! Rendered: final-output000_processed_subtitled.mp4"
    ass_call = stubbed_render["ass"][0]
    assert ass_call[1] == str(project / "subs_ass" / "final-output000_processed.ass")
    assert ass_call[3]["uppercase"] is False
    assert ass_call[3]["face_modes"] == {}
    burn_call = stubbed_render["burn"][0]
    assert burn_call[0] == str(video)
    assert burn_call[2] == str(
        project / "burned_sub" / "final-output000_processed_subtitled.mp4"
    )


def test_render_specific_video_loads_face_modes(tmp_path, stubbed_render):
    project, json_path = _project(tmp_path, segments=[_segment(0, 2, "hi")])
    (project / "final" / "final-output000.mp4").write_bytes(b"fake")
    (project / "face_modes.json").write_text(
        json.dumps({"0": "closeup"}), encoding="utf-8"
    )
    assert render_specific_video(str(json_path)).startswith("Success!")
    assert stubbed_render["ass"][0][3]["face_modes"] == {"0": "closeup"}


def test_render_specific_video_strips_processed_suffix(tmp_path, stubbed_render):
    project, json_path = _project(tmp_path, segments=[_segment(0, 2, "hi")])
    video = project / "final" / "final-output000.mp4"
    video.write_bytes(b"fake")
    assert render_specific_video(str(json_path)).startswith("Success!")
    assert stubbed_render["burn"][0][0] == str(video)


def test_render_specific_video_finds_video_by_output_id(tmp_path, stubbed_render):
    project, json_path = _project(
        tmp_path, name="final-output042_processed.json", segments=[_segment(0, 2, "hi")]
    )
    video = project / "final" / "final-output042.mp4"
    video.write_bytes(b"fake")
    assert render_specific_video(str(json_path)).startswith("Success!")
    assert stubbed_render["burn"][0][0] == str(video)


def test_render_specific_video_burn_failure_reported(
    tmp_path, monkeypatch, stubbed_render
):
    project, json_path = _project(tmp_path, segments=[_segment(0, 2, "hi")])
    (project / "final" / "final-output000.mp4").write_bytes(b"fake")
    monkeypatch.setattr(
        subtitle_editor.burn,
        "burn_video_file",
        lambda *a: (False, "encoder missing"),
    )
    assert render_specific_video(str(json_path)) == "Render Failed: encoder missing"


def test_render_specific_video_no_id_in_name(tmp_path, stubbed_render):
    project, json_path = _project(
        tmp_path, name="final-clip_processed.json", segments=[_segment(0, 2, "hi")]
    )
    result = render_specific_video(str(json_path))
    assert result == "Error: Could not determine video ID from final-clip_processed"
    assert stubbed_render["ass"] == []


def test_render_specific_video_id_but_no_matching_file(tmp_path, stubbed_render):
    project, json_path = _project(
        tmp_path, name="final-output042_processed.json", segments=[_segment(0, 2, "hi")]
    )
    result = render_specific_video(str(json_path))
    assert result.startswith(
        "Error: Could not find video file for ID 042 (from final-output042_processed)"
    )
    assert stubbed_render["ass"] == []


def test_render_specific_video_missing_final_folder_raises(tmp_path, stubbed_render):
    # Quirk: the video search (including os.listdir on the `final`
    # folder) runs BEFORE the try/except that produces "Critical Error",
    # so a missing `final` folder raises FileNotFoundError out of the
    # function instead of returning an error string.
    project = tmp_path / "proj"
    subs = project / "subs"
    subs.mkdir(parents=True)
    json_path = subs / "final-output042_processed.json"
    _write_json(json_path, _transcription([_segment(0, 2, "hi")]))
    with pytest.raises(FileNotFoundError):
        render_specific_video(str(json_path))
