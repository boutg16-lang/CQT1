import json

from scripts import audio_qc
from scripts import audio_qc_calibrate as calibrate_mod


def _clip(name, input_i, input_tp, ratio=0.1, duration=30.0):
    return {
        "file": name,
        "path": "/corpus/" + name,
        "duration": duration,
        "input_i": input_i,
        "input_tp": input_tp,
        "silence_ratio": ratio,
    }


def _fake_analyze(metrics_by_name):
    def fake(path, **kwargs):
        name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        metrics = metrics_by_name.get(name)
        if metrics is None:
            return {"ok": False, "status": "block", "duration": 0.0,
                    "metrics": {}, "issues": [{"code": "loudness_failed"}]}
        return {"ok": True, "status": "pass", "duration": metrics.pop("duration", 30.0),
                "metrics": metrics, "issues": []}
    return fake


def test_percentile_interpolates_linearly():
    assert calibrate_mod._percentile([], 50) is None
    assert calibrate_mod._percentile([7.5], 95) == 7.5
    assert calibrate_mod._percentile([0.0, 10.0], 50) == 5.0
    assert calibrate_mod._percentile([0.0, 10.0, 20.0], 25) == 5.0
    assert calibrate_mod._percentile([0.0, 100.0], 95) == 95.0


def test_clamp_and_round_half():
    assert calibrate_mod._clamp(-30.0, (-24.0, -12.0)) == -24.0
    assert calibrate_mod._clamp(-14.0, (-24.0, -12.0)) == -14.0
    assert calibrate_mod._round_half(-16.24) == -16.0
    assert calibrate_mod._round_half(-16.26) == -16.5
    assert calibrate_mod._round_half(-15.74) == -15.5


def test_measure_corpus_splits_measured_and_skipped(tmp_path, monkeypatch):
    for name in ("a.mp4", "b.mp4", "broken.mp4", "notes.txt"):
        (tmp_path / name).write_bytes(b"x")
    metrics = {
        "a.mp4": {"input_i": -16.0, "input_tp": -2.0, "silence": {"ratio": 0.1}},
        "b.mp4": {"input_i": -18.0, "input_tp": -3.0, "silence": {"ratio": 0.2}},
    }
    monkeypatch.setattr(audio_qc, "analyze_file", _fake_analyze(metrics))

    result = calibrate_mod.measure_corpus(str(tmp_path))

    assert result["total_candidates"] == 3  # notes.txt is not a video
    assert [m["file"] for m in result["measured"]] == ["a.mp4", "b.mp4"]
    assert result["measured"][0]["silence_ratio"] == 0.1
    assert result["skipped"] == [{"file": "broken.mp4", "reason": "loudness_failed"}]


def test_measure_corpus_honours_limit_and_missing_dir(tmp_path, monkeypatch):
    assert calibrate_mod.measure_corpus(str(tmp_path / "nope"))["total_candidates"] == 0
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        (tmp_path / name).write_bytes(b"x")
    metrics = {name: {"input_i": -16.0, "input_tp": -2.0, "silence": {"ratio": 0.0}}
               for name in ("a.mp4", "b.mp4", "c.mp4")}
    monkeypatch.setattr(audio_qc, "analyze_file", _fake_analyze(metrics))
    result = calibrate_mod.measure_corpus(str(tmp_path), limit=2)
    assert result["total_candidates"] == 2
    assert len(result["measured"]) == 2


def test_recommend_thresholds_is_deterministic():
    measured = [
        _clip("c1.mp4", -15.0, -1.5, 0.05),
        _clip("c2.mp4", -16.0, -2.0, 0.10),
        _clip("c3.mp4", -16.5, -2.5, 0.15),
        _clip("c4.mp4", -17.0, -3.0, 0.20),
        _clip("c5.mp4", -17.5, -3.5, 0.25),
    ]
    first = calibrate_mod.recommend_thresholds(measured)
    second = calibrate_mod.recommend_thresholds(measured)
    assert first == second

    recommended = first["recommended"]
    # corpus median loudness is -16.5
    assert recommended["target_i"] == -16.5
    # p95 true peak = -1.75 → minus 0.5 margin → -2.25 → half-rounded -2.0
    assert recommended["target_tp"] == -2.0
    # p05/p95 loudness widened by 2 LU: -15.125-2 ≈ -17.0 / -17.375+2 ≈ -15.5
    low, high = recommended["loudness_review_range"]
    assert low < -15.0 and high > -17.5  # wider than the raw corpus span
    assert low <= recommended["target_i"] <= high
    # p95 silence ratio = 0.24 + 0.10 margin, clamped & rounded
    assert recommended["max_silence_ratio"] == 0.34
    assert first["statistics"]["input_i"]["p50"] == -16.5


def test_recommend_thresholds_clamps_skewed_corpus():
    whisper_quiet = [_clip("q%d.mp4" % i, -50.0 - i, -20.0, 0.9) for i in range(6)]
    recommended = calibrate_mod.recommend_thresholds(whisper_quiet)["recommended"]
    assert recommended["target_i"] == -24.0  # never below the sanity floor
    assert recommended["target_tp"] == -6.0  # clamped, never follows the corpus
    assert recommended["max_silence_ratio"] == 0.8  # clamped ceiling


def test_recommend_thresholds_handles_missing_silence():
    measured = [_clip("c1.mp4", -16.0, -2.0, None) for _ in range(5)]
    recommended = calibrate_mod.recommend_thresholds(measured)["recommended"]
    assert recommended["max_silence_ratio"] is None


def test_calibrate_fails_below_min_files(tmp_path, monkeypatch):
    (tmp_path / "only.mp4").write_bytes(b"x")
    monkeypatch.setattr(audio_qc, "analyze_file", _fake_analyze(
        {"only.mp4": {"input_i": -16.0, "input_tp": -2.0, "silence": {"ratio": 0.1}}}))
    report = calibrate_mod.calibrate(str(tmp_path), min_files=5)
    assert report["ok"] is False
    assert report["recommended"] is None
    assert "corpus too small" in report["error"]
    assert report["corpus"]["files_measured"] == 1


def test_calibrate_report_round_trips_through_json(tmp_path, monkeypatch):
    metrics = {}
    for index in range(6):
        name = "clip%d.mp4" % index
        (tmp_path / name).write_bytes(b"x")
        metrics[name] = {"input_i": -15.0 - index * 0.5, "input_tp": -2.0,
                         "silence": {"ratio": 0.1}}
    monkeypatch.setattr(audio_qc, "analyze_file", _fake_analyze(metrics))

    report = calibrate_mod.calibrate(str(tmp_path))
    assert report["ok"] is True
    target = calibrate_mod.write_report(str(tmp_path / "out" / "thresholds.json"), report)
    with open(target, "r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    assert loaded["recommended"] == report["recommended"]
    assert loaded["corpus"]["files_measured"] == 6


def test_main_exit_codes(tmp_path, monkeypatch, capsys):
    (tmp_path / "a.mp4").write_bytes(b"x")
    monkeypatch.setattr(audio_qc, "analyze_file", _fake_analyze(
        {"a.mp4": {"input_i": -16.0, "input_tp": -2.0, "silence": {"ratio": 0.1}}}))
    assert calibrate_mod.main(["--corpus", str(tmp_path), "--min-files", "1"]) == 0
    assert calibrate_mod.main(["--corpus", str(tmp_path), "--min-files", "9"]) == 2
    assert (tmp_path / calibrate_mod.DEFAULT_OUTPUT_NAME).exists()
    out = capsys.readouterr().out
    assert "target_i=" in out and "FAIL" in out


def test_load_thresholds_accepts_calibrator_output(tmp_path):
    payload = {"ok": True, "recommended": {"target_i": -16.5, "target_tp": -2.5}}
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert audio_qc.load_thresholds(str(path)) == {"target_i": -16.5, "target_tp": -2.5}


def test_load_thresholds_rejects_broken_files(tmp_path):
    assert audio_qc.load_thresholds(str(tmp_path / "missing.json")) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert audio_qc.load_thresholds(str(bad)) is None
    failed = tmp_path / "failed.json"
    failed.write_text(json.dumps({"ok": False, "recommended": None}), encoding="utf-8")
    assert audio_qc.load_thresholds(str(failed)) is None
    insane = tmp_path / "insane.json"
    insane.write_text(json.dumps(
        {"ok": True, "recommended": {"target_i": -99.0, "target_tp": 5.0}}), encoding="utf-8")
    assert audio_qc.load_thresholds(str(insane)) is None


def test_audio_qc_main_wires_thresholds_file(tmp_path, monkeypatch):
    captured = {}

    def fake_analyze_project(project, **kwargs):
        captured.update(kwargs)
        return {"status": "pass", "summary": {}, "ok": True}

    monkeypatch.setattr(audio_qc, "analyze_project", fake_analyze_project)
    thresholds = tmp_path / "thresholds.json"
    thresholds.write_text(json.dumps(
        {"ok": True, "recommended": {"target_i": -18.0, "target_tp": -3.0}}), encoding="utf-8")

    assert audio_qc.main(["--project", str(tmp_path),
                          "--thresholds-file", str(thresholds)]) == 0
    assert captured["target_i"] == -18.0
    assert captured["target_tp"] == -3.0

    # explicit flags still win over the file
    assert audio_qc.main(["--project", str(tmp_path), "--target-i", "-14.0",
                          "--thresholds-file", str(thresholds)]) == 0
    assert captured["target_i"] == -14.0
    assert captured["target_tp"] == -3.0

    # a broken file falls back to defaults without crashing
    thresholds.write_text("{broken", encoding="utf-8")
    assert audio_qc.main(["--project", str(tmp_path),
                          "--thresholds-file", str(thresholds)]) == 0
    assert captured["target_i"] == audio_qc.DEFAULT_TARGET_I
