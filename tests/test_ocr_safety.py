import subprocess

import pytest

from scripts import ocr_safety


def test_unavailable_tesseract_is_safe_and_explicit(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ocr_safety,
        "availability",
        lambda *_: {
            "available": False,
            "binary": None,
            "reason": "tesseract_not_installed",
        },
    )
    report = ocr_safety.analyze_video(str(tmp_path / "clip.mp4"))
    assert report["available"] is False
    assert report["reason"] == "tesseract_not_installed"
    assert report["action"] == "review"


def test_ocr_text_is_sent_through_policy_engine(monkeypatch):
    monkeypatch.setattr(
        ocr_safety,
        "availability",
        lambda *_: {
            "available": True,
            "binary": "/usr/bin/tesseract",
            "reason": None,
        },
    )
    monkeypatch.setattr(ocr_safety, "_duration", lambda *_: 2.0)
    monkeypatch.setattr(ocr_safety, "_frame_png", lambda *_: b"png")
    monkeypatch.setattr(ocr_safety, "_recognize", lambda *_: "kill them all")
    report = ocr_safety.analyze_video("clip.mp4", frames=1)
    assert report["status"] == "scanned"
    assert report["action"] == "block"
    assert report["score"] == 100
    assert report["matches"]


def test_missing_selected_language_fails_closed_without_english_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ocr_safety,
        "availability",
        lambda *_: {
            "available": True,
            "binary": "/usr/bin/tesseract",
            "reason": None,
        },
    )
    monkeypatch.setattr(ocr_safety, "_duration", lambda *_: 2.0)
    monkeypatch.setattr(ocr_safety, "_frame_png", lambda *_: b"png")

    def recognize(_png, _binary, language):
        calls.append(language)
        if language == "ara+eng":
            raise RuntimeError("selected language data is missing")
        return ""

    monkeypatch.setattr(ocr_safety, "_recognize", recognize)
    report = ocr_safety.analyze_video("clip.mp4", frames=1, language="ara+eng")

    assert calls == ["ara+eng"]
    assert report["status"] == "failed"
    assert report["available"] is False
    assert report["reason"] == "frame_sampling_incomplete"
    assert report["action"] == "review"
    assert report["score"] >= 70
    assert "selected language data is missing" in report["frames"][0]["error"]


def test_recognize_reports_tesseract_stderr_for_language_errors(monkeypatch):
    error = subprocess.CalledProcessError(
        1, ["tesseract"], stderr=b"Error opening ara.traineddata"
    )
    monkeypatch.setattr(
        ocr_safety.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )
    with pytest.raises(RuntimeError, match="ara.traineddata"):
        ocr_safety._recognize(b"png", "/usr/bin/tesseract", "ara+eng")
