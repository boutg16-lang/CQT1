"""Regression tests for the watermark-failure error message in
``scripts/branding.py`` ``process_file``.

When the watermark step fails before creating the staged ``.wm.mp4``
file, ``process_file`` used to attempt ``os.replace`` on that missing
file; the resulting ``FileNotFoundError`` overwrote the real
"watermark rendering failed" message in the report (documented by the
NOTE in ``tests/test_branding.py::test_process_file_watermark_failure_is_degraded``).
These tests fail on the unfixed code and pass on the fixed code.

ffmpeg/ffprobe are never run for real — ``apply_watermark`` is stubbed.
Real files are only tiny placeholders inside ``tmp_path``.
"""

from scripts import branding


def _make_video(tmp_path, name="clip.mp4"):
    video = tmp_path / name
    video.write_bytes(b"video-bytes")
    return video


def test_watermark_failure_keeps_original_error(monkeypatch, tmp_path):
    video = _make_video(tmp_path)
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"png")
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(branding, "apply_watermark",
                        lambda *a, **k: False)
    report = branding.process_file(str(video), str(out), logo_path=str(logo))
    assert report["ok"] is False
    assert report["degraded"] is True
    assert report["watermark"] is False
    # The real watermark error, not the missing staged file's rename error.
    assert report["error"] == "watermark rendering failed"
    assert "wm.mp4" not in report["error"]
    assert "No such file" not in report["error"]
    # The fallback copy still happens and no staged file was created.
    assert out.read_bytes() == b"video-bytes"
    assert not (tmp_path / "out.mp4.wm.mp4").exists()


def test_watermark_failure_in_place_keeps_original_error(monkeypatch, tmp_path):
    video = _make_video(tmp_path)
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"png")
    monkeypatch.setattr(branding, "apply_watermark",
                        lambda *a, **k: False)
    report = branding.process_file(str(video), logo_path=str(logo))
    assert report["ok"] is False
    assert report["degraded"] is True
    assert report["watermark"] is False
    assert report["error"] == "watermark rendering failed"
    # In-place output: the video itself is untouched, no crash.
    assert video.read_bytes() == b"video-bytes"


def test_watermark_success_still_moves_staged_file(monkeypatch, tmp_path):
    video = _make_video(tmp_path)
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"png")
    out = tmp_path / "out.mp4"

    def fake_watermark(video_path, out_path, *a, **k):
        with open(out_path, "wb") as f:
            f.write(b"watermarked-bytes")
        return True

    monkeypatch.setattr(branding, "apply_watermark", fake_watermark)
    report = branding.process_file(str(video), str(out), logo_path=str(logo))
    assert report["ok"] is True
    assert report["watermark"] is True
    assert report["degraded"] is False
    # The staged file was moved to the output, not left behind.
    assert out.read_bytes() == b"watermarked-bytes"
    assert not (tmp_path / "out.mp4.wm.mp4").exists()
