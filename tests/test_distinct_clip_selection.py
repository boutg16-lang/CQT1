# -*- coding: utf-8 -*-
"""v7.52 — distinct-clip guarantee regression tests.

The reported bug: the selection returned the SAME footage more than once,
dressed up with a different title. The AI re-serves a moment with a shifted
start (a different sentence boundary) and a fresh title, and the old
temporal-dedup floor (>=60% of the shorter window, <=1s shift) let both
survive — the user saw "the same clip, only the title changed".

This module locks in the stricter, env-tunable contract so no two exported
clips can share more than the configured fraction of footage.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import create_viral_segments as cvs


def _win(start, end, title="clip", score=50):
    return {"title": title, "start_time": start, "end_time": end,
            "score": score}


# ---------------------------------------------------------------------------
# 1. The temporal floor
# ---------------------------------------------------------------------------

def test_forty_percent_overlap_is_a_duplicate():
    # 0-30 vs 12-42: intersection 18s over a 30s shorter window = 0.60.
    assert cvs._windows_are_near_duplicates(_win(0, 30), _win(12, 42)) is True


def test_fifty_percent_shifted_overlap_is_a_duplicate():
    # The exact shape the AI produced: same beat, shifted 15s, half shared.
    assert cvs._windows_are_near_duplicates(_win(12, 42), _win(30, 60)) is True


def test_thirty_three_percent_overlap_stays_distinct():
    # 0-15 vs 10-25 = 0.33, below the 0.40 floor — real different footage.
    assert cvs._windows_are_near_duplicates(_win(0, 15), _win(10, 25)) is False


def test_small_shift_with_thirty_percent_overlap_is_a_duplicate():
    # Start within 2s and >=30% shared: the same selection re-cut.
    assert cvs._windows_are_near_duplicates(_win(10, 30), _win(11, 25)) is True


def test_disjoint_windows_stay_distinct():
    assert cvs._windows_are_near_duplicates(_win(0, 20), _win(40, 60)) is False


def test_threshold_is_env_overridable(monkeypatch):
    # A channel that wants the old, looser behaviour can restore it.
    monkeypatch.setenv("VIRALCUTTER_WINDOW_DUP_OVERLAP", "0.60")
    monkeypatch.setenv("VIRALCUTTER_WINDOW_SHIFT_DUP_OVERLAP", "0.50")
    assert cvs._windows_are_near_duplicates(_win(0, 20), _win(9, 29)) is False
    # ...and the strict default catches the same pair.
    monkeypatch.delenv("VIRALCUTTER_WINDOW_DUP_OVERLAP")
    monkeypatch.delenv("VIRALCUTTER_WINDOW_SHIFT_DUP_OVERLAP")
    assert cvs._windows_are_near_duplicates(_win(0, 20), _win(9, 29)) is True


# ---------------------------------------------------------------------------
# 2. deduplicate_segments never exports a retitled copy
# ---------------------------------------------------------------------------

def test_deduplicate_drops_shifted_retitled_copy():
    segments = [
        _win(12, 42, title="عنوان أول", score=95),
        _win(30, 60, title="عنوان ثانٍ مختلف", score=90),
    ]
    kept = cvs.deduplicate_segments(segments)
    assert [item["title"] for item in kept] == ["عنوان أول"]


def test_deduplicate_keeps_the_higher_scored_window():
    segments = [
        _win(12, 42, title="weaker", score=70),
        _win(30, 60, title="stronger", score=99),
    ]
    kept = cvs.deduplicate_segments(segments)
    assert [item["title"] for item in kept] == ["stronger"]


def test_deduplicate_keeps_genuinely_distinct_windows():
    segments = [
        _win(0, 15, title="first", score=95),
        _win(10, 25, title="second", score=90),
        _win(60, 90, title="third", score=80),
    ]
    kept = cvs.deduplicate_segments(segments)
    assert {item["title"] for item in kept} == {"first", "second", "third"}


# ---------------------------------------------------------------------------
# 3. End-to-end: process_segments returns distinct footage only
# ---------------------------------------------------------------------------

def _arabic_transcript():
    return [
        {"start": float(i * 6), "end": float(i * 6 + 5),
         "text": f"جملة رقم {i} تتحدث عن نقطة مهمة في هذا الموضوع بالتفصيل"}
        for i in range(12)
    ]


def test_process_segments_drops_same_beat_with_different_title():
    raw = [
        {"title": "عنوان أول", "start_time": 12, "end_time": 42, "score": 95},
        {"title": "عنوان ثانٍ مختلف تماماً", "start_time": 30, "end_time": 60, "score": 90},
    ]
    result = cvs.process_segments(raw, _arabic_transcript(), 15, 60)
    assert len(result["segments"]) == 1
    assert result["segments"][0]["title"] == "عنوان أول"


def test_process_segments_export_is_pairwise_distinct():
    # Many overlapping candidates for a few real moments: whatever survives
    # must satisfy the distinctness invariant pairwise.
    transcript = _arabic_transcript()
    raw = [
        {"title": f"t{i}", "start_time": start, "end_time": end,
         "score": 90 - i}
        for i, (start, end) in enumerate([
            (0, 30), (12, 42), (30, 60), (6, 36), (48, 78),
        ])
    ]
    result = cvs.process_segments(raw, transcript, 15, 60)
    windows = result["segments"]
    for i in range(len(windows)):
        for j in range(i + 1, len(windows)):
            assert not cvs._windows_are_near_duplicates(windows[i], windows[j]), (
                f"exported duplicate footage: {windows[i]} vs {windows[j]}")


# ---------------------------------------------------------------------------
# 4. Overlap ratio + diversity demotion of partially overlapping picks
# ---------------------------------------------------------------------------

def test_window_overlap_ratio_measures_shared_fraction():
    assert cvs._window_overlap_ratio(_win(0, 20), _win(0, 20)) == 1.0
    assert cvs._window_overlap_ratio(_win(0, 20), _win(10, 30)) == 0.5
    assert cvs._window_overlap_ratio(_win(0, 20), _win(40, 60)) == 0.0
    assert cvs._window_overlap_ratio({"title": "no times"}, _win(0, 20)) == 0.0


def test_diversity_ranking_demotes_partially_overlapping_pick():
    # After A (best) is picked, B shares 1/3 of A's footage while C is a
    # genuinely different moment. B's raw score is higher, but the graded
    # temporal penalty must push C ahead so distinct clips rank first.
    segments = [
        {"title": "A", "topic": "t1", "start_time": 0, "end_time": 30,
         "selection_score": 90},
        {"title": "B", "topic": "t2", "start_time": 20, "end_time": 50,
         "selection_score": 88},
        {"title": "C", "topic": "t3", "start_time": 100, "end_time": 130,
         "selection_score": 80},
    ]
    ranked = cvs._rank_segments_with_diversity(segments, 3)
    assert [item["title"] for item in ranked] == ["A", "C", "B"]
    assert ranked[2]["diversity_adjustment"] > 0


def test_diversity_ranking_keeps_disjoint_moments_unpenalised():
    segments = [
        {"title": "A", "start_time": 0, "end_time": 30, "selection_score": 90},
        {"title": "B", "start_time": 100, "end_time": 130, "selection_score": 80},
    ]
    ranked = cvs._rank_segments_with_diversity(segments, 2)
    assert [item["title"] for item in ranked] == ["A", "B"]
    assert all(item["diversity_adjustment"] == 0.0 for item in ranked)
