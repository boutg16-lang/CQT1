# -*- coding: utf-8 -*-
"""Direct tests for scripts/download_video.py (core pipeline, no network).

Covers only pure/deterministic behaviour: filename sanitising, friendly
error mapping, transient-error detection, retry/backoff, format fallbacks,
runtime options, and adoption of yt-dlp output. No download is ever
attempted and yt-dlp is never imported for real (it is optional at import
time); ``download()`` itself is intentionally not tested here because it
needs network + yt-dlp + ffmpeg.
"""

import pytest

from scripts import download_video as dv

# --- sanitize_filename -------------------------------------------------------


def test_sanitize_filename_keeps_plain_and_arabic_names():
    assert dv.sanitize_filename("Hello World") == "Hello World"
    assert dv.sanitize_filename("فيديو تجريبي 2026") == "فيديو تجريبي 2026"


def test_sanitize_filename_strips_reserved_and_control_chars():
    assert dv.sanitize_filename('a/b\\c:d*e?f"g<h>i|j') == "abcdefghij"
    assert dv.sanitize_filename("test 🎉 video") == "test video"


def test_sanitize_filename_collapses_spaces_and_strips_dots():
    assert dv.sanitize_filename("  too   many    spaces  ") == "too many spaces"
    assert dv.sanitize_filename("...hello...") == "hello"


def test_sanitize_filename_empty_or_blank_becomes_unknown_video():
    assert dv.sanitize_filename("") == "Unknown_Video"
    assert dv.sanitize_filename("   ") == "Unknown_Video"
    assert dv.sanitize_filename("???") == "Unknown_Video"


def test_sanitize_filename_truncates_to_120_chars():
    name = "x" * 200
    result = dv.sanitize_filename(name)
    assert result == "x" * 120


# --- _friendly_download_error ------------------------------------------------


def test_friendly_error_private_video_points_at_cookies():
    msg = dv._friendly_download_error(Exception("ERROR: Private video"))
    assert "PRIVATE" in msg
    assert "cookies-from-browser" in msg


def test_friendly_error_sign_in_is_treated_as_private():
    msg = dv._friendly_download_error(Exception("Please sign in to view"))
    assert "PRIVATE" in msg


def test_friendly_error_age_restricted():
    msg = dv._friendly_download_error(Exception("This video is age-restricted"))
    assert "age-restricted" in msg
    assert "cookies-from-browser" in msg


def test_friendly_error_unavailable_and_copyright():
    assert (
        "unavailable"
        in dv._friendly_download_error(Exception("Video unavailable")).lower()
    )
    assert (
        "copyright"
        in dv._friendly_download_error(
            Exception("removed for copyright reasons")
        ).lower()
    )


def test_friendly_error_invalid_url_and_js_runtime():
    assert "not a valid YouTube URL" in dv._friendly_download_error(
        Exception("is not a valid URL")
    )
    msg = dv._friendly_download_error(Exception("no supported javascript runtime"))
    assert "JavaScript runtime" in msg
    assert "Deno" in msg


def test_friendly_error_subtitle_429_returns_none_for_retry_branch():
    assert (
        dv._friendly_download_error(Exception("unable to download video subtitles"))
        is None
    )
    assert dv._friendly_download_error(Exception("HTTP Error 429")) is None


def test_friendly_error_transient_and_403():
    msg = dv._friendly_download_error(Exception("connection reset by peer"))
    assert "transfer kept failing" in msg
    msg = dv._friendly_download_error(Exception("HTTP Error 403: Forbidden"))
    assert "HTTP 403" in msg


def test_friendly_error_falls_back_to_generic_message():
    msg = dv._friendly_download_error(Exception("boom"))
    assert msg.startswith("\n[ERROR] YouTube download failed: boom")


# --- _is_http_block / _is_transient_download_error ---------------------------


@pytest.mark.parametrize(
    "text", ["403", "HTTP 403", "Forbidden", "429", "HTTP Error 500"]
)
def test_is_http_block_true(text):
    assert dv._is_http_block(text) is True


@pytest.mark.parametrize("text", ["", None, "video unavailable", "timeout"])
def test_is_http_block_false(text):
    assert dv._is_http_block(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "IncompleteRead(0 bytes read, 100 more expected)",
        "connection reset by peer",
        "Connection aborted",
        "remote end closed connection",
        "read timed out",
        "unexpected EOF",
        "content too short",
    ],
)
def test_is_transient_download_error_true(text):
    assert dv._is_transient_download_error(text) is True


@pytest.mark.parametrize("text", ["", "private video", "video unavailable", "403"])
def test_is_transient_download_error_false(text):
    assert dv._is_transient_download_error(text) is False


# --- _run_download_with_retries ----------------------------------------------


def test_run_download_with_retries_success_first_try():
    calls = []

    def run(options):
        calls.append(options)
        return "ok"

    assert dv._run_download_with_retries(run, {"a": 1}) == "ok"
    assert calls == [{"a": 1}]


def test_run_download_with_retries_sleeps_with_backoff_then_succeeds():
    attempts = []
    sleeps = []

    def run(options):
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("connection reset by peer")
        return "done"

    result = dv._run_download_with_retries(run, {}, sleep=sleeps.append)
    assert result == "done"
    assert len(attempts) == 3
    assert sleeps == [2, 4]  # base 2s, doubling per retry


def test_run_download_with_retries_gives_up_after_retries():
    attempts = []

    def run(options):
        attempts.append(1)
        raise RuntimeError("connection reset by peer")

    with pytest.raises(RuntimeError, match="connection reset"):
        dv._run_download_with_retries(run, {}, sleep=lambda s: None)
    assert len(attempts) == 3  # initial attempt + 2 transport retries


def test_run_download_with_retries_non_transient_raises_immediately():
    attempts = []

    def run(options):
        attempts.append(1)
        raise RuntimeError("private video")

    with pytest.raises(RuntimeError, match="private video"):
        dv._run_download_with_retries(run, {}, sleep=lambda s: None)
    assert len(attempts) == 1


# --- _format_attempts ---------------------------------------------------------


def test_format_attempts_default_format_has_three_player_clients():
    attempts = dv._format_attempts("best[ext=mp4]/best")
    assert attempts == [
        ("best[ext=mp4]/best", None),
        ("best[ext=mp4]/best", ["android", "web_safari"]),
        ("best[ext=mp4]/best", ["tv", "web_safari"]),
    ]


def test_format_attempts_bestvideo_adds_mp4_and_best_fallbacks():
    attempts = dv._format_attempts("bestvideo+bestaudio/best")
    formats = [fmt for fmt, _ in attempts]
    assert formats == (
        ["bestvideo+bestaudio/best"] * 3 + ["best[ext=mp4]/best"] * 3 + ["best"] * 3
    )
    assert len(attempts) == 9


# --- _require_yt_dlp / _runtime_options ---------------------------------------


def test_require_yt_dlp_raises_without_yt_dlp(monkeypatch):
    monkeypatch.setattr(dv, "yt_dlp", None)
    with pytest.raises(RuntimeError, match="yt-dlp is not installed"):
        dv._require_yt_dlp()


def test_require_yt_dlp_returns_module_when_present(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(dv, "yt_dlp", sentinel)
    assert dv._require_yt_dlp() is sentinel


def test_runtime_options_empty_without_deno(monkeypatch):
    monkeypatch.delenv("VIRALCUTTER_DENO_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert dv._runtime_options() == {}


def test_runtime_options_uses_explicit_deno_path(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_DENO_PATH", "/opt/deno")
    assert dv._runtime_options() == {"js_runtimes": {"deno": {"path": "/opt/deno"}}}


# --- _adopt_downloaded_video --------------------------------------------------


def _write(path, size=2048):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def test_adopt_prefers_existing_final_video(tmp_path):
    final = _write(tmp_path / "input.mp4")
    assert dv._adopt_downloaded_video(tmp_path, tmp_path / "input", final) == final


def test_adopt_moves_extensionless_output_to_final(tmp_path):
    base = tmp_path / "input"
    final = tmp_path / "input.mp4"
    _write(base)
    assert dv._adopt_downloaded_video(tmp_path, base, final) == final
    assert final.is_file() and final.stat().st_size == 2048
    assert not base.exists()


def test_adopt_finds_suffixed_mp4_candidate(tmp_path):
    base = tmp_path / "input"
    final = tmp_path / "input.mp4"
    candidate = _write(tmp_path / "input-clip.mp4")
    assert dv._adopt_downloaded_video(tmp_path, base, final) == final
    assert candidate.exists() is False  # moved onto the final path


def test_adopt_ignores_subtitles_partials_and_json(tmp_path):
    base = tmp_path / "input"
    final = tmp_path / "input.mp4"
    _write(tmp_path / "input.en.srt")
    _write(tmp_path / "input.mp4.part")
    _write(tmp_path / "input.info.json")
    assert dv._adopt_downloaded_video(tmp_path, base, final) is None


def test_adopt_ignores_files_under_1kib(tmp_path):
    base = tmp_path / "input"
    final = tmp_path / "input.mp4"
    _write(tmp_path / "input.mp4", size=100)
    assert dv._adopt_downloaded_video(tmp_path, base, final) is None


def test_adopt_mkv_without_ffmpeg_returns_source_unchanged(tmp_path, monkeypatch):
    base = tmp_path / "input"
    final = tmp_path / "input.mp4"
    source = _write(tmp_path / "input.mkv")
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert dv._adopt_downloaded_video(tmp_path, base, final) == str(source)
    assert source.is_file()


def test_adopt_returns_none_when_folder_is_empty(tmp_path):
    assert (
        dv._adopt_downloaded_video(tmp_path, tmp_path / "input", tmp_path / "input.mp4")
        is None
    )


# --- progress_hook / _print_friendly_and_exit ---------------------------------


def test_progress_hook_prints_percent_and_finished(capsys):
    dv.progress_hook(
        {"status": "downloading", "_percent_str": "42.5%", "_eta_str": "00:10"}
    )
    assert "42.5%" in capsys.readouterr().out
    dv.progress_hook({"status": "finished", "filename": "input.mp4"})
    assert "input.mp4" in capsys.readouterr().out
    dv.progress_hook({"status": "error"})
    assert capsys.readouterr().out == ""


def test_print_friendly_and_exit_raises_auth_for_private():
    with pytest.raises(dv.AuthNeededError):
        dv._print_friendly_and_exit(Exception("Private video"))


def test_print_friendly_and_exit_exits_1_for_generic_error():
    with pytest.raises(SystemExit) as excinfo:
        dv._print_friendly_and_exit(Exception("boom"))
    assert excinfo.value.code == 1


def test_auth_needed_error_is_a_runtime_error():
    assert issubclass(dv.AuthNeededError, RuntimeError)
