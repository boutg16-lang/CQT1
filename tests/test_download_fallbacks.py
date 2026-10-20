import pytest

from scripts import download_video


def test_format_attempts_contains_quality_and_safe_fallbacks():
    attempts = download_video._format_attempts("bestvideo+bestaudio/best")
    formats = [item[0] for item in attempts]
    clients = [item[1] for item in attempts]
    assert formats[0] == "bestvideo+bestaudio/best"
    assert "best" in formats
    assert any(client == ["android", "web_safari"] for client in clients)


def test_runtime_options_uses_explicit_deno_path(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_DENO_PATH", r"D:\\Tools\\deno.exe")
    options = download_video._runtime_options()
    assert options == {"js_runtimes": {"deno": {"path": r"D:\\Tools\\deno.exe"}}}


def test_http_block_detection_is_narrow_enough_for_fallbacks():
    assert download_video._is_http_block("HTTP Error 429: Too Many Requests")
    assert download_video._is_http_block("403 Forbidden")
    assert not download_video._is_http_block("Private video. Sign in")



def test_incomplete_transfer_is_transient_but_http_blocks_are_not():
    assert download_video._is_transient_download_error(
        "ERROR: [download] Got error: 10436544 bytes read, 42325 more expected"
    )
    assert download_video._is_transient_download_error("Connection reset by peer")
    assert not download_video._is_transient_download_error("HTTP Error 429: Too Many Requests")
    assert not download_video._is_transient_download_error("HTTP Error 403: Forbidden")


def test_download_retries_resume_transient_errors_with_backoff():
    calls = []
    delays = []
    options = {"format": "bestvideo+bestaudio/best"}

    def fake_download(current_options):
        calls.append(current_options)
        if len(calls) < 3:
            raise RuntimeError("10436544 bytes read, 42325 more expected")
        return "downloaded"

    result = download_video._run_download_with_retries(
        fake_download, options, sleep=delays.append
    )

    assert result == "downloaded"
    assert len(calls) == 3
    assert all(current_options is options for current_options in calls)
    assert delays == [2, 4]


def test_download_does_not_retry_permanent_http_errors():
    calls = []
    delays = []

    def fake_download(options):
        calls.append(options)
        raise RuntimeError("HTTP Error 429: Too Many Requests")

    with pytest.raises(RuntimeError, match="HTTP Error 429"):
        download_video._run_download_with_retries(fake_download, {}, sleep=delays.append)

    assert len(calls) == 1
    assert delays == []


def test_download_stops_after_bounded_transient_retries():
    calls = []
    delays = []

    def fake_download(options):
        calls.append(options)
        raise RuntimeError("10436544 bytes read, 42325 more expected")

    with pytest.raises(RuntimeError, match="bytes read"):
        download_video._run_download_with_retries(fake_download, {}, sleep=delays.append)

    assert len(calls) == 3
    assert delays == [2, 4]
