# -*- coding: utf-8 -*-
"""Source-video transcript from the YouTube captions API (round 2, item 3b).

When the source video is a YouTube URL and the project has an OAuth token that
can read its caption tracks (``captions.list`` + ``captions.download`` — both
need ``youtube.force-ssl`` and the token must belong to the video owner), the
caption track is fetched and converted into the exact transcript artifacts the
pipeline expects (``<base>.srt`` / ``<base>.tsv`` / ``<base>.json`` in the
``transcribe_video`` contract, including word timings).  This is also what
Studio's own transcript-based Short editor uses, so cue boundaries match what
the user sees on YouTube.

Every failure is loud and specific (no silent zeros): the exact reason is
printed and ``None`` is returned so the caller falls back to Whisper.
"""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List, Optional, Tuple

DATA_API_BASE = "https://www.googleapis.com/youtube/v3/captions"

_ID_RE = re.compile(r"(?:v=|/shorts/|/live/|youtu\.be/)([\w-]{11})")
_RAW_ID_RE = re.compile(r"^[\w-]{11}$")

# srv3 <p t="start" d="dur"> holds absolute cue times in milliseconds and its
# <s t="offset" d="dur"> children hold word offsets relative to the cue start.
_WORD_TAG = "s"
_CUE_TAG = "p"

__all__ = [
    "parse_srv3",
    "segments_to_json_payload",
    "segments_to_srt",
    "segments_to_tsv",
    "try_source_transcript",
    "video_id_from_url",
    "write_transcript_artifacts",
]


def video_id_from_url(url: str) -> str:
    """The 11-char video id from a YouTube URL, '' when it is not YouTube."""
    text = str(url or "").strip()
    if not text:
        return ""
    match = _RAW_ID_RE.match(text)
    if match:
        return match.group(0)
    match = _ID_RE.search(text)
    if "youtu" not in text:
        return ""
    return match.group(1) if match else ""


def _access_token(token_path: str) -> str:
    try:
        from google.oauth2.credentials import Credentials
    except ImportError as exc:  # pragma: no cover - env without google-auth
        raise RuntimeError("google-auth is required for caption download") from exc
    creds = Credentials.from_authorized_user_file(token_path)
    if not creds.valid:
        import google.auth.transport.requests as transport

        creds.refresh(transport.Request())
    return str(creds.token)


def _api_get(url: str, token: str, timeout: int = 30) -> bytes:
    import urllib.request

    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read()


def _api_get_json(url: str, token: str, timeout: int = 30) -> Dict[str, Any]:
    return json.loads(
        _api_get(url, token, timeout=timeout).decode("utf-8", "replace") or "{}"
    )


def _err_text(exc: BaseException) -> str:
    """A specific, honest error string for HTTP/API failures."""
    status = ""
    body = ""
    if hasattr(exc, "code"):
        status = str(exc.code)
    if hasattr(exc, "read"):
        try:
            body = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            body = ""
    detail = " ".join(part for part in (status, body) if part).strip()
    return detail or str(exc)[:200]


def list_captions(video_id: str, token_path: str) -> List[Dict[str, Any]]:
    """Caption tracks of the video (captions.list), Arabic tracks first."""
    token = _access_token(token_path)
    data = _api_get_json(
        DATA_API_BASE + "?part=snippet&videoId={}".format(video_id), token
    )
    tracks = []
    for item in (data or {}).get("items") or []:
        snippet = item.get("snippet") or {}
        tracks.append(
            {
                "id": item.get("id"),
                "language": str(snippet.get("language") or ""),
                "kind": str(snippet.get("trackKind") or ""),
                "name": str((snippet.get("name") or "")),
            }
        )
    tracks.sort(
        key=lambda t: (
            0 if str(t.get("language") or "").lower().startswith("ar") else 1,
            0 if t.get("kind") == "asr" else 1,
        )
    )
    return tracks


def download_captions(caption_id: str, token_path: str, tfmt: str = "srv3") -> bytes:
    """The raw caption track bytes (captions.download with a timed format)."""
    token = _access_token(token_path)
    return _api_get(DATA_API_BASE + "/{}?tfmt={}".format(caption_id, tfmt), token)


def parse_srv3(payload: bytes) -> List[Dict[str, Any]]:
    """srv3 (TTML timedtext) → segments with per-word timings.

    Contract matches ``transcribe_video``: ``{start, end, text, words}`` with
    float seconds; word offsets inside a cue are relative to the cue start.
    """
    try:
        root = ET.fromstring(payload.decode("utf-8", "replace"))
    except (ET.ParseError, UnicodeDecodeError) as exc:
        raise ValueError("caption track is not valid srv3 XML: {}".format(exc)) from exc

    segments: List[Dict[str, Any]] = []
    for cue in root.iter(_CUE_TAG):
        start_ms = _int_attr(cue, "t")
        dur_ms = _int_attr(cue, "d")
        if start_ms is None:
            continue
        end_ms = start_ms + (dur_ms or 0)
        words: List[Dict[str, Any]] = []
        text_parts: List[str] = []
        if cue.text and cue.text.strip():
            text_parts.append(cue.text.strip())
        for word in cue.iter(_WORD_TAG):
            word_text = (word.text or "").strip()
            if not word_text:
                continue
            offset_ms = _int_attr(word, "t") or 0
            word_dur = _int_attr(word, "d") or 0
            words.append(
                {
                    "word": word_text,
                    "start": (start_ms + offset_ms) / 1000.0,
                    "end": (start_ms + offset_ms + word_dur) / 1000.0,
                }
            )
            text_parts.append(word_text)
        text = " ".join(part for part in text_parts if part)
        if not text:
            continue
        segments.append(
            {
                "start": start_ms / 1000.0,
                "end": (end_ms if end_ms > start_ms else start_ms + 1) / 1000.0,
                "text": text,
                "words": words,
            }
        )
    return segments


def _int_attr(node, name: str) -> Optional[int]:
    raw = node.attrib.get(name)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _fmt_srt_ts(seconds: float) -> str:
    total_ms = int(round(max(0.0, float(seconds)) * 1000))
    hours, rem = divmod(total_ms, 3600000)
    minutes, rem = divmod(rem, 60000)
    secs, millis = divmod(rem, 1000)
    return "{:02d}:{:02d}:{:02d},{:03d}".format(hours, minutes, secs, millis)


def segments_to_srt(segments: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for index, seg in enumerate(segments, start=1):
        lines.append(str(index))
        lines.append(
            "{} --> {}".format(_fmt_srt_ts(seg["start"]), _fmt_srt_ts(seg["end"]))
        )
        lines.append(str(seg.get("text") or "").strip())
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def segments_to_tsv(segments: List[Dict[str, Any]]) -> str:
    """WhisperX TSV contract: integer milliseconds."""
    lines = ["start\tend\ttext"]
    for seg in segments:
        lines.append(
            "{}\t{}\t{}".format(
                int(round(float(seg["start"]) * 1000)),
                int(round(float(seg["end"]) * 1000)),
                str(seg.get("text") or "").replace("\t", " "),
            )
        )
    return "\n".join(lines) + "\n"


def segments_to_json_payload(
    segments: List[Dict[str, Any]], language: str
) -> Dict[str, Any]:
    return {"segments": list(segments), "language": str(language or "ar")}


def write_transcript_artifacts(
    project_folder: str, base_name: str, segments: List[Dict[str, Any]], language: str
) -> Tuple[str, str, str]:
    """Write <base>.srt/.tsv/.json in the transcribe_video contract."""
    srt_file = os.path.join(project_folder, base_name + ".srt")
    tsv_file = os.path.join(project_folder, base_name + ".tsv")
    json_file = os.path.join(project_folder, base_name + ".json")
    with open(srt_file, "w", encoding="utf-8") as handle:
        handle.write(segments_to_srt(segments))
    with open(tsv_file, "w", encoding="utf-8") as handle:
        handle.write(segments_to_tsv(segments))
    with open(json_file, "w", encoding="utf-8") as handle:
        json.dump(
            segments_to_json_payload(segments, language),
            handle,
            ensure_ascii=False,
            indent=2,
        )
    return srt_file, tsv_file, json_file


def _pick_track(tracks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for track in tracks:
        lang = str(track.get("language") or "").lower()
        if lang.startswith("ar") and track.get("id"):
            return track
    for track in tracks:
        if track.get("kind") != "asr" and track.get("id"):
            return track
    for track in tracks:
        if track.get("id"):
            return track
    return None


def try_source_transcript(
    url: str,
    project_folder: str,
    *,
    base_name: Optional[str] = None,
    token_path: str = "",
    log: Optional[Callable[[str], None]] = None,
) -> Optional[Tuple[str, str]]:
    """Transcript artifacts from the source video's own caption track.

    Returns ``(srt_file, tsv_file)`` on success, ``None`` (after printing the
    exact reason) so the caller falls back to Whisper.  Never raises for
    expected API conditions; unexpected bugs propagate.
    """
    emit = log or print

    video_id = video_id_from_url(url)
    if not video_id:
        return None

    token_file = str(token_path or "").strip()
    if not token_file:
        try:
            from webui import youtube_credentials

            token_file = youtube_credentials.token_path()
        except Exception:
            token_file = ""
    if not token_file or not os.path.isfile(token_file):
        emit(
            "[transcribe] YouTube captions unavailable: no OAuth token file "
            "({}); falling back to Whisper.".format(token_file or "default token.json")
        )
        return None

    try:
        tracks = list_captions(video_id, token_file)
    except Exception as exc:
        emit(
            "[transcribe] YouTube captions unavailable (captions.list failed: "
            "{}); falling back to Whisper.".format(_err_text(exc))
        )
        return None
    if not tracks:
        emit(
            "[transcribe] YouTube captions unavailable: video {} has no caption "
            "tracks; falling back to Whisper.".format(video_id)
        )
        return None

    track = _pick_track(tracks)
    if not track or not track.get("id"):
        emit(
            "[transcribe] YouTube captions unavailable: no downloadable track "
            "id; falling back to Whisper."
        )
        return None

    try:
        payload = download_captions(str(track["id"]), token_file)
    except Exception as exc:
        emit(
            "[transcribe] YouTube captions unavailable (captions.download "
            "failed: {}); falling back to Whisper.".format(_err_text(exc))
        )
        return None

    try:
        segments = parse_srv3(payload)
    except ValueError as exc:
        emit(
            "[transcribe] YouTube captions unavailable ({}); falling back to "
            "Whisper.".format(exc)
        )
        return None
    if not segments:
        emit(
            "[transcribe] YouTube captions unavailable: caption track parsed to "
            "zero segments; falling back to Whisper."
        )
        return None

    base = str(base_name or "").strip() or "youtube_source"
    srt_file, tsv_file, json_file = write_transcript_artifacts(
        project_folder, base, segments, str(track.get("language") or "ar")
    )
    emit(
        "[transcribe] Using YouTube caption track ({} track, {} segments) for "
        "the source video — skipping Whisper. [{}]".format(
            track.get("language") or "?", len(segments), video_id
        )
    )
    return srt_file, tsv_file
