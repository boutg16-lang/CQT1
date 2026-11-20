# -*- coding: utf-8 -*-
"""Direct tests for scripts/crash_report.py (privacy-respecting reports)."""

import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import crash_report


class TestSanitize:
    def test_home_directory_replaced(self):
        home = os.path.expanduser("~")
        text = crash_report._sanitize("failed at {}//project//x.mp4".format(home))
        assert home not in text
        assert "<HOME>" in text

    def test_windows_media_path_replaced(self):
        text = crash_report._sanitize(r"failed at C:\Users\Bob\My Videos\clip.mp4 here")
        assert "C:\\Users" not in text
        assert "<PATH>" in text

    def test_windows_extensionless_path_replaced(self):
        text = crash_report._sanitize(r"failed at C:\Users\Bob\secret\file here")
        assert "C:\\Users" not in text
        assert "<PATH>" in text

    def test_unix_absolute_path_replaced(self):
        text = crash_report._sanitize("failed at /opt/somewhere/deep/file.txt here")
        assert "/opt/somewhere" not in text
        assert "<PATH>" in text

    def test_bearer_token_redacted(self):
        text = crash_report._sanitize("Authorization: Bearer abc123XYZ")
        assert "abc123XYZ" not in text
        assert "<REDACTED>" in text

    def test_bare_bearer_redacted(self):
        text = crash_report._sanitize("got bearer abc.def-ghi_123")
        assert "abc.def" not in text
        assert "Bearer <REDACTED>" in text

    def test_jwt_redacted(self):
        token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        text = crash_report._sanitize("token " + token)
        assert token not in text
        assert "<JWT>" in text

    def test_google_api_key_redacted(self):
        key = "AIza" + "A" * 35
        text = crash_report._sanitize("key " + key)
        assert key not in text
        assert "<REDACTED>" in text

    def test_openai_style_key_redacted(self):
        text = crash_report._sanitize("key sk-abcdefghijklmnop")
        assert "sk-abcdefghijklmnop" not in text
        assert "<REDACTED>" in text

    def test_key_value_assignment_redacted(self):
        text = crash_report._sanitize("failed with api_key=supersecret123 and ok")
        assert "supersecret123" not in text
        assert "api_key=<REDACTED>" in text

    def test_password_assignment_redacted(self):
        text = crash_report._sanitize("login password=hunter2 failed")
        assert "hunter2" not in text
        assert "password=<REDACTED>" in text

    def test_clean_message_unchanged(self):
        assert crash_report._sanitize("plain error message") == "plain error message"

    def test_truncated_to_300_chars_by_default(self):
        text = crash_report._sanitize("x" * 500)
        assert len(text) == 300

    def test_max_len_parameter_respected(self):
        assert crash_report._sanitize("x" * 50, max_len=10) == "x" * 10

    def test_non_string_input_coerced(self):
        assert crash_report._sanitize(12345) == "12345"


class TestCollect:
    def test_entry_shape_with_exception(self):
        entry = crash_report._collect("transcribe", ValueError("boom"))
        assert entry["stage"] == "transcribe"
        assert entry["error"] == "boom"
        assert entry["error_type"] == "ValueError"
        assert entry["cuda"] is False  # torch is not installed here
        assert entry["version"] is None
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", entry["timestamp"])
        assert isinstance(entry["python"], str) and entry["python"]
        assert isinstance(entry["os"], str) and entry["os"]

    def test_entry_without_exception(self):
        entry = crash_report._collect("cut")
        assert entry["error"] is None
        assert entry["error_type"] is None
        assert entry["stage"] == "cut"

    def test_default_stage_is_unknown(self):
        assert crash_report._collect()["stage"] == "unknown"

    def test_error_message_is_sanitized(self):
        exc = ValueError("failed at /opt/somewhere/deep/file.txt")
        entry = crash_report._collect("x", exc)
        assert "/opt/somewhere" not in entry["error"]
        assert "<PATH>" in entry["error"]


class TestLogCrash:
    def test_writes_json_line_and_returns_entry(self, tmp_path):
        log = tmp_path / "crash_report.log"
        entry = crash_report.log_crash("cut", ValueError("boom"), log_path=str(log))
        lines = log.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["error"] == "boom"
        assert entry["stage"] == "cut"

    def test_appends_multiple_entries(self, tmp_path):
        log = tmp_path / "crash_report.log"
        crash_report.log_crash("a", ValueError("1"), log_path=str(log))
        crash_report.log_crash("b", ValueError("2"), log_path=str(log))
        assert len(log.read_text(encoding="utf-8").splitlines()) == 2

    def test_unwritable_path_does_not_raise(self):
        entry = crash_report.log_crash(
            "x", ValueError("boom"), log_path="/nonexistent-dir-xyz/crash.log"
        )
        assert entry["stage"] == "x"


class _FakeResponse:
    """Minimal context-manager response for urlopen stubs."""

    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestSendCrash:
    def test_no_endpoint_returns_false(self, monkeypatch):
        monkeypatch.delenv("VIRALCUTTER_CRASH_ENDPOINT", raising=False)
        assert crash_report.send_crash({"stage": "x"}) is False

    def test_empty_endpoint_returns_false(self):
        assert crash_report.send_crash({"stage": "x"}, endpoint="") is False

    def _fake_urlopen(self, status):
        def fake(req, timeout=None):
            assert req.get_method() == "POST"
            assert req.headers.get("Content-type") == "application/json"
            json.loads(req.data.decode("utf-8"))  # payload must be valid JSON
            return _FakeResponse(status)

        return fake

    def test_success_status_returns_true(self, monkeypatch):
        monkeypatch.setattr(
            crash_report.urllib.request, "urlopen", self._fake_urlopen(200)
        )
        assert (
            crash_report.send_crash({"stage": "x"}, endpoint="http://localhost:9/x")
            is True
        )

    def test_error_status_returns_false(self, monkeypatch):
        monkeypatch.setattr(
            crash_report.urllib.request, "urlopen", self._fake_urlopen(500)
        )
        assert (
            crash_report.send_crash({"stage": "x"}, endpoint="http://localhost:9/x")
            is False
        )

    def test_urlopen_failure_returns_false(self, monkeypatch):
        def boom(_req, timeout=None):
            raise ConnectionError("refused")

        monkeypatch.setattr(crash_report.urllib.request, "urlopen", boom)
        assert (
            crash_report.send_crash({"stage": "x"}, endpoint="http://localhost:9/x")
            is False
        )

    def test_endpoint_from_env(self, monkeypatch):
        monkeypatch.setenv("VIRALCUTTER_CRASH_ENDPOINT", "http://localhost:9/x")
        monkeypatch.setattr(
            crash_report.urllib.request, "urlopen", self._fake_urlopen(201)
        )
        assert crash_report.send_crash({"stage": "x"}) is True


class TestReport:
    def test_no_opt_in_logs_only(self, tmp_path, monkeypatch):
        monkeypatch.delenv("VIRALCUTTER_CRASH_REPORT", raising=False)
        sent = []
        monkeypatch.setattr(
            crash_report, "send_crash", lambda e, endpoint=None: sent.append(e) or True
        )
        log = tmp_path / "crash_report.log"
        entry = crash_report.report("cut", ValueError("boom"), log_path=str(log))
        assert sent == []
        assert entry["error"] == "boom"
        assert log.exists()

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", " On "])
    def test_opt_in_values_send(self, tmp_path, monkeypatch, value):
        monkeypatch.setenv("VIRALCUTTER_CRASH_REPORT", value)
        sent = []
        monkeypatch.setattr(
            crash_report, "send_crash", lambda e, endpoint=None: sent.append(e) or True
        )
        crash_report.report("cut", ValueError("boom"), log_path=str(tmp_path / "l.log"))
        assert len(sent) == 1

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
    def test_opt_out_values_do_not_send(self, tmp_path, monkeypatch, value):
        monkeypatch.setenv("VIRALCUTTER_CRASH_REPORT", value)
        sent = []
        monkeypatch.setattr(
            crash_report, "send_crash", lambda e, endpoint=None: sent.append(e) or True
        )
        crash_report.report("cut", ValueError("boom"), log_path=str(tmp_path / "l.log"))
        assert sent == []

    def test_endpoint_argument_forwarded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VIRALCUTTER_CRASH_REPORT", "1")
        seen = {}
        monkeypatch.setattr(
            crash_report,
            "send_crash",
            lambda e, endpoint=None: seen.setdefault("endpoint", endpoint) or True,
        )
        crash_report.report(
            "cut",
            ValueError("boom"),
            log_path=str(tmp_path / "l.log"),
            endpoint="http://example.invalid/x",
        )
        assert seen["endpoint"] == "http://example.invalid/x"


class TestMain:
    def test_main_prints_entry_and_returns_zero(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            sys, "argv", ["crash_report", "--stage", "cli", "--message", "oops"]
        )
        assert crash_report.main() == 0
        entry = json.loads(capsys.readouterr().out)
        assert entry["stage"] == "cli"
        assert entry["error"] == "oops"
        assert (tmp_path / "crash_report.log").exists()

    def test_main_send_flag_sets_opt_in(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("VIRALCUTTER_CRASH_ENDPOINT", raising=False)
        monkeypatch.setattr(
            sys,
            "argv",
            ["crash_report", "--stage", "cli", "--message", "oops", "--send"],
        )
        assert crash_report.main() == 0
        assert os.environ.get("VIRALCUTTER_CRASH_REPORT") == "1"
