from scripts import upload_gate as ug
from webui import app, publish_panel


def test_protector_edits_field_starts_empty():
    component = next(
        (
            component
            for component in app.demo.get_config_file()["components"]
            if component.get("props", {}).get("label")
            == "JSON — التعديلات المعتمدة بشرياً"
        ),
        None,
    )
    assert component is not None
    assert component["props"]["value"] == "[]"


def _validation_args(**overrides):
    values = {
        "input_source": "YouTube URL",
        "project_name": None,
        "url": "https://youtu.be/abc",
        "video_file": None,
        "segments": 3,
        "min_duration": 15,
        "max_duration": 60,
        "workflow": "Full",
        "ai_backend": "manual",
        "transcription_device": "auto",
        "safety_mode": "block",
        "visual_check": "auto",
        "visual_model": "",
        "logo_path": "",
        "music_path": "",
        "auto_upload": False,
        "auto_upload_dry_run": True,
        "auto_upload_source": "auto",
        "auto_upload_specific_file": None,
        "auto_upload_privacy": "private",
        "auto_upload_publish_at": "",
        "auto_upload_public_confirm": False,
    }
    values.update(overrides)
    return values


def test_expected_project_clip_count_reads_current_segments(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "viral_segments.txt").write_text(
        '{"segments": [{"start_time": 0, "end_time": 5}, {"start_time": 10, "end_time": 15}]}',
        encoding="utf-8",
    )
    assert app._expected_project_clip_count(str(project)) == 2
    assert app._expected_project_clip_count(str(tmp_path / "missing")) == 0


def test_public_upload_is_blocked_before_uploader_creation(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    clip = project / "final.mp4"
    clip.write_bytes(b"fake mp4")

    def fail_if_created(*_args, **_kwargs):
        raise AssertionError("uploader must not be created for an unconfirmed public upload")

    monkeypatch.setattr(ug, "UPLOADERS", {"youtube": fail_if_created})
    lines = list(
        publish_panel.stream_upload(
            str(project), "youtube", str(clip), "Title", "Caption", [], False, "warn",
            privacy_status="public", public_confirm=False,
        )
    )
    output = "\n".join(lines)
    assert "تم إيقاف الرفع العام" in output
    assert "uploader must not" not in output


def test_public_upload_dry_run_does_not_require_confirmation(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    clip = project / "final.mp4"
    clip.write_bytes(b"fake mp4")
    seen = {}

    class FakeUploader:
        def __init__(self, *_args, **kwargs):
            seen.update(kwargs)

        def upload(self, *_args, **_kwargs):
            return {"status": "dry-run"}

    monkeypatch.setattr(ug, "UPLOADERS", {"youtube": FakeUploader})
    output = "\n".join(
        publish_panel.stream_upload(
            str(project), "youtube", str(clip), "Title", "Caption", [], True, "warn",
            privacy_status="public", public_confirm=False,
        )
    )
    assert "Upload finished." in output
    assert seen["dry_run"] is True


def test_processing_validator_rejects_invalid_input_and_public_without_confirmation():
    from webui.app import validate_processing_config

    report = validate_processing_config(
        **_validation_args(
            url="https://example.com/not-youtube",
            auto_upload=True,
            auto_upload_dry_run=False,
            auto_upload_privacy="public",
        )
    )
    assert any("رابط YouTube" in item for item in report["errors"])
    assert any("النشر العام الحقيقي" in item for item in report["errors"])


def test_processing_validator_accepts_safe_dry_run():
    from webui.app import validate_processing_config

    report = validate_processing_config(**_validation_args(auto_upload=True, auto_upload_privacy="public"))
    assert report["errors"] == []
    assert any("Dry Run" in item for item in report["warnings"])


def test_youtube_preflight_checks_channel_and_schedule_without_upload(tmp_path, monkeypatch):
    import scripts.content_guard as content_guard
    import scripts.upload_gate as upload_gate
    import webui.youtube_credentials as credentials
    from webui import app

    oauth = tmp_path / "client_secrets.json"
    oauth.write_text("{}", encoding="utf-8")
    seen = {}

    monkeypatch.setattr(credentials, "store_client_secrets", lambda path: {
        "path": str(tmp_path / "stored-client.json"), "changed": False,
    })
    monkeypatch.setattr(content_guard, "channel_status", lambda *_args, **_kwargs: {
        "locked": False, "incidents": [],
    })

    class FakeUploader:
        def __init__(self, project_folder, **kwargs):
            seen["project"] = project_folder
            seen["kwargs"] = kwargs

        def verify_channel(self):
            seen["verified"] = True
            return {"id": "UC-test", "title": "Test channel"}

    monkeypatch.setattr(upload_gate, "YouTubeUploader", FakeUploader)
    result = app.prepare_youtube_preflight(
        str(tmp_path / "project"), str(oauth), dry_run=False,
        privacy_status="private", publish_at="2030-01-01T10:00:00+00:00",
        schedule_interval_minutes=90, expected_clips=6,
    )
    assert result["ready"] is True
    assert result["scheduled_count"] == 6
    assert result["schedule_last"].startswith("2030-01-01T17:30:00")
    assert seen["verified"] is True
    assert seen["kwargs"]["dry_run"] is True


def test_schedule_requires_timezone_future_and_valid_interval():
    from webui.app import validate_processing_config

    good = validate_processing_config(**_validation_args(
        auto_upload=True,
        auto_upload_publish_at="2030-01-01T10:00:00+00:00",
        auto_upload_interval_minutes=90,
    ))
    assert good["errors"] == []

    naive = validate_processing_config(**_validation_args(
        auto_upload=True,
        auto_upload_publish_at="2030-01-01T10:00:00",
    ))
    assert any("منطقة زمنية" in item for item in naive["errors"])

    invalid_interval = validate_processing_config(**_validation_args(
        auto_upload=True,
        auto_upload_interval_minutes=0,
    ))
    assert any("فاصل الجدولة" in item for item in invalid_interval["errors"])


def test_post_render_ocr_uses_selected_sampling_settings(tmp_path, monkeypatch):
    from pathlib import Path

    import scripts.ocr_safety as ocr_safety
    import scripts.upload_content_review as upload_content_review
    import scripts.video_sanitizer as video_sanitizer

    config = app.demo.get_config_file()
    component_ids = {
        component.get("props", {}).get("label"): component["id"]
        for component in config["components"]
    }
    process_button = next(
        component
        for component in config["components"]
        if component.get("type") == "button"
        and component.get("props", {}).get("value")
        == "⚙️ معالجة الفيديو وإعادة الفحص آلياً"
    )
    process_event = next(
        dependency
        for dependency in config["dependencies"]
        if any(
            target[0] == process_button["id"] and target[1] == "click"
            for target in dependency.get("targets", [])
        )
    )
    assert component_ids["عدد إطارات OCR للفحص البصري"] in process_event["inputs"]
    assert component_ids["لغات OCR — يتطلب Tesseract واللغات المختارة"] in process_event["inputs"]

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    output_root = tmp_path / "VIRALS"
    monkeypatch.setattr(app, "VIRALS_DIR", str(output_root))
    monkeypatch.setattr(app, "WORKING_DIR", str(tmp_path))
    seen = {}

    def fake_sanitize(_source, _edits, output_path, subtitle_path=None):
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"rendered")
        return {
            "video": str(output),
            "audit": str(output.parent / "audit.json"),
            "report": {},
        }

    def fake_review(*_args, **_kwargs):
        return {"status": "clear", "review_complete": True}

    def fake_ocr(_video, *, frames, language):
        seen["frames"] = frames
        seen["language"] = language
        return {"status": "scanned", "action": "allow"}

    monkeypatch.setattr(video_sanitizer, "sanitize_video", fake_sanitize)
    monkeypatch.setattr(upload_content_review, "review_video", fake_review)
    monkeypatch.setattr(ocr_safety, "analyze_video", fake_ocr)
    process_callback = next(
        block_fn.fn
        for block_fn in app.demo.fns.values()
        if getattr(block_fn.fn, "__name__", "") == "_ultimate_process"
    )
    result = process_callback(
        str(source),
        '[{"start": 1, "end": 2, "action": "mute"}]',
        None,
        "reviewed.mp4",
        "title",
        "context",
        "auto",
        "cpu",
        14,
        "fra+eng",
    )

    assert seen == {"frames": 14, "language": "fra+eng"}
    assert "HUMAN_APPROVAL_REQUIRED" in result[0]
