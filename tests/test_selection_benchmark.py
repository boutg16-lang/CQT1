# -*- coding: utf-8 -*-
"""Tests for the offline selection-quality benchmark."""
import json

import pytest

from scripts import selection_benchmark as sb

# ---------------------------------------------------------------------------
# Metric math
# ---------------------------------------------------------------------------

def test_coverage_math():
    assert sb.coverage((10, 20), (10, 20)) == 1.0
    assert sb.coverage((10, 20), (15, 25)) == 0.5
    assert sb.coverage((10, 20), (30, 40)) == 0.0
    assert sb.coverage((10, 20), (0, 100)) == 1.0
    assert sb.coverage((10, 10), (0, 100)) == 0.0  # degenerate gold


def test_evaluate_recall_at_k():
    gold = [(0, 30), (100, 130), (200, 230)]
    outputs = [(0, 30), (500, 530), (100, 130)]
    metrics = sb.evaluate(gold, outputs, k=3)
    assert metrics["gold_recalled"] == 2
    assert metrics["recall_at_k"] == pytest.approx(2 / 3, abs=1e-3)
    ranks = {tuple(d["gold"]): d["best_rank"] for d in metrics["details"]}
    assert ranks[(0.0, 30.0)] == 0
    assert ranks[(100.0, 130.0)] == 2
    assert ranks[(200.0, 230.0)] is None


def test_evaluate_respects_k_limit():
    gold = [(100, 130)]
    outputs = [(0, 30), (500, 530), (100, 130)]
    assert sb.evaluate(gold, outputs, k=2)["gold_recalled"] == 0
    assert sb.evaluate(gold, outputs, k=3)["gold_recalled"] == 1


def test_evaluate_empty_gold_is_safe():
    assert sb.evaluate([], [(0, 10)])["recall_at_k"] is None


# ---------------------------------------------------------------------------
# Fixture validation
# ---------------------------------------------------------------------------

def test_validate_fixture_accepts_the_demo():
    assert sb.validate_fixture(sb.demo_fixture()) == []


@pytest.mark.parametrize("fixture,fragment", [
    ({"transcript": [], "candidates": [{}], "gold": [{}]}, "transcript"),
    ({"transcript": [{"start": 0, "end": 1, "text": "x"}],
      "candidates": [], "gold": [{"start": 0, "end": 1}]}, "candidates"),
    ({"transcript": [{"start": 0, "end": 1, "text": "x"}],
      "candidates": [{"start_time": 0, "end_time": 1}],
      "gold": []}, "gold"),
    ({"transcript": [{"start": 0, "end": 1, "text": "x"}],
      "candidates": [{"start_time": 5, "end_time": 2}],
      "gold": [{"start": 0, "end": 1}]}, "start_time/end_time"),
])
def test_validate_fixture_rejects_bad_input(fixture, fragment):
    problems = sb.validate_fixture(fixture)
    assert problems
    assert any(fragment in problem for problem in problems)


def test_load_fixture_rejects_invalid_file(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"transcript": []}', encoding="utf-8")
    with pytest.raises(SystemExit):
        sb.load_fixture(str(bad))


# ---------------------------------------------------------------------------
# End-to-end (deterministic, offline)
# ---------------------------------------------------------------------------

def test_demo_run_is_deterministic_and_perfect():
    fixture = sb.demo_fixture()
    first = sb.run_benchmark(fixture)
    second = sb.run_benchmark(fixture)
    assert first == second
    assert first["metrics"]["gold_total"] == 3
    assert first["metrics"]["recall_at_k"] == 1.0
    # every gold window lands inside the top-3
    assert all(d["best_rank"] is not None and d["best_rank"] < 3
               for d in first["metrics"]["details"])


def test_cli_demo_writes_report(tmp_path, capsys):
    out = tmp_path / "report.json"
    assert sb.main(["--demo", "--output", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["tool"] == "selection_benchmark"
    assert report["metrics"]["recall_at_k"] is not None
    assert "recall@" in capsys.readouterr().out


def test_cli_with_a_real_fixture_file(tmp_path):
    fixture = sb.demo_fixture()
    fixture["name"] = "from-disk"
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
    assert sb.main(["--fixture", str(path), "-k", "2"]) == 0
