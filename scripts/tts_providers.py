# -*- coding: utf-8 -*-
"""TTS provider registry for dubbing (idea from MoneyPrinterTurbo, MIT).

OUSSAMA cuts a creator's *own* footage, so dubbing exists for one reason:
letting a clip that was already translated (``scripts/translate_json.py``) be
**heard** in the target language instead of only subtitled. The provider layer
is deliberately tiny and swappable — exactly the part worth taking from
MoneyPrinterTurbo's design, rewritten for this project's constraints.

Two providers ship:

* ``edge`` — Microsoft Edge TTS (free, no API key). **Network**: the text is
  sent to Microsoft's service, so it is refused unless the operator opts in
  with ``VIRALCUTTER_TTS_ALLOW_NETWORK=1``. This is the only place the project
  sends content off the machine, and it is always explicit.
* ``file`` — reads pre-recorded audio from a folder (``voice`` = directory).
  Fully offline; used for tests, for pre-recorded dubs, and as the failure
  fallback story.

Every provider returns the same dict shape and **never raises**::

    {"ok": True, "path": ..., "duration": 3.2, "provider": "edge",
     "voice": "...", "network": True, "error": None}

New providers register with ``@register("name")``.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
from typing import Any, Callable, Dict, List, Optional

NETWORK_OPT_IN_ENV = "VIRALCUTTER_TTS_ALLOW_NETWORK"
DEFAULT_TIMEOUT = 120
AUDIO_SUFFIXES = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus")

_PROVIDERS: Dict[str, Dict[str, Any]] = {}


def register(name: str, *, network: bool = False, description: str = ""):
    def decorator(function: Callable[..., Dict[str, Any]]):
        _PROVIDERS[name] = {
            "name": name,
            "network": network,
            "description": description,
            "synthesize": function,
        }
        return function
    return decorator


def provider_names() -> List[str]:
    return sorted(_PROVIDERS)


def provider_info(name: str) -> Optional[Dict[str, Any]]:
    entry = _PROVIDERS.get(str(name or "").strip().lower())
    if not entry:
        return None
    return {key: value for key, value in entry.items() if key != "synthesize"}


def _network_allowed() -> bool:
    return str(os.getenv(NETWORK_OPT_IN_ENV, "") or "").strip() == "1"


def _duration(path: str, ffprobe: str = "ffprobe") -> float:
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30)
        return max(0.0, float((proc.stdout or "").strip() or 0.0))
    except Exception:
        return 0.0


def _result(path: str, provider: str, voice: Optional[str], network: bool) -> Dict[str, Any]:
    return {"ok": True, "path": path, "provider": provider, "voice": voice,
            "network": bool(network), "duration": _duration(path), "error": None}


def _failure(provider: str, message: str) -> Dict[str, Any]:
    return {"ok": False, "path": None, "provider": provider, "voice": None,
            "network": bool(provider_info(provider) and provider_info(provider)["network"]),
            "duration": 0.0, "error": str(message)[:400]}


def synthesize(text: str, out_path: str, *, provider: str = "edge",
               voice: Optional[str] = None, rate: Optional[str] = None,
               timeout: int = DEFAULT_TIMEOUT,
               allow_network: Optional[bool] = None) -> Dict[str, Any]:
    """Route one utterance to a provider. Never raises.

    ``allow_network`` lets a caller that already collected *explicit, visible*
    consent (e.g. a checked box in the WebUI) authorise a network provider.
    ``None`` keeps the environment gate as the only source of truth.
    """
    name = str(provider or "edge").strip().lower()
    entry = _PROVIDERS.get(name)
    if entry is None:
        return _failure(name, "unknown TTS provider {!r} (available: {})".format(
            name, ", ".join(provider_names())))
    if not str(text or "").strip():
        return _failure(name, "empty text")
    network_ok = _network_allowed() if allow_network is None else bool(allow_network)
    if entry["network"] and not network_ok:
        return _failure(name, (
            "{} sends the text to an external service; set {}=1 to allow it "
            "(privacy: the transcript leaves this machine)").format(name, NETWORK_OPT_IN_ENV))
    try:
        return entry["synthesize"](str(text), str(out_path), voice=voice,
                                   rate=rate, timeout=timeout)
    except Exception as exc:
        return _failure(name, "{}: {}".format(type(exc).__name__, exc))


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

@register("edge", network=True,
          description="Microsoft Edge TTS — free, no API key, sends text to Microsoft")
def _edge(text: str, out_path: str, *, voice: Optional[str] = None,
          rate: Optional[str] = None, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    try:
        import edge_tts  # noqa: PLC0415 — optional dependency, lazy on purpose
    except Exception as exc:
        return _failure("edge", "edge-tts is not installed (pip install edge-tts): {}".format(
            str(exc)[:160]))
    import asyncio

    voice_name = voice or os.getenv("VIRALCUTTER_TTS_VOICE", "ar-EG-ShakirNeural")
    rate_value = rate or "+0%"
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)

    async def _run() -> None:
        communicate = edge_tts.Communicate(text, voice_name, rate=rate_value)
        await communicate.save(out_path)

    try:
        asyncio.run(asyncio.wait_for(_run(), timeout=timeout))
    except Exception as exc:
        return _failure("edge", "synthesis failed: {}".format(str(exc)[:240]))
    if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        return _failure("edge", "provider produced no audio")
    return _result(out_path, "edge", voice_name, True)


@register("file", network=False,
          description="Pre-recorded audio from a folder (offline; voice = directory)")
def _file(text: str, out_path: str, *, voice: Optional[str] = None,
          rate: Optional[str] = None, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Copy the next unused audio file from *voice* (a directory).

    Deterministic (sorted order) and offline — this is what makes the dubbing
    pipeline testable and what a creator uses to dub with their own recording.
    """
    folder = os.path.abspath(os.path.expanduser(str(voice or "")))
    if not folder or not os.path.isdir(folder):
        return _failure("file", "voice must be an existing folder of audio files")
    candidates = sorted(
        path for path in glob.glob(os.path.join(folder, "*"))
        if path.lower().endswith(AUDIO_SUFFIXES))
    if not candidates:
        return _failure("file", "no audio files in {}".format(folder))
    used_marker = os.path.join(folder, ".used")
    used: List[str] = []
    if os.path.isfile(used_marker):
        with open(used_marker, "r", encoding="utf-8") as handle:
            used = [line.strip() for line in handle if line.strip()]
    for path in candidates:
        if os.path.basename(path) not in used:
            os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
            shutil.copyfile(path, out_path)
            with open(used_marker, "a", encoding="utf-8") as handle:
                handle.write(os.path.basename(path) + "\n")
            return _result(out_path, "file", os.path.basename(path), False)
    return _failure("file", "all files in {} are already used".format(folder))
