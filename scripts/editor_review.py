"""Editor-in-chief review pass for candidate clips (LLM-backed, opt-in).

A first-pass model PROPOSES candidate windows; a professional editor then
rejects most of them. This module is that second look: it sends the top
candidates — with the real transcript inside each window and the title
that would ship — to a harsh editor prompt and applies structured
verdicts:

- ``keep``   — the moment genuinely stands alone; publish candidate.
- ``trim``   — the moment is real but the window is loose; the editor
  suggests tighter start/end times (clamped to the original window and
  the minimum duration, never silently widened).
- ``reject`` — no payoff, missing context, or a title that promises what
  the clip never delivers (clickbait lie). Rejection is recorded with a
  reason, never applied silently: the segment stays in the payload,
  marked, and the caller decides.

The LLM call is *injected* (``llm_call(prompt) -> text``), so the module
works with any backend — Gemini, g4f, local, manual — and is fully
testable offline. ``editor_review_segments`` never raises on backend
failure: a broken review pass must never kill a working pipeline
(project rule since v6.7b).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

VERDICTS = ("keep", "trim", "reject")
REVIEW_FILENAME = "editor_review.json"
DEFAULT_BATCH_SIZE = 5
DEFAULT_MAX_CANDIDATES = 10

_EDITOR_PROMPT = """أنت رئيس تحرير قاسٍ لمقاطع قصيرة (Shorts) عربية. أمامك مقاطع مرشحة من فيديو واحد، كل مقطع بنصّه الحقيقي وعنوانه المقترح.

قيّم كل مقطع بمعايير المحرر المحترف:
1. HOOK — هل أول جملة تمسك المشاهد فوراً، أم حشو/تمهيد ميت؟
2. PAYOFF — هل المقطع يَعِد ثم يفي فعلاً داخل النافذة، أم ينقطع قبل اللحظة الحاسمة؟
3. STANDALONE — هل يُفهم وحده دون مشاهدة الفيديو كاملاً (لا «كما قلت قبل قليل» خارج النافذة)؟
4. TITLE — هل العنوان المقترح صادق مع ما يظهر في النص؟ عنوان يَعِد بما لا يُسلَّم = كذبة clickbait مرفوضة.

الحكم لكل مقطع:
- "keep" = يستحق النشر كما هو
- "trim" = اللحظة حقيقية لكن النافذة فضفاضة — حدّد trim_start/trim_end (بالثواني، داخل النافذة الأصلية)
- "reject" = لا خطاف، أو لا payoff، أو يتيم السياق — اذكر السبب

أجب بـ JSON فقط بهذا الشكل:
{{"reviews": [{{"id": "<معرّف المقطع>", "verdict": "keep|trim|reject",
"hook_score": 0-100, "payoff_score": 0-100, "standalone_score": 0-100,
"title_verdict": "honest|oversells|mismatch",
"trim_start": null, "trim_end": null,
"suggested_title": null,
"reason": "<جملة واحدة بالعربية>"}}]}}

المقاطع المرشحة:
{candidates}
"""


def _fmt_time(seconds: Any) -> str:
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return str(seconds)
    minutes, secs = divmod(int(round(seconds)), 60)
    return "{:02d}:{:02d}".format(minutes, secs)


def _candidate_brief(cid: str, segment: Dict[str, Any], *, text_bound: int = 700) -> str:
    title = segment.get("recommended_title") or segment.get("title") or ""
    text = str(segment.get("transcript_text") or segment.get("caption") or "").strip()
    if len(text) > text_bound:
        text = text[:text_bound].rsplit(" ", 1)[0] + "…"
    return (
        "المقطع {cid} | العنوان: «{title}» | النافذة: {start}–{end} ({dur:.1f} ث) | "
        "الدرجة: {score}\nالنص: {text}"
    ).format(
        cid=cid,
        title=title,
        start=_fmt_time(segment.get("start_time")),
        end=_fmt_time(segment.get("end_time")),
        dur=_safe_float(segment.get("duration"), 0.0),
        score=segment.get("score", "?"),
        text=text or "(لا يوجد نص)",
    )


def build_editor_prompt(candidates: Sequence[Tuple[str, Dict[str, Any]]]) -> str:
    """Build the harsh-editor prompt for a batch of (candidate_id, segment).

    Candidate ids are the global ``c<index>`` ids of the reviewed list, so
    the model's verdicts map back without any re-indexing.
    """
    briefs = "\n\n".join(_candidate_brief(cid, segment) for cid, segment in candidates)
    return _EDITOR_PROMPT.replace("{candidates}", briefs)


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Pull the first balanced {...} out of a noisy model response."""
    cleaned = re.sub(r"```(?:json)?|```", "", str(text or ""))
    start = cleaned.find("{")
    while start != -1:
        depth = 0
        for position in range(start, len(cleaned)):
            char = cleaned[position]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        payload = json.loads(cleaned[start:position + 1])
                    except (TypeError, ValueError):
                        break
                    return payload if isinstance(payload, dict) else None
        start = cleaned.find("{", start + 1)
    return None


def _bounded_score(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number < 0.0 or number > 100.0:
        return None
    return round(number, 1)


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Coerce an untrusted value to float, never raising.

    The module contract is fail-open ("NEVER raises"): a segment carrying a
    non-numeric duration or a review carrying a null/string trim timestamp
    must degrade (skip/clamp the bad field), not crash the pipeline.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if number != number:  # NaN
        return float(default)
    return number


def parse_editor_response(text: str) -> List[Dict[str, Any]]:
    """Parse the editor's JSON into validated review dicts.

    Tolerant to code fences, prose around the JSON, missing optional
    fields, and out-of-range scores; reviews without a known id or a
    valid verdict are dropped (a hallucinated review must not silently
    mutate a real segment).
    """
    payload = _extract_json_object(text)
    if not payload:
        return []
    raw_reviews = payload.get("reviews")
    if not isinstance(raw_reviews, list):
        return []
    reviews: List[Dict[str, Any]] = []
    for raw in raw_reviews:
        if not isinstance(raw, dict):
            continue
        cid = str(raw.get("id") or "").strip()
        verdict = str(raw.get("verdict") or "").strip().lower()
        if not cid or verdict not in VERDICTS:
            continue
        review: Dict[str, Any] = {"id": cid, "verdict": verdict}
        for key in ("hook_score", "payoff_score", "standalone_score"):
            score = _bounded_score(raw.get(key))
            if score is not None:
                review[key] = score
        title_verdict = str(raw.get("title_verdict") or "").strip().lower()
        if title_verdict in ("honest", "oversells", "mismatch"):
            review["title_verdict"] = title_verdict
        for key in ("trim_start", "trim_end"):
            try:
                value = raw.get(key)
                if value is not None:
                    review[key] = float(value)
            except (TypeError, ValueError):
                pass
        suggested_title = str(raw.get("suggested_title") or "").strip()
        if suggested_title:
            review["suggested_title"] = suggested_title
        reason = str(raw.get("reason") or "").strip()
        if reason:
            review["reason"] = reason
        reviews.append(review)
    return reviews


def apply_editor_reviews(
    segments: List[Dict[str, Any]],
    reviews: Sequence[Dict[str, Any]],
    *,
    min_duration: float = 15.0,
) -> Dict[str, Any]:
    """Apply validated reviews to the segments (never mutates the input).

    Every reviewed segment gets an ``editor_review`` audit dict. Trims are
    clamped INSIDE the original window and to ``min_duration``; an
    impossible trim falls back to ``keep`` with a note. Rejected segments
    stay in the list, marked — deleting them is the caller's decision.
    """
    by_id = {"c{}".format(index): segment for index, segment in enumerate(segments)}
    summary = {"reviewed": 0, "keep": 0, "trim": 0, "reject": 0, "title_warnings": 0}
    annotated = [dict(segment) for segment in segments]

    for review in reviews:
        source = by_id.get(review.get("id"))
        if source is None:
            continue
        index = int(review["id"][1:])
        entry = annotated[index]
        verdict = review["verdict"]
        record: Dict[str, Any] = {key: review[key] for key in (
            "hook_score", "payoff_score", "standalone_score", "title_verdict",
            "suggested_title", "reason") if key in review}
        record["verdict"] = verdict

        if review.get("title_verdict") in ("oversells", "mismatch"):
            record["title_warning"] = True
            summary["title_warnings"] += 1

        if verdict == "trim":
            start = _safe_float(source.get("start_time"), 0.0)
            end = _safe_float(source.get("end_time"), 0.0)
            # A null/string trim timestamp is a bad suggestion, not a crash:
            # fall back to the original edge and let the clamp/min-duration
            # checks decide (impossible trim -> keep).
            new_start = _safe_float(review.get("trim_start"), start)
            new_end = _safe_float(review.get("trim_end"), end)
            # clamp inside the original window; never widen, never invert
            new_start = max(start, min(new_start, end))
            new_end = min(end, max(new_end, new_start))
            if end - start > 0 and new_end - new_start >= _safe_float(min_duration, 0.0):
                entry["start_time"] = round(new_start, 2)
                entry["end_time"] = round(new_end, 2)
                entry["duration"] = round(new_end - new_start, 2)
                record["trimmed_from"] = [start, end]
            else:
                record["verdict"] = "keep"
                record["trim_fallback"] = (
                    "اقتراح القص خارج النافذة أو أقصر من الحد الأدنى — أُبقي المقطع كما هو")
                verdict = "keep"

        entry["editor_review"] = record
        summary["reviewed"] += 1
        summary[verdict] += 1
    return {"segments": annotated, "summary": summary}


def editor_review_segments(
    segments: List[Dict[str, Any]],
    llm_call: Callable[[str], str],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    min_duration: float = 15.0,
) -> Dict[str, Any]:
    """Run the editor pass over the top candidates. NEVER raises.

    Returns {"segments": annotated, "summary": {...}} where summary lists
    per-batch errors instead of failing — a review outage degrades the
    pass to a no-op, not to a pipeline crash.
    """
    ordered = sorted(
        segments, key=lambda item: _safe_float(item.get("score"), 0.0), reverse=True)
    head = ordered[: max(0, int(max_candidates))]
    tail = ordered[max(0, int(max_candidates)):]

    errors: List[str] = []
    all_reviews: List[Dict[str, Any]] = []
    batch_size = max(1, int(batch_size))
    for offset in range(0, len(head), batch_size):
        batch = list(enumerate(head))[offset: offset + batch_size]
        candidates = [("c{}".format(index), segment) for index, segment in batch]
        prompt = build_editor_prompt(candidates)
        try:
            response = llm_call(prompt)
            for review in parse_editor_response(response):
                cid = review.get("id", "")
                # ids are global head-relative indexes by construction;
                # accept only ids that actually reference a candidate
                if cid.startswith("c") and cid[1:].isdigit() and int(cid[1:]) < len(head):
                    all_reviews.append(review)
        except Exception as exc:  # noqa: BLE001 - fail-open by design
            errors.append("batch {}: {}".format(offset // batch_size, exc))

    result = apply_editor_reviews(head, all_reviews, min_duration=min_duration)
    summary = result["summary"]
    summary["candidates"] = len(head)
    summary["skipped"] = len(tail)
    summary["errors"] = errors
    return {"segments": result["segments"] + tail, "summary": summary}


def _load_llm(backend: str) -> Callable[[str], str]:
    """Resolve a backend name into an llm_call for the CLI."""
    from scripts import create_viral_segments as cvs
    backend = backend.strip().lower()
    if backend == "gemini":
        api_key = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
        if not api_key:
            raise SystemExit("GEMINI_API_KEY is not set — export it or pick --backend g4f")
        return lambda prompt: cvs.call_gemini(prompt, api_key)
    if backend == "g4f":
        return lambda prompt: cvs.call_g4f(prompt)
    raise SystemExit("unknown backend: {} (expected gemini|g4f)".format(backend))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Editor-in-chief review of a project's candidate clips (LLM-backed).")
    parser.add_argument("--project", required=True, help="Project folder with viral_segments.txt")
    parser.add_argument("--backend", default="gemini", choices=["gemini", "g4f"])
    parser.add_argument("--top", type=int, default=DEFAULT_MAX_CANDIDATES,
                        help="How many top-scored candidates to review (default: %(default)s)")
    parser.add_argument("--min-duration", type=float, default=15.0,
                        help="Minimum clip length a trim may keep (default: %(default)s)")
    parser.add_argument("--apply", action="store_true",
                        help="Write the annotated segments back (a .bak backup is kept)")
    args = parser.parse_args(argv)

    from webui import segments_review
    payload = segments_review._read_payload(args.project)
    if not payload or not payload.get("segments"):
        print("[editor-review] no segments found in {}".format(args.project))
        return 2
    segments = payload["segments"]

    result = editor_review_segments(
        segments, _load_llm(args.backend), max_candidates=args.top,
        min_duration=args.min_duration)
    summary = result["summary"]
    print("[editor-review] reviewed={reviewed} keep={keep} trim={trim} reject={reject} "
          "title_warnings={title_warnings} errors={errors}".format(
              **summary, errors=len(summary["errors"])))

    report_path = os.path.join(args.project, REVIEW_FILENAME)
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump({"summary": summary,
                   "reviews": [s.get("editor_review") for s in result["segments"]
                               if s.get("editor_review")]},
                  handle, ensure_ascii=False, indent=2)
    print("[editor-review] report: {}".format(report_path))

    if args.apply:
        target = segments_review.segments_file_path(args.project)
        shutil.copyfile(target, target + ".editor_review.bak")
        payload["segments"] = result["segments"]
        segments_review._write_payload(args.project, payload)
        print("[editor-review] applied (backup: {})".format(target + ".editor_review.bak"))
    return 0 if not summary["errors"] else 1


__all__ = [
    "REVIEW_FILENAME", "VERDICTS", "apply_editor_reviews", "build_editor_prompt",
    "editor_review_segments", "main", "parse_editor_response",
]


if __name__ == "__main__":
    raise SystemExit(main())
