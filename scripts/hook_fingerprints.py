# -*- coding: utf-8 -*-
"""Opening-hook fingerprints vs the publish history (round 2 item 8).

(a) Cut-time refusal: a second Short from the same source video may not open
    with the same quote/hook as an already-published Short from that source.
(b) Hook fingerprints normalise the Arabic opening sentence so "ال، ال" and
    diacritic variants of the same quote collide.

Pure logic, no network: the publish history comes from
``webui.publish_history.load`` (or a caller-supplied event list for tests).
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any, Dict, Iterable, List, Tuple

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webui import publish_history  # noqa: E402

HOOK_WORDS = 8

# Normalisation map: unify alef/hamza/yaa/taa-marbuta variants and drop
# Arabic diacritics so the SAME quote always produces the same fingerprint.
_CHAR_MAP = {
    "أ": "ا",
    "إ": "ا",
    "آ": "ا",
    "ٱ": "ا",
    "ة": "ه",
    "ى": "ي",
    "ـ": "",
}
_DIACRITICS = re.compile(r"[\u064B-\u065F\u0670\u0640]")
_PUNCT = re.compile(r"[^\w\u0600-\u06FF ]+")


def normalize_hook(text: str) -> str:
    """Canonical form of an opening quote (case/hamza/diacritic-insensitive)."""
    text = str(text or "")
    text = _DIACRITICS.sub("", text)
    text = "".join(_CHAR_MAP.get(ch, ch) for ch in text)
    text = re.sub(r"[\u0660-\u0669]", lambda m: str(int(m.group(0))), text)
    text = _PUNCT.sub(" ", text)
    return " ".join(text.split()).casefold()


def hook_fingerprint(text: str, words: int = HOOK_WORDS) -> str:
    """Fingerprint of a clip's opening quote: first N normalised words."""
    norm = normalize_hook(text)
    if not norm:
        return ""
    return " ".join(norm.split()[: max(1, int(words))])


def _first_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.!?؟؛])\s+|[\r\n]+", str(text or ""))
    for part in parts:
        part = part.strip(" \t\r\n-–—•،؛:|")
        if part:
            return part
    return str(text or "").strip()


def _event_fingerprint(event: Dict[str, Any]) -> str:
    """The published clip's opening fingerprint from its stored transcript."""
    transcript = str(event.get("transcript_text") or "")
    if not transcript.strip():
        title = str(event.get("title") or "")
        # The title is not the hook; without a transcript the event simply
        # cannot be fingerprinted (never guess from a title alone).
        del title
        return ""
    return hook_fingerprint(_first_sentence(transcript))


def published_hooks(
    project_folder: str,
    *,
    events: Iterable[Dict[str, Any]] | None = None,
) -> Dict[str, Dict[str, Any]]:
    """Map of opening-fingerprint → published event for this project.

    Only successful uploads count; a scheduled-but-not-published clip does
    not block a re-cut.
    """
    if events is None:
        events = publish_history.load(project_folder)
    hooks: Dict[str, Dict[str, Any]] = {}
    for event in events or []:
        if str(event.get("status") or "").lower() not in publish_history.SUCCESS_STATUSES:
            continue
        fp = _event_fingerprint(event)
        if fp and fp not in hooks:
            hooks[fp] = event
    return hooks


def segment_hook_conflict(
    segment: Dict[str, Any],
    hooks: Dict[str, Dict[str, Any]],
) -> Tuple[str, Dict[str, Any]]:
    """(fingerprint, published_event) when this segment opens a published clip."""
    fp = hook_fingerprint(
        _first_sentence(str(segment.get("transcript_text") or ""))
    )
    if not fp:
        return "", {}
    published = hooks.get(fp)
    if not published:
        return fp, {}
    return fp, published


def apply_published_hook_guard(
    segments: List[Dict[str, Any]],
    hooks: Dict[str, Dict[str, Any]],
    *,
    blocked: List[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    """Drop segments that would re-cut an already-published opening quote.

    ``blocked`` (optional out-param) collects the refused segments with the
    reason so callers can report exactly what was refused and why.
    """
    kept: List[Dict[str, Any]] = []
    for segment in segments:
        fp, published = segment_hook_conflict(segment, hooks)
        if published:
            record = dict(segment)
            record["blocked_reason"] = (
                "opening hook already published: {}".format(
                    str(published.get("title") or "")[:80]
                )
            )
            record["hook_fingerprint"] = fp
            if blocked is not None:
                blocked.append(record)
            continue
        kept.append(segment)
    return kept


__all__ = [
    "HOOK_WORDS",
    "apply_published_hook_guard",
    "hook_fingerprint",
    "normalize_hook",
    "published_hooks",
    "segment_hook_conflict",
]
