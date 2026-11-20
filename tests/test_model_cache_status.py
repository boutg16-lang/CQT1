# -*- coding: utf-8 -*-
"""An empty cache is normal; a stranded legacy cache is an actionable warning."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import preflight


def test_active_cache_is_ok(monkeypatch):
    monkeypatch.setattr(
        preflight, "_dir_size_gb", lambda path: 12.0 if path == "new" else 0.0
    )
    status, detail = preflight._model_cache_status("new", "old")
    assert status == preflight.OK
    assert "12.0 GB cached" in detail


def test_stranded_legacy_cache_warns_with_the_fix(monkeypatch):
    monkeypatch.setattr(
        preflight, "_dir_size_gb", lambda path: 8.0 if path == "old" else 0.0
    )
    status, detail = preflight._model_cache_status("new", "old")
    assert status == preflight.WARN
    assert "old cache has 8.0 GB" in detail
    assert "--migrate" in detail


def test_first_run_is_informational_not_a_failure(monkeypatch):
    monkeypatch.setattr(preflight, "_dir_size_gb", lambda path: 0.0)
    status, detail = preflight._model_cache_status("new", "old")
    assert status == preflight.OK
    assert "1.6 GB" in detail
