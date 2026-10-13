# -*- coding: utf-8 -*-
"""Release-pipeline consistency guard (GitHub issue #25).

The v7.18 rebrand renamed the PyInstaller output to ``OUSSAMA-Cutter.exe``
while ``.github/workflows/build-exe.yml`` kept smoke-testing, uploading and
releasing ``dist/ViralCutter.exe``. Every tagged Windows build then polled for
a file that never existed and burned the whole job timeout — with no
diagnostics in the logs.

The temporary fix is a CI-only shim in ``packaging/viralcutter.spec`` that also
emits the legacy name under GitHub Actions. That shim and the workflow are
coupled, and the coupling is invisible: editing one without the other silently
breaks releases again.

These tests pin the contract:

* if the workflow defines ``EXE_NAME`` → it must equal the spec's ``EXE(...)``
  name (plus ``.exe``), and the release must publish a ``checksums.txt``
  manifest, because ``scripts/auto_updater.py`` is fail-closed;
* if the workflow still references the legacy name → the spec must keep
  emitting it under GitHub Actions.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "packaging" / "viralcutter.spec"
WORKFLOW = ROOT / ".github" / "workflows" / "build-exe.yml"
LEGACY_EXE = "ViralCutter.exe"


def _spec_exe_name() -> str:
    """The `name=` argument of the EXE(...) call that builds the binary."""
    text = SPEC.read_text(encoding="utf-8")
    marker = text.index("EXE(")
    match = re.search(r'name\s*=\s*["\']([^"\']+)["\']', text[marker:])
    assert match, "packaging/viralcutter.spec has no name= on its EXE() call"
    return match.group(1)


def _workflow_exe_name():
    """The workflow's EXE_NAME env value, or None for the legacy workflow."""
    text = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(r"^\s*EXE_NAME:\s*(\S+)\s*$", text, flags=re.MULTILINE)
    return match.group(1) if match else None


def test_workflow_and_spec_agree_on_the_binary_name():
    spec_name = _spec_exe_name()
    workflow_name = _workflow_exe_name()
    workflow_text = WORKFLOW.read_text(encoding="utf-8")

    if workflow_name is None:
        # Legacy workflow: it still looks for dist/ViralCutter.exe, so the
        # spec MUST keep emitting that name under GitHub Actions.
        assert LEGACY_EXE in workflow_text, (
            "build-exe.yml no longer references the legacy exe name and has no "
            "EXE_NAME — the workflow/spec contract is undefined (issue #25)"
        )
        spec_text = SPEC.read_text(encoding="utf-8")
        assert "GITHUB_ACTIONS" in spec_text and LEGACY_EXE in spec_text, (
            "build-exe.yml still consumes dist/ViralCutter.exe but the CI shim "
            "that emits it was removed from packaging/viralcutter.spec; every "
            "tagged build would hang again (issue #25)"
        )
    else:
        assert workflow_name == f"{spec_name}.exe", (
            f"build-exe.yml EXE_NAME={workflow_name!r} does not match the spec "
            f"name {spec_name!r} (expected {spec_name}.exe) — see issue #25"
        )


def test_release_publishes_a_checksum_manifest():
    """auto_updater refuses any release without checksums.txt / SHA256SUMS."""
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    if _workflow_exe_name() is None:
        # The legacy workflow cannot publish a manifest; the shim is a stop-gap
        # and the real fix is documented in docs/ISSUE_25_WORKFLOW_FIX_AR.md.
        assert "checksums" not in workflow_text.lower()
        return
    assert "checksums.txt" in workflow_text, (
        "the release must attach checksums.txt or scripts/auto_updater.py "
        "refuses to install the update (fail-closed by design)"
    )


def test_ci_shim_is_marked_temporary():
    """The shim must stay discoverable so it gets removed with the fix."""
    spec_text = SPEC.read_text(encoding="utf-8")
    if LEGACY_EXE not in spec_text:
        return
    assert "issue #25" in spec_text.lower(), (
        "the CI compatibility shim must reference issue #25 so it is removed "
        "once the workflow is updated"
    )
