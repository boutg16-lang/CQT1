from datetime import date

from scripts.validate_dependency_audit import (
    ALLOWED_IDS,
    FILELOCK_ALLOWED_IDS,
    validate_report,
)


def _report(name="Pillow", version="12.3.0", vuln_ids=None):
    vuln_ids = ALLOWED_IDS if vuln_ids is None else vuln_ids
    return {
        "dependencies": [
            {
                "name": name,
                "version": version,
                "vulns": [{"id": vuln_id} for vuln_id in sorted(vuln_ids)],
            }
        ]
    }


def test_accepts_only_listed_pillow_advisories_before_expiry():
    assert len(ALLOWED_IDS) == 37
    assert validate_report(_report(), date(2026, 11, 3)) == []


def test_rejects_same_advisory_on_another_package():
    errors = validate_report(_report(name="other-package"), date(2026, 11, 3))
    assert any("Unapproved vulnerability" in error for error in errors)


def test_accepts_scoped_filelock_exception_for_python_310():
    assert len(FILELOCK_ALLOWED_IDS) == 6
    assert validate_report(
        _report("filelock", "4.1.0", FILELOCK_ALLOWED_IDS), date(2026, 11, 3)
    ) == []


def test_rejects_filelock_exception_for_another_version():
    errors = validate_report(
        _report("filelock", "4.2.4", FILELOCK_ALLOWED_IDS), date(2026, 11, 3)
    )
    assert any("Unapproved vulnerability" in error for error in errors)


def test_rejects_unlisted_advisory_and_wrong_pillow_version():
    assert any(
        "Unapproved vulnerability" in error
        for error in validate_report(_report(vuln_ids={"CVE-UNKNOWN"}), date(2026, 11, 3))
    )
    assert any(
        "Unapproved vulnerability" in error
        for error in validate_report(_report(version="12.3.1"), date(2026, 11, 3))
    )


def test_rejects_malformed_advisory_identifier():
    report = {"dependencies": [{"name": "Pillow", "version": "12.3.0", "vulns": [{"id": []}]}]}
    assert any("Unapproved vulnerability" in error for error in validate_report(report, date(2026, 11, 3)))


def test_rejects_expired_exception():
    assert any("expired" in error for error in validate_report(_report(), date(2026, 11, 4)))


def test_accepts_clean_report():
    assert validate_report({"dependencies": []}, date(2026, 11, 3)) == []
