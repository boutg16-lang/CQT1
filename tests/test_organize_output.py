# -*- coding: utf-8 -*-
"""Direct tests for scripts/organize_output.py — output file organisation.

``sanitize_filename`` is pure. ``organize()`` reads ``tmp/viral_segments.txt``
relative to the current directory and copies videos into ``VIRALS/<title>/``,
so every test runs inside a tmp dir via ``monkeypatch.chdir`` and never
touches real project files. No network, no media processing (fake bytes).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import organize_output as oo

# ---------------------------------------------------------------------------
# sanitize_filename
# ---------------------------------------------------------------------------


def test_sanitize_filename_removes_invalid_chars():
    assert (
        oo.sanitize_filename('My: Video / Title? "x" <y> | z*')
        == "My Video  Title x y  z"
    )
    assert oo.sanitize_filename("a\\b*c") == "abc"


def test_sanitize_filename_strips_and_keeps_the_rest():
    assert oo.sanitize_filename("  spaced  ") == "spaced"
    assert oo.sanitize_filename("normal-name_1.mp4") == "normal-name_1.mp4"


def test_sanitize_filename_all_invalid_becomes_empty():
    assert oo.sanitize_filename("///") == ""
    assert oo.sanitize_filename("") == ""


# ---------------------------------------------------------------------------
# organize()
# ---------------------------------------------------------------------------


def _write_meta(folder, segments):
    (folder / "tmp").mkdir(exist_ok=True)
    (folder / "tmp" / "viral_segments.txt").write_text(
        json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8"
    )


def test_organize_missing_metadata_returns(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    oo.organize()
    assert "not found" in capsys.readouterr().out
    assert not (tmp_path / "VIRALS").exists()


def test_organize_malformed_metadata_returns(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp" / "viral_segments.txt").write_text("{bad json", encoding="utf-8")
    oo.organize()
    assert "Error reading metadata" in capsys.readouterr().out
    assert not (tmp_path / "VIRALS").exists()


def test_organize_empty_segments(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write_meta(tmp_path, [])
    oo.organize()
    assert "0 virals saved" in capsys.readouterr().out
    assert (tmp_path / "VIRALS").is_dir()


def test_organize_happy_path_burned_subtitles(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write_meta(tmp_path, [{"title": "My: First *Clip*", "score": 1}])
    (tmp_path / "burned_sub").mkdir()
    video = tmp_path / "burned_sub" / "output000_original_scale_subtitled.mp4"
    video.write_bytes(b"fakevideo")

    oo.organize()

    folder = tmp_path / "VIRALS" / "My First Clip"
    assert (folder / "My First Clip.mp4").read_bytes() == b"fakevideo"
    saved = json.loads((folder / "My First Clip.json").read_text(encoding="utf-8"))
    assert saved == {"title": "My: First *Clip*", "score": 1}
    assert "Saved: My First Clip" in capsys.readouterr().out


def test_organize_falls_back_to_final_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_meta(tmp_path, [{"title": "Second"}])
    (tmp_path / "final").mkdir()
    (tmp_path / "final" / "output000_original_scale.mp4").write_bytes(b"finalcut")

    oo.organize()

    assert (tmp_path / "VIRALS" / "Second" / "Second.mp4").read_bytes() == b"finalcut"


def test_organize_missing_video_is_skipped(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write_meta(
        tmp_path,
        [
            {"title": "Missing"},
            {"title": "Present"},
        ],
    )
    (tmp_path / "burned_sub").mkdir()
    (tmp_path / "burned_sub" / "output001_original_scale_subtitled.mp4").write_bytes(
        b"v2"
    )

    oo.organize()
    out = capsys.readouterr().out

    # The segment folder is created before the video lookup, so it exists
    # but stays empty when the video is missing; nothing is saved for it.
    missing_folder = tmp_path / "VIRALS" / "Missing"
    assert missing_folder.is_dir()
    assert list(missing_folder.iterdir()) == []
    assert (tmp_path / "VIRALS" / "Present" / "Present.mp4").exists()
    assert "Could not find video file for segment 1" in out
    assert "1 virals saved" in out


def test_organize_empty_title_after_sanitising_uses_fallback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_meta(tmp_path, [{"title": "???"}])
    (tmp_path / "burned_sub").mkdir()
    (tmp_path / "burned_sub" / "output000_original_scale_subtitled.mp4").write_bytes(
        b"v"
    )

    oo.organize()

    assert (tmp_path / "VIRALS" / "Viral_Segment_1" / "Viral_Segment_1.mp4").exists()


def test_organize_missing_title_key_uses_fallback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_meta(tmp_path, [{"score": 9}])
    (tmp_path / "burned_sub").mkdir()
    (tmp_path / "burned_sub" / "output000_original_scale_subtitled.mp4").write_bytes(
        b"v"
    )

    oo.organize()

    assert (tmp_path / "VIRALS" / "Viral_Segment_1").is_dir()
