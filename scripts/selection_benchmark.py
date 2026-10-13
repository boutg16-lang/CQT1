# -*- coding: utf-8 -*-
"""Offline selection-quality benchmark for OUSSAMA Cutter.

The selection stack (clip_scoring → hook analysis → repetition penalty →
editorial floor → dedup) is unit-tested layer by layer, but its *combined*
effect was only ever tuned by intuition. This harness replaces intuition with
a metric: given a transcript, a set of candidate windows, and human-labelled
GOLD windows (the moments a human would actually clip), it runs the real
deterministic selection path and reports **recall@k** — how many gold moments
survive into the pipeline's top-k output.

* fully offline and deterministic (no LLM, no network);
* the same fixture always yields the same numbers;
* a built-in ``--demo`` fixture runs out of the box; real labelled fixtures
  use the same JSON schema (see ``docs/SELECTION_BENCHMARK_AR.md``).

Fixture schema::

    {
      "name": "my-video",
      "transcript": [{"start": 0.0, "end": 2.5, "text": "..."}, ...],
      "candidates": [{"start_time": 30, "end_time": 60, "title": "...",
                      "text": "...", "score": 8}, ...],
      "gold": [{"start": 30, "end_time": 60}, ...]
    }

A gold window counts as *recalled* when any top-k output covers at least half
of it (temporal overlap ≥ 0.5 of the gold window's duration).

Exit codes: 0 on success, 1 on a bad fixture, 2 on usage errors.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_MIN_DURATION = 15.0
DEFAULT_MAX_DURATION = 60.0
GOLD_COVERAGE = 0.5


# ---------------------------------------------------------------------------
# Metric
# ---------------------------------------------------------------------------

def coverage(gold: Tuple[float, float], output: Tuple[float, float]) -> float:
    """Fraction of the gold window covered by the output window (0..1)."""
    g_start, g_end = float(gold[0]), float(gold[1])
    o_start, o_end = float(output[0]), float(output[1])
    if g_end <= g_start:
        return 0.0
    intersection = max(0.0, min(g_end, o_end) - max(g_start, o_start))
    return intersection / (g_end - g_start)


def evaluate(gold: Sequence[Tuple[float, float]],
             outputs: Sequence[Tuple[float, float]], *,
             k: Optional[int] = None,
             min_coverage: float = GOLD_COVERAGE) -> Dict[str, Any]:
    """Recall@k over gold windows, plus per-gold diagnostics."""
    limit = int(k) if k else len(outputs)
    top = list(outputs)[: max(0, limit)]
    details = []
    hits = 0
    for window in gold:
        best_rank, best_cov = None, 0.0
        for rank, output in enumerate(top):
            cov = coverage(window, output)
            if cov > best_cov:
                best_rank, best_cov = rank, cov
        recalled = best_cov >= float(min_coverage)
        hits += int(recalled)
        details.append({
            "gold": [round(float(window[0]), 2), round(float(window[1]), 2)],
            "recalled": recalled,
            "best_rank": best_rank,
            "coverage": round(best_cov, 3),
        })
    return {
        "k": limit,
        "gold_total": len(gold),
        "gold_recalled": hits,
        "recall_at_k": round(hits / len(gold), 3) if gold else None,
        "min_coverage": float(min_coverage),
        "details": details,
    }


# ---------------------------------------------------------------------------
# Fixture handling
# ---------------------------------------------------------------------------

def _window(entry: Any, keys: Sequence[str]) -> Optional[Tuple[float, float]]:
    if not isinstance(entry, dict):
        return None
    try:
        start = float(entry.get(keys[0]))
        end = float(entry.get(keys[1]))
    except (TypeError, ValueError):
        return None
    if end <= start:
        return None
    return (start, end)


def validate_fixture(fixture: Dict[str, Any]) -> List[str]:
    """Schema check; returns a list of problems (empty = valid)."""
    problems = []
    if not isinstance(fixture, dict):
        return ["fixture must be a JSON object"]
    transcript = fixture.get("transcript")
    if not isinstance(transcript, list) or not transcript:
        problems.append("transcript must be a non-empty list")
    else:
        for i, seg in enumerate(transcript):
            if _window(seg, ("start", "end")) is None:
                problems.append("transcript[{}] has no valid start/end".format(i))
                break
    candidates = fixture.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        problems.append("candidates must be a non-empty list")
    else:
        for i, cand in enumerate(candidates):
            if _window(cand, ("start_time", "end_time")) is None:
                problems.append("candidates[{}] has no valid start_time/end_time".format(i))
                break
    gold = fixture.get("gold")
    if not isinstance(gold, list) or not gold:
        problems.append("gold must be a non-empty list of labelled windows")
    else:
        for i, window in enumerate(gold):
            if _window(window, ("start", "end")) is None:
                problems.append("gold[{}] has no valid start/end".format(i))
                break
    return problems


def load_fixture(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        fixture = json.load(handle)
    problems = validate_fixture(fixture)
    if problems:
        raise SystemExit("invalid fixture {}: {}".format(path, "; ".join(problems)))
    return fixture


def demo_fixture() -> Dict[str, Any]:
    """A synthetic 10-minute 'talk': 3 hook-strong moments + weak filler."""
    transcript: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []
    gold: List[Dict[str, float]] = []

    t = 0.0
    filler_texts = [
        "يعني طيب أه كما قلت قبل قليل",
        "حسناً لنكمل الحديث العادي عن الموضوع",
        "أه نعم هذا صحيح تماماً يعني",
    ]
    gold_texts = [
        "هل تعلم أن تسعين بالمئة من الناس يخطئون في هذا الرقم الصادم؟",
        "السر الذي لا يخبرك به أحد: ثلاث خطوات فقط تغير كل شيء",
        "أنت تسأل نفسك لماذا يفشل الجميع هنا — الجواب في جملة واحدة",
    ]
    plan = [
        (filler_texts[0], False, 4),
        (filler_texts[1], False, 5),
        (gold_texts[0], True, 9),
        (filler_texts[2], False, 4),
        (filler_texts[1], False, 5),
        (gold_texts[1], True, 8),
        (filler_texts[0], False, 4),
        (gold_texts[2], True, 9),
        (filler_texts[2], False, 5),
        (filler_texts[1], False, 4),
    ]
    for text, is_gold, score in plan:
        start = t
        end = t + 30.0
        # fill the window with sentence-level transcript entries
        cursor = start
        while cursor < end:
            transcript.append({
                "start": round(cursor, 2),
                "end": round(min(cursor + 4.0, end), 2),
                "text": text,
            })
            cursor += 5.0
        candidates.append({
            "start_time": start,
            "end_time": end,
            "title": ("لحظة قوية" if is_gold else "مقطع عادي"),
            "text": text,
            "score": score,
        })
        if is_gold:
            gold.append({"start": start, "end": end})
        t = end + 2.0
    return {"name": "demo", "transcript": transcript,
            "candidates": candidates, "gold": gold}


# ---------------------------------------------------------------------------
# Benchmark run
# ---------------------------------------------------------------------------

def run_benchmark(fixture: Dict[str, Any], *,
                  k: Optional[int] = None,
                  min_duration: float = DEFAULT_MIN_DURATION,
                  max_duration: float = DEFAULT_MAX_DURATION) -> Dict[str, Any]:
    """Run the real selection path and evaluate gold recall."""
    problems = validate_fixture(fixture)
    if problems:
        raise SystemExit("invalid fixture: " + "; ".join(problems))

    from scripts import create_viral_segments

    result = create_viral_segments.process_segments(
        fixture["candidates"], fixture["transcript"],
        min_duration, max_duration,
        output_count=max(len(fixture["gold"]) * 2, 6))
    outputs = result.get("segments", [])
    output_windows = [
        (float(seg.get("start_time", 0.0)), float(seg.get("end_time", 0.0)))
        for seg in outputs
        if isinstance(seg, dict)
    ]
    gold_windows = [
        (float(window["start"]), float(window["end"]))
        for window in fixture["gold"]
    ]
    metrics = evaluate(gold_windows, output_windows, k=k)
    return {
        "tool": "selection_benchmark",
        "fixture": fixture.get("name", "unnamed"),
        "candidates": len(fixture["candidates"]),
        "outputs": len(output_windows),
        "output_windows": [[round(a, 2), round(b, 2)] for a, b in output_windows],
        "metrics": metrics,
    }


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".selection_bench_", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def render_console(report: Dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "قياس جودة الاختيار — fixture: {}".format(report["fixture"]),
        "  المرشحون: {} — النواتج: {} — recall@{}: {} ({}/{})".format(
            report["candidates"], report["outputs"], metrics["k"],
            metrics["recall_at_k"], metrics["gold_recalled"], metrics["gold_total"]),
    ]
    for detail in metrics["details"]:
        icon = "✓" if detail["recalled"] else "✗"
        lines.append("  {} ذهبي {} ← رتبة {} (تغطية {:.0%})".format(
            icon, detail["gold"], detail["best_rank"], detail["coverage"]))
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline selection-quality benchmark (gold-window recall@k)")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fixture", metavar="JSON", help="labelled fixture file")
    source.add_argument("--demo", action="store_true",
                        help="run the built-in synthetic fixture")
    parser.add_argument("-k", type=int, default=None,
                        help="top-k outputs to recall within (default: all outputs)")
    parser.add_argument("--min-duration", type=float, default=DEFAULT_MIN_DURATION)
    parser.add_argument("--max-duration", type=float, default=DEFAULT_MAX_DURATION)
    parser.add_argument("--output", metavar="JSON", help="write the report to a file")
    args = parser.parse_args(argv)

    fixture = demo_fixture() if args.demo else load_fixture(args.fixture)
    report = run_benchmark(fixture, k=args.k,
                           min_duration=args.min_duration,
                           max_duration=args.max_duration)
    print(render_console(report))
    if args.output:
        _write_json(args.output, report)
        print("التقرير: {}".format(os.path.abspath(args.output)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
