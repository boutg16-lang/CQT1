# -*- coding: utf-8 -*-
"""Professional pre-publication review for one rendered clip.

Combines transcript safety, semantic hate/incitement analysis, OCR safety,
factual title validation and metadata compliance into one auditable verdict.
All checks are local; no media or transcript is uploaded by this module.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from scripts import (
    audio_qc,
    media_validation,
    metadata_compliance,
    ocr_safety,
    safety_filter,
    semantic_safety,
    title_factual,
)


def _read_transcript(value: str | None) -> str:
    if not value:
        return ""
    path = Path(value)
    if path.is_file():
        raw = path.read_text(encoding="utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return raw.strip()
        if isinstance(payload, dict):
            if isinstance(payload.get("text"), str):
                return payload["text"].strip()
            entries = payload.get("segments", [])
        else:
            entries = payload
        if isinstance(entries, list):
            return " ".join(str(item.get("text", "")) for item in entries
                            if isinstance(item, dict)).strip()
        return ""
    return str(value).strip()


def _hashtags(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.replace(",", " ").split() if part.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def _title_result(title: str, transcript: str, language: str) -> dict[str, Any]:
    validation = title_factual.validate_title_vs_clip(title, transcript, language)
    return {
        "title": title,
        "validation": validation,
        "seo_score": __import__("scripts.seo_titles", fromlist=["score_title"]).score_title(title),
    }


def review_clip(*, transcript: str, title: str = "", alternatives: list[str] | None = None,
                caption: str = "", hashtags: list[str] | None = None,
                video_path: str | None = None, language: str = "auto",
                scan_ocr: bool = True, ocr_frames: int = 6,
                run_media_qc: bool = True) -> dict[str, Any]:
    """Return a complete review report and a fail-closed publish recommendation."""
    transcript = str(transcript or "").strip()
    if not transcript:
        raise ValueError("transcript is required for a meaningful clip review")

    segment = {"title": title, "text": transcript, "start_time": 0.0,
               "end_time": 0.0}
    _, lexical_report = safety_filter.analyze_segments(
        [segment], transcript_segments=[], mode="block", min_severity="medium")
    semantic = semantic_safety.analyze_text(transcript)

    titles = []
    for candidate in [title, *(alternatives or [])]:
        candidate = str(candidate or "").strip()
        if candidate and candidate.casefold() not in {x["title"].casefold() for x in titles}:
            titles.append(_title_result(candidate, transcript, language))

    metadata = metadata_compliance.metadata_axis(title, caption, _hashtags(hashtags))
    ocr_report: dict[str, Any] = {"enabled": bool(scan_ocr), "status": "not_run",
                                  "action": "allow", "reason": None}
    media_report: dict[str, Any] = {"enabled": bool(run_media_qc), "status": "not_run",
                                    "action": "allow", "technical": None, "audio": None}
    if video_path and not scan_ocr:
        ocr_report.update(status="skipped", action="review", reason="disabled_by_user")
    if video_path and not run_media_qc:
        media_report.update(status="skipped", action="review", reason="disabled_by_user")
    if video_path and scan_ocr:
        if not os.path.isfile(video_path):
            ocr_report.update(status="failed", action="review", reason="video_not_found")
        else:
            try:
                ocr_report = ocr_safety.analyze_video(
                    video_path, frames=max(1, min(int(ocr_frames), 24)), language="ara+eng")
                ocr_report["enabled"] = True
                # An unavailable OCR engine or a partially failed scan is not a clean pass.
                if not ocr_report.get("available", False) or ocr_report.get("status") in {"failed", "unavailable"}:
                    ocr_report["action"] = "review"
                elif any(frame.get("error") for frame in ocr_report.get("frames", [])
                         if isinstance(frame, dict)):
                    ocr_report["action"] = "review"
            except Exception as exc:
                ocr_report = {"enabled": True, "status": "failed", "action": "review",
                              "reason": str(exc)[:300]}

    if video_path and run_media_qc:
        try:
            technical = media_validation.validate_media_file(video_path, require_audio=True)
            audio = audio_qc.analyze_file(video_path)
            technical_ok = bool(technical.get("ok"))
            audio_status = str(audio.get("status", "block"))
            if not technical_ok or audio_status == "block":
                action = "block"
            elif audio_status != "pass":
                action = "review"
            else:
                action = "allow"
            media_report.update(status="checked", action=action,
                                technical=technical, audio=audio)
        except Exception as exc:
            media_report.update(status="failed", action="review",
                                reason=str(exc)[:300])

    lexical_entries = lexical_report.get("segments", [])
    lexical_status = lexical_entries[0].get("status", "unknown") if lexical_entries else "unknown"
    title_rejected = [item["title"] for item in titles
                      if item["validation"].get("status") == "rejected"]
    title_review = [item["title"] for item in titles
                    if item["validation"].get("status") == "review"]

    hard_block = (lexical_status in {"blocked", "semantic_blocked"}
                  or semantic.get("action") == "block"
                  or ocr_report.get("action") == "block"
                  or metadata.get("severity") == "high"
                  or media_report.get("action") == "block")
    needs_review = (not titles or not any(item["validation"].get("status") == "verified"
                                          for item in titles)
                    or semantic.get("action") == "review"
                    or lexical_status == "manual_review"
                    or ocr_report.get("action") == "review"
                    or metadata.get("severity") == "medium"
                    or media_report.get("action") == "review")
    status = "blocked" if hard_block else ("review" if needs_review else "approved_for_human_signoff")

    return {
        "schema_version": 1,
        "status": status,
        "automated_checks_passed": status == "approved_for_human_signoff",
        "human_signoff_required": True,
        "publish_allowed": False,
        "review_scope": "transcript + title + metadata"
                         + (" + sampled_video_frames" if video_path and scan_ocr else "")
                         + (" + technical_media_and_audio_qc" if video_path and run_media_qc else ""),
        "safety": {
            "lexical_status": lexical_status,
            "lexical_report": lexical_report,
            "semantic": semantic,
        },
        "titles": titles,
        "title_summary": {"rejected": title_rejected, "needs_review": title_review,
                          "verified": [item["title"] for item in titles
                                       if item["validation"].get("status") == "verified"]},
        "metadata": metadata,
        "ocr": ocr_report,
        "media_quality": media_report,
        "recommendations": _recommendations(status, lexical_status, semantic, title_rejected,
                                             metadata, ocr_report, media_report),
    }


def _recommendations(status, lexical_status, semantic, rejected_titles, metadata, ocr, media):
    result = []
    if status == "blocked":
        result.append("احجب المقطع عن النشر، وحدد العبارة أو الإطار المخالف قبل أي تعديل.")
    if lexical_status == "manual_review" or semantic.get("action") == "review":
        result.append("راجع السياق الكامل قبل القص؛ فرّق بين الاقتباس الإخباري والإقرار بالتحريض.")
    if rejected_titles:
        result.append("استبدل العناوين المرفوضة بعنوان موثق من كلام المقطع نفسه.")
    if metadata.get("findings"):
        result.append("عالج ملاحظات العنوان والوصف والوسوم قبل التصدير.")
    if ocr.get("action") == "review":
        result.append("أعد فحص الإطارات التي تعذر استخراج النص منها أو راجعها بصرياً.")
    if media.get("action") == "block":
        result.append("أصلح ملف الفيديو أو الصوت؛ فشل التحقق التقني أو غاب مسار الصوت.")
    elif media.get("action") == "review":
        result.append("راجع جودة الصوت والملف؛ تعذر إكمال أحد فحوص الجودة التقنية.")
    if status == "approved_for_human_signoff":
        result.append("اجتاز الفحوص الآلية؛ تبقى الموافقة التحريرية النهائية قبل النشر.")
    return result


def write_report(path: str, report: dict[str, Any]) -> str:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".clip-review-", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(target)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Professional pre-publication clip review")
    parser.add_argument("--transcript", required=True, help="Transcript text or JSON file")
    parser.add_argument("--title", default="")
    parser.add_argument("--alternative-title", action="append", default=[])
    parser.add_argument("--caption", default="")
    parser.add_argument("--hashtag", action="append", default=[])
    parser.add_argument("--video", default=None, help="Optional rendered clip for OCR sampling")
    parser.add_argument("--language", default="auto", choices=("auto", "ar", "en", "fr", "pt", "tr"))
    parser.add_argument("--report", required=True)
    parser.add_argument("--no-ocr", action="store_true")
    parser.add_argument("--no-media-qc", action="store_true",
                        help="Skip FFprobe and audio-quality analysis")
    parser.add_argument("--ocr-frames", type=int, default=6)
    args = parser.parse_args(argv)
    report = review_clip(transcript=_read_transcript(args.transcript), title=args.title,
                         alternatives=args.alternative_title, caption=args.caption,
                         hashtags=args.hashtag, video_path=args.video, language=args.language,
                         scan_ocr=not args.no_ocr, ocr_frames=args.ocr_frames,
                         run_media_qc=not args.no_media_qc)
    write_report(args.report, report)
    print(json.dumps({"status": report["status"],
                      "automated_checks_passed": report["automated_checks_passed"],
                      "human_signoff_required": report["human_signoff_required"],
                      "publish_allowed": report["publish_allowed"],
                      "report": os.path.abspath(args.report)}, ensure_ascii=False))
    return 2 if report["status"] == "blocked" else (1 if report["status"] == "review" else 0)


if __name__ == "__main__":
    raise SystemExit(main())
