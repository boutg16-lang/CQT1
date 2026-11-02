import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _read_ascii(name):
    path = ROOT / name
    data = path.read_bytes()
    # Batch files are intentionally ASCII: this avoids Windows cmd parsing
    # corruption on systems using a legacy OEM code page.
    data.decode("ascii")
    return data.decode("ascii").replace("\r\n", "\n")


def _labels(text):
    return {match.group(1).strip().upper() for match in re.finditer(r"^:([A-Za-z0-9_]+)\s*$", text, re.MULTILINE)}


def test_windows_launchers_are_ascii_and_have_valid_gotos():
    for name in ("install_dependencies.bat", "run_webui.bat", "packaging/install_ffmpeg_windows.bat"):
        text = _read_ascii(name)
        labels = _labels(text)
        for target in re.findall(r"\bgoto\s+([A-Za-z0-9_]+)", text, flags=re.IGNORECASE):
            assert target.upper() in labels, f"{name}: missing goto label {target}"


def test_install_launcher_uses_project_drive_for_temp_and_runs_diagnostics():
    text = _read_ascii("install_dependencies.bat")
    assert 'set "TEMP=%CD%\\.installer-tmp"' in text
    assert 'set "TMP=%CD%\\.installer-tmp"' in text
    assert "scripts.windows_diagnostics" in text
    assert "requirements-upload.txt" in text
    assert "google_auth_oauthlib" in text


def test_installer_checks_for_locked_venv_before_mutating_it():
    text = _read_ascii("install_dependencies.bat")
    guard = 'scripts\\windows_installer_preflight.ps1'
    assert guard in text
    assert text.index(guard) < text.index('rmdir /s /q ".venv"')
    assert text.index(guard) < text.index("uv pip install")
    assert "(3,10) <= sys.version_info[:2] <= (3,12)" in text
    assert "[INFO] If an Access is denied error names a .pyd or .dll file" in text


def test_installer_preflight_matches_running_python_environment():
    text = (ROOT / "scripts" / "windows_installer_preflight.ps1").read_text(encoding="ascii")
    assert "Get-CimInstance -ClassName Win32_Process" in text
    assert "[System.IO.Path]::GetFullPath" in text
    assert "OrdinalIgnoreCase" in text
    assert "exit 2" in text


def test_installer_preflight_detects_python_hosts_loading_venv_modules():
    text = (ROOT / "scripts" / "windows_installer_preflight.ps1").read_text(encoding="ascii")
    assert "Get-Process -Name python,pythonw" in text
    assert "$process.Modules" in text
    assert "$venvPrefix" in text
    assert "moduleInspectionFailed" in text


@pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell")
def test_installer_preflight_blocks_current_python_interpreter():
    powershell = shutil.which("powershell")
    if not powershell:
        pytest.skip("Windows PowerShell is unavailable")
    script = ROOT / "scripts" / "windows_installer_preflight.ps1"
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-PythonPath",
            sys.executable,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "using modules from this project's Python environment" in result.stdout


def test_webui_launcher_is_non_destructive_and_has_warning_path():
    text = _read_ascii("run_webui.bat")
    assert "setlocal EnableExtensions" in text
    assert "cd /d \"%~dp0\"" in text
    assert "scripts/ensure_websockets.py --check" in text
    assert "scripts/ensure_websockets.py --repair" in text
    assert text.index("scripts/ensure_websockets.py --check") < text.index("scripts.preflight --auto-fix --ensure-upload")
    assert text.index("scripts/ensure_websockets.py --repair") < text.index("scripts.preflight --auto-fix --ensure-upload")
    assert 'if "%PREFLIGHT_EXIT%"=="2" goto START_WEBUI_WARNINGS' in text
    assert 'if exist "bin\\ffmpeg.exe" set "PATH=%CD%\\bin;%PATH%"' in text


def test_cli_launchers_repair_websockets_before_preflight():
    windows = (ROOT / "run.bat").read_text(encoding="utf-8")
    shell = (ROOT / "run.sh").read_text(encoding="utf-8")

    assert "ensure_websockets.py --check" in windows
    assert "ensure_websockets.py --repair" in windows
    assert windows.index("ensure_websockets.py --repair") < windows.index("scripts.preflight --auto-fix")
    assert "ensure_websockets.py --repair" in shell
    assert shell.index("ensure_websockets.py --repair") < shell.index("scripts.preflight --auto-fix")


def test_powershell_wrappers_keep_temp_on_project_drive_and_forward_args():
    for name in ("setup_on_d.ps1", "run_webui_on_d.ps1"):
        text = (ROOT / name).read_text(encoding="ascii")
        assert "$env:TEMP = $TempRoot" in text
        assert "$env:TMP = $TempRoot" in text
        assert "$env:UV_NO_CACHE = \"1\"" in text or name == "setup_on_d.ps1"
    text = (ROOT / "run_webui_on_d.ps1").read_text(encoding="ascii")
    assert "ValueFromRemainingArguments" in text
    assert "& $Launcher @Arguments" in text
