from scripts import ocr_safety


def test_unavailable_ocr_requires_review(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ocr_safety,
        "availability",
        lambda *_: {"available": False, "binary": None, "reason": "missing"},
    )
    r = ocr_safety.analyze_video(str(tmp_path / "clip.mp4"))
    assert r["status"] == "unavailable" and r["action"] == "review"


def test_partial_ocr_sampling_requires_review(monkeypatch):
    monkeypatch.setattr(
        ocr_safety,
        "availability",
        lambda *_: {"available": True, "binary": "tesseract", "reason": None},
    )
    monkeypatch.setattr(ocr_safety, "_duration", lambda *_: 10.0)
    monkeypatch.setattr(
        ocr_safety, "_frame_png", lambda path, seconds, *_: str(seconds).encode()
    )

    def recognize(png, *_):
        if png == b"5.0":
            raise RuntimeError("OCR failed")
        return "ordinary caption"

    monkeypatch.setattr(ocr_safety, "_recognize", recognize)
    r = ocr_safety.analyze_video("clip.mp4", frames=3)
    assert r["status"] == "partial" and r["action"] == "review" and r["score"] >= 70


def test_sample_count_is_bounded():
    assert len(ocr_safety._sample_times(100, 10000)) == 60
