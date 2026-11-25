# -*- coding: utf-8 -*-
"""Tests for platform templates (Roadmap 5.2)."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import platform_templates as pt


class TestTemplates:
    def test_all_templates_present(self):
        assert set(pt.TEMPLATES) == {"yt_shorts", "tiktok", "reels", "yt_standard"}

    def test_shorts_max_60(self):
        assert pt.get_template("yt_shorts")["max_duration"] == 60

    def test_tiktok_and_reels_90(self):
        assert pt.get_template("tiktok")["max_duration"] == 90
        assert pt.get_template("reels")["max_duration"] == 90

    def test_aspects(self):
        for name in ("yt_shorts", "tiktok", "reels"):
            assert pt.get_template(name)["aspect"] == "9:16"
        assert pt.get_template("yt_standard")["aspect"] == "16:9"

    def test_unknown_template_none(self):
        assert pt.get_template("snapchat") is None


class TestResolve:
    def test_template_defaults(self):
        min_d, max_d, tpl = pt.resolve_durations("tiktok")
        assert (min_d, max_d) == (15, 90)
        assert tpl is not None

    def test_user_values_win(self):
        min_d, max_d, tpl = pt.resolve_durations(
            "tiktok", min_duration=20, max_duration=45
        )
        assert (min_d, max_d) == (20, 45)

    def test_unknown_template_keeps_fallback(self):
        min_d, max_d, tpl = pt.resolve_durations("nope")
        assert (min_d, max_d) == (15, 90)
        assert tpl is None

    def test_case_insensitive(self):
        min_d, max_d, _ = pt.resolve_durations("YT_SHORTS")
        assert max_d == 60


class TestPersist:
    def test_save_template_choice(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        pt.save_template_choice(str(project), "reels")
        with open(project / "process_config.json", encoding="utf-8") as f:
            data = json.load(f)
        assert data["platform_template"] == "reels"
        assert data["platform_template_info"]["max_duration"] == 90

    def test_save_preserves_existing_config(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / "process_config.json").write_text(
            json.dumps({"workflow": "1"}), encoding="utf-8"
        )
        pt.save_template_choice(str(project), "yt_shorts")
        with open(project / "process_config.json", encoding="utf-8") as f:
            data = json.load(f)
        assert data["workflow"] == "1"
        assert data["platform_template"] == "yt_shorts"

    def test_save_missing_project_is_safe(self, tmp_path):
        pt.save_template_choice(str(tmp_path / "absent"), "reels")  # no raise


class TestFunnelDescription:
    """feat/pro-cutting item 1 — funnel description + related-video policy."""

    FULL_URL = "https://www.youtube.com/watch?v=abcdefghijk"
    CHANNEL = "https://www.youtube.com/@MostefaBounif"

    def test_description_starts_with_full_video_line(self):
        text = pt.build_funnel_description(self.FULL_URL, self.CHANNEL, body="شرح")
        assert text.startswith(
            "شاهدوا الفيديو الكامل على القناة: " + self.FULL_URL
        )
        lines = text.splitlines()
        assert lines[1] == self.CHANNEL

    def test_description_order_url_channel_hashtags(self):
        text = pt.build_funnel_description(
            self.FULL_URL, self.CHANNEL, body="شرح", hashtags=["الجزائر", "#مصطفى_بونيف"]
        )
        assert "#الجزائر" in text
        assert "#مصطفى_بونيف" in text
        assert text.index(self.CHANNEL) < text.index("#الجزائر")
        assert text.index("شرح") < text.index("#الجزائر")

    def test_description_without_channel_link(self):
        text = pt.build_funnel_description(self.FULL_URL, "", body="", hashtags=None)
        assert text.startswith("شاهدوا الفيديو الكامل على القناة: ")
        assert text.strip().splitlines() == [
            "شاهدوا الفيديو الكامل على القناة: " + self.FULL_URL
        ]

    def test_empty_url_returns_body_only(self):
        assert pt.build_funnel_description("", "", body="نص") == "نص"

    def test_unknown_template_falls_back_to_shorts_funnel(self):
        text = pt.build_funnel_description(self.FULL_URL, template_name="nope")
        assert text.startswith("شاهدوا الفيديو الكامل على القناة: ")

    def test_related_video_policy_source_for_shorts(self):
        assert pt.related_video_policy("yt_shorts") == "source"
        assert pt.related_video_policy("yt_standard") == ""

    def test_title_pattern(self):
        assert pt.title_pattern("yt_shorts") == "{hook_quote} | الفيديو الكامل"

    def test_apply_title_pattern_appends_and_is_idempotent(self):
        assert (
            pt.apply_title_pattern("حوار ساخر")
            == "حوار ساخر | الفيديو الكامل"
        )
        assert (
            pt.apply_title_pattern("حوار ساخر | الفيديو الكامل")
            == "حوار ساخر | الفيديو الكامل"
        )
        assert pt.apply_title_pattern("") == ""
