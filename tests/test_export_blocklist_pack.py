# -*- coding: utf-8 -*-
"""Tests for scripts/export_blocklist_pack.py — the canonical safety-pack export.

Every installation auto-updates its blocklist from the committed
``safety_blocklist.json`` (see scripts/safety_updater.py). A drift between that
pack and the built-in ``safety_filter.BLOCKLIST`` silently means users never
receive new safety terms — so the sync itself is a tested invariant.
"""

import datetime
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import export_blocklist_pack, safety_filter


def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["export_blocklist_pack", *argv])
    export_blocklist_pack.main()


def _expected_terms():
    return sorted((term, lang, severity, category)
                  for term, lang, severity, category in safety_filter.BLOCKLIST)


def _pack_terms(pack):
    return sorted((item["term"], item["lang"], item["severity"], item["category"])
                  for item in pack["terms"])


def test_export_writes_matching_pack(tmp_path, monkeypatch):
    out = tmp_path / "pack.json"
    _run(monkeypatch, "--version", "99", "--out", str(out))

    pack = json.loads(out.read_text(encoding="utf-8"))
    assert pack["version"] == 99
    assert pack["source"] == "ViralCutter built-in blocklist"
    # updated must be a real ISO date (clients parse it for staleness).
    datetime.date.fromisoformat(pack["updated"])
    assert len(pack["terms"]) == len(safety_filter.BLOCKLIST)
    assert _pack_terms(pack) == _expected_terms()


def test_export_requires_version(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["export_blocklist_pack", "--out", str(tmp_path / "x.json")])
    with pytest.raises(SystemExit) as exc:
        export_blocklist_pack.main()
    assert exc.value.code == 2


def test_committed_pack_is_in_sync_with_builtin_blocklist():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "safety_blocklist.json"), encoding="utf-8") as handle:
        pack = json.load(handle)

    assert isinstance(pack.get("version"), int) and pack["version"] > 0
    assert _pack_terms(pack) == _expected_terms(), (
        "safety_blocklist.json drifted from safety_filter.BLOCKLIST — run "
        "`python scripts/export_blocklist_pack.py --version N+1` and commit it, "
        "otherwise users never receive the new safety terms.")
