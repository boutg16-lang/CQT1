from scripts import clip_scoring, create_viral_segments


def _cache():
    return {
        "source_meta": {
            "source_video_fp": "video-v1",
            "config_fp": "settings-v1",
            "transcript_fp": "transcript-v1",
        }
    }


def test_current_cache_is_reusable():
    assert create_viral_segments.segment_cache_staleness_reason(
        _cache(), "video-v1", "settings-v1", "transcript-v1"
    ) is None


def test_cache_without_provenance_is_stale():
    assert create_viral_segments.segment_cache_staleness_reason(
        {"segments": []}, "video-v1", "settings-v1"
    ) == "missing_source_metadata"


def test_changed_video_or_settings_invalidates_cache():
    cache = _cache()
    assert create_viral_segments.segment_cache_staleness_reason(
        cache, "video-v2", "settings-v1", "transcript-v1"
    ) == "source_video_changed"
    assert create_viral_segments.segment_cache_staleness_reason(
        cache, "video-v1", "settings-v2", "transcript-v1"
    ) == "settings_changed"


def test_changed_or_unavailable_transcript_invalidates_cache():
    cache = _cache()
    assert create_viral_segments.segment_cache_staleness_reason(
        cache, "video-v1", "settings-v1", "transcript-v2"
    ) == "transcript_changed"
    assert create_viral_segments.segment_cache_staleness_reason(
        cache, "video-v1", "settings-v1"
    ) == "current_transcript_unavailable"


def test_current_transcript_requires_a_saved_fingerprint():
    cache = _cache()
    cache["source_meta"].pop("transcript_fp")
    assert create_viral_segments.segment_cache_staleness_reason(
        cache, "video-v1", "settings-v1", "transcript-v1"
    ) == "missing_transcript_fingerprint"


def test_selection_config_transcript_fingerprint_is_supported():
    cache = _cache()
    cache["source_meta"].pop("transcript_fp")
    cache["selection_config"] = {"transcript_fingerprint": "transcript-v1"}
    assert create_viral_segments.segment_cache_staleness_reason(
        cache, "video-v1", "settings-v1", "transcript-v1"
    ) is None


def test_scoring_version_bump_changes_prompt_fingerprint(monkeypatch):
    before = create_viral_segments.prompt_version_fingerprint()
    monkeypatch.setattr(clip_scoring, "SCORING_VERSION", "next-test-version")
    after = create_viral_segments.prompt_version_fingerprint()
    assert before != after

def test_cli_cache_check_uses_current_video_settings_and_transcript(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import main_improved

    monkeypatch.setattr(create_viral_segments, "source_video_fingerprint", lambda path: "video-v1")
    monkeypatch.setattr(create_viral_segments, "load_transcript", lambda folder: [{"text": "current"}])
    monkeypatch.setattr(create_viral_segments, "transcript_fingerprint", lambda transcript: "transcript-v1")
    monkeypatch.setattr(main_improved, "_segment_settings_fingerprint", lambda args: "settings-v1")
    args = SimpleNamespace()

    assert main_improved._segment_cache_staleness_reason(
        _cache(), "source.mp4", str(tmp_path), args
    ) is None

    monkeypatch.setattr(create_viral_segments, "source_video_fingerprint", lambda path: "video-v2")
    assert main_improved._segment_cache_staleness_reason(
        _cache(), "source.mp4", str(tmp_path), args
    ) == "source_video_changed"
