"""OpenTimelineIO export for ViralCutter segment manifests."""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, Iterable, List, Optional


def _require_otio():
    try:
        import opentimelineio as otio
    except ImportError as exc:
        raise RuntimeError(
            "OpenTimelineIO is not installed. Install the optional extra with "
            "'pip install oussama-cutter[otio]'."
        ) from exc
    return otio


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_segments(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    segments = data.get("segments", []) if isinstance(data, dict) else data
    if not isinstance(segments, list):
        raise ValueError("segment manifest must contain a list of segments")
    result = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        start = _number(segment.get("start_time", segment.get("start")))
        end = _number(segment.get("end_time", segment.get("end")))
        if end <= start:
            continue
        result.append({**segment, "start_time": start, "end_time": end})
    return result


def build_timeline(segments: Iterable[Dict[str, Any]], media_path: str, *, name: str = "ViralCutter Timeline", rate: float = 30.0):
    otio = _require_otio()
    rate = float(rate) if float(rate) > 0 else 30.0
    timeline = otio.schema.Timeline(name=name)
    track = otio.schema.Track(name="Selected clips", kind="Video")
    for index, segment in enumerate(segments, 1):
        start = _number(segment["start_time"])
        duration = _number(segment["end_time"]) - start
        clip = otio.schema.Clip(
            name=str(segment.get("title") or "Clip {}".format(index)),
            media_reference=otio.schema.ExternalReference(target_url=os.path.abspath(media_path)),
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(start * rate, rate),
                duration=otio.opentime.RationalTime(duration * rate, rate),
            ),
        )
        clip.metadata["viralcutter"] = {"segment_index": index, "start_time": start, "end_time": start + duration}
        track.append(clip)
    timeline.tracks.append(track)
    return timeline


def export_timeline(segment_manifest: str, output_path: str, media_path: str, *, rate: float = 30.0) -> str:
    otio = _require_otio()
    timeline = build_timeline(load_segments(segment_manifest), media_path, rate=rate)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    otio.adapters.write_to_file(timeline, output_path)
    return os.path.abspath(output_path)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Export a ViralCutter manifest as OpenTimelineIO")
    parser.add_argument("--segments", required=True)
    parser.add_argument("--media", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--rate", type=float, default=30.0)
    args = parser.parse_args(argv)
    print(export_timeline(args.segments, args.output, args.media, rate=args.rate))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
