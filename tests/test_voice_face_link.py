# -*- coding: utf-8 -*-
"""Direct tests for scripts/voice_face_link.py.

Online voice ↔ face association: per-frame mouth-open flags are attributed
to the speaker active at that moment. Pure and deterministic — no models,
no I/O, no network.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.voice_face_link import VoiceFaceLink  # noqa: E402


def _turns():
    return [
        {"start": 0.0, "end": 5.0, "speaker": "SPEAKER_00"},
        {"start": 5.0, "end": 10.0, "speaker": "SPEAKER_01"},
    ]


# ---------------------------------------------------------------------------
# speaker_at
# ---------------------------------------------------------------------------

def test_speaker_at_inside_turn():
    link = VoiceFaceLink(_turns())
    assert link.speaker_at(0.0) == "SPEAKER_00"
    assert link.speaker_at(4.9) == "SPEAKER_00"
    assert link.speaker_at(5.0) == "SPEAKER_01"
    assert link.speaker_at(9.9) == "SPEAKER_01"


def test_speaker_at_gap_and_beyond():
    link = VoiceFaceLink(_turns())
    assert link.speaker_at(10.0) is None
    assert link.speaker_at(99.0) is None
    assert link.speaker_at(None) is None


def test_speaker_at_gap_between_turns():
    turns = [
        {"start": 0.0, "end": 2.0, "speaker": "A"},
        {"start": 4.0, "end": 6.0, "speaker": "B"},
    ]
    link = VoiceFaceLink(turns)
    assert link.speaker_at(3.0) is None
    assert link.speaker_at(4.0) == "B"


def test_turns_sorted_on_construction():
    link = VoiceFaceLink(list(reversed(_turns())))
    assert link.speaker_at(1.0) == "SPEAKER_00"
    assert link.speaker_at(7.0) == "SPEAKER_01"


def test_empty_turns():
    link = VoiceFaceLink([])
    assert link.speaker_at(1.0) is None
    link = VoiceFaceLink(None)
    assert link.speaker_at(1.0) is None


def test_constructor_clamps():
    link = VoiceFaceLink(_turns(), seconds_per_frame=0, min_confidence=-1,
                         margin=-2)
    assert link.seconds_per_frame == 1.0 / 30.0
    assert link.min_confidence == 0.0
    assert link.margin == 0.0


# ---------------------------------------------------------------------------
# update / association
# ---------------------------------------------------------------------------

def test_update_counts_only_frames_inside_a_turn():
    link = VoiceFaceLink(_turns())
    link.update(1.0, {"t1": True, "t2": False})
    link.update(1.1, {"t1": True})
    link.update(20.0, {"t1": True})  # silence: teaches nothing
    assoc = link.association()
    assert assoc["t1"]["SPEAKER_00"] == 1.0
    assert assoc["t2"]["SPEAKER_00"] == 0.0
    assert "SPEAKER_01" not in assoc["t1"]


def test_update_empty_flags_is_noop():
    link = VoiceFaceLink(_turns())
    link.update(1.0, {})
    assert link.association() == {}


def test_association_ratios():
    link = VoiceFaceLink(_turns())
    for _ in range(3):
        link.update(1.0, {"t1": True})
    link.update(1.0, {"t1": False})
    assert link.association()["t1"]["SPEAKER_00"] == 0.75


def test_update_attributes_to_current_speaker_only():
    link = VoiceFaceLink(_turns())
    link.update(1.0, {"t1": True})
    link.update(7.0, {"t1": False})
    assoc = link.association()["t1"]
    assert assoc["SPEAKER_00"] == 1.0
    assert assoc["SPEAKER_01"] == 0.0


# ---------------------------------------------------------------------------
# best_speaker_for
# ---------------------------------------------------------------------------

def test_best_speaker_for_highest_ratio():
    link = VoiceFaceLink(_turns())
    link.update(1.0, {"t1": True})
    link.update(1.0, {"t1": True})
    link.update(7.0, {"t1": False})
    link.update(7.0, {"t1": False})
    speaker, confidence = link.best_speaker_for("t1")
    assert speaker == "SPEAKER_00"
    assert confidence == 1.0


def test_best_speaker_for_unknown_track():
    link = VoiceFaceLink(_turns())
    assert link.best_speaker_for("nope") == (None, 0.0)


def test_best_speaker_for_no_frames_yet():
    link = VoiceFaceLink(_turns())
    assert link.best_speaker_for("t1") == (None, 0.0)


# ---------------------------------------------------------------------------
# current_speaker_face
# ---------------------------------------------------------------------------

def _trained_link():
    """t1 is SPEAKER_00's mouth (always open), t2 is SPEAKER_01's."""
    link = VoiceFaceLink(_turns())
    for _ in range(10):
        link.update(1.0, {"t1": True, "t2": False})
        link.update(7.0, {"t1": False, "t2": True})
    return link


def test_current_speaker_face_clear_winner():
    link = _trained_link()
    track, confidence = link.current_speaker_face(1.0, ["t1", "t2"])
    assert track == "t1"
    assert confidence == 1.0
    track, confidence = link.current_speaker_face(7.0, ["t1", "t2"])
    assert track == "t2"
    assert confidence == 1.0


def test_current_speaker_face_nobody_speaking():
    link = _trained_link()
    assert link.current_speaker_face(20.0, ["t1", "t2"]) == (None, 0.0)


def test_current_speaker_face_no_tracks():
    link = _trained_link()
    assert link.current_speaker_face(1.0, []) == (None, 0.0)


def test_current_speaker_face_no_evidence():
    link = VoiceFaceLink(_turns())
    assert link.current_speaker_face(1.0, ["t1"]) == (None, 0.0)


def test_current_speaker_face_below_min_confidence():
    link = VoiceFaceLink(_turns(), min_confidence=0.9)
    link.update(1.0, {"t1": True})
    link.update(1.0, {"t1": False})  # confidence 0.5 < 0.9
    assert link.current_speaker_face(1.0, ["t1"]) == (None, 0.0)


def test_current_speaker_face_ambiguous_within_margin():
    link = VoiceFaceLink(_turns(), margin=0.15)
    # Both tracks correlate equally with SPEAKER_00 -> ambiguous.
    for _ in range(5):
        link.update(1.0, {"t1": True, "t2": True})
    assert link.current_speaker_face(1.0, ["t1", "t2"]) == (None, 0.0)


def test_current_speaker_face_clear_gap_beats_margin():
    link = VoiceFaceLink(_turns(), margin=0.15)
    for _ in range(10):
        link.update(1.0, {"t1": True, "t2": False})
    link.update(1.0, {"t2": True})  # t2 at 1/11 ~ 0.09, gap >> margin
    track, confidence = link.current_speaker_face(1.0, ["t1", "t2"])
    assert track == "t1"
    assert confidence == 1.0


def test_current_speaker_face_ignores_other_speaker_tracks():
    # A track that matches the *other* speaker is not a candidate.
    link = _trained_link()
    track, _ = link.current_speaker_face(1.0, ["t2"])
    assert track is None
