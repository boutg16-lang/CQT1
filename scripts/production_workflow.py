# -*- coding: utf-8
"""Unified, review-first production workflow for OUSSAMA Cutter.

Consumes transcript-derived clip candidates and produces one auditable JSON
package: Arabic dialogue analysis, contextual timing suggestions, diverse
selection, final-segment validation, speaker-turn context and optional QC of
already-rendered media. It never renders, uploads, or silently approves clips.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

try:
    from scripts.audio_qc import analyze_file as analyze_audio_file
    from scripts.editorial_intelligence import build_editorial_package
    from scripts.segment_validator import validate_final_segment
except ImportError:  # direct execution from scripts/
    from audio_qc import analyze_file as analyze_audio_file
    from editorial_intelligence import build_editorial_package
    from segment_validator import validate_final_segment

WORKFLOW_VERSION = "1.0.0"


def _text(item: dict[str, Any]) -> str:
    return str(item.get("transcript_text") or item.get("text") or "").strip()


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _speaker_context(segments: list[dict[str, Any]], gap_limit: float = 3.0) -> list[dict[str, Any]]:
    """Annotate whether adjacent source segments continue the same speaker."""
    def sort_start(item: dict[str, Any]) -> float:
        value = item.get("start", 0)
        if value is None or value == "":
            return 0.0
        number = _finite_number(value)
        return number if number is not None else float("inf")

    ordered = sorted(
        (dict(x) for x in segments if isinstance(x, dict)),
        key=sort_start,
    )
    result = []
    for index, item in enumerate(ordered):
        previous = ordered[index - 1] if index else None
        current_speaker = item.get("speaker_id", item.get("speaker", item.get("speaker_label")))
        previous_speaker = None if previous is None else previous.get(
            "speaker_id", previous.get("speaker", previous.get("speaker_label")))
        current_start = _finite_number(item.get("start"))
        previous_end = _finite_number(previous.get("end")) if previous else None
        gap = (current_start - previous_end
               if current_start is not None and previous_end is not None else None)
        same = bool(previous is not None and current_speaker is not None
                    and previous_speaker == current_speaker and gap is not None
                    and -0.001 <= gap <= gap_limit)
        item["speaker_turn_context"] = {
            "speaker_id": current_speaker,
            "continues_previous_speaker": same,
            "gap_from_previous_seconds": round(gap, 3) if gap is not None else None,
            "review_note": (
                "راجع حدود المقطع مع الجملة السابقة للمتحدث نفسه."
                if same else "تحقق من انتقال المتحدث والسياق السمعي."
            ),
        }
        result.append(item)
    return result


def _validate_candidate(item: dict[str, Any], min_duration: float, max_duration: float) -> dict[str, Any]:
    # Validator's stable contract uses start_time/end_time; preserve source keys.
    normalized = dict(item)
    if "start_time" not in normalized:
        normalized["start_time"] = normalized.get("start")
    if "end_time" not in normalized:
        normalized["end_time"] = normalized.get("end")
    if "transcript_text" not in normalized:
        normalized["transcript_text"] = _text(normalized)
    verdict = validate_final_segment(
        normalized, min_duration=min_duration, max_duration=max_duration,
        require_title=False, min_lexical_words=3,
    )
    return {
        "ok": bool(verdict.get("ok")),
        "errors": verdict.get("errors", []),
        "warnings": verdict.get("warnings", []),
        "checks": verdict.get("checks", {}),
        "duration": verdict.get("duration"),
        "decision": "eligible_for_human_review" if verdict.get("ok") else "fix_required",
    }


def run_workflow(segments: list[dict[str, Any]], *, limit: int = 5,
                 max_per_topic: int = 2, min_duration: float = 0.0,
                 max_duration: float = 180.0,
                 rendered_media: list[str] | None = None) -> dict[str, Any]:
    if not isinstance(segments, list):
        raise TypeError("segments must be a list")
    if limit < 1 or max_per_topic < 1:
        raise ValueError("limit and max_per_topic must be >= 1")
    if min_duration < 0 or max_duration <= 0 or min_duration > max_duration:
        raise ValueError("duration bounds must satisfy 0 <= min <= max and max > 0")

    valid_inputs = [dict(x) for x in segments if isinstance(x, dict)]
    speaker_annotated = _speaker_context(valid_inputs)
    package = build_editorial_package(
        speaker_annotated, limit=limit, max_per_topic=max_per_topic,
    )
    selected = []
    for candidate in package.get("selected", []):
        item = dict(candidate)
        item["final_validation"] = _validate_candidate(item, min_duration, max_duration)
        # Never label automated output as publish-approved.
        item["publication_status"] = "human_review_required"
        selected.append(item)

    audio_reports = []
    for path in rendered_media or []:
        audio_reports.append(analyze_audio_file(path))

    fix_required = sum(not x["final_validation"]["ok"] for x in selected)
    audio_blocked = sum(x.get("status") == "block" for x in audio_reports)
    audio_review = sum(x.get("status") == "review" for x in audio_reports)
    return {
        "schema_version": 1,
        "workflow": "OUSSAMA Cutter Production Workflow",
        "workflow_version": WORKFLOW_VERSION,
        "policy": "review_first_no_auto_publish",
        "selected": selected,
        "excluded": package.get("excluded", []),
        "audio_quality_reports": audio_reports,
        "summary": {
            "input_count": len(segments),
            "valid_object_count": len(valid_inputs),
            "selected_count": len(selected),
            "excluded_count": len(package.get("excluded", [])),
            "fix_required_count": fix_required,
            "audio_blocked_count": audio_blocked,
            "audio_review_count": audio_review,
            "publish_approved_count": 0,
            "human_review_required_count": len(selected),
        },
        "release_gate": {
            "status": "blocked" if fix_required or audio_blocked else "human_review_required",
            "automatic_publish_allowed": False,
            "reason": "اجتياز الفحوص الآلية لا يثبت صحة المحتوى أو اكتمال السياق أو موافقة المنصة. يلزم اعتماد بشري قبل النشر.",
        },
        "limitations": [
            "تحليل الحوار يعتمد مؤشرات لغوية تقريبية ولا يفهم كل اللهجات والسياقات.",
            "استمرارية المتحدث تعتمد على speaker_id المرفق من مرحلة diarization ولا تنشئ هوية متحدث من تلقاء نفسها.",
            "فحص الصوت الاختياري يعمل على الملفات المصدّرة فقط ولا يضمن جودة الصورة أو الترجمة.",
            "لا يتنبأ المحرك بالانتشار ولا يقرر مخالفة سياسات YouTube بشكل نهائي.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the unified OUSSAMA Cutter review-first production workflow")
    parser.add_argument("input", type=Path, help="JSON list or object containing segments")
    parser.add_argument("--output", type=Path, default=Path("production_workflow_report.json"))
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--max-per-topic", type=int, default=2)
    parser.add_argument("--min-duration", type=float, default=0.0)
    parser.add_argument("--max-duration", type=float, default=180.0)
    parser.add_argument("--rendered-media", nargs="*", default=[], help="Optional already-rendered clips to audio-QC")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8-sig"))
        segments = payload.get("segments", []) if isinstance(payload, dict) else payload
        report = run_workflow(
            segments, limit=args.limit, max_per_topic=args.max_per_topic,
            min_duration=args.min_duration, max_duration=args.max_duration,
            rendered_media=args.rendered_media,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"summary": report["summary"], "release_gate": report["release_gate"]}, ensure_ascii=False))
        return 0 if report["release_gate"]["status"] != "blocked" else 2
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
