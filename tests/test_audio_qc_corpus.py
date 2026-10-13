# -*- coding: utf-8 -*-
"""Tests for the licensed audio-QC corpus manager.

The end-to-end path is exercised with REAL FFmpeg: init → add generated
recordings → status → license-gated calibrate → thresholds file that
``scripts.audio_qc`` actually consumes.
"""
import json
import shutil
import subprocess

import pytest

from scripts import audio_qc_corpus as corpus

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                reason="ffmpeg required")


def _tone(path, *, frequency=440.0, duration=4.0, volume=0.5):
    """A short 'recording' WAV, distinct enough per clip for calibration."""
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i",
        "sine=frequency={}:sample_rate=44100,volume={}".format(frequency, volume),
        "-t", str(duration), str(path)], check=True)
    return str(path)


# ---------------------------------------------------------------------------
# Manifest / licensing
# ---------------------------------------------------------------------------

def test_init_writes_manifest(tmp_path):
    manifest = corpus.init_corpus(
        str(tmp_path / "c"), source="Own recordings", license_id="own-recording")
    assert manifest["license"]["id"] == "own-recording"
    on_disk = json.loads((tmp_path / "c" / corpus.MANIFEST_NAME)
                         .read_text(encoding="utf-8"))
    assert on_disk["source"]["name"] == "Own recordings"


def test_custom_license_requires_a_note(tmp_path):
    with pytest.raises(SystemExit):
        corpus.init_corpus(str(tmp_path / "c"), source="Somewhere",
                           license_id="custom")
    manifest = corpus.init_corpus(
        str(tmp_path / "c2"), source="Somewhere", license_id="custom",
        license_note="Permission granted by the author in writing")
    assert manifest["license"]["note"]


def test_cc_by_requires_attribution(tmp_path):
    with pytest.raises(SystemExit):
        corpus.init_corpus(str(tmp_path / "c"), source="CV", license_id="cc-by-4.0")
    manifest = corpus.init_corpus(
        str(tmp_path / "c2"), source="CV", license_id="cc-by-4.0",
        attribution="Some contributor")
    assert manifest["license"]["attribution"] == "Some contributor"


def test_calibrate_refuses_unlicensed_corpus(tmp_path):
    clips = tmp_path / "raw" / corpus.CLIPS_SUBDIR
    clips.mkdir(parents=True)
    _tone(str(clips / "001_x.mp4").replace(".mp4", ".wav"))
    with pytest.raises(SystemExit):
        corpus.calibrate_corpus(str(tmp_path / "raw"))


# ---------------------------------------------------------------------------
# Clip normalisation
# ---------------------------------------------------------------------------

def test_add_normalises_to_canonical_mp4(tmp_path):
    corpus.init_corpus(str(tmp_path / "c"), source="Own", license_id="own-recording")
    wav = _tone(str(tmp_path / "take.wav"))
    result = corpus.add_clips(str(tmp_path / "c"), [wav])
    assert result["added"] and not result["failed"]

    clip = tmp_path / "c" / corpus.CLIPS_SUBDIR / result["added"][0]
    assert clip.suffix == ".mp4"

    from scripts import media_validation
    probe = media_validation.probe_media(str(clip))
    assert probe["ok"] and probe["audio"] is not None
    assert probe["duration"] > 0


def test_add_refuses_without_manifest(tmp_path):
    wav = _tone(str(tmp_path / "take.wav"))
    with pytest.raises(SystemExit):
        corpus.add_clips(str(tmp_path / "c"), [wav])


def test_add_reports_failures_without_dying(tmp_path):
    corpus.init_corpus(str(tmp_path / "c"), source="Own", license_id="own-recording")
    wav = _tone(str(tmp_path / "good.wav"))
    result = corpus.add_clips(str(tmp_path / "c"), [wav, str(tmp_path / "missing.wav")])
    assert len(result["added"]) == 1
    assert len(result["failed"]) == 1


# ---------------------------------------------------------------------------
# Common Voice import
# ---------------------------------------------------------------------------

def _fake_cv_export(tmp_path, clips=3):
    cv = tmp_path / "cv_ar"
    (cv / "clips").mkdir(parents=True)
    rows = []
    for index in range(clips):
        name = "common_voice_ar_{}.mp3".format(index)
        _tone(str(cv / "clips" / name), frequency=330 + index * 60, duration=3)
        rows.append(name)
    (cv / "validated.tsv").write_text(
        "client_id\tpath\tsentence\n" +
        "".join("u{}\t{}\tجملة تجريبية\n".format(i, n) for i, n in enumerate(rows)),
        encoding="utf-8")
    return str(cv)


def test_import_common_voice_uses_cc0_manifest_and_validated_rows(tmp_path):
    cv_dir = _fake_cv_export(tmp_path, clips=4)
    result = corpus.import_common_voice(str(tmp_path / "corpus"), cv_dir)
    assert len(result["added"]) == 4

    manifest = corpus.load_manifest(str(tmp_path / "corpus"))
    assert manifest["license"]["id"] == "cc0"
    assert "commonvoice.mozilla.org" in manifest["source"]["url"]

    limited = corpus.import_common_voice(str(tmp_path / "corpus2"), cv_dir, limit=2)
    assert len(limited["added"]) == 2


# ---------------------------------------------------------------------------
# Status + end-to-end calibration
# ---------------------------------------------------------------------------

def _seed_corpus(tmp_path, count=5):
    corpus.init_corpus(str(tmp_path / "corpus"), source="Own", license_id="own-recording")
    sources = [
        _tone(str(tmp_path / "rec{}.wav".format(i)),
              frequency=380 + i * 40, duration=4.0)
        for i in range(count)
    ]
    corpus.add_clips(str(tmp_path / "corpus"), sources)
    return str(tmp_path / "corpus")


def test_status_tracks_calibration_readiness(tmp_path):
    corpus.init_corpus(str(tmp_path / "c"), source="Own", license_id="own-recording")
    status = corpus.corpus_status(str(tmp_path / "c"))
    assert status["calibration_ready"] is False

    corpus_dir = _seed_corpus(tmp_path, count=5)
    status = corpus.corpus_status(corpus_dir)
    assert status["calibration_ready"] is True
    assert status["clips"] == 5


def test_end_to_end_calibration_produces_usable_thresholds(tmp_path):
    corpus_dir = _seed_corpus(tmp_path, count=5)
    report = corpus.calibrate_corpus(corpus_dir)

    assert report["ok"] is True
    assert report["corpus"]["files_measured"] == 5
    assert report["corpus_manifest"]["license"] == "own-recording"

    thresholds_path = tmp_path / "corpus" / "audio_qc_thresholds.json"
    assert thresholds_path.is_file()
    payload = json.loads(thresholds_path.read_text(encoding="utf-8"))
    recommended = payload.get("recommended") or {}
    assert recommended, payload
    # The sane clamps must hold whatever the corpus sounded like.
    if recommended.get("target_i") is not None:
        assert -24.0 <= recommended["target_i"] <= -12.0


def test_cli_flow(tmp_path, capsys):
    corpus_dir = str(tmp_path / "c")
    assert corpus.main(["init", corpus_dir, "--source", "Own",
                        "--license", "own-recording"]) == 0
    wav = _tone(str(tmp_path / "a.wav"))
    assert corpus.main(["add", corpus_dir, wav]) == 0
    assert corpus.main(["status", corpus_dir, "--json"]) in (0, 2)
    out = capsys.readouterr().out
    assert "corpus" in out
