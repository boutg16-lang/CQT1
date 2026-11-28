# -*- coding: utf-8 -*-
"""Tests for Arabic sentence-style captions (round 2 item 3).

The caption style must mirror the Studio transcript-based Short editor:
consistent sentence cues (word timings drive the boundaries), a burn-in
ASS with RTL shaping, and an ``.ar.srt`` sidecar for the YouTube caption
track. Clips without captions fall back to Whisper transcription.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import adjust_subtitles as ads  # noqa: E402
from scripts import arabic_captions as ac  # noqa: E402

AR_WORDS = [
    {"word": "قال", "start": 0.0, "end": 0.4},
    {"word": "إنه", "start": 0.5, "end": 0.8},
    {"word": "لن يدفع.", "start": 0.9, "end": 1.8},
    {"word": "ثم", "start": 3.0, "end": 3.2},
    {"word": "انفجر", "start": 3.3, "end": 3.8},
    {"word": "الموقف", "start": 3.9, "end": 4.4},
]


def _write_clip(tmp_path, name="000_Test", words=None, arabic=True):
    subs = tmp_path / "subs"
    subs.mkdir(exist_ok=True)
    payload = {"segments": [{"words": words or AR_WORDS, "start": 0.0, "end": 4.4}]}
    path = subs / (name + "_processed.json")
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_sentence_cues_group_words_and_keep_timings():
    cues = ac.group_sentences(AR_WORDS)
    assert len(cues) == 2  # punctuation + 1.2s gap split
    assert cues[0]["text"].endswith(".")
    assert cues[0]["start"] == 0.0 and cues[0]["end"] == 1.8
    assert cues[1]["start"] == 3.0


def test_srt_and_ass_are_written_for_arabic_clip(tmp_path):
    json_path = _write_clip(tmp_path)
    report = ac.build_caption_assets(str(tmp_path), json_path)
    assert report["arabic"] is True
    srt = open(report["srt_path"], encoding="utf-8").read()
    assert "00:00:00,000 --> 00:00:01,800" in srt
    ass = open(report["ass_path"], encoding="utf-8").read()
    assert "{\\rtl}" in ass
    assert "Cairo" in ass  # Arabic font family for burn-in


def test_non_arabic_clip_is_noop(tmp_path):
    words = [
        {"word": w, "start": 0.5 * i, "end": 0.5 * i + 0.4}
        for i, w in enumerate(["hello", "world", "friends"])
    ]
    json_path = _write_clip(tmp_path, words=words)
    report = ac.build_caption_assets(str(tmp_path), json_path)
    assert report["arabic"] is False
    assert not os.path.exists(ac.srt_path_for(str(tmp_path), "000_Test"))


def test_adjust_routes_arabic_to_sentences_and_keeps_word_flash(tmp_path):
    _write_clip(tmp_path, name="000_Ar")
    en_words = [
        {"word": w, "start": 0.5 * i, "end": 0.5 * i + 0.4}
        for i, w in enumerate(["hello", "world", "friends", "stay", "tuned"])
    ]
    _write_clip(tmp_path, name="001_En", words=en_words)
    ads.adjust(
        base_color="&H00FFFFFF",
        base_size=28,
        highlight_size=34,
        highlight_color="&H000000FF",
        words_per_block=3,
        gap_limit=1.0,
        mode="auto",
        vertical_position=60,
        alignment=2,
        font="Montserrat-Bold",
        outline_color="&H00101010",
        shadow_color="&H7F000000",
        bold=-1,
        italic=0,
        underline=0,
        strikeout=0,
        border_style=1,
        outline_thickness=3,
        shadow_size=1,
        uppercase=False,
        project_folder=str(tmp_path),
    )
    ar_ass = open(
        tmp_path / "subs_ass" / "000_Ar_processed.ass", encoding="utf-8"
    ).read()
    en_ass = open(
        tmp_path / "subs_ass" / "001_En_processed.ass", encoding="utf-8"
    ).read()
    assert ar_ass.count("Dialogue:") == 2  # sentence cues
    assert "{\\rtl}" in ar_ass
    assert en_ass.count("Dialogue:") == 5  # word flash untouched
    assert os.path.exists(tmp_path / "subs" / "000_Ar.ar.srt")


def test_adjust_off_restores_word_flash_for_arabic(tmp_path):
    _write_clip(tmp_path, name="000_Ar")
    ads.adjust(
        base_color="&H00FFFFFF",
        base_size=28,
        highlight_size=34,
        highlight_color="&H000000FF",
        words_per_block=3,
        gap_limit=1.0,
        mode="auto",
        vertical_position=60,
        alignment=2,
        font="Montserrat-Bold",
        outline_color="&H00101010",
        shadow_color="&H7F000000",
        bold=-1,
        italic=0,
        underline=0,
        strikeout=0,
        border_style=1,
        outline_thickness=3,
        shadow_size=1,
        uppercase=False,
        project_folder=str(tmp_path),
        arabic_sentence_style=False,
    )
    ar_ass = open(
        tmp_path / "subs_ass" / "000_Ar_processed.ass", encoding="utf-8"
    ).read()
    assert ar_ass.count("Dialogue:") > 2  # karaoke word flash restored
    assert not os.path.exists(tmp_path / "subs" / "000_Ar.ar.srt")


def test_whisper_fallback_transcribes_missing_clip(tmp_path):
    video = tmp_path / "final"
    video.mkdir()
    clip = video / "000_Missing.mp4"
    clip.write_bytes(b"stub")
    calls = []

    def fake_transcribe(video_path, model_name="", project_folder="", device=""):
        calls.append((video_path, model_name, project_folder, device))
        _write_clip(tmp_path, name="000_Missing")

    report = ac.ensure_clip_captions(
        str(tmp_path), str(clip), transcribe_fn=fake_transcribe
    )
    assert report["transcribed"] is True
    assert calls and calls[0][0] == str(clip)


def test_upload_caption_track_requires_video_id_and_srt(tmp_path):
    report = ac.upload_caption_track("", str(tmp_path / "x.ar.srt"))
    assert report["uploaded"] is False and report.get("error")
    report = ac.upload_caption_track("vid1234567", str(tmp_path / "missing.ar.srt"))
    assert report["uploaded"] is False
