"""Fail-closed policy review for a finished video before YouTube upload."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

from scripts import safety_ai, safety_filter, secure_config
from scripts.media_validation import validate_media_file
from scripts.transcription_validation import read_tsv_entries, validate_entries

MAX_VIDEO_SECONDS = 30 * 60
WINDOW_SECONDS = 45.0
WINDOW_OVERLAP_SECONDS = 5.0
MAX_WINDOW_CHARS = 1400


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def metadata_fingerprint(title: str, caption: str) -> str:
    payload = json.dumps(
        {"title": str(title or ""), "caption": str(caption or "")},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_review_windows(entries, *, duration, window_seconds=WINDOW_SECONDS,
                         overlap_seconds=WINDOW_OVERLAP_SECONDS,
                         max_chars=MAX_WINDOW_CHARS):
    """Create overlapping transcript-backed windows with complete text."""
    clean = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        try:
            start = float(entry["start"])
            end = float(entry["end"])
        except (KeyError, TypeError, ValueError):
            continue
        text = str(entry.get("text") or "").strip()
        if start < 0 or end <= start or not text:
            continue
        clean.append({"start": start, "end": end, "text": text})
    if not clean:
        raise ValueError("transcription contains no usable speech")
    if not 0 < window_seconds <= 60 or not 0 <= overlap_seconds < window_seconds:
        raise ValueError("invalid transcript review window configuration")
    if any(len(item["text"]) > max_chars for item in clean):
        raise ValueError("a transcript entry exceeds the complete-review limit")

    start_at = min(item["start"] for item in clean)
    end_at = min(float(duration), max(item["end"] for item in clean))
    windows = []
    cursor = start_at
    while cursor < end_at:
        stop = min(end_at, cursor + window_seconds)
        parts = [
            item["text"] for item in clean
            if item["end"] > cursor and item["start"] < stop
        ]
        text = " ".join(parts).strip()
        if text:
            if len(text) > max_chars:
                raise ValueError("a transcript review window exceeds the complete-review limit")
            windows.append({
                "index": len(windows),
                "title": "",
                "start_time": round(cursor, 3),
                "end_time": round(stop, 3),
                "text": text,
            })
        if stop >= end_at:
            break
        cursor = stop - overlap_seconds
    if not windows:
        raise ValueError("transcription did not overlap the video duration")
    return windows


def _choose_backend(requested, root_dir):
    requested = str(requested or "auto").strip().lower()
    env_openai_key = (
        os.getenv("OPENAI_API_KEY", "").strip()
        or os.getenv("OPENAI_MODERATION_API_KEY", "").strip()
    )
    config = secure_config.load_api_config(base_dir=root_dir)
    gemini_key = str(config.get("gemini", {}).get("api_key") or "").strip()
    selected = str(config.get("selected_api") or "").strip().lower()
    model = str(config.get("gemini", {}).get("model") or "").strip()

    if requested == "auto":
        if env_openai_key:
            return "openai-moderation", env_openai_key, "omni-moderation-latest"
        if gemini_key:
            return "gemini", gemini_key, model
        if selected == "g4f":
            return "g4f", None, "gpt-4o-mini"
        raise RuntimeError("no contextual safety reviewer is configured")
    if requested == "openai-moderation":
        if not env_openai_key:
            raise RuntimeError("OPENAI_API_KEY is required for OpenAI moderation")
        return requested, env_openai_key, "omni-moderation-latest"
    if requested == "gemini":
        if not gemini_key:
            raise RuntimeError("Gemini API key is not configured in OUSSAMA Cutter")
        return requested, gemini_key, model
    if requested == "g4f":
        return requested, None, "gpt-4o-mini"
    raise ValueError("unsupported contextual safety backend")


def review_transcript(entries, *, duration, title="", caption="", backend="unknown",
                      reviewer=None, video_sha256=""):
    """Run deterministic and contextual checks over every transcript window."""
    windows = build_review_windows(entries, duration=duration)
    for window in windows:
        window["title"] = str(title or "")
        window["caption"] = str(caption or "")

    _, local_report = safety_filter.analyze_segments(
        windows,
        transcript_segments=entries,
        mode="block",
        min_severity="medium",
    )
    if reviewer is None:
        raise RuntimeError("contextual safety reviewer is unavailable")
    verdicts = reviewer(windows)
    verdicts = safety_ai.validate_review_verdicts(windows, verdicts)

    blocked_windows = []
    review_windows = []
    for item in local_report.get("segments", []):
        if item.get("status") == "blocked":
            blocked_windows.append(item.get("index"))
        elif item.get("status") == "manual_review":
            review_windows.append(item.get("index"))
    ai_violations = [
        index for index, verdict in verdicts.items()
        if verdict.get("violation")
    ]
    if blocked_windows or ai_violations:
        status = "blocked"
    elif review_windows:
        status = "review"
    else:
        status = "clear"

    return {
        "schema_version": 1,
        "status": status,
        "review_complete": True,
        "backend": str(backend),
        "video_sha256": video_sha256,
        "metadata_sha256": metadata_fingerprint(title, caption),
        "transcript_entries": len(entries),
        "review_windows": len(windows),
        "blocked_windows": blocked_windows,
        "manual_review_windows": review_windows,
        "ai_violations": ai_violations,
        "ai_review": [
            {"index": index, "violation": item["violation"], "reason": item["reason"]}
            for index, item in sorted(verdicts.items())
        ],
        "local_review": local_report,
    }


def review_video(video_path, *, title="", caption="", backend="auto", model_name=None,
                 transcription_device="auto", root_dir=None, transcriber=None,
                 reviewer=None):
    video_path = os.path.abspath(os.path.expanduser(str(video_path or "")))
    if not os.path.isfile(video_path):
        raise FileNotFoundError("video file does not exist")
    media = validate_media_file(video_path, min_duration=1.0, require_audio=True)
    if not media.get("ok"):
        raise ValueError("video failed media validation: {}".format(
            "; ".join(media.get("errors", [media.get("error", "invalid media")]))))
    duration = float(media.get("duration") or 0)
    if duration <= 0 or duration > MAX_VIDEO_SECONDS:
        raise ValueError("video duration is outside the supported review range")

    if root_dir is None:
        root_dir = str(Path(__file__).resolve().parent.parent)
    if transcriber is None:
        from scripts.transcribe_video import transcribe
        transcriber = transcribe
    if reviewer is None:
        selected_backend, api_key, configured_model = _choose_backend(backend, root_dir)

        def run_reviewer(clips):
            return safety_ai.review_segments(
                clips,
                selected_backend,
                api_key=api_key,
                model_name=model_name or configured_model,
            )

        reviewer = run_reviewer
    else:
        selected_backend = str(backend)

    with tempfile.TemporaryDirectory(prefix="oussama-upload-review-") as work_dir:
        _, tsv_path = transcriber(
            video_path,
            model_name=model_name or "large-v3-turbo",
            project_folder=work_dir,
            device=transcription_device,
        )
        entries = read_tsv_entries(tsv_path)
        validation = validate_entries(entries)
        if not validation["ok"]:
            raise ValueError("transcription failed validation: {}".format(
                "; ".join(validation["errors"])))
        return review_transcript(
            entries,
            duration=duration,
            title=title,
            caption=caption,
            backend=selected_backend,
            reviewer=reviewer,
            video_sha256=_sha256_file(video_path),
        )


def write_report(path, report):
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".upload-review-", suffix=".tmp", dir=str(target.parent))
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


def main(argv=None):
    parser = argparse.ArgumentParser(description="Contextual policy check for a video before upload")
    parser.add_argument("--video", required=True, help="Finished local video file")
    parser.add_argument("--report", required=True, help="JSON report output path")
    parser.add_argument("--title", default="", help="Exact title intended for upload")
    parser.add_argument("--caption", default="", help="Exact description intended for upload")
    parser.add_argument("--backend", choices=("auto", "gemini", "g4f", "openai-moderation"), default="auto")
    parser.add_argument("--model", default=None)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    args = parser.parse_args(argv)
    try:
        report = review_video(
            args.video, title=args.title, caption=args.caption, backend=args.backend,
            model_name=args.model, transcription_device=args.device)
    except Exception as exc:
        report = {
            "schema_version": 1,
            "status": "unavailable",
            "review_complete": False,
            "error": "{}: {}".format(type(exc).__name__, str(exc)[:500]),
        }
        write_report(args.report, report)
        print("[upload-review] review unavailable; upload must remain blocked.", flush=True)
        return 2
    write_report(args.report, report)
    print("[upload-review] {} ({} window(s))".format(
        report["status"], report["review_windows"]), flush=True)
    return 0 if report["status"] == "clear" else 1


if __name__ == "__main__":
    raise SystemExit(main())
