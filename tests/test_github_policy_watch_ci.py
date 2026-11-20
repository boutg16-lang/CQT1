# -*- coding: utf-8 -*-
"""Direct tests for scripts/github_policy_watch_ci.py.

Covers _github_output (GITHUB_OUTPUT file vs legacy ::set-output print)
and main() with a stubbed check_policy_pages: changed / unchanged /
offline / checker-raises (error path, exit 1). No network.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import github_policy_watch_ci as gpwc  # noqa: E402


class TestGithubOutput:
    def test_writes_to_github_output_file(self, tmp_path, monkeypatch):
        out_path = tmp_path / "gh_out"
        monkeypatch.setenv("GITHUB_OUTPUT", str(out_path))
        gpwc._github_output("status", "changed")
        gpwc._github_output("changed", "yes")
        assert out_path.read_text(encoding="utf-8").splitlines() == [
            "status=changed",
            "changed=yes",
        ]

    def test_prints_legacy_set_output_without_env(self, monkeypatch, capsys):
        monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
        gpwc._github_output("status", "unchanged")
        assert capsys.readouterr().out == "::set-output name=status::unchanged\n"


def _stub_check(monkeypatch, result):
    if isinstance(result, Exception):

        def _raise():
            raise result

        monkeypatch.setattr(gpwc, "check_policy_pages", _raise)
    else:
        monkeypatch.setattr(gpwc, "check_policy_pages", lambda: result)


class TestMain:
    def test_changed_status(self, tmp_path, monkeypatch, capsys):
        _stub_check(
            monkeypatch,
            {
                "status": "changed",
                "changes": ["hate_speech"],
                "markers": {"hate_speech": ["m1"]},
            },
        )
        out_path = tmp_path / "gh_out"
        monkeypatch.setenv("GITHUB_OUTPUT", str(out_path))
        assert gpwc.main() == 0
        lines = out_path.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "status=changed"
        assert lines[1] == "changed=yes"
        assert json.loads(lines[2].split("=", 1)[1]) == ["hate_speech"]
        assert json.loads(lines[3].split("=", 1)[1]) == {"hate_speech": ["m1"]}
        assert (
            "status=changed changed=yes changes=hate_speech" in capsys.readouterr().out
        )

    def test_unchanged_status(self, monkeypatch, capsys):
        _stub_check(monkeypatch, {"status": "unchanged", "changes": [], "markers": {}})
        assert gpwc.main() == 0
        out = capsys.readouterr().out
        assert "::set-output name=changed::no" in out
        assert "status=unchanged changed=no changes=none" in out

    def test_offline_status_is_not_changed(self, monkeypatch, capsys):
        _stub_check(monkeypatch, {"status": "offline", "changes": [], "markers": {}})
        assert gpwc.main() == 0
        assert "::set-output name=changed::no" in capsys.readouterr().out

    def test_checker_raising_becomes_error_exit_one(self, monkeypatch, capsys):
        _stub_check(monkeypatch, RuntimeError("boom"))
        assert gpwc.main() == 1
        out = capsys.readouterr().out
        assert "::set-output name=status::error" in out
        assert "status=error" in out

    def test_missing_keys_default_gracefully(self, monkeypatch, capsys):
        # a result dict without changes/markers must not crash
        _stub_check(monkeypatch, {"status": "changed"})
        assert gpwc.main() == 0
        assert "changed=yes" in capsys.readouterr().out
