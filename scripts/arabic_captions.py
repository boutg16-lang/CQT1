# -*- coding: utf-8 -*-
"""Arabic caption style (round 2 item 3).

Replaces the fast word-by-word karaoke flashes with sentence-level cues
("consistent phrases") — the same shape YouTube Studio's own transcript-based
Short editor uses. Word timings are still honoured: they decide where each
phrase starts and ends, so cues stay in sync without flashing single words.

Three responsibilities:

1. ``group_sentences``  — word timings → sentence cues.
2. ``write_sentence_ass`` / ``srt_text`` — the burn-in ASS (Cairo bold,
   centered, RTL) plus a ``<base>.ar.srt`` sidecar for the YouTube caption
   track.
3. ``ensure_clip_captions`` — Whisper fallback when a clip has no caption
   JSON, and ``upload_caption_track`` — push the Arabic SRT to YouTube after
   publishing (no-op when the token cannot create caption tracks).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, Iterable, List, Optional

from scripts.subtitle_language import detect_text_script

SENTENCE_END = re.compile(r"[.؟?!۔،؛:]$")
RTL_ASS_PREFIX = "{\\rtl}"
ARABIC_FONT = "Cairo"
MAX_CUE_CHARS = 42
MAX_CUE_SECONDS = 3.5
MAX_CUE_GAP = 0.8
CAPTION_LANGUAGE = "ar"


def is_arabic_captions(text: str) -> bool:
    """True when the caption text is Arabic-script dominant."""
    return detect_text_script(text) == "ar"


def _clean_word(word: Any) -> str:
    text = str((word or {}).get("word") or (word or {}).get("text") or "")
    return text.strip()


def group_sentences(
    words: Iterable[Dict[str, Any]],
    *,
    max_chars: int = MAX_CUE_CHARS,
    max_cue_seconds: float = MAX_CUE_SECONDS,
    max_gap: float = MAX_CUE_GAP,
) -> List[Dict[str, Any]]:
    """Group timed words into sentence cues (Studio transcript-editor style).

    A cue closes when: sentence punctuation lands on the current word, the
    character budget (``max_chars``) or duration budget (``max_cue_seconds``)
    is reached, or the gap to the next word exceeds ``max_gap``. Cues never
    drop words: every word lands in exactly one cue, in order.
    """
    cue_words: List[str] = []
    cue_start: Optional[float] = None
    last_end: Optional[float] = None
    cues: List[Dict[str, Any]] = []

    def flush() -> None:
        nonlocal cue_words, cue_start
        text = " ".join(w for w in cue_words if w).strip()
        if not text:
            cue_words, cue_start = [], None
            return
        cues.append(
            {
                "start": float(cue_start or 0.0),
                "end": float(last_end or cue_start or 0.0),
                "text": text,
            }
        )
        cue_words, cue_start = [], None

    for word in words:
        text = _clean_word(word)
        if not text:
            continue
        try:
            start = float(word.get("start") or 0.0)
            end = float(word.get("end") or start)
        except (TypeError, ValueError):
            start = end = float(last_end or 0.0)
        if last_end is not None and cue_words and (start - last_end) > max_gap:
            flush()
        if cue_start is None:
            cue_start = start
        cue_words.append(text)
        last_end = max(last_end or start, end)
        chars = sum(len(w) for w in cue_words) + max(0, len(cue_words) - 1)
        duration = last_end - (cue_start or 0.0)
        sentence_done = bool(SENTENCE_END.search(text))
        if sentence_done or chars >= max_chars or duration >= max_cue_seconds:
            flush()
    flush()
    return cues


def _srt_time(seconds: float) -> str:
    total_ms = int(round(max(0.0, float(seconds)) * 1000))
    hours, rest = divmod(total_ms, 3600000)
    minutes, rest = divmod(rest, 60000)
    secs, ms = divmod(rest, 1000)
    return "{:02d}:{:02d}:{:02d},{:03d}".format(hours, minutes, secs, ms)


def srt_text(cues: Iterable[Dict[str, Any]]) -> str:
    """The YouTube caption-track SRT for the sentence cues."""
    lines: List[str] = []
    for index, cue in enumerate(cues, start=1):
        lines.append(str(index))
        lines.append("{} --> {}".format(_srt_time(cue["start"]), _srt_time(cue["end"])))
        lines.append(str(cue.get("text") or "").strip())
        lines.append("")
    return "\n".join(lines)


def write_srt(cues: Iterable[Dict[str, Any]], output_path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(srt_text(cues))
    return output_path


def _ass_time(seconds: float) -> str:
    total_cs = int(round(max(0.0, float(seconds)) * 100))
    hours, rest = divmod(total_cs, 360000)
    minutes, rest = divmod(rest, 6000)
    secs, cs = divmod(rest, 100)
    return "{:d}:{:02d}:{:02d}.{:02d}".format(hours, minutes, secs, cs)


ASS_HEADER = """[Script Info]
Title: Arabic Sentence Captions
ScriptType: v4.00+
PlayDepth: 0
PlayResX: 360
PlayResY: 640

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{font_size},&H00FFFFFF,&H000000FF,&H00101010,&H7F000000,-1,0,0,0,100,100,0,0,1,3,1,2,20,20,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ass_text(
    cues: Iterable[Dict[str, Any]],
    *,
    font: str = ARABIC_FONT,
    font_size: int = 30,
    margin_v: int = 60,
) -> str:
    """Burn-in ASS for the sentence cues (centered, RTL, Cairo bold)."""
    lines = [ASS_HEADER.format(font=font, font_size=font_size, margin_v=margin_v)]
    for cue in cues:
        text = str(cue.get("text") or "").strip()
        if not text:
            continue
        lines.append(
            "Dialogue: 0,{},{},Default,,0,0,0,{},{}".format(
                _ass_time(cue["start"]), _ass_time(cue["end"]), RTL_ASS_PREFIX, text
            )
        )
    return "\n".join(lines) + "\n"


def write_sentence_ass(
    cues: Iterable[Dict[str, Any]], output_path: str, **style_kwargs: Any
) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(ass_text(cues, **style_kwargs))
    return output_path


def cues_from_processed_json(json_path: str) -> List[Dict[str, Any]]:
    """Sentence cues from a WhisperX ``*_processed.json`` word timeline."""
    with open(json_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    words: List[Dict[str, Any]] = []
    for segment in data.get("segments", []):
        for word in segment.get("words", []):
            entry = dict(word)
            entry.setdefault("start", segment.get("start"))
            entry.setdefault("end", word.get("end") or segment.get("end"))
            words.append(entry)
    return group_sentences(words)


def clip_base(video_path: str) -> str:
    return os.path.splitext(os.path.basename(video_path or ""))[0]


def srt_path_for(project_folder: str, base: str) -> str:
    return os.path.join(project_folder, "subs", "{}.ar.srt".format(base))


def processed_json_path(project_folder: str, base: str) -> str:
    return os.path.join(project_folder, "subs", "{}_processed.json".format(base))


def ensure_clip_captions(
    project_folder: str,
    video_path: str,
    *,
    transcribe_fn: Optional[Callable[..., Any]] = None,
    device: str = "auto",
    model_name: str = "large-v3",
) -> Dict[str, Any]:
    """Guarantee a caption timeline exists for the clip — Whisper fallback.

    When ``subs/<base>_processed.json`` is missing (e.g. the clip was renamed,
    re-rendered, or produced by an older build), the clip is re-transcribed via
    ``transcribe_fn`` (defaults to ``transcribe_video.transcribe`` — WhisperX /
    faster-whisper). Never raises: the report records what happened.
    """
    base = clip_base(video_path)
    report: Dict[str, Any] = {"base": base, "had_captions": False, "transcribed": False}
    if os.path.isfile(processed_json_path(project_folder, base)):
        report["had_captions"] = True
        return report
    if not video_path or not os.path.isfile(video_path):
        report["error"] = "clip file missing; cannot transcribe"
        return report
    try:
        if transcribe_fn is None:
            from scripts import transcribe_video

            transcribe_fn = transcribe_video.transcribe
        transcribe_fn(
            video_path,
            model_name=model_name,
            project_folder=project_folder,
            device=device,
        )
        report["transcribed"] = True
    except Exception as exc:  # noqa: BLE001 - fallback must never break rendering
        report["error"] = str(exc)[:300]
    return report


def ensure_project_captions(
    project_folder: str, *, transcribe_fn: Optional[Callable[..., Any]] = None
) -> List[Dict[str, Any]]:
    """Whisper fallback for every final clip missing its caption JSON."""
    final_folder = os.path.join(project_folder, "final")
    if not os.path.isdir(final_folder):
        return []
    reports: List[Dict[str, Any]] = []
    for name in sorted(os.listdir(final_folder)):
        if not name.endswith((".mp4", ".mkv", ".avi")):
            continue
        report = ensure_clip_captions(
            project_folder,
            os.path.join(final_folder, name),
            transcribe_fn=transcribe_fn,
        )
        if (
            report.get("had_captions")
            or report.get("transcribed")
            or report.get("error")
        ):
            reports.append(report)
    return reports


def build_caption_assets(
    project_folder: str,
    video_path: str,
    *,
    font: str = ARABIC_FONT,
    font_size: int = 30,
    margin_v: int = 60,
) -> Dict[str, Any]:
    """Sentence ASS + ``.ar.srt`` for one clip (no-op for non-Arabic clips).

    Returns the paths so the render step and the post-publish uploader can
    find them without re-parsing the timeline.
    """
    base = clip_base(video_path)
    json_path = processed_json_path(project_folder, base)
    # Accept a subtitle JSON path directly (adjust_subtitles iterates subs/*.json):
    # subs/000_Test_processed.json -> base 000_Test.
    if video_path.endswith("_processed.json"):
        base = clip_base(video_path)[: -len("_processed")]
        json_path = os.path.abspath(video_path)
    report: Dict[str, Any] = {"base": base, "arabic": False}
    if not os.path.isfile(json_path):
        report["error"] = "no caption JSON"
        return report
    try:
        cues = cues_from_processed_json(json_path)
    except Exception as exc:  # noqa: BLE001
        report["error"] = str(exc)[:300]
        return report
    joined = " ".join(cue["text"] for cue in cues)
    if not is_arabic_captions(joined):
        return report
    report["arabic"] = True
    report["cues"] = len(cues)
    ass_path = os.path.join(project_folder, "subs_ass", base + "_processed.ass")
    write_sentence_ass(
        cues, ass_path, font=font, font_size=font_size, margin_v=margin_v
    )
    report["ass_path"] = ass_path
    report["srt_path"] = write_srt(cues, srt_path_for(project_folder, base))
    return report


# ---------------------------------------------------------------------------
# YouTube caption-track upload (after publishing)
# ---------------------------------------------------------------------------

CAPTIONS_SCOPE_NOTE = (
    "youtube.force-ssl scope is required for caption upload; tokens without "
    "it stay silent (the Short itself is already published)."
)


def arabic_srt_for_clip(project_folder: str, video_path: str) -> str:
    """Path of the clip's Arabic caption track ("" when none was exported)."""
    return srt_path_for(project_folder, clip_base(video_path))


def upload_caption_track(
    video_id: str, srt_path: str, token_path: str = ""
) -> Dict[str, Any]:
    """Upload the Arabic SRT as the video's caption track (youtube.captions).

    Uses the same OAuth token file as the uploader. Returns a JSON-safe
    report; failures are recorded, never raised into the publish flow.
    """
    report: Dict[str, Any] = {"video_id": video_id, "srt": srt_path, "uploaded": False}
    if not token_path:
        try:
            from webui import youtube_credentials

            token_path = youtube_credentials.token_path() or ""
        except Exception:
            token_path = ""
    if not video_id or not srt_path or not os.path.isfile(srt_path):
        report["error"] = "missing video_id or srt file"
        return report
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:  # pragma: no cover - env without google libs
        report["error"] = "google-api-python-client required: {}".format(str(exc)[:120])
        return report
    try:
        from webui import youtube_credentials

        creds = Credentials.from_authorized_user_file(
            token_path, youtube_credentials.FULL_SCOPES
        )
        service = build("youtube", "v3", credentials=creds)
        media = MediaFileUpload(
            srt_path, mimetype="application/x-subrip", chunksize=-1, resumable=False
        )
        result = (
            service.captions()
            .insert(
                part="snippet",
                body={
                    "snippet": {
                        "videoId": video_id,
                        "language": CAPTION_LANGUAGE,
                        "name": "Arabic",
                    }
                },
                media_body=media,
            )
            .execute()
        )
        report["uploaded"] = True
        report["caption_id"] = str((result or {}).get("id") or "")
    except Exception as exc:  # noqa: BLE001 - best-effort post-publish step
        report["error"] = str(exc)[:300]
    return report


def upload_clip_captions(
    project_path: str, video_path: str, video_id: str, token_path: str = ""
) -> Dict[str, Any]:
    """Upload the clip's Arabic SRT when one exists (best-effort, no-op else)."""
    base = clip_base(video_path)
    srt_path = srt_path_for(project_path, base)
    if not os.path.isfile(srt_path):
        return {"video_id": video_id, "uploaded": False, "error": "no .ar.srt for clip"}
    return upload_caption_track(video_id, srt_path, token_path)


__all__ = [
    "ARABIC_FONT",
    "CAPTION_LANGUAGE",
    "MAX_CUE_CHARS",
    "MAX_CUE_GAP",
    "MAX_CUE_SECONDS",
    "ass_text",
    "build_caption_assets",
    "cues_from_processed_json",
    "ensure_clip_captions",
    "arabic_srt_for_clip",
    "ensure_project_captions",
    "group_sentences",
    "is_arabic_captions",
    "processed_json_path",
    "srt_path_for",
    "srt_text",
    "upload_caption_track",
    "upload_clip_captions",
    "write_sentence_ass",
    "write_srt",
]
