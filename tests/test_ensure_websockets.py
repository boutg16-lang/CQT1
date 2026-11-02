import re
from pathlib import Path

from scripts import ensure_websockets

ROOT = Path(__file__).resolve().parents[1]


class Result:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


def test_compatibility_range_accepts_16_x_and_rejects_incompatible_versions():
    assert ensure_websockets.is_compatible_version("16.1.1")
    assert ensure_websockets.is_compatible_version("16.1.2")
    assert not ensure_websockets.is_compatible_version("16.1.0")
    assert not ensure_websockets.is_compatible_version("17.0.0")
    assert not ensure_websockets.is_compatible_version("17.4")
    assert not ensure_websockets.is_compatible_version(None)


def test_compatibility_range_matches_requirements_pyproject_and_lock():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    match = re.search(r'(?ms)^\[\[package\]\]\nname = "websockets"\nversion = "([^"]+)"', lock)

    assert ensure_websockets.COMPATIBILITY_SPEC in requirements
    assert '"{}"'.format(ensure_websockets.COMPATIBILITY_SPEC) in pyproject
    assert match and ensure_websockets.is_compatible_version(match.group(1))


def test_compatible_version_does_not_trigger_repair(monkeypatch):
    monkeypatch.setattr(ensure_websockets, "installed_version", lambda: "16.1.2")
    monkeypatch.setattr(ensure_websockets, "has_connection_closed_export", lambda: True)
    monkeypatch.setattr(
        ensure_websockets.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected install")),
    )

    assert ensure_websockets.needs_repair() is False
    assert ensure_websockets.repair() is True


def test_incompatible_version_is_detected_without_importing_it(monkeypatch):
    monkeypatch.setattr(ensure_websockets, "installed_version", lambda: "17.4")
    monkeypatch.setattr(
        ensure_websockets,
        "has_connection_closed_export",
        lambda: (_ for _ in ()).throw(AssertionError("incompatible package must not load")),
    )

    assert ensure_websockets.needs_repair() is True


def test_missing_connection_closed_export_requires_repair(monkeypatch):
    monkeypatch.setattr(ensure_websockets, "installed_version", lambda: "16.1.1")
    monkeypatch.setattr(ensure_websockets, "has_connection_closed_export", lambda: False)

    assert ensure_websockets.needs_repair() is True


def test_repair_uses_uv_without_upgrading_other_packages(monkeypatch):
    versions = iter(["17.4", "16.1.2"])
    calls = []
    monkeypatch.setattr(ensure_websockets, "installed_version", lambda: next(versions))
    monkeypatch.setattr(ensure_websockets, "has_connection_closed_export", lambda: True)
    monkeypatch.setattr(ensure_websockets.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(
        ensure_websockets.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)) or Result(),
    )
    monkeypatch.setattr(ensure_websockets.importlib, "invalidate_caches", lambda: None)

    assert ensure_websockets.repair() is True
    command, kwargs = calls[0]
    assert command[1:3] == ["pip", "install"]
    assert "--reinstall-package" in command
    assert "--no-deps" in command
    assert command[-1] == ensure_websockets.COMPATIBILITY_SPEC
    assert kwargs["timeout"] == 300


def test_repair_falls_back_to_pip_without_uv(monkeypatch):
    versions = iter(["17.4", "16.1.2"])
    calls = []
    monkeypatch.setattr(ensure_websockets, "installed_version", lambda: next(versions))
    monkeypatch.setattr(ensure_websockets, "has_connection_closed_export", lambda: True)
    monkeypatch.setattr(ensure_websockets.shutil, "which", lambda _: None)
    monkeypatch.setattr(ensure_websockets.os.path, "isfile", lambda _: False)
    monkeypatch.setattr(
        ensure_websockets.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or Result(),
    )
    monkeypatch.setattr(ensure_websockets.importlib, "invalidate_caches", lambda: None)

    assert ensure_websockets.repair() is True
    command = calls[0]
    assert command[1:5] == ["-m", "pip", "install", "--force-reinstall"]
    assert "--no-deps" in command
    assert command[-1] == ensure_websockets.COMPATIBILITY_SPEC


def test_repair_finds_uv_in_windows_user_install_path(monkeypatch, tmp_path):
    versions = iter(["17.4", "16.1.2"])
    calls = []
    expected_uv = tmp_path / ".local" / "bin" / "uv.exe"
    monkeypatch.setattr(ensure_websockets.sys, "platform", "win32")
    monkeypatch.setattr(ensure_websockets, "installed_version", lambda: next(versions))
    monkeypatch.setattr(ensure_websockets, "has_connection_closed_export", lambda: True)
    monkeypatch.setattr(ensure_websockets.shutil, "which", lambda _: None)
    monkeypatch.setattr(ensure_websockets.os.path, "expanduser", lambda _: str(tmp_path))
    monkeypatch.setattr(ensure_websockets.os.path, "isfile", lambda path: path == str(expected_uv))
    monkeypatch.setattr(
        ensure_websockets.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or Result(),
    )
    monkeypatch.setattr(ensure_websockets.importlib, "invalidate_caches", lambda: None)

    assert ensure_websockets.repair() is True
    assert calls[0][0] == str(expected_uv)


def test_repair_returns_false_when_package_install_fails(monkeypatch):
    monkeypatch.setattr(ensure_websockets, "installed_version", lambda: "17.4")
    monkeypatch.setattr(ensure_websockets.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(ensure_websockets.subprocess, "run", lambda *args, **kwargs: Result(1))

    assert ensure_websockets.repair() is False
