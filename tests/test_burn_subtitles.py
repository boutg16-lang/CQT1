"""Direct tests for scripts/burn_subtitles.py.

ffmpeg is never run for real — subprocess.run is stubbed and the ffmpeg
command is asserted, not executed. Real files are only tiny placeholders
inside tmp_path.
"""

import os
import subprocess
import sys
import types

from scripts import burn_subtitles


class _FakeResult:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _stub_encoder(monkeypatch, ffmpeg_stdout, works):
    """Stub subprocess.run (ffmpeg -encoders) and scripts.ffmpeg_toolchain."""
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _FakeResult(stdout=ffmpeg_stdout))
    stub = types.ModuleType("scripts.ffmpeg_toolchain")
    stub.encoder_works = lambda codec: works.get(codec, False)
    monkeypatch.setitem(sys.modules, "scripts.ffmpeg_toolchain", stub)


# ------------------------------------------------------- _ffmpeg_filter_value


def test_filter_value_escapes_colons_and_quotes():
    assert burn_subtitles._ffmpeg_filter_value("C:\\Videos\\my file.ass") == \
        "C\\:/Videos/my file.ass"
    assert burn_subtitles._ffmpeg_filter_value("it's.ass") == "it\\'s.ass"
    assert burn_subtitles._ffmpeg_filter_value("D:\\a:b\\c.ass") == \
        "D\\:/a\\:b/c.ass"


def test_filter_value_empty_and_none():
    assert burn_subtitles._ffmpeg_filter_value("") == ""
    assert burn_subtitles._ffmpeg_filter_value(None) == ""


def test_filter_value_plain_path_unchanged():
    assert burn_subtitles._ffmpeg_filter_value("/tmp/subs/a.ass") == "/tmp/subs/a.ass"


# ------------------------------------------------------------ _subtitles_filter


def test_subtitles_filter_without_fonts_dir(monkeypatch):
    monkeypatch.setattr(burn_subtitles, "_fonts_dir", lambda: None)
    vf = burn_subtitles._subtitles_filter("/tmp/subs/a.ass")
    assert vf == "subtitles='/tmp/subs/a.ass'"
    assert "fontsdir" not in vf


def test_subtitles_filter_adds_fontsdir_when_present(monkeypatch):
    monkeypatch.setattr(burn_subtitles, "_fonts_dir", lambda: "/repo/fonts")
    vf = burn_subtitles._subtitles_filter("/tmp/subs/a.ass")
    assert vf == "subtitles='/tmp/subs/a.ass':fontsdir='/repo/fonts'"


def test_subtitles_filter_escapes_paths(monkeypatch):
    monkeypatch.setattr(burn_subtitles, "_fonts_dir", lambda: None)
    vf = burn_subtitles._subtitles_filter("C:\\My Subs\\a b.ass")
    assert vf == "subtitles='C\\:/My Subs/a b.ass'"


def test_fonts_dir_returns_none_when_missing(monkeypatch):
    monkeypatch.setattr(os.path, "isdir", lambda p: False)
    assert burn_subtitles._fonts_dir() is None


def test_fonts_dir_returns_repo_fonts_when_present(monkeypatch, tmp_path):
    # _fonts_dir derives from the module file location, so only the isdir
    # check is stubbed here; the returned path must end in "fonts".
    monkeypatch.setattr(os.path, "isdir", lambda p: p.endswith("fonts"))
    assert burn_subtitles._fonts_dir().endswith("fonts")


# -------------------------------------------------------- _detect_best_encoder


def test_detect_best_encoder_prefers_nvenc(monkeypatch):
    _stub_encoder(monkeypatch, "h264_nvenc h264_amf h264_qsv",
                  {"h264_nvenc": True})
    assert burn_subtitles._detect_best_encoder() == ("h264_nvenc", "p1")


def test_detect_best_encoder_skips_listed_but_broken_encoder(monkeypatch):
    # nvenc is compiled in but encoder_works says it fails on this box.
    _stub_encoder(monkeypatch, "h264_nvenc h264_amf h264_qsv",
                  {"h264_nvenc": False, "h264_amf": True})
    assert burn_subtitles._detect_best_encoder() == ("h264_amf", "quality")


def test_detect_best_encoder_falls_back_to_qsv(monkeypatch):
    _stub_encoder(monkeypatch, "h264_qsv h264_videotoolbox",
                  {"h264_qsv": True})
    assert burn_subtitles._detect_best_encoder() == ("h264_qsv", "veryfast")


def test_detect_best_encoder_cpu_when_nothing_matches(monkeypatch):
    _stub_encoder(monkeypatch, "libx264", {})
    assert burn_subtitles._detect_best_encoder() == ("libx264", "ultrafast")


def test_detect_best_encoder_cpu_when_ffmpeg_missing(monkeypatch):
    def boom(*a, **k):
        raise OSError("ffmpeg not found")

    monkeypatch.setattr(subprocess, "run", boom)
    assert burn_subtitles._detect_best_encoder() == ("libx264", "ultrafast")


def test_detect_best_encoder_cpu_when_toolchain_has_no_encoder_works(monkeypatch):
    # scripts.ffmpeg_toolchain imports but lacks encoder_works -> the
    # fallback treats every encoder as not working.
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _FakeResult(stdout="h264_nvenc h264_amf"))
    stub = types.ModuleType("scripts.ffmpeg_toolchain")
    monkeypatch.setitem(sys.modules, "scripts.ffmpeg_toolchain", stub)
    assert burn_subtitles._detect_best_encoder() == ("libx264", "ultrafast")


# ------------------------------------------------------------ burn_video_file


def _stub_ffmpeg(monkeypatch, fail_hw=False, fail_cpu=False):
    """Stub subprocess.run for ffmpeg invocations; return the commands seen."""
    cmds = []

    def fake_run(cmd, **kwargs):
        cmds.append(cmd)
        encoder = cmd[cmd.index("-c:v") + 1]
        if encoder != "libx264" and fail_hw:
            raise subprocess.CalledProcessError(1, cmd, stderr=b"hw fail")
        if encoder == "libx264" and fail_cpu:
            raise subprocess.CalledProcessError(1, cmd, stderr=b"cpu fail")
        return _FakeResult()

    # _detect_best_encoder reads the -encoders output; give it nvenc.
    def dispatch(cmd, **kwargs):
        if cmd[:2] == ["ffmpeg", "-hide_banner"]:
            return _FakeResult(stdout="h264_nvenc")
        return fake_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", dispatch)
    return cmds


def _fake_toolchain(monkeypatch):
    stub = types.ModuleType("scripts.ffmpeg_toolchain")
    stub.encoder_works = lambda codec: codec == "h264_nvenc"
    monkeypatch.setitem(sys.modules, "scripts.ffmpeg_toolchain", stub)


def test_burn_video_file_hardware_success(monkeypatch, tmp_path):
    _fake_toolchain(monkeypatch)
    cmds = _stub_ffmpeg(monkeypatch)
    ok, msg = burn_subtitles.burn_video_file(
        str(tmp_path / "v.mp4"), str(tmp_path / "s.ass"), str(tmp_path / "o.mp4"))
    assert ok is True
    assert msg == "h264_nvenc Success"
    assert len(cmds) == 1
    cmd = cmds[0]
    assert "-vf" in cmd
    assert "subtitles=" in cmd[cmd.index("-vf") + 1]
    assert "-b:v" in cmd and "5M" in cmd
    assert "-pix_fmt" in cmd and "yuv420p" in cmd
    assert "-c:a" in cmd and "copy" in cmd


def test_burn_video_file_hw_failure_falls_back_to_cpu(monkeypatch, tmp_path, capsys):
    _fake_toolchain(monkeypatch)
    cmds = _stub_ffmpeg(monkeypatch, fail_hw=True)
    ok, msg = burn_subtitles.burn_video_file(
        str(tmp_path / "v.mp4"), str(tmp_path / "s.ass"), str(tmp_path / "o.mp4"))
    assert ok is True
    assert msg == "CPU Success"
    assert len(cmds) == 2
    assert cmds[1][cmds[1].index("-c:v") + 1] == "libx264"
    assert "Hardware encoder failed" in capsys.readouterr().out


def test_burn_video_file_both_fail_reports_fatal(monkeypatch, tmp_path):
    _fake_toolchain(monkeypatch)
    _stub_ffmpeg(monkeypatch, fail_hw=True, fail_cpu=True)
    ok, msg = burn_subtitles.burn_video_file(
        str(tmp_path / "v.mp4"), str(tmp_path / "s.ass"), str(tmp_path / "o.mp4"))
    assert ok is False
    assert "Fatal error burning subtitles" in msg
    assert "v.mp4" in msg
    assert "cpu fail" in msg  # ffmpeg stderr is attached to the message


def test_burn_video_file_libx264_detected_goes_straight_to_cpu(monkeypatch, tmp_path):
    _fake_toolchain(monkeypatch)
    cmds = _stub_ffmpeg(monkeypatch)
    # Force the CPU path: pretend no hardware encoder is available.
    monkeypatch.setattr(burn_subtitles, "_detect_best_encoder",
                        lambda: ("libx264", "ultrafast"))
    ok, msg = burn_subtitles.burn_video_file(
        str(tmp_path / "v.mp4"), str(tmp_path / "s.ass"), str(tmp_path / "o.mp4"))
    assert ok is True
    assert msg == "CPU Success"
    assert len(cmds) == 1
    assert cmds[0][cmds[0].index("-c:v") + 1] == "libx264"


def test_burn_video_file_prefer_no_hw_skips_detection(monkeypatch, tmp_path):
    _fake_toolchain(monkeypatch)
    cmds = _stub_ffmpeg(monkeypatch)
    detected = []
    monkeypatch.setattr(burn_subtitles, "_detect_best_encoder",
                        lambda: detected.append(1) or ("h264_nvenc", "p1"))
    ok, msg = burn_subtitles.burn_video_file(
        str(tmp_path / "v.mp4"), str(tmp_path / "s.ass"), str(tmp_path / "o.mp4"),
        prefer_hardware_acceleration=False)
    assert ok is True
    assert msg == "CPU Success"
    assert detected == []  # detection never ran
    assert len(cmds) == 1


# ---------------------------------------------------------------------- burn


def _project(tmp_path, videos, subs):
    final = tmp_path / "final"
    final.mkdir()
    for name in videos:
        (final / name).write_bytes(b"v")
    subs_dir = tmp_path / "subs_ass"
    subs_dir.mkdir()
    for name in subs:
        (subs_dir / name).write_text("sub", encoding="utf-8")
    return tmp_path


def test_burn_missing_final_folder_returns_none(tmp_path, capsys):
    assert burn_subtitles.burn(str(tmp_path)) is None
    assert "Final video folder not found" in capsys.readouterr().out
    assert (tmp_path / "burned_sub").is_dir()  # output folder is created first


def test_burn_empty_final_folder_returns_none(tmp_path, capsys):
    (tmp_path / "final").mkdir()
    assert burn_subtitles.burn(str(tmp_path)) is None
    assert "No files found" in capsys.readouterr().out


def test_burn_skips_temp_video_no_audio(tmp_path, monkeypatch, capsys):
    _project(tmp_path, ["temp_video_no_audio.mp4"], [])
    calls = []
    monkeypatch.setattr(burn_subtitles, "burn_video_file",
                        lambda *a, **k: calls.append(a) or (True, "ok"))
    burn_subtitles.burn(str(tmp_path))
    assert calls == []


def test_burn_prefers_processed_subtitle(monkeypatch, tmp_path):
    _project(tmp_path, ["clip1.mp4"], ["clip1_processed.ass"])
    calls = []
    monkeypatch.setattr(burn_subtitles, "burn_video_file",
                        lambda *a, **k: calls.append(a) or (True, "ok"))
    burn_subtitles.burn(str(tmp_path))
    assert len(calls) == 1
    assert calls[0][1].endswith("clip1_processed.ass")
    assert calls[0][2].endswith("clip1_subtitled.mp4")


def test_burn_prefers_polished_folder(monkeypatch, tmp_path):
    _project(tmp_path, ["clip1.mp4"], ["clip1.ass"])
    polished = tmp_path / "final_polished"
    polished.mkdir()
    (polished / "clip1.mp4").write_bytes(b"v")
    calls = []
    monkeypatch.setattr(burn_subtitles, "burn_video_file",
                        lambda *a, **k: calls.append(a) or (True, "ok"))
    burn_subtitles.burn(str(tmp_path))
    assert len(calls) == 1
    assert "final_polished" in calls[0][0]


def test_burn_reports_missing_subtitle(monkeypatch, tmp_path, capsys):
    _project(tmp_path, ["clip1.mp4"], [])
    calls = []
    monkeypatch.setattr(burn_subtitles, "burn_video_file",
                        lambda *a, **k: calls.append(a) or (True, "ok"))
    burn_subtitles.burn(str(tmp_path))
    assert calls == []
    assert "Subtitle not found for: clip1" in capsys.readouterr().out


def test_burn_reports_failure(monkeypatch, tmp_path, capsys):
    _project(tmp_path, ["clip1.mp4"], ["clip1.ass"])
    monkeypatch.setattr(burn_subtitles, "burn_video_file",
                        lambda *a, **k: (False, "boom"))
    burn_subtitles.burn(str(tmp_path))
    assert "Fail: boom" in capsys.readouterr().out


def test_burn_non_video_files_ignored(monkeypatch, tmp_path):
    _project(tmp_path, ["notes.txt", "clip1.mp4"], ["clip1.ass"])
    calls = []
    monkeypatch.setattr(burn_subtitles, "burn_video_file",
                        lambda *a, **k: calls.append(a) or (True, "ok"))
    burn_subtitles.burn(str(tmp_path))
    assert len(calls) == 1
