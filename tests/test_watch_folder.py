from argparse import Namespace

from scripts import watch_folder
from scripts.watch_folder import WatchQueue, discover, is_stable, resolve_runtime


def test_queue_claim_and_deduplicate(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    queue = WatchQueue(tmp_path / "state.sqlite3")
    stat = video.stat()
    assert queue.claim(video, stat) is True
    assert queue.claim(video, stat) is False
    queue.finish(video, "success", 0, tmp_path / "job.log")
    assert queue.status(video, stat) == "success"
    queue.close()


def test_changed_file_can_be_requeued(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"a")
    queue = WatchQueue(tmp_path / "state.sqlite3")
    first = video.stat()
    assert queue.claim(video, first)
    queue.finish(video, "success", 0, tmp_path / "job.log")
    video.write_bytes(b"changed-size")
    assert queue.status(video, video.stat()) is None
    assert queue.claim(video, video.stat())
    queue.close()


def test_discover_filters_video_extensions(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.txt").write_text("x")
    assert [p.name for p in discover(tmp_path, False)] == ["a.mp4"]


def test_unstable_or_recent_file_is_skipped(tmp_path):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    assert is_stable(video, 3600) is None


def test_auto_runtime_uses_detected_gpu_profile(tmp_path):
    profile = tmp_path / "gpu_profile.json"
    profile.write_text('{"vram_tier":"6gb","transcription":{"device":"cuda","model":"medium"}}', encoding="utf-8")
    args = Namespace(gpu_profile=profile, device="auto", model="auto")
    assert resolve_runtime(args) == ("cuda", "medium", "6gb")


def test_auto_runtime_falls_back_safely_without_profile(tmp_path):
    args = Namespace(gpu_profile=tmp_path / "missing.json", device="auto", model="auto")
    assert resolve_runtime(args) == ("auto", "medium", "unknown")


def test_explicit_runtime_overrides_profile(tmp_path):
    profile = tmp_path / "gpu_profile.json"
    profile.write_text('{"vram_tier":"12gb","transcription":{"device":"cuda","model":"large-v3-turbo"}}', encoding="utf-8")
    args = Namespace(gpu_profile=profile, device="cpu", model="small")
    assert resolve_runtime(args) == ("cpu", "small", "12gb")


def test_scan_skips_file_removed_before_stat(tmp_path, monkeypatch):
    input_dir = tmp_path / "inbox"
    input_dir.mkdir()
    app = tmp_path / "main.py"
    app.write_text("", encoding="utf-8")
    missing = input_dir / "vanished.mp4"
    monkeypatch.setattr(watch_folder, "discover", lambda *_args: [missing])
    result = watch_folder.main([
        "--input", str(input_dir), "--app", str(app),
        "--state-dir", str(tmp_path / ".state"), "--once",
    ])
    assert result == 0
