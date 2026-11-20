"""Persist the transcription doctor's decision as environment defaults.

The doctor runs in its own process (a native VAD crash must not kill the
WebUI). This small profile carries its conclusion to the app: which backend
to use, whether to force CPU, and how large an audio window is safe for the
available RAM. Explicit user environment variables always win.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict

PROFILE_NAME = ".transcription_health.json"
MAX_AGE_DAYS = 7
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def profile_path() -> str:
    return os.path.join(APP_ROOT, PROFILE_NAME)


def recommended_chunk_seconds() -> int:
    """Pick a window size from free RAM; 300s is the normal default."""
    try:
        import psutil

        free = float(psutil.virtual_memory().available)
    except Exception:
        return 300
    if free < 1.5 * 1024**3:
        return 120
    if free < 2.5 * 1024**3:
        return 180
    return 300


def write_profile(
    *, backend: str, force_cpu: bool, chunk_seconds: int, reason: str = ""
) -> str:
    data: Dict[str, Any] = {
        "backend": str(backend),
        "force_cpu": bool(force_cpu),
        "chunk_seconds": int(chunk_seconds),
        "reason": str(reason or ""),
        "written_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    path = profile_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    except OSError:
        return ""
    return path


def read_profile(path: str | None = None) -> Dict[str, Any]:
    target = path or profile_path()
    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _is_fresh(data: Dict[str, Any]) -> bool:
    raw = str(data.get("written_at") or "")
    if not raw:
        return False
    try:
        written = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    age = datetime.now(timezone.utc) - written
    return age.total_seconds() <= MAX_AGE_DAYS * 86400


def apply_profile(path: str | None = None) -> Dict[str, Any]:
    """Apply profile values with setdefault; explicit user env stays intact."""
    data = read_profile(path)
    if not data or not _is_fresh(data):
        return {}
    backend = str(data.get("backend") or "").strip()
    if backend in {"whisperx", "faster-whisper", "auto"}:
        os.environ.setdefault("VIRALCUTTER_TRANSCRIPTION_BACKEND", backend)
    if data.get("force_cpu") is True:
        os.environ.setdefault("VIRALCUTTER_TRANSCRIPTION_FORCE_CPU", "1")
    try:
        chunk = int(data.get("chunk_seconds"))
    except (TypeError, ValueError):
        chunk = 0
    if 60 <= chunk <= 900:
        os.environ.setdefault("VIRALCUTTER_TRANSCRIBE_CHUNK_SECONDS", str(chunk))
    return data
