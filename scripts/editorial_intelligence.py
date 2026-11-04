# -*- coding: utf-8 -*-
"""One-command Arabic editorial analysis + diverse clip packaging.

This is an explainable decision-support layer, not an autonomous virality
predictor. It annotates transcript cues, applies conservative ranking hints,
and returns selected and excluded candidates for human review.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

try:  # package execution
    from scripts.arabic_dialogue_intelligence import analyze_segments
    from scripts.clip_package import plan_package
    from scripts.viral_moment_intelligence import suggest_moment_window
except ImportError:  # direct execution from scripts directory
    from arabic_dialogue_intelligence import analyze_segments
    from clip_package import plan_package
    from viral_moment_intelligence import suggest_moment_window

_CUE_WEIGHTS = {"question": 1.0, "challenge": 1.5, "rebuttal": 1.5,
                "revelation": 2.0, "conclusion": 1.0, "call_to_action": 0.0}


def prepare_candidates(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate candidates and add a small transparent cue bonus (0..7)."""
    annotated = analyze_segments(segments)
    prepared: list[dict[str, Any]] = []
    for item in annotated:
        # Attach a conservative timing proposal when real timestamps exist.
        try:
            item = suggest_moment_window(item)
        except (TypeError, ValueError) as exc:
            item["moment_window"] = {"status": "invalid_timestamps", "reason": str(exc)}
        intelligence = item["dialogue_intelligence"]
        cue_score = sum(_CUE_WEIGHTS.get(cue["type"], 0.0)
                        for cue in intelligence["cues"])
        cue_score = min(7.0, cue_score)
        base = item.get("selection_score", item.get("score", 0.0))
        try:
            base_score = float(base)
        except (TypeError, ValueError):
            base_score = 0.0
        if not math.isfinite(base_score):
            base_score = 0.0
        item["dialogue_cue_bonus"] = round(cue_score, 2)
        item["editorial_review_score"] = round(base_score + cue_score, 2)
        item["editorial_review_flags"] = []
        if intelligence["word_count"] < 5:
            item["editorial_review_flags"].append("transcript_very_short")
        if intelligence["ending_completeness_proxy"] == "unknown":
            item["editorial_review_flags"].append("ending_needs_manual_review")
        if item.get("moment_window", {}).get("status") != "suggested":
            item["editorial_review_flags"].append("timing_needs_review_or_missing")
        else:
            item["editorial_review_flags"].append("suggested_timing_requires_audio_review")
        item["editorial_review_flags"].append("human_context_review_required")
        prepared.append(item)
    return prepared


def build_editorial_package(segments: list[dict[str, Any]], *, limit: int = 5,
                            max_per_topic: int = 2,
                            min_similarity: float = 0.72,
                            min_gap_seconds: float = 0.0) -> dict[str, Any]:
    prepared = prepare_candidates(segments)
    # Preserve original score; package planner ranks by editorial_review_score.
    for candidate in prepared:
        candidate["selection_score"] = candidate["editorial_review_score"]
    package = plan_package(prepared, limit=limit, max_per_topic=max_per_topic,
                           min_similarity=min_similarity,
                           min_gap_seconds=min_gap_seconds)
    package["engine"] = "OUSSAMA Editorial Intelligence"
    package["engine_version"] = 1
    package["policy"] = "decision_support_only"
    package["review_notice"] = (
        "النتائج مؤشرات لغوية وتنظيمية وليست حكماً على صحة الكلام أو انتشاره "
        "أو مخالفته للسياسات. راجع الصوت والسياق قبل النشر."
    )
    package["summary"]["review_flagged_count"] = sum(
        bool(item.get("editorial_review_flags")) for item in package["selected"])
    return package


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Arabic editorial intelligence pipeline")
    parser.add_argument("input", type=Path, help="JSON list or object containing segments")
    parser.add_argument("--output", type=Path, default=Path("editorial_package.json"))
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--max-per-topic", type=int, default=2)
    parser.add_argument("--min-similarity", type=float, default=0.72)
    parser.add_argument("--min-gap-seconds", type=float, default=0.0)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8-sig"))
        segments = payload.get("segments", []) if isinstance(payload, dict) else payload
        if not isinstance(segments, list):
            raise ValueError("input must be a JSON list or contain a segments list")
        result = build_editorial_package(
            segments, limit=args.limit, max_per_topic=args.max_per_topic,
            min_similarity=args.min_similarity, min_gap_seconds=args.min_gap_seconds)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result["summary"], ensure_ascii=False))
        return 0
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
