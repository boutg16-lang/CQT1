"""Append-only, secret-free publish history for long-running projects."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone

HISTORY_NAME = "publish_history.jsonl"
SUCCESS_STATUSES = {"uploaded", "scheduled"}

# Several upload workers can record outcomes concurrently inside one process
# (stream_upload_batch spawns a thread per clip). Serialise appends so two
# read-modify-write cycles can never clobber each other.
_RECORD_LOCK = threading.Lock()


def file_fingerprint(video_path, chunk_size=1024 * 1024):
    """Return a content fingerprint without exposing file contents in logs."""
    if not video_path or not os.path.isfile(video_path):
        return None
    digest = hashlib.sha256()
    try:
        with open(video_path, "rb") as stream:
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        return None
    return "sha256:" + digest.hexdigest()


def find_success(project_path, *, platform, video_path):
    """Return a prior successful publish event for this exact file, if any.

    A successful *cross-platform variant* of this clip counts as a success for
    the original too: the uploader stores the variant's bytes/fingerprint under
    a ``variant_of`` marker, so without this the original never matches and the
    clip is re-rendered and re-uploaded on every run.
    """
    fingerprint = file_fingerprint(video_path)
    base_name = os.path.basename(str(video_path or ""))
    for event in load(project_path, limit=1000):
        if event.get("platform") != platform:
            continue
        if event.get("status") not in SUCCESS_STATUSES:
            continue
        same_file = fingerprint is not None and event.get("file_fingerprint") == fingerprint
        same_variant = bool(base_name) and event.get("variant_of") == base_name
        if same_file or same_variant:
            return event
    return None


def record(project_path, *, platform, video_path, title, result=None, error=None,
           privacy_status=None, publish_at=None, extra=None):
    if not project_path or not os.path.isdir(project_path):
        return False
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "platform": platform,
        "video": os.path.basename(video_path or ""),
        "title": (title or "")[:200],
        "status": (result or {}).get("status") if isinstance(result, dict) else "failed" if error else "unknown",
        "privacy_status": privacy_status,
        "publish_at": publish_at,
        "file_fingerprint": file_fingerprint(video_path),
    }
    # Optional structured markers (hashtags, topic/angle/hook_type, variant
    # info…) so the performance-learning loop can correlate *content* with
    # outcomes, not just numeric scores. Simple scalars only — never paths.
    if isinstance(extra, dict):
        for key in ("hashtags", "topic", "angle", "hook_type",
                    "thumbnail", "variant_of"):
            if extra.get(key) is not None:
                event[key] = str(extra.get(key))[:1000]
    if isinstance(result, dict):
        for key in ("video_id", "url"):
            if result.get(key):
                event[key] = result[key]
    if error:
        event["error"] = str(error)[:1000]
    target = os.path.join(project_path, HISTORY_NAME)
    line = json.dumps(event, ensure_ascii=False) + "\n"
    # Append-only under a process-local lock: a single write keeps every event
    # (concurrent records previously overwrote each other via read-modify-write
    # + os.replace). JSONL format and back-compat are unchanged.
    with _RECORD_LOCK:
        try:
            with open(target, "a", encoding="utf-8") as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            return False
    return True


def load(project_path, limit=100):
    path = os.path.join(project_path, HISTORY_NAME)
    if not os.path.isfile(path):
        return []
    rows = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except OSError:
        return []
    return rows[-max(1, int(limit)) :]
