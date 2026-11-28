# -*- coding: utf-8 -*-
"""Studio checklist (feat/pro-cutting round 2 item 1).

Two publish steps have NO API endpoint (round 1, verified): pinning a related
video and answering the self-assessment questionnaire. Two more are Studio-only
conveniences: adding an end screen pointing back at the source long video, and
eyeballing the thumbnail in search. This module turns them into a per-clip,
per-video checklist of one-click Studio deep links that is stored in
publish_history and rendered in the WebUI — honestly marked done/pending.
Nothing here pretends to automate what has no API endpoint.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

STUDIO_EDIT_URL = "https://studio.youtube.com/video/{video_id}/edit"
STUDIO_EDITOR_URL = "https://studio.youtube.com/video/{video_id}/editor"

# Compact JSON: publish_history stores it inline (cap 4000 chars).
CHECKLIST_KEYS = ("related_video", "self_assessment", "end_screen", "thumbnail")

_LABELS = {
    "related_video": ("📌 تثبيت الفيديو ذي الصلة", "Pin the related video"),
    "self_assessment": ("🗂️ تقييم المحتوى الذاتي", "Self-assessment questionnaire"),
    "end_screen": ("🎬 شاشة نهاية للفيديو الكامل", "End screen → source long video"),
    "thumbnail": ("🖼️ التحقق من الصورة المصغرة", "Verify thumbnail in search"),
}


def studio_video_url(video_id: str, *, editor: bool = False) -> str:
    vid = str(video_id or "").strip()
    if not vid:
        return ""
    template = STUDIO_EDITOR_URL if editor else STUDIO_EDIT_URL
    return template.format(video_id=vid)


def build_checklist(
    video_id: str,
    *,
    related_video_id: str = "",
    related_video_policy: str = "",
    thumbnail_uploaded: bool = False,
    source_video_url: str = "",
) -> List[Dict[str, Any]]:
    """The four Studio-only steps for one uploaded Short.

    Each item: ``key``, arabic/en label, one-click ``url``, ``done`` flag and a
    short ``note`` explaining why it stays manual (no API endpoint exists).
    """
    vid = str(video_id or "").strip()
    items: List[Dict[str, Any]] = []

    def _item(key: str, url: str, done: bool, note: str) -> Dict[str, Any]:
        ar, en = _LABELS[key]
        return {
            "key": key,
            "label_ar": ar,
            "label_en": en,
            "url": url,
            "done": bool(done),
            "note": note,
        }

    # (a) Pin the related video. The Data API has no relatedVideoId field
    # (videos.update snippet only carries the documented snippet props), so the
    # pin is a Studio-only manual tick; the id is pre-selected for the user.
    related_url = studio_video_url(vid)
    if related_video_id:
        related_url = studio_video_url(str(related_video_id).strip())
    items.append(
        _item(
            "related_video",
            related_url,
            False,
            "no relatedVideoId in the YouTube Data API — pinned manually; "
            "the link opens the pre-selected video",
        )
    )

    # (b) Self-assessment questionnaire. No questionnaire endpoint; the
    # self_cert pre-answer (round 1 item 7) is only a recommendation.
    items.append(
        _item(
            "self_assessment",
            studio_video_url(vid),
            False,
            "no self-assessment API — open Studio and tick the questionnaire; "
            "local self_cert recommendation is stored alongside",
        )
    )

    # (c) End screen pointing at the source long video.
    editor_url = studio_video_url(vid, editor=True)
    end_note = (
        "end screens have no Data-API write; add an element linking the "
        "source long video in the Studio editor"
    )
    if source_video_url:
        end_note += ": " + str(source_video_url).strip()
    items.append(_item("end_screen", editor_url, False, end_note))

    # (d) Verify the thumbnail in search. The thumbnail IS uploaded via the API
    # when present, but search rendering can only be eyeballed.
    items.append(
        _item(
            "thumbnail",
            studio_video_url(vid),
            bool(thumbnail_uploaded),
            "thumbnail uploaded via API when available; verify how it renders "
            "in search and in the Shorts feed",
        )
    )
    return items


def checklist_json(items: List[Dict[str, Any]]) -> str:
    """Compact JSON for the publish_history extra (cap 4000 chars)."""
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def parse_checklist(value: Any) -> List[Dict[str, Any]]:
    """Parse a stored checklist (JSON string or already-parsed list)."""
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


def mark_done(
    items: List[Dict[str, Any]], key: str, done: bool = True
) -> List[Dict[str, Any]]:
    updated: List[Dict[str, Any]] = []
    for item in items:
        entry = dict(item)
        if entry.get("key") == key:
            entry["done"] = bool(done)
        updated.append(entry)
    return updated


def checklist_markdown(items: List[Dict[str, Any]]) -> str:
    """Human-readable checklist for the WebUI publish panel."""
    if not items:
        return ""
    lines = ["**📋 قائمة الاستوديو اليدوية / Manual Studio checklist**", ""]
    for item in items:
        mark = "✅" if item.get("done") else "⬜"
        ar = item.get("label_ar") or item.get("key")
        en = item.get("label_en") or ""
        url = item.get("url") or ""
        link = " [فتح]({})".format(url) if url else ""
        lines.append("- {} {} ({}){}".format(mark, ar, en, link))
        if item.get("note"):
            lines.append("  - ↳ {}".format(item["note"]))
    pending = sum(1 for item in items if not item.get("done"))
    lines.append("")
    lines.append(
        "⏳ {} خطوات يدوية متبقية / {} manual steps pending".format(pending, pending)
    )
    return "\n".join(lines)


def pending_count(items: List[Dict[str, Any]]) -> int:
    return sum(1 for item in items if not item.get("done"))


def checklist_for_event(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The stored checklist of a publish_history event (may be empty)."""
    return parse_checklist((event or {}).get("studio_checklist"))


def update_history_checklist(
    project_path: str,
    platform: str,
    video_id: str,
    key: str,
    done: bool = True,
) -> bool:
    """Append a checklist-update event to publish_history (append-only store).

    The newest event carrying a checklist for the video wins; older events keep
    their own copy (time-travel friendly, same pattern as the rest of the log).
    """
    from webui import publish_history

    target = None
    for event in publish_history.load(project_path, limit=1000):
        if (
            event.get("platform") == platform
            and str(event.get("video_id") or "") == str(video_id or "")
            and checklist_for_event(event)
        ):
            target = event  # load() returns newest-first
    if target is None:
        return False
    items = mark_done(checklist_for_event(target), key, done)
    video_path = target.get("video_path") or target.get("video") or ""
    status = target.get("status") or "success"
    if status not in publish_history.SUCCESS_STATUSES:
        status = "success"
    return publish_history.record(
        project_path,
        platform=platform,
        video_path=str(video_path),
        title=target.get("title") or "",
        result={"status": status, "video_id": video_id, "url": target.get("url") or ""},
        privacy_status=target.get("privacy_status"),
        publish_at=target.get("publish_at"),
        extra={"studio_checklist": checklist_json(items)},
    )


__all__ = [
    "CHECKLIST_KEYS",
    "STUDIO_EDIT_URL",
    "STUDIO_EDITOR_URL",
    "build_checklist",
    "checklist_for_event",
    "checklist_json",
    "checklist_markdown",
    "mark_done",
    "parse_checklist",
    "pending_count",
    "studio_video_url",
    "update_history_checklist",
]
