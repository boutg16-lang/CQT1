# -*- coding: utf-8 -*-
"""Explainable timing suggestions for Arabic dialogue moments.

This module proposes conservative clip windows around transcript segments. It
never claims to predict virality and never changes the source timestamps.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

try:
    from scripts.arabic_dialogue_intelligence import analyze_dialogue
except ImportError:  # direct script execution
    from arabic_dialogue_intelligence import analyze_dialogue

_IMPORTANT = {"question", "challenge", "rebuttal", "revelation", "conclusion"}


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def suggest_moment_window(segment: dict[str, Any], *, lead_in: float = 2.5,
                          tail_out: float = 3.5) -> dict[str, Any]:
    """Return a copy with a reviewable padded window, or a clear missing-time flag."""
    lead_in = _number(lead_in, "lead_in")
    tail_out = _number(tail_out, "tail_out")
    if lead_in < 0 or tail_out < 0:
        raise ValueError("padding values must be >= 0")
    if not isinstance(segment, dict):
        raise TypeError("segment must be a dict")
    item = dict(segment)
    raw_start, raw_end = item.get("start"), item.get("end")
    if raw_start is None or raw_end is None:
        item["moment_window"] = {"status": "needs_timestamps"}
        return item
    start, end = _number(raw_start, "start"), _number(raw_end, "end")
    if start < 0 or end <= start:
        raise ValueError("segment timestamps must satisfy 0 <= start < end")
    text = str(item.get("transcript_text") or item.get("text") or "")
    analysis = analyze_dialogue(text)
    cue_types = [cue["type"] for cue in analysis["cues"]]
    important = [cue for cue in cue_types if cue in _IMPORTANT]
    item["moment_window"] = {
        "status": "suggested",
        "source_start": start,
        "source_end": end,
        "suggested_start": round(max(0.0, start - lead_in), 3),
        "suggested_end": round(end + tail_out, 3),
        "lead_in_seconds": lead_in,
        "tail_out_seconds": tail_out,
        "dialogue_cues": cue_types,
        "importance_signals": important,
        "reason": ("dialogue_cue_context" if important else "context_padding_only"),
        "review_required": True,
        "notice": "اقتراح زمني آلي؛ راجع الصوت للتأكد من اكتمال السؤال والجواب وعدم قطع المتحدث.",
    }
    return item


def analyze_moments(segments: list[dict[str, Any]], *, lead_in: float = 2.5,
                    tail_out: float = 3.5) -> dict[str, Any]:
    if not isinstance(segments, list):
        raise TypeError("segments must be a list")
    annotated = [suggest_moment_window(s, lead_in=lead_in, tail_out=tail_out)
                 for s in segments if isinstance(s, dict)]
    return {
        "schema_version": 1,
        "engine": "OUSSAMA Viral Moment Intelligence",
        "policy": "timing_assistance_not_virality_prediction",
        "segments": annotated,
        "summary": {
            "input_count": len(segments),
            "annotated_count": sum(s.get("moment_window", {}).get("status") == "suggested" for s in annotated),
            "needs_timestamps_count": sum(s.get("moment_window", {}).get("status") == "needs_timestamps" for s in annotated),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Suggest contextual windows around transcript moments")
    parser.add_argument("input", type=Path, help="JSON list or object with segments")
    parser.add_argument("--output", type=Path, default=Path("viral_moments.json"))
    parser.add_argument("--lead-in", type=float, default=2.5)
    parser.add_argument("--tail-out", type=float, default=3.5)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8-sig"))
        segments = payload.get("segments", []) if isinstance(payload, dict) else payload
        result = analyze_moments(segments, lead_in=args.lead_in, tail_out=args.tail_out)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result["summary"], ensure_ascii=False))
        return 0
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
