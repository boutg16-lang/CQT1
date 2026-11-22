from scripts.validate_dependency_audit import (
    FILELOCK_ALLOWED_IDS,
    validate_report,
)


def _report(name="filelock", version="4.1.0", vuln_ids=None):
    vuln_ids = FILELOCK_ALLOWED_IDS if vuln_ids is None else vuln_ids
    return {
        "dependencies": [
            {
                "name": name,
                "version": version,
                "vulns": [{"id": vuln_id} for vuln_id in sorted(vuln_ids)],
            }
        ]
    }


def test_accepts_only_listed_filelock_advisories():
    assert len(FILELOCK_ALLOWED_IDS) == 17
    assert validate_report(_report()) == []


def test_rejects_same_advisory_on_another_package():
    errors = validate_report(_report(name="other-package"))
    assert any("Unapproved vulnerability" in error for error in errors)


def test_rejects_filelock_exception_for_another_version():
    errors = validate_report(_report("filelock", "4.2.4", FILELOCK_ALLOWED_IDS))
    assert any("Unapproved vulnerability" in error for error in errors)


def test_rejects_unlisted_advisory():
    assert any(
        "Unapproved vulnerability" in error
        for error in validate_report(_report(vuln_ids={"CVE-UNKNOWN"}))
    )


def test_rejects_malformed_advisory_identifier():
    report = {
        "dependencies": [
            {"name": "filelock", "version": "4.1.0", "vulns": [{"id": []}]}
        ]
    }
    assert any("Unapproved vulnerability" in error for error in validate_report(report))


def test_pillow_vulnerabilities_are_no_longer_excepted():
    # The Pillow 12.3.0 exception existed only because Pillow 13 needs
    # Python >=3.11; with 3.10 dropped the pin moved to 13.0.0, so a
    # 12.3.0 advisory must now fail validation.
    errors = validate_report(_report("Pillow", "12.3.0", {"CVE-2026-112895"}))
    assert any("Unapproved vulnerability" in error for error in errors)


def test_accepts_clean_report():
    assert validate_report({"dependencies": []}) == []
