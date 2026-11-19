# -*- coding: utf-8 -*-
"""Direct tests for scripts/export_xml.py and its export_xml_lib.

Covers the CLI wrapper (``--help``, argument passing to ``export_pack``),
``export_pack`` (format validation, missing video, happy-path pack with a
mocked ``get_video_dims``), the Premiere XML generator (escaping, single vs
dual track, gap filling, overlays) and the SRT/dimension utils. All media
is fake bytes in tmp dirs; ffprobe/cv2 are never invoked for real.
"""

import json
import os
import runpy
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import export_xml as ex
from scripts.export_xml_lib import exporter
from scripts.export_xml_lib.utils import (
    get_video_dims,
    json_to_srt,
    timestamp_to_srt,
)
from scripts.export_xml_lib.xml_generator import create_premiere_xml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# scripts/export_xml.py appends the scripts dir to sys.path and imports
# export_pack from the TOP-LEVEL export_xml_lib package — a different module
# object from scripts.export_xml_lib.exporter. The CLI test patches that
# top-level module, which is what the wrapper actually calls.
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
import export_xml_lib.exporter as top_level_exporter  # noqa: E402

FACE_SINGLE = [{"frame": 0, "faces": [[100, 100, 300, 400]]}]
FACE_DUAL = [
    {"frame": f, "faces": [[100, 100, 300, 400], [600, 100, 800, 400]]}
    for f in range(0, 30)
]


def _make_project(tmp_path, name="MyProj", with_coords=True):
    proj = tmp_path / name
    (proj / "cuts").mkdir(parents=True)
    (proj / "cuts" / "000_clip_original_scale.mp4").write_bytes(b"VID")
    if with_coords:
        (proj / "final").mkdir(exist_ok=True)
        (proj / "final" / "000_coords.json").write_text(
            json.dumps(FACE_SINGLE), encoding="utf-8"
        )
    return proj


# ---------------------------------------------------------------------------
# scripts/export_xml.py wrapper
# ---------------------------------------------------------------------------


def test_export_xml_module_exposes_export_pack(tmp_path):
    # The wrapper re-exports export_pack; behaviour (not module identity)
    # is what matters — see the comment above about the dual import.
    assert ex.export_pack.__name__ == "export_pack"
    assert ex.export_pack.__module__.endswith("export_xml_lib.exporter")
    with pytest.raises(ValueError, match="Unsupported export format"):
        ex.export_pack(str(tmp_path), 0, "nope")
    assert exporter.SUPPORTED_EXPORT_FORMATS == ("premiere",)


def test_export_xml_cli_help_exits_zero():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "scripts", "export_xml.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()


def test_export_xml_cli_passes_args_to_export_pack(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        top_level_exporter,
        "export_pack",
        lambda project, segment, fmt="premiere": calls.append((project, segment, fmt)),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "export_xml.py",
            "--project",
            str(tmp_path),
            "--segment",
            "2",
            "--format",
            "premiere",
        ],
    )
    runpy.run_path(
        os.path.join(REPO_ROOT, "scripts", "export_xml.py"), run_name="__main__"
    )
    assert calls == [(str(tmp_path), 2, "premiere")]


def test_export_xml_cli_missing_required_arg_exits(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO_ROOT, "scripts", "export_xml.py"),
            "--project",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert "segment" in result.stderr


# ---------------------------------------------------------------------------
# export_pack
# ---------------------------------------------------------------------------


def test_export_pack_rejects_unsupported_format(tmp_path):
    proj = _make_project(tmp_path)
    with pytest.raises(ValueError, match="Unsupported export format"):
        exporter.export_pack(str(proj), 0, "final_cut")


def test_export_pack_format_is_case_insensitive(tmp_path, monkeypatch):
    proj = _make_project(tmp_path)
    monkeypatch.setattr(exporter, "get_video_dims", lambda p: (1920, 1080, 300, 30.0))
    assert exporter.export_pack(str(proj), 0, "PREMIERE") is not None


def test_export_pack_missing_video_returns_none(tmp_path):
    assert exporter.export_pack(str(tmp_path / "Nope"), 3) is None


def test_export_pack_happy_path_1080p(tmp_path, monkeypatch):
    proj = _make_project(tmp_path)
    monkeypatch.setattr(exporter, "get_video_dims", lambda p: (1920, 1080, 300, 30.0))

    zip_path = exporter.export_pack(str(proj), 0)

    assert zip_path is not None
    assert os.path.exists(zip_path)
    stage = proj / "export_MyProj_seg0"
    assert (stage / "timeline.xml").exists()
    assert (stage / "video_source.mp4").read_bytes() == b"VID"
    with zipfile.ZipFile(zip_path) as zf:
        assert set(zf.namelist()) == {"timeline.xml", "video_source.mp4"}
    root = ET.fromstring((stage / "timeline.xml").read_text(encoding="utf-8"))
    assert root.tag == "xmeml"
    assert root.find(".//sequence/name").text == "MyProj_CutRef"
    assert (
        root.find(".//sequence/media/video/format/samplecharacteristics/width").text
        == "1080"
    )
    assert (
        root.find(".//sequence/media/video/format/samplecharacteristics/height").text
        == "1920"
    )


def test_export_pack_4k_source_upgrades_sequence(tmp_path, monkeypatch):
    proj = _make_project(tmp_path)
    monkeypatch.setattr(exporter, "get_video_dims", lambda p: (3840, 2160, 600, 30.0))

    zip_path = exporter.export_pack(str(proj), 0)
    assert zip_path is not None
    xml = (proj / "export_MyProj_seg0" / "timeline.xml").read_text(encoding="utf-8")
    root = ET.fromstring(xml)
    assert (
        root.find(".//sequence/media/video/format/samplecharacteristics/width").text
        == "2160"
    )
    assert (
        root.find(".//sequence/media/video/format/samplecharacteristics/height").text
        == "3840"
    )


def test_export_pack_with_subtitles_writes_srt_and_overlays(tmp_path, monkeypatch):
    proj = _make_project(tmp_path)
    (proj / "subs_ass").mkdir()
    (proj / "subs_ass" / "000_test_processed.ass").write_text(
        "[Script Info]", encoding="utf-8"
    )
    (proj / "subs").mkdir()
    (proj / "subs" / "000_test_processed.json").write_text(
        json.dumps({"segments": [{"start": 0.0, "end": 1.5, "text": "hello"}]}),
        encoding="utf-8",
    )
    overlay_file = tmp_path / "ov0.mp4"
    overlay_file.write_bytes(b"OV")
    monkeypatch.setattr(exporter, "get_video_dims", lambda p: (1920, 1080, 300, 30.0))
    monkeypatch.setattr(
        exporter,
        "render_segmented_overlays",
        lambda *args: [
            {"start": 0.0, "end": 1.0, "path": str(overlay_file), "index": 0}
        ],
    )

    zip_path = exporter.export_pack(str(proj), 0)

    assert zip_path is not None
    stage = proj / "export_MyProj_seg0"
    srt = (stage / "MyProj_Seg0.srt").read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:01,500" in srt
    assert "hello" in srt
    xml = (stage / "timeline.xml").read_text(encoding="utf-8")
    assert "ov0.mp4" in xml  # overlay track present


def test_export_pack_without_face_coords_calls_jit_detection(tmp_path, monkeypatch):
    proj = _make_project(tmp_path, with_coords=False)
    monkeypatch.setattr(exporter, "get_video_dims", lambda p: (1920, 1080, 300, 30.0))
    monkeypatch.setattr(exporter, "detect_faces_jit", lambda video: FACE_SINGLE)

    zip_path = exporter.export_pack(str(proj), 0)
    assert zip_path is not None
    assert (proj / "export_MyProj_seg0" / "timeline.xml").exists()


# ---------------------------------------------------------------------------
# create_premiere_xml
# ---------------------------------------------------------------------------


def test_xml_escapes_user_strings_and_parses():
    xml = create_premiere_xml(
        project_name="A & B <Test>",
        video_path="/v/clip & one.mp4",
        overlay_segments=[],
        duration_frames=300,
    )
    assert "A &amp; B" in xml
    assert "& B" not in xml.replace("&amp;", "")
    root = ET.fromstring(xml)
    assert root.find(".//sequence/name").text == "A & B <Test>_CutRef"


def test_xml_single_track_default_center():
    xml = create_premiere_xml(
        project_name="P",
        video_path="/v/clip.mp4",
        overlay_segments=[],
        duration_frames=300,
    )
    assert xml.count("clipitem-video-") == 1
    assert "Crop" not in xml


def test_xml_dual_track_when_two_faces_dominate():
    xml = create_premiere_xml(
        project_name="P",
        video_path="/v/clip.mp4",
        overlay_segments=[{"start": 0.0, "end": 1.0, "path": "/v/ov0.mp4", "index": 0}],
        duration_frames=60,
        face_data=FACE_DUAL,
        source_width=1920,
        source_height=1080,
    )
    # V1 + V2 + overlay + audio tracks, and split-screen cropping.
    assert xml.count("<track>") == 4
    assert "Crop" in xml
    ET.fromstring(xml)  # still well-formed


def test_xml_gap_filling_and_zero_duration_segments_skipped():
    xml = create_premiere_xml(
        project_name="P",
        video_path="/v/clip.mp4",
        overlay_segments=[
            {"start": 1.0, "end": 2.0, "path": "/v/ov1.mp4", "index": 1},
            {"start": 2.0, "end": 2.0, "path": "/v/ov2.mp4", "index": 2},  # zero length
        ],
        duration_frames=90,
    )
    # Gap [0,30] + segment [30,60] + final gap [60,90] = 3 V1 cuts.
    assert xml.count("clipitem-video-") == 3
    assert "ov2.mp4" not in xml


def test_xml_face_src_size_metadata_and_malformed_fallback():
    good = [{"frame": 0, "src_size": [3840, 2160], "faces": [[100, 100, 300, 400]]}]
    xml = create_premiere_xml(
        project_name="P",
        video_path="/v/clip.mp4",
        overlay_segments=[],
        duration_frames=300,
        face_data=good,
        source_width=1920,
        source_height=1080,
    )
    ET.fromstring(xml)

    bad = [{"frame": 0, "src_size": "not-a-pair", "faces": [[100, 100, 300, 400]]}]
    xml = create_premiere_xml(
        project_name="P",
        video_path="/v/clip.mp4",
        overlay_segments=[],
        duration_frames=300,
        face_data=bad,
        source_width=1920,
        source_height=1080,
    )
    ET.fromstring(xml)  # malformed metadata falls back to source dims


def test_xml_explicit_ids_are_used():
    xml = create_premiere_xml(
        project_name="P",
        video_path="/v/clip.mp4",
        overlay_segments=[],
        duration_frames=30,
        video_file_id="file-video-abc",
        audio_file_id="file-audio-xyz",
    )
    assert 'id="file-video-abc"' in xml
    assert 'id="file-audio-xyz"' in xml


# ---------------------------------------------------------------------------
# utils: SRT + ffprobe dims
# ---------------------------------------------------------------------------


def test_timestamp_to_srt():
    assert timestamp_to_srt(0) == "00:00:00,000"
    assert timestamp_to_srt(3661.234) == "01:01:01,234"
    assert timestamp_to_srt(59.999) == "00:00:59,999"


def test_json_to_srt_segment_and_word_level():
    srt = json_to_srt([{"start": 0, "end": 1.5, "text": "hello"}])
    assert srt == "1\n00:00:00,000 --> 00:00:01,500\nhello\n\n"
    srt = json_to_srt(
        [{"start": 2, "end": 3, "words": [{"start": 2, "end": 2.5, "word": "hi"}]}]
    )
    assert "1\n00:00:02,000 --> 00:00:02,500\nhi\n\n" in srt


def test_json_to_srt_tuple_rows_and_empty():
    assert json_to_srt([(0, 1.0, "tuple text")]) == (
        "1\n00:00:00,000 --> 00:00:01,000\ntuple text\n\n"
    )
    assert json_to_srt([]) == ""


def test_get_video_dims_success(monkeypatch):
    calls = iter(["1920", "1080", "10.0", "30/1"])

    def fake_check_output(cmd, **kwargs):
        return next(calls).encode()

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    assert get_video_dims("/v/clip.mp4") == (1920, 1080, 300, 30.0)


def test_get_video_dims_failure_falls_back(monkeypatch):
    def boom(cmd, **kwargs):
        raise OSError("ffprobe not found")

    monkeypatch.setattr(subprocess, "check_output", boom)
    assert get_video_dims("/v/clip.mp4") == (1920, 1080, 300, 30.0)
