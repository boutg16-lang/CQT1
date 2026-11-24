# -*- coding: utf-8 -*-
"""Tests for webui.editor_core.

Covers the Clip dataclass, the _number guard, EditorState mutations
(add/update/trim/transform), undo/redo, validation of every field,
render_plan, and save/load round-trips including error paths.

Deliberately excluded (covered by tests/test_pro_components.py):
the undo/redo/save/load happy path, invalid-transform state preservation,
non-finite values and bad project shapes on load, and save rejecting NaN.
"""

import json
from dataclasses import asdict

import pytest

from webui.editor_core import ASPECTS, Clip, EditorState, _number

# ---------------------------------------------------------------------------
# Clip dataclass
# ---------------------------------------------------------------------------


def test_clip_defaults():
    clip = Clip("a.mp4")
    assert clip.source == "a.mp4"
    assert clip.start == 0.0
    assert clip.end is None
    assert clip.x == 0.5
    assert clip.y == 0.5
    assert clip.zoom == 1.0
    assert clip.rotation == 0.0
    assert clip.aspect == "original"
    assert clip.text == ""
    assert clip.logo is None


def test_clip_asdict_matches_editor_state_shape():
    clip = Clip("a.mp4", 1.0, 5.0, text="hi", logo="logo.png")
    assert asdict(clip) == {
        "source": "a.mp4",
        "start": 1.0,
        "end": 5.0,
        "x": 0.5,
        "y": 0.5,
        "zoom": 1.0,
        "rotation": 0.0,
        "aspect": "original",
        "text": "hi",
        "logo": "logo.png",
    }


def test_aspects_constant():
    assert ASPECTS == {"original", "9:16", "4:5", "1:1", "16:9"}


# ---------------------------------------------------------------------------
# _number guard
# ---------------------------------------------------------------------------


def test_number_accepts_int_float_and_numeric_string():
    assert _number(3, "start") == 3.0
    assert _number(2.5, "start") == 2.5
    assert _number("4", "start") == 4.0


@pytest.mark.parametrize("bad", [True, False, None, "abc", float("nan"), float("inf")])
def test_number_rejects_bool_missing_and_non_finite(bad):
    with pytest.raises(ValueError, match="start must be finite"):
        _number(bad, "start")


def test_number_rejects_overflow_string():
    with pytest.raises(ValueError, match="start must be finite"):
        _number("1e999", "start")


# ---------------------------------------------------------------------------
# EditorState basics / add_clip
# ---------------------------------------------------------------------------


def test_editor_state_initial_state():
    state = EditorState()
    assert state.version == 1
    assert state.source is None
    assert state.clips == []
    assert state.undo() is False
    assert state.redo() is False


def test_add_clip_defaults_and_explicit_end():
    state = EditorState("input.mp4")
    state.add_clip("a.mp4")
    assert state.clips[0] == {
        "source": "a.mp4",
        "start": 0.0,
        "end": None,
        "x": 0.5,
        "y": 0.5,
        "zoom": 1.0,
        "rotation": 0.0,
        "aspect": "original",
        "text": "",
        "logo": None,
    }
    state.add_clip("b.mp4", 2, 9)
    assert state.clips[1]["start"] == 2.0
    assert state.clips[1]["end"] == 9.0


def test_add_clip_rejects_end_not_after_start_and_leaves_no_undo_entry():
    state = EditorState("input.mp4")
    with pytest.raises(ValueError):
        state.add_clip("a.mp4", 5, 5)
    with pytest.raises(ValueError):
        state.add_clip("a.mp4", 5, 2)
    assert state.clips == []
    assert state.undo() is False


def test_add_clip_rejects_negative_start():
    state = EditorState("input.mp4")
    with pytest.raises(ValueError):
        state.add_clip("a.mp4", -1, 5)
    assert state.clips == []


def test_add_clip_rejects_empty_source():
    state = EditorState("input.mp4")
    with pytest.raises(ValueError):
        state.add_clip("   ", 0, 5)
    assert state.clips == []


def test_add_clip_rejects_non_numeric_start():
    state = EditorState("input.mp4")
    with pytest.raises(ValueError):
        state.add_clip("a.mp4", "soon", 5)
    assert state.clips == []


# ---------------------------------------------------------------------------
# update_clip / trim / set_transform
# ---------------------------------------------------------------------------


def test_update_clip_changes_fields_and_is_undoable():
    state = EditorState("input.mp4")
    state.add_clip("a.mp4", 0, 10)
    state.update_clip(0, text="hello", zoom=2.0)
    assert state.clips[0]["text"] == "hello"
    assert state.clips[0]["zoom"] == 2.0
    assert state.undo()
    assert state.clips[0]["text"] == ""
    assert state.redo()
    assert state.clips[0]["zoom"] == 2.0


def test_update_clip_index_out_of_range():
    state = EditorState("input.mp4")
    state.add_clip("a.mp4", 0, 10)
    with pytest.raises(IndexError):
        state.update_clip(1, text="x")
    with pytest.raises(IndexError):
        state.update_clip(-1, text="x")


def test_update_clip_invalid_change_restores_previous():
    state = EditorState("input.mp4")
    state.add_clip("a.mp4", 0, 10)
    before = dict(state.clips[0])
    with pytest.raises(ValueError):
        state.update_clip(0, zoom=99)
    assert state.clips[0] == before


def test_trim_valid_and_boundary_errors():
    state = EditorState("input.mp4")
    state.add_clip("a.mp4", 0, 10)
    state.trim(0, 2, 8)
    assert state.clips[0]["start"] == 2.0
    assert state.clips[0]["end"] == 8.0
    with pytest.raises(ValueError):
        state.trim(0, 8, 8)
    with pytest.raises(ValueError):
        state.trim(0, 9, 3)
    with pytest.raises(ValueError):
        state.trim(0, -1, 5)
    assert state.clips[0]["start"] == 2.0
    assert state.clips[0]["end"] == 8.0


def test_trim_rejects_non_numeric_bounds_with_type_error():
    # Quirk: trim compares end <= start before any float conversion, so
    # a non-numeric bound raises TypeError, not ValueError.
    state = EditorState("input.mp4")
    state.add_clip("a.mp4", 0, 10)
    with pytest.raises(TypeError):
        state.trim(0, "a", 5)


@pytest.mark.parametrize(
    "field,value", [("x", 0.0), ("x", 1.0), ("y", 0.0), ("y", 1.0)]
)
def test_set_transform_accepts_position_boundaries(field, value):
    state = EditorState("input.mp4")
    state.add_clip("a.mp4", 0, 10)
    state.set_transform(0, **{field: value})
    assert state.clips[0][field] == value


@pytest.mark.parametrize(
    "field,value", [("x", -0.01), ("x", 1.01), ("y", -1), ("y", 2)]
)
def test_set_transform_rejects_position_out_of_range(field, value):
    state = EditorState("input.mp4")
    state.add_clip("a.mp4", 0, 10)
    with pytest.raises(ValueError):
        state.set_transform(0, **{field: value})


@pytest.mark.parametrize("zoom", [0.1, 8.0])
def test_set_transform_accepts_zoom_boundaries(zoom):
    state = EditorState("input.mp4")
    state.add_clip("a.mp4", 0, 10)
    state.set_transform(0, zoom=zoom)
    assert state.clips[0]["zoom"] == zoom


@pytest.mark.parametrize("zoom", [0.09, 8.01])
def test_set_transform_rejects_zoom_out_of_range(zoom):
    state = EditorState("input.mp4")
    state.add_clip("a.mp4", 0, 10)
    with pytest.raises(ValueError):
        state.set_transform(0, zoom=zoom)


@pytest.mark.parametrize("rotation", [-360.0, 360.0])
def test_set_transform_accepts_rotation_boundaries(rotation):
    state = EditorState("input.mp4")
    state.add_clip("a.mp4", 0, 10)
    state.set_transform(0, rotation=rotation)
    assert state.clips[0]["rotation"] == rotation


@pytest.mark.parametrize("rotation", [-360.1, 360.1])
def test_set_transform_rejects_rotation_out_of_range(rotation):
    state = EditorState("input.mp4")
    state.add_clip("a.mp4", 0, 10)
    with pytest.raises(ValueError):
        state.set_transform(0, rotation=rotation)


@pytest.mark.parametrize("aspect", ["original", "9:16", "4:5", "1:1", "16:9"])
def test_set_transform_accepts_each_aspect(aspect):
    state = EditorState("input.mp4")
    state.add_clip("a.mp4", 0, 10)
    state.set_transform(0, aspect=aspect)
    assert state.clips[0]["aspect"] == aspect


def test_set_transform_rejects_unknown_aspect():
    state = EditorState("input.mp4")
    state.add_clip("a.mp4", 0, 10)
    with pytest.raises(ValueError):
        state.set_transform(0, aspect="21:9")


def test_set_transform_rejects_non_numeric_values():
    state = EditorState("input.mp4")
    state.add_clip("a.mp4", 0, 10)
    with pytest.raises(ValueError):
        state.set_transform(0, zoom="big")


def test_set_transform_with_no_changes_still_pushes_undo_entry():
    # Quirk: calling set_transform with no keyword arguments still commits
    # an (unchanged) snapshot, so undo() reports True afterwards.
    state = EditorState("input.mp4")
    state.add_clip("a.mp4", 0, 10)
    state.set_transform(0)
    assert state.undo() is True


# ---------------------------------------------------------------------------
# undo / redo
# ---------------------------------------------------------------------------


def test_undo_redo_multiple_steps_and_redo_cleared_by_new_change():
    state = EditorState("input.mp4")
    state.add_clip("a.mp4", 0, 10)
    state.set_transform(0, zoom=2.0)
    state.set_transform(0, zoom=3.0)
    assert state.undo()
    assert state.clips[0]["zoom"] == 2.0
    assert state.undo()
    assert state.clips[0]["zoom"] == 1.0
    assert state.undo()
    assert state.clips == []
    assert state.undo() is False
    assert state.redo()
    assert len(state.clips) == 1
    # A new change clears the redo stack.
    state.set_transform(0, zoom=4.0)
    assert state.redo() is False
    assert state.clips[0]["zoom"] == 4.0


def test_undo_restores_project_source_too():
    state = EditorState("input.mp4")
    state.add_clip("a.mp4", 0, 10)
    state.source = "changed.mp4"
    state.add_clip("b.mp4", 0, 5)
    assert state.undo()
    assert state.source == "changed.mp4"
    assert state.undo()
    assert state.source == "input.mp4"


def test_failed_mutation_does_not_consume_undo_history():
    state = EditorState("input.mp4")
    state.add_clip("a.mp4", 0, 10)
    with pytest.raises(ValueError):
        state.update_clip(0, zoom=99)
    assert state.undo()
    assert state.clips == []


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_accepts_open_ended_clip_and_missing_optional_fields():
    state = EditorState("input.mp4")
    state.clips = [{"source": "a.mp4"}]
    assert state.validate() is True


def test_validate_rejects_bad_project_source():
    for bad_source in ("", "   ", 123):
        state = EditorState(bad_source)
        with pytest.raises(ValueError):
            state.validate()


def test_validate_rejects_clips_not_a_list():
    state = EditorState("input.mp4")
    state.clips = {"source": "a.mp4"}
    with pytest.raises(ValueError):
        state.validate()


@pytest.mark.parametrize(
    "clip",
    [
        "not-a-dict",
        {"source": ""},
        {"source": "   "},
        {},
        {"source": "a.mp4", "start": -1},
        {"source": "a.mp4", "start": 5, "end": 5},
        {"source": "a.mp4", "start": 5, "end": 2},
        {"source": "a.mp4", "zoom": 0.05},
        {"source": "a.mp4", "zoom": 9},
        {"source": "a.mp4", "x": -0.1},
        {"source": "a.mp4", "y": 1.1},
        {"source": "a.mp4", "rotation": 361},
        {"source": "a.mp4", "rotation": -361},
        {"source": "a.mp4", "aspect": "21:9"},
        {"source": "a.mp4", "text": 42},
        {"source": "a.mp4", "logo": ""},
        {"source": "a.mp4", "logo": 7},
        {"source": "a.mp4", "start": True},
    ],
)
def test_validate_rejects_bad_clip(clip):
    state = EditorState("input.mp4")
    state.clips = [clip]
    with pytest.raises(ValueError):
        state.validate()


def test_validate_accepts_string_numbers_and_valid_logo():
    state = EditorState("input.mp4")
    state.clips = [{"source": "a.mp4", "start": "1", "end": "5", "logo": "logo.png"}]
    assert state.validate() is True


# ---------------------------------------------------------------------------
# render_plan
# ---------------------------------------------------------------------------


def test_render_plan_returns_deep_copy():
    state = EditorState("input.mp4")
    state.add_clip("a.mp4", 0, 10)
    plan = state.render_plan()
    assert plan == {"version": 1, "source": "input.mp4", "clips": state.clips}
    plan["clips"][0]["zoom"] = 99
    plan["source"] = "other.mp4"
    assert state.clips[0]["zoom"] == 1.0
    assert state.source == "input.mp4"


def test_render_plan_validates_first():
    state = EditorState("input.mp4")
    state.clips = [{"source": "a.mp4", "zoom": 99}]
    with pytest.raises(ValueError):
        state.render_plan()


# ---------------------------------------------------------------------------
# save / load
# ---------------------------------------------------------------------------


def test_save_creates_parent_dirs_and_writes_unicode(tmp_path):
    state = EditorState("input.mp4")
    state.add_clip("a.mp4", 0, 10)
    state.update_clip(0, text="مرحبا")
    path = tmp_path / "nested" / "deep" / "project.json"
    state.save(path)
    assert path.exists()
    assert not list(tmp_path.rglob("*.tmp"))
    assert "مرحبا" in path.read_text(encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8"))["clips"][0]["text"] == "مرحبا"


def test_save_load_roundtrip_preserves_all_fields(tmp_path):
    state = EditorState("input.mp4")
    state.add_clip("a.mp4", 1.5, 9.5)
    state.set_transform(0, x=0.2, y=0.8, zoom=1.5, rotation=45, aspect="9:16")
    state.update_clip(0, text="caption", logo="logo.png")
    path = tmp_path / "project.json"
    state.save(path)
    loaded = EditorState.load(path)
    assert loaded.version == 1
    assert loaded.source == "input.mp4"
    assert loaded.clips == state.clips


def test_load_defaults_missing_version_source_and_clips(tmp_path):
    path = tmp_path / "minimal.json"
    path.write_text(json.dumps({}), encoding="utf-8")
    loaded = EditorState.load(path)
    assert loaded.version == 1
    assert loaded.source is None
    assert loaded.clips == []


def test_load_rejects_unsupported_version(tmp_path):
    path = tmp_path / "v2.json"
    path.write_text(
        json.dumps({"version": 2, "source": "a.mp4", "clips": []}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsupported editor project version"):
        EditorState.load(path)


def test_load_rejects_non_integer_version_string(tmp_path):
    path = tmp_path / "badver.json"
    path.write_text(
        json.dumps({"version": "two", "source": "a.mp4", "clips": []}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        EditorState.load(path)


def test_load_accepts_float_version_that_truncates_to_one(tmp_path):
    # Quirk: int(1.9) == 1, so a non-integer version like 1.9 loads fine.
    path = tmp_path / "v19.json"
    path.write_text(
        json.dumps({"version": 1.9, "source": "a.mp4", "clips": []}),
        encoding="utf-8",
    )
    assert EditorState.load(path).version == 1


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        EditorState.load(tmp_path / "nope.json")


def test_load_corrupt_json_raises(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        EditorState.load(path)


def test_load_validates_clips(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            {"version": 1, "source": "a.mp4", "clips": [{"source": "a.mp4", "x": 5}]}
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        EditorState.load(path)


def test_loaded_state_has_empty_undo_history(tmp_path):
    state = EditorState("input.mp4")
    state.add_clip("a.mp4", 0, 10)
    path = tmp_path / "project.json"
    state.save(path)
    loaded = EditorState.load(path)
    assert loaded.undo() is False
    assert loaded.redo() is False
