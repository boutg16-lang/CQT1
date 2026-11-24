"""Tests for the WebUI console logfile (webui/logfile.py).

The logfile tee must be deterministic: no network, no real media, and
the global stdout/stderr streams are restored after every test.
"""

import sys

import pytest

from webui.logfile import LOG_FILENAME, install_webui_logfile


@pytest.fixture()
def _restore_streams():
    stdout, stderr = sys.stdout, sys.stderr
    try:
        yield
    finally:
        sys.stdout, sys.stderr = stdout, stderr


def test_install_creates_logfile_and_tees_stdout(tmp_path, capsys, _restore_streams):
    log_path = install_webui_logfile(tmp_path)
    assert log_path == tmp_path / LOG_FILENAME
    assert log_path.exists()
    print("hello logfile")
    captured = capsys.readouterr()
    assert "hello logfile" in captured.out
    assert "hello logfile" in log_path.read_text(encoding="utf-8")


def test_install_tees_stderr(tmp_path, capsys, _restore_streams):
    log_path = install_webui_logfile(tmp_path)
    print("error line", file=sys.stderr)
    captured = capsys.readouterr()
    assert "error line" in captured.err
    assert "error line" in log_path.read_text(encoding="utf-8")


def test_install_writes_session_header(tmp_path, _restore_streams):
    log_path = install_webui_logfile(tmp_path)
    assert "OUSSAMA Cutter WebUI started" in log_path.read_text(encoding="utf-8")


def test_install_disabled_by_env(tmp_path, monkeypatch, _restore_streams):
    monkeypatch.setenv("VIRALCUTTER_DISABLE_WEBUI_LOGFILE", "1")
    stdout, stderr = sys.stdout, sys.stderr
    assert install_webui_logfile(tmp_path) is None
    assert sys.stdout is stdout
    assert sys.stderr is stderr
    assert not (tmp_path / LOG_FILENAME).exists()


def test_install_returns_none_when_logs_dir_unusable(tmp_path, _restore_streams):
    blocker = tmp_path / "logs"
    blocker.write_text("not a directory", encoding="utf-8")
    stdout, stderr = sys.stdout, sys.stderr
    assert install_webui_logfile(blocker) is None
    assert sys.stdout is stdout
    assert sys.stderr is stderr


def test_tee_survives_closed_logfile(tmp_path, capsys, _restore_streams):
    install_webui_logfile(tmp_path)
    # Close the shared log file handle behind the tee's back; a write
    # must still reach the console and must not raise.
    sys.stdout._log_file.close()
    print("still fine")
    captured = capsys.readouterr()
    assert "still fine" in captured.out
