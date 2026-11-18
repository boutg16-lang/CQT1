# -*- coding: utf-8 -*-
"""Direct tests for scripts/arabic_lexicon_importer.py.

Covers term extraction from the upstream README markdown (headings,
bullets, slash variants, transliteration, length filter), the section
import rules, build_terms categories/severities/dedup, the fetch wrapper
(mocked urlopen), main() dry-run / fetch-failure / --write (to a tmp
repo root via a patched __file__), and _merge_into_blocklist with a
fake safety_filter module (no network, no real blocklist mutation).
"""

import json
import os
import sys
import types
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import arabic_lexicon_importer as ali  # noqa: E402

SAMPLE = """
# Arabic Offensive Lexicon

## 1. General Profanity
* **كلمة سيئة (bad word):** a bad word

## 4. Identity-Based Slurs
* **زنجي / زنوج (zanji):** a slur
* **ك (k):** too short, dropped by the length filter
* **كلمة طويلة جدا (long):** fine

## 4.4 Misogynist Phrases
* **عبارة (phrase):** sexist phrase

## 5. Dehumanizing Insults
* **كلب (dog):** dehumanizing

## 7. Other Stuff
* **شيء (thing):** not imported
"""


# ---------------------------------------------------------------------------
# _extract_terms
# ---------------------------------------------------------------------------

class TestExtractTerms:
    def test_sections_grouped_by_heading(self):
        sections = ali._extract_terms(SAMPLE)
        titles = [title for title, _ in sections]
        assert titles == ["1. General Profanity", "4. Identity-Based Slurs",
                          "4.4 Misogynist Phrases", "5. Dehumanizing Insults",
                          "7. Other Stuff"]

    def test_slash_variants_split(self):
        sections = dict(ali._extract_terms(SAMPLE))
        assert sections["4. Identity-Based Slurs"] == [
            "زنجي", "زنوج", "كلمة طويلة جدا"]

    def test_transliteration_dropped(self):
        sections = dict(ali._extract_terms(SAMPLE))
        assert all("(" not in term for terms in sections.values() for term in terms)

    def test_short_terms_dropped(self):
        sections = dict(ali._extract_terms(SAMPLE))
        assert "ك" not in sections["4. Identity-Based Slurs"]

    def test_bullets_before_any_heading_ignored(self):
        text = "* **قبل (before):** no heading yet\n\n## 4. Identity-Based Slurs\n* **بعد (after):** kept\n"
        sections = ali._extract_terms(text)
        assert sections == [("4. Identity-Based Slurs", ["بعد"])]

    def test_empty_text(self):
        assert ali._extract_terms("") == []
        assert ali._extract_terms("no headings here\n* **x (y):** z\n") == []

    def test_long_terms_dropped(self):
        text = "## 4. Identity-Based Slurs\n* **" + "ا" * 61 + " (long):** too long\n"
        # the over-long term is filtered, leaving an empty section, which is
        # not appended at all
        assert ali._extract_terms(text) == []


# ---------------------------------------------------------------------------
# _should_import / build_terms
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("4. Identity-Based Slurs", True),
    ("4.1 Something", True),
    ("4.4 Misogynist Phrases", True),
    ("5. Dehumanizing Insults", True),
    ("6. Dehumanizing Insults", True),
    ("Random Slurs Here", True),
    ("Misogynist language", True),
    ("Sexist terms", True),
    ("1. General Profanity", False),
    ("7. Other Stuff", False),
    ("", False),
])
def test_should_import(title, expected):
    assert ali._should_import(title) is expected


class TestBuildTerms:
    def test_categories_and_severities(self):
        terms = {(t["term"], t["category"], t["severity"]) for t in ali.build_terms(SAMPLE)}
        assert ("زنجي", "hate_slur", "high") in terms
        assert ("زنوج", "hate_slur", "high") in terms
        assert ("عبارة", "harassment", "medium") in terms
        assert ("كلب", "hate_dehumanize", "medium") in terms

    def test_excluded_sections_not_imported(self):
        terms = [t["term"] for t in ali.build_terms(SAMPLE)]
        assert "كلمة سيئة" not in terms
        assert "شيء" not in terms

    def test_all_terms_arabic(self):
        assert all(t["lang"] == "ar" for t in ali.build_terms(SAMPLE))

    def test_duplicate_terms_deduplicated_casefold(self):
        text = ("## 4. Identity-Based Slurs\n"
                "* **زنجي (a):** one\n* **زنجي (b):** two\n")
        terms = ali.build_terms(text)
        assert [t["term"] for t in terms] == ["زنجي"]

    def test_empty_text_builds_nothing(self):
        assert ali.build_terms("") == []


# ---------------------------------------------------------------------------
# _fetch
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, n=-1):
        return self._data


class TestFetch:
    def test_fetch_sends_user_agent_and_decodes(self, monkeypatch):
        seen = {}

        def _urlopen(req, timeout=None):
            seen["url"] = req.full_url
            seen["ua"] = req.headers.get("User-agent")
            seen["timeout"] = timeout
            return _FakeResponse("héllo".encode("utf-8"))

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        assert ali._fetch() == "héllo"
        assert seen["url"] == ali.LEXICON_URL
        assert seen["ua"] == "ViralCutter-lexicon-importer"
        assert seen["timeout"] == ali.FETCH_TIMEOUT

    def test_fetch_failure_propagates(self, monkeypatch):
        def _raise(req, timeout=None):
            raise RuntimeError("offline")

        monkeypatch.setattr(urllib.request, "urlopen", _raise)
        with pytest.raises(RuntimeError, match="offline"):
            ali._fetch()


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

class TestMain:
    def test_dry_run_returns_zero_and_prints_stats(self, monkeypatch, capsys):
        monkeypatch.setattr(ali, "_fetch", lambda url=ali.LEXICON_URL: SAMPLE)
        monkeypatch.setattr(sys, "argv", ["prog", "--dry-run"])
        assert ali.main() == 0
        out = capsys.readouterr().out
        assert "imported 5 high-severity terms" in out
        assert "hate_slur: 3" in out

    def test_fetch_failure_returns_one(self, monkeypatch, capsys):
        def _raise(url=ali.LEXICON_URL):
            raise RuntimeError("offline")

        monkeypatch.setattr(ali, "_fetch", _raise)
        monkeypatch.setattr(sys, "argv", ["prog", "--dry-run"])
        assert ali.main() == 1
        assert "fetch failed" in capsys.readouterr().out

    def test_write_creates_pack_in_tmp_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ali, "_fetch", lambda url=ali.LEXICON_URL: SAMPLE)
        # point the module's __file__ at a tmp "repo root" so --write lands there
        monkeypatch.setattr(ali, "__file__", str(tmp_path / "scripts" / "arabic_lexicon_importer.py"))
        monkeypatch.setattr(sys, "argv", ["prog", "--write"])
        assert ali.main() == 0
        pack = json.loads((tmp_path / ali.OUT_FILENAME).read_text(encoding="utf-8"))
        assert pack["version"] == 1
        assert pack["source"].startswith("Mohammed Attia")
        assert len(pack["upstream_sha256"]) == 64
        assert len(pack["terms"]) == 5
        assert all(t["lang"] == "ar" for t in pack["terms"])

    def test_write_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ali, "_fetch", lambda url=ali.LEXICON_URL: SAMPLE)
        monkeypatch.setattr(ali, "__file__", str(tmp_path / "scripts" / "arabic_lexicon_importer.py"))
        monkeypatch.setattr(sys, "argv", ["prog", "--dry-run"])
        assert ali.main() == 0
        assert not (tmp_path / ali.OUT_FILENAME).exists()


# ---------------------------------------------------------------------------
# _merge_into_blocklist (fake safety_filter)
# ---------------------------------------------------------------------------

def _fake_safety_filter(monkeypatch, blocklist):
    fake = types.SimpleNamespace(BLOCKLIST=list(blocklist))
    monkeypatch.setitem(sys.modules, "scripts.safety_filter", fake)
    import scripts
    monkeypatch.setattr(scripts, "safety_filter", fake, raising=False)
    return fake


class TestMergeIntoBlocklist:
    def test_appends_missing_terms(self, monkeypatch):
        fake = _fake_safety_filter(monkeypatch, [("existing", "ar", "high", "hate_slur")])
        terms = [
            {"term": "زنجي", "lang": "ar", "severity": "high", "category": "hate_slur"},
            {"term": "newterm", "lang": "ar", "severity": "medium", "category": "harassment"},
        ]
        ali._merge_into_blocklist(terms)
        assert ("زنجي", "ar", "high", "hate_slur") in fake.BLOCKLIST
        assert ("newterm", "ar", "medium", "harassment") in fake.BLOCKLIST
        assert len(fake.BLOCKLIST) == 3

    def test_idempotent_second_merge_adds_nothing(self, monkeypatch):
        fake = _fake_safety_filter(monkeypatch, [])
        terms = [{"term": "زنجي", "lang": "ar", "severity": "high", "category": "hate_slur"}]
        ali._merge_into_blocklist(terms)
        ali._merge_into_blocklist(terms)
        assert len(fake.BLOCKLIST) == 1

    def test_casefold_dedup(self, monkeypatch):
        fake = _fake_safety_filter(monkeypatch, [("Term", "ar", "high", "hate_slur")])
        ali._merge_into_blocklist(
            [{"term": "TERM", "lang": "ar", "severity": "high", "category": "hate_slur"}])
        assert len(fake.BLOCKLIST) == 1
