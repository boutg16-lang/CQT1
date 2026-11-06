import hashlib
import shutil
import subprocess

import pytest

from scripts.video_sanitizer import process_srt, sanitize_video, validate_edits


def test_edit_validation_rejects_overlap_and_mixed_cut():
    assert (
        validate_edits([{"start": 1, "end": 2, "action": "mute"}], 4)[0]["action"]
        == "mute"
    )
    with pytest.raises(ValueError, match="متداخلة"):
        validate_edits(
            [
                {"start": 1, "end": 2, "action": "mute"},
                {"start": 1.5, "end": 3, "action": "bleep"},
            ],
            4,
        )
    with pytest.raises(ValueError, match="لا تخلط"):
        validate_edits(
            [
                {"start": 1, "end": 2, "action": "cut"},
                {"start": 2.5, "end": 3, "action": "mute"},
            ],
            4,
        )


@pytest.mark.parametrize("action", ["mute", "bleep", "cut"])
def test_ffmpeg_render_preserves_source_and_writes_audit(tmp_path, action):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg unavailable")
    src = tmp_path / "source.mp4"
    out = tmp_path / (action + ".mp4")
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=25:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=4",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(src),
        ],
        check=True,
    )
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    result = sanitize_video(
        str(src),
        [{"start": 1, "end": 1.7, "action": action, "reason": "approved test"}],
        str(out),
    )
    assert out.exists() and out.stat().st_size > 1000
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before
    assert result["report"]["original_preserved"] is True
    assert result["report"]["output_validation"]["ok"] is True
    assert (tmp_path / (action + ".audit.json")).exists()


def test_srt_masking(tmp_path):
    src = tmp_path / "input.srt"
    dst = tmp_path / "output.srt"
    src.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nhello\n\n2\n00:00:02,000 --> 00:00:03,000\nphrase\n",
        encoding="utf-8",
    )
    process_srt(str(src), str(dst), [{"start": 2, "end": 3, "action": "mute"}])
    assert "[تم حجب العبارة بعد المراجعة]" in dst.read_text(encoding="utf-8")
