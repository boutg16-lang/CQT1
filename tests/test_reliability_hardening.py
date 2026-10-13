# -*- coding: utf-8 -*-
"""Regression tests for the v7.44 reliability-hardening fixes.

Each test pins one confirmed, execution-reproduced defect:

1. shared ``path + ".tmp"`` corrupted concurrently-saved settings/secrets,
2. malformed queue state crashed the whole WebUI at import time,
3. ``stop()`` + ``start()`` left zero live workers (stale ``None`` sentinels),
4. a corrupt ``checkpoint.json`` version crashed the pipeline,
5. jobs stuck in ``retrying`` were never recovered after a restart,
6. ``ffprobe`` reporting ``"size": "N/A"`` escaped the structured-error contract,
7. credentials leaked into crash reports,
8. manifest-supplied paths could escape the project root (incl. via symlinks),
9. non-JSON-serialisable stage results were recorded as failures.
"""

import json
import os
import stat
import sys
import threading
import time

import pytest

# cv2 is a real dependency. Importing it before ``tests/test_cli_main.py``
# installs its MagicMock stubs keeps ``scripts.face_detection_insightface``
# bound to the real module instead of a stale mock (a pre-existing cross-test
# ordering issue, not caused by these fixes).
try:  # pragma: no cover - optional dependency at import time
    import cv2  # noqa: F401
    import numpy  # noqa: F401
except ImportError:  # pragma: no cover
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import checkpoint, crash_report, media_validation, pipeline_engine
from scripts.pipeline_engine import PipelineEngine
from webui import project_store, settings_store
from webui.render_queue import RenderQueue


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """No real credentials from the host environment may leak into tests."""
    for var in ("GEMINI_API_KEY", "VIRALCUTTER_GEMINI_KEY", "VIRALCUTTER_GEMINI_KEYS",
                "VIRALCUTTER_CONFIG_PASSPHRASE"):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# 1. settings_store — unique temp files + restrictive permissions
# ---------------------------------------------------------------------------

def test_save_webui_prefs_concurrent_writers_keep_valid_json(tmp_path):
    errors = []

    def writer(index):
        for _ in range(30):
            ok, err = settings_store.save_webui_prefs(
                {"writer": index, "blob": "x" * 256}, base_dir=str(tmp_path))
            if not ok:
                errors.append(err)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    raw = (tmp_path / settings_store.WEBUI_PREFS_FILE).read_text(encoding="utf-8")
    assert raw.rstrip().endswith("}")  # no trailing garbage after the JSON
    data = json.loads(raw)             # the loader must not silently reset
    assert isinstance(data, dict)
    assert data["blob"] == "x" * 256
    assert settings_store.load_webui_prefs(base_dir=str(tmp_path))["blob"] == "x" * 256
    assert not list(tmp_path.glob("*.tmp"))


def test_save_ui_settings_concurrent_writers_keep_valid_json(tmp_path):
    errors = []

    def writer(index):
        for _ in range(20):
            ok, err = settings_store.save_ui_settings(
                ai_backend="gemini",
                api_key="AIzaSyD4iE8fG0hI1jK2lM3nO4pQ5rS6tU7vW8x",
                ai_model="gemini-2.5-flash",
                chunk_size=20000 + index,
                base_dir=str(tmp_path))
            if not ok:
                errors.append(err)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    config = json.loads((tmp_path / "api_config.json").read_text(encoding="utf-8"))
    assert config["gemini"]["api_key"] == "AIzaSyD4iE8fG0hI1jK2lM3nO4pQ5rS6tU7vW8x"
    # The stored key must still be readable (a corrupt file resets everything).
    assert settings_store.load_ui_settings(base_dir=str(tmp_path))["api_key"] == \
        "AIzaSyD4iE8fG0hI1jK2lM3nO4pQ5rS6tU7vW8x"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
def test_save_ui_settings_uses_owner_only_permissions(tmp_path):
    ok, err = settings_store.save_ui_settings(
        ai_backend="gemini", api_key="AIzaSyD4iE8fG0hI1jK2lM3nO4pQ5rS6tU7vW8x",
        base_dir=str(tmp_path))
    assert ok, err
    mode = stat.S_IMODE(os.stat(tmp_path / "api_config.json").st_mode)
    assert mode == 0o600


# ---------------------------------------------------------------------------
# 2. render_queue — malformed state must not crash the WebUI
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    '{"jobs": [1, 2, 3]}',
    '{"jobs": "abc"}',
    '{"jobs": 5}',
    '{"jobs": {"a": 5}}',
])
def test_render_queue_survives_malformed_jobs_and_preserves_corrupt_file(tmp_path, payload):
    path = tmp_path / "queue.json"
    path.write_text(payload, encoding="utf-8")

    queue = RenderQueue(path)  # must not raise AttributeError

    assert queue.snapshot() == {}
    assert queue.state_warning
    assert not path.exists()
    assert list(tmp_path.glob("queue.json.corrupt-*"))


# ---------------------------------------------------------------------------
# 3. render_queue — stop()/start() must leave live workers
# ---------------------------------------------------------------------------

def test_render_queue_restart_after_stop_still_runs_new_jobs(tmp_path):
    path = tmp_path / "restart.json"
    release = threading.Event()
    started = threading.Event()

    def runner(job, cancel_event, progress):
        if job.plan.get("block"):
            started.set()
            release.wait(5)
        return "out-" + job.id

    queue = RenderQueue(path, runner=runner, max_workers=1)
    blocked = queue.add({"block": True})
    queue.start()
    try:
        assert started.wait(3)
        queue.stop(wait=False)
        release.set()
        time.sleep(0.2)  # let the busy worker finish and exit untouched
        queue.start()
        second = queue.add({"block": False})
        assert queue.wait(second, timeout=5) == "succeeded"
    finally:
        queue.stop()
    assert queue.snapshot(blocked)["status"] in {"succeeded", "running"}


# ---------------------------------------------------------------------------
# 4. checkpoint — corrupt version + concurrent read-modify-write
# ---------------------------------------------------------------------------

def test_checkpoint_corrupt_version_degrades_gracefully(tmp_path):
    (tmp_path / checkpoint.CHECKPOINT_FILENAME).write_text(
        json.dumps({"version": "abc", "stages": {}}), encoding="utf-8")

    # None of these may raise; the docstring promises a broken checkpoint
    # cannot crash the pipeline.
    checkpoint.mark_started(str(tmp_path), "cut")
    checkpoint.mark_done(str(tmp_path), "cut")
    checkpoint.mark_failed(str(tmp_path), "edit", RuntimeError("boom"))
    checkpoint.clear(str(tmp_path), "transcribe")

    assert checkpoint.load_checkpoint(str(tmp_path))["version"] == 2
    assert checkpoint.is_done(str(tmp_path), "cut") is True


def test_checkpoint_concurrent_marks_preserve_all_stages(tmp_path):
    stages = checkpoint.STAGES[:8]

    def writer(stage):
        for _ in range(20):
            checkpoint.mark_done(str(tmp_path), stage)

    threads = [threading.Thread(target=writer, args=(stage,)) for stage in stages]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    done = checkpoint.load_checkpoint(str(tmp_path))["stages"]
    assert set(stages) <= set(done)


# ---------------------------------------------------------------------------
# 5. render_queue — jobs stuck in "retrying" must recover
# ---------------------------------------------------------------------------

def test_render_queue_recovers_retrying_job_on_load_and_runs(tmp_path):
    path = tmp_path / "retrying.json"
    queue = RenderQueue(path)
    job_id = queue.add({"source": "a.mp4"})
    queue.jobs[job_id].status = "retrying"
    queue._save()

    reloaded = RenderQueue(path)
    assert reloaded.snapshot(job_id)["status"] == "queued"

    reloaded.start(lambda job, cancel_event, progress: "done.mp4")
    try:
        assert reloaded.wait(job_id, timeout=5) == "succeeded"
    finally:
        reloaded.stop()


def test_render_queue_resume_all_requeues_retrying_jobs(tmp_path):
    path = tmp_path / "resume.json"
    queue = RenderQueue(path, runner=lambda job, cancel_event, progress: "ok.mp4")
    job_id = queue.add({"source": "a.mp4"})
    queue.jobs[job_id].status = "retrying"
    queue.pause_all()
    queue.start()
    try:
        assert queue.snapshot(job_id)["status"] == "retrying"
        queue.resume_all()
        assert queue.wait(job_id, timeout=5) == "succeeded"
    finally:
        queue.stop()


# ---------------------------------------------------------------------------
# 6. media_validation — ffprobe "size": "N/A"
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="POSIX shell stub")
def test_probe_media_handles_na_size(tmp_path, monkeypatch):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"0123456789")

    stub = tmp_path / "ffprobe"
    stub.write_text(
        "#!/bin/sh\n"
        "cat <<'EOF'\n"
        '{"format": {"duration": "1.5", "size": "N/A"}, '
        '"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264", '
        '"width": 1920, "height": 1080, "r_frame_rate": "30/1"}, '
        '{"index": 1, "codec_type": "audio", "codec_name": "aac"}]}\n'
        "EOF\n",
        encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))

    report = media_validation.probe_media(str(media))
    assert report["ok"] is True
    assert report["size"] == media.stat().st_size  # fell back to os.path.getsize
    assert report["duration"] == 1.5

    structured = media_validation.validate_media_file(str(media))
    assert isinstance(structured, dict)
    assert structured["ok"] is True


# ---------------------------------------------------------------------------
# 7. crash_report — no credentials in sanitized output
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("secret", [
    "AIzaSyD4iE8fG0hI1jK2lM3nO4pQ5rS6tU7vW8x",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0In0.abc123signature",
    "supersecrettoken",
    "hunter2",
    "sk-livesecretkeyvalue0123456789",
])
def test_sanitize_redacts_credentials(secret):
    message = (
        "call https://generativelanguage.googleapis.com/v1beta/models?key={secret} "
        "with Authorization: Bearer {secret} token={secret} password={secret} "
        "secret={secret} sk=sk-livesecretkeyvalue0123456789"
    ).format(secret=secret)
    sanitized = crash_report._sanitize(message, max_len=2000)
    assert secret not in sanitized


def test_sanitize_redacts_windows_path_with_spaces():
    message = r"failed opening C:\Users\Bob\My Videos\secret client name.mp4"
    sanitized = crash_report._sanitize(message, max_len=2000)
    assert "secret client name.mp4" not in sanitized
    assert "Videos" not in sanitized or "<PATH>" in sanitized


def test_collect_does_not_expose_credentials():
    exc = RuntimeError("download failed: https://x/y?key=AIzaSyD4iE8fG0hI1jK2lM3nO4pQ5rS6tU7vW8x "
                       "token=topsecretvalue")
    entry = crash_report._collect("cut", exc)
    assert "AIzaSyD4iE8fG0hI1jK2lM3nO4pQ5rS6tU7vW8x" not in entry["error"]
    assert "topsecretvalue" not in entry["error"]


# ---------------------------------------------------------------------------
# 8. project_store — path containment (relative + symlink escapes)
# ---------------------------------------------------------------------------

def test_resolve_project_input_rejects_relative_escape(tmp_path):
    root = tmp_path / "VIRALS"
    project = root / "proj"
    project.mkdir(parents=True)
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(b"secret")
    (project / project_store.MANIFEST_NAME).write_text(
        json.dumps({"source": {"path": "../secret.mp4"}}), encoding="utf-8")

    assert project_store.resolve_project_input(str(project)) is None


def test_resolve_project_input_rejects_absolute_escape(tmp_path):
    root = tmp_path / "VIRALS"
    project = root / "proj"
    project.mkdir(parents=True)
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(b"secret")
    (project / project_store.MANIFEST_NAME).write_text(
        json.dumps({"source": {"path": str(outside)}}), encoding="utf-8")

    assert project_store.resolve_project_input(str(project)) is None
    assert project_store.resolve_project_input(str(project), allowed_root=str(root)) is None


def test_resolve_project_input_rejects_symlink_escape(tmp_path):
    root = tmp_path / "VIRALS"
    project = root / "proj"
    project.mkdir(parents=True)
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(b"secret")
    link = project / "input_link.mp4"
    link.symlink_to(outside)
    (project / project_store.MANIFEST_NAME).write_text(
        json.dumps({"source": {"path": "input_link.mp4"}}), encoding="utf-8")

    assert project_store.resolve_project_input(str(project)) is None


def test_resolve_project_input_allows_explicit_external_reference(tmp_path):
    root = tmp_path / "VIRALS"
    project = root / "proj"
    project.mkdir(parents=True)
    source = tmp_path / "recording.mp4"
    source.write_bytes(b"video")
    (project / project_store.MANIFEST_NAME).write_text(json.dumps({
        "source": {"path": str(source), "managed": False},
        "settings": {"storage": "external_reference"},
    }), encoding="utf-8")

    assert project_store.resolve_project_input(str(project)) == os.path.realpath(source)


def test_safe_project_path_rejects_symlink_escape(tmp_path):
    root = tmp_path / "VIRALS"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside)

    with pytest.raises(ValueError):
        project_store.safe_project_path(str(root), "link")


# ---------------------------------------------------------------------------
# 9. pipeline_engine — non-serialisable result is still a success
# ---------------------------------------------------------------------------

class _Opaque:
    def __init__(self, value):
        self.value = value


def test_pipeline_records_success_for_non_serialisable_result(tmp_path):
    state_path = tmp_path / "run.json"
    engine = PipelineEngine(state_path)
    engine.register("odd", lambda context, results: _Opaque(1), retries=1)
    engine.register("downstream", lambda context, results: {"values": {1, 2, 3}},
                    deps=["odd"])

    result = engine.run()

    assert result["odd"].value == 1  # in-memory callers keep the real object
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["stages"]["odd"]["status"] == "success"
    assert persisted["stages"]["downstream"]["status"] == "success"
    # a later save must not fail on the stored value
    engine.register("extra", lambda context, results: "ok")
    engine.run(targets=["extra"])
    assert json.loads(state_path.read_text(encoding="utf-8"))["stages"]["extra"]["status"] == "success"


def test_pipeline_json_safe_helper_falls_back_to_repr():
    class Unserialisable:
        def __repr__(self):
            return "<unserialisable>"

    assert pipeline_engine._json_safe(Unserialisable()) == "<unserialisable>"
    assert pipeline_engine._json_safe({"a": 1}) == {"a": 1}
