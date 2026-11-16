"""Safely update a source checkout from origin/main.

Packaged installs use scripts.auto_updater (signed release assets). This is
for the D:\\OS source layout: fetch, compare, and fast-forward only. It
refuses to touch a dirty tree, never merges, and reports when dependency
manifests changed so the installer can be rerun deliberately.

    python -m scripts.source_updater --check
    python -m scripts.source_updater --apply
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
REPO = "boutg16-lang/CQT1"
DEPENDENCY_FILES = (
    "requirements.txt", "requirements-transcribe.txt",
    "requirements-transcribe-fallback.txt", "requirements-upload.txt",
    "requirements-dubbing.txt", "requirements-browser-bridge.txt",
    "pyproject.toml", "uv.lock", "install_dependencies.bat",
    "setup_on_d.ps1",
)


def _git(args: List[str], cwd: Path | None = None, timeout: int = 120):
    return subprocess.run(
        ["git", *args], cwd=str(cwd or ROOT), capture_output=True,
        text=True, timeout=timeout)


def _out(proc) -> str:
    return (proc.stdout or "").strip()


def _is_repo(root: Path) -> bool:
    try:
        return _git(["rev-parse", "--is-inside-work-tree"], root).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _dirty_files(root: Path) -> List[str]:
    proc = _git(["status", "--porcelain", "--untracked-files=no"], root)
    if proc.returncode != 0:
        return []
    return [line[3:].strip() for line in _out(proc).splitlines() if line.strip()]


def _changed_between(old: str, new: str, root: Path) -> List[str]:
    proc = _git(["diff", "--name-only", old, new], root)
    if proc.returncode != 0:
        return []
    return [line.strip() for line in _out(proc).splitlines() if line.strip()]


def check(root: Path | None = None) -> Dict[str, Any]:
    root = Path(root or ROOT).resolve()
    if not _is_repo(root):
        return {"ok": False, "reason": "not_a_git_checkout", "root": str(root),
                "update_available": False, "dirty": []}
    fetch = _git(["fetch", "origin", "main", "--prune"], root)
    if fetch.returncode != 0:
        return {"ok": False, "reason": "fetch_failed",
                "error": _out(fetch)[-500:], "root": str(root),
                "update_available": False, "dirty": []}
    head = _out(_git(["rev-parse", "HEAD"], root))
    remote = _out(_git(["rev-parse", "origin/main"], root))
    if not head or not remote:
        return {"ok": False, "reason": "cannot_resolve_refs", "root": str(root),
                "update_available": False, "dirty": []}
    ahead = 0
    behind = 0
    counts = _out(_git(["rev-list", "--left-right", "--count",
                        "HEAD...origin/main"], root)).split()
    if len(counts) == 2:
        try:
            ahead, behind = int(counts[0]), int(counts[1])
        except ValueError:
            pass
    dirty = _dirty_files(root)
    changed = _changed_between(head, remote, root) if behind else []
    deps = [path for path in changed
            if os.path.basename(path) in DEPENDENCY_FILES or path in DEPENDENCY_FILES]
    return {
        "ok": True,
        "root": str(root),
        "head": head[:12],
        "remote": remote[:12],
        "ahead": ahead,
        "behind": behind,
        "update_available": behind > 0,
        "dirty": dirty,
        "changed_files": changed,
        "dependency_files": deps,
        "dependencies_changed": bool(deps),
    }


def apply(root: Path | None = None, allow_dirty: bool = False) -> Dict[str, Any]:
    root = Path(root or ROOT).resolve()
    report = check(root)
    if not report.get("ok"):
        return report
    if report["dirty"] and not allow_dirty:
        report.update({
            "applied": False,
            "reason": "dirty_worktree",
            "message": "Tracked local changes exist; commit or stash them first.",
        })
        return report
    if report["ahead"]:
        report.update({
            "applied": False,
            "reason": "diverged",
            "message": "Local main is ahead of origin/main; refusing to reset.",
        })
        return report
    if not report["update_available"]:
        report.update({"applied": False, "reason": "up_to_date"})
        return report
    pull = _git(["pull", "--ff-only", "origin", "main"], root)
    if pull.returncode != 0:
        report.update({
            "applied": False,
            "reason": "pull_failed",
            "error": (_out(pull) or (pull.stderr or ""))[-800:],
        })
        return report
    new_head = _out(_git(["rev-parse", "HEAD"], root))
    report.update({
        "applied": True,
        "reason": "updated",
        "head": new_head[:12] or report["head"],
        "old_head": report["head"],
    })
    if report["dependencies_changed"]:
        report["next_step"] = (
            "Dependency manifests changed. Run .\\setup_on_d.ps1 once, then "
            "restart the app.")
    else:
        report["next_step"] = "Restart the app to load the new code."
    return report


def _print(report: Dict[str, Any]) -> None:
    if not report.get("ok"):
        print("❌ " + str(report.get("reason") or report.get("error") or "update failed"))
        if report.get("root"):
            print("   root: " + report["root"])
        return
    print("Repository: {}".format(report["root"]))
    print("HEAD: {}  origin/main: {}".format(report["head"], report["remote"]))
    if report.get("dirty"):
        print("⚠️ tracked local changes: {}".format(", ".join(report["dirty"][:5])))
    if report.get("applied"):
        print("✅ updated to {}".format(report["head"]))
        print("   " + report.get("next_step", "Restart the app."))
    elif report.get("reason") == "up_to_date":
        print("✅ already up to date")
    elif report.get("reason") == "dirty_worktree":
        print("⛔ refusing to update: tracked local changes exist")
        print("   commit or stash them, then retry")
    elif report.get("reason") == "diverged":
        print("⛔ local main is ahead of origin/main; refusing to reset")
    elif report.get("reason") == "pull_failed":
        print("❌ git pull failed:")
        print(report.get("error", "")[-800:])
    if report.get("dependencies_changed"):
        print("📦 dependency manifests changed: {}".format(
            ", ".join(report["dependency_files"][:6])))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Update a source checkout safely")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="check only (default)")
    mode.add_argument("--apply", action="store_true", help="fast-forward origin/main")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="with --apply: allow tracked local changes")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    report = apply() if args.apply else check()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print(report)
    if not report.get("ok"):
        return 1
    if args.apply:
        return 0 if report.get("applied") or report.get("reason") == "up_to_date" else 2
    return 0 if not report.get("update_available") else 3


if __name__ == "__main__":
    raise SystemExit(main())
