# -*- coding: utf-8 -*-
"""Direct tests for scripts/platform_variant.py.

Covers platform normalisation, clip-index parsing, the deterministic
seed, the original-contribution gate (manifest / provenance report), the
prior-publish matching, plan_variant decisions, and maybe_variant with a
stubbed originality module (no ffmpeg, no OpenCV).
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import platform_variant as pv  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (" TikTok ", "tiktok"), ("YouTube", "youtube"), ("", ""), (None, ""),
    ("INSTAGRAM", "instagram"),
])
def test_normalize_platform(raw, expected):
    assert pv.normalize_platform(raw) == expected


@pytest.mark.parametrize("platform,expected", [
    ("tiktok", True), ("TikTok", True), ("instagram", True),
    ("reels", True), ("youtube", False), ("yt_shorts", False), ("", False),
    (None, False),
])
def test_is_variant_platform(platform, expected):
    assert pv.is_variant_platform(platform) is expected


@pytest.mark.parametrize("name,expected", [
    ("03_clip.mp4", 3), ("1234_x.mp4", 1234), ("1.mp4", 1),
    ("clip.mp4", None), ("12345_x.mp4", None), ("03clip.mp4", None),
    ("", None), (None, None),
])
def test_clip_index(name, expected):
    assert pv._clip_index(name) == expected


def test_choose_seed_deterministic_and_sensitive():
    a = pv.choose_seed("/p", "01_a.mp4", "tiktok", 1)
    assert a == pv.choose_seed("/p", "01_a.mp4", "tiktok", 1)
    assert a != pv.choose_seed("/p", "01_a.mp4", "tiktok", 2)
    assert a != pv.choose_seed("/p", "01_a.mp4", "instagram", 1)
    assert a != pv.choose_seed("/p", "02_a.mp4", "tiktok", 1)
    assert isinstance(a, int) and a >= 0


# ---------------------------------------------------------------------------
# Original-contribution gate
# ---------------------------------------------------------------------------

def _project(tmp_path, manifest=None, provenance=None):
    if manifest is not None:
        (tmp_path / "project_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8")
    if provenance is not None:
        (tmp_path / "provenance_report.json").write_text(
            json.dumps(provenance), encoding="utf-8")
    video = tmp_path / "01_clip.mp4"
    video.write_bytes(b"x")
    return tmp_path, str(video)


class TestOriginalContribution:
    def test_missing_files_not_meaningful(self, tmp_path):
        project, video = _project(tmp_path)
        result = pv._original_contribution(str(project), video)
        assert result["meaningful"] is False
        assert result["index"] == 1
        assert result["signals"] == []
        assert result["source"] is None

    def test_manifest_commentary(self, tmp_path):
        project, video = _project(
            tmp_path, manifest={"transformation": {"clips": {"1": {"commentary": True}}}})
        result = pv._original_contribution(str(project), video)
        assert result["meaningful"] is True
        assert result["signals"] == ["commentary"]
        assert result["source"] == "project_manifest"

    def test_manifest_voiceover_and_broll(self, tmp_path):
        project, video = _project(
            tmp_path,
            manifest={"transformation": {"voiceover_path": "v.mp3", "broll": ["b1.mp4"]}})
        result = pv._original_contribution(str(project), video)
        assert result["meaningful"] is True
        assert sorted(result["signals"]) == ["broll", "voiceover"]

    def test_manifest_nested_clips_int_key(self, tmp_path):
        # JSON keys are strings; the lookup handles both spellings.
        project, video = _project(
            tmp_path, manifest={"transformation": {"clips": {"1": {"original_analysis": "x"}}}})
        assert pv._original_contribution(str(project), video)["meaningful"] is True

    def test_manifest_wrong_clip_index_ignored(self, tmp_path):
        project, video = _project(
            tmp_path, manifest={"transformation": {"clips": {"2": {"commentary": True}}}})
        assert pv._original_contribution(str(project), video)["meaningful"] is False

    def test_malformed_manifest_falls_through(self, tmp_path):
        project, video = _project(tmp_path)
        (tmp_path / "project_manifest.json").write_text("{not json", encoding="utf-8")
        assert pv._original_contribution(str(project), video)["meaningful"] is False

    def test_provenance_report_fallback(self, tmp_path):
        project, video = _project(
            tmp_path,
            provenance={"clips": [
                {"index": 1, "transformation": {
                    "status": "meaningful",
                    "evidence": [{"name": "voiceover"}, {"name": "broll"}]}}]})
        result = pv._original_contribution(str(project), video)
        assert result["meaningful"] is True
        assert result["signals"] == ["voiceover", "broll"]
        assert result["source"] == "provenance_report"

    def test_provenance_action_allow_counts(self, tmp_path):
        project, video = _project(
            tmp_path,
            provenance={"clips": [
                {"index": 1, "transformation": {"action": "allow", "evidence": []}}]})
        assert pv._original_contribution(str(project), video)["meaningful"] is True

    def test_provenance_wrong_index_ignored(self, tmp_path):
        project, video = _project(
            tmp_path,
            provenance={"clips": [
                {"index": 2, "transformation": {"status": "meaningful", "evidence": []}}]})
        assert pv._original_contribution(str(project), video)["meaningful"] is False

    def test_unindexed_video_manifest_without_clips(self, tmp_path):
        project, video = _project(
            tmp_path, manifest={"transformation": {"commentary": True}})
        video2 = tmp_path / "clip.mp4"
        video2.write_bytes(b"x")
        result = pv._original_contribution(str(project), str(video2))
        assert result["meaningful"] is True
        assert result["index"] is None


# ---------------------------------------------------------------------------
# Prior publishes
# ---------------------------------------------------------------------------

_YT = {"platform": "youtube", "status": "uploaded", "video": "01_clip.mp4"}
_TT = {"platform": "tiktok", "status": "uploaded", "video": "01_clip.mp4"}
_IG = {"platform": "instagram", "status": "scheduled", "variant_of": "01_clip.mp4"}
_FAILED = {"platform": "youtube", "status": "failed", "video": "01_clip.mp4"}
_OTHER = {"platform": "youtube", "status": "uploaded", "video": "99_other.mp4"}


class TestPriorPublishes:
    def test_matches_by_video_name(self, tmp_path):
        project, video = _project(tmp_path)
        events = [_YT, _TT, _FAILED, _OTHER]
        matches = pv.prior_other_platform_publishes(
            str(project), video, "tiktok", events)
        assert matches == [_YT]

    def test_target_platform_excluded(self, tmp_path):
        project, video = _project(tmp_path)
        matches = pv.prior_other_platform_publishes(
            str(project), video, "youtube", [_YT, _IG])
        assert matches == [_IG]

    def test_variant_of_matches(self, tmp_path):
        project, video = _project(tmp_path)
        matches = pv.prior_other_platform_publishes(
            str(project), video, "tiktok", [_IG])
        assert matches == [_IG]

    def test_non_dict_events_ignored(self, tmp_path):
        project, video = _project(tmp_path)
        matches = pv.prior_other_platform_publishes(
            str(project), video, "tiktok", ["junk", None, _YT])
        assert matches == [_YT]

    def test_fingerprint_match(self, tmp_path, monkeypatch):
        import webui.publish_history as ph

        project, video = _project(tmp_path)
        monkeypatch.setattr(ph, "file_fingerprint", lambda path: "fp-123")
        event = {"platform": "youtube", "status": "uploaded",
                 "video": "different_name.mp4", "file_fingerprint": "fp-123"}
        matches = pv.prior_other_platform_publishes(
            str(project), video, "tiktok", [event])
        assert matches == [event]

    def test_previous_target_attempts(self, tmp_path):
        project, video = _project(tmp_path)
        events = [_TT, _TT, _YT, {"platform": "tiktok", "status": "failed",
                                  "video": "01_clip.mp4"}]
        assert pv._previous_target_attempts(str(project), video, "tiktok", events) == 2
        assert pv._previous_target_attempts(str(project), video, "youtube", events) == 1


# ---------------------------------------------------------------------------
# plan_variant
# ---------------------------------------------------------------------------

class TestPlanVariant:
    def test_non_variant_platform(self, tmp_path):
        project, video = _project(tmp_path)
        decision = pv.plan_variant(str(project), video, "youtube")
        assert decision["action"] == "none"
        assert "does not need" in decision["reason"]

    def test_missing_clip_file(self, tmp_path):
        decision = pv.plan_variant(str(tmp_path), str(tmp_path / "nope.mp4"), "tiktok")
        assert decision["action"] == "none"
        assert decision["reason"] == "clip file not found"

    def test_unknown_policy(self, tmp_path):
        project, video = _project(tmp_path)
        decision = pv.plan_variant(str(project), video, "tiktok", policy="bogus")
        assert decision["action"] == "none"
        assert "unknown variant_policy 'bogus'" in decision["reason"]

    def test_policy_off(self, tmp_path):
        project, video = _project(tmp_path)
        decision = pv.plan_variant(str(project), video, "tiktok", policy="off")
        assert decision["action"] == "none"
        assert decision["reason"] == "variant_policy=off"

    def test_auto_no_siblings(self, tmp_path):
        project, video = _project(tmp_path)
        decision = pv.plan_variant(str(project), video, "tiktok", events=[])
        assert decision["action"] == "none"
        assert "no prior publish" in decision["reason"]

    def test_auto_siblings_no_contribution_needs_review(self, tmp_path):
        project, video = _project(tmp_path)
        decision = pv.plan_variant(str(project), video, "tiktok", events=[_YT])
        assert decision["action"] == "review"
        assert decision["requires_original_contribution"] is True
        assert decision["contribution"]["meaningful"] is False

    def test_auto_siblings_with_contribution_variates(self, tmp_path):
        project, video = _project(
            tmp_path, manifest={"transformation": {"clips": {"1": {"commentary": True}}}})
        decision = pv.plan_variant(str(project), video, "tiktok", events=[_YT])
        assert decision["action"] == "variate"
        assert decision["siblings"] == 1
        assert decision["attempt"] == 1
        assert decision["seed"] == pv.choose_seed(str(project), video, "tiktok", 1)
        assert isinstance(decision["preset"], dict)
        assert "republishing" in decision["reason"]

    def test_always_without_siblings_variates(self, tmp_path):
        project, video = _project(
            tmp_path, manifest={"transformation": {"clips": {"1": {"voiceover": True}}}})
        decision = pv.plan_variant(str(project), video, "tiktok",
                                   policy="always", events=[])
        assert decision["action"] == "variate"
        assert decision["siblings"] == 0
        assert "variant_policy=always" in decision["reason"]

    def test_always_without_contribution_needs_review(self, tmp_path):
        project, video = _project(tmp_path)
        decision = pv.plan_variant(str(project), video, "tiktok",
                                   policy="always", events=[])
        assert decision["action"] == "review"

    def test_attempt_counts_previous_target_publishes(self, tmp_path):
        project, video = _project(
            tmp_path, manifest={"transformation": {"clips": {"1": {"commentary": True}}}})
        events = [_YT, _TT, _TT]
        decision = pv.plan_variant(str(project), video, "tiktok", events=events)
        assert decision["attempt"] == 3
        assert decision["seed"] == pv.choose_seed(str(project), video, "tiktok", 3)

    def test_policy_case_and_whitespace_normalised(self, tmp_path):
        project, video = _project(tmp_path)
        decision = pv.plan_variant(str(project), video, "tiktok", policy=" OFF ")
        assert decision["action"] == "none"
        assert decision["policy"] == "off"


# ---------------------------------------------------------------------------
# maybe_variant (stubbed originality)
# ---------------------------------------------------------------------------

def _contributing_project(tmp_path):
    return _project(
        tmp_path, manifest={"transformation": {"clips": {"1": {"voiceover": True}}}})


class TestMaybeVariant:
    def _stub_originality(self, monkeypatch, transform=None, compare=None):
        import scripts.originality as orig
        if transform is not None:
            monkeypatch.setattr(orig, "transform_with_seed", transform)
        if compare is not None:
            monkeypatch.setattr(orig, "compare_clips", compare)

    def test_non_variate_plan_passes_through(self, tmp_path):
        project, video = _project(tmp_path)
        decision = pv.maybe_variant(str(project), video, "youtube", events=[])
        assert decision["action"] == "none"

    def test_verify_disabled_accepts_first_variant(self, tmp_path, monkeypatch):
        project, video = _contributing_project(tmp_path)
        monkeypatch.setenv("VIRALCUTTER_VARIANT_VERIFY", "0")

        def _transform(src, out, seed=None, preset=None, ffmpeg="ffmpeg"):
            with open(out, "wb") as f:
                f.write(b"variant")
            return {"ok": True, "transforms": ["mirror"]}

        self._stub_originality(monkeypatch, transform=_transform)
        decision = pv.maybe_variant(str(project), video, "tiktok", events=[_YT])
        assert decision["action"] == "variate"
        assert decision["path"].endswith(".mp4")
        assert os.path.isfile(decision["path"])
        assert decision["transforms"] == ["mirror"]
        assert "similarity" not in decision

    def test_verify_too_similar_keeps_best_effort(self, tmp_path, monkeypatch):
        project, video = _contributing_project(tmp_path)
        monkeypatch.setenv("VIRALCUTTER_VARIANT_VERIFY", "1")
        attempts = {"n": 0}

        def _transform(src, out, seed=None, preset=None, ffmpeg="ffmpeg"):
            attempts["n"] += 1
            with open(out, "wb") as f:
                f.write(b"variant")
            return {"ok": True, "transforms": ["mirror"]}

        self._stub_originality(
            monkeypatch, transform=_transform,
            compare=lambda a, b: {"similarity": 0.9})
        decision = pv.maybe_variant(str(project), video, "tiktok", events=[_YT])
        assert decision["action"] == "variate"
        assert attempts["n"] == pv.VERIFY_ATTEMPTS
        assert decision["similarity"] == 0.9
        assert "review before publishing" in decision["reason"]

    def test_verify_distinct_stops_early(self, tmp_path, monkeypatch):
        project, video = _contributing_project(tmp_path)
        monkeypatch.setenv("VIRALCUTTER_VARIANT_VERIFY", "1")
        attempts = {"n": 0}

        def _transform(src, out, seed=None, preset=None, ffmpeg="ffmpeg"):
            attempts["n"] += 1
            with open(out, "wb") as f:
                f.write(b"variant")
            return {"ok": True, "transforms": ["speed"]}

        self._stub_originality(
            monkeypatch, transform=_transform,
            compare=lambda a, b: {"similarity": 0.2})
        decision = pv.maybe_variant(str(project), video, "tiktok", events=[_YT])
        assert decision["action"] == "variate"
        assert attempts["n"] == 1
        assert decision["similarity"] == 0.2

    def test_compare_failure_accepts_best_effort(self, tmp_path, monkeypatch):
        project, video = _contributing_project(tmp_path)
        monkeypatch.setenv("VIRALCUTTER_VARIANT_VERIFY", "1")

        def _transform(src, out, seed=None, preset=None, ffmpeg="ffmpeg"):
            with open(out, "wb") as f:
                f.write(b"variant")
            return {"ok": True, "transforms": []}

        def _compare(a, b):
            raise RuntimeError("no cv2")

        self._stub_originality(monkeypatch, transform=_transform, compare=_compare)
        decision = pv.maybe_variant(str(project), video, "tiktok", events=[_YT])
        assert decision["action"] == "variate"
        # unverifiable similarity is not recorded on the decision at all
        assert "similarity" not in decision

    def test_transform_failure_falls_back_to_original(self, tmp_path, monkeypatch):
        project, video = _contributing_project(tmp_path)
        self._stub_originality(
            monkeypatch, transform=lambda *a, **k: {"ok": False})
        decision = pv.maybe_variant(str(project), video, "tiktok", events=[_YT])
        assert decision["action"] == "none"
        assert decision["path"] == video
        assert "transform reported failure" in decision["reason"]

    def test_transform_without_output_file_falls_back(self, tmp_path, monkeypatch):
        project, video = _contributing_project(tmp_path)
        # reports ok but writes nothing -> treated as failure
        self._stub_originality(
            monkeypatch, transform=lambda *a, **k: {"ok": True, "transforms": []})
        decision = pv.maybe_variant(str(project), video, "tiktok", events=[_YT])
        assert decision["action"] == "none"
        assert decision["path"] == video
