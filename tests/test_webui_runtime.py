# -*- coding: utf-8 -*-
"""Direct tests for webui.runtime (is_frozen / app_dir / python_cmd).

Covers the frozen-exe vs source-run helpers in isolation. Deliberately
excluded: the build_command integration that consumes python_cmd — see
TestFrozenExeCommands in tests/test_pipeline.py. No subprocess is launched
and no real exe is involved; sys.frozen / sys.executable are monkeypatched.
"""

import os
import sys

from webui import runtime as rt


def test_is_frozen_false_when_attribute_absent(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert rt.is_frozen() is False


def test_is_frozen_true_when_attribute_set(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert rt.is_frozen() is True


def test_is_frozen_false_when_attribute_is_falsy(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert rt.is_frozen() is False


def test_is_frozen_coerces_truthy_value(monkeypatch):
    monkeypatch.setattr(sys, "frozen", "yes", raising=False)
    assert rt.is_frozen() is True


def test_app_dir_source_returns_repo_root(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    expected = os.path.dirname(os.path.dirname(os.path.abspath(rt.__file__)))
    assert rt.app_dir() == expected


def test_app_dir_source_ignores_executable_location(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", "/some/other/python", raising=False)
    expected = os.path.dirname(os.path.dirname(os.path.abspath(rt.__file__)))
    assert rt.app_dir() == expected


def test_app_dir_frozen_returns_exe_folder(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        sys, "executable", "/opt/OUSSAMA-Cutter/OUSSAMA-Cutter.exe", raising=False
    )
    assert rt.app_dir() == "/opt/OUSSAMA-Cutter"


def test_python_cmd_source_without_script(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert rt.python_cmd() == [sys.executable]


def test_python_cmd_source_with_script(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert rt.python_cmd("/repo/main_improved.py") == [
        sys.executable,
        "/repo/main_improved.py",
    ]


def test_python_cmd_source_empty_script_is_omitted(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert rt.python_cmd("") == [sys.executable]
    assert rt.python_cmd(None) == [sys.executable]


def test_python_cmd_frozen_ignores_script_path(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert rt.python_cmd("/repo/main_improved.py") == [sys.executable]


def test_python_cmd_frozen_without_script(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert rt.python_cmd() == [sys.executable]


def test_python_cmd_returns_fresh_list_each_call(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    first = rt.python_cmd("a.py")
    first.append("tampered")
    assert rt.python_cmd("a.py") == [sys.executable, "a.py"]
