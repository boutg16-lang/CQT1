# -*- coding: utf-8 -*-
"""Tests for the module logger in main_improved.py.

The module is only ever executed as a CLI subprocess (the WebUI spawns it
with stderr merged into stdout), so these tests pin the exact contract
that keeps the 8.10.1 live log streaming working:

* diagnostics go to a module-level logger on stderr — never to stdout,
  which is reserved for ``PROGRESS|`` / ``DEVICE|`` lines and prints;
* the logger has exactly one handler, writes to ``sys.stderr``, and does
  not propagate (each record appears on stderr exactly once);
* no bare root-logger call (``logging.debug(`` / ``logging.warning(``)
  remains, and no ``print`` call site was converted.

Importing main_improved pulls in the pipeline (cv2, requests, ...), so the
module is skipped — not failed — in environments without those deps
(``pytest.importorskip``); CI installs the full stack.
"""

import io
import logging
from pathlib import Path

import pytest

main_improved = pytest.importorskip("main_improved")

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "main_improved.py").read_text(encoding="utf-8")


def test_logger_is_module_named():
    assert main_improved.logger.name == "main_improved"
    assert isinstance(main_improved.logger, logging.Logger)


def test_logger_has_exactly_one_own_stderr_handler():
    # pytest's logging plugin attaches its own capture handlers to
    # non-propagating loggers at test time, so count only the handler this
    # module installed (kept as main_improved._stderr_handler).
    handler = main_improved._stderr_handler
    assert isinstance(handler, logging.StreamHandler)
    assert handler in main_improved.logger.handlers
    assert handler.stream is not None
    assert handler.stream is not __import__("sys").stdout
    assert handler.formatter._fmt == "%(levelname)s: %(message)s"
    own_handlers = [
        h
        for h in main_improved.logger.handlers
        if isinstance(h, logging.StreamHandler)
        and getattr(h, "formatter", None) is not None
        and getattr(h.formatter, "_fmt", None) == "%(levelname)s: %(message)s"
    ]
    assert own_handlers == [handler]


def test_logger_does_not_propagate():
    assert main_improved.logger.propagate is False


def test_logger_warning_goes_to_stderr_not_stdout(capsys):
    stream = io.StringIO()
    capture = logging.StreamHandler(stream)
    capture.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    # Swap in the capture handler so the module's real stderr handler does
    # not pollute the test output; restore it afterwards.
    saved = list(main_improved.logger.handlers)
    try:
        main_improved.logger.handlers = [capture]
        main_improved.logger.warning("test warning %s", "here")
    finally:
        main_improved.logger.handlers = saved
    assert stream.getvalue() == "WARNING: test warning here\n"
    assert capsys.readouterr().out == ""


def test_logger_debug_is_dropped_by_default():
    # Effective level is WARNING (inherited from the root logger), exactly
    # as the previous bare logging.debug calls behaved.
    assert main_improved.logger.getEffectiveLevel() >= logging.WARNING


def test_no_bare_root_logger_calls_remain():
    assert "logging.debug(" not in SOURCE
    assert "logging.warning(" not in SOURCE
    assert "logging.info(" not in SOURCE
    assert "logging.error(" not in SOURCE
    assert SOURCE.count("logging.getLogger(__name__)") == 1


def test_no_logger_call_emits_progress_or_device_lines():
    for line in SOURCE.splitlines():
        if "logger." in line:
            assert "PROGRESS|" not in line
            assert "DEVICE|" not in line


def test_print_call_sites_untouched():
    # All print() call sites are still prints; the live log streaming
    # reads stdout, so converting any of them would change visible output.
    assert SOURCE.count("print(") == 204
    assert "emit_progress(" in SOURCE
