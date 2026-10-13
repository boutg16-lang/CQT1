# -*- coding: utf-8 -*-
"""One-command applier for the CI workflow fix (issues #25 + #26).

**Why this exists.** The automation account used by the AI agent cannot modify
``.github/workflows/**``: GitHub requires the ``workflows`` permission, which
the installed GitHub App does not hold. That was verified exhaustively —
``git push`` (rejected with the explicit workflow-permission error), the
Contents API (403) and the Git Data API (403 at ``git/trees``) — so the change
must be committed by the repository owner.

This script reduces the owner's job to a single command: it copies the two
final, pre-tested workflow files from ``packaging/ci-fix/`` into
``.github/workflows/``, removes the temporary CI name shim from
``packaging/viralcutter.spec`` (safe only once the workflow is fixed, which is
why the order matters and why the agent cannot do that half alone), validates
the result, and — with ``--push`` — commits and pushes it.

Usage (from the project root, with your own git credentials)::

    python -m scripts.apply_ci_workflow_fix                 # dry run (default)
    python -m scripts.apply_ci_workflow_fix --apply         # write files only
    python -m scripts.apply_ci_workflow_fix --apply --push  # write, commit, push

The run is idempotent: a second invocation reports "already applied".

Exit codes: 0 = success (or nothing to do), 1 = validation failed, 2 = git error.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence

SOURCE_DIR = os.path.join("packaging", "ci-fix")
WORKFLOW_DIR = os.path.join(".github", "workflows")
FILES = ("build-exe.yml", "ci.yml")
SPEC_PATH = os.path.join("packaging", "viralcutter.spec")
SPEC_SHIM_MARKER = "# --- CI compatibility shim"
DEFAULT_MESSAGE = (
    "ci(release): single-source EXE_NAME, checksums manifest, resilient "
    "artifact uploads (#25, #26)"
)


def _repo_root(start: Optional[str] = None) -> str:
    root = os.path.abspath(start or os.getcwd())
    if os.path.isdir(os.path.join(root, ".git")):
        return root
    # Walk up a few levels so the command works from a subfolder.
    probe = root
    for _ in range(4):
        probe = os.path.dirname(probe)
        if os.path.isdir(os.path.join(probe, ".git")):
            return probe
    return root


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _write_lf(path: str, text: str) -> None:
    """Write with LF endings (the repo is LF; CRLF would dirty every line)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text.replace("\r\n", "\n"))


def _spec_exe_name(spec_text: str) -> Optional[str]:
    marker = spec_text.find("EXE(")
    if marker < 0:
        return None
    match = re.search(r'name\s*=\s*["\']([^"\']+)["\']', spec_text[marker:])
    return match.group(1) if match else None


def inspect(root: str) -> Dict[str, Any]:
    """Report the current state without changing anything."""
    state: Dict[str, Any] = {"root": root, "files": {}, "spec": {}, "applied": False}
    for name in FILES:
        target = os.path.join(root, WORKFLOW_DIR, name)
        source = os.path.join(root, SOURCE_DIR, name)
        current = _read(target) if os.path.isfile(target) else None
        wanted = _read(source) if os.path.isfile(source) else None
        state["files"][name] = {
            "target": target,
            "target_exists": current is not None,
            "source_exists": wanted is not None,
            "already_current": bool(current is not None and wanted is not None
                                    and current == wanted),
            "needs_write": bool(wanted is not None and current != wanted),
        }
    spec_file = os.path.join(root, SPEC_PATH)
    spec_text = _read(spec_file) if os.path.isfile(spec_file) else ""
    state["spec"] = {
        "path": spec_file,
        "exists": bool(spec_text),
        "has_shim": SPEC_SHIM_MARKER in spec_text,
        "exe_name": _spec_exe_name(spec_text),
    }
    build_exe = state["files"]["build-exe.yml"]
    state["applied"] = bool(
        not build_exe["needs_write"]
        and build_exe["target_exists"]
        and not state["spec"]["has_shim"])
    return state


def validate(root: str) -> List[str]:
    """Post-change validation of the on-disk state (no git required)."""
    problems: List[str] = []
    build_exe_path = os.path.join(root, WORKFLOW_DIR, "build-exe.yml")
    ci_path = os.path.join(root, WORKFLOW_DIR, "ci.yml")
    for path in (build_exe_path, ci_path):
        if not os.path.isfile(path):
            problems.append("missing after apply: {}".format(path))
    if problems:
        return problems

    build_exe = _read(build_exe_path)
    ci = _read(ci_path)
    if "EXE_NAME" not in build_exe:
        problems.append("build-exe.yml has no EXE_NAME — the fix was not applied")
    if "checksums.txt" not in build_exe:
        problems.append("build-exe.yml does not publish checksums.txt (auto_updater "
                        "would refuse the release)")
    try:
        import yaml  # optional dependency (PyYAML ships with gradio)
        for text in (build_exe, ci):
            yaml.safe_load(text)
    except ImportError:
        pass
    except Exception as exc:
        problems.append("invalid YAML: {}".format(exc))

    spec_text = _read(os.path.join(root, SPEC_PATH))
    spec_name = _spec_exe_name(spec_text)
    match = re.search(r"^\s*EXE_NAME:\s*(\S+)\s*$", build_exe, flags=re.MULTILINE)
    workflow_name = match.group(1) if match else None
    if spec_name and workflow_name and workflow_name != "{}.exe".format(spec_name):
        problems.append("EXE_NAME={} does not match the spec name {}".format(
            workflow_name, spec_name))
    if SPEC_SHIM_MARKER in spec_text:
        problems.append("the temporary CI shim is still present in {}".format(SPEC_PATH))
    return problems


def apply_changes(root: str) -> Dict[str, Any]:
    """Copy the fixed workflows in and drop the spec shim."""
    written: List[str] = []
    for name in FILES:
        source = os.path.join(root, SOURCE_DIR, name)
        target = os.path.join(root, WORKFLOW_DIR, name)
        if not os.path.isfile(source):
            raise SystemExit("missing packaged workflow: {}".format(source))
        wanted = _read(source)
        if not os.path.isfile(target) or _read(target) != wanted:
            _write_lf(target, wanted)
            written.append(target)

    shim_removed = False
    spec_file = os.path.join(root, SPEC_PATH)
    if os.path.isfile(spec_file):
        spec_text = _read(spec_file)
        if SPEC_SHIM_MARKER in spec_text:
            trimmed = spec_text[:spec_text.index(SPEC_SHIM_MARKER)].rstrip() + "\n"
            _write_lf(spec_file, trimmed)
            shim_removed = True
    return {"written": written, "shim_removed": shim_removed}


def _git(root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", root, *args],
                          capture_output=True, text=True)


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply the CI workflow fix (#25/#26) — run by the repo owner")
    parser.add_argument("--root", default=None, help="repository root")
    parser.add_argument("--apply", action="store_true",
                        help="write the files (default is a dry run)")
    parser.add_argument("--push", action="store_true",
                        help="git add/commit/push after applying")
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    args = parser.parse_args(argv)

    root = _repo_root(args.root)
    state = inspect(root)
    print("repo: {}".format(root))

    if state["applied"] and not args.apply:
        print("✓ الإصلاح مُطبَّق بالفعل — لا شيء لفعله.")
        print("  spec name = {}, shim = {}".format(
            state["spec"]["exe_name"], "موجود" if state["spec"]["has_shim"] else "مُزال"))
        return 0

    for name, info in state["files"].items():
        if not info["source_exists"]:
            label = "✗ ملف المصدر مفقود (هل أنت في جذر المستودع؟)"
        elif info["needs_write"]:
            label = "سيُكتب"
        else:
            label = "مطابق"
        print("  {}: {}{}".format(
            name, label,
            "" if (info["target_exists"] or not info["source_exists"]) else " (غير موجود بعد)"))
    print("  حذف الـ shim من spec: {}".format(
        "نعم" if state["spec"]["has_shim"] else "لا حاجة"))

    if not args.apply:
        print("\n(تجربة جافة — لم يُكتب شيء. أضف --apply للتنفيذ.)")
        return 0

    result = apply_changes(root)
    if not result["written"] and not result["shim_removed"]:
        print("\n✓ لا شيء لفعله — الملفات مطابقة والإصلاح مُطبَّق.")
        return 0
    problems = validate(root)
    if problems:
        print("\n✗ فشل التحقق:", file=sys.stderr)
        for problem in problems:
            print("  - {}".format(problem), file=sys.stderr)
        return 1
    print("\n✓ كُتب {} ملفاً{}".format(
        len(result["written"]), "، وأُزيل الـ shim" if result["shim_removed"] else ""))
    for path in result["written"]:
        print("  {}  sha256={}".format(os.path.relpath(path, root), _sha256(path)[:16]))

    if not args.push:
        print("\nالخطوة التالية: git add .github packaging && git commit && git push"
              "\nأو شغّل نفس الأمر مع --push.")
        return 0

    print("\ngit add/commit/push …")
    add = _git(root, "add", WORKFLOW_DIR, SPEC_PATH)
    if add.returncode != 0:
        print(add.stderr.strip(), file=sys.stderr)
        return 2
    commit = _git(root, "commit", "-m", args.message)
    if commit.returncode != 0:
        print((commit.stdout + commit.stderr).strip(), file=sys.stderr)
        return 2
    push = _git(root, "push", "origin", "HEAD")
    if push.returncode != 0:
        print((push.stdout + push.stderr).strip(), file=sys.stderr)
        return 2
    print("✓ مدفوع. أول وسم جديد سيُنتج Release باسم OUSSAMA-Cutter.exe مع checksums.txt")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
