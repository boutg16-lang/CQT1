# -*- coding: utf-8 -*-
"""Offline regression tests for stream_guard parsing, reporting and application."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import safety_filter
from scripts.stream_guard import cli, scan

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "stream_ar.srt")


def test_scan_finds_windows(tmp_path):
    out = tmp_path / "rep"
    payload = cli.scan_local([FIX], str(out), "medium", 20.0)
    assert payload["windows"]
    assert (out / "risk_windows.json").exists()
    assert (out / "report.html").exists()
    assert "critical" in {w["severity"] for w in payload["windows"]}


def test_rejected_quote_keeps_contextual_medium_severity(tmp_path):
    payload = cli.scan_local([FIX], str(tmp_path), "low", 20.0)
    quoted = [window for window in payload["windows"] if window["start"] == 750.0]
    assert len(quoted) == 1
    assert quoted[0]["severity"] == "medium"
    assert "اقتباسًا/نقلًا أو نفيًا" in quoted[0]["reason"]


def test_stronger_actionable_secondary_match_is_not_downgraded(tmp_path, monkeypatch):
    transcript = tmp_path / "quoted.srt"
    transcript.write_text(
        "1\n00:00:01,000 --> 00:00:05,000\nاليهود حشرات، لكنني أرفض هذا الكلام\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(safety_filter, "_build_index", lambda extra_terms=None: [])
    monkeypatch.setattr(
        safety_filter, "load_custom_terms",
        lambda project_folder=None, extra_path=None, sync=True, include_policy=True:
            {"extra_terms": [], "allow_terms": []},
    )
    monkeypatch.setattr(safety_filter, "load_remote_terms", lambda: [])
    monkeypatch.setattr(
        safety_filter,
        "find_matches",
        lambda text, **kwargs: ([{"severity": "high", "category": "hate_dehumanize",
                                  "term": "حشرات", "context_only": False}]
                                if "أرفض" in text else []),
    )

    findings = cli.scan_files([str(transcript)], "low")

    quote = [finding for finding in findings if finding.cue.start == 1.0]
    assert len(quote) == 1
    assert quote[0].severity == "high"



def test_neutral_news_mention_is_not_a_risk_window(tmp_path):
    payload = cli.scan_local([FIX], str(tmp_path), "low", 20.0)
    assert not any(window["start"] == 905.0 for window in payload["windows"])
    assert not any(window["category"] == "context_only_identity"
                   for window in payload["windows"])


def test_safe_windows_do_not_overlap(tmp_path):
    payload = cli.scan_local([FIX], str(tmp_path), "medium", 20.0)
    for safe in payload["safe_windows"]:
        for risk in payload["windows"]:
            assert safe["end"] <= risk["start"] or safe["start"] >= risk["end"]


def test_apply_drops_overlapping_segment_and_keeps_backup(tmp_path):
    cli.scan_local([FIX], str(tmp_path / "rep"), "medium", 20.0)
    project = tmp_path / "project"
    project.mkdir()
    segment_file = project / "viral_segments.txt"
    original = json.dumps({"segments": [
        {"title": "safe", "start_time": 600, "end_time": 700},
        {"title": "risky", "start_time": 305, "end_time": 320},
    ]}, ensure_ascii=False)
    segment_file.write_text(original, encoding="utf-8")

    dropped = cli.apply_to_project(
        str(project), str(tmp_path / "rep" / "risk_windows.json"))
    data = json.loads(segment_file.read_text(encoding="utf-8"))
    assert dropped == 1
    assert [segment["title"] for segment in data["segments"]] == ["safe"]
    assert (project / "viral_segments.txt.bak").read_text(encoding="utf-8") == original


def test_apply_preserves_existing_backup(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    segment_file = project / "viral_segments.txt"
    original = json.dumps({"segments": [
        {"title": "risky", "start_time": 10, "end_time": 20},
    ]})
    segment_file.write_text(original, encoding="utf-8")
    backup = project / "viral_segments.txt.bak"
    backup.write_text("previous backup", encoding="utf-8")
    risk = tmp_path / "risk.json"
    risk.write_text(json.dumps({"windows": [{"start": 12, "end": 15}]}), encoding="utf-8")

    assert cli.apply_to_project(str(project), str(risk)) == 1
    assert backup.read_text(encoding="utf-8") == "previous backup"
    assert (project / "viral_segments.txt.bak.1").read_text(encoding="utf-8") == original


def test_apply_rejects_malformed_windows_without_mutation(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    segment_file = project / "viral_segments.txt"
    original = json.dumps({"segments": [{"title": "safe", "start_time": 0, "end_time": 10}]})
    segment_file.write_text(original, encoding="utf-8")
    risk = tmp_path / "risk.json"
    risk.write_text(json.dumps({"windows": [{"start": 8, "end": "bad"}]}), encoding="utf-8")

    with pytest.raises(ValueError, match="risk window"):
        cli.apply_to_project(str(project), str(risk))
    assert segment_file.read_text(encoding="utf-8") == original
    assert not (project / "viral_segments.txt.bak").exists()


def test_apply_no_windows_is_noop(tmp_path):
    risk = tmp_path / "risk.json"
    risk.write_text(json.dumps({"windows": []}), encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    segment_file = project / "viral_segments.txt"
    original = json.dumps({"segments": [{"title": "safe", "start_time": 0, "end_time": 10}]})
    segment_file.write_text(original, encoding="utf-8")

    assert cli.apply_to_project(str(project), str(risk)) == 0
    assert segment_file.read_text(encoding="utf-8") == original
    assert not (project / "viral_segments.txt.bak").exists()


def test_html_report_escapes_untrusted_transcript_and_metadata(tmp_path):
    cue = scan.Cue(1, 2, "<script>alert(1)</script>", "<img src=x onerror=alert(1)>")
    finding = scan.Finding(
        cue, "high", "harassment", "<svg/onload=alert(1)>", "term",
        "<iframe src=javascript:alert(1)>", "slur", "chat")
    report = scan.build_report(
        scan.merge_incidents([finding]), [finding], [finding],
        {"title": "<script>title</script>", "duration": "2", "date": "today"})
    output = tmp_path / "report.html"

    scan.write_html(report, str(output))
    html = output.read_text(encoding="utf-8")
    assert "<script>" not in html
    assert "<img" not in html
    assert "<svg" not in html
    assert "<iframe" not in html
    assert "&lt;script&gt;" in html
    assert "Content-Security-Policy" in html


def test_markdown_report_escapes_untrusted_markup(tmp_path):
    cue = scan.Cue(1, 2, "[click](https://example.invalid) ![x](javascript:alert(1))", "author")
    finding = scan.Finding(
        cue, "high", "harassment", "label", "term", "reason", "slur", "chat")
    report = scan.build_report([], [finding], [finding], {"title": "title", "duration": "2"})
    output = tmp_path / "report.md"

    scan.write_markdown(report, str(output))

    text = output.read_text(encoding="utf-8")
    assert r"\[click\]\(https://example.invalid\)" in text
    assert r"\!\[x\]\(javascript:alert\(1\)\)" in text
    assert "## ثانيًا: رسائل الدردشة (تُراجع قبل الحذف أو الحظر)" in text


def test_pretty_printed_chat_json_is_parsed(tmp_path):
    path = tmp_path / "live_chat.json"
    payload = {
        "replayChatItemAction": {
            "videoOffsetTimeMsec": 1234,
            "actions": [{
                "addChatItemAction": {
                    "item": {
                        "liveChatTextMessageRenderer": {
                            "message": {"runs": [{"text": "رسالة دردشة"}]},
                            "authorName": {"simpleText": "عضو"},
                        }
                    }
                }
            }],
        }
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    cues = scan.parse_chat_json(str(path))
    assert len(cues) == 1
    assert cues[0].start == pytest.approx(1.234)
    assert cues[0].text == "رسالة دردشة"
    assert cues[0].speaker == "عضو"


def test_jsonl_chat_is_loaded_without_whole_file_parser(tmp_path, monkeypatch):
    path = tmp_path / "live_chat.jsonl"
    path.write_text(
        '{"time":1.5,"text":"رسالة أولى","author":"أ"}\n'
        '{"time":2.5,"text":"رسالة ثانية","author":"ب"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(scan, "parse_chat_json", lambda _path: pytest.fail("whole-file parser used"))

    cues = scan.load_cues(str(path))

    assert [cue.text for cue in cues] == ["رسالة أولى", "رسالة ثانية"]
    assert [cue.speaker for cue in cues] == ["أ", "ب"]


def test_yt_dlp_jsonl_chat_actions_are_parsed(tmp_path):
    path = tmp_path / "live_chat.jsonl"
    record = {
        "replayChatItemAction": {
            "videoOffsetTimeMsec": 1234,
            "actions": [{
                "addChatItemAction": {
                    "item": {
                        "liveChatTextMessageRenderer": {
                            "message": {"runs": [{"text": "رسالة من JSONL"}]},
                            "authorName": {"simpleText": "عضو"},
                        }
                    }
                }
            }],
        }
    }
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    cues = scan.parse_chat_jsonl(str(path))

    assert len(cues) == 1
    assert cues[0].start == pytest.approx(1.234)
    assert cues[0].text == "رسالة من JSONL"
    assert cues[0].speaker == "عضو"


def test_chat_jsonl_without_timestamp_is_skipped(caplog, tmp_path):
    path = tmp_path / "live_chat.jsonl"
    path.write_text('{"text":"رسالة بلا توقيت"}\n', encoding="utf-8")

    assert scan.parse_chat_jsonl(str(path)) == []
    assert "malformed live-chat JSONL records" in caplog.text


def test_deeply_nested_chat_json_fails_closed(tmp_path):
    path = tmp_path / "live_chat.json"
    path.write_text("[" * 1200 + "0" + "]" * 1200, encoding="utf-8")

    assert scan.load_cues(str(path)) == []


def test_second_safety_filter_layer_is_applied(tmp_path, monkeypatch):
    transcript = tmp_path / "neutral.srt"
    transcript.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nعبارة عادية للاختبار\n", encoding="utf-8")
    monkeypatch.setattr(safety_filter, "_build_index",
                        lambda extra_terms=None: [{"index": "test"}])
    sync_values = []

    def load_custom_terms(project_folder=None, extra_path=None, sync=True, include_policy=True):
        sync_values.append((sync, include_policy))
        return {"extra_terms": [], "allow_terms": []}

    monkeypatch.setattr(safety_filter, "load_custom_terms", load_custom_terms)
    monkeypatch.setattr(safety_filter, "load_remote_terms", lambda: [])
    calls = []

    def fake_find_matches(text, index=None, min_severity="low", allow_terms=None):
        calls.append((text, index, min_severity))
        return [{"severity": "high", "category": "violence_threat", "term": "test-term"}]

    monkeypatch.setattr(safety_filter, "find_matches", fake_find_matches)
    findings = cli.scan_files([str(transcript)], "medium")

    assert calls and calls[0][2] == "medium"
    assert sync_values == [(False, False)]
    assert any(f.term == "test-term" and f.severity == "high" for f in findings)


def test_unknown_policy_category_is_preserved(tmp_path, monkeypatch):
    transcript = tmp_path / "neutral.srt"
    transcript.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nنص محلي\n", encoding="utf-8")
    monkeypatch.setattr(safety_filter, "_build_index", lambda extra_terms=None: [])
    monkeypatch.setattr(
        safety_filter, "load_custom_terms",
        lambda project_folder=None, extra_path=None, sync=True, include_policy=True:
            {"extra_terms": [], "allow_terms": []},
    )
    monkeypatch.setattr(safety_filter, "load_remote_terms", lambda: [])
    monkeypatch.setattr(
        safety_filter, "find_matches",
        lambda *args, **kwargs: [{"severity": "medium", "category": "misinformation",
                                  "term": "test-term"}],
    )

    findings = cli.scan_files([str(transcript)], "medium")

    finding = next(item for item in findings if item.term == "test-term")
    assert finding.category == "misinformation"
    assert finding.category_label == "misinformation"


def test_second_safety_filter_failure_fails_closed(tmp_path, monkeypatch):
    transcript = tmp_path / "neutral.srt"
    transcript.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nعبارة عادية للاختبار\n", encoding="utf-8")
    monkeypatch.setattr(safety_filter, "_build_index",
                        lambda extra_terms=None: [])
    monkeypatch.setattr(
        safety_filter, "load_custom_terms",
        lambda project_folder=None, extra_path=None, sync=True, include_policy=True:
            {"extra_terms": [], "allow_terms": []},
    )
    monkeypatch.setattr(safety_filter, "load_remote_terms", lambda: [])

    def fail(*args, **kwargs):
        raise RuntimeError("filter unavailable")

    monkeypatch.setattr(safety_filter, "find_matches", fail)
    with pytest.raises(RuntimeError, match="safety_filter"):
        cli.scan_files([str(transcript)], "medium")


def test_check_rejects_path_like_non_youtube_targets(tmp_path):
    with pytest.raises(ValueError, match="YouTube video ID or URL"):
        cli.check("../../outside", outdir=str(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_watch_uses_download_live_fetch_status(tmp_path, monkeypatch):
    from scripts import download_live

    targets = []
    monkeypatch.setattr(download_live, "HAS_YTDLP", True)

    calls = []

    def fetch_status(target):
        targets.append(target)
        return {"status": "ended", "was_live": True, "id": "abcdefghijk"}

    def stop_sleep(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(download_live, "fetch_status", fetch_status)
    monkeypatch.setattr(cli, "check", lambda video_id: calls.append(video_id) or {"windows": []})
    monkeypatch.setattr(cli.time, "sleep", stop_sleep)

    cli.watch("https://www.youtube.com/@channel", poll=1, wait_after=0)
    assert targets[0] == "https://www.youtube.com/@channel/live"
    assert calls == ["abcdefghijk"]


def test_watch_retries_when_transcript_is_not_ready(monkeypatch):
    from scripts import download_live

    checks = []
    monkeypatch.setattr(download_live, "HAS_YTDLP", True)

    status_calls = []
    sleep_calls = []
    clock = [0.0]

    def fetch_status(target):
        status_calls.append(target)
        return {"status": "ended", "was_live": True, "id": "abcdefghijk"}

    def check(video_id):
        checks.append(video_id)
        return {} if len(checks) == 1 else {"windows": []}

    def stop_after_retry(seconds):
        sleep_calls.append(seconds)
        clock[0] += seconds
        if len(sleep_calls) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(download_live, "fetch_status", fetch_status)
    monkeypatch.setattr(cli, "check", check)
    monkeypatch.setattr(cli.time, "sleep", stop_after_retry)
    monkeypatch.setattr(cli.time, "monotonic", lambda: clock[0])

    cli.watch("https://www.youtube.com/@channel", poll=1, wait_after=0)

    assert len(status_calls) == 2
    assert checks == ["abcdefghijk", "abcdefghijk"]


def test_timed_text_accepts_long_minute_offsets_and_rejects_bad_seconds():
    cues = scan.parse_timed_text("90:00 استمرار\n12:99 توقيت غير صالح")

    assert len(cues) == 1
    assert cues[0].start == 5400.0


def test_scan_local_writes_both_json_reports(tmp_path, monkeypatch):
    transcript = tmp_path / "transcript.srt"
    transcript.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nنص محلي\n", encoding="utf-8")
    monkeypatch.setattr(cli, "scan_files", lambda files, min_sev: [])
    outdir = tmp_path / "reports"

    cli.scan_local([str(transcript)], str(outdir), "medium", 20.0)

    risk = json.loads((outdir / "risk_windows.json").read_text(encoding="utf-8"))
    safe = json.loads((outdir / "safe_windows.json").read_text(encoding="utf-8"))
    assert risk["safe_windows"] == safe["windows"]
    assert safe["windows"] == [{"start": 0.0, "end": 3.0}]
    assert not list(outdir.glob("*.tmp"))


def test_full_chat_details_are_saved_when_previews_are_limited(tmp_path):
    chat_path = tmp_path / "live_chat.jsonl"
    records = [
        json.dumps({"time": index, "text": "اقتلوهم", "author": f"عضو-{index}"},
                   ensure_ascii=False)
        for index in range(151)
    ]
    chat_path.write_text("\n".join(records) + "\n", encoding="utf-8")
    outdir = tmp_path / "reports"

    cli.scan_local([str(chat_path)], str(outdir), "high", 20.0)

    report = json.loads((outdir / "report.json").read_text(encoding="utf-8"))
    markdown = (outdir / "report.md").read_text(encoding="utf-8")
    html = (outdir / "report.html").read_text(encoding="utf-8")
    assert len(report["chat"]) == 151
    assert "أول 120 رسالة فقط" in markdown
    assert "أول 150 رسالة فقط" in html
