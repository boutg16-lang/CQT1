# -*- coding: utf-8 -*-
"""Tests for thumbnail Test & Compare variants (round 2, item 4)."""
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import thumbnail_generator as tg  # noqa: E402


def _source(tmp_path, name="frame.png"):
    path = tmp_path / name
    Image.new("RGB", (1920, 1080), (120, 40, 40)).save(path, "PNG")
    return str(path)


def _no_face(monkeypatch):
    monkeypatch.setattr(tg, "_face_bbox", lambda image: None)


def test_variants_titles_differ_and_sheet_written(tmp_path, monkeypatch):
    _no_face(monkeypatch)
    result = tg.generate_variants(
        _source(tmp_path),
        titles=["كشف مفاجئ في القضية", "الحقيقة التي أخفوها"],
        out_dir=str(tmp_path),
        base_name="clip01",
    )
    assert result["ok"] is True
    outs = [v["out"] for v in result["variants"]]
    assert len(outs) == 2
    for path in outs:
        assert os.path.isfile(path)
        assert Image.open(path).size == (1280, 720)
    assert result["variants"][0]["title"] == "كشف مفاجئ في القضية"
    assert result["variants"][1]["title"] == "الحقيقة التي أخفوها"
    assert os.path.isfile(result["sheet"])


def test_variants_single_title_isolates_hook_badge(tmp_path, monkeypatch):
    _no_face(monkeypatch)
    result = tg.generate_variants(
        _source(tmp_path),
        titles=["عنوان واحد فقط"],
        hook="حصري",
        out_dir=str(tmp_path),
        base_name="clip02",
    )
    assert result["ok"] is True
    badges = [v["hook_badge"] for v in result["variants"]]
    assert True in badges and False in badges


def test_variants_respect_max_variants(tmp_path, monkeypatch):
    _no_face(monkeypatch)
    result = tg.generate_variants(
        _source(tmp_path),
        titles=["أ", "ب"],
        out_dir=str(tmp_path),
        base_name="clip03",
        max_variants=1,
    )
    assert result["ok"] is True
    assert len(result["variants"]) == 1


def test_video_source_adds_second_frame(tmp_path, monkeypatch):
    _no_face(monkeypatch)
    frames = []

    def fake_extract(video_path, at_seconds=0.0):
        frames.append(float(at_seconds))
        return Image.new("RGB", (1920, 1080), (10, 90, 30))

    monkeypatch.setattr(tg, "extract_frame", fake_extract)
    result = tg.generate_variants(
        _source(tmp_path, "vid.mp4"),
        titles=["عنوان أول", "عنوان ثانٍ"],
        out_dir=str(tmp_path),
        base_name="vid",
    )
    assert result["ok"] is True
    assert len(result["variants"]) == 3
    assert len(frames) == 3
    assert sorted(set(frames)) == [0.0, 2.0]


def test_variants_empty_dir_error(tmp_path, monkeypatch):
    monkeypatch.setattr(tg, "_face_bbox", lambda image: None)
    result = tg.generate_variants(
        str(tmp_path / "missing.png"),
        titles=["x"],
        out_dir=str(tmp_path),
        base_name="clip04",
    )
    assert result["ok"] is False
    assert result["error"]
