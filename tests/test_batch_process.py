"""Direct tests for scripts/batch_process.py.

The pipeline subprocess is never run for real — subprocess.run is stubbed
and the assembled command is asserted. The WebUI publish panel is stubbed
via sys.modules. Real files are only tiny URL lists inside tmp_path.
"""

import json
import subprocess
import sys
import types
from argparse import Namespace

import pytest

from scripts import batch_process


def _args(**overrides):
    base = {
        "segments": 3, "min_duration": 15, "max_duration": 90,
        "ai_backend": "manual", "workflow": 1, "viral": False,
        "themes": None, "live_wait": None, "sponsorblock": None,
        "quality": None, "safety_mode": "block", "upload": False,
        "dry_run": False, "privacy": None, "extra": [],
        "stop_on_error": False,
    }
    base.update(overrides)
    return Namespace(**base)


# ------------------------------------------------------------------- _read_urls


def test_read_urls_skips_comments_and_blanks(tmp_path):
    path = tmp_path / "urls.txt"
    path.write_text(
        "# a comment\n\n  https://youtu.be/aaa  \nhttps://youtu.be/bbb\n"
        "   # indented comment\n",
        encoding="utf-8")
    assert batch_process._read_urls(str(path)) == [
        "https://youtu.be/aaa", "https://youtu.be/bbb"]


def test_read_urls_empty_file(tmp_path):
    path = tmp_path / "urls.txt"
    path.write_text("# only comments\n\n", encoding="utf-8")
    assert batch_process._read_urls(str(path)) == []


def test_read_urls_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        batch_process._read_urls(str(tmp_path / "gone.txt"))


def test_now_is_utc_zulu():
    now = batch_process._now()
    assert now.endswith("Z")
    assert "T" in now


# --------------------------------------------------------------- build_command


def test_build_command_base_shape():
    cmd = batch_process.build_command("https://youtu.be/aaa", _args())
    assert cmd[0] == sys.executable
    assert cmd[1].endswith("main_improved.py")
    assert "--url" in cmd and "https://youtu.be/aaa" in cmd
    assert "--segments" in cmd and "3" in cmd
    assert "--min-duration" in cmd and "15" in cmd
    assert "--max-duration" in cmd and "90" in cmd
    assert "--ai-backend" in cmd and "manual" in cmd
    assert "--workflow" in cmd and "1" in cmd
    assert "--skip-prompts" in cmd


def test_build_command_optional_flags():
    cmd = batch_process.build_command("https://youtu.be/aaa", _args(
        viral=True, themes="tech", live_wait=30.5, sponsorblock="all",
        quality="1080p", safety_mode="flag", segments=5, ai_backend="gemini"))
    assert "--viral" in cmd
    assert cmd[cmd.index("--themes") + 1] == "tech"
    assert cmd[cmd.index("--live-wait") + 1] == "30.5"
    assert cmd[cmd.index("--sponsorblock") + 1] == "all"
    assert cmd[cmd.index("--video-quality") + 1] == "1080p"
    assert cmd[cmd.index("--safety-mode") + 1] == "flag"
    assert cmd[cmd.index("--segments") + 1] == "5"
    assert cmd[cmd.index("--ai-backend") + 1] == "gemini"


def test_build_command_extra_pairs_split_on_first_equals():
    cmd = batch_process.build_command("https://youtu.be/aaa", _args(
        extra=["speed=1.5", "title=a=b=c", "bare"]))
    assert "--speed" in cmd and cmd[cmd.index("--speed") + 1] == "1.5"
    assert "--title" in cmd and cmd[cmd.index("--title") + 1] == "a=b=c"
    assert "--bare" not in cmd  # no '=' -> ignored


def test_build_command_no_optional_flags_when_defaults():
    cmd = batch_process.build_command("https://youtu.be/aaa", _args())
    for flag in ("--viral", "--themes", "--live-wait", "--sponsorblock",
                 "--video-quality"):
        assert flag not in cmd


# -------------------------------------------------------------- upload_project


def _stub_publish_panel(monkeypatch, list_clips=None, stream=None):
    panel = types.ModuleType("webui.publish_panel")
    if list_clips is not None:
        panel.list_clips = list_clips
    if stream is not None:
        panel.stream_upload_batch = stream
    webui = types.ModuleType("webui")
    webui.publish_panel = panel
    monkeypatch.setitem(sys.modules, "webui", webui)
    monkeypatch.setitem(sys.modules, "webui.publish_panel", panel)
    return panel


def test_upload_panel_unavailable(monkeypatch):
    # A webui package without publish_panel makes the import fail.
    monkeypatch.setitem(sys.modules, "webui", types.ModuleType("webui"))
    monkeypatch.delitem(sys.modules, "webui.publish_panel", raising=False)
    result = batch_process.upload_project("/proj", _args(dry_run=True))
    assert result["ok"] is False
    assert result["uploaded"] == 0
    assert result["dry_run"] is True
    assert "publish_panel unavailable" in result["detail"]


def test_upload_no_clips_found(monkeypatch):
    _stub_publish_panel(monkeypatch, list_clips=lambda folder: [])
    result = batch_process.upload_project("/proj", _args(dry_run=True))
    assert result["ok"] is False
    assert result["uploaded"] == 0
    assert "no mp4 clips found" in result["detail"]


def test_upload_clip_discovery_failure(monkeypatch):
    def boom(folder):
        raise RuntimeError("disk gone")

    _stub_publish_panel(monkeypatch, list_clips=boom)
    result = batch_process.upload_project("/proj", _args())
    assert result["ok"] is False
    assert "clip discovery failed" in result["detail"]
    assert "disk gone" in result["detail"]


def test_upload_batch_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("quota exceeded")

    _stub_publish_panel(monkeypatch,
                        list_clips=lambda folder: ["a.mp4"],
                        stream=boom)
    result = batch_process.upload_project("/proj", _args())
    assert result["ok"] is False
    assert "upload batch failed" in result["detail"]


def test_upload_counts_uploaded_and_scheduled(monkeypatch):
    _stub_publish_panel(
        monkeypatch,
        list_clips=lambda folder: ["a.mp4", "b.mp4", "c.mp4"],
        stream=lambda *a, **k: ["uploaded a.mp4", "scheduled b.mp4", "failed c.mp4"])
    result = batch_process.upload_project("/proj", _args(dry_run=False))
    assert result["ok"] is True
    assert result["uploaded"] == 2
    assert result["dry_run"] is False


def test_upload_dry_run_passthrough(monkeypatch):
    seen = {}

    def stream(folder, platform, clips, dry_run, *a, **k):
        seen["dry_run"] = dry_run
        seen["privacy"] = k.get("privacy_status")
        return ["uploaded x"]

    _stub_publish_panel(monkeypatch,
                        list_clips=lambda folder: ["a.mp4"],
                        stream=stream)
    result = batch_process.upload_project(
        "/proj", _args(dry_run=True, privacy="unlisted"))
    assert result["ok"] is True
    assert seen["dry_run"] is True
    assert seen["privacy"] == "unlisted"


# ----------------------------------------------------------------- process_url


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _stub_subprocess(monkeypatch, proc):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: proc)


def test_process_url_success(monkeypatch):
    _stub_subprocess(monkeypatch, _FakeProc(0, stdout="done\n"))
    result = batch_process.process_url("https://youtu.be/aaa", _args(), 0, 1)
    assert result["url"] == "https://youtu.be/aaa"
    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert result["elapsed_seconds"] >= 0
    assert result["started_at"].endswith("Z")
    assert result["upload"] is None  # no --upload requested


def test_process_url_failure_reports_exit_code(monkeypatch, capsys):
    _stub_subprocess(monkeypatch, _FakeProc(2, stdout="out", stderr="err"))
    result = batch_process.process_url("https://youtu.be/aaa", _args(), 1, 3)
    assert result["ok"] is False
    assert result["exit_code"] == 2
    assert "FAILED" in capsys.readouterr().out


def test_process_url_uploads_only_on_success(monkeypatch):
    _stub_subprocess(monkeypatch, _FakeProc(0, stdout="done\n"))
    calls = []
    monkeypatch.setattr(batch_process, "upload_project",
                        lambda folder, args: calls.append(folder) or
                        {"ok": True, "uploaded": 1})
    result = batch_process.process_url(
        "https://youtu.be/aaa", _args(upload=True), 0, 1)
    # stdout has no project folder and no VIRALS dir exists here.
    assert result["upload"] == {"ok": False, "detail": "project folder not found"}
    assert calls == []


def test_process_url_upload_uses_project_folder_from_stdout(monkeypatch, tmp_path):
    project = tmp_path / "proj1"
    project.mkdir()
    stdout = "finished\nproject folder: {}\n".format(project)
    _stub_subprocess(monkeypatch, _FakeProc(0, stdout=stdout))
    monkeypatch.setattr(batch_process, "upload_project",
                        lambda folder, args: {"ok": True, "uploaded": 2})
    result = batch_process.process_url(
        "https://youtu.be/aaa", _args(upload=True), 0, 1)
    assert result["upload"] == {"ok": True, "uploaded": 2}


def test_process_url_no_upload_on_failure(monkeypatch):
    _stub_subprocess(monkeypatch, _FakeProc(1, stdout="x", stderr="y"))
    calls = []
    monkeypatch.setattr(batch_process, "upload_project",
                        lambda folder, args: calls.append(folder))
    result = batch_process.process_url(
        "https://youtu.be/aaa", _args(upload=True), 0, 1)
    assert result["ok"] is False
    assert result["upload"] is None
    assert calls == []


# ------------------------------------------------------------------------ main


def _urls_file(tmp_path, content):
    path = tmp_path / "urls.txt"
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_main_no_urls_returns_one(tmp_path, capsys):
    path = _urls_file(tmp_path, "# nothing\n")
    assert batch_process.main([path]) == 1
    assert "no URLs found" in capsys.readouterr().out


def test_main_all_ok_writes_report(tmp_path, monkeypatch):
    path = _urls_file(tmp_path, "https://youtu.be/aaa\nhttps://youtu.be/bbb\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(batch_process, "process_url",
                        lambda url, args, i, total: {
                            "url": url, "ok": True, "exit_code": 0,
                            "elapsed_seconds": 0.1, "started_at": "x",
                            "upload": None})
    assert batch_process.main([path]) == 0
    report = json.loads((tmp_path / "batch_report.json").read_text(encoding="utf-8"))
    assert report["total"] == 2
    assert report["ok"] == 2
    assert report["failed"] == 0
    assert [r["url"] for r in report["results"]] == [
        "https://youtu.be/aaa", "https://youtu.be/bbb"]


def test_main_any_failure_returns_one(tmp_path, monkeypatch):
    path = _urls_file(tmp_path, "https://youtu.be/aaa\nhttps://youtu.be/bbb\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(batch_process, "process_url",
                        lambda url, args, i, total: {
                            "url": url, "ok": url.endswith("aaa"),
                            "exit_code": 0, "elapsed_seconds": 0.1,
                            "started_at": "x", "upload": None})
    assert batch_process.main([path]) == 1
    report = json.loads((tmp_path / "batch_report.json").read_text(encoding="utf-8"))
    assert report["ok"] == 1
    assert report["failed"] == 1


def test_main_stop_on_error_aborts_batch(tmp_path, monkeypatch):
    path = _urls_file(tmp_path, "https://youtu.be/aaa\nhttps://youtu.be/bbb\n"
                                "https://youtu.be/ccc\n")
    monkeypatch.chdir(tmp_path)
    seen = []
    monkeypatch.setattr(batch_process, "process_url",
                        lambda url, args, i, total: seen.append(url) or {
                            "url": url, "ok": False, "exit_code": 1,
                            "elapsed_seconds": 0.1, "started_at": "x",
                            "upload": None})
    assert batch_process.main([path, "--stop-on-error"]) == 1
    assert seen == ["https://youtu.be/aaa"]


def test_main_missing_urls_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        batch_process.main([str(tmp_path / "gone.txt")])


def test_main_rejects_bad_ai_backend(tmp_path):
    path = _urls_file(tmp_path, "https://youtu.be/aaa\n")
    with pytest.raises(SystemExit):
        batch_process.main([path, "--ai-backend", "nope"])
