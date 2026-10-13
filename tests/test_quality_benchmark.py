import shutil

import pytest

from scripts.quality_benchmark import run_benchmark

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not available")


def test_quality_benchmark_passes(tmp_path):
    output = tmp_path / "benchmark.json"
    report = run_benchmark(str(output))
    assert report["passed"] is True
    assert report["gate_failures"] == []
    assert report["media_validation"]["ok"] is True
    assert report["media_validation"]["path"] == "fixture.mp4"
    assert report["audio_qc"]["status"] == "pass"
    assert report["audio_qc"]["path"] == "fixture.mp4"
