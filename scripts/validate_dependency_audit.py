"""Validate the temporary, package-scoped dependency audit exceptions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

FILELOCK_VERSION = "4.1.0"
FILELOCK_ALLOWED_IDS = {
    "CVE-2026-112906",
    "CVE-2026-112908",
    "CVE-2026-112909",
    "CVE-2026-112910",
    "CVE-2026-112911",
    "CVE-2026-112912",
    "CVE-2026-112915",
    "CVE-2026-112934",
    "CVE-2026-112935",
    "CVE-2026-112938",
    "CVE-2026-112939",
    "CVE-2026-112940",
    "CVE-2026-112941",
    "CVE-2026-112914",
    "CVE-2026-112913",
    "CVE-2026-112937",
    "CVE-2026-112907",
}


def validate_report(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    dependencies = report.get("dependencies")
    if not isinstance(dependencies, list):
        return errors + ["Audit report must contain a dependencies list."]

    for dependency in dependencies:
        if not isinstance(dependency, dict):
            errors.append("Audit report contains an invalid dependency entry.")
            continue
        vulnerabilities = dependency.get("vulns", [])
        if not isinstance(vulnerabilities, list):
            errors.append("Audit report contains an invalid vulnerability list.")
            continue
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict):
                errors.append("Audit report contains an invalid vulnerability entry.")
                continue
            vuln_id = vulnerability.get("id")
            name = str(dependency.get("name", "")).casefold().replace("_", "-")
            version = str(dependency.get("version", ""))
            allowed = (
                name == "filelock"
                and version == FILELOCK_VERSION
                and isinstance(vuln_id, str)
                and vuln_id in FILELOCK_ALLOWED_IDS
            )
            if not allowed:
                errors.append(
                    f"Unapproved vulnerability: {name or '<unknown>'} {version or '<unknown>'} {vuln_id or '<unknown>'}."
                )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="pip-audit JSON report")
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Cannot read pip-audit report: {exc}", file=sys.stderr)
        return 2
    if not isinstance(report, dict):
        print("Audit report root must be a JSON object.", file=sys.stderr)
        return 2

    errors = validate_report(report)
    if errors:
        print("Dependency audit validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Dependency audit accepted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
