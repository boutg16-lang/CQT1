"""Safety-gate helpers must report data they had to skip instead of hiding it."""
import json

from scripts import censor_engine


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_load_word_transcript_reports_unreadable_timestamps(tmp_path, capsys):
    _write(tmp_path / "input.json", {"segments": [{"words": [
        {"word": "ok", "start": 1.0, "end": 1.5},
        {"word": "bad", "start": None, "end": 2.0},
    ]}]})
    words = censor_engine.load_word_transcript(str(tmp_path))
    assert [w["word"] for w in words] == ["ok"]
    assert "Skipped 1 word(s)" in capsys.readouterr().out


def test_load_word_transcript_is_quiet_when_all_words_readable(tmp_path, capsys):
    _write(tmp_path / "input.json", {"segments": [{"words": [
        {"word": "ok", "start": 0.0, "end": 0.4}]}]})
    assert len(censor_engine.load_word_transcript(str(tmp_path))) == 1
    assert capsys.readouterr().out == ""


def test_mask_subtitle_json_reports_unmaskable_words(tmp_path, capsys):
    sub = tmp_path / "cut.json"
    _write(sub, {"segments": [{"words": [
        {"word": "bad", "start": 1.0, "end": 1.4},
        {"word": "broken", "start": None, "end": 2.0},
    ]}]})
    masked = censor_engine.mask_subtitle_json(
        str(sub), [{"start": 0.9, "end": 1.5}], mask="***")
    assert masked == 1
    out = capsys.readouterr().out
    assert "Could not mask 1 word(s)" in out
    assert str(sub) not in out
    saved = json.loads(sub.read_text(encoding="utf-8"))
    assert saved["segments"][0]["words"][0]["word"] == "***"
