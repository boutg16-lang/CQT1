"""Direct tests for scripts/branding.py.

ffmpeg/ffprobe are never run for real — subprocess.run is stubbed and the
ffmpeg command is asserted, not executed. Real files are only tiny
placeholders inside tmp_path.
"""

import os
import subprocess
import sys

import pytest

from scripts import branding


class _FakeResult:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _make_video(tmp_path, name="clip.mp4"):
    video = tmp_path / name
    video.write_bytes(b"video-bytes")
    return video


# -------------------------------------------------------------- _probe_streams


def _probe_stub(monkeypatch, video_line, audio_line=None, fail=False):
    def fake_run(cmd, **kwargs):
        if fail:
            raise OSError("ffprobe missing")
        if "-select_streams" in cmd and "a" in cmd:
            return _FakeResult(stdout=audio_line if audio_line is not None else "0\n")
        return _FakeResult(stdout=video_line)

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_probe_streams_parses_dimensions_and_fps(monkeypatch):
    _probe_stub(monkeypatch, "1920,1080,30/1\n")
    assert branding._probe_streams("clip.mp4") == (1920, 1080, 30.0, True)


def test_probe_streams_fractional_fps(monkeypatch):
    _probe_stub(monkeypatch, "1080,1920,30000/1001\n")
    w, h, fps, audio = branding._probe_streams("clip.mp4")
    assert (w, h) == (1080, 1920)
    assert fps == pytest.approx(29.97, abs=0.01)
    assert audio is True


def test_probe_streams_zero_denominator_falls_back_to_30(monkeypatch):
    _probe_stub(monkeypatch, "1920,1080,30/0\n")
    assert branding._probe_streams("clip.mp4")[2] == 30.0


def test_probe_streams_no_audio_stream(monkeypatch):
    _probe_stub(monkeypatch, "1920,1080,30/1\n", audio_line="")
    assert branding._probe_streams("clip.mp4") == (1920, 1080, 30.0, False)


def test_probe_streams_malformed_line(monkeypatch):
    _probe_stub(monkeypatch, "1920,1080\n")
    assert branding._probe_streams("clip.mp4") == (None, None, None, True)


def test_probe_streams_empty_output(monkeypatch):
    _probe_stub(monkeypatch, "")
    assert branding._probe_streams("clip.mp4") == (None, None, None, True)


def test_probe_streams_failure_returns_all_none(monkeypatch):
    _probe_stub(monkeypatch, "", fail=True)
    assert branding._probe_streams("clip.mp4") == (None, None, None, None)


# ------------------------------------------------------------------- _has_audio


def test_has_audio_true_false_and_error(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeResult(stdout="0\n"))
    assert branding._has_audio("clip.mp4") is True
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeResult(stdout=""))
    assert branding._has_audio("clip.mp4") is False

    def boom(*a, **k):
        raise OSError("ffprobe missing")

    monkeypatch.setattr(subprocess, "run", boom)
    assert branding._has_audio("clip.mp4") is True  # fail-open default


# --------------------------------------------------------------- apply_watermark


def _stub_ffmpeg_success(monkeypatch, out_paths):
    """Stub subprocess.run so ffmpeg 'creates' its output file."""
    cmds = []

    def fake_run(cmd, **kwargs):
        cmds.append(cmd)
        if cmd[0] == "ffprobe":
            return _FakeResult(stdout="1080,1920,30/1\n")
        out = cmd[-1]
        if out.endswith(".mp4"):
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
            with open(out, "wb") as f:
                f.write(b"out-bytes")
        return _FakeResult()

    monkeypatch.setattr(subprocess, "run", fake_run)
    return cmds


def test_watermark_missing_video_returns_false(tmp_path):
    assert (
        branding.apply_watermark(
            str(tmp_path / "gone.mp4"),
            str(tmp_path / "o.mp4"),
            str(tmp_path / "logo.png"),
        )
        is False
    )


def test_watermark_missing_logo_copies_clip(tmp_path):
    video = _make_video(tmp_path)
    out = tmp_path / "out.mp4"
    assert (
        branding.apply_watermark(str(video), str(out), str(tmp_path / "gone.png"))
        is False
    )
    assert out.read_bytes() == b"video-bytes"


def test_watermark_none_logo_copies_clip(tmp_path):
    video = _make_video(tmp_path)
    out = tmp_path / "out.mp4"
    assert branding.apply_watermark(str(video), str(out), None) is False
    assert out.read_bytes() == b"video-bytes"


def test_watermark_happy_path_builds_overlay_command(tmp_path, monkeypatch):
    video = _make_video(tmp_path)
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"png")
    out = tmp_path / "out.mp4"
    cmds = _stub_ffmpeg_success(monkeypatch, out)
    assert branding.apply_watermark(str(video), str(out), str(logo)) is True
    ffmpeg_cmds = [c for c in cmds if c[0] == "ffmpeg"]
    assert len(ffmpeg_cmds) == 1
    cmd = ffmpeg_cmds[0]
    fc = cmd[cmd.index("-filter_complex") + 1]
    # 1080 * 0.12 = 129 px wide logo, 0.9 opacity, bottom-right overlay.
    assert "scale=129:-1" in fc
    assert "colorchannelmixer=aa=0.9" in fc
    assert "overlay=W-w-10:H-h-10" in fc
    assert "-map" in cmd and "0:a?" in cmd
    assert "-c:v" in cmd and "libx264" in cmd
    assert out.read_bytes() == b"out-bytes"


def test_watermark_positions(monkeypatch, tmp_path):
    video = _make_video(tmp_path)
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"png")
    for position, overlay in branding.POSITIONS.items():
        out = tmp_path / ("out_" + position.replace("-", "_") + ".mp4")
        cmds = _stub_ffmpeg_success(monkeypatch, out)
        assert (
            branding.apply_watermark(str(video), str(out), str(logo), position=position)
            is True
        )
        fc = [c for c in cmds if c[0] == "ffmpeg"][0]
        fc = fc[fc.index("-filter_complex") + 1]
        assert "overlay={}".format(overlay) in fc


def test_watermark_unknown_position_falls_back_to_bottom_right(tmp_path, monkeypatch):
    video = _make_video(tmp_path)
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"png")
    out = tmp_path / "out.mp4"
    cmds = _stub_ffmpeg_success(monkeypatch, out)
    assert (
        branding.apply_watermark(str(video), str(out), str(logo), position="nowhere")
        is True
    )
    fc = [c for c in cmds if c[0] == "ffmpeg"][0]
    fc = fc[fc.index("-filter_complex") + 1]
    assert "overlay=W-w-10:H-h-10" in fc


def test_watermark_logo_width_minimum_40(tmp_path, monkeypatch):
    video = _make_video(tmp_path)
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"png")
    out = tmp_path / "out.mp4"
    cmds = []

    # Tiny video (100 px wide): 100 * 0.12 = 12 -> clamped to 40.
    def fake_run(cmd, **kwargs):
        cmds.append(cmd)
        if cmd[0] == "ffprobe":
            return _FakeResult(stdout="100,100,30/1\n")
        with open(cmd[-1], "wb") as f:
            f.write(b"out-bytes")
        return _FakeResult()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert (
        branding.apply_watermark(str(video), str(out), str(logo), size_fraction=0.12)
        is True
    )
    fc = [c for c in cmds if c[0] == "ffmpeg"][0]
    fc = fc[fc.index("-filter_complex") + 1]
    assert "scale=40:-1" in fc


def test_watermark_ffmpeg_failure_falls_back_to_copy(tmp_path, monkeypatch):
    video = _make_video(tmp_path)
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"png")
    out = tmp_path / "out.mp4"

    def fake_run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return _FakeResult(stdout="1080,1920,30/1\n")
        raise subprocess.CalledProcessError(1, cmd, stderr=b"boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert branding.apply_watermark(str(video), str(out), str(logo)) is False
    assert out.read_bytes() == b"video-bytes"


# -------------------------------------------------------------- _normalize_to


def test_normalize_to_builds_scale_pad_command(monkeypatch, tmp_path):
    cmds = []
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **k: cmds.append(cmd) or _FakeResult()
    )
    branding._normalize_to("in.mp4", str(tmp_path / "n.mp4"), (1080, 1920, 30.0, True))
    assert len(cmds) == 1
    cmd = cmds[0]
    vf = cmd[cmd.index("-vf") + 1]
    assert "scale=1080:1920" in vf
    assert "pad=1080:1920" in vf
    assert "fps=30.0" in vf
    assert "-c:a" in cmd and "aac" in cmd


def test_normalize_to_no_audio_uses_an(monkeypatch, tmp_path):
    cmds = []
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **k: cmds.append(cmd) or _FakeResult()
    )
    branding._normalize_to("in.mp4", str(tmp_path / "n.mp4"), (1080, 1920, 30.0, False))
    cmd = cmds[0]
    assert cmd[cmd.index("-c:a") + 1] == "an"


def test_normalize_to_missing_fps_defaults_30(monkeypatch, tmp_path):
    cmds = []
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **k: cmds.append(cmd) or _FakeResult()
    )
    branding._normalize_to("in.mp4", str(tmp_path / "n.mp4"), (None, None, None, True))
    vf = cmds[0][cmds[0].index("-vf") + 1]
    assert "fps=30" in vf


# ------------------------------------------------------------ apply_intro_outro


def test_intro_outro_missing_video_returns_false(tmp_path):
    assert (
        branding.apply_intro_outro(
            str(tmp_path / "gone.mp4"),
            str(tmp_path / "o.mp4"),
            intro=str(tmp_path / "i.mp4"),
        )
        is False
    )


def test_intro_outro_no_assets_copies_clip(tmp_path):
    video = _make_video(tmp_path)
    out = tmp_path / "out.mp4"
    assert branding.apply_intro_outro(str(video), str(out)) is False
    assert out.read_bytes() == b"video-bytes"


def test_intro_outro_missing_assets_copies_clip(tmp_path):
    video = _make_video(tmp_path)
    out = tmp_path / "out.mp4"
    assert (
        branding.apply_intro_outro(
            str(video), str(out), intro=str(tmp_path / "gone.mp4")
        )
        is False
    )
    assert out.read_bytes() == b"video-bytes"


def test_intro_outro_happy_path_concats_normalized_clips(tmp_path, monkeypatch):
    video = _make_video(tmp_path)
    intro = tmp_path / "intro.mp4"
    intro.write_bytes(b"intro")
    outro = tmp_path / "outro.mp4"
    outro.write_bytes(b"outro")
    out = tmp_path / "out.mp4"
    cmds = _stub_ffmpeg_success(monkeypatch, out)
    assert (
        branding.apply_intro_outro(
            str(video), str(out), intro=str(intro), outro=str(outro)
        )
        is True
    )
    concat_cmds = [c for c in cmds if "-f" in c and "concat" in c]
    assert len(concat_cmds) == 1
    assert "-c" in concat_cmds[0] and "copy" in concat_cmds[0]
    # Temp files (normalized clips + concat list) are cleaned up.
    leftovers = [
        p.name for p in tmp_path.iterdir() if "norm" in p.name or "concat" in p.name
    ]
    assert leftovers == []
    assert out.read_bytes() == b"out-bytes"


def test_intro_outro_ffmpeg_failure_falls_back_to_copy(tmp_path, monkeypatch):
    video = _make_video(tmp_path)
    intro = tmp_path / "intro.mp4"
    intro.write_bytes(b"intro")
    out = tmp_path / "out.mp4"

    def fake_run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return _FakeResult(stdout="1080,1920,30/1\n")
        raise subprocess.CalledProcessError(1, cmd, stderr=b"boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert branding.apply_intro_outro(str(video), str(out), intro=str(intro)) is False
    assert out.read_bytes() == b"video-bytes"


# ----------------------------------------------------------------- process_file


def test_process_file_no_branding_copies_through(tmp_path):
    video = _make_video(tmp_path)
    out = tmp_path / "out.mp4"
    report = branding.process_file(str(video), str(out))
    assert report["ok"] is True
    assert report["copy_through"] is True
    assert report["watermark"] is False
    assert report["intro_outro"] is False
    assert out.read_bytes() == b"video-bytes"


def test_process_file_same_path_no_copy_needed(tmp_path):
    video = _make_video(tmp_path)
    report = branding.process_file(str(video))
    assert report["ok"] is True
    assert report["copy_through"] is True


def test_process_file_missing_logo_is_degraded(tmp_path):
    video = _make_video(tmp_path)
    out = tmp_path / "out.mp4"
    report = branding.process_file(
        str(video), str(out), logo_path=str(tmp_path / "gone.png")
    )
    assert report["ok"] is False
    assert report["degraded"] is True
    assert report["error"] == "logo file not found"
    assert out.read_bytes() == b"video-bytes"


def test_process_file_watermark_success(monkeypatch, tmp_path):
    video = _make_video(tmp_path)
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"png")
    out = tmp_path / "out.mp4"
    cmds = _stub_ffmpeg_success(monkeypatch, out)
    report = branding.process_file(str(video), str(out), logo_path=str(logo))
    assert report["ok"] is True
    assert report["watermark"] is True
    assert report["intro_outro"] is False
    assert report["degraded"] is False
    assert len([c for c in cmds if c[0] == "ffmpeg"]) == 1


def test_process_file_missing_intro_is_degraded(monkeypatch, tmp_path):
    video = _make_video(tmp_path)
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(branding, "apply_intro_outro", lambda *a, **k: False)
    report = branding.process_file(
        str(video), str(out), intro=str(tmp_path / "gone.mp4")
    )
    assert report["ok"] is False
    assert report["degraded"] is True
    assert report["error"] == "intro/outro file not found"


def test_process_file_watermark_failure_is_degraded(monkeypatch, tmp_path):
    video = _make_video(tmp_path)
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"png")
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(branding, "apply_watermark", lambda *a, **k: False)
    report = branding.process_file(str(video), str(out), logo_path=str(logo))
    assert report["ok"] is False
    assert report["degraded"] is True
    assert report["watermark"] is False
    # NOTE: fixed by PR #102 — process_file now only attempts the rename
    # when the staged .wm.mp4 exists, so the original
    # "watermark rendering failed" message is kept. The fallback copy
    # still happens.
    assert "watermark rendering failed" in report["error"]
    assert out.read_bytes() == b"video-bytes"


def test_process_file_full_flow(monkeypatch, tmp_path):
    video = _make_video(tmp_path)
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"png")
    intro = tmp_path / "intro.mp4"
    intro.write_bytes(b"intro")
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(branding, "apply_watermark", lambda *a, **k: True)
    monkeypatch.setattr(branding, "apply_intro_outro", lambda *a, **k: True)
    report = branding.process_file(
        str(video), str(out), logo_path=str(logo), intro=str(intro)
    )
    assert report["ok"] is True
    assert report["watermark"] is True
    assert report["intro_outro"] is True
    assert report["degraded"] is False


# ------------------------------------------------------------------------ main


def test_main_json_reports_status(tmp_path, monkeypatch, capsys):
    video = _make_video(tmp_path)
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(
        sys, "argv", ["branding.py", "--video", str(video), "--out", str(out), "--json"]
    )
    assert branding.main() == 0
    import json

    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["copy_through"] is True


def test_main_text_output_and_failure_code(tmp_path, monkeypatch, capsys):
    video = _make_video(tmp_path)
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "branding.py",
            "--video",
            str(video),
            "--out",
            str(out),
            "--logo",
            str(tmp_path / "gone.png"),
        ],
    )
    assert branding.main() == 1
    assert "watermark=False" in capsys.readouterr().out
