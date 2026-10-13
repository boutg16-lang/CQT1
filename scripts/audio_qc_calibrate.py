"""Calibrate OUSSAMA Cutter audio-QC thresholds from a reference corpus.

The defaults in :mod:`scripts.audio_qc` (target -16 LUFS / -1.5 dBTP,
silence -50 dB) are generic broadcast values. The product gap analysis
(``docs/PRODUCT_GAP_ANALYSIS_AR.md`` — «جودة الصوت») calls for thresholds
*calibrated on a licensed Arabic corpus* instead of generic constants.

This module closes the measurement half of that gap: point it at a folder
of approved reference clips (e.g. shorts that already passed manual review)
and it measures every clip with :func:`scripts.audio_qc.analyze_file`,
aggregates the loudness / true-peak / silence distributions, and writes
``audio_qc_thresholds.json`` with percentile-based recommendations. Feed the
result back to the QC run with ``python -m scripts.audio_qc --project ...
--thresholds-file audio_qc_thresholds.json``.

The tool is read-only on the corpus, needs nothing beyond FFmpeg/ffprobe
(like ``audio_qc`` itself), makes no network calls, and is deterministic:
the same corpus always yields the same recommendations.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

from scripts import audio_qc

SCHEMA_VERSION = 1
DEFAULT_OUTPUT_NAME = "audio_qc_thresholds.json"
MIN_FILES_DEFAULT = 5

# Sanity clamps: even a skewed corpus must not produce dangerous thresholds
# (a whisper-quiet corpus must not drag the loudness target to -40 LUFS).
TARGET_I_RANGE = (-24.0, -12.0)
REVIEW_I_RANGE = (-40.0, 0.0)
TARGET_TP_RANGE = (-6.0, -1.0)
MAX_SILENCE_RATIO_RANGE = (0.05, 0.80)

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm")


def _now() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()


def _percentile(values: Sequence[float], percent: float) -> Optional[float]:
    """Linear-interpolation percentile. Deterministic and numpy-free."""
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (float(percent) / 100.0)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    fraction = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * fraction


def _clamp(value: float, bounds: Tuple[float, float]) -> float:
    low, high = bounds
    return max(low, min(high, float(value)))


def _round_half(value: float) -> float:
    """Round to the nearest 0.5 — thresholds stay human-readable."""
    return round(float(value) * 2.0) / 2.0


def _video_files(folder: str, *, recursive: bool = False) -> List[str]:
    if not os.path.isdir(folder):
        return []
    found: List[str] = []
    if recursive:
        for root, _dirs, files in os.walk(folder):
            for name in files:
                if name.lower().endswith(VIDEO_EXTENSIONS):
                    found.append(os.path.join(root, name))
    else:
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if name.lower().endswith(VIDEO_EXTENSIONS) and os.path.isfile(path):
                found.append(path)
    return sorted(found)


def measure_corpus(
    corpus_dir: str,
    *,
    recursive: bool = False,
    limit: Optional[int] = None,
    silence_db: float = audio_qc.DEFAULT_SILENCE_DB,
    silence_duration: float = audio_qc.DEFAULT_SILENCE_DURATION,
) -> Dict[str, Any]:
    """Measure every clip in *corpus_dir*; a bad clip is skipped, never fatal."""
    folder = os.path.abspath(os.path.expanduser(os.fspath(corpus_dir)))
    paths = _video_files(folder, recursive=recursive)
    if limit is not None:
        paths = paths[: max(0, int(limit))]
    measured: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    for path in paths:
        result = audio_qc.analyze_file(
            path, silence_db=silence_db, silence_duration=silence_duration)
        metrics = result.get("metrics") or {}
        input_i = metrics.get("input_i")
        input_tp = metrics.get("input_tp")
        silence = metrics.get("silence")
        silence_ratio = silence.get("ratio") if isinstance(silence, dict) else None
        if input_i is None or input_tp is None:
            reason = "unmeasurable"
            issues = result.get("issues") or []
            if issues and isinstance(issues[0], dict):
                reason = str(issues[0].get("code", reason))
            skipped.append({"file": os.path.basename(path), "reason": reason})
            continue
        measured.append({
            "file": os.path.basename(path),
            "path": path,
            "duration": float(result.get("duration") or 0.0),
            "input_i": float(input_i),
            "input_tp": float(input_tp),
            "silence_ratio": float(silence_ratio) if silence_ratio is not None else None,
        })
    return {
        "corpus_dir": folder,
        "total_candidates": len(paths),
        "measured": measured,
        "skipped": skipped,
    }


def _distribution(values: Sequence[float]) -> Dict[str, Optional[float]]:
    ordered = [float(value) for value in values]
    if not ordered:
        return {"min": None, "p05": None, "p50": None, "p95": None, "max": None}
    return {
        "min": min(ordered),
        "p05": _percentile(ordered, 5),
        "p50": _percentile(ordered, 50),
        "p95": _percentile(ordered, 95),
        "max": max(ordered),
    }


def recommend_thresholds(
    measured: Sequence[Dict[str, Any]],
    *,
    loudness_margin: float = 2.0,
    true_peak_margin: float = 0.5,
    silence_margin: float = 0.10,
) -> Dict[str, Any]:
    """Derive recommended thresholds from measured clips (percentile-based).

    - ``target_i`` — corpus median loudness (the loudness your approved
      content actually ships at), clamped to a sane broadcast band.
    - ``target_tp`` — at least ``true_peak_margin`` dB under the corpus p95
      true peak and never above -1.0 dBTP.
    - ``loudness_review_range`` — corpus p05..p95 loudness widened by
      ``loudness_margin`` on both sides (replaces the hardcoded -28..-8
      review window in ``audio_qc`` when the operator chooses to apply it).
    - ``max_silence_ratio`` — corpus p95 silence ratio plus
      ``silence_margin`` (None when silence could not be measured).
    """
    loudness = [item["input_i"] for item in measured]
    peaks = [item["input_tp"] for item in measured]
    silence_ratios = [
        item["silence_ratio"] for item in measured if item.get("silence_ratio") is not None]

    statistics = {
        "input_i": _distribution(loudness),
        "input_tp": _distribution(peaks),
        "silence_ratio": _distribution(silence_ratios),
    }
    notes: List[str] = []

    median_i = statistics["input_i"]["p50"] or audio_qc.DEFAULT_TARGET_I
    p05_i = statistics["input_i"]["p05"] or median_i
    p95_i = statistics["input_i"]["p95"] or median_i
    p95_tp = statistics["input_tp"]["p95"]
    p95_silence = statistics["silence_ratio"]["p95"]

    target_i = _round_half(_clamp(median_i, TARGET_I_RANGE))
    notes.append(
        "target_i {} LUFS = corpus median loudness clamped to {}..{}".format(
            target_i, TARGET_I_RANGE[0], TARGET_I_RANGE[1]))

    if p95_tp is None:
        target_tp = audio_qc.DEFAULT_TARGET_TP
        notes.append("target_tp kept at default: corpus true-peak p95 unavailable")
    else:
        target_tp = _round_half(_clamp(min(-1.0, p95_tp - true_peak_margin), TARGET_TP_RANGE))
        notes.append(
            "target_tp {} dBTP = corpus p95 ({:.2f}) minus {} dB, never above -1.0".format(
                target_tp, p95_tp, true_peak_margin))

    review_low = _round_half(_clamp(p05_i - loudness_margin, REVIEW_I_RANGE))
    review_high = _round_half(_clamp(p95_i + loudness_margin, REVIEW_I_RANGE))
    notes.append(
        "loudness_review_range {}..{} = corpus p05/p95 widened by {} LU".format(
            review_low, review_high, loudness_margin))

    if p95_silence is None:
        max_silence_ratio = None
        notes.append("max_silence_ratio unavailable: silence could not be measured in the corpus")
    else:
        max_silence_ratio = round(
            _clamp(p95_silence + silence_margin, MAX_SILENCE_RATIO_RANGE), 3)
        notes.append(
            "max_silence_ratio {} = corpus p95 ({:.3f}) plus {} margin".format(
                max_silence_ratio, p95_silence, silence_margin))

    return {
        "statistics": statistics,
        "recommended": {
            "target_i": target_i,
            "target_tp": target_tp,
            "loudness_review_range": [review_low, review_high],
            "max_silence_ratio": max_silence_ratio,
        },
        "notes": notes,
    }


def calibrate(
    corpus_dir: str,
    *,
    recursive: bool = False,
    limit: Optional[int] = None,
    min_files: int = MIN_FILES_DEFAULT,
    silence_db: float = audio_qc.DEFAULT_SILENCE_DB,
    silence_duration: float = audio_qc.DEFAULT_SILENCE_DURATION,
) -> Dict[str, Any]:
    """Measure the corpus and build the full calibration report."""
    measurement = measure_corpus(
        corpus_dir, recursive=recursive, limit=limit,
        silence_db=silence_db, silence_duration=silence_duration)
    measured = measurement["measured"]
    report: Dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "generated_at": _now(),
        "corpus": {
            "dir": measurement["corpus_dir"],
            "candidates": measurement["total_candidates"],
            "files_measured": len(measured),
            "files_skipped": len(measurement["skipped"]),
            "min_files_required": int(min_files),
        },
        "skipped": measurement["skipped"],
        "clips": measured,
    }
    if len(measured) < int(min_files):
        report["ok"] = False
        report["error"] = (
            "corpus too small: {} measurable clips found, need at least {} — "
            "add more approved reference clips or lower --min-files".format(
                len(measured), int(min_files)))
        report["statistics"] = None
        report["recommended"] = None
        return report
    report["ok"] = True
    report.update(recommend_thresholds(measured))
    return report


def write_report(path: str, report: Dict[str, Any]) -> str:
    """Atomically write the calibration report; returns the final path."""
    target = os.path.abspath(os.path.expanduser(os.fspath(path)))
    folder = os.path.dirname(target) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".qc_thresholds_", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return target


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Calibrate audio-QC thresholds from a reference clip corpus.")
    parser.add_argument("--corpus", required=True,
                        help="Folder with approved reference clips (.mp4/.mov/.mkv/.webm)")
    parser.add_argument("--output", default=None,
                        help="Report path (default: <corpus>/{})".format(DEFAULT_OUTPUT_NAME))
    parser.add_argument("--min-files", type=int, default=MIN_FILES_DEFAULT,
                        help="Minimum measurable clips required (default: %(default)s)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Measure at most N clips (alphabetical order)")
    parser.add_argument("--recursive", action="store_true",
                        help="Recurse into sub-folders of the corpus")
    args = parser.parse_args(argv)

    report = calibrate(
        args.corpus, recursive=args.recursive, limit=args.limit,
        min_files=args.min_files)
    output = args.output or os.path.join(report["corpus"]["dir"], DEFAULT_OUTPUT_NAME)
    target = write_report(output, report)

    corpus = report["corpus"]
    print("[audio-qc-calibrate] measured={} skipped={} candidates={}".format(
        corpus["files_measured"], corpus["files_skipped"], corpus["candidates"]))
    if not report.get("ok"):
        print("[audio-qc-calibrate] FAIL: {}".format(report.get("error", "calibration failed")))
        print("[audio-qc-calibrate] report: {}".format(target))
        return 2
    recommended = report["recommended"]
    print("[audio-qc-calibrate] target_i={} target_tp={} review={}..{} max_silence={}".format(
        recommended["target_i"], recommended["target_tp"],
        recommended["loudness_review_range"][0], recommended["loudness_review_range"][1],
        recommended["max_silence_ratio"]))
    print("[audio-qc-calibrate] report: {}".format(target))
    return 0


__all__ = [
    "DEFAULT_OUTPUT_NAME", "MIN_FILES_DEFAULT", "SCHEMA_VERSION", "calibrate",
    "main", "measure_corpus", "recommend_thresholds", "write_report",
]


if __name__ == "__main__":
    raise SystemExit(main())
