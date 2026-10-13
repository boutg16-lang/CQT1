# -*- coding: utf-8 -*-
"""Shared pipeline utilities (first extraction from main_improved.py).

These leaf helpers lived at the top of ``main_improved.py``; they moved here
unchanged in behaviour so the entrypoint can shrink step by step. Contracts
that MUST NOT change:

* ``emit_progress`` prints ``PROGRESS|stage|percent|message`` — parsed by
  ``webui/app.py`` (the ``PROGRESS|`` protocol), keep it byte-identical.
* ``TEMP_SUBTITLE_CONFIG`` is anchored to the **repo root** (the parent of
  this package), exactly where ``main_improved.py`` used to point it.
* ``cleanup_temp_files`` is registered with ``atexit`` at import time, as it
  was when ``main_improved`` defined it.
"""

import atexit
import json
import os

BASE_VERBOSE = os.getenv("VIRALCUTTER_VERBOSE", "").strip().lower() in {"1", "true", "yes", "on"}
RUNTIME_VERBOSE = BASE_VERBOSE


def set_verbose(flag):
    """Enable runtime debug output (CLI --verbose ORs with the env var)."""
    global RUNTIME_VERBOSE
    RUNTIME_VERBOSE = BASE_VERBOSE or bool(flag)


def debug(message):
    if RUNTIME_VERBOSE:
        print(f"[debug] {message}", flush=True)


def emit_progress(stage, percent, message):
    try:
        print(f"PROGRESS|{stage}|{int(percent)}|{message}", flush=True)
    except Exception:
        pass


#
TEMP_SUBTITLE_CONFIG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "temp_subtitle_config.json")


def cleanup_temp_files():
    try:
        if os.path.exists(TEMP_SUBTITLE_CONFIG):
            os.remove(TEMP_SUBTITLE_CONFIG)
    except Exception:
        pass


atexit.register(cleanup_temp_files)


#
# Configurações de Legenda (ASS Style)
# Cores no formato BGR (Blue-Green-Red) para o ASS
COLORS = {
    "red": "0000FF",  # Red
    "yellow": "00FFFF",   # Yellow
    "green": "00FF00",     # Green
    "white": "FFFFFF",    # White
    "black": "000000",     # Black
    "grey": "808080",     # Grey
}


def load_json_file(path, default=None):
    if default is None:
        default = {}
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        debug(f"Failed to load JSON from {path}: {e}")
        return default


def parse_face_detect_interval(raw_value):
    raw = str(raw_value or "").strip()
    if not raw:
        return None
    try:
        parts = [part.strip() for part in raw.split(",") if part.strip()]
        if len(parts) == 1:
            value = float(parts[0])
            return {"1": value, "2": value}
        values = [float(part) for part in parts[:2]]
        while len(values) < 2:
            values.append(values[-1])
        return {"1": values[0], "2": values[1]}
    except (ValueError, IndexError):
        debug(f"Invalid face detection interval value: {raw_value}")
    return None
