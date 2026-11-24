"""Write the WebUI's console output to a log file as well as the console.

The WebUI used to print everything to stdout only, so when the process
died (for example a Windows native crash, which Python cannot catch) the
whole output vanished with the terminal window and there was no report
to diagnose from. Installing this tee at startup keeps a copy in
``logs/webui.log`` under the project root, so the next stop leaves a
report behind.

Set ``VIRALCUTTER_DISABLE_WEBUI_LOGFILE=1`` to turn it off.
"""

from __future__ import annotations

import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

LOG_FILENAME = "webui.log"
_DISABLE_VALUES = ("1", "true", "yes", "on")


def _disabled() -> bool:
    return (
        os.environ.get("VIRALCUTTER_DISABLE_WEBUI_LOGFILE", "").strip().lower()
        in _DISABLE_VALUES
    )


class _Tee:
    """A stream that writes to the console stream and to a log file."""

    def __init__(self, stream, log_file, lock):
        self._stream = stream
        self._log_file = log_file
        self._lock = lock

    def write(self, data):
        with self._lock:
            try:
                self._stream.write(data)
            except Exception:
                pass
            try:
                self._log_file.write(data)
                self._log_file.flush()
            except Exception:
                pass
        return len(data)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        with self._lock:
            try:
                self._stream.flush()
            except Exception:
                pass
            try:
                self._log_file.flush()
            except Exception:
                pass

    def fileno(self):
        return self._stream.fileno()

    def isatty(self):
        try:
            return self._stream.isatty()
        except Exception:
            return False

    @property
    def encoding(self):
        return getattr(self._stream, "encoding", "utf-8")

    @property
    def closed(self):
        return getattr(self._stream, "closed", False)


def install_webui_logfile(logs_dir=None):
    """Start teeing the WebUI's stdout/stderr to ``logs/webui.log``.

    Returns the log path, or None when logging is disabled or cannot start.
    Never raises: a logging failure must not prevent the WebUI from booting.
    """
    if _disabled():
        return None
    try:
        if logs_dir is not None:
            root = Path(logs_dir)
        else:
            root = Path(__file__).resolve().parent.parent / "logs"
        root.mkdir(parents=True, exist_ok=True)
        log_path = root / LOG_FILENAME
        log_file = open(log_path, "a", encoding="utf-8", errors="replace")
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        header = "\n--- OUSSAMA Cutter WebUI started {} ---\n".format(stamp)
        log_file.write(header)
        log_file.flush()
        lock = threading.Lock()
        sys.stdout = _Tee(sys.stdout, log_file, lock)
        sys.stderr = _Tee(sys.stderr, log_file, lock)
        print("[webui] console log: {}".format(log_path))
        return log_path
    except Exception as exc:
        try:
            print("[webui] could not start console log: {}".format(exc))
        except Exception:
            pass
        return None
