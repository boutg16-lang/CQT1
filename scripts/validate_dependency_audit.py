"""Validate the temporary, package-scoped Pillow audit exception."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

EXPIRES = date(2026, 11, 4)
PILLOW_VERSION = "12.3.0"
ALLOWED_IDS = {
    "CVE-2026-112895",
    "CVE-2026-112400",
    "CVE-2026-112397",
    "CVE-2026-112896",
    "CVE-2026-112897",
    "CVE-2026-112395",
    "CVE-2026-112404",
    "CVE-2026-112899",
    "CVE-2026-112402",
    "CVE-2026-112900",
    "CVE-2026-112893",
    "CVE-2026-112392",
    "CVE-2026-112891",
    "CVE-2026-112405",
    "CVE-2026-112390",
    "CVE-2026-112401",
    "CVE-2026-112901",
    "CVE-2026-112391",
    "CVE-2026-112894",
    "CVE-2026-112406",
    "CVE-2026-112399",
    "CVE-2026-112892",
    "CVE-2026-112393",
    "CVE-2026-112396",
}


def validate_report(report: dict[str, Any], today: date | None = None) -> list[str]:
    today = today or datetime.now(timezone.utc).date()
    errors: list[str] = []
    if today >= EXPIRES:
        errors.append(f"Temporary Pillow exception expired on {EXPIRES.isoformat()}.")

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
            if (
                name != "pillow"
                or version != PILLOW_VERSION
                or not isinstance(vuln_id, str)
                or vuln_id not in ALLOWED_IDS
            ):
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
    print(f"Dependency audit accepted; Pillow exception expires {EXPIRES.isoformat()}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
