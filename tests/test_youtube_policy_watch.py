# -*- coding: utf-8 -*-
"""Direct tests for scripts/youtube_policy_watch.py.

Covers HTML normalisation/hashing, marker extraction, feed load/save,
check_policy_pages with a stubbed fetcher (unchanged / changed / offline /
partial failure / unwritable feed), the watch loop (sleep stubbed), and
main() in one-shot and --watch modes. No network.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import youtube_policy_watch as ypw  # noqa: E402

PAGE_A = "<html><head><style>a{}</style><script>var x=1;</script></head><body>  Hate Policy Text do not post hateful slurs here. </body></html>"
PAGE_B = "<html><body>COMPLETELY different hate policy do not post hateful slurs here.</body></html>"


# ---------------------------------------------------------------------------
# _normalize_html / _page_hash / _extract_markers
# ---------------------------------------------------------------------------

class TestNormalizeAndHash:
    def test_strips_script_style_and_tags(self):
        assert ypw._normalize_html(PAGE_A) == "hate policy text do not post hateful slurs here."

    def test_casefold_and_whitespace_collapse(self):
        assert ypw._normalize_html("<p>  Hello   WORLD\n\nagain </p>") == "hello world again"

    def test_empty_html(self):
        assert ypw._normalize_html("") == ""
        assert ypw._normalize_html("<br>") == ""

    def test_hash_stable_for_same_content(self):
        assert ypw._page_hash("<b>Hello</b>") == ypw._page_hash("hello")
        assert ypw._page_hash("<b>Hello</b>") == ypw._page_hash("<i>HELLO</i>")

    def test_hash_changes_with_content(self):
        assert ypw._page_hash("hello") != ypw._page_hash("goodbye")

    def test_hash_is_sha256_hex(self):
        digest = ypw._page_hash("hello")
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)


class TestExtractMarkers:
    def test_finds_do_not_markers(self):
        text = "do not upload hateful content here. this is fine. policy: be kind to others always."
        assert ypw._extract_markers(text) == [
            "do not upload hateful content here.",
            "policy: be kind to others always."]

    def test_short_and_long_snippets_skipped(self):
        assert ypw._extract_markers("do not x.") == []
        assert ypw._extract_markers("do not " + "a" * 200 + ".") == []

    def test_caps_at_eight(self):
        text = " ".join("do not do thing number {} here please.".format(i) for i in range(20))
        assert len(ypw._extract_markers(text)) == 8

    def test_no_markers(self):
        assert ypw._extract_markers("a calm page about cats.") == []


# ---------------------------------------------------------------------------
# feed helpers
# ---------------------------------------------------------------------------

class TestFeedHelpers:
    def test_feed_path_default_is_repo_root(self):
        assert ypw.feed_path() == os.path.join(ypw._repo_root(), ypw.FEED_FILENAME)

    def test_feed_path_with_base_dir(self, tmp_path):
        assert ypw.feed_path(str(tmp_path)) == str(tmp_path / ypw.FEED_FILENAME)

    def test_load_feed_missing(self, tmp_path):
        assert ypw.load_feed(str(tmp_path)) == {}

    def test_load_feed_malformed(self, tmp_path):
        (tmp_path / ypw.FEED_FILENAME).write_text("{bad json", encoding="utf-8")
        assert ypw.load_feed(str(tmp_path)) == {}

    def test_load_feed_non_dict(self, tmp_path):
        (tmp_path / ypw.FEED_FILENAME).write_text("[1, 2]", encoding="utf-8")
        assert ypw.load_feed(str(tmp_path)) == {}

    def test_load_feed_round_trip(self, tmp_path):
        data = {"hashes": {"hate_speech": "abc"}}
        (tmp_path / ypw.FEED_FILENAME).write_text(json.dumps(data), encoding="utf-8")
        assert ypw.load_feed(str(tmp_path)) == data


# ---------------------------------------------------------------------------
# check_policy_pages (stubbed fetcher)
# ---------------------------------------------------------------------------

def _stub_fetch(monkeypatch, pages):
    """pages: dict key -> html, or Exception to raise for every fetch."""
    def _fetch(url):
        if isinstance(pages, Exception):
            raise pages
        for key, page_url in ypw.POLICY_PAGES.items():
            if url == page_url:
                return pages[key]
        raise AssertionError("unexpected url: " + url)

    monkeypatch.setattr(ypw, "_fetch_text", _fetch)


class TestCheckPolicyPages:
    def test_first_run_unchanged_and_writes_feed(self, tmp_path, monkeypatch):
        _stub_fetch(monkeypatch, dict.fromkeys(ypw.POLICY_PAGES, PAGE_A))
        result = ypw.check_policy_pages(base_dir=str(tmp_path))
        assert result["status"] == "unchanged"
        assert result["changes"] == []
        assert result["markers"] == {}
        assert result["feed_written"] is True
        assert result["offline_pages"] == []
        feed = ypw.load_feed(str(tmp_path))
        assert set(feed["hashes"]) == set(ypw.POLICY_PAGES)
        assert feed["last_change_detected_at"] is None

    def test_second_run_unchanged(self, tmp_path, monkeypatch):
        _stub_fetch(monkeypatch, dict.fromkeys(ypw.POLICY_PAGES, PAGE_A))
        ypw.check_policy_pages(base_dir=str(tmp_path))
        result = ypw.check_policy_pages(base_dir=str(tmp_path))
        assert result["status"] == "unchanged"
        assert result["changes"] == []

    def test_changed_page_detected_with_markers(self, tmp_path, monkeypatch):
        _stub_fetch(monkeypatch, dict.fromkeys(ypw.POLICY_PAGES, PAGE_A))
        ypw.check_policy_pages(base_dir=str(tmp_path))
        pages = dict.fromkeys(ypw.POLICY_PAGES, PAGE_A)
        pages["hate_speech"] = PAGE_B
        _stub_fetch(monkeypatch, pages)
        result = ypw.check_policy_pages(base_dir=str(tmp_path))
        assert result["status"] == "changed"
        assert result["changes"] == ["hate_speech"]
        assert "hate_speech" in result["markers"]
        feed = ypw.load_feed(str(tmp_path))
        assert feed["last_change_detected_at"] == result["checked_at"]

    def test_all_fetches_fail_is_offline(self, tmp_path, monkeypatch):
        _stub_fetch(monkeypatch, RuntimeError("offline"))
        result = ypw.check_policy_pages(base_dir=str(tmp_path))
        assert result["status"] == "offline"
        assert result["changes"] == []
        assert result["feed_written"] is False
        assert not (tmp_path / ypw.FEED_FILENAME).exists()

    def test_partial_failure_lists_offline_pages(self, tmp_path, monkeypatch):
        def _fetch(url):
            if "2801939" in url:  # hate_speech fails
                raise RuntimeError("offline")
            return PAGE_A

        monkeypatch.setattr(ypw, "_fetch_text", _fetch)
        result = ypw.check_policy_pages(base_dir=str(tmp_path))
        assert result["status"] == "unchanged"
        assert result["offline_pages"] == ["hate_speech"]
        assert "hate_speech" not in ypw.load_feed(str(tmp_path))["hashes"]

    def test_unwritable_feed_reports_not_written(self, tmp_path, monkeypatch):
        _stub_fetch(monkeypatch, dict.fromkeys(ypw.POLICY_PAGES, PAGE_A))
        real_open = open

        def _failing_open(path, mode="r", *args, **kwargs):
            if "w" in mode and str(path).endswith(ypw.FEED_FILENAME):
                raise OSError("read-only")
            return real_open(path, mode, *args, **kwargs)

        monkeypatch.setattr("builtins.open", _failing_open)
        result = ypw.check_policy_pages(base_dir=str(tmp_path))
        assert result["status"] == "unchanged"
        assert result["feed_written"] is False

    def test_force_parameter_accepted(self, tmp_path, monkeypatch):
        _stub_fetch(monkeypatch, dict.fromkeys(ypw.POLICY_PAGES, PAGE_A))
        result = ypw.check_policy_pages(base_dir=str(tmp_path), force=True)
        assert result["status"] == "unchanged"


# ---------------------------------------------------------------------------
# watch / main
# ---------------------------------------------------------------------------

class TestWatch:
    def test_one_cycle(self, monkeypatch):
        calls = {"n": 0}

        def _check(base_dir=None, force=False):
            calls["n"] += 1
            return {"status": "unchanged"}

        monkeypatch.setattr(ypw, "check_policy_pages", _check)
        assert ypw.watch(max_cycles=1) == 1
        assert calls["n"] == 1

    def test_two_cycles_sleeps_once(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(time, "sleep", lambda seconds: sleeps.append(seconds))
        monkeypatch.setattr(ypw, "check_policy_pages",
                            lambda base_dir=None, force=False: {"status": "unchanged"})
        assert ypw.watch(interval_hours=2, max_cycles=2) == 2
        assert sleeps == [2 * 3600]

    def test_interval_clamped_to_one_hour(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(time, "sleep", lambda seconds: sleeps.append(seconds))
        monkeypatch.setattr(ypw, "check_policy_pages",
                            lambda base_dir=None, force=False: {"status": "unchanged"})
        ypw.watch(interval_hours=0.001, max_cycles=2)
        assert sleeps == [3600.0]

    def test_changed_cycle_printed(self, monkeypatch, capsys):
        monkeypatch.setattr(ypw, "check_policy_pages",
                            lambda base_dir=None, force=False: {
                                "status": "changed", "changes": ["hate_speech"]})
        assert ypw.watch(max_cycles=1) == 1
        out = capsys.readouterr().out
        assert "cycle 1: changed" in out
        assert "CHANGED policies: hate_speech" in out


class TestMain:
    def test_one_shot_prints_status(self, monkeypatch, capsys):
        monkeypatch.setattr(ypw, "check_policy_pages",
                            lambda: {"status": "unchanged", "changes": []})
        monkeypatch.setattr(sys, "argv", ["prog"])
        ypw.main()
        assert "status=unchanged changes=none" in capsys.readouterr().out

    def test_one_shot_with_changes(self, monkeypatch, capsys):
        monkeypatch.setattr(ypw, "check_policy_pages",
                            lambda: {"status": "changed", "changes": ["harassment"]})
        monkeypatch.setattr(sys, "argv", ["prog"])
        ypw.main()
        assert "status=changed changes=harassment" in capsys.readouterr().out

    def test_watch_mode(self, monkeypatch, capsys):
        monkeypatch.setattr(ypw, "watch", lambda interval_hours=None, max_cycles=None: 3)
        monkeypatch.setattr(sys, "argv", ["prog", "--watch", "24", "--cycles", "3"])
        ypw.main()
        assert "finished after 3 cycle(s)" in capsys.readouterr().out
