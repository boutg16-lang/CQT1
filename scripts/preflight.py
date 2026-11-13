# -*- coding: utf-8 -*-
"""ViralCutter pre-flight check & auto-repair.

Runs BEFORE the app (CLI or WebUI) so every missing piece is detected and —
when possible — installed/repaired automatically. The goal: when the app
starts, everything is in place and nothing is missing.

Design rules:
  * Pure Python stdlib — it must run even on a machine where NOTHING is
    installed yet (that is exactly the case it has to fix).
  * Exit codes are machine-readable:
        0 = ready (all critical checks pass)
        1 = critical problems remain after repair
        2 = warnings only (optional things missing)

Usage:
  python -m scripts.preflight               # read-only check
  python -m scripts.preflight --fix         # interactive repair (asks y/n)
  python -m scripts.preflight --auto-fix    # non-interactive repair
  python -m scripts.preflight --json        # machine-readable report
  python -m scripts.preflight --off         # exit 0 immediately (CI escape)

The same module is imported by main_improved.py / webui/app.py through
run_preflight(mode) — see the integration notes there.
"""
import argparse
import datetime
import importlib
import importlib.metadata as _pkg_meta
import importlib.util as _iu
import json
import logging
import os
import re
import shutil
import subprocess
import sys

from app_assets import RUNTIME_ASSETS
from scripts import ensure_websockets
from scripts.gemini_models import DEFAULT_GEMINI_MODEL

try:
    from app_brand import APP_NAME
except Exception:
    APP_NAME = "OUSSAMA Cutter"

logger = logging.getLogger(__name__)

OK, WARN, FAIL = "ok", "warn", "fail"
ICONS = {OK: "✅", WARN: "⚠️ ", FAIL: "❌"}

MIN_PYTHON = (3, 10)
MIN_PYTHON_RECOMMENDED = (3, 10)
# The project pins NumPy <2 for WhisperX/pyannote compatibility. NumPy 1.26
# wheels support Python through 3.12; fail early on 3.13+ instead of letting
# pip attempt a source build that requires MSVC.
MAX_PYTHON = (3, 12)
MIN_FREE_DISK_MB = 1024  # transcription + ffmpeg temp files need headroom
SAFETY_BLOCKLIST_MAX_AGE_DAYS = 14

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --------------------------------------------------------------------------
# Dependency catalogue
# --------------------------------------------------------------------------
# requirement file -> (label, optional?, install hint)
REQ_FILES = {
    "requirements.txt": ("Core", False, "required for the app to boot"),
    "requirements-transcribe.txt": ("Transcription (whisperx + torch)", True,
                                    "needed for the FULL YouTube->shorts pipeline (~2 GB)"),
    "requirements-transcribe-fallback.txt": ("Transcription fallback (faster-whisper)", True,
                                              "optional recovery path when WhisperX is unavailable"),
    "requirements-upload.txt": ("Upload (YouTube OAuth)", True,
                                "needed only for direct uploads"),
    "requirements-dubbing.txt": ("Dubbing (Edge TTS)", True,
                                 "optional; the offline 'file' provider needs nothing"),
}

# Distribution name -> import module. Covers the dotted-import packages.
DIST_TO_MODULE = {
    "yt-dlp": "yt_dlp",
    "opencv-python": "cv2",
    "ffmpeg-python": "ffmpeg",
    "faster-whisper": "faster_whisper",
    "ctranslate2": "ctranslate2",
    "google-genai": "google.genai",
    "google-generativeai": "google.generativeai",
    "google-api-python-client": "googleapiclient",
    "google-auth-oauthlib": "google_auth_oauthlib",
    "google-auth-httplib2": "google_auth_httplib2",
    "opentelemetry-api": "opentelemetry",
    "pyacoustid": "pyacoustid",
    "Pillow": "PIL",
    "PyYAML": "yaml",
    "python-multipart": "multipart",
    "numpy": "numpy",
    "g4f": "g4f",
    "mediapipe": "mediapipe",
    "insightface": "insightface",
    "gradio": "gradio",
    "psutil": "psutil",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "tqdm": "tqdm",
    "onnxruntime": "onnxruntime",
    "cryptography": "cryptography",
    "whisperx": "whisperx",
    "torch": "torch",
    "torchaudio": "torchaudio",
}

# Version pins that MUST be enforced at runtime (import alone is not enough).
PINNED_RULES = [
    # (dist, violated_if, fix_note)
    ("numpy", "numpy<2", "whisperx/pyannote break on NumPy 2.x - installing numpy==1.26.4"),
    ("transformers", "transformers>=5.14.1,<5.16",
     "keep Transformers on the Hub 1.x-compatible release line"),
    ("tokenizers", "tokenizers<0.23.1",
     "install tokenizers 0.22.2, the newest available version below 0.23.1"),
    ("huggingface-hub", "huggingface-hub>=1.16.0,<2.0",
     "the supported Transformers/Gradio stack requires huggingface-hub>=1.16.0,<2.0"),
    ("ctranslate2", "ctranslate2>=4.4.0,<4.5.0",
     "WhisperX 3.3.1 requires CTranslate2<4.5.0; Windows uses the local pkg_resources compatibility shim"),
    ("setuptools", "setuptools>=84.1.0,<85",
     "avoid vulnerable setuptools releases while retaining the tested transcription stack"),

]

# --------------------------------------------------------------------------
# Asset catalogue (files that must ship with the repo/app)
# --------------------------------------------------------------------------
ASSETS = list(RUNTIME_ASSETS)

API_CONFIG_TEMPLATE = {
    "selected_api": "gemini",
    "gemini": {"api_key": "", "model": DEFAULT_GEMINI_MODEL, "chunk_size": 20000},
    "openai": {"base_url": "http://localhost:11434/v1", "api_key": "",
               "model": "llama3.1", "chunk_size": 12000},
    "g4f": {"model": "gpt-4o-mini", "chunk_size": 2000},
}

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _is_frozen():
    return bool(getattr(sys, "frozen", False))


def _app_dir():
    if _is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return APP_ROOT


def _parse_req_line(line):
    """Parse one requirements.txt line -> (dist_name, raw_spec) or None."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    # strip inline comments (careful: urls contain #)
    if " #" in line:
        line = line.split(" #", 1)[0].strip()
    if line.startswith(("-e ", "--", "-r ", "-c ")) or "://" in line:
        return None
    # "name[extra]==1.0" -> name; "name @ url" -> skip url form
    if " @ " in line:
        line = line.split(" @ ", 1)[0]
    name = line.split("==")[0].split("<")[0].split(">")[0].split("~=")[0].split("!=")[0].strip()
    name = name.split("[")[0].strip()
    if not name:
        return None
    return name, line


def _read_req_names(req_file):
    """Return ordered list of (dist_name, raw_line) from a requirements file."""
    out = []
    path = os.path.join(APP_ROOT, req_file)
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            parsed = _parse_req_line(line)
            if parsed:
                out.append(parsed)
    return out


def _import_name(dist):
    return DIST_TO_MODULE.get(dist, dist.replace("-", "_"))


def _dist_installed(dist):
    try:
        _pkg_meta.version(dist)
        return True
    except _pkg_meta.PackageNotFoundError:
        return False


def _module_importable(module):
    # The legacy Gemini SDK emits a deprecation warning at import time. Check
    # its distribution metadata instead; the modern google-genai SDK is used
    # by default and the legacy package remains a silent compatibility fallback.
    if module == "google.generativeai":
        return _dist_installed("google-generativeai")
    if module in {"ctranslate2", "whisperx", "faster_whisper"}:
        try:
            from scripts.transcription_diagnostics import (
                prepare_ctranslate2_windows_import,
            )
            prepare_ctranslate2_windows_import()
        except Exception:
            pass
    try:
        importlib.import_module(module)
        return True
    except Exception:
        return False


def _import_failure_detail(module):
    try:
        importlib.import_module(module)
    except ModuleNotFoundError as exc:
        missing = exc.name or module
        return "ModuleNotFoundError: missing module %s" % missing
    except Exception as exc:
        return "%s while importing %s" % (type(exc).__name__, module)
    return "UnknownImportError while importing %s" % module


def _version(dist):
    try:
        version = _pkg_meta.version(dist)
    except Exception:
        return "?"
    if not isinstance(version, str) or not version.strip():
        return "?"
    return version.strip()


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------
def check_python():
    v = sys.version_info
    version_text = "%d.%d.%d" % (v[0], v[1], v[2])
    if v < MIN_PYTHON:
        return {"name": "Python", "status": FAIL,
                "detail": "%s - required >= %d.%d" % (version_text, MIN_PYTHON[0], MIN_PYTHON[1])}
    if (v[0], v[1]) > MAX_PYTHON:
        return {"name": "Python", "status": FAIL,
                "detail": "%s - supported range is Python %d.%d through %d.%d; use Python 3.12" % (
                    version_text, MIN_PYTHON[0], MIN_PYTHON[1], MAX_PYTHON[0], MAX_PYTHON[1])}
    status = OK if v >= MIN_PYTHON_RECOMMENDED else WARN
    note = "recommended 3.10/3.11" if status == WARN else ""
    return {"name": "Python", "status": status,
            "detail": "%s%s" % (version_text, (" - " + note) if note else "")}


def check_binary(name, critical=True):
    path = shutil.which(name)
    if path:
        return {"name": name, "status": OK, "detail": path}
    return {"name": name, "status": FAIL if critical else WARN,
            "detail": "not found on PATH"}


def check_ffmpeg_toolchain():
    """ffmpeg/ffprobe checks that verify the binaries actually RUN.

    yt-dlp (2025+) executes ``ffmpeg -bsfs`` and requires parseable version
    output before any merge; an existence-only check lets a broken PATH copy
    (lone ffmpeg.exe without DLLs shadowing a healthy install) through and
    every download dies later with "ffmpeg is not installed".
    """
    try:
        from scripts.ffmpeg_toolchain import resolve_toolchain
    except Exception:  # pragma: no cover - defensive
        return [check_binary("ffmpeg", critical=True),
                check_binary("ffprobe", critical=True)]
    tc = resolve_toolchain()
    entries = []
    for name in ("ffmpeg", "ffprobe"):
        info = tc["programs"][name]
        state, path = info["state"], info["path"]
        if state == "ok":
            entries.append({"name": name, "status": OK, "detail": path})
        elif state == "rescued":
            entries.append({"name": name, "status": WARN,
                            "detail": "PATH copy broken/missing - using verified %s "
                                      "(fix the broken PATH entry when convenient)" % path})
        elif state == "broken":
            entries.append({"name": name, "status": FAIL,
                            "detail": "found at %s but it does not run (corrupt or missing "
                                      "DLLs) - delete it or reinstall full ffmpeg "
                                      "(winget install ffmpeg)" % path})
        else:
            entries.append({"name": name, "status": FAIL, "detail": "not found on PATH"})
    return entries


def check_bundled_binary(name):
    """Frozen exe: tools live in sys._MEIPASS - check there first."""
    if not _is_frozen():
        return None
    bundle = getattr(sys, "_MEIPASS", "") or ""
    exe = name + (".exe" if os.name == "nt" else "")
    for base in (bundle, _app_dir()):
        cand = os.path.join(base, exe)
        if os.path.exists(cand):
            return {"name": "%s (bundled)" % name, "status": OK, "detail": cand}
    return None


def check_disk_space(path):
    try:
        free = shutil.disk_usage(path).free // (1024 * 1024)
        status = OK if free >= MIN_FREE_DISK_MB else FAIL
        return {"name": "Disk space", "status": status,
                "detail": "%d MB free (need >= %d MB)" % (free, MIN_FREE_DISK_MB)}
    except Exception as e:
        return {"name": "Disk space", "status": WARN, "detail": "cannot check: %s" % e}


def check_writable(path, label):
    try:
        os.makedirs(path, exist_ok=True)
        test_path = os.path.join(path, ".vc_write_test")
        with open(test_path, "w") as fh:
            fh.write("ok")
        os.remove(test_path)
        return {"name": label, "status": OK, "detail": os.path.abspath(path)}
    except Exception as e:
        return {"name": label, "status": FAIL, "detail": str(e)}


def check_dependency(dist, raw_line, critical):
    module = _import_name(dist)
    if dist == "websockets" and ensure_websockets.needs_repair():
        installed = _dist_installed(dist)
        if not installed:
            detail = "missing (%s)" % dist
        else:
            detail = "incompatible or damaged version %s (requires %s)" % (
                _version(dist), ensure_websockets.COMPATIBILITY_SPEC,
            )
        return {"name": dist, "status": FAIL if critical else WARN, "detail": detail}
    if not _module_importable(module):
        installed = _dist_installed(dist)
        if not installed:
            detail = "missing (%s)" % dist
        else:
            detail = "installed but import failed (%s; %s)" % (
                module, _import_failure_detail(module),
            )
        return {"name": dist, "status": FAIL if critical else WARN, "detail": detail}
    version = _version(dist)
    if version == "?":
        if not _dist_installed(dist):
            return {
                "name": dist,
                "status": OK,
                "detail": "import succeeded; no distribution metadata is available",
            }
        return {
            "name": dist,
            "status": WARN,
            "detail": "import succeeded; distribution version metadata unavailable",
        }
    return {"name": dist, "status": OK, "detail": version}


def check_pin(dist, rule, note=None):
    """Enforce PINNED_RULES. Returns None when ok, else a FAIL dict."""
    spec = _iu.find_spec(dist.replace("-", "_"))
    if spec is None:
        return None
    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import parse as parse_version
    except Exception:
        return {
            "name": dist,
            "status": FAIL,
            "detail": "cannot verify %s because packaging metadata support is unavailable" % rule,
        }
    if note is None:
        for _dist, _rule, _note in PINNED_RULES:
            if _dist == dist and _rule == rule:
                note = _note
                break
        note = note or "version pin %s violated" % rule
    version = _version(dist)
    if version == "?":
        return {
            "name": dist,
            "status": FAIL,
            "detail": "could not determine installed version for %s; cannot verify %s" % (dist, rule),
        }
    try:
        installed = parse_version(version)
    except Exception:
        return {
            "name": dist,
            "status": FAIL,
            "detail": "invalid installed version metadata for %s; cannot verify %s" % (dist, rule),
        }
    constraint = rule[len(dist):].strip()
    if constraint and not SpecifierSet(constraint).contains(installed, prereleases=True):
        return {
            "name": dist,
            "status": FAIL,
            "detail": "version %s violates %s - %s" % (version, rule, note),
        }
    return None


def check_api_config():
    app_dir = _app_dir()
    path = os.path.join(app_dir, "api_config.json")
    env_present = any(os.getenv(name, "").strip() for name in (
        "VIRALCUTTER_GEMINI_KEYS", "VIRALCUTTER_GEMINI_KEY", "GEMINI_API_KEY"))
    secure_store_exists = os.path.isfile(os.path.join(app_dir, "api_config.secure.json"))
    try:
        from scripts.secure_config import get_keys, resolve_api_keys, secure_config_path
        resolved_keys = resolve_api_keys(base_dir=app_dir, warn=False)
        encrypted_keys = get_keys(base_dir=app_dir)
        secure_store_exists = os.path.isfile(secure_config_path(app_dir))
    except Exception as exc:
        logger.warning("Could not resolve Gemini credentials (%s).", type(exc).__name__)
        resolved_keys = []
        encrypted_keys = []

    if not os.path.exists(path):
        if env_present and resolved_keys:
            key = resolved_keys[0]
            if not key.startswith("AIza"):
                return {"name": "Gemini API key", "status": WARN,
                        "detail": "environment key does not look like a Gemini key"}
            return {"name": "Gemini API key", "status": OK,
                    "detail": "configured via environment; no plaintext config file needed"}
        if encrypted_keys:
            return {"name": "Gemini API key", "status": OK,
                    "detail": "configured in encrypted store; no plaintext config file needed"}
        if secure_store_exists:
            return {"name": "Encrypted Gemini key store", "status": WARN,
                    "detail": "encrypted credentials could not be opened; check VIRALCUTTER_CONFIG_PASSPHRASE; plaintext fallback is disabled"}
        return {"name": "api_config.json", "status": WARN,
                "detail": "missing on first run; Gemini credentials are optional until AI analysis is used"}

    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        return {"name": "api_config.json", "status": FAIL,
                "detail": "invalid JSON (%s); the file was not changed" % type(exc).__name__}
    if not isinstance(data, dict):
        return {"name": "api_config.json", "status": FAIL,
                "detail": "invalid configuration structure; expected a JSON object"}
    gemini = data.get("gemini") or {}
    if not isinstance(gemini, dict):
        return {"name": "api_config.json", "status": FAIL,
                "detail": "invalid Gemini configuration structure"}
    backend = str(data.get("selected_api") or "gemini").strip().lower()
    if backend in {"g4f", "local", "manual", "openai"}:
        detail = "not required for selected backend: %s" % backend
        if backend == "openai":
            base_url = str((data.get("openai") or {}).get("base_url")
                           or "http://localhost:11434/v1")
            detail += " (OpenAI-compatible server: %s)" % base_url
        return {"name": "Gemini API key", "status": OK, "detail": detail}

    if env_present and resolved_keys:
        key = resolved_keys[0]
        if not key.startswith("AIza"):
            return {"name": "Gemini API key", "status": WARN,
                    "detail": "environment key does not look like a Gemini key"}
        return {"name": "Gemini API key", "status": OK,
                "detail": "configured via environment"}
    if encrypted_keys:
        key = encrypted_keys[0]
        if not key.startswith("AIza"):
            return {"name": "Gemini API key", "status": WARN,
                    "detail": "encrypted key does not look like a Gemini key"}
        return {"name": "Gemini API key", "status": OK,
                "detail": "configured in encrypted store"}

    if secure_store_exists:
        return {"name": "Encrypted Gemini key store", "status": WARN,
                "detail": "encrypted credentials could not be opened; check VIRALCUTTER_CONFIG_PASSPHRASE; plaintext fallback is disabled"}

    file_keys = gemini.get("api_keys") or [gemini.get("api_key", "")]
    key = next((str(value or "").strip() for value in file_keys
                if str(value or "").strip()), "")
    if not key:
        return {"name": "Gemini API key", "status": WARN,
                "detail": "empty - set an environment key or encrypted store, or use --ai-backend manual/g4f/local/openai"}
    if not key.startswith("AIza"):
        return {"name": "Gemini API key", "status": WARN,
                "detail": "legacy file key does not look like a Gemini key"}
    return {"name": "Gemini API key", "status": WARN,
            "detail": "configured in legacy plaintext api_config.json; set VIRALCUTTER_CONFIG_PASSPHRASE to migrate"}


def check_assets():
    missing = [a for a in ASSETS if not os.path.exists(os.path.join(APP_ROOT, a))]
    if missing:
        return {"name": "Packaged assets", "status": FAIL,
                "detail": "missing: %s - re-clone/re-extract the app; a damaged install will fail mid-pipeline"
                % ", ".join(missing[:3])}
    return {"name": "Packaged assets", "status": OK, "detail": "%d files present" % len(ASSETS)}


def check_safety_blocklist_freshness(base_dir=None, today=None):
    base_dir = base_dir or APP_ROOT
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    candidates = []

    try:
        from scripts.safety_updater import load_cached_pack, validate_pack
    except Exception:
        load_cached_pack = None
        validate_pack = None

    if validate_pack:
        try:
            with open(os.path.join(base_dir, "safety_blocklist.json"), "r", encoding="utf-8") as f:
                bundled = validate_pack(json.load(f))
            if bundled:
                candidates.append(("bundled list", bundled))
        except Exception:
            pass
        try:
            cached = load_cached_pack(base_dir)
            if cached:
                candidates.append(("downloaded cache", cached))
        except Exception:
            pass

    dated = []
    for source, pack in candidates:
        try:
            version = int(pack.get("version", 0))
        except (TypeError, ValueError):
            version = 0
        try:
            content_updated = datetime.date.fromisoformat(
                str(pack.get("updated", ""))[:10])
        except (TypeError, ValueError):
            content_updated = None
        review_value = pack.get("reviewed_at") or pack.get("updated", "")
        try:
            reviewed = datetime.date.fromisoformat(str(review_value)[:10])
        except (TypeError, ValueError):
            continue
        dated.append((version, reviewed, content_updated, source))

    if not dated:
        return {"name": "Safety blocklist freshness", "status": WARN,
                "detail": "could not verify the list review date; review the safety pack manually"}

    latest_version = max(item[0] for item in dated)
    current = max((item for item in dated if item[0] == latest_version),
                  key=lambda item: item[1])
    version, reviewed, content_updated, source = current
    age_days = (today - reviewed).days
    content_date = content_updated.isoformat() if content_updated else "unknown"
    detail = "v{} from {} last reviewed {} ({} days ago; content last updated {})".format(
        version or "unknown", source, reviewed.isoformat(), age_days, content_date)
    if age_days < 0:
        return {"name": "Safety blocklist freshness", "status": WARN,
                "detail": detail + "; review date is in the future, check the system clock"}
    if age_days > SAFETY_BLOCKLIST_MAX_AGE_DAYS:
        return {"name": "Safety blocklist freshness", "status": WARN,
                "detail": detail + "; older than the {}-day review interval".format(
                    SAFETY_BLOCKLIST_MAX_AGE_DAYS)}
    return {"name": "Safety blocklist freshness", "status": OK, "detail": detail}


def check_models_dir():
    path = os.path.join(APP_ROOT, "models")
    if os.path.isdir(path):
        files = [f for f in os.listdir(path) if not f.startswith(".")]
        if files:
            return {"name": "models/", "status": OK,
                    "detail": "%d local model(s): %s" % (len(files), ", ".join(files[:3]))}
        return {"name": "models/", "status": WARN,
                "detail": "empty - local GGUF models go here (optional; cloud AI works without them)"}
    return {"name": "models/", "status": WARN, "detail": "missing - will be created (optional)"}


def check_telegram_config(environ=None):
    """Check optional local Telegram settings without importing the WebUI."""
    env = environ if environ is not None else os.environ
    enabled = str(env.get("VIRALCUTTER_TELEGRAM_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return {"name": "Telegram Control", "status": WARN,
                "detail": "disabled - no local polling will start"}
    token = str(env.get("VIRALCUTTER_TELEGRAM_BOT_TOKEN", "") or "").strip()
    raw_ids = str(env.get("VIRALCUTTER_TELEGRAM_CHAT_IDS", "") or "")
    values = [item for item in re.split(r"[\s,;]+", raw_ids.strip()) if item]
    chat_ids = {item for item in values if re.fullmatch(r"-?\d+", item)}
    if not token:
        return {"name": "Telegram Control", "status": WARN,
                "detail": "enabled but bot token is missing (value is never shown)"}
    if not chat_ids:
        return {"name": "Telegram Control", "status": WARN,
                "detail": "enabled but no valid allowlisted Chat ID is configured"}
    if not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{20,}", token):
        return {"name": "Telegram Control", "status": WARN,
                "detail": "enabled with a token whose format could not be validated"}
    return {"name": "Telegram Control", "status": OK,
            "detail": "ready; local long polling; %d allowlisted Chat ID(s)" % len(chat_ids)}

# --------------------------------------------------------------------------
# Repair actions
# --------------------------------------------------------------------------
def _run_pip(args):
    """Install packages through venv pip or uv pip when uv created a pip-less venv."""
    if _module_importable("pip"):
        cmd = [sys.executable, "-m", "pip", "install"] + args
    else:
        uv = shutil.which("uv")
        if not uv:
            return None
        cmd = [uv, "pip", "install", "--python", sys.executable] + args
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except Exception:
        return None


def _run_package_reinstall(dist, version):
    spec = "%s==%s" % (dist, version)
    if _module_importable("pip"):
        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--force-reinstall",
            "--no-deps",
            spec,
        ]
    else:
        uv = shutil.which("uv")
        if not uv:
            return None
        cmd = [
            uv,
            "pip",
            "install",
            "--python",
            sys.executable,
            "--reinstall-package",
            dist,
            "--no-deps",
            spec,
        ]
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except Exception:
        return None


def fix_broken_import(dist):
    if not _dist_installed(dist):
        return False, "%s metadata is missing; core dependency installation is required" % dist
    version = _version(dist)
    if version == "?":
        return False, "could not determine the installed %s version" % dist
    if _is_frozen():
        return False, "cannot reinstall %s inside the packaged exe" % dist
    proc = _run_package_reinstall(dist, version)
    if proc is None:
        return False, "pip and uv are unavailable for repairing %s" % dist
    if proc.returncode != 0:
        return False, "could not reinstall %s %s (installer exit %d)" % (dist, version, proc.returncode)
    module = _import_name(dist)
    for loaded in tuple(sys.modules):
        if loaded == module or loaded.startswith(module + "."):
            del sys.modules[loaded]
    importlib.invalidate_caches()
    if not _module_importable(module):
        return False, "reinstalled %s %s but import still fails (%s)" % (
            dist, version, _import_failure_detail(module),
        )
    return True, "reinstalled %s %s without changing other dependencies" % (dist, version)


def fix_google_genai_import():
    return fix_broken_import("google-genai")


def install_requirements(req_file, dry_run=False):
    """Install every line of a requirements file via pip. Returns (ok, detail)."""
    deps = _read_req_names(req_file)
    if not deps:
        return True, "nothing to install (%s empty)" % req_file
    if dry_run:
        return False, "would run: pip install -r %s" % req_file
    if _is_frozen():
        return False, "cannot pip-install inside the packaged exe (bundled deps are fixed)"
    proc = _run_pip(["-r", os.path.join(APP_ROOT, req_file)])
    if proc is None:
        return False, "pip unavailable (%s)" % sys.executable
    if proc.returncode == 0:
        return True, "installed %d packages from %s" % (len(deps), req_file)
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [""]
    return False, "pip failed (exit %d) on %s: %s" % (proc.returncode, req_file, tail[0][:200])


def fix_numpy_pin(dry_run=False):
    """Keep NumPy on the known-good 1.26.4 wheel used by WhisperX."""
    spec = _iu.find_spec("numpy")
    if spec is None:
        return True, "numpy not installed - nothing to pin"
    version = _version("numpy")
    parsed = None
    if version != "?":
        try:
            import packaging.version as _pv
            parsed = _pv.parse(version)
        except Exception:
            pass
    if parsed is not None and parsed < _pv.parse("2.0"):
        return True, "numpy %s OK (<2)" % version
    if dry_run:
        if version == "?" or parsed is None:
            return False, "would reinstall numpy==1.26.4 because the installed version cannot be verified"
        return False, "would downgrade numpy (currently %s)" % version
    if _is_frozen():
        return False, "cannot reinstall numpy inside the packaged exe"
    proc = _run_package_reinstall("numpy", "1.26.4")
    if proc is None:
        return False, "pip and uv are unavailable for repairing numpy"
    if proc.returncode != 0:
        return False, "could not reinstall numpy==1.26.4 (installer exit %d)" % proc.returncode
    importlib.invalidate_caches()
    repaired_version = _version("numpy")
    if repaired_version != "1.26.4":
        return False, "reinstalled numpy==1.26.4 but its version metadata is still unverifiable"
    return True, "numpy pinned to 1.26.4 (<2)"


def fix_tokenizers_pin(dry_run=False):
    """tokenizers 0.23.1 breaks the transformers import check (the installed
    transformers caps at <=0.23.0, and 0.23.0 was never released on PyPI).
    Force the known-good 0.22.2 wheel."""
    spec = _iu.find_spec("tokenizers")
    if spec is None:
        return True, "tokenizers not installed - nothing to pin"
    try:
        import packaging.version as _pv
        v = _version("tokenizers")
        if v != "?" and _pv.parse(v) < _pv.parse("0.23.1"):
            return True, "tokenizers %s OK (<0.23.1)" % v
    except Exception:
        return True, "cannot read tokenizers version"
    if dry_run:
        return False, "would downgrade tokenizers (currently %s)" % v
    if _is_frozen():
        return False, "cannot reinstall tokenizers inside the packaged exe"
    proc = _run_pip(["--force-reinstall", "--no-cache-dir", "--no-deps", "tokenizers==0.22.2"])
    if proc is not None and proc.returncode == 0:
        return True, "pinned tokenizers to 0.22.2 (<0.23.1)"
    return False, "could not downgrade tokenizers (exit %s)" % (proc.returncode if proc else "?")


def fix_transformers_pin(dry_run=False):
    """Keep Transformers aligned with the supported Hugging Face Hub 1.x stack."""
    spec = _iu.find_spec("transformers")
    if spec is None:
        return True, "transformers not installed - nothing to pin"
    try:
        import packaging.version as _pv
        v = _version("transformers")
        if v != "?" and _pv.parse("5.14.1") <= _pv.parse(v) < _pv.parse("5.16"):
            return True, "transformers %s OK (>=5.14.1,<5.16)" % v
    except Exception:
        return True, "cannot read transformers version"
    if dry_run:
        return False, "would reinstall the compatible Transformers 5.x stack (currently %s)" % v
    if _is_frozen():
        return False, "cannot reinstall transformers inside the packaged exe"
    proc = _run_pip([
        "--force-reinstall", "--no-cache-dir",
        "transformers>=5.14.1,<5.16",
        "huggingface-hub>=1.16.0,<2.0",
        "tokenizers>=0.22.2,<0.23.1",
        "numpy<2",
    ])
    if proc is not None and proc.returncode == 0:
        return True, "installed the compatible Transformers 5.x / Hub 1.x / tokenizers stack"
    return False, "could not install compatible Transformers stack (exit %s)" % (proc.returncode if proc else "?")


def fix_hub_pin(dry_run=False):
    """Restore the Hub 1.x range required by the Transformers 5.x stack."""
    spec = _iu.find_spec("huggingface_hub")
    if spec is None:
        return True, "huggingface-hub not installed - nothing to pin"
    try:
        import packaging.version as _pv
        v = _version("huggingface-hub")
        if v != "?" and _pv.parse("1.16.0") <= _pv.parse(v) < _pv.parse("2.0"):
            return True, "huggingface-hub %s OK (>=1.16.0,<2.0)" % v
    except Exception:
        return True, "cannot read huggingface-hub version"
    if dry_run:
        return False, "would reinstall the compatible Transformers 5.x / Hub 1.x stack (Hub currently %s)" % v
    if _is_frozen():
        return False, "cannot reinstall huggingface-hub inside the packaged exe"
    proc = _run_pip([
        "--force-reinstall", "--no-cache-dir",
        "transformers>=5.14.1,<5.16",
        "huggingface-hub>=1.16.0,<2.0",
        "tokenizers>=0.22.2,<0.23.1",
        "numpy<2",
    ])
    if proc is not None and proc.returncode == 0:
        return True, "installed the compatible Transformers 5.x / Hub 1.x / tokenizers stack"
    return False, "could not install compatible Hub stack (exit %s)" % (proc.returncode if proc else "?")


def fix_setuptools_pin(dry_run=False):
    """Keep the transcription environment on a patched setuptools release."""
    spec = _iu.find_spec("setuptools")
    if spec is None:
        return True, "setuptools not installed - the transcription stack installs its supported range"
    try:
        from packaging.version import Version
        v = _version("setuptools")
        if v != "?" and Version("84.1.0") <= Version(v) < Version("85.0.0"):
            return True, "setuptools %s OK (>=84.1.0,<85)" % v
    except Exception:
        return True, "cannot read setuptools version"
    if dry_run:
        return False, "would install patched setuptools>=84.1.0,<85"
    if _is_frozen():
        return False, "cannot reinstall setuptools inside the packaged exe"
    proc = _run_pip(["--upgrade", "--no-cache-dir", "setuptools>=84.1.0,<85"])
    if proc is not None and proc.returncode == 0:
        return True, "installed patched setuptools>=84.1.0,<85"
    return False, "could not install patched setuptools (exit %s)" % (proc.returncode if proc else "?")


def fix_ctranslate2_pin(dry_run=False):
    """Keep CTranslate2 inside WhisperX 3.3.1's supported dependency range."""
    spec = _iu.find_spec("ctranslate2")
    if spec is None:
        return True, "CTranslate2 not installed - the optional transcription stack installs its supported range"
    try:
        from packaging.version import Version
        version = _version("ctranslate2")
        if version != "?" and Version("4.4.0") <= Version(version) < Version("4.5.0"):
            return True, "CTranslate2 %s OK (>=4.4.0,<4.5.0)" % version
    except Exception:
        return True, "cannot read CTranslate2 version"
    if dry_run:
        return False, "would install ctranslate2>=4.4.0,<4.5.0"
    if _is_frozen():
        return False, "cannot reinstall CTranslate2 inside the packaged exe"
    proc = _run_pip(["--upgrade", "--no-cache-dir", "--no-deps", "ctranslate2>=4.4.0,<4.5.0"])
    if proc is not None and proc.returncode == 0:
        return True, "installed CTranslate2>=4.4.0,<4.5.0"
    return False, "could not install compatible CTranslate2 (exit %s)" % (proc.returncode if proc else "?")


def ensure_dirs():
    """Create the folders the app expects. Returns list of (label, ok, detail)."""
    out = []
    for rel in ["models", "fonts", "i18n/locale", "VIRALS", "logs"]:
        path = os.path.join(_app_dir(), rel)
        try:
            os.makedirs(path, exist_ok=True)
            out.append((rel, True, "created/ok"))
        except Exception as e:
            out.append((rel, False, str(e)))
    return out


def ensure_api_config():
    """Create api_config.json from template when missing/invalid. Returns (ok, detail)."""
    path = os.path.join(_app_dir(), "api_config.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                json.load(fh)
            return True, "api_config.json present and valid"
        except Exception:
            pass  # fall through: rewrite a valid template (backup broken file)
    backup = path + ".bak"
    try:
        if os.path.exists(path):
            shutil.copy2(path, backup)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(API_CONFIG_TEMPLATE, fh, indent=4, ensure_ascii=False)
        return True, "api_config.json %s (add your AI key in it)" % ("recreated" if os.path.exists(backup) else "created")
    except Exception as e:
        return False, "could not write api_config.json: %s" % e

# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def collect_checks():
    """Run every check. Returns (checks, critical_names, warn_names)."""
    checks = [check_python()] + check_ffmpeg_toolchain()
    bundled = check_bundled_binary("ffmpeg")
    if bundled:
        checks.append(bundled)
    checks += [check_disk_space(_app_dir()),
               check_writable(_app_dir(), "Working dir writable")]
    checks += [check_api_config(), check_assets(), check_safety_blocklist_freshness(),
               check_models_dir(), check_telegram_config()]

    # dependency checks per requirements file
    seen_dists = set()
    for req_file, (_label, _optional, _hint) in REQ_FILES.items():
        deps = _read_req_names(req_file)
        if not deps:
            continue
        for dist, _raw in deps:
            if dist in seen_dists:
                continue
            seen_dists.add(dist)
            checks.append(check_dependency(dist, _raw, critical=not _optional))
            for pin_dist, pin_rule, pin_note in PINNED_RULES:
                if dist == pin_dist:
                    pin = check_pin(dist, pin_rule, pin_note)
                    if pin:
                        checks.append(pin)

    critical_names = [c["name"] for c in checks if c["status"] == FAIL]
    warn_names = [c["name"] for c in checks if c["status"] == WARN]
    return checks, critical_names, warn_names


def _prompt(msg):
    try:
        return input(msg + " [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def repair(mode, checks, ensure_upload=False, websockets_prechecked=False):
    """Apply fixes. mode: 'fix' (interactive) or 'auto-fix' (non-interactive).
    Returns (fixed_report, remaining_critical, remaining_warn)."""
    fixed = []

    # 1) cheap fixes: dirs + config
    for rel, ok, detail in ensure_dirs():
        if ok:
            fixed.append({"name": "dir " + rel, "status": OK, "detail": detail})
    ok_cfg, cfg_detail = ensure_api_config()
    fixed.append({"name": "api_config.json", "status": OK if ok_cfg else FAIL, "detail": cfg_detail})

    # 2) Reinstall broken packages individually before resolving the core stack.
    broken_imports = {
        c["name"]
        for c in checks
        if c["status"] == FAIL
        and c["detail"].startswith("installed but import failed")
    }
    failed_import_repairs = set()
    for dist in sorted(broken_imports):
        ok, detail = fix_broken_import(dist)
        if not ok:
            failed_import_repairs.add(dist)
        fixed.append({"name": dist, "status": OK if ok else FAIL, "detail": detail})
    broken_websockets = any(
        c["name"] == "websockets" and c["status"] == FAIL
        for c in checks
    )
    if broken_websockets and not websockets_prechecked:
        ok = not _is_frozen() and ensure_websockets.repair()
        detail = "compatible version installed" if ok else "compatible version could not be installed"
        fixed.append({"name": "websockets", "status": OK if ok else FAIL, "detail": detail})

    # 3) pip installs (core always attempted; optional stacks only on request)
    fail_names = [
        c["name"] for c in checks
        if c["status"] == FAIL
        and (c["name"] not in broken_imports or c["name"] in failed_import_repairs)
        and not (broken_websockets and c["name"] == "websockets")
    ]
    if ensure_upload:
        upload_dists = {dist for dist, _raw in _read_req_names("requirements-upload.txt")}
        fail_names.extend(
            c["name"] for c in checks
            if c["name"] in upload_dists and c["status"] == WARN
        )
    req_fails = {}
    for req_file, (_label, _optional, _hint) in REQ_FILES.items():
        for dist, _raw in _read_req_names(req_file):
            if dist in fail_names and dist not in req_fails:
                req_fails[dist] = req_file

    for req_file, (label, optional, hint) in REQ_FILES.items():
        missing_in_file = [d for d, rf in req_fails.items() if rf == req_file]
        if not missing_in_file:
            continue
        do_install = not optional
        if optional and mode == "fix":
            print("\n[preflight] Optional stack: %s (%s)" % (label, hint))
            do_install = _prompt("  Install %s now?" % label)
        if optional and mode == "auto-fix":
            # OAuth dependencies are small and required by the WebUI channel-linking
            # controls when requested; keep the large transcription stack opt-in.
            do_install = bool(ensure_upload and req_file == "requirements-upload.txt")
        if not do_install:
            for d in missing_in_file:
                fixed.append({"name": d, "status": WARN, "detail": "optional - skipped"})
            continue
        ok, detail = install_requirements(req_file, dry_run=False)
        fixed.append({"name": "stack " + label, "status": OK if ok else FAIL, "detail": detail})

    # 3) version pins for the transcription stack and NumPy.
    for dist, rule, note in PINNED_RULES:
        pin = check_pin(dist, rule, note)
        if pin:
            if dist == "numpy":
                ok, detail = fix_numpy_pin()
            elif dist == "transformers":
                ok, detail = fix_transformers_pin()
            elif dist == "tokenizers":
                ok, detail = fix_tokenizers_pin()
            elif dist == "huggingface-hub":
                ok, detail = fix_hub_pin()
            elif dist == "setuptools":
                ok, detail = fix_setuptools_pin()
            elif dist == "ctranslate2":
                ok, detail = fix_ctranslate2_pin()
            else:
                continue
            fixed.append({"name": dist, "status": OK if ok else FAIL, "detail": detail})

    # 4) re-check everything
    _, remaining_crit, remaining_warn = collect_checks()
    return fixed, remaining_crit, remaining_warn


def _print_report(checks, title=None):
    title = title or APP_NAME + " Pre-flight"
    print("\n=== %s ===\n" % title)
    for c in checks:
        print("%s %-28s %s" % (ICONS.get(c["status"], "  "), c["name"], c["detail"]))
    print()


def print_ffmpeg_help():
    print("""
  ffmpeg/ffprobe are REQUIRED for video processing.
  Install them, then run this check again:
    - Windows:  winget install ffmpeg        (then reopen the terminal)
                or double-click packaging\\install_ffmpeg_windows.bat
    - Linux:    sudo apt install ffmpeg      (or dnf/pacman equivalent)
    - macOS:    brew install ffmpeg
  (The packaged OUSSAMA-Cutter.exe already bundles ffmpeg - no action needed.)""")


def run_preflight(mode="auto-fix", json_out=False, quiet=False, ensure_upload=False):
    """Entry point used by the app (main_improved / webui).

    mode: 'check' (read-only) | 'fix' (interactive) | 'auto-fix' (default)
    Returns exit code: 0 ready, 1 critical remains, 2 warnings only.
    """
    websockets_prechecked = mode in ("fix", "auto-fix") and not _is_frozen()
    if websockets_prechecked:
        ensure_websockets.repair()
    checks, critical, warn = collect_checks()

    upload_missing = False
    if ensure_upload:
        upload_dists = {dist for dist, _raw in _read_req_names("requirements-upload.txt")}
        upload_missing = any(
            c["name"] in upload_dists and c["status"] in (FAIL, WARN)
            for c in checks
        )
    if (critical or upload_missing) and mode in ("fix", "auto-fix"):
        if not quiet and not json_out:
            _print_report(checks, "Pre-flight: problems found, repairing")
        _, critical, warn = repair(
            mode,
            checks,
            ensure_upload=ensure_upload,
            websockets_prechecked=websockets_prechecked,
        )
        checks, critical, warn = collect_checks()

    if not quiet and not json_out:
        _print_report(checks)
        if critical:
            print("❌ %d critical problem(s) remain. Fix them before running %s." % (len(critical), APP_NAME))
            if "ffmpeg" in critical or "ffprobe" in critical:
                print_ffmpeg_help()
            return 1
        if warn:
            print("✅ System ready. (%d optional warning(s) - app will still work)" % len(warn))
        else:
            print("✅ System ready. Everything in place.")
        return 0

    if json_out:
        print(json.dumps({"exit": 1 if critical else (2 if warn else 0),
                          "critical": critical, "warnings": warn,
                          "checks": checks}, indent=2, ensure_ascii=False))
    return 1 if critical else (2 if warn else 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=APP_NAME + " pre-flight check & auto-repair",
                                     epilog="Exit: 0=ready, 1=critical problems remain, 2=warnings only")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="read-only check (no installs)")
    mode.add_argument("--fix", action="store_true", help="interactive repair")
    mode.add_argument("--auto-fix", action="store_true", help="non-interactive repair")
    mode.add_argument("--off", action="store_true", help="skip everything, exit 0")
    parser.add_argument("--ensure-upload", action="store_true",
                        help="auto-install YouTube OAuth/upload dependencies")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--quiet", action="store_true", help="no console output")
    args = parser.parse_args(argv)

    if args.off:
        return 0
    # Safe default: plain `python -m scripts.preflight` is READ-ONLY.
    # Auto-install only happens on explicit --auto-fix / --fix (the run
    # scripts and the app launcher pass it explicitly).
    if args.fix:
        m = "fix"
    elif args.auto_fix:
        m = "auto-fix"
    else:
        m = "check"
    return run_preflight(mode=m, json_out=args.json, quiet=args.quiet,
                         ensure_upload=args.ensure_upload)


if __name__ == "__main__":
    sys.exit(main())
