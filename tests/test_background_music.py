"""Direct tests for scripts/background_music.py.

ffmpeg/ffprobe are never run for real — subprocess.run is stubbed and the
ffmpeg command is asserted, not executed. Real files are only tiny
placeholders inside tmp_path.
"""

import subprocess
import sys

from scripts import background_music


class _FakeResult:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


# ---------------------------------------------------------------- find_music


def test_find_music_file_prefers_explicit_path(tmp_path):
    music = tmp_path / "song.mp3"
    music.write_bytes(b"x")
    assert background_music.find_music_file(str(music)) == str(music)


def test_find_music_file_explicit_missing_falls_through(tmp_path):
    folder = tmp_path / "music"
    folder.mkdir()
    song = folder / "a.wav"
    song.write_bytes(b"x")
    found = background_music.find_music_file(
        str(tmp_path / "nope.mp3"), project_folder=str(tmp_path)
    )
    assert found == str(song)


def test_find_music_file_searches_project_music_folder_sorted(tmp_path):
    folder = tmp_path / "music"
    folder.mkdir()
    (folder / "z.mp3").write_bytes(b"x")
    (folder / "a.mp3").write_bytes(b"x")
    (folder / "notes.txt").write_bytes(b"x")
    found = background_music.find_music_file(project_folder=str(tmp_path))
    assert found == str(folder / "a.mp3")


def test_find_music_file_ignores_non_audio_extensions(tmp_path):
    folder = tmp_path / "music"
    folder.mkdir()
    (folder / "readme.txt").write_bytes(b"x")
    assert background_music.find_music_file(project_folder=str(tmp_path)) is None


def test_find_music_file_accepts_all_audio_extensions(tmp_path):
    folder = tmp_path / "music"
    folder.mkdir()
    for ext in (".m4a", ".ogg", ".flac", ".aac"):
        (folder / ("song" + ext)).write_bytes(b"x")
    found = background_music.find_music_file(project_folder=str(tmp_path))
    assert found is not None
    assert found.endswith(".aac")  # sorted order: aac < flac < m4a < ogg


def test_find_music_file_returns_none_when_nothing_exists(tmp_path):
    assert (
        background_music.find_music_file(
            str(tmp_path / "nope.mp3"), project_folder=str(tmp_path)
        )
        is None
    )


def test_find_music_file_uses_local_music_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    local = tmp_path / "music"
    local.mkdir()
    song = local / "local.mp3"
    song.write_bytes(b"x")
    assert background_music.find_music_file() == str(song)


def test_find_music_file_explicit_music_dir(tmp_path):
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    song = folder / "track.mp3"
    song.write_bytes(b"x")
    assert background_music.find_music_file(music_dir=str(folder)) == str(song)


# ------------------------------------------------------------ _has_audio_stream


def _stub_run(monkeypatch, handler):
    monkeypatch.setattr(subprocess, "run", handler)


def test_has_audio_stream_true(monkeypatch):
    _stub_run(monkeypatch, lambda *a, **k: _FakeResult(stdout="0\n"))
    assert background_music._has_audio_stream("clip.mp4") is True


def test_has_audio_stream_false_on_empty_stdout(monkeypatch):
    _stub_run(monkeypatch, lambda *a, **k: _FakeResult(stdout="  \n"))
    assert background_music._has_audio_stream("clip.mp4") is False


def test_has_audio_stream_none_on_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("ffprobe missing")

    _stub_run(monkeypatch, boom)
    assert background_music._has_audio_stream("clip.mp4") is None


# ------------------------------------------------------- apply_background_music


def _make_clip(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video-bytes")
    return clip


def _make_music(tmp_path):
    music = tmp_path / "song.mp3"
    music.write_bytes(b"music-bytes")
    return music


def test_apply_missing_video_reports_not_ok(tmp_path):
    report = background_music.apply_background_music(
        str(tmp_path / "gone.mp4"),
        str(tmp_path / "song.mp3"),
        str(tmp_path / "out.mp4"),
    )
    assert report["ok"] is False
    assert not (tmp_path / "out.mp4").exists()


def test_apply_missing_music_copies_clip_unchanged(tmp_path):
    clip = _make_clip(tmp_path)
    out = tmp_path / "out.mp4"
    report = background_music.apply_background_music(
        str(clip), str(tmp_path / "gone.mp3"), str(out)
    )
    assert report["ok"] is True
    assert report["skipped"] == "no music file found"
    assert out.read_bytes() == b"video-bytes"


def test_apply_none_music_copies_clip_unchanged(tmp_path):
    clip = _make_clip(tmp_path)
    out = tmp_path / "out.mp4"
    report = background_music.apply_background_music(str(clip), None, str(out))
    assert report["ok"] is True
    assert report["skipped"] == "no music file found"
    assert out.read_bytes() == b"video-bytes"


def test_apply_ffprobe_failure_copies_and_errors(tmp_path, monkeypatch):
    clip = _make_clip(tmp_path)
    music = _make_music(tmp_path)
    out = tmp_path / "out.mp4"

    def boom(*a, **k):
        raise OSError("ffprobe missing")

    _stub_run(monkeypatch, boom)
    report = background_music.apply_background_music(str(clip), str(music), str(out))
    assert report["ok"] is False
    assert "ffprobe" in report["error"]
    assert out.read_bytes() == b"video-bytes"


def test_apply_no_audio_stream_copies_clip(tmp_path, monkeypatch):
    clip = _make_clip(tmp_path)
    music = _make_music(tmp_path)
    out = tmp_path / "out.mp4"
    _stub_run(monkeypatch, lambda *a, **k: _FakeResult(stdout=""))
    report = background_music.apply_background_music(str(clip), str(music), str(out))
    assert report["ok"] is True
    assert report["skipped"] == "clip has no audio stream"
    assert out.read_bytes() == b"video-bytes"


def test_apply_happy_path_builds_ducking_command(tmp_path, monkeypatch):
    clip = _make_clip(tmp_path)
    music = _make_music(tmp_path)
    out = tmp_path / "out.mp4"
    cmds = []

    def fake_run(cmd, **kwargs):
        cmds.append(cmd)
        if cmd[0] == "ffprobe":
            return _FakeResult(stdout="0\n")
        return _FakeResult()

    _stub_run(monkeypatch, fake_run)
    report = background_music.apply_background_music(str(clip), str(music), str(out))
    assert report["ok"] is True
    assert report["music"] == "song.mp3"
    assert report["duck"] == {"threshold": 0.02, "ratio": 10}
    assert report["limiter"] == {"limit": 0.95, "attack_ms": 5, "release_ms": 50}
    ffmpeg_cmds = [c for c in cmds if c[0] == "ffmpeg"]
    assert len(ffmpeg_cmds) == 1
    cmd = ffmpeg_cmds[0]
    assert "-stream_loop" in cmd and "-1" in cmd
    assert "-shortest" in cmd
    assert "-map" in cmd and "[a]" in cmd
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "sidechaincompress" in fc
    assert "volume=0.15" in fc
    assert "amix=inputs=2" in fc


def test_apply_clamps_volume_threshold_and_ratio(tmp_path, monkeypatch):
    clip = _make_clip(tmp_path)
    music = _make_music(tmp_path)
    out = tmp_path / "out.mp4"
    cmds = []

    def fake_run(cmd, **kwargs):
        cmds.append(cmd)
        if cmd[0] == "ffprobe":
            return _FakeResult(stdout="0\n")
        return _FakeResult()

    _stub_run(monkeypatch, fake_run)
    report = background_music.apply_background_music(
        str(clip),
        str(music),
        str(out),
        music_volume=0.9,
        duck_threshold=5.0,
        duck_ratio=99,
    )
    assert report["ok"] is True
    cmd = [c for c in cmds if c[0] == "ffmpeg"][0]
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "volume=0.35" in fc  # clamped from 0.9
    assert "threshold=0.5" in fc  # clamped from 5.0
    assert "ratio=20.0" in fc or "ratio=20" in fc  # clamped from 99
    assert report["duck"] == {"threshold": 0.5, "ratio": 20.0}


def test_apply_clamps_tiny_values_up(tmp_path, monkeypatch):
    clip = _make_clip(tmp_path)
    music = _make_music(tmp_path)
    out = tmp_path / "out.mp4"
    cmds = []

    def fake_run(cmd, **kwargs):
        cmds.append(cmd)
        if cmd[0] == "ffprobe":
            return _FakeResult(stdout="0\n")
        return _FakeResult()

    _stub_run(monkeypatch, fake_run)
    report = background_music.apply_background_music(
        str(clip),
        str(music),
        str(out),
        music_volume=0.001,
        duck_threshold=0.0,
        duck_ratio=0.1,
    )
    assert report["ok"] is True
    cmd = [c for c in cmds if c[0] == "ffmpeg"][0]
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "volume=0.02" in fc
    assert "threshold=0.001" in fc
    assert "ratio=1.0" in fc


def test_apply_ffmpeg_failure_falls_back_to_copy(tmp_path, monkeypatch):
    clip = _make_clip(tmp_path)
    music = _make_music(tmp_path)
    out = tmp_path / "out.mp4"

    def fake_run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return _FakeResult(stdout="0\n")
        raise subprocess.CalledProcessError(1, cmd, stderr=b"boom")

    _stub_run(monkeypatch, fake_run)
    report = background_music.apply_background_music(str(clip), str(music), str(out))
    assert report["ok"] is False
    assert "error" in report
    assert out.read_bytes() == b"video-bytes"


# ---------------------------------------------------------------------- main


def test_main_json_reports_skipped_when_no_music(tmp_path, monkeypatch, capsys):
    clip = _make_clip(tmp_path)
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(
        sys,
        "argv",
        ["background_music.py", "--video", str(clip), "--out", str(out), "--json"],
    )
    # Missing music is a safe copy-through, so main() reports success (0).
    assert background_music.main() == 0
    out_text = capsys.readouterr().out
    assert "no music file found" in out_text
    assert out.read_bytes() == b"video-bytes"


def test_apply_same_file_no_music_succeeds_without_copy(tmp_path, monkeypatch):
    # Regression test: copying the clip onto itself used to raise
    # SameFileError inside the no-music path, turning a safe skip into a
    # failure. The copy is now skipped and the clip is left untouched.
    clip = _make_clip(tmp_path)
    calls = []
    monkeypatch.setattr(background_music.shutil, "copy2", lambda *a: calls.append(a))
    report = background_music.apply_background_music(str(clip), None, str(clip))
    assert report["ok"] is True
    assert report["skipped"] == "no music file found"
    assert "error" not in report
    assert calls == []
    assert clip.read_bytes() == b"video-bytes"


def test_apply_same_file_via_different_spelling_skips_copy(tmp_path, monkeypatch):
    clip = _make_clip(tmp_path)
    calls = []
    monkeypatch.setattr(background_music.shutil, "copy2", lambda *a: calls.append(a))
    monkeypatch.chdir(tmp_path)
    report = background_music.apply_background_music("clip.mp4", None, "./clip.mp4")
    assert report["ok"] is True
    assert calls == []
    assert clip.read_bytes() == b"video-bytes"


def test_apply_same_file_no_audio_succeeds_without_copy(tmp_path, monkeypatch):
    clip = _make_clip(tmp_path)
    music = _make_music(tmp_path)
    calls = []
    monkeypatch.setattr(background_music.shutil, "copy2", lambda *a: calls.append(a))
    _stub_run(monkeypatch, lambda *a, **k: _FakeResult(stdout=""))
    report = background_music.apply_background_music(str(clip), str(music), str(clip))
    assert report["ok"] is True
    assert report["skipped"] == "clip has no audio stream"
    assert "error" not in report
    assert calls == []
    assert clip.read_bytes() == b"video-bytes"


def test_apply_same_file_probe_failure_keeps_error_without_copy(tmp_path, monkeypatch):
    clip = _make_clip(tmp_path)
    music = _make_music(tmp_path)
    calls = []
    monkeypatch.setattr(background_music.shutil, "copy2", lambda *a: calls.append(a))

    def boom(*a, **k):
        raise OSError("ffprobe missing")

    _stub_run(monkeypatch, boom)
    report = background_music.apply_background_music(str(clip), str(music), str(clip))
    assert report["ok"] is False
    assert "ffprobe" in report["error"]
    assert calls == []
    assert clip.read_bytes() == b"video-bytes"


def test_main_defaults_out_to_video_and_succeeds(tmp_path, monkeypatch, capsys):
    # Without --out, main() used to copy the clip onto itself: the report
    # was not ok and main() returned 1. The copy is now skipped, so a
    # no-music run reports success and leaves the clip untouched.
    clip = _make_clip(tmp_path)
    monkeypatch.setattr(sys, "argv", ["background_music.py", "--video", str(clip)])
    assert background_music.main() == 0
    assert "no music file found" in capsys.readouterr().out
    assert clip.read_bytes() == b"video-bytes"
