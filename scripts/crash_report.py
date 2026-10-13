# -*- coding: utf-8 -*-
"""
Crash Report — opt-in, privacy-respecting error reporting.

Roadmap item 4.5 ("تقارير أخطاء تحترم الخصوصية"). Lets the maintainer see
which failures are common without leaking the user's data:

    * strips every absolute path (replaces with <PROJECT>/<HOME>/...),
    * never includes transcripts, subtitles, titles or filenames,
    * collects only: error class, first 300 chars of the message,
      Python version, OS, whether CUDA was used, and the failed stage,
    * sends nothing unless the user opted in (VIRALCUTTER_CRASH_REPORT=1
      or --crash-report on), default endpoint: none — set
      VIRALCUTTER_CRASH_ENDPOINT to enable.

Fully local-friendly: failures are logged to crash_report.log even when
the user did not opt in to sending them.
"""

import json
import os
import platform
import re
import time
import urllib.request

DEFAULT_ENDPOINT = ""  # set VIRALCUTTER_CRASH_ENDPOINT to a collector URL

# Common media/artifact extensions used to bound a Windows path that may
# legitimately contain spaces ("C:\\Users\\Bob\\My Videos\\clip.mp4").
_WIN_MEDIA_EXTS = (
    "mp4|mov|mkv|avi|webm|m4v|wmv|flv|wav|mp3|m4a|aac|flac|ogg|opus|"
    "srt|ass|vtt|txt|log|json|csv|jpg|jpeg|png|gif|webp|bmp|pdf|"
    "doc|docx|xls|xlsx|ppt|pptx"
)

# Credentials must never leave the machine in a crash report.
_CREDENTIAL_PATTERNS = (
    # Authorization: Bearer <token> / Authorization: Basic <blob>
    (re.compile(r"(?i)\b(authorization)\s*[:=]\s*[\"']?(?:bearer|basic)\s+[^\s,\"';]+"),
     r"\1=<REDACTED>"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]+=*"), "Bearer <REDACTED>"),
    # JSON Web Tokens
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "<JWT>"),
    # Google API keys / OpenAI-style secret keys
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), "<REDACTED>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"), "<REDACTED>"),
    # key=/token=/secret=/password= query params and assignments
    (re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
        r"token|secret|password|passwd|pwd|auth|key)\s*[=:]\s*[\"']?([^\"'\s&,;]+)"),
     r"\1=<REDACTED>"),
)


def _sanitize(message, max_len=300):
    """Strip absolute paths and credentials from a message."""
    text = str(message)
    home = os.path.expanduser("~")
    text = text.replace(home, "<HOME>")
    # Credentials first: path redaction must not leave fragments of a token.
    for pattern, replacement in _CREDENTIAL_PATTERNS:
        text = pattern.sub(replacement, text)
    # Windows paths may contain spaces; bound them by a known media extension,
    # then fall back to end-of-line for extensionless paths.
    text = re.sub(
        r"[A-Za-z]:\\[^\r\n'\"]*?\.(?:" + _WIN_MEDIA_EXTS + r")\b",
        "<PATH>", text, flags=re.IGNORECASE)
    text = re.sub(r"[A-Za-z]:\\(?:[^\r\n'\"\\]+\\)*[^\r\n'\"]+", "<PATH>", text)
    text = re.sub(r"(?<![A-Za-z0-9])/(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]+", "<PATH>", text)
    return text[:max_len]


def _collect(stage="unknown", exc=None):
    import traceback
    tb = ""
    if exc is not None:
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        tb = "".join(tb)
    cuda = False
    try:
        import torch
        cuda = bool(torch.cuda.is_available())
    except Exception:
        pass
    return {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "stage": stage,
        "error": _sanitize(getattr(exc, "repr", "") or str(exc)) if exc else None,
        "error_type": type(exc).__name__ if exc else None,
        "python": platform.python_version(),
        "os": platform.system() + " " + platform.release(),
        "cuda": cuda,
        "version": None,
    }


def log_crash(stage="unknown", exc=None, log_path="crash_report.log"):
    """Write a sanitized crash entry to crash_report.log (always local)."""
    entry = _collect(stage, exc)
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return entry


def send_crash(entry, endpoint=None):
    """POST the sanitized entry to the collector. Returns bool. Never raises."""
    endpoint = endpoint or os.getenv("VIRALCUTTER_CRASH_ENDPOINT", DEFAULT_ENDPOINT)
    if not endpoint:
        return False
    try:
        req = urllib.request.Request(
            endpoint, data=json.dumps(entry).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status < 400
    except Exception:
        return False


def report(stage="unknown", exc=None, log_path="crash_report.log", endpoint=None):
    """Log locally; send only when the user opted in. Returns the entry."""
    entry = log_crash(stage, exc, log_path)
    opt_in = os.getenv("VIRALCUTTER_CRASH_REPORT", "").strip().lower() in {"1", "true", "yes", "on"}
    if opt_in:
        send_crash(entry, endpoint)
    return entry


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ViralCutter crash reporter.")
    parser.add_argument("--stage", default="cli")
    parser.add_argument("--message", default="test crash")
    parser.add_argument("--send", action="store_true", help="send to endpoint if configured")
    args = parser.parse_args()
    if args.send:
        os.environ.setdefault("VIRALCUTTER_CRASH_REPORT", "1")
    entry = report(args.stage, ValueError(args.message))
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
