# -*- coding: utf-8 -*-
"""Round 3 item 1 — download resilience tests (no network, no yt-dlp import).

Covers the three behaviours added for R3-1:
1. 429/403 blocks are retried with a longer, increasing cooldown.
2. Authentication blocks (private/age-restricted) are never auto-retried.
3. The outside-YouTube escape hatch: source.mp4 / input.mp4 in the project
   folder is adopted when YouTube stays blocked.
"""

from __future__ import annotations

import os

import pytest

from scripts import download_video as dv

# --- retry loop: HTTP blocks --------------------------------------------------


def test_http_block_429_retried_with_long_backoff():
    attempts = []
    sleeps = []

    def run(options):
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("HTTP Error 429: Too Many Requests")
        return "done"

    result = dv._run_download_with_retries(run, {}, sleep=sleeps.append)
    assert result == "done"
    assert len(attempts) == 3
    # HTTP blocks use the long base (20s) with doubling waits.
    assert sleeps == [dv._HTTP_RETRY_BASE_SECONDS, dv._HTTP_RETRY_BASE_SECONDS * 2]


def test_http_block_403_forbidden_retried():
    attempts = []

    def run(options):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("ERROR: [youtube] HTTP Error 403: Forbidden")
        return "ok"

    assert dv._run_download_with_retries(run, {}, sleep=lambda s: None) == "ok"
    assert len(attempts) == 2


def test_http_block_gives_up_after_http_retries():
    attempts = []

    def run(options):
        attempts.append(1)
        raise RuntimeError("HTTP Error 429: Too Many Requests")

    with pytest.raises(RuntimeError, match="429"):
        dv._run_download_with_retries(run, {}, sleep=lambda s: None)
    assert len(attempts) == dv._HTTP_BLOCK_RETRIES + 1


def test_auth_block_never_retried():
    attempts = []

    def run(options):
        attempts.append(1)
        raise RuntimeError("This video is private")

    with pytest.raises(RuntimeError, match="private"):
        dv._run_download_with_retries(run, {}, sleep=lambda s: None)
    assert len(attempts) == 1


def test_invalid_url_never_retried():
    attempts = []

    def run(options):
        attempts.append(1)
        raise RuntimeError("is not a valid URL")

    with pytest.raises(RuntimeError):
        dv._run_download_with_retries(run, {}, sleep=lambda s: None)
    assert len(attempts) == 1


def test_http_backoff_is_longer_than_transport_backoff():
    assert dv._HTTP_RETRY_BASE_SECONDS > dv._DOWNLOAD_RETRY_BASE_SECONDS


# --- auth detection ------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "This video is private. Sign in to confirm",
        "Sign in to confirm your age",
        "age-restricted video requires authentication",
    ],
)
def test_is_auth_block_true(text):
    assert dv._is_auth_block(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "HTTP Error 429: Too Many Requests",
        "HTTP Error 403: Forbidden",
        "connection reset by peer",
    ],
)
def test_is_auth_block_false(text):
    assert dv._is_auth_block(text) is False


# --- local-source adoption (outside YouTube) -----------------------------------


def _write(path, size=2048):
    path.write_bytes(b"x" * size)
    return str(path)


def test_adopt_local_source_finds_source_mp4(tmp_path):
    path = _write(tmp_path / "source.mp4")
    assert dv._adopt_local_source(str(tmp_path)) == path


def test_adopt_local_source_prefers_input_mp4(tmp_path):
    _write(tmp_path / "input.mp4")
    _write(tmp_path / "source.mp4")
    result = dv._adopt_local_source(str(tmp_path))
    assert os.path.basename(result) == "input.mp4"


def test_adopt_local_source_ignores_tiny_files(tmp_path):
    _write(tmp_path / "source.mp4", size=100)
    assert dv._adopt_local_source(str(tmp_path)) is None


def test_adopt_local_source_none_when_absent(tmp_path):
    assert dv._adopt_local_source(str(tmp_path)) is None


def test_adopt_local_source_none_for_bad_folder(tmp_path):
    assert dv._adopt_local_source(str(tmp_path / "missing")) is None
    assert dv._adopt_local_source("") is None


def test_local_candidates_match_documented_names():
    assert "source.mp4" in dv._LOCAL_SOURCE_CANDIDATES
    assert "input.mp4" in dv._LOCAL_SOURCE_CANDIDATES
