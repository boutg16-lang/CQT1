# -*- coding: utf-8 -*-
"""Tests for webui.subtitle_handler (subtitle presets, HTML preview, video preview).

Covers:
- SUBTITLE_PRESETS contents and shape (all 11 presets).
- apply_preset: known presets (21-tuple in documented order), defaults for
  missing keys, unknown presets (21x gr.skip()).
- generate_preview_html: bold/italic/uppercase/decoration handling, the three
  modes (highlight / word_by_word / no_highlight), border style 3 opaque box,
  highlight-size ratio and its cap, size<=0 guard, and the nested
  sanitize_color rules (hex, missing '#', rgb/rgba, clamping, empty/None,
  malformed input).
- render_preview_video: hex-to-ASS conversion, missing preview.json template,
  ffmpeg command construction, cache-busted output, stale-preview cleanup,
  and error paths — with scripts.adjust_subtitles.generate_ass_from_file and
  subprocess.run mocked (no ffmpeg, no real media, no network).

Deliberately excluded:
- Launching a real Gradio UI or a real ffmpeg render (subprocess is mocked;
  the ffmpeg command is asserted, never executed).
- Windows path escaping in the ffmpeg filter (backslashes cannot occur in
  paths on Linux; the replace() is a no-op here).
- The OSError path inside hex_to_ass when debug_preview.log cannot be
  written (requires a read-only working directory).
- Behaviour of scripts.adjust_subtitles.generate_ass_from_file itself
  (covered by its own tests); here it is mocked and only the call is checked.
"""

import os
import re
import subprocess

import pytest

from webui import subtitle_handler as sh

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PRESET_ARG_NAMES = (
    "font_name",
    "font_size",
    "base_color",
    "highlight_color",
    "outline_color",
    "outline_thickness",
    "shadow_color",
    "shadow_size",
    "bold",
    "italic",
    "uppercase",
    "highlight_size",
    "words_per_block",
    "gap_limit",
    "mode",
    "underline",
    "strikeout",
    "border_style",
    "vertical_position",
    "alignment",
    "remove_punctuation",
)


def preview_kwargs(**overrides):
    """Default generate_preview_html arguments; override any by keyword."""
    kwargs = {
        "font": "Arial",
        "size": 30,
        "color": "#FFFFFF",
        "highlight": "#FFD700",
        "outline": "#000000",
        "outline_thick": 3,
        "shadow": "#000000",
        "shadow_sz": 2,
        "bold": True,
        "italic": False,
        "upper": True,
        "h_size": 36,
        "w_block": 3,
        "gap": 0.25,
        "mode": "highlight",
        "under": False,
        "strike": False,
        "border_s": 1,
        "vert_pos": 180,
        "align": 2,
        "remove_punc": True,
    }
    kwargs.update(overrides)
    return kwargs


def preview_html(**overrides):
    """Call generate_preview_html positionally, in signature order."""
    kw = preview_kwargs(**overrides)
    return sh.generate_preview_html(
        kw["font"],
        kw["size"],
        kw["color"],
        kw["highlight"],
        kw["outline"],
        kw["outline_thick"],
        kw["shadow"],
        kw["shadow_sz"],
        kw["bold"],
        kw["italic"],
        kw["upper"],
        kw["h_size"],
        kw["w_block"],
        kw["gap"],
        kw["mode"],
        kw["under"],
        kw["strike"],
        kw["border_s"],
        kw["vert_pos"],
        kw["align"],
        kw["remove_punc"],
    )


def render_video(monkeypatch, tmp_path, **overrides):
    """Run render_preview_video with generate_ass_from_file + ffmpeg mocked.

    Returns (result, ass_calls, ffmpeg_cmds). The fake ffmpeg creates the
    output video file so the success path is exercised; pass
    ffmpeg_side_effect="raise" / "noop" via overrides to change that.
    """
    preview_dir = tmp_path / "PREVIEW"
    preview_dir.mkdir(exist_ok=True)
    (tmp_path / "preview.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sh, "CURRENT_DIR", str(tmp_path))

    ass_calls = []
    monkeypatch.setattr(
        sh.adjust,
        "generate_ass_from_file",
        lambda **kwargs: ass_calls.append(kwargs),
    )

    ffmpeg_cmds = []
    ffmpeg_mode = overrides.pop("ffmpeg_mode", "create_output")

    def fake_run(cmd, **kwargs):
        ffmpeg_cmds.append(cmd)
        if ffmpeg_mode == "raise":
            raise RuntimeError("ffmpeg not found")
        if ffmpeg_mode == "create_output":
            out_path = cmd[-1]
            with open(out_path, "wb") as f:
                f.write(b"fake mp4")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("time.time", lambda: 1700000000)

    args = {
        "font": "Arial",
        "size": 30,
        "color": "#FFFFFF",
        "highlight": "#FFD700",
        "outline": "#000000",
        "outline_thick": 3,
        "shadow": "#000000",
        "shadow_sz": 2,
        "bold": True,
        "italic": False,
        "upper": True,
        "h_size": 36,
        "w_block": 3,
        "gap": 0.25,
        "mode": "highlight",
        "under": False,
        "strike": False,
        "border_s": 1,
        "vert_pos": 180,
        "align": 2,
        "remove_punc": True,
    }
    args.update(overrides)
    result = sh.render_preview_video(
        args["font"],
        args["size"],
        args["color"],
        args["highlight"],
        args["outline"],
        args["outline_thick"],
        args["shadow"],
        args["shadow_sz"],
        args["bold"],
        args["italic"],
        args["upper"],
        args["h_size"],
        args["w_block"],
        args["gap"],
        args["mode"],
        args["under"],
        args["strike"],
        args["border_s"],
        args["vert_pos"],
        args["align"],
        args["remove_punc"],
    )
    return result, ass_calls, ffmpeg_cmds


# ---------------------------------------------------------------------------
# Module-level facts
# ---------------------------------------------------------------------------


def test_module_paths():
    assert sh.CURRENT_DIR.endswith("webui")
    # WORKING_DIR is the parent of the webui/ directory.
    assert sh.WORKING_DIR == os.path.dirname(sh.CURRENT_DIR)
    assert sh.logger.name == "webui.subtitle_handler"


# ---------------------------------------------------------------------------
# SUBTITLE_PRESETS
# ---------------------------------------------------------------------------

EXPECTED_PRESET_NAMES = {
    "MrBeast Clean Hook",
    "Hormozi (Classic)",
    "Beasty (Loud)",
    "Word Killer (TikTok)",
    "Rapid Fire (Sprint)",
    "Educational Fast",
    "Podcast Viral (Centered)",
    "Drama Emocional",
    "Story Subtitle (Netflix Style)",
    "Neon Cyber",
    "Retro Pixel",
}

COMMON_PRESET_KEYS = {
    "font_name",
    "font_size",
    "base_color",
    "highlight_color",
    "outline_color",
    "outline_thickness",
    "shadow_color",
    "shadow_size",
    "bold",
    "italic",
    "uppercase",
    "highlight_size",
    "words_per_block",
    "gap_limit",
    "mode",
    "underline",
    "strikeout",
    "border_style",
    "vertical_position",
    "alignment",
}


def test_presets_count_and_names():
    assert set(sh.SUBTITLE_PRESETS) == EXPECTED_PRESET_NAMES
    assert len(sh.SUBTITLE_PRESETS) == 11


def test_presets_share_common_keys():
    for name, preset in sh.SUBTITLE_PRESETS.items():
        assert COMMON_PRESET_KEYS <= set(preset), name
        assert set(preset) <= COMMON_PRESET_KEYS | {"remove_punctuation"}, name


def test_retro_pixel_lacks_remove_punctuation():
    # QUIRK: every preset except "Retro Pixel" defines remove_punctuation.
    # apply_preset() papers over this with .get(..., True), so the preset
    # behaves as if remove_punctuation were True; the dict itself is missing
    # the key, so code reading the dict directly sees an inconsistent shape.
    assert "remove_punctuation" not in sh.SUBTITLE_PRESETS["Retro Pixel"]
    for name, preset in sh.SUBTITLE_PRESETS.items():
        if name != "Retro Pixel":
            assert "remove_punctuation" in preset, name


def test_preset_values_are_well_formed():
    hex_color = re.compile(r"^#[0-9A-Fa-f]{6}$")
    for name, p in sh.SUBTITLE_PRESETS.items():
        assert isinstance(p["font_name"], str) and p["font_name"], name
        assert isinstance(p["font_size"], int) and p["font_size"] > 0, name
        assert isinstance(p["highlight_size"], int), name
        assert p["highlight_size"] >= p["font_size"], name
        for key in (
            "base_color",
            "highlight_color",
            "outline_color",
            "shadow_color",
        ):
            assert hex_color.match(p[key]), (name, key, p[key])
        assert isinstance(p["words_per_block"], int) and p["words_per_block"] >= 1, name
        assert 0 < p["gap_limit"] <= 1, name
        assert p["mode"] in {"highlight", "word_by_word", "no_highlight"}, name
        assert p["border_style"] in {1, 3}, name
        assert p["alignment"] == 2, name
        assert 0 < p["vertical_position"] < 1080, name
        for key in (
            "bold",
            "italic",
            "uppercase",
            "underline",
            "strikeout",
        ):
            assert isinstance(p[key], bool), (name, key)
        assert isinstance(p.get("remove_punctuation", True), bool), name


def test_preset_spot_values():
    mrbeast = sh.SUBTITLE_PRESETS["MrBeast Clean Hook"]
    assert mrbeast["font_name"] == "Montserrat-ExtraBold"
    assert mrbeast["highlight_color"] == "#FFD700"
    assert mrbeast["words_per_block"] == 3
    assert sh.SUBTITLE_PRESETS["Word Killer (TikTok)"]["mode"] == "word_by_word"
    assert sh.SUBTITLE_PRESETS["Word Killer (TikTok)"]["words_per_block"] == 1
    assert (
        sh.SUBTITLE_PRESETS["Story Subtitle (Netflix Style)"]["mode"] == "no_highlight"
    )
    assert (
        sh.SUBTITLE_PRESETS["Story Subtitle (Netflix Style)"]["outline_thickness"] == 0
    )
    assert sh.SUBTITLE_PRESETS["Neon Cyber"]["underline"] is True


# ---------------------------------------------------------------------------
# apply_preset
# ---------------------------------------------------------------------------


def test_apply_preset_known_returns_21_tuple_in_documented_order():
    result = sh.apply_preset("MrBeast Clean Hook")
    assert len(result) == 21
    preset = sh.SUBTITLE_PRESETS["MrBeast Clean Hook"]
    for value, key in zip(result, PRESET_ARG_NAMES, strict=True):
        assert value == preset[key], key
    assert result[0] == "Montserrat-ExtraBold"
    assert result[1] == 32
    assert result[11] == 38  # highlight_size
    assert result[14] == "highlight"  # mode
    assert result[18] == 180  # vertical_position
    assert result[19] == 2  # alignment
    assert result[20] is True  # remove_punctuation


def test_apply_preset_all_presets_roundtrip():
    for name, preset in sh.SUBTITLE_PRESETS.items():
        result = sh.apply_preset(name)
        assert len(result) == 21, name
        assert result[0] == preset["font_name"], name
        assert result[14] == preset["mode"], name
        assert result[18] == preset["vertical_position"], name
        assert result[19] == preset["alignment"], name
        assert result[20] == preset.get("remove_punctuation", True), name


def test_apply_preset_retro_pixel_defaults_remove_punctuation_true():
    # QUIRK: "Retro Pixel" has no remove_punctuation key; apply_preset()
    # silently defaults it to True via .get(). Same for vertical_position
    # and alignment, though every current preset defines those two.
    result = sh.apply_preset("Retro Pixel")
    assert result[20] is True


@pytest.mark.parametrize(
    "unknown", ["", "mrbeast clean hook", "MrBeast", "Does Not Exist", None]
)
def test_apply_preset_unknown_returns_21_gr_skip(unknown):
    result = sh.apply_preset(unknown)
    assert len(result) == 21
    # Gradio 6.32: gr.skip() is a {'__type__': 'update'} dict, and the tuple
    # repeats the *same* dict object 21 times (built as (gr.skip(),) * 21).
    assert all(isinstance(v, dict) and v.get("__type__") == "update" for v in result)
    assert all(v is result[0] for v in result)
    # Unknown names are case-sensitive: exact match only.
    assert result != sh.apply_preset("MrBeast Clean Hook")


def test_apply_preset_unknown_skip_is_not_a_real_preset_tuple():
    assert sh.apply_preset("nope") != sh.apply_preset("Neon Cyber")


# ---------------------------------------------------------------------------
# generate_preview_html — styling
# ---------------------------------------------------------------------------


def test_preview_html_base_styling():
    html = preview_html()
    assert "font-family: 'Arial', sans-serif;" in html
    assert "font-size: 40px;" in html  # base preview size is forced to 40px
    assert "color: #FFFFFF;" in html
    assert "font-weight: bold;" in html
    assert "font-style: normal;" in html
    assert "text-transform: uppercase;" in html
    assert "text-decoration: none;" in html
    assert "-webkit-text-stroke: 3px #000000;" in html
    assert "text-shadow: 2px 2px 0 #000000;" in html
    assert "PREVIEW" in html


def test_preview_html_plain_styling():
    html = preview_html(bold=False, italic=True, upper=False, under=False, strike=False)
    assert "font-weight: normal;" in html
    assert "font-style: italic;" in html
    assert "text-transform: none;" in html
    assert "text-decoration: none;" in html


@pytest.mark.parametrize(
    "under,strike,expected",
    [
        (True, False, "text-decoration: underline;"),
        (False, True, "text-decoration: line-through;"),
        (True, True, "text-decoration: underline line-through;"),
    ],
)
def test_preview_html_decorations(under, strike, expected):
    assert expected in preview_html(under=under, strike=strike)


def test_preview_html_border_style_3_adds_opaque_box():
    html = preview_html(border_s=3)
    assert "background-color: rgba(0,0,0,0.6);" in html
    assert "padding: 5px 10px; border-radius: 4px;" in html


def test_preview_html_border_style_1_has_no_opaque_box():
    assert "rgba(0,0,0,0.6)" not in preview_html(border_s=1)


def test_preview_html_border_style_other_than_3_has_no_opaque_box():
    # Only border_style == 3 gets the opaque-box simulation; any other value
    # (including 0 or 2) behaves like style 1 here.
    assert "rgba(0,0,0,0.6)" not in preview_html(border_s=2)


# ---------------------------------------------------------------------------
# generate_preview_html — modes and highlight-size ratio
# ---------------------------------------------------------------------------


def test_preview_html_word_by_word_shows_only_preview_word():
    html = preview_html(mode="word_by_word")
    assert "This is a" not in html
    assert "of your subtitles" not in html
    # Only the highlighted word, at the highlight size (40 * 36/30 = 48px).
    assert "font-size: 48.0px; color: #FFD700;" in html
    assert "PREVIEW" in html


def test_preview_html_no_highlight_uses_base_color_and_size():
    html = preview_html(mode="no_highlight")
    assert "This is a" in html
    assert "of your subtitles" in html
    assert "font-size: 40px; color: #FFFFFF;" in html
    # The sentence span uses the base color/size, not the highlight ones.
    assert '<span style="font-size: 40px; color: #FFFFFF;' in html


def test_preview_html_highlight_mode_uses_highlight_color_and_size():
    html = preview_html(mode="highlight")
    assert "This is a" in html
    assert '<span style="font-size: 48.0px; color: #FFD700;' in html


def test_preview_html_unknown_mode_falls_back_to_highlight():
    # QUIRK: any mode other than "word_by_word"/"no_highlight" takes the
    # default highlight branch — an invalid mode is silently treated as
    # "highlight" instead of raising.
    assert preview_html(mode="bogus") == preview_html(mode="highlight")
    assert preview_html(mode="") == preview_html(mode="highlight")


def test_preview_html_highlight_size_ratio():
    # size=20, h_size=30 -> ratio 1.5 -> highlight preview 60px.
    html = preview_html(size=20, h_size=30)
    assert "font-size: 60.0px;" in html
    # size=30, h_size=36 -> ratio 1.2 -> 48px (default case above).
    assert "font-size: 48.0px;" in preview_html()


def test_preview_html_highlight_ratio_capped_at_twice_base():
    # size=10, h_size=100 -> ratio 10 -> would be 400px, capped at 80px.
    html = preview_html(size=10, h_size=100)
    assert "font-size: 80px;" in html
    assert "400" not in html


def test_preview_html_zero_and_negative_size_avoid_division_by_zero():
    # size <= 0 keeps ratio 1.0 instead of dividing by zero.
    assert "font-size: 40.0px;" in preview_html(size=0, h_size=99)
    assert "font-size: 40.0px;" in preview_html(size=-5, h_size=99)


def test_preview_html_unused_parameters_have_no_effect():
    # QUIRK: words_per_block, gap, vertical_position, alignment and
    # remove_punctuation are accepted but never used by the HTML preview —
    # changing them produces byte-identical output.
    base = preview_html()
    assert (
        preview_html(w_block=99, gap=9.9, vert_pos=999, align=1, remove_punc=False)
        == base
    )


# ---------------------------------------------------------------------------
# generate_preview_html — sanitize_color (via the HTML output)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "color,expected_in_html",
    [
        ("#FF0000", "#FF0000"),  # standard hex kept as-is
        ("FF0000", "#FF0000"),  # missing '#' is added
        ("rgb(255, 0, 0)", "#FF0000"),  # rgb() converted to hex
        ("rgba(0, 128, 255, 0.5)", "#0080FF"),  # alpha ignored
        ("rgb(10.9, 20.1, 30.9)", "#0A141E"),  # floats truncated via int()
        ("", "#FFFFFF"),  # empty falls back to white
        (None, "#FFFFFF"),  # None falls back to white
    ],
)
def test_preview_html_sanitize_color(color, expected_in_html):
    html = preview_html(color=color)
    assert f"color: {expected_in_html};" in html


def test_preview_html_sanitize_color_clamps_rgb_range():
    # Out-of-range rgb components are clamped to 0..255.
    html = preview_html(color="rgb(300, 0, 260)")
    assert "color: #FF00FF;" in html


def test_preview_html_sanitize_color_negative_rgb_loses_sign():
    # QUIRK: the rgb regex [\d\.]+ does not match '-', so "rgb(0, -5, 0)"
    # is parsed as g=5, not clamped to 0 — a negative component becomes
    # positive instead of being clamped.
    html = preview_html(color="rgb(0, -5, 0)")
    assert "color: #000500;" in html


def test_preview_html_sanitize_color_malformed_rgb_passed_through():
    # QUIRK: "rgb()" has fewer than 3 numbers, so parsing is skipped and
    # the raw string is returned unchanged (it starts with "rgb", so the
    # '#' prefix branch is also skipped) — invalid CSS ends up in the HTML.
    html = preview_html(color="rgb()")
    assert "color: rgb();" in html


def test_preview_html_sanitize_color_scientific_notation_misparsed():
    # QUIRK: the rgb regex [\d\.]+ does not match 'e', so "rgb(1e400, 0, 0)"
    # is parsed as the four numbers 1, 400, 0, 0 — r=1, g=400 (clamped to
    # 255), b=0 — instead of erroring or treating 1e400 as one component.
    html = preview_html(color="rgb(1e400, 0, 0)")
    assert "color: #01FF00;" in html


def test_preview_html_sanitize_color_short_hex_not_expanded():
    # QUIRK: unlike hex_to_ass in render_preview_video, sanitize_color does
    # NOT expand 3-digit hex — "#FFF" is emitted verbatim.
    html = preview_html(color="#FFF")
    assert "color: #FFF;" in html


def test_preview_html_sanitize_color_leading_whitespace_double_hash():
    # QUIRK: clean strips whitespace for the rgb check, but the '#' prefix
    # branch tests the ORIGINAL string, so "  #FF0000" (leading spaces)
    # becomes "#  #FF0000" — an invalid color.
    html = preview_html(color="  #FF0000")
    assert "color: #  #FF0000;" in html


def test_preview_html_sanitize_applies_to_all_four_colors():
    html = preview_html(
        color="rgb(1, 2, 3)",
        highlight="rgb(4, 5, 6)",
        outline="rgb(7, 8, 9)",
        shadow="rgb(10, 11, 12)",
    )
    assert "color: #010203;" in html
    assert "color: #040506;" in html
    assert "-webkit-text-stroke: 3px #070809;" in html
    assert "text-shadow: 2px 2px 0 #0A0B0C;" in html


# ---------------------------------------------------------------------------
# render_preview_video
# ---------------------------------------------------------------------------


def test_render_preview_video_success(monkeypatch, tmp_path):
    result, ass_calls, ffmpeg_cmds = render_video(monkeypatch, tmp_path)

    assert len(ass_calls) == 1
    call = ass_calls[0]
    assert call["input_path"].endswith("preview.json")
    assert call["output_path"].endswith("preview.ass")
    assert call["base_color"] == "&H00FFFFFF&"
    assert call["highlight_color"] == "&H0000D7FF&"  # #FFD700 -> BGR
    assert call["outline_color"] == "&H00000000&"
    assert call["shadow_color"] == "&H00000000&"
    assert call["base_size"] == 30
    assert call["highlight_size"] == 36
    assert call["words_per_block"] == 3
    assert call["gap_limit"] == 0.25
    assert call["mode"] == "highlight"
    assert call["bold"] == "-1"
    assert call["italic"] == "0"
    assert call["underline"] == "0"
    assert call["strikeout"] == "0"
    assert call["border_style"] == 1
    assert call["font"] == "Arial"
    assert call["face_modes"] == {}
    assert call["remove_punctuation"] is True
    assert call["caption_animation"] == "none"
    assert call["auto_emoji"] is False

    assert len(ffmpeg_cmds) == 1
    cmd = ffmpeg_cmds[0]
    assert cmd[0] == "ffmpeg"
    assert "-y" in cmd
    assert "color=c=0x333333:s=480x854:d=2.4" in cmd
    vf = cmd[cmd.index("-vf") + 1]
    assert vf.startswith("ass='") and vf.endswith("preview.ass'")
    assert "libx264" in cmd
    assert "ultrafast" in cmd
    assert "yuv420p" in cmd
    assert "-an" in cmd
    assert cmd[-1].endswith("preview_render.mp4")

    # gr.update() result points at the timestamped cache-busted copy.
    assert isinstance(result, dict)
    assert result["value"].endswith("preview_render_1700000000.mp4")
    assert result.get("autoplay") is True
    assert (tmp_path / "PREVIEW" / "preview_render_1700000000.mp4").exists()

    # QUIRK: hex_to_ass appends its inputs to debug_preview.log in the
    # CURRENT working directory (not next to the module or the preview dir).
    debug_log = tmp_path / "debug_preview.log"
    assert debug_log.exists()
    assert debug_log.read_text(encoding="utf-8").count("PREVIEW INPUT:") == 4


def test_render_preview_video_missing_template_returns_none(monkeypatch, tmp_path):
    # No preview.json in CURRENT_DIR: warns and returns None without
    # calling the ASS generator or ffmpeg.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sh, "CURRENT_DIR", str(tmp_path))
    ass_calls = []
    monkeypatch.setattr(
        sh.adjust, "generate_ass_from_file", lambda **kw: ass_calls.append(kw)
    )
    ffmpeg_cmds = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: ffmpeg_cmds.append(cmd))

    result = sh.render_preview_video(
        "Arial",
        30,
        "#FFFFFF",
        "#FFD700",
        "#000000",
        3,
        "#000000",
        2,
        True,
        False,
        True,
        36,
        3,
        0.25,
        "highlight",
        False,
        False,
        1,
        180,
        2,
        True,
    )
    assert result is None
    assert ass_calls == []
    assert ffmpeg_cmds == []
    # QUIRK: the PREVIEW directory is created BEFORE the template check,
    # and hex_to_ass already wrote debug_preview.log into the CWD, so both
    # side effects happen even though nothing was rendered.
    assert (tmp_path / "PREVIEW").is_dir()
    assert (tmp_path / "debug_preview.log").exists()


def test_render_preview_video_removes_stale_cache_busted_renders(monkeypatch, tmp_path):
    preview_dir = tmp_path / "PREVIEW"
    preview_dir.mkdir()
    (preview_dir / "preview_render_1111111111.mp4").write_bytes(b"stale")
    (preview_dir / "preview_render_2222222222.mp4").write_bytes(b"stale")
    (preview_dir / "unrelated.mp4").write_bytes(b"keep")

    result, _, _ = render_video(monkeypatch, tmp_path)
    assert result is not None
    assert not (preview_dir / "preview_render_1111111111.mp4").exists()
    assert not (preview_dir / "preview_render_2222222222.mp4").exists()
    assert (preview_dir / "unrelated.mp4").exists()
    assert (preview_dir / "preview_render_1700000000.mp4").exists()


def test_render_preview_video_ffmpeg_error_returns_none(monkeypatch, tmp_path):
    result, ass_calls, ffmpeg_cmds = render_video(
        monkeypatch, tmp_path, ffmpeg_mode="raise"
    )
    assert result is None
    assert len(ass_calls) == 1
    assert len(ffmpeg_cmds) == 1


def test_render_preview_video_no_output_file_returns_none(monkeypatch, tmp_path):
    # ffmpeg "succeeds" but produces no file: falls through to return None.
    result, _, ffmpeg_cmds = render_video(monkeypatch, tmp_path, ffmpeg_mode="noop")
    assert result is None
    assert len(ffmpeg_cmds) == 1


def test_render_preview_video_ass_generation_error_returns_none(monkeypatch, tmp_path):
    (tmp_path / "preview.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sh, "CURRENT_DIR", str(tmp_path))

    def boom(**kwargs):
        raise ValueError("bad json")

    monkeypatch.setattr(sh.adjust, "generate_ass_from_file", boom)
    ffmpeg_cmds = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: ffmpeg_cmds.append(cmd))
    result = sh.render_preview_video(
        "Arial",
        30,
        "#FFFFFF",
        "#FFD700",
        "#000000",
        3,
        "#000000",
        2,
        True,
        False,
        True,
        36,
        3,
        0.25,
        "highlight",
        False,
        False,
        1,
        180,
        2,
        True,
    )
    assert result is None
    assert ffmpeg_cmds == []


def test_render_preview_video_bool_flags_and_passthrough(monkeypatch, tmp_path):
    result, ass_calls, _ = render_video(
        monkeypatch,
        tmp_path,
        bold=False,
        italic=True,
        under=True,
        strike=True,
    )
    assert result is not None
    call = ass_calls[0]
    assert call["bold"] == "0"
    assert call["italic"] == "-1"
    assert call["underline"] == "-1"
    assert call["strikeout"] == "-1"


def test_render_preview_video_animation_and_emoji_passthrough(monkeypatch, tmp_path):
    # caption_animation / auto_emoji are keyword-only extras forwarded to
    # the ASS generator.
    (tmp_path / "preview.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sh, "CURRENT_DIR", str(tmp_path))
    ass_calls = []
    monkeypatch.setattr(
        sh.adjust, "generate_ass_from_file", lambda **kw: ass_calls.append(kw)
    )

    def fake_run(cmd, **kwargs):
        with open(cmd[-1], "wb") as f:
            f.write(b"fake mp4")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("time.time", lambda: 1700000000)
    result = sh.render_preview_video(
        "Arial",
        30,
        "#FFFFFF",
        "#FFD700",
        "#000000",
        3,
        "#000000",
        2,
        True,
        False,
        True,
        36,
        3,
        0.25,
        "highlight",
        False,
        False,
        1,
        180,
        2,
        True,
        caption_animation="karaoke",
        auto_emoji=True,
    )
    assert result is not None
    assert ass_calls[0]["caption_animation"] == "karaoke"
    assert ass_calls[0]["auto_emoji"] is True


@pytest.mark.parametrize(
    "color,expected_ass",
    [
        ("#FF0000", "&H000000FF&"),  # hex -> BGR ASS order
        ("#123456", "&H00563412&"),
        ("#FFF", "&H00FFFFFF&"),  # 3-digit hex IS expanded here
        ("#fff", "&H00FFFFFF&"),  # lower-case 3-digit hex also expanded
        ("", "&H00FFFFFF&"),  # empty -> white
        (None, "&H00FFFFFF&"),  # None -> white
        ("notacolor", "&H00FFFFFF&"),  # wrong length -> white
        ("rgb(255, 0, 0)", "&H000000FF&"),  # rgb() -> BGR ASS
        ("rgba(0, 128, 255, 0.5)", "&H00FF8000&"),  # alpha ignored
        ("rgb(300, 0, 260)", "&H00FF00FF&"),  # clamped to 0..255
        ("#ff0000", "&H000000FF&"),  # lower-case hex, upper-cased output
    ],
)
def test_render_preview_video_hex_to_ass_conversion(
    monkeypatch, tmp_path, color, expected_ass
):
    _, ass_calls, _ = render_video(monkeypatch, tmp_path, color=color)
    assert ass_calls[0]["base_color"] == expected_ass


def test_render_preview_video_hex_to_ass_non_hex_3char_accepted(monkeypatch, tmp_path):
    # QUIRK: a 3-character non-hex string is doubled to 6 chars and emitted
    # verbatim — "zzz" becomes the invalid ASS color &H00ZZZZZZ& instead of
    # falling back to white.
    _, ass_calls, _ = render_video(monkeypatch, tmp_path, color="zzz")
    assert ass_calls[0]["base_color"] == "&H00ZZZZZZ&"


def test_render_preview_video_hex_to_ass_negative_rgb_loses_sign(monkeypatch, tmp_path):
    # QUIRK: same regex as sanitize_color — '-' is not matched, so
    # "rgb(0, -5, 0)" parses g as 5 rather than clamping to 0.
    _, ass_calls, _ = render_video(monkeypatch, tmp_path, color="rgb(0, -5, 0)")
    # r=0, g=5, b=0 -> ASS BGR order "&H00" + BB + GG + RR = "&H00000500&".
    assert ass_calls[0]["base_color"] == "&H00000500&"


def test_render_preview_video_hex_to_ass_malformed_rgb_falls_back(
    monkeypatch, tmp_path
):
    # "rgb()" has no numbers -> falls through to the length checks and the
    # white default (unlike sanitize_color, which passes it through raw).
    _, ass_calls, _ = render_video(monkeypatch, tmp_path, color="rgb()")
    assert ass_calls[0]["base_color"] == "&H00FFFFFF&"
