# -*- coding: utf-8 -*-
"""Direct tests for scripts/translate_json.py.

Covers the GoogleTranslator adapter (mocked HTTP), the RTL helpers, the
sentence join/unjoin machinery, segment word-timing adjustment, and the
async JSON translation pipeline (translator mocked, no network).
"""

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import translate_json as tj  # noqa: E402

# ---------------------------------------------------------------------------
# GoogleTranslator
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload=None, status_error=None):
        self._payload = payload
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error

    def json(self):
        return self._payload


def _fake_get(payload, status_error=None, capture=None):
    def _get(url, params=None, headers=None, timeout=None):
        if capture is not None:
            capture.update(url=url, params=params, headers=headers, timeout=timeout)
        return _FakeResponse(payload, status_error)

    return _get


class TestGoogleTranslator:
    def test_init_defaults(self):
        tr = tj.GoogleTranslator()
        assert tr.source == "auto"
        assert tr.target == "en"
        assert tr.timeout == 30
        assert tr.user_agent.startswith("OUSSAMA-Cutter/")

    def test_init_empty_source_becomes_auto(self):
        assert tj.GoogleTranslator(source="").source == "auto"
        assert tj.GoogleTranslator(source="fr", target="ar", timeout=5).target == "ar"

    def test_translate_joins_parts(self, monkeypatch):
        monkeypatch.setattr(
            tj.requests,
            "get",
            _fake_get(
                [
                    [["Hola", "Hello", None, None], ["mundo", "world", None, None]],
                    None,
                    "en",
                ]
            ),
        )
        assert tj.GoogleTranslator(target="es").translate("Hello world") == "Holamundo"

    def test_translate_sends_expected_params(self, monkeypatch):
        capture = {}
        monkeypatch.setattr(
            tj.requests,
            "get",
            _fake_get([[["x", "y", None, None]], None, "en"], capture=capture),
        )
        tj.GoogleTranslator(source="fr", target="ar", timeout=7).translate("bonjour")
        assert capture["url"] == tj.GoogleTranslator.endpoint
        assert capture["params"] == {
            "client": "gtx",
            "sl": "fr",
            "tl": "ar",
            "dt": "t",
            "q": "bonjour",
        }
        assert capture["headers"]["User-Agent"].startswith("OUSSAMA-Cutter/")
        assert capture["timeout"] == 7

    def test_translate_skips_empty_parts(self, monkeypatch):
        monkeypatch.setattr(
            tj.requests,
            "get",
            _fake_get(
                [[["", "x", None, None], None, ["A", "y", None, None]], None, "en"]
            ),
        )
        assert tj.GoogleTranslator().translate("xy") == "A"

    def test_translate_invalid_payload_raises(self, monkeypatch):
        for bad in ({"bad": True}, [], "not-a-list", None):
            monkeypatch.setattr(tj.requests, "get", _fake_get(bad))
            with pytest.raises(ValueError, match="invalid response"):
                tj.GoogleTranslator().translate("hello")

    def test_translate_empty_payload_list_raises_empty_text(self, monkeypatch):
        # payload[0] is an empty list: the shape check passes, but there is
        # nothing to join, so the empty-text error fires instead.
        monkeypatch.setattr(tj.requests, "get", _fake_get([[]]))
        with pytest.raises(ValueError, match="empty text"):
            tj.GoogleTranslator().translate("hello")

    def test_translate_empty_text_raises(self, monkeypatch):
        monkeypatch.setattr(
            tj.requests, "get", _fake_get([[["   ", "x", None, None]], None, "en"])
        )
        with pytest.raises(ValueError, match="empty text"):
            tj.GoogleTranslator().translate("hello")

    def test_translate_http_error_propagates(self, monkeypatch):
        monkeypatch.setattr(
            tj.requests,
            "get",
            _fake_get(None, status_error=RuntimeError("403 Forbidden")),
        )
        with pytest.raises(RuntimeError, match="403 Forbidden"):
            tj.GoogleTranslator().translate("hello")


# ---------------------------------------------------------------------------
# RTL helpers and substitutions
# ---------------------------------------------------------------------------


class TestRtlHelpers:
    @pytest.mark.parametrize(
        "lang,expected",
        [
            ("ar", True),
            ("ar-EG", True),
            ("AR", True),
            ("he", True),
            ("fa", True),
            ("ur", True),
            ("en", False),
            ("fr", False),
            ("", False),
            (None, False),
        ],
    )
    def test_is_rtl_language(self, lang, expected):
        assert tj.is_rtl_language(lang) is expected

    def test_apply_rtl_text(self):
        assert tj.apply_rtl_text("abc") == "\u202babc\u202c"
        assert tj.apply_rtl_text("") == ""
        assert tj.apply_rtl_text(None) is None

    def test_should_render_rtl(self):
        assert tj.should_render_rtl("ar") is True
        assert tj.should_render_rtl("en") is False

    def test_substituir_texto(self):
        assert tj.substituir_texto("hello world", {"hello": "hola"}) == "hola world"
        assert tj.substituir_texto("aaa", {"a": "b"}) == "bbb"
        assert tj.substituir_texto("unchanged", {}) == "unchanged"


# ---------------------------------------------------------------------------
# translate_chunk (async, retries)
# ---------------------------------------------------------------------------


class _TranslatorStub:
    def __init__(self, results, *args, **kwargs):
        self._results = list(results)
        self.calls = 0

    def translate(self, text):
        self.calls += 1
        outcome = self._results.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class TestTranslateChunk:
    def test_success_first_try(self, monkeypatch):
        monkeypatch.setattr(
            tj, "GoogleTranslator", lambda *a, **k: _TranslatorStub(["TRANSLATED"])
        )
        assert (
            asyncio.run(
                tj.translate_chunk(0, "hello", "es", max_attempts=3, retry_delay=0)
            )
            == "TRANSLATED"
        )

    def test_retry_then_success(self, monkeypatch):
        calls = {"n": 0}

        class _Flaky:
            def __init__(self, *args, **kwargs):
                pass

            def translate(self, text):
                calls["n"] += 1
                if calls["n"] < 3:
                    raise RuntimeError("boom")
                return "OK3"

        monkeypatch.setattr(tj, "GoogleTranslator", _Flaky)
        result = asyncio.run(
            tj.translate_chunk(1, "hello", "es", max_attempts=3, retry_delay=0)
        )
        assert result == "OK3"
        assert calls["n"] == 3

    def test_always_fails_returns_original(self, monkeypatch):
        class _Bad:
            def __init__(self, *args, **kwargs):
                pass

            def translate(self, text):
                raise RuntimeError("nope")

        monkeypatch.setattr(tj, "GoogleTranslator", _Bad)
        assert (
            asyncio.run(
                tj.translate_chunk(2, "orig text", "es", max_attempts=2, retry_delay=0)
            )
            == "orig text"
        )

    @pytest.mark.parametrize("bad_result", [None, " ◌ ", "   ", ""])
    def test_empty_translation_returns_original(self, monkeypatch, bad_result):
        class _Empty:
            def __init__(self, *args, **kwargs):
                pass

            def translate(self, text):
                return bad_result

        monkeypatch.setattr(tj, "GoogleTranslator", _Empty)
        assert (
            asyncio.run(
                tj.translate_chunk(3, "orig", "es", max_attempts=1, retry_delay=0)
            )
            == "orig"
        )

    def test_max_attempts_zero_means_one(self, monkeypatch):
        calls = {"n": 0}

        class _Bad:
            def __init__(self, *args, **kwargs):
                pass

            def translate(self, text):
                calls["n"] += 1
                raise RuntimeError("nope")

        monkeypatch.setattr(tj, "GoogleTranslator", _Bad)
        assert (
            asyncio.run(
                tj.translate_chunk(4, "orig", "es", max_attempts=0, retry_delay=0)
            )
            == "orig"
        )
        assert calls["n"] == 1


# ---------------------------------------------------------------------------
# join_sentences / unjoin_sentences
# ---------------------------------------------------------------------------


class TestJoinSentences:
    def test_empty(self):
        assert tj.join_sentences([], 100) == []

    def test_sentence_ending_flushes(self):
        assert tj.join_sentences(["Hello world.", "Second one."], 100) == [
            "Hello world. ◌ ",
            "Second one. ◌ ",
        ]

    def test_no_ending_accumulates(self):
        assert tj.join_sentences(["hello", "world"], 100) == ["hello ◌ world ◌ "]

    def test_none_text_becomes_placeholder(self):
        assert tj.join_sentences([None, "ok."], 100) == ["ㅤ ◌ ok. ◌ "]

    def test_long_text_split_with_ellipsis(self):
        assert tj.join_sentences(["a" * 30], 10) == ["aaaaaa… ◌ "]

    def test_chunk_boundary_forces_new_chunk(self):
        chunks = tj.join_sentences(["aaa.", "bbb.", "ccc."], 12)
        assert chunks == ["aaa. ◌ ", "bbb. ◌ ", "ccc. ◌ "]


class TestUnjoinSentences:
    def test_none_original(self):
        assert tj.unjoin_sentences(None, "x", tj.separator) == " "

    def test_none_modified_returns_originals(self):
        assert tj.unjoin_sentences("a ◌ b", None, tj.separator) == ["a", "b"]

    def test_equal_counts(self):
        assert tj.unjoin_sentences("a ◌ b", "A ◌ B", tj.separator) == ["A", "B"]

    def test_mismatched_counts_reallocates_by_words(self):
        result = tj.unjoin_sentences(
            "one two three ◌ four five", "uno dos tres cuatro cinco", tj.separator
        )
        assert result == ["uno dos tres", "cuatro cinco"]

    def test_leftover_words_appended_to_last(self):
        # proportion 4/2 = 2.0: "one" takes 2 words, "two" takes the rest.
        result = tj.unjoin_sentences("one ◌ two", "uno dos tres cuatro", tj.separator)
        assert result == ["uno dos", "tres cuatro"]

    def test_zero_word_counts_falls_back(self):
        # modified side has no words at all -> original sentence returned.
        assert tj.unjoin_sentences("◌", "◌", tj.separator) == "◌"


# ---------------------------------------------------------------------------
# adjust_segments
# ---------------------------------------------------------------------------


class TestAdjustSegments:
    def test_word_timing_even_split(self):
        segments = [{"text": "hello world", "start": 0.0, "end": 4.0}]
        out = tj.adjust_segments([dict(s) for s in segments])
        words = out[0]["words"]
        assert [(w["word"], w["start"], w["end"], w["score"]) for w in words] == [
            ("hello", 0.0, 2.0, 1.0),
            ("world", 2.0, 4.0, 1.0),
        ]

    def test_next_segment_caps_last_word_end(self):
        segments = [
            {"text": "one two", "start": 0.0, "end": 10.0},
            {"text": "next", "start": 8.0, "end": 10.0},
        ]
        out = tj.adjust_segments([dict(s) for s in segments])
        # last word of segment 0 starts at 5.0; its end is capped at
        # min(next_start=8.0, start+2=7.0) = 7.0 (was 10.0).
        assert out[0]["words"][-1]["end"] == 7.0
        assert out[0]["end"] == 7.0
        # next segment's first word start is forced to its segment start
        assert out[1]["words"][0]["start"] == 8.0

    def test_last_word_end_never_moves_before_its_start(self):
        # next segment starts at 3.0, before the last word even starts (5.0):
        # max(start, extended_end) keeps the end at the word start.
        segments = [
            {"text": "one two", "start": 0.0, "end": 10.0},
            {"text": "next", "start": 3.0, "end": 5.0},
        ]
        out = tj.adjust_segments([dict(s) for s in segments])
        assert out[0]["words"][-1]["end"] == 5.0
        assert out[0]["end"] == 5.0

    def test_last_segment_end_extended_by_two(self):
        segments = [{"text": "one two", "start": 0.0, "end": 4.0}]
        out = tj.adjust_segments([dict(s) for s in segments])
        assert out[0]["words"][-1]["end"] == 4.0
        assert out[0]["end"] == 4.0

    def test_empty_text_gets_no_words(self):
        segments = [{"text": "", "start": 1.0, "end": 2.0}]
        out = tj.adjust_segments([dict(s) for s in segments])
        assert out[0]["words"] == []

    def test_returns_same_list(self):
        segments = [{"text": "x", "start": 0.0, "end": 1.0}]
        assert tj.adjust_segments(segments) is segments


# ---------------------------------------------------------------------------
# translate_json_file / translate_project_subs / main
# ---------------------------------------------------------------------------


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class TestTranslateJsonFile:
    def test_full_flow_uppercases_text_and_words(self, tmp_path, monkeypatch):
        async def fake_chunk(index, chunk, lang, max_attempts=4, retry_delay=5):
            return chunk.upper()

        monkeypatch.setattr(tj, "translate_chunk", fake_chunk)
        src = {
            "segments": [
                {
                    "text": "hello world",
                    "start": 0.0,
                    "end": 2.0,
                    "words": [
                        {"word": "hello", "start": 0.0, "end": 1.0},
                        {"word": "world", "start": 1.0, "end": 2.0},
                    ],
                },
                {"text": "second line", "start": 2.0, "end": 4.0, "words": []},
            ]
        }
        inp = tmp_path / "in.json"
        out = tmp_path / "sub" / "out.json"
        _write_json(inp, src)
        data = asyncio.run(tj.translate_json_file(inp, out, "es"))
        assert [s["text"] for s in data["segments"]] == ["HELLO WORLD", "SECOND LINE"]
        assert [w["word"] for s in data["segments"] for w in s["words"]] == [
            "HELLO",
            "WORLD",
            "SECOND",
            "LINE",
        ]
        assert out.exists()
        assert _read_json(out)["segments"][0]["text"] == "HELLO WORLD"

    def test_non_list_segments_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            tj, "translate_chunk", lambda *a, **k: asyncio.sleep(0, result="x")
        )
        inp = tmp_path / "in.json"
        _write_json(inp, {"segments": "nope"})
        with pytest.raises(ValueError, match="segments list"):
            asyncio.run(tj.translate_json_file(inp, tmp_path / "out.json", "es"))

    def test_empty_segments_ok(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            tj, "translate_chunk", lambda *a, **k: asyncio.sleep(0, result="x")
        )
        inp = tmp_path / "in.json"
        _write_json(inp, {"segments": []})
        data = asyncio.run(tj.translate_json_file(inp, tmp_path / "out.json", "es"))
        assert data["segments"] == []

    def test_segments_without_text_skipped(self, tmp_path, monkeypatch):
        async def fake_chunk(index, chunk, lang, max_attempts=4, retry_delay=5):
            return chunk.upper()

        monkeypatch.setattr(tj, "translate_chunk", fake_chunk)
        inp = tmp_path / "in.json"
        _write_json(inp, {"segments": [{"start": 0.0, "end": 1.0}]})
        data = asyncio.run(tj.translate_json_file(inp, tmp_path / "out.json", "es"))
        assert data["segments"][0].get("text", "") == ""
        assert data["segments"][0]["words"] == []


class TestTranslateProjectSubs:
    def test_missing_subs_folder_returns_none(self, tmp_path):
        assert asyncio.run(tj.translate_project_subs(str(tmp_path), "es")) is None

    def test_empty_subs_folder_returns_none(self, tmp_path):
        (tmp_path / "subs").mkdir()
        assert asyncio.run(tj.translate_project_subs(str(tmp_path), "es")) is None

    def test_backup_created_and_file_translated(self, tmp_path, monkeypatch):
        async def fake_tjf(src_path, dst_path, lang):
            data = _read_json(src_path)
            data["segments"][0]["text"] = "HI"
            _write_json(dst_path, data)
            return data

        monkeypatch.setattr(tj, "translate_json_file", fake_tjf)
        subs = tmp_path / "subs"
        subs.mkdir()
        src = {"segments": [{"text": "hi", "start": 0.0, "end": 1.0, "words": []}]}
        _write_json(subs / "clip_processed.json", src)
        asyncio.run(tj.translate_project_subs(str(tmp_path), "es"))
        backup = subs / "clip_processed_original.json"
        assert backup.exists()
        assert _read_json(backup)["segments"][0]["text"] == "hi"
        assert _read_json(subs / "clip_processed.json")["segments"][0]["text"] == "HI"

    def test_existing_backup_is_used_as_source(self, tmp_path, monkeypatch):
        seen = {}

        async def fake_tjf(src_path, dst_path, lang):
            seen["src"] = src_path.name
            data = _read_json(src_path)
            _write_json(dst_path, data)
            return data

        monkeypatch.setattr(tj, "translate_json_file", fake_tjf)
        subs = tmp_path / "subs"
        subs.mkdir()
        _write_json(
            subs / "clip_processed.json",
            {"segments": [{"text": "new", "start": 0.0, "end": 1.0, "words": []}]},
        )
        _write_json(
            subs / "clip_processed_original.json",
            {"segments": [{"text": "old", "start": 0.0, "end": 1.0, "words": []}]},
        )
        asyncio.run(tj.translate_project_subs(str(tmp_path), "es"))
        assert seen["src"] == "clip_processed_original.json"


class TestMain:
    def test_main_translates_json_folder(self, tmp_path, monkeypatch):
        async def fake_tjf(src_path, dst_path, lang):
            data = _read_json(src_path)
            data["segments"][0]["text"] = "TRANSLATED"
            _write_json(dst_path, data)
            return data

        monkeypatch.setattr(tj, "translate_json_file", fake_tjf)
        monkeypatch.chdir(tmp_path)
        folder = tmp_path / "JSON"
        folder.mkdir()
        _write_json(
            folder / "a.json",
            {"segments": [{"text": "hello", "start": 0.0, "end": 1.0, "words": []}]},
        )
        asyncio.run(tj.main())
        assert (folder / "a_en.json").exists()
        assert _read_json(folder / "a_en.json")["segments"][0]["text"] == "TRANSLATED"

    def test_main_missing_json_folder_raises(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(FileNotFoundError):
            asyncio.run(tj.main())

    def test_main_empty_json_folder_ok(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "JSON").mkdir()
        asyncio.run(tj.main())  # no files, no crash
