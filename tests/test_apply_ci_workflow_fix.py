# -*- coding: utf-8 -*-
"""Tests for the one-command CI workflow applier (issues #25/#26).

The applier is the owner-side half of the fix: the AI agent cannot push
``.github/workflows/**`` (no ``workflows`` permission — verified via git push,
the Contents API and the Git Data API), so the owner runs one command. These
tests prove the command does exactly the right thing, in the right order, and
is safe to run twice.
"""
import shutil
from pathlib import Path

import pytest

from scripts import apply_ci_workflow_fix as applier

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def fake_repo(tmp_path):
    """A minimal repo copy containing only what the applier touches."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    (repo / applier.SOURCE_DIR).mkdir(parents=True, exist_ok=True)
    (repo / applier.WORKFLOW_DIR).mkdir(parents=True, exist_ok=True)
    for name in applier.FILES:
        shutil.copy(ROOT / applier.SOURCE_DIR / name, repo / applier.SOURCE_DIR / name)
    # legacy workflow content (pre-fix) + the spec with its CI shim
    (repo / applier.WORKFLOW_DIR / "build-exe.yml").write_text(
        "name: Build Windows EXE\n"
        "      - name: Smoke-test the exe (WebUI must boot)\n"
        "          ./dist/ViralCutter.exe --webui\n",
        encoding="utf-8")
    (repo / applier.WORKFLOW_DIR / "ci.yml").write_text(
        "name: CI\njobs:\n  test:\n    steps:\n      - run: pytest -v\n",
        encoding="utf-8")
    (repo / applier.SPEC_PATH).write_text(
        '# -*- mode: python ; coding: utf-8 -*-\n'
        'exe = EXE(\n    name="OUSSAMA-Cutter",\n)\n\n'
        '# --- CI compatibility shim (temporary see issue #25) ---\n'
        'import shutil as _shutil\n'
        'if _os.environ.get("GITHUB_ACTIONS") == "true":\n'
        '    _shutil.copyfile("dist/OUSSAMA-Cutter.exe", "dist/ViralCutter.exe")\n',
        encoding="utf-8")
    return repo


# ---------------------------------------------------------------------------
# State inspection
# ---------------------------------------------------------------------------

def test_inspect_reports_the_legacy_state(fake_repo):
    state = applier.inspect(str(fake_repo))
    assert state["applied"] is False
    assert state["files"]["build-exe.yml"]["needs_write"] is True
    assert state["spec"]["has_shim"] is True
    assert state["spec"]["exe_name"] == "OUSSAMA-Cutter"


def test_inspect_flags_a_missing_packaged_source(fake_repo):
    (fake_repo / applier.SOURCE_DIR / "ci.yml").unlink()
    state = applier.inspect(str(fake_repo))
    assert state["files"]["ci.yml"]["source_exists"] is False
    assert state["files"]["ci.yml"]["needs_write"] is False  # nothing to write


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------

def test_apply_writes_both_workflows_and_drops_the_shim(fake_repo):
    result = applier.apply_changes(str(fake_repo))

    assert len(result["written"]) == 2
    assert result["shim_removed"] is True

    build_exe = (fake_repo / applier.WORKFLOW_DIR / "build-exe.yml").read_text(encoding="utf-8")
    assert "EXE_NAME: OUSSAMA-Cutter.exe" in build_exe
    assert "checksums.txt" in build_exe
    # the legacy name only survives in the historical comment, not as a path
    assert "./dist/ViralCutter.exe" not in build_exe

    spec = (fake_repo / applier.SPEC_PATH).read_text(encoding="utf-8")
    assert "CI compatibility shim" not in spec
    assert 'name="OUSSAMA-Cutter"' in spec  # the EXE block itself is untouched


def test_applied_state_is_idempotent(fake_repo):
    applier.apply_changes(str(fake_repo))
    assert applier.inspect(str(fake_repo))["applied"] is True

    second = applier.apply_changes(str(fake_repo))
    assert second["written"] == []
    assert second["shim_removed"] is False


def test_written_files_use_lf_endings(fake_repo):
    applier.apply_changes(str(fake_repo))
    for name in applier.FILES:
        raw = (fake_repo / applier.WORKFLOW_DIR / name).read_bytes()
        assert b"\r\n" not in raw, name


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_validate_passes_after_apply(fake_repo):
    applier.apply_changes(str(fake_repo))
    assert applier.validate(str(fake_repo)) == []


def test_validate_rejects_files_without_the_fix(fake_repo):
    problems = applier.validate(str(fake_repo))
    assert any("EXE_NAME" in problem for problem in problems)
    assert any("checksums.txt" in problem for problem in problems)
    assert any("CI shim" in problem for problem in problems)


def test_validate_catches_a_spec_name_mismatch(fake_repo):
    applier.apply_changes(str(fake_repo))
    spec = fake_repo / applier.SPEC_PATH
    spec.write_text(spec.read_text(encoding="utf-8").replace(
        'name="OUSSAMA-Cutter"', 'name="Totally-Different"'), encoding="utf-8")
    problems = applier.validate(str(fake_repo))
    assert any("does not match" in problem for problem in problems)


def test_validate_flags_a_backslash_line_ending_regression(fake_repo):
    applier.apply_changes(str(fake_repo))
    target = fake_repo / applier.WORKFLOW_DIR / "build-exe.yml"
    target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))
    # validation is about content; CRLF is caught by the LF test, but the
    # file must still be considered valid YAML and match the spec name
    assert applier.validate(str(fake_repo)) == []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_dry_run_changes_nothing(fake_repo, capsys):
    assert applier.main(["--root", str(fake_repo)]) == 0
    out = capsys.readouterr().out
    assert "تجربة جافة" in out
    assert applier.inspect(str(fake_repo))["applied"] is False


def test_cli_apply_is_a_success(fake_repo, capsys):
    assert applier.main(["--root", str(fake_repo), "--apply"]) == 0
    assert applier.inspect(str(fake_repo))["applied"] is True
    assert "أو شغّل نفس الأمر مع --push" in capsys.readouterr().out


def test_cli_reports_already_applied(fake_repo, capsys):
    applier.apply_changes(str(fake_repo))
    assert applier.main(["--root", str(fake_repo)]) == 0
    assert "مُطبَّق بالفعل" in capsys.readouterr().out


def test_cli_push_runs_git_commands(fake_repo, monkeypatch):
    calls = []

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_git(root, *args):
        calls.append(args)
        return _Result()

    monkeypatch.setattr(applier, "_git", fake_git)
    assert applier.main(["--root", str(fake_repo), "--apply", "--push"]) == 0
    assert calls[0][0] == "add"
    assert calls[1][0] == "commit"
    assert calls[2][0] == "push"


def test_cli_push_surfaces_a_git_failure(fake_repo, monkeypatch):
    class _Result:
        def __init__(self, rc):
            self.returncode = rc
            self.stdout = "boom"
            self.stderr = "boom"

    monkeypatch.setattr(applier, "_git", lambda root, *args: _Result(1))
    assert applier.main(["--root", str(fake_repo), "--apply", "--push"]) == 2


# ---------------------------------------------------------------------------
# The packaged sources must match the documented patch
# ---------------------------------------------------------------------------

def test_packaged_workflows_are_the_fixed_ones():
    """The shipped data files must carry the fix (not the legacy workflow)."""
    build_exe = (ROOT / applier.SOURCE_DIR / "build-exe.yml").read_text(encoding="utf-8")
    ci = (ROOT / applier.SOURCE_DIR / "ci.yml").read_text(encoding="utf-8")
    assert "EXE_NAME: OUSSAMA-Cutter.exe" in build_exe
    assert build_exe.index("Create / update GitHub Release") < \
        build_exe.index("Upload exe as workflow artifact")
    assert build_exe.count("retention-days: 7") == 1
    assert ci.index("Run unit tests") < ci.index("Upload build and compliance reports")
    assert "retention-days: 7" in ci


def test_packaged_workflows_match_the_documented_patch():
    """`docs/ISSUE_25_workflow_fix.patch` must stay in sync with the data files."""
    patch = (ROOT / "docs" / "ISSUE_25_workflow_fix.patch").read_text(encoding="utf-8")
    for name in applier.FILES:
        assert "b/.github/workflows/{}".format(name) in patch
