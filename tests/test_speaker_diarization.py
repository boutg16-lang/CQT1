# -*- coding: utf-8 -*-
"""Direct tests for scripts/speaker_diarization.py.

Layer 1 (detect_speech_turns) is pure Python and fully testable.
Layer 2 (pyannote diarization) is only exercised through its graceful
fallbacks — no model, no token, no network.
"""
import json

from scripts import speaker_diarization

# ---------------------------------------------------------------------------
# detect_speech_turns
# ---------------------------------------------------------------------------

def test_turns_empty_input():
    assert speaker_diarization.detect_speech_turns([], fps=10) == []
    assert speaker_diarization.detect_speech_turns(None, fps=10) == []


def test_turns_invalid_fps_returns_empty():
    assert speaker_diarization.detect_speech_turns([0.9] * 20, fps=0) == []
    assert speaker_diarization.detect_speech_turns([0.9] * 20, fps=-5) == []


def test_turns_all_silent_returns_empty():
    assert speaker_diarization.detect_speech_turns([0.0] * 50, fps=10) == []
    assert speaker_diarization.detect_speech_turns([0.05] * 50, fps=10) == []


def test_turns_single_speech_run():
    # 20 frames at 10 fps, all above threshold -> one 2.0s turn
    turns = speaker_diarization.detect_speech_turns([0.9] * 20, fps=10)
    assert turns == [{"start": 0.0, "end": 2.0,
                      "speaker": speaker_diarization.SINGLE_SPEAKER}]


def test_turns_short_burst_dropped_as_noise():
    # 5 frames = 0.5s < min_turn_seconds 0.8 -> dropped
    assert speaker_diarization.detect_speech_turns([0.9] * 5, fps=10) == []


def test_turns_min_turn_boundary_kept():
    # exactly 8 frames = 0.8s meets the minimum
    turns = speaker_diarization.detect_speech_turns([0.9] * 8, fps=10)
    assert len(turns) == 1
    assert turns[0]["end"] == 0.8


def test_turns_short_gap_merges_runs():
    # speech, 3 silent frames (0.3s < max_gap 0.4s), speech -> one turn
    energies = [0.9] * 10 + [0.0] * 3 + [0.9] * 10
    turns = speaker_diarization.detect_speech_turns(energies, fps=10)
    assert len(turns) == 1
    assert turns[0] == {"start": 0.0, "end": 2.3,
                        "speaker": speaker_diarization.SINGLE_SPEAKER}


def test_turns_long_gap_splits_runs():
    # 5 silent frames (0.5s > max_gap 0.4s) splits the turn
    energies = [0.9] * 10 + [0.0] * 5 + [0.9] * 10
    turns = speaker_diarization.detect_speech_turns(energies, fps=10)
    assert len(turns) == 2
    assert turns[0] == {"start": 0.0, "end": 1.0,
                        "speaker": speaker_diarization.SINGLE_SPEAKER}
    assert turns[1] == {"start": 1.5, "end": 2.5,
                        "speaker": speaker_diarization.SINGLE_SPEAKER}


def test_turns_gap_exactly_at_boundary_merges():
    # gap of exactly max_gap_seconds (4 frames at 10 fps) does NOT split:
    # split only when idx - last_speech > gap_frames (strictly greater)
    energies = [0.9] * 10 + [0.0] * 4 + [0.9] * 10
    turns = speaker_diarization.detect_speech_turns(energies, fps=10)
    assert len(turns) == 1


def test_turns_trailing_speech_is_flushed():
    energies = [0.0] * 5 + [0.9] * 12
    turns = speaker_diarization.detect_speech_turns(energies, fps=10)
    assert turns == [{"start": 0.5, "end": 1.7,
                      "speaker": speaker_diarization.SINGLE_SPEAKER}]


def test_turns_threshold_boundary():
    # values exactly at the threshold count as speech
    turns = speaker_diarization.detect_speech_turns([0.15] * 10, fps=10, threshold=0.15)
    assert len(turns) == 1
    turns = speaker_diarization.detect_speech_turns([0.14] * 10, fps=10, threshold=0.15)
    assert turns == []


def test_turns_custom_threshold_and_min_turn():
    energies = [0.5] * 4 + [0.0] * 10 + [0.5] * 4
    turns = speaker_diarization.detect_speech_turns(
        energies, fps=10, threshold=0.4, min_turn_seconds=0.3)
    assert len(turns) == 2


def test_turns_none_values_treated_as_silence():
    energies = [None, 0.0, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, None, 0.0]
    turns = speaker_diarization.detect_speech_turns(energies, fps=10)
    assert turns == [{"start": 0.2, "end": 1.0,
                      "speaker": speaker_diarization.SINGLE_SPEAKER}]


def test_turns_timestamps_rounded_to_milliseconds():
    # fps 3 -> frame boundaries at 1/3s, rounded to 3 decimals
    turns = speaker_diarization.detect_speech_turns([0.9] * 10, fps=3)
    assert turns[0]["start"] == 0.0
    assert turns[0]["end"] == 3.333


def test_turns_short_tail_after_long_gap_dropped():
    # first turn kept; a 3-frame tail after a long gap is below min length
    energies = [0.9] * 10 + [0.0] * 10 + [0.9] * 3
    turns = speaker_diarization.detect_speech_turns(energies, fps=10)
    assert len(turns) == 1
    assert turns[0]["end"] == 1.0


# ---------------------------------------------------------------------------
# diarize_audio — graceful fallbacks only
# ---------------------------------------------------------------------------

def test_diarize_no_token_returns_none(tmp_path):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"fake")
    assert speaker_diarization.diarize_audio(str(audio)) is None


def test_diarize_missing_file_returns_none():
    assert speaker_diarization.diarize_audio("/no/such/audio.wav",
                                             token="hf_fake") is None


def test_diarize_empty_path_returns_none():
    assert speaker_diarization.diarize_audio("", token="hf_fake") is None


def test_diarize_env_token_without_file_returns_none(monkeypatch):
    monkeypatch.setenv("PYANNOTE_AUTH_TOKEN", "hf_from_env")
    assert speaker_diarization.diarize_audio("/no/such/audio.wav") is None


def test_diarize_blank_token_returns_none(tmp_path):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"fake")
    assert speaker_diarization.diarize_audio(str(audio), token="   ") is None


# ---------------------------------------------------------------------------
# turns_for_clip
# ---------------------------------------------------------------------------

def test_turns_for_clip_prefers_energy_when_diarization_disabled():
    turns = speaker_diarization.turns_for_clip(
        None, [0.9] * 20, fps=10, prefer_diarization=False)
    assert turns == [{"start": 0.0, "end": 2.0,
                      "speaker": speaker_diarization.SINGLE_SPEAKER}]


def test_turns_for_clip_falls_back_when_diarization_unavailable():
    # no token -> diarize_audio returns None -> energy fallback
    turns = speaker_diarization.turns_for_clip(
        "/no/such/audio.wav", [0.9] * 20, fps=10, prefer_diarization=True)
    assert len(turns) == 1
    assert turns[0]["speaker"] == speaker_diarization.SINGLE_SPEAKER


def test_turns_for_clip_uses_diarization_when_available(monkeypatch):
    expected = [{"start": 0.5, "end": 1.5, "speaker": "SPEAKER_01"},
                {"start": 2.0, "end": 3.0, "speaker": "SPEAKER_00"}]
    monkeypatch.setattr(speaker_diarization, "diarize_audio",
                        lambda path, token=None: expected)
    turns = speaker_diarization.turns_for_clip(
        "clip.wav", [0.9] * 20, fps=10, prefer_diarization=True)
    assert turns == expected


def test_turns_for_clip_empty_when_no_speech_anywhere():
    assert speaker_diarization.turns_for_clip(None, [], fps=10) == []
    assert speaker_diarization.turns_for_clip(None, [0.0] * 30, fps=10) == []


# ---------------------------------------------------------------------------
# load_segments_file
# ---------------------------------------------------------------------------

def test_load_segments_missing_file_returns_none(tmp_path):
    assert speaker_diarization.load_segments_file(str(tmp_path)) is None


def test_load_segments_valid_file(tmp_path):
    payload = {"segments": [{"start": 1.0, "end": 2.0, "speaker": "SPEAKER_0"}]}
    (tmp_path / "speaker_segments.json").write_text(
        json.dumps(payload), encoding="utf-8")
    assert speaker_diarization.load_segments_file(str(tmp_path)) == payload["segments"]


def test_load_segments_empty_list_returns_none(tmp_path):
    (tmp_path / "speaker_segments.json").write_text(
        json.dumps({"segments": []}), encoding="utf-8")
    assert speaker_diarization.load_segments_file(str(tmp_path)) is None


def test_load_segments_malformed_json_returns_none(tmp_path):
    (tmp_path / "speaker_segments.json").write_text("{broken", encoding="utf-8")
    assert speaker_diarization.load_segments_file(str(tmp_path)) is None


def test_load_segments_non_dict_returns_none(tmp_path):
    (tmp_path / "speaker_segments.json").write_text(
        json.dumps([{"start": 1.0}]), encoding="utf-8")
    assert speaker_diarization.load_segments_file(str(tmp_path)) is None


def test_load_segments_segments_not_a_list_returns_none(tmp_path):
    (tmp_path / "speaker_segments.json").write_text(
        json.dumps({"segments": "not-a-list"}), encoding="utf-8")
    assert speaker_diarization.load_segments_file(str(tmp_path)) is None
