# -*- coding: utf-8 -*-
"""Tests for YouTube-captions transcript source (round 2, item 3b)."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import yt_transcript as yt  # noqa: E402

SRV3 = (
    b'<?xml version="1.0" encoding="utf-8"?>\n'
    b'<timedtext lang="ar">\n<body>\n'
    b'<p t="0" d="2000"><s t="0" d="800">\xd9\x85\xd8\xb1\xd8\xad\xd8\xa8\xd8\xa7</s>'
    b'<s t="900" d="900">\xd8\xa8\xd9\x83\xd9\x85</s></p>\n'
    b'<p t="2000" d="1800"><s t="0" d="500">\xd9\x81\xd9\x8a</s>'
    b'<s t="600" d="900">\xd8\xa7\xd9\x84\xd9\x81\xd9\x8a\xd8\xaf\xd9\x8a\xd9\x88.</s></p>\n'
    b"</body>\n</timedtext>\n"
)


def test_video_id_from_url():
    assert (
        yt.video_id_from_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30")
        == "dQw4w9WgXcQ"
    )
    assert yt.video_id_from_url("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert yt.video_id_from_url("https://vimeo.com/12345") == ""
    assert yt.video_id_from_url("") == ""


def test_parse_srv3_words_and_cues():
    segments = yt.parse_srv3(SRV3)
    assert len(segments) == 2
    first = segments[0]
    assert first["start"] == 0.0
    assert first["end"] == 2.0
    assert first["text"] == "مرحبا بكم"
    assert first["words"][1] == {"word": "بكم", "start": 0.9, "end": 1.8}
    assert segments[1]["end"] == 3.8


def test_write_transcript_artifacts_matches_transcribe_contract(tmp_path):
    segments = [
        {"start": 0.0, "end": 2.0, "text": "مرحبا بكم", "words": []},
        {"start": 2.0, "end": 3.8, "text": "في هذا الفيديو.", "words": []},
    ]
    srt, tsv, jsn = yt.write_transcript_artifacts(
        str(tmp_path), "input", segments, "ar"
    )
    assert os.path.isfile(srt) and os.path.isfile(tsv) and os.path.isfile(jsn)
    tsv_rows = open(tsv, encoding="utf-8").read().strip().splitlines()
    assert tsv_rows[0] == "start\tend\ttext"
    assert tsv_rows[1].split("\t")[0] == "0" and tsv_rows[1].split("\t")[1] == "2000"
    payload = json.load(open(jsn, encoding="utf-8"))
    assert payload["language"] == "ar"
    assert payload["segments"][1]["text"] == "في هذا الفيديو."


class _FakeResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_pick_track_prefers_arabic_asr_last_resort():
    tracks = [
        {"id": "en1", "language": "en", "trackKind": "asr", "isAutoSynced": True},
        {"id": "ar1", "language": "ar", "trackKind": "standard"},
        {"id": "fr1", "language": "fr", "trackKind": "standard"},
    ]
    assert yt._pick_track(tracks)["id"] == "ar1"
    assert yt._pick_track([tracks[0]])["id"] == "en1"
    assert yt._pick_track([]) is None


def test_no_token_falls_back_to_whisper_with_reason(tmp_path, capsys):
    result = yt.try_source_transcript(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        str(tmp_path),
        token_path=str(tmp_path / "missing.json"),
    )
    assert result is None
    out = capsys.readouterr().out
    assert "falling back to Whisper" in out
    assert "no OAuth token file" in out


def test_non_youtube_url_is_silent_noop(tmp_path):
    result = yt.try_source_transcript(
        "https://vimeo.com/123", str(tmp_path), token_path=""
    )
    assert result is None


def test_list_failure_reason_surfaced(tmp_path, capsys, monkeypatch):
    def boom(video_id, token_file):
        raise RuntimeError("HTTP 403: insufficient permissions")

    monkeypatch.setattr(yt, "list_captions", boom)
    token_file = tmp_path / "token.json"
    token_file.write_text("{}", encoding="utf-8")
    result = yt.try_source_transcript(
        "https://youtu.be/dQw4w9WgXcQ",
        str(tmp_path),
        token_path=str(token_file),
    )
    assert result is None
    assert "captions.list failed" in capsys.readouterr().out


def test_no_tracks_reason(tmp_path, capsys, monkeypatch):
    token_file = tmp_path / "token.json"
    token_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(yt, "list_captions", lambda vid, tok: [])
    result = yt.try_source_transcript(
        "https://youtu.be/dQw4w9WgXcQ",
        str(tmp_path),
        token_path=str(token_file),
    )
    assert result is None
    out = capsys.readouterr().out
    assert "has no caption tracks" in out


def test_success_writes_artifacts_and_returns_pair(tmp_path, monkeypatch):
    token_file = tmp_path / "token.json"
    token_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        yt,
        "list_captions",
        lambda vid, tok: [{"id": "cap1", "language": "ar", "trackKind": "standard"}],
    )
    monkeypatch.setattr(yt, "_access_token", lambda token_path: "fake-token")
    monkeypatch.setattr(yt, "download_captions", lambda cid, tok, tfmt="srv3": SRV3)
    result = yt.try_source_transcript(
        "https://youtu.be/dQw4w9WgXcQ",
        str(tmp_path),
        base_name="input",
        token_path=str(token_file),
    )
    assert result is not None
    srt_file, tsv_file = result
    assert os.path.isfile(srt_file) and os.path.isfile(tsv_file)
    assert os.path.isfile(os.path.join(str(tmp_path), "input.json"))
    payload = json.load(
        open(os.path.join(str(tmp_path), "input.json"), encoding="utf-8")
    )
    assert payload["language"] == "ar"
