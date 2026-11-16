# -*- coding: utf-8 -*-
"""The source updater fast-forwards safely; it never merges or resets."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import source_updater


class FakeGit:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, args, cwd=None, timeout=120):
        self.calls.append(list(args))
        value = self.outputs.pop(0)
        if isinstance(value, tuple):
            code, text = value
        else:
            code, text = 0, value
        return SimpleNamespace(returncode=code, stdout=text, stderr="")


def test_not_a_git_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(source_updater, "_git", FakeGit([(1, "")]))
    result = source_updater.check(tmp_path)
    assert result["ok"] is False
    assert result["reason"] == "not_a_git_checkout"


def test_up_to_date(tmp_path, monkeypatch):
    fake = FakeGit(["true", "", "abc123", "abc123", "0 0", ""])
    monkeypatch.setattr(source_updater, "_git", fake)
    result = source_updater.check(tmp_path)
    assert result["ok"] is True
    assert result["update_available"] is False
    assert result["behind"] == 0


def test_behind_reports_dependency_changes(tmp_path, monkeypatch):
    fake = FakeGit([
        "true", "", "old111", "new222", "0 2", "",
        "requirements.txt\nREADME.md\nscripts/x.py\n",
    ])
    monkeypatch.setattr(source_updater, "_git", fake)
    result = source_updater.check(tmp_path)
    assert result["update_available"] is True
    assert result["behind"] == 2
    assert result["dependencies_changed"] is True
    assert result["dependency_files"] == ["requirements.txt"]


def test_apply_refuses_a_dirty_worktree(tmp_path, monkeypatch):
    fake = FakeGit(["true", "", "abc", "def", "0 1", " M scripts/x.py\n",
                    "scripts/x.py\n"])
    monkeypatch.setattr(source_updater, "_git", fake)
    result = source_updater.apply(tmp_path)
    assert result["applied"] is False
    assert result["reason"] == "dirty_worktree"
    assert not any(call[0] == "pull" for call in fake.calls)


def test_apply_fast_forwards(tmp_path, monkeypatch):
    fake = FakeGit([
        "true", "", "old111", "new222", "0 1", "",
        "scripts/x.py\n",
        "",            # git pull
        "new222",      # rev-parse after pull
    ])
    monkeypatch.setattr(source_updater, "_git", fake)
    result = source_updater.apply(tmp_path)
    assert result["applied"] is True
    assert result["reason"] == "updated"
    assert result["head"] == "new222"
    assert "Restart the app" in result["next_step"]
    assert any(call[:2] == ["pull", "--ff-only"] for call in fake.calls)
