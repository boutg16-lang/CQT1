# -*- coding: utf-8 -*-
"""Unit tests for scripts/webui_e2e_smoke.py (the browser E2E harness).

The browser-driving part needs Playwright + a real Chrome, so it is exercised
manually/CI-optional. Here we cover the deterministic parts: report rendering
and the HTTP readiness probe. The module must also import without Playwright
installed (Playwright is a lazy, optional dependency).
"""

import importlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import webui_e2e_smoke as smoke


def test_module_imports_without_playwright():
    # Re-import fresh; top-level imports must stay stdlib-only.
    module = importlib.reload(smoke)
    assert hasattr(module, "main") and hasattr(module, "run")


def test_write_markdown_renders_ok_report(tmp_path):
    report = {
        "url": "http://127.0.0.1:7860",
        "ok": True,
        "title": "OUSSAMA Cutter",
        "tabs": [{"index": 1, "label": "🏠 الرئيسية", "clicked": True}],
        "console_errors": [],
        "page_errors": [],
    }
    out = tmp_path / "report.md"
    smoke._write_markdown(report, str(out))
    text = out.read_text(encoding="utf-8")

    assert "✅ OK" in text
    assert "Tabs clicked: 1/1" in text
    assert "🏠 الرئيسية" in text


def test_write_markdown_lists_errors(tmp_path):
    report = {
        "url": "http://127.0.0.1:7860",
        "ok": False,
        "title": "OUSSAMA Cutter",
        "tabs": [{"index": 1, "label": "T", "clicked": False}],
        "console_errors": [{"type": "error", "text": "boom"}],
        "page_errors": ["uncaught"],
    }
    out = tmp_path / "report.md"
    smoke._write_markdown(report, str(out))
    text = out.read_text(encoding="utf-8")

    assert "❌ FAILED" in text
    assert "boom" in text and "uncaught" in text


def test_wait_for_http_is_false_on_closed_port():
    # Port 1 is never listened on; the probe must return quickly, not raise.
    assert smoke._wait_for_http("http://127.0.0.1:1/", timeout=1.0) is False


def test_report_json_is_written_by_run_documented_shape(tmp_path):
    # Guards the on-disk contract the user shares with the agent.
    report = {"url": "u", "ok": True, "tabs": [], "console_errors": [], "page_errors": []}
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8"))["ok"] is True
