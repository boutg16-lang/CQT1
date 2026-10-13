import json

import pytest

from scripts import otio_export


def test_load_segments_normalizes_manifest(tmp_path):
    manifest = tmp_path / "segments.json"
    manifest.write_text(json.dumps({"segments": [{"title": "A", "start": 2, "end": 5}, {"start": 8, "end": 8}]}), encoding="utf-8")
    assert otio_export.load_segments(str(manifest)) == [{"title": "A", "start": 2, "end": 5, "start_time": 2.0, "end_time": 5.0}]


def test_otio_export_requires_optional_dependency(tmp_path):
    try:
        import opentimelineio  # noqa: F401
    except ImportError:
        with pytest.raises(RuntimeError, match="OpenTimelineIO is not installed"):
            otio_export.build_timeline([], str(tmp_path / "video.mp4"))
    else:
        timeline = otio_export.build_timeline([{"start_time": 1, "end_time": 3}], str(tmp_path / "video.mp4"))
        assert len(timeline.tracks[0]) == 1


def test_otio_export_round_trips(tmp_path):
    otio = pytest.importorskip("opentimelineio")
    manifest = tmp_path / "segments.json"
    output = tmp_path / "timeline.otio"
    media = tmp_path / "source.mp4"
    manifest.write_text(json.dumps({"segments": [{"title": "Hook", "start_time": 1.5, "end_time": 4.0}]}), encoding="utf-8")

    result = otio_export.export_timeline(str(manifest), str(output), str(media), rate=30)

    assert result == str(output.resolve())
    timeline = otio.adapters.read_from_file(str(output))
    clip = timeline.tracks[0][0]
    assert clip.name == "Hook"
    assert clip.source_range.start_time.to_seconds() == pytest.approx(1.5)
    assert clip.source_range.duration.to_seconds() == pytest.approx(2.5)
    assert clip.metadata["viralcutter"]["segment_index"] == 1
