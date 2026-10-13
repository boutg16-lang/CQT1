"""Tests for the scripts/export_xml_lib package.

Covers the current, real behaviour of the five modules:
  - utils.py          timestamp_to_srt / json_to_srt / get_video_dims
  - xml_generator.py  create_premiere_xml (XMEML output, cuts, dual-track)
  - face_detection.py detect_faces_jit (InsightFace optional, stubbed loop)
  - rendering.py      render_segmented_overlays (ffmpeg qtrle captions)
  - exporter.py       export_pack end-to-end on fake VIRALS project dirs

A "project folder" mirrors what webui/app.py and webui/subtitle_editor.py
hand to export_pack:
  <project>/cuts/{seg:03d}_*.mp4            segment video (optional *_original_scale.mp4)
  <project>/subs_ass/{seg:03d}_*.ass        rendered ASS subtitles
  <project>/subs/{seg:03d}_*.json           subtitle JSON (dict with "segments" or list)
  <project>/final/{seg:03d}_*_coords.json   pre-computed face coordinates
Output: <project>/export_<name>_seg<n>/ staging dir + export_<name>_seg<n>.zip
"""

import json
import re
import shutil
import subprocess
import uuid
import xml.etree.ElementTree as ET
import zipfile
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.export_xml_lib import (
    exporter,
    face_detection,
    rendering,
    utils,
    xml_generator,
)

# ---------------------------------------------------------------------------
# Helpers: build fake project dirs on disk (tmp_path)
# ---------------------------------------------------------------------------

ASS_TEMPLATE = """[Script Info]
ScriptType: v4.00+
PlayResX: 160
PlayResY: 120

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,Hello
Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,World
"""


def _make_video(path, duration=2.0, fps=10, size="160x120"):
    """Create a tiny deterministic test video with ffmpeg."""
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"testsrc=size={size}:duration={duration}:rate={fps}",
            "-pix_fmt", "yuv420p",
            "-c:v", "libx264",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _make_ass(path):
    path.write_text(ASS_TEMPLATE, encoding="utf-8")
    return path


def _build_project(
    tmp_path,
    name="TestProj",
    segment=1,
    with_video=True,
    with_ass=True,
    with_subs_json=True,
    coords="dual",
    original_scale=False,
):
    """Create a minimal fake VIRALS project folder. Returns the project path."""
    proj = tmp_path / name
    prefix = f"{segment:03d}_"
    (proj / "cuts").mkdir(parents=True)

    if with_video:
        _make_video(proj / "cuts" / f"{prefix}clip.mp4")
        if original_scale:
            _make_video(proj / "cuts" / f"{prefix}clip_original_scale.mp4", size="320x240")

    if with_ass:
        (proj / "subs_ass").mkdir(exist_ok=True)
        _make_ass(proj / "subs_ass" / f"{prefix}clip_processed.ass")

    if with_subs_json:
        (proj / "subs").mkdir(exist_ok=True)
        segments = [
            {"start": 0.0, "end": 1.0, "text": "Hello"},
            {"start": 1.0, "end": 2.0, "text": "World"},
        ]
        (proj / "subs" / f"{prefix}clip_processed.json").write_text(
            json.dumps({"segments": segments}), encoding="utf-8"
        )

    if coords is not None:
        (proj / "final").mkdir(exist_ok=True)
        coords_path = proj / "final" / f"{prefix}clip_coords.json"
        if coords == "corrupt":
            coords_path.write_text("{not valid json", encoding="utf-8")
        elif coords == "dual":
            data = [
                {"frame": i, "faces": [[20, 30, 60, 80], [110, 30, 150, 80]]}
                for i in range(20)
            ]
            coords_path.write_text(json.dumps(data), encoding="utf-8")
        elif coords == "4k":
            # x2 > probed video width (160) triggers the exporter's 4K correction
            data = [{"frame": i, "faces": [[2800, 100, 3100, 400]]} for i in range(20)]
            coords_path.write_text(json.dumps(data), encoding="utf-8")
        else:
            coords_path.write_text(json.dumps(coords), encoding="utf-8")

    return proj


def _patch_jit(monkeypatch):
    """Keep export_pack fast and deterministic: never run real face detection."""
    monkeypatch.setattr(exporter, "detect_faces_jit", lambda video_path: [])


def _parse_xml(xml_text):
    return ET.fromstring(xml_text)


# ---------------------------------------------------------------------------
# utils.py
# ---------------------------------------------------------------------------


class TestTimestampToSrt:
    def test_zero(self):
        assert utils.timestamp_to_srt(0) == "00:00:00,000"

    def test_hours_minutes_seconds_millis(self):
        assert utils.timestamp_to_srt(3661.5) == "01:01:01,500"

    def test_millisecond_precision(self):
        assert utils.timestamp_to_srt(0.001) == "00:00:00,001"

    def test_hours_exceed_24(self):
        # Current contract: no day rollover, hours keep growing.
        assert utils.timestamp_to_srt(90061) == "25:01:01,000"

    def test_negative_seconds_current_behaviour(self):
        # Current (quirky) contract: negative values are not normalized,
        # timedelta internals leak into the formatted string.
        assert utils.timestamp_to_srt(-1.5) == "-1:59:59,500"


class TestJsonToSrt:
    def test_segment_level_dicts(self):
        data = [
            {"start": 0.0, "end": 1.5, "text": "Hello"},
            {"start": 2.0, "end": 3.0, "text": "World"},
        ]
        assert utils.json_to_srt(data) == (
            "1\n00:00:00,000 --> 00:00:01,500\nHello\n\n"
            "2\n00:00:02,000 --> 00:00:03,000\nWorld\n\n"
        )

    def test_word_level_when_words_present(self):
        data = [
            {
                "start": 0.0,
                "end": 1.0,
                "text": "ignored",
                "words": [
                    {"start": 0.0, "end": 0.4, "word": "Hel"},
                    {"start": 0.4, "end": 1.0, "word": "lo"},
                ],
            }
        ]
        assert utils.json_to_srt(data) == (
            "1\n00:00:00,000 --> 00:00:00,400\nHel\n\n"
            "2\n00:00:00,400 --> 00:00:01,000\nlo\n\n"
        )

    def test_empty_words_list_falls_back_to_segment_level(self):
        data = [{"start": 1.0, "end": 2.0, "text": "Fallback", "words": []}]
        srt = utils.json_to_srt(data)
        assert "Fallback" in srt
        assert srt.startswith("1\n00:00:01,000 --> 00:00:02,000\n")

    def test_list_tuple_blocks(self):
        data = [[0.5, 1.0, "From list"], (2.0, 2.5, "From tuple")]
        srt = utils.json_to_srt(data)
        assert "00:00:00,500 --> 00:00:01,000\nFrom list" in srt
        assert "00:00:02,000 --> 00:00:02,500\nFrom tuple" in srt

    def test_missing_keys_default(self):
        srt = utils.json_to_srt([{}])
        assert srt == "1\n00:00:00,000 --> 00:00:00,000\n\n\n"

    def test_empty_input(self):
        assert utils.json_to_srt([]) == ""

    def test_counter_is_continuous_across_mixed_blocks(self):
        data = [
            {"words": [{"start": 0, "end": 1, "word": "a"}, {"start": 1, "end": 2, "word": "b"}]},
            {"start": 2, "end": 3, "text": "c"},
        ]
        srt = utils.json_to_srt(data)
        entries = [block.split("\n")[0] for block in srt.strip().split("\n\n")]
        assert entries == ["1", "2", "3"]


class TestGetVideoDims:
    def test_probes_real_video(self, tmp_path):
        vid = _make_video(tmp_path / "probe.mp4", duration=2.0, fps=10, size="160x120")
        width, height, frames, fps = utils.get_video_dims(str(vid))
        assert (width, height) == (160, 120)
        assert frames == 20  # int(duration * fps)
        assert fps == pytest.approx(10.0)

    def test_missing_file_returns_fallback(self, tmp_path, capsys):
        result = utils.get_video_dims(str(tmp_path / "does_not_exist.mp4"))
        assert result == (1920, 1080, 300, 30.0)
        assert "Error probing video" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# xml_generator.py
# ---------------------------------------------------------------------------


class TestCreatePremiereXmlStructure:
    def test_minimal_xml_is_well_formed_with_key_markers(self):
        xml_text = xml_generator.create_premiere_xml("MyProj", "/v/video_cut.mp4", [], 300)
        root = _parse_xml(xml_text)

        assert root.tag == "xmeml"
        assert root.attrib["version"] == "4"

        sequence = root.find("sequence")
        assert sequence.find("name").text == "MyProj_CutRef"
        assert sequence.find("duration").text == "300"
        assert sequence.find("rate/timebase").text == "30"
        assert sequence.find("rate/ntsc").text == "FALSE"
        assert sequence.find("timecode/string").text == "00:00:00:00"

        fmt = sequence.find("media/video/format/samplecharacteristics")
        assert fmt.find("width").text == "1080"  # default vertical sequence
        assert fmt.find("height").text == "1920"
        assert fmt.find("pixelaspectratio").text == "square"

        # Without overlay segments: one full-duration cut on V1, empty V2 + overlay tracks.
        tracks = sequence.find("media/video").findall("track")
        assert len(tracks) == 3
        v1_items = tracks[0].findall("clipitem")
        assert len(v1_items) == 1
        assert v1_items[0].find("start").text == "0"
        assert v1_items[0].find("end").text == "300"
        assert v1_items[0].find("name").text == "video_cut.mp4"
        assert tracks[1].findall("clipitem") == []
        assert tracks[2].findall("clipitem") == []

        audio_item = sequence.find("media/audio/track/clipitem")
        assert audio_item.find("start").text == "0"
        assert audio_item.find("end").text == "300"
        assert audio_item.find("sourcetrack/mediatype").text == "audio"

    def test_timebase_is_propagated_everywhere(self):
        xml_text = xml_generator.create_premiere_xml(
            "P", "/v/v.mp4", [], 240, timebase=24, width=720, height=1280
        )
        root = _parse_xml(xml_text)
        timebases = {el.text for el in root.iter("timebase")}
        assert timebases == {"24"}
        fmt = root.find("sequence/media/video/format/samplecharacteristics")
        assert fmt.find("width").text == "720"
        assert fmt.find("height").text == "1280"

    def test_overlay_segments_fill_gaps_on_main_track(self):
        overlays = [
            {"path": "captions/caption_0.mov", "start": 0.0, "end": 1.0, "index": 0},
            {"path": "captions/caption_1.mov", "start": 2.0, "end": 3.0, "index": 1},
        ]
        xml_text = xml_generator.create_premiere_xml("P", "/v/v.mp4", overlays, 120, timebase=30)
        root = _parse_xml(xml_text)
        tracks = root.find("sequence/media/video").findall("track")

        # Gap between segments and the tail gap become extra cuts on V1.
        v1_starts = [c.find("start").text for c in tracks[0].findall("clipitem")]
        v1_ends = [c.find("end").text for c in tracks[0].findall("clipitem")]
        assert v1_starts == ["0", "30", "60", "90"]
        assert v1_ends == ["30", "60", "90", "120"]

        overlay_items = tracks[2].findall("clipitem")
        assert len(overlay_items) == 2
        assert overlay_items[0].find("in").text == "0"
        assert overlay_items[0].find("out").text == "30"
        assert overlay_items[0].find("file/pathurl").text == "captions/caption_0.mov"
        assert overlay_items[0].find("compositemode").text == "normal"

    def test_zero_duration_overlay_segment_is_skipped(self):
        overlays = [
            {"path": "captions/caption_0.mov", "start": 1.0, "end": 1.0, "index": 0},
            {"path": "captions/caption_1.mov", "start": 1.0, "end": 2.0, "index": 1},
        ]
        xml_text = xml_generator.create_premiere_xml("P", "/v/v.mp4", overlays, 60, timebase=30)
        root = _parse_xml(xml_text)
        tracks = root.find("sequence/media/video").findall("track")
        # Only the valid segment reaches the overlay track.
        assert len(tracks[2].findall("clipitem")) == 1
        # The zero-length cut is dropped from V1 as well (gap + real segment only).
        assert len(tracks[0].findall("clipitem")) == 2


class TestCreatePremiereXmlDeterminism:
    def test_same_bytes_when_uuids_are_stable(self, monkeypatch):
        # The generator embeds uuid4 ids; with a stable uuid source the output
        # is fully deterministic (two runs -> same bytes).
        monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=0))
        overlays = [{"path": "captions/caption_0.mov", "start": 0.0, "end": 1.0, "index": 0}]
        first = xml_generator.create_premiere_xml("P", "/v/v.mp4", overlays, 60)
        second = xml_generator.create_premiere_xml("P", "/v/v.mp4", overlays, 60)
        assert first == second

    def test_default_runs_differ_only_in_embedded_ids(self):
        # Current contract: default output embeds random uuid4 identifiers, so
        # raw bytes differ; after normalizing id attributes the XML is identical.
        first = xml_generator.create_premiere_xml("P", "/v/v.mp4", [], 60)
        second = xml_generator.create_premiere_xml("P", "/v/v.mp4", [], 60)
        normalize = lambda text: re.sub(r'id="[^"]*"', 'id="ID"', text)  # noqa: E731
        assert normalize(first) == normalize(second)


class TestCreatePremiereXmlFaceData:
    def test_no_face_data_still_valid_and_centered(self, capsys):
        overlays = [{"path": "captions/caption_0.mov", "start": 0.0, "end": 1.0, "index": 0}]
        xml_text = xml_generator.create_premiere_xml(
            "P", "/v/v.mp4", overlays, 30, face_data=None
        )
        _parse_xml(xml_text)  # well-formed without face data
        assert "Basic Motion" in xml_text
        # Default center (0.5, 0.5) -> no shift from sequence center.
        assert "<horiz>0.00000</horiz>" in xml_text
        assert "<vert>0.00000</vert>" in xml_text
        assert "No pre-computed" not in capsys.readouterr().out  # generator stays silent

    def test_single_face_positions_clip(self):
        overlays = [{"path": "captions/caption_0.mov", "start": 0.0, "end": 1.0, "index": 0}]
        face_data = [{"frame": i, "faces": [[100, 100, 200, 300]]} for i in range(30)]
        xml_text = xml_generator.create_premiere_xml(
            "P", "/v/v.mp4", overlays, 30,
            face_data=face_data, source_width=1920, source_height=1080,
        )
        root = _parse_xml(xml_text)
        # Scale fills the 1920-high sequence from a 1080-high source: 177.78.
        scale = root.find(".//parameter[parameterid='scale']/value")
        assert scale.text == "177.78"
        center = root.find(".//parameter[parameterid='center']/value")
        assert float(center.find("horiz").text) > 0.0  # face left of center -> clip shifts right
        assert float(center.find("vert").text) > 0.0
        # Single face -> no dual-track crop.
        assert "<name>Crop</name>" not in xml_text

    def test_dual_faces_create_second_track_and_crops(self):
        overlays = [{"path": "captions/caption_0.mov", "start": 0.0, "end": 1.0, "index": 0}]
        face_data = [
            {"frame": i, "faces": [[100, 100, 200, 300], [1500, 100, 1700, 300]]}
            for i in range(30)
        ]
        xml_text = xml_generator.create_premiere_xml(
            "P", "/v/v.mp4", overlays, 30,
            face_data=face_data, source_width=1920, source_height=1080,
        )
        root = _parse_xml(xml_text)
        tracks = root.find("sequence/media/video").findall("track")
        assert len(tracks[0].findall("clipitem")) == 1
        assert len(tracks[1].findall("clipitem")) == 1  # secondary track populated
        # Split-screen mode: both panes get a Crop filter and the 1.2x zoom boost.
        assert xml_text.count("<name>Crop</name>") == 2
        assert "<value>213.33</value>" in xml_text  # 177.78 * 1.2

    def test_faces_with_src_size_metadata_set_coordinate_reference(self, capsys):
        overlays = [{"path": "captions/caption_0.mov", "start": 0.0, "end": 1.0, "index": 0}]
        face_data = [
            {"frame": i, "src_size": [3840, 2160], "faces": [[200, 200, 400, 600]]}
            for i in range(30)
        ]
        xml_text = xml_generator.create_premiere_xml(
            "P", "/v/v.mp4", overlays, 30,
            face_data=face_data, source_width=1920, source_height=1080,
        )
        _parse_xml(xml_text)
        assert "Coordinate System Reference: 3840x2160" in capsys.readouterr().out

    def test_empty_face_entries_are_skipped(self):
        overlays = [{"path": "captions/caption_0.mov", "start": 0.0, "end": 1.0, "index": 0}]
        face_data = [{"frame": i, "faces": []} for i in range(30)]
        xml_text = xml_generator.create_premiere_xml(
            "P", "/v/v.mp4", overlays, 30, face_data=face_data
        )
        _parse_xml(xml_text)
        assert "<horiz>0.00000</horiz>" in xml_text  # falls back to default center


# ---------------------------------------------------------------------------
# face_detection.py
# ---------------------------------------------------------------------------


class _FakeFaceApp:
    """InsightFace stand-in: deterministic 'detections' on every frame."""

    def __init__(self, *args, **kwargs):
        pass

    def prepare(self, *args, **kwargs):
        pass

    def get(self, frame):
        return [
            SimpleNamespace(bbox=np.array([10.7, 20.2, 30.9, 40.1])),
            SimpleNamespace(bbox=np.array([50.0, 60.0, 70.0, 80.0])),
        ]


class _EmptyFaceApp(_FakeFaceApp):
    def get(self, frame):
        return []


class TestFaceDetection:
    def test_availability_flag_is_bool(self):
        assert isinstance(face_detection.INSIGHTFACE_AVAILABLE, bool)

    def test_returns_empty_list_when_insightface_unavailable(self, monkeypatch):
        monkeypatch.setattr(face_detection, "INSIGHTFACE_AVAILABLE", False)
        assert face_detection.detect_faces_jit("whatever.mp4") == []

    def test_detection_loop_with_stubbed_backend(self, tmp_path, monkeypatch):
        monkeypatch.setattr(face_detection, "INSIGHTFACE_AVAILABLE", True)
        monkeypatch.setattr(face_detection, "FaceAnalysis", _FakeFaceApp, raising=False)
        vid = _make_video(tmp_path / "faces.mp4", duration=1.0, fps=10)

        data = face_detection.detect_faces_jit(str(vid))

        assert len(data) >= 1
        # One entry per scanned frame, in order, bboxes truncated to ints.
        assert [entry["frame"] for entry in data] == list(range(len(data)))
        assert data[0]["faces"] == [[10, 20, 30, 40], [50, 60, 70, 80]]

    def test_frames_without_detections_are_omitted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(face_detection, "INSIGHTFACE_AVAILABLE", True)
        monkeypatch.setattr(face_detection, "FaceAnalysis", _EmptyFaceApp, raising=False)
        vid = _make_video(tmp_path / "noface.mp4", duration=1.0, fps=10)
        assert face_detection.detect_faces_jit(str(vid)) == []

    def test_unopenable_video_returns_empty(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(face_detection, "INSIGHTFACE_AVAILABLE", True)
        monkeypatch.setattr(face_detection, "FaceAnalysis", _FakeFaceApp, raising=False)
        missing = tmp_path / "missing.mp4"
        assert face_detection.detect_faces_jit(str(missing)) == []
        assert "CRITICAL ERROR" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# rendering.py
# ---------------------------------------------------------------------------


class TestRenderSegmentedOverlays:
    def test_renders_segments_to_qtrle_movs(self, tmp_path):
        vid = _make_video(tmp_path / "vid.mp4")
        ass = _make_ass(tmp_path / "subs.ass")
        out_dir = tmp_path / "captions_out"
        out_dir.mkdir()
        segments = [
            {"start": 0.0, "end": 1.0, "text": "Hello"},
            {"start": 1.0, "end": 2.0, "text": "World"},
        ]

        result = rendering.render_segmented_overlays(str(ass), segments, str(vid), str(out_dir))

        assert result == [
            {"path": "captions/caption_0.mov", "start": 0.0, "end": 1.0, "index": 0},
            {"path": "captions/caption_1.mov", "start": 1.0, "end": 2.0, "index": 1},
        ]
        for entry in result:
            mov = out_dir / f"caption_{entry['index']}.mov"
            assert mov.exists() and mov.stat().st_size > 0
        # The temporary transparent canvas is cleaned up.
        assert not (out_dir / "base_canvas.png").exists()

    def test_zero_and_negative_duration_segments_are_skipped(self, tmp_path):
        vid = _make_video(tmp_path / "vid.mp4")
        ass = _make_ass(tmp_path / "subs.ass")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        segments = [{"start": 1.0, "end": 1.0}, {"start": 2.0, "end": 1.0}]

        result = rendering.render_segmented_overlays(str(ass), segments, str(vid), str(out_dir))

        assert result == []
        assert list(out_dir.iterdir()) == []  # canvas removed, nothing rendered

    def test_ffmpeg_failure_is_caught_and_returns_empty(self, tmp_path, capsys):
        vid = _make_video(tmp_path / "vid.mp4")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        bad_ass = tmp_path / "missing.ass"  # ass filter cannot open this

        result = rendering.render_segmented_overlays(
            str(bad_ass), [{"start": 0.0, "end": 1.0}], str(vid), str(out_dir)
        )

        assert result == []
        assert "Failed" in capsys.readouterr().out
        assert not (out_dir / "base_canvas.png").exists()


# ---------------------------------------------------------------------------
# exporter.py — end-to-end on fake project dirs
# ---------------------------------------------------------------------------


class TestExportPackGracefulFailures:
    def test_nonexistent_project_returns_none(self, tmp_path, capsys):
        result = exporter.export_pack(str(tmp_path / "nope"), 1, "premiere")
        assert result is None
        assert "Error: No video file found" in capsys.readouterr().out
        assert list(tmp_path.iterdir()) == []  # nothing created

    def test_project_without_segment_video_returns_none(self, tmp_path, capsys):
        proj = _build_project(tmp_path, with_video=False, with_ass=False,
                              with_subs_json=False, coords=None)
        result = exporter.export_pack(str(proj), 1, "premiere")
        assert result is None
        assert "Error: No video file found" in capsys.readouterr().out
        assert list(proj.iterdir()) == [proj / "cuts"]  # no staging dir left behind


class TestExportPackEndToEnd:
    def test_minimal_project_produces_zip(self, tmp_path, monkeypatch):
        _patch_jit(monkeypatch)
        proj = _build_project(tmp_path, with_ass=False, with_subs_json=False, coords=None)

        zip_path = exporter.export_pack(str(proj), 1, "premiere")

        expected = str(proj / "export_TestProj_seg1.zip")
        assert zip_path == expected
        assert (proj / "export_TestProj_seg1.zip").exists()

        stage = proj / "export_TestProj_seg1"
        assert stage.is_dir()  # current contract: staging dir is kept after export
        with zipfile.ZipFile(zip_path) as zf:
            members = zf.namelist()
        assert "timeline.xml" in members
        assert "video_cut.mp4" in members
        assert "TestProj_Seg1.srt" not in members  # no JSON subtitles provided

        root = ET.parse(str(stage / "timeline.xml")).getroot()
        assert root.tag == "xmeml"
        assert root.find("sequence/name").text == "TestProj_CutRef"
        assert root.find("sequence/rate/timebase").text == "10"  # probed fps

    def test_full_project_produces_complete_pack(self, tmp_path, monkeypatch):
        _patch_jit(monkeypatch)
        proj = _build_project(tmp_path)  # video + ass + json + dual-face coords

        zip_path = exporter.export_pack(str(proj), 1, "premiere")

        with zipfile.ZipFile(zip_path) as zf:
            members = zf.namelist()
            srt_bytes = zf.read("TestProj_Seg1.srt")
            xml_bytes = zf.read("timeline.xml")
            caption = zf.read("captions/caption_0.mov")
        assert "video_cut.mp4" in members
        assert "captions/caption_1.mov" in members
        assert len(caption) > 0

        srt = srt_bytes.decode("utf-8")
        assert srt.startswith("1\n00:00:00,000 --> 00:00:01,000\nHello\n\n")
        assert "2\n00:00:01,000 --> 00:00:02,000\nWorld\n\n" in srt

        xml_text = xml_bytes.decode("utf-8")
        root = _parse_xml(xml_text)
        assert root.find("sequence/name").text == "TestProj_CutRef"
        # Dual-face coords drive the split-screen path end to end.
        assert "<name>Crop</name>" in xml_text

    def test_original_scale_video_is_preferred(self, tmp_path, monkeypatch, capsys):
        _patch_jit(monkeypatch)
        proj = _build_project(tmp_path, with_ass=False, with_subs_json=False,
                              coords=None, original_scale=True)

        zip_path = exporter.export_pack(str(proj), 1, "premiere")

        assert "Using Original Scale Source" in capsys.readouterr().out
        with zipfile.ZipFile(zip_path) as zf:
            members = zf.namelist()
        assert "video_source.mp4" in members  # distinct name for original-scale source
        assert "video_cut.mp4" not in members

    def test_corrupt_coords_fall_back_to_jit(self, tmp_path, monkeypatch, capsys):
        _patch_jit(monkeypatch)
        proj = _build_project(tmp_path, with_ass=False, with_subs_json=False,
                              coords="corrupt")

        zip_path = exporter.export_pack(str(proj), 1, "premiere")

        out = capsys.readouterr().out
        assert "Face coords load error" in out
        assert "No pre-computed face data found" in out
        assert (proj / "export_TestProj_seg1.zip").exists()
        assert zip_path.endswith(".zip")

    def test_wide_face_coords_trigger_4k_sequence(self, tmp_path, monkeypatch, capsys):
        _patch_jit(monkeypatch)
        proj = _build_project(tmp_path, with_ass=False, with_subs_json=False, coords="4k")

        exporter.export_pack(str(proj), 1, "premiere")

        assert "Correction: Detecting 4K source" in capsys.readouterr().out
        xml_text = (proj / "export_TestProj_seg1" / "timeline.xml").read_text(encoding="utf-8")
        fmt = _parse_xml(xml_text).find("sequence/media/video/format/samplecharacteristics")
        assert fmt.find("width").text == "2160"   # upgraded to 4K vertical
        assert fmt.find("height").text == "3840"

    def test_rerun_over_existing_staging_dir(self, tmp_path, monkeypatch):
        _patch_jit(monkeypatch)
        proj = _build_project(tmp_path, with_ass=False, with_subs_json=False, coords=None)

        first = exporter.export_pack(str(proj), 1, "premiere")
        # Leave junk in the staging dir: the rerun must wipe and recreate it.
        junk = proj / "export_TestProj_seg1" / "stale.txt"
        junk.write_text("stale", encoding="utf-8")
        second = exporter.export_pack(str(proj), 1, "premiere")

        assert first == second
        assert not junk.exists()
        assert (proj / "export_TestProj_seg1" / "timeline.xml").exists()

    def test_unremovable_staging_dir_gets_random_suffix(self, tmp_path, monkeypatch):
        _patch_jit(monkeypatch)
        proj = _build_project(tmp_path, with_ass=False, with_subs_json=False, coords=None)
        stage = proj / "export_TestProj_seg1"
        stage.mkdir()
        sentinel = stage / "keepme.txt"
        sentinel.write_text("do not delete", encoding="utf-8")

        def _blocked_rmtree(path, *args, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(shutil, "rmtree", _blocked_rmtree)

        zip_path = exporter.export_pack(str(proj), 1, "premiere")

        # Original staging dir untouched; a suffixed sibling received the export.
        assert sentinel.exists()
        siblings = [p for p in proj.iterdir()
                    if p.is_dir() and p.name.startswith("export_TestProj_seg1_")]
        assert len(siblings) == 1
        assert (siblings[0] / "timeline.xml").exists()
        assert zip_path == f"{siblings[0]}.zip"


class TestXmlEscaping:
    """User-controlled strings must never break the XMEML (fixed: raw `&`/`<>`
    in project names or paths produced malformed XML Premiere can't import)."""

    def test_ampersand_project_name_and_path_stay_well_formed(self):
        import xml.etree.ElementTree as ET

        from scripts.export_xml_lib.xml_generator import create_premiere_xml

        xml = create_premiere_xml("A & B <Test>", "D:\\vids\\A & B.mp4",
                                  [], 300, timebase=30)
        root = ET.fromstring(xml)  # must not raise
        assert root.find(".//sequence/name").text == "A & B <Test>_CutRef"
        assert "A &amp; B.mp4" in xml

    def test_overlay_segment_path_is_escaped(self):
        import xml.etree.ElementTree as ET

        from scripts.export_xml_lib.xml_generator import create_premiere_xml

        xml = create_premiere_xml("p", "video.mp4",
                                  [{"start": 0, "end": 30, "index": 0,
                                    "path": "cap & tion.mov"}], 300)
        ET.fromstring(xml)  # must not raise
        assert "cap &amp; tion.mov" in xml
