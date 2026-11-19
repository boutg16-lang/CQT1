"""Fingerprint and cache-staleness helpers for viral segment selection.

Extracted verbatim from :mod:`scripts.create_viral_segments` (step 2 of the
incremental monolith split). The helpers are pure and deterministic: they
turn segment windows, a source-video path, the active prompt template and
the selection weights into stable fingerprints, and decide whether a saved
segment list is safe to reuse.

``scripts.create_viral_segments`` re-exports every name in this module, so
existing callers keep working unchanged.
"""

import hashlib
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

try:
    from scripts import arabic_text as _arabic_text
except Exception:
    _arabic_text = None

# Same optional-import guard as scripts/create_viral_segments.py: the four
# clip-quality modules are imported together so that a failure in any one
# of them degrades all of them to None, exactly as the original module did.
# Only clip_scoring and title_factual are used below; segment_validator and
# transcript_window are imported solely for that guard parity.
try:
    from scripts import (
        clip_scoring,
        segment_validator,  # noqa: F401  (guard parity, see above)
        title_factual,
        transcript_window,  # noqa: F401  (guard parity, see above)
    )
except Exception:
    clip_scoring = title_factual = transcript_window = segment_validator = None


if _arabic_text is not None:
    _ARABIC_NUM_TRANSLATION = str.maketrans({})
    _normalize_timestamp_digits = _arabic_text.normalize_digits
else:
    _ARABIC_NUM_TRANSLATION = str.maketrans({
        # Arabic-Indic digits U+0660..U+0669 (٠١٢٣٤٥٦٧٨٩)
        **{ord(src): dst for src, dst in zip("٠١٢٣٤٥٦٧٨٩", "0123456789", strict=False)},
        # Persian digits U+06F0..U+06F9 (۰۱۲۳۴۵۶۷۸۹)
        **{ord(src): dst for src, dst in zip("۰۱۲۳۴۵۶۷۸۹", "0123456789", strict=False)},
        # Arabic decimal separator ٫ (U+066B) and the Arabic comma ٬ (U+066C)
        # — Arabic/Darija LLM output routinely uses both as a decimal point.
        0x066B: ".",
        0x066C: ".",
    })

    def _normalize_timestamp_digits(value):
        return str(value).translate(_ARABIC_NUM_TRANSLATION)


def _normalize_timestamp_text(value):
    """Translate Arabic-Indic/Persian digits and Arabic decimal marks to ASCII.

    Arabic-language LLMs often emit timestamps such as ``٩:٥٠`` or ``١٢٫٥``;
    without this translation ``int()``/``float()`` reject them and the
    segment silently falls back to default timings.
    """
    return _normalize_timestamp_digits(str(value))


def _parse_segment_time(value, default=0.0):
    """Parse seconds from AI timestamps without treating a missing ref as zero.

    Arabic-Indic (٠-٩) and Persian (۰-۹) digits are translated to ASCII
    before parsing, and the Arabic decimal separator ٫ (U+066B) / Arabic
    comma ٬ (U+066C) count as decimal points. mm:ss and h:mm:ss both parse
    after translation: ``٩:٥٠`` → 9 min + 50 s = 590 s, ``1:٩٠`` → 1 min +
    90 s = 150 s. Genuinely malformed input still falls back to ``default``.
    """
    if value is None or value == "":
        return float(default)
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = _normalize_timestamp_text(str(value).strip().lower())
    match = re.search(r"([-+]?\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?", text)
    if ":" in text:
        parts = text.split(":")
        try:
            if len(parts) == 3:
                return max(0.0, int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2]))
            if len(parts) == 2:
                return max(0.0, int(parts[0]) * 60 + float(parts[1]))
        except (TypeError, ValueError):
            return float(default)
    if match:
        try:
            return max(0.0, float(match.group(1)))
        except (TypeError, ValueError, OverflowError):
            return float(default)
    return float(default)


def _segment_window_fingerprint(start_time, end_time):
    """Stable identity for a source window, independent of its title."""
    payload = "{:.3f}:{:.3f}".format(float(start_time), float(end_time))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def segments_manifest_fingerprint(segments):
    """Fingerprint ordered source windows and titles used to create cuts."""
    payload = []
    for item in list(segments or []):
        if not isinstance(item, dict):
            continue
        # Titles are metadata, not footage. Including them here made every
        # title/A-B edit invalidate the manifest and force a full re-cut of
        # clips whose windows had not moved.
        payload.append({
            "start_time": round(_parse_segment_time(item.get("start_time"), 0.0), 3),
            "end_time": round(_parse_segment_time(item.get("end_time"), 0.0), 3),
        })
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def source_video_fingerprint(path):
    """Cheap staleness fingerprint of a source video file: sha1("name|size|mtime_ns").

    Uses only ``os.stat`` metadata — no file content is read, so this is safe
    to call on every pipeline stage. Returns None when the path is missing or
    unreadable.

    main_improved.py embeds this value as ``source_meta.source_video_fp`` when
    it saves the AI-generated segment windows, and compares it on reuse: a
    changed/re-exported input video produces a different fingerprint and
    forces regeneration instead of silently reusing stale AI windows against
    new footage.
    """
    try:
        if not path:
            return None
        stat = os.stat(path)
    except (OSError, TypeError, ValueError):
        return None
    name = os.path.basename(str(path))
    size = getattr(stat, "st_size", 0)
    mtime_ns = getattr(stat, "st_mtime_ns", None)
    if mtime_ns is None:  # tolerate minimal fake stat objects in tests
        mtime_ns = int(getattr(stat, "st_mtime", 0.0) * 1_000_000_000)
    payload = "{}|{}|{}".format(name, size, mtime_ns)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def segments_source_fingerprint(data):
    """Read the embedded source-video fingerprint from a saved segments payload.

    Returns ``data["source_meta"]["source_video_fp"]`` when present, else
    None (missing payload / missing nested dicts are tolerated). Compare this
    against ``source_video_fingerprint(current_input_video)`` before reusing
    previously saved AI windows: a mismatch means the input video changed and
    the old segment list is stale.
    """
    if not isinstance(data, dict):
        return None
    source_meta = data.get("source_meta")
    if not isinstance(source_meta, dict):
        return None
    return source_meta.get("source_video_fp")


def segment_cache_staleness_reason(
        data, current_source_fingerprint, current_config_fingerprint,
        current_transcript_fingerprint=None):
    """Return why saved segment choices cannot be safely reused, or None."""
    if not isinstance(data, dict):
        return "invalid_cache"

    source_meta = data.get("source_meta")
    if not isinstance(source_meta, dict):
        return "missing_source_metadata"

    stored_source = source_meta.get("source_video_fp")
    if not stored_source:
        return "missing_source_fingerprint"
    if not current_source_fingerprint:
        return "current_source_unavailable"
    if stored_source != current_source_fingerprint:
        return "source_video_changed"

    stored_config = source_meta.get("config_fp")
    if not stored_config:
        return "missing_config_fingerprint"
    if not current_config_fingerprint:
        return "current_settings_unavailable"
    if stored_config != current_config_fingerprint:
        return "settings_changed"

    selection_config = data.get("selection_config")
    stored_transcript = source_meta.get("transcript_fp")
    if not stored_transcript and isinstance(selection_config, dict):
        stored_transcript = selection_config.get("transcript_fingerprint")
    if stored_transcript and not current_transcript_fingerprint:
        return "current_transcript_unavailable"
    if current_transcript_fingerprint:
        if not stored_transcript:
            return "missing_transcript_fingerprint"
        if stored_transcript != current_transcript_fingerprint:
            return "transcript_changed"

    return None


# Schema/prompt versioning (v7.40): embedded in the segments config
# fingerprint so changing the selection schema, the scoring weights or the
# prompt template invalidates previously saved results instead of silently
# reusing them.
SEGMENTS_SCHEMA_VERSION = "2.1"


def prompt_version_fingerprint():
    """Stable fingerprint of the ACTIVE prompt template + segment schema.

    sha1 of prompt.txt (the file actually sent to the LLM) combined with the
    selection-schema and scoring versions. Editing prompt.txt, upgrading the
    scoring formula or the JSON schema all change this value → cached segment
    lists are regenerated rather than reused.
    """
    template = ""
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        prompt_path = os.path.join(base_dir, "prompt.txt")
        if os.path.exists(prompt_path):
            with open(prompt_path, "r", encoding="utf-8") as handle:
                template = handle.read()
    except (OSError, UnicodeError) as exc:
        logger.warning("Could not read prompt.txt for cache fingerprinting: %s", type(exc).__name__)
        template = ""
    scoring_version = getattr(clip_scoring, "SCORING_VERSION", "legacy") if clip_scoring else "legacy"
    title_schema = getattr(title_factual, "TITLE_VALIDATION_SCHEMA_VERSION", "legacy") if title_factual else "legacy"
    payload = "{}|{}|{}|{}".format(
        SEGMENTS_SCHEMA_VERSION, scoring_version, title_schema, template)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def selection_weights_fingerprint():
    """Stable fingerprint of the ACTIVE selection weights (env override aware)."""
    if clip_scoring is None:
        return "legacy"
    try:
        return clip_scoring.weights_fingerprint()
    except Exception as exc:
        logger.warning("Could not fingerprint selection weights: %s", type(exc).__name__)
        return "legacy"


def transcript_fingerprint(transcript_segments):
    """Content fingerprint of the transcript used for a segment run.

    sha1 over the ordered (start, end, text) triples. Any re-transcription
    that changes the words/timings changes this value, so previously saved
    windows/titles are recognised as stale instead of being silently reused.
    Returns None for an empty transcript.
    """
    payload = []
    for line in transcript_segments or []:
        if not isinstance(line, dict):
            continue
        payload.append([
            round(float(line.get("start", 0.0) or 0.0), 3),
            round(float(line.get("end", 0.0) or 0.0), 3),
            str(line.get("text") or ""),
        ])
    if not payload:
        return None
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]
