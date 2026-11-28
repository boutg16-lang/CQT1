# -*- coding: utf-8 -*-
"""YouTube chapters from accepted segments (round 2, item 5).

Chapters are built from the segments that survived selection/validation —
the same hooks that opened each accepted Short become the chapter titles of
the long video's description. YouTube's own rules are enforced here:

- the FIRST chapter MUST start at 0:00 (YouTube: otherwise chapters are ignored);
- every chapter must be at least 10 seconds long;
- timestamps must be ascending;
- a video needs at least 3 chapters for the chapter bar to appear.

Chapters are only produced for sources LONGER than 3 minutes (180s): shorter
videos don't need a chapter bar and the accepted segments would trip the
3-chapter minimum anyway.

Output lands in ``chapters.txt`` inside the project (timestamp + title lines)
and is appended to the long video's description template via
``description_with_chapters`` — idempotent, so re-running never duplicates.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

MIN_SOURCE_SECONDS = 180.0  # only long videos get chapters (>3 minutes)
MIN_CHAPTER_SECONDS = 10.0  # YouTube minimum per chapter
MIN_CHAPTERS = 3  # YouTube minimum chapter count
CHAPTERS_HEADER = "الفصول:"  # chapter block header in descriptions

__all__ = [
    "CHAPTERS_HEADER",
    "MIN_CHAPTER_SECONDS",
    "MIN_CHAPTERS",
    "MIN_SOURCE_SECONDS",
    "build_chapters",
    "chapters_block",
    "chapters_file_path",
    "description_with_chapters",
    "format_ts",
    "project_has_chapters",
    "read_source_description",
    "write_project_chapters",
    "write_project_chapters_from_segments",
]


def _seg_seconds(seg: Dict[str, Any], key: str) -> Optional[float]:
    try:
        value = float(seg.get(key))
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return value


def format_ts(seconds: float) -> str:
    """``MM:SS`` below an hour, ``H:MM:SS`` at/above it (YouTube format)."""
    total = max(0, int(round(float(seconds))))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "{}:{:02d}:{:02d}".format(hours, minutes, secs)
    return "{:02d}:{:02d}".format(minutes, secs)


def build_chapters(
    segments: List[Dict[str, Any]],
    source_duration: Optional[float] = None,
) -> Dict[str, Any]:
    """Chapter list (``[{timestamp, title, start}]``) or ``[]`` with a reason.

    Titles come from the segment's hook/title — the exact text a viewer sees
    opening the related Short. The first chapter is pinned to 0:00 so the
    chapter bar activates; any accepted segment that would violate the
    10-second minimum is skipped.
    """
    duration: Optional[float] = None
    if source_duration is not None:
        try:
            duration = float(source_duration)
        except (TypeError, ValueError):
            duration = None
    if duration is not None and duration <= MIN_SOURCE_SECONDS:
        return {
            "chapters": [],
            "reason": (
                "source is {}s — chapters are only generated for videos longer "
                "than 3 minutes".format(int(duration))
            ),
        }

    usable: List[Tuple[float, str]] = []
    for seg in segments or []:
        start = _seg_seconds(seg, "start_time")
        if start is None:
            continue
        title = str(
            seg.get("hook_text")
            or seg.get("recommended_title")
            or seg.get("title")
            or ""
        ).strip()
        if not title:
            continue
        usable.append((start, title))
    if not usable:
        return {"chapters": [], "reason": "no accepted segments carry a hook title"}

    usable.sort(key=lambda pair: pair[0])

    chapters: List[Dict[str, Any]] = [{"start": 0.0, "title": usable[0][1]}]
    for start, title in usable[1:]:
        previous = chapters[-1]["start"]
        if start - previous < MIN_CHAPTER_SECONDS:
            continue
        if duration is not None and start >= duration - MIN_CHAPTER_SECONDS:
            continue
        chapters.append({"start": start, "title": title})

    if len(chapters) < MIN_CHAPTERS:
        return {
            "chapters": [],
            "reason": (
                "only {} eligible chapters — YouTube needs at least {}".format(
                    len(chapters), MIN_CHAPTERS
                )
            ),
        }

    out = [
        {
            "timestamp": format_ts(ch["start"]),
            "title": ch["title"],
            "start": ch["start"],
        }
        for ch in chapters
    ]
    return {"chapters": out, "reason": ""}


def chapters_block(chapters: List[Dict[str, Any]]) -> str:
    """The description block: header + ``timestamp title`` lines."""
    lines = ["الفصول:", ""]
    for ch in chapters or []:
        lines.append(
            "{} {}".format(ch.get("timestamp", ""), str(ch.get("title", "")).strip())
        )
    return "\n".join(lines).rstrip()


def description_with_chapters(description: str, chapters: List[Dict[str, Any]]) -> str:
    """Append the chapter block to a description; idempotent."""
    if not chapters:
        return str(description or "")
    base = str(description or "").rstrip()
    if "الفصول:" in base:
        return base
    block = chapters_block(chapters)
    return "\n\n".join(part for part in (base, block) if part)


def chapters_file_path(project_folder: str) -> str:
    return os.path.join(project_folder, "chapters.txt")


def project_has_chapters(project_folder: str) -> bool:
    return os.path.isfile(chapters_file_path(project_folder))


def chapters_block_file(project_folder: str) -> str:
    """The chapters.txt content, or ``""`` when there is nothing to append."""
    if not project_has_chapters(project_folder):
        return ""
    try:
        with open(chapters_file_path(project_folder), "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def contains_chapter_block(text: str) -> bool:
    """True when ``text`` already carries chapter timestamps (M:SS/MM:SS)."""
    return CHAPTERS_HEADER in text or "0:00" in text or "00:00" in text


def write_project_chapters(
    project_folder: str,
    chapters: List[Dict[str, Any]],
    *,
    source_description: str = "",
) -> Dict[str, Any]:
    """Persist chapters.txt (+ optionally the updated source description).

    ``source_description``: when non-empty AND the description does not yet
    carry a chapter block, the updated text (with chapters appended) is
    written back to the project as ``chapters_description.txt`` — the template
    the long-video uploader consumes.
    """
    if not chapters:
        return {}
    os.makedirs(project_folder, exist_ok=True)
    path = chapters_file_path(project_folder)
    text = chapters_block(chapters)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text + "\n")
    result: Dict[str, Any] = {"chapters_file": path, "description_file": ""}
    if source_description and "الفصول:" not in str(source_description):
        desc_path = os.path.join(project_folder, "chapters_description.txt")
        with open(desc_path, "w", encoding="utf-8") as handle:
            handle.write(description_with_chapters(source_description, chapters) + "\n")
        result["description_file"] = desc_path
    return result


def read_source_description(project_folder: str) -> str:
    """The long-video description template, when the project carries one.

    Looked for in priority order: ``source_description.txt``,
    ``yt_standard_description.txt``, then the funnel settings'
    ``source_description`` key. Empty string when none exist — chapters still
    land in ``chapters.txt``.
    """
    for name in ("source_description.txt", "yt_standard_description.txt"):
        path = os.path.join(project_folder, name)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read().strip()
            if text:
                return text
        except OSError:
            continue
    try:
        from webui import project_store

        settings = project_store.load_settings(project_folder)
        text = str((settings or {}).get("funnel", {}).get("source_description") or "")
        if text.strip():
            return text.strip()
    except Exception:
        pass
    return ""


def write_project_chapters_from_segments(
    project_folder: str,
    segments: List[Dict[str, Any]],
    *,
    source_video: str = "",
    source_description: str = "",
) -> Dict[str, Any]:
    """Build chapters from accepted segments and persist them.

    ``source_video`` is probed with ffprobe for the source duration; a probe
    failure treats the source as short (chapters skipped with the reason).
    Returns ``{"chapters": [...], "reason": str, "files": {...}}``.
    """
    duration: Optional[float] = None
    if source_video and os.path.isfile(source_video):
        try:
            from scripts import audio_qc

            probe = audio_qc._probe_media(source_video)
            duration = probe.get("duration")
        except Exception:
            duration = None
    report = build_chapters(segments, source_duration=duration)
    files: Dict[str, Any] = {}
    if report.get("chapters"):
        files = write_project_chapters(
            project_folder,
            report["chapters"],
            source_description=str(source_description or ""),
        )
    report["files"] = files
    return report
