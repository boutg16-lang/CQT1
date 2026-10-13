"""Deterministic media-quality benchmark used locally and in CI."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional

from scripts import audio_qc, media_validation

BENCHMARK_VERSION = "1"
EXPECTED_DURATION = 6.0
EXPECTED_WIDTH = 640
EXPECTED_HEIGHT = 360
EXPECTED_FPS = 30.0


def _run(command: List[str]) -> None:
    subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _fps(stream: Dict[str, Any]) -> Optional[float]:
    value = str(stream.get("r_frame_rate") or "")
    try:
        numerator, denominator = value.split("/", 1)
        return float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return None


def _check_gates(validation: Dict[str, Any], audio: Dict[str, Any]) -> List[str]:
    failures: List[str] = []
    if not validation.get("ok"):
        failures.extend(validation.get("errors", ["media validation failed"]))
    video = validation.get("video") or {}
    if video.get("width") != EXPECTED_WIDTH or video.get("height") != EXPECTED_HEIGHT:
        failures.append("fixture dimensions changed")
    frame_rate = _fps(video)
    if frame_rate is None or abs(frame_rate - EXPECTED_FPS) > 0.01:
        failures.append("fixture frame rate changed")
    if abs(float(validation.get("duration") or 0.0) - EXPECTED_DURATION) > 0.15:
        failures.append("fixture duration changed")
    if audio.get("status") != "pass":
        failures.append("audio QC status is not pass")
    return failures


def _stable_report_paths(report: Dict[str, Any]) -> None:
    validation = report.get("media_validation") or {}
    if validation.get("path"):
        validation["path"] = "fixture.mp4"
    audio = report.get("audio_qc") or {}
    if audio.get("path"):
        audio["path"] = "fixture.mp4"


def run_benchmark(output_path: str, *, keep_media: bool = False) -> Dict[str, Any]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        raise RuntimeError("quality benchmark requires ffmpeg and ffprobe")
    started = time.perf_counter()
    temp_dir = tempfile.mkdtemp(prefix="viralcutter-quality-")
    media_path = os.path.join(temp_dir, "fixture.mp4")
    try:
        _run([
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=6",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-shortest", media_path,
        ])
        validation = media_validation.validate_media_file(media_path, require_audio=True, expected_aspect="16:9")
        audio = audio_qc.analyze_file(media_path)
        failures = _check_gates(validation, audio)
        report = {
            "schema_version": 1,
            "benchmark_version": BENCHMARK_VERSION,
            "fixture": "testsrc + 440Hz sine, 6s, 640x360, 30fps",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "media_validation": validation,
            "audio_qc": audio,
            "gate_failures": failures,
            "passed": not failures,
        }
        _stable_report_paths(report)
        if keep_media:
            report["media_path"] = media_path
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        return report
    finally:
        if not keep_media:
            shutil.rmtree(temp_dir, ignore_errors=True)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic video/audio quality benchmark")
    parser.add_argument("--output", default="quality_benchmark.json")
    parser.add_argument("--keep-media", action="store_true")
    args = parser.parse_args(argv)
    report = run_benchmark(args.output, keep_media=args.keep_media)
    print(json.dumps({"output": os.path.abspath(args.output), "passed": report["passed"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
