# -*- coding: utf-8 -*-
"""Version-consistency regression tests.

The v7.32 release shipped code+changelog at 7.32 while ``app_version.py``
(the single source of truth shown in the WebUI) still said 7.30 and
``pyproject.toml`` said 7.26. These tests pin the three canonical version
declarations together so a release can never desync them again.

v7.32.4: the same drift class was found in the three READMEs — their
"Current Version" lines still advertised 7.26.0 while the code was at
7.32.x. The README pin test below closes that hole.
"""
import re
from pathlib import Path

import app_version

ROOT = Path(__file__).resolve().parent.parent


def _core(version: str) -> str:
    """'7.32.0-pro' / '7.32.0+pro' -> '7.32.0'."""
    return re.split(r"[-+]", version, maxsplit=1)[0]


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    assert match, "pyproject.toml is missing a [project] version field"
    return match.group(1)


def _changelog_top_version() -> str:
    text = (ROOT / "changelog.md").read_text(encoding="utf-8")
    match = re.search(r"OUSSAMA Cutter (\d+\.\d+\.\d+)", text)
    assert match, "changelog.md has no 'OUSSAMA Cutter X.Y.Z' release entry"
    return match.group(1)


def test_app_version_matches_pyproject():
    assert _core(app_version.__version__) == _core(_pyproject_version())


def test_app_version_matches_changelog_top_entry():
    assert _core(app_version.__version__) == _changelog_top_version()


def test_version_and_alias_stay_in_sync():
    assert app_version.VERSION == app_version.__version__


# Each README advertises the running version in a human-language line.
# (marker text -> file). A stale line is how 7.26.0 survived six releases.
README_VERSION_MARKERS = {
    "README.md": "Current Version",
    "README_en.md": "Current Version",
    "README_ar.md": "الإصدار الحالي",
}


def test_readme_current_version_lines_match_app_version():
    core = _core(app_version.__version__)
    for filename, marker in README_VERSION_MARKERS.items():
        text = (ROOT / filename).read_text(encoding="utf-8")
        line = next((ln for ln in text.splitlines() if marker in ln), None)
        assert line, f"{filename} is missing its '{marker}' line"
        assert core in line, (
            f"{filename} advertises a stale version: {line.strip()!r} "
            f"(expected it to contain {core})"
        )


def test_windows_installer_version_matches_app_version():
    text = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    match = re.search(
        r'^\s*#define\s+MyAppVersion\s+"([^"]+)"',
        text,
        flags=re.MULTILINE,
    )
    assert match, "packaging/installer.iss is missing its default MyAppVersion"
    assert _core(app_version.__version__) == _core(match.group(1))




def test_transcription_torch_pair_matches_windows_installer():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r"(?ms)^transcribe\s*=\s*\[(.*?)^\]", pyproject)
    assert match, "pyproject.toml is missing its transcribe extra"

    def pinned_pair(lines):
        pair = {}
        for line in lines:
            requirement = line.split("#", 1)[0].strip().strip('\" ,')
            pin = re.fullmatch(r"(torch|torchaudio)==([0-9]+(?:\.[0-9]+)*)", requirement)
            if pin:
                pair[pin.group(1)] = pin.group(2)
        return pair

    expected = {"torch": "2.8.0", "torchaudio": "2.8.0"}
    assert pinned_pair(match.group(1).splitlines()) == expected
    requirements = (ROOT / "requirements-transcribe.txt").read_text(encoding="utf-8")
    assert pinned_pair(requirements.splitlines()) == expected
    installers = [
        (ROOT / "install_dependencies.bat").read_text(encoding="utf-8"),
        (ROOT / "install_dependencies_advanced_LocalLLM.bat").read_text(encoding="utf-8"),
    ]
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    for package in ("torch", "torchaudio"):
        locked = re.search(
            rf'(?ms)^\[\[package\]\]\nname = "{package}"\nversion = "([^"]+)"',
            lock,
        )
        assert locked and locked.group(1) == expected[package]

    for installer in installers:
        assert set(re.findall(r"(?:torch|torchaudio)==([0-9]+(?:\.[0-9]+)*)", installer)) == set(expected.values())
        assert set(re.findall(r"torchvision==([0-9]+(?:\.[0-9]+)*)", installer)) == {"0.23.0"}
        assert "https://download.pytorch.org/whl/cu126" in installer
        assert "https://download.pytorch.org/whl/cpu" in installer


def test_transcription_dependency_floors_match_project_and_requirements():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"(?ms)^transcribe\s*=\s*\[(.*?)^\]", pyproject)
    assert block, "pyproject.toml is missing the transcribe extra"
    project_requirements = re.findall(r'"([^\"]+)"', block.group(1))
    requirements_file = (ROOT / "requirements-transcribe.txt").read_text(encoding="utf-8").splitlines()

    def pinned_pair(lines):
        pair = {}
        for line in lines:
            line = line.partition("#")[0].strip().strip('",')
            match = re.fullmatch(r"(torch|torchaudio)==([^,\s]+)", line)
            if match:
                pair[match.group(1)] = match.group(2)
        return pair

    expected = {"torch": "2.8.0", "torchaudio": "2.8.0"}
    assert pinned_pair(project_requirements) == expected
    assert pinned_pair(requirements_file) == expected

    security_floors = {
        "lightning": "2.6.6",
        "pytorch-lightning": "2.6.6",
        "nltk": "3.10.4",
        "setuptools": "84.1.0",
    }
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    locked_versions = dict(re.findall(r'(?ms)^\[\[package\]\]\nname = "([^"]+)"\nversion = "([^"]+)"', lock))
    for package, minimum in security_floors.items():
        requirement = f"{package}>={minimum}"
        assert any(value.startswith(requirement) for value in project_requirements)
        assert any(value.startswith(requirement) for value in requirements_file)
        assert tuple(map(int, locked_versions[package].split("."))) >= tuple(map(int, minimum.split(".")))


def test_core_and_upload_security_floors_match_project_files():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    upload = re.search(r"(?ms)^upload\s*=\s*\[(.*?)^\]", pyproject)
    assert upload
    upload_requirements = re.findall(r'"([^\"]+)"', upload.group(1))
    upload_file = (ROOT / "requirements-upload.txt").read_text(encoding="utf-8").splitlines()
    floors = {"opentelemetry-api": "1.45.1", "oauthlib": "4.0.0"}

    for package, minimum in floors.items():
        requirement = f"{package}>={minimum}"
        assert requirement in upload_requirements
        assert requirement in upload_file
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    locked_versions = dict(re.findall(r'(?ms)^\[\[package\]\]\nname = "([^"]+)"\nversion = "([^"]+)"', lock))
    for package, minimum in floors.items():
        assert tuple(map(int, locked_versions[package].split("."))) >= tuple(map(int, minimum.split(".")))
