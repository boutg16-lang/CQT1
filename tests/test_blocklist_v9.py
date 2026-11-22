# -*- coding: utf-8 -*-
"""Regression tests for safety blocklist v9 (pack v9, 2026-11-22).

v9: 421 terms (377 in v8 − 18 deduped + 62 new), 6 new categories
(scam_fraud, sexual_explicit, self_harm, gambling, misinformation,
ai_deception). Review decisions pinned here:
- "داعش" moved hate_slur/high -> violence_threat/medium (organisation
  name, not a slur; it was already context-only, so bare news mentions
  never blocked — see test_safety_filter.TestMatching
  .test_news_org_mentions_do_not_block).
- "كفار"/"كفرة"/"ملاحدة" added to CONTEXT_ONLY_TERMS (the v7.42 pattern
  already used for the singulars كافر/ملحد): bare religious-discussion
  mentions never block; harmful context still escalates ملاحدة (medium).
"""

from collections import Counter

from scripts import safety_filter as sf


def test_v9_term_count_and_categories():
    assert len(sf.BLOCKLIST) == 421
    assert len({term for term, _, _, _ in sf.BLOCKLIST}) == 421  # no duplicates
    cats = Counter(cat for _, _, _, cat in sf.BLOCKLIST)
    assert cats["scam_fraud"] == 12
    assert cats["sexual_explicit"] == 11
    assert cats["self_harm"] == 11
    assert cats["gambling"] == 7
    assert cats["misinformation"] == 7
    assert cats["ai_deception"] == 5
    assert cats["hate_slur"] == 131
    assert cats["hate_dehumanize"] == 90
    assert cats["violence_threat"] == 75
    assert cats["profanity"] == 51
    assert cats["harassment"] == 21


def test_daesh_recategorised_in_blocklist():
    entries = [e for e in sf.BLOCKLIST if e[0] == "داعش"]
    assert entries == [("داعش", "ar", "medium", "violence_threat")]


def test_theological_plurals_are_context_only():
    norms = {sf.normalize_text(t) for t in sf.CONTEXT_ONLY_TERMS}
    for word in ("كفار", "كفرة", "ملاحدة"):
        assert sf.normalize_text(word) in norms
    # Bare mentions never reach the blocking threshold.
    assert sf.find_matches("قال تعالى عن الكفار", min_severity="medium") == []
    assert sf.find_matches("درس عن الملاحدة", min_severity="medium") == []
    assert sf.find_matches("نقاش عن الكفرة", min_severity="medium") == []
    # ... but they are still reported as low-severity context signals.
    matches = sf.find_matches("درس عن الملاحدة")
    assert matches
    assert all(m["severity"] == "low" for m in matches)
    assert all(m["category"] == "context_only_identity" for m in matches)


def test_v9_new_category_terms_match():
    matches = sf.find_matches("double your bitcoin now")
    assert any(
        m["term"] == "double your bitcoin"
        and m["category"] == "scam_fraud"
        and m["severity"] == "high"
        for m in matches
    )
    matches = sf.find_matches("what is a deepfake")
    assert any(
        m["term"] == "deepfake"
        and m["category"] == "ai_deception"
        and m["severity"] == "low"
        for m in matches
    )
    matches = sf.find_matches("how to kill yourself")
    assert any(
        m["term"] == "how to kill yourself"
        and m["category"] == "self_harm"
        and m["severity"] == "high"
        for m in matches
    )
    matches = sf.find_matches("online casino free spins")
    assert any(m["category"] == "gambling" for m in matches)
    matches = sf.find_matches("nicotine is not addictive")
    assert any(m["category"] == "misinformation" for m in matches)


def test_scam_phrase_blocks_segment():
    segments = [{"title": "x", "start_time": 0, "end_time": 5}]
    transcript = [
        {"start": 0, "end": 5, "text": "double your bitcoin double your crypto"}
    ]
    kept, report = sf.analyze_segments(segments, transcript, mode="block")
    assert len(kept) == 0
    assert report["blocked"] == 1


def test_low_severity_ai_deception_term_does_not_block_segment():
    segments = [{"title": "شرح", "start_time": 0, "end_time": 5}]
    transcript = [{"start": 0, "end": 5, "text": "هذا فيديو يشرح ما هو الديب فيك"}]
    kept, report = sf.analyze_segments(segments, transcript, mode="block")
    assert len(kept) == 1
    assert report["blocked"] == 0
