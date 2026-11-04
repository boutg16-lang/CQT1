from scripts.pro_clip_review import review_clip, write_report


def test_review_blocks_clear_group_hate_and_violence():
    report = review_clip(
        transcript="يجب قتل المهاجرين وطردهم من البلاد",
        title="يجب قتل المهاجرين",
        language="ar",
        scan_ocr=False,
    )
    assert report["status"] == "blocked"
    assert report["publish_allowed"] is False
    assert report["safety"]["semantic"]["action"] == "block"


def test_review_does_not_auto_block_news_counter_speech_but_requires_review():
    report = review_clip(
        transcript="ندين خطاب الكراهية ضد المهاجرين ونرفض التحريض على العنف",
        title="ندين خطاب الكراهية ضد المهاجرين",
        language="ar",
        scan_ocr=False,
    )
    assert report["status"] in {"review", "approved_for_human_signoff"}
    assert report["status"] != "blocked"
    assert report["publish_allowed"] is False
    assert report["human_signoff_required"] is True


def test_review_rejects_title_with_unsupported_number():
    report = review_clip(
        transcript="شرح المتحدث أسباب القرار وتأثيره على المواطنين",
        title="خمسة أسباب وراء القرار",
        language="ar",
        scan_ocr=False,
    )
    assert report["title_summary"]["rejected"] == ["خمسة أسباب وراء القرار"]
    assert report["publish_allowed"] is False
    assert report["human_signoff_required"] is True
    assert report["automated_checks_passed"] is False


def test_review_report_is_written_atomically(tmp_path):
    report = review_clip(
        transcript="شرح المتحدث أسباب القرار وتأثيره على المواطنين",
        title="أسباب القرار وتأثيره على المواطنين",
        language="ar",
        scan_ocr=False,
    )
    path = write_report(str(tmp_path / "review.json"), report)
    assert (tmp_path / "review.json").is_file()
    assert '"schema_version": 1' in (tmp_path / "review.json").read_text(encoding="utf-8")
    assert path.endswith("review.json")
    assert report["publish_allowed"] is False
    assert report["human_signoff_required"] is True
    assert report["automated_checks_passed"] == (report["status"] == "approved_for_human_signoff")


def test_unavailable_ocr_requires_review_for_video(monkeypatch, tmp_path):
    from scripts import ocr_safety

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"placeholder")
    monkeypatch.setattr(ocr_safety, "analyze_video", lambda *args, **kwargs: {
        "available": False, "status": "unavailable", "action": "allow",
        "reason": "tesseract_not_installed", "frames": [],
    })
    report = review_clip(
        transcript="شرح المتحدث القرار وأسبابه بالتفصيل",
        title="شرح القرار وأسبابه بالتفصيل",
        video_path=str(video), language="ar", run_media_qc=False,
    )
    assert report["ocr"]["action"] == "review"
    assert report["status"] == "review"
    assert report["publish_allowed"] is False


def test_media_quality_block_prevents_publish(monkeypatch, tmp_path):
    from scripts import audio_qc, media_validation, ocr_safety

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"placeholder")
    monkeypatch.setattr(ocr_safety, "analyze_video", lambda *args, **kwargs: {
                            "available": True, "status": "scanned", "action": "allow",
                            "frames": [], "text": "",
                        })
    monkeypatch.setattr(media_validation, "validate_media_file", lambda *args, **kwargs: {
        "ok": False, "errors": ["missing audio stream"],
    })
    monkeypatch.setattr(audio_qc, "analyze_file", lambda *args, **kwargs: {
        "ok": False, "status": "block", "issues": [],
    })
    report = review_clip(
        transcript="شرح المتحدث القرار وأسبابه بالتفصيل",
        title="شرح القرار وأسبابه بالتفصيل",
        video_path=str(video), language="ar",
    )
    assert report["media_quality"]["action"] == "block"
    assert report["status"] == "blocked"
    assert report["publish_allowed"] is False


def test_skipped_video_checks_require_manual_review(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"placeholder")
    report = review_clip(
        transcript="شرح المتحدث القرار وأسبابه بالتفصيل",
        title="شرح القرار وأسبابه بالتفصيل",
        video_path=str(video), language="ar", scan_ocr=False,
        run_media_qc=False,
    )
    assert report["ocr"]["status"] == "skipped"
    assert report["ocr"]["action"] == "review"
    assert report["media_quality"]["status"] == "skipped"
    assert report["media_quality"]["action"] == "review"
    assert report["status"] == "review"
    assert report["automated_checks_passed"] is False
    assert report["human_signoff_required"] is True
    assert report["publish_allowed"] is False
