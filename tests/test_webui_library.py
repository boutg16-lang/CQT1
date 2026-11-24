# -*- coding: utf-8 -*-
"""Tests for webui/library.py — project listing, filtering and gallery HTML.

Covers: cache helpers (invalidate_caches, set_url_mode, _dir_signature),
get_existing_projects (incl. TTL cache and the exception fallback),
refresh_projects / filter_projects, _find_segment_video candidate order,
and generate_project_gallery (banners, score colours, URL modes, caching).

Deliberately excluded: the real project_store / checkpoint on-disk
behaviour (those modules have their own tests) — here they are mocked so
these tests exercise only library.py's own logic. No Gradio server is
launched and no real media is read; video "files" are empty placeholders.
"""

import datetime
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webui import library  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Point VIRALS_DIR at a scratch dir, reset caches/URL mode, fix i18n."""
    virals = tmp_path / "VIRALS"
    virals.mkdir()
    monkeypatch.setattr(library, "VIRALS_DIR", str(virals))
    monkeypatch.setattr(library, "URL_MODE", "fastapi")
    # Identity i18n so assertions do not depend on translation files.
    monkeypatch.setattr(library, "i18n", lambda text, *a, **k: text)
    library.invalidate_caches()
    yield
    library.invalidate_caches()
    library.URL_MODE = "fastapi"


@pytest.fixture
def virals_dir(tmp_path):
    return tmp_path / "VIRALS"


def _make_project(virals_dir, name):
    path = virals_dir / name
    path.mkdir(parents=True)
    return path


def _records(*names):
    return [{"path": os.path.join("/fake", name)} for name in names]


# ---------------------------------------------------------------------------
# cache helpers
# ---------------------------------------------------------------------------


def test_invalidate_caches_clears_both_caches(monkeypatch):
    library._PROJECTS_CACHE["stamp"] = 123.0
    library._PROJECTS_CACHE["data"] = ["a"]
    library._GALLERY_CACHE["x"] = {"html": "y"}
    library.invalidate_caches()
    assert library._PROJECTS_CACHE == {"stamp": 0.0, "data": []}
    assert library._GALLERY_CACHE == {}


def test_set_url_mode_switches_and_restores():
    library.set_url_mode("gradio")
    assert library.URL_MODE == "gradio"
    library.set_url_mode("fastapi")
    assert library.URL_MODE == "fastapi"


def test_dir_signature_returns_mtime_for_existing_path(tmp_path):
    path = tmp_path / "f.txt"
    path.write_text("x")
    assert library._dir_signature(str(path)) == os.path.getmtime(str(path))


def test_dir_signature_returns_zero_for_missing_path(tmp_path):
    assert library._dir_signature(str(tmp_path / "nope")) == 0.0


# ---------------------------------------------------------------------------
# get_existing_projects
# ---------------------------------------------------------------------------


def test_get_existing_projects_empty_when_virals_dir_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(library, "VIRALS_DIR", str(tmp_path / "no-such-dir"))
    assert library.get_existing_projects() == []


def test_get_existing_projects_returns_basenames_and_skips_empty_paths(monkeypatch):
    monkeypatch.setattr(
        library,
        "_list_project_records",
        lambda root: _records("alpha", "beta") + [{"path": ""}, {}],
    )
    assert library.get_existing_projects() == ["alpha", "beta"]


def test_get_existing_projects_caches_within_ttl(monkeypatch):
    calls = []
    monkeypatch.setattr(
        library,
        "_list_project_records",
        lambda root: calls.append(root) or _records("one"),
    )
    assert library.get_existing_projects() == ["one"]
    assert library.get_existing_projects() == ["one"]
    assert len(calls) == 1


def test_get_existing_projects_force_refresh_bypasses_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(
        library,
        "_list_project_records",
        lambda root: calls.append(root) or _records("one"),
    )
    library.get_existing_projects()
    assert library.get_existing_projects(force_refresh=True) == ["one"]
    assert len(calls) == 2


def test_get_existing_projects_refetches_after_ttl_expiry(monkeypatch):
    calls = []
    monkeypatch.setattr(
        library,
        "_list_project_records",
        lambda root: calls.append(root) or _records("one"),
    )
    library.get_existing_projects()
    # Fake an expired stamp instead of sleeping through the 2s TTL.
    library._PROJECTS_CACHE["stamp"] = time.time() - library._GALLERY_CACHE_TTL - 1
    assert library.get_existing_projects() == ["one"]
    assert len(calls) == 2


def test_get_existing_projects_returns_copy_not_live_cache(monkeypatch):
    monkeypatch.setattr(library, "_list_project_records", lambda root: _records("one"))
    first = library.get_existing_projects()
    first.append("mutated")
    assert library.get_existing_projects() == ["one"]


def test_get_existing_projects_exception_returns_stale_cache(monkeypatch):
    library._PROJECTS_CACHE["stamp"] = time.time()
    library._PROJECTS_CACHE["data"] = ["stale"]

    def _boom(root):
        raise RuntimeError("store down")

    monkeypatch.setattr(library, "_list_project_records", _boom)
    # Quirk: on error the module silently serves the last good list.
    assert library.get_existing_projects(force_refresh=True) == ["stale"]


def test_get_existing_projects_exception_with_empty_cache_returns_empty(monkeypatch):
    monkeypatch.setattr(
        library,
        "_list_project_records",
        lambda root: (_ for _ in ()).throw(RuntimeError("store down")),
    )
    assert library.get_existing_projects() == []


# ---------------------------------------------------------------------------
# refresh_projects / filter_projects
# ---------------------------------------------------------------------------


def test_refresh_projects_returns_gr_update_with_first_as_value(monkeypatch):
    monkeypatch.setattr(
        library, "_list_project_records", lambda root: _records("b", "a")
    )
    result = library.refresh_projects()
    assert result["choices"] == ["b", "a"]
    assert result["value"] == "b"


def test_refresh_projects_empty_value_is_none(monkeypatch):
    monkeypatch.setattr(library, "_list_project_records", lambda root: [])
    result = library.refresh_projects()
    assert result == {"choices": [], "value": None, "__type__": "update"}


def test_refresh_projects_invalidates_stale_cache(monkeypatch):
    library._PROJECTS_CACHE["stamp"] = time.time()
    library._PROJECTS_CACHE["data"] = ["old"]
    monkeypatch.setattr(library, "_list_project_records", lambda root: _records("new"))
    assert library.refresh_projects()["choices"] == ["new"]


def test_filter_projects_no_query_returns_all(monkeypatch):
    monkeypatch.setattr(
        library, "_list_project_records", lambda root: _records("Alpha", "beta")
    )
    result = library.filter_projects()
    assert result["choices"] == ["Alpha", "beta"]
    assert result["value"] == "Alpha"


def test_filter_projects_query_is_case_insensitive_and_stripped(monkeypatch):
    monkeypatch.setattr(
        library, "_list_project_records", lambda root: _records("Alpha", "beta")
    )
    assert library.filter_projects(query="  ALP ")["choices"] == ["Alpha"]
    assert library.filter_projects(query="zzz") == {
        "choices": [],
        "value": None,
        "__type__": "update",
    }


def test_filter_projects_date_range_uses_ctime(monkeypatch, virals_dir):
    _make_project(virals_dir, "proj")
    monkeypatch.setattr(library, "_list_project_records", lambda root: _records("proj"))
    now = datetime.datetime.now()
    yesterday = (now - datetime.timedelta(days=1)).isoformat()
    tomorrow = (now + datetime.timedelta(days=1)).isoformat()
    assert library.filter_projects(date_from=yesterday)["choices"] == ["proj"]
    assert library.filter_projects(date_from=tomorrow)["choices"] == []
    assert library.filter_projects(date_to=yesterday)["choices"] == []
    assert library.filter_projects(date_to=tomorrow)["choices"] == ["proj"]


def test_filter_projects_invalid_date_is_silently_ignored(monkeypatch, virals_dir):
    _make_project(virals_dir, "proj")
    monkeypatch.setattr(library, "_list_project_records", lambda root: _records("proj"))
    # Quirk: an unparseable date string is swallowed and the project kept.
    assert library.filter_projects(date_from="not-a-date")["choices"] == ["proj"]
    assert library.filter_projects(date_to="also-bad")["choices"] == ["proj"]


# ---------------------------------------------------------------------------
# _find_segment_video
# ---------------------------------------------------------------------------


def test_find_segment_video_prefers_existing_filepath(tmp_path):
    video = tmp_path / "somewhere.mp4"
    video.write_bytes(b"")
    seg = {"filepath": str(video)}
    assert library._find_segment_video(str(tmp_path), seg, 0) == str(video)


def test_find_segment_video_ignores_missing_filepath_and_falls_back(tmp_path):
    seg = {"filepath": str(tmp_path / "gone.mp4")}
    burned = tmp_path / "burned_sub"
    burned.mkdir()
    target = burned / "output000.mp4"
    target.write_bytes(b"")
    assert library._find_segment_video(str(tmp_path), seg, 0) == str(target)


def test_find_segment_video_filename_prefers_burned_sub(tmp_path):
    (tmp_path / "burned_sub").mkdir()
    root_copy = tmp_path / "clip.mp4"
    root_copy.write_bytes(b"")
    burned_copy = tmp_path / "burned_sub" / "clip.mp4"
    burned_copy.write_bytes(b"")
    seg = {"filename": "clip.mp4"}
    assert library._find_segment_video(str(tmp_path), seg, 0) == str(burned_copy)


def test_find_segment_video_filename_falls_back_to_project_root(tmp_path):
    root_copy = tmp_path / "clip.mp4"
    root_copy.write_bytes(b"")
    seg = {"filename": "clip.mp4"}
    assert library._find_segment_video(str(tmp_path), seg, 0) == str(root_copy)


def test_find_segment_video_numbered_candidates_in_order(tmp_path):
    cuts = tmp_path / "cuts"
    cuts.mkdir()
    first = tmp_path / "final-output002_processed.mp4"
    first.write_bytes(b"")
    second = cuts / "output002_original_scale.mp4"
    second.write_bytes(b"")
    seg = {}
    # final-output{idx}_processed.mp4 precedes the cuts candidates.
    assert library._find_segment_video(str(tmp_path), seg, 2) == str(first)


def test_find_segment_video_scans_subdirs_for_index_match(tmp_path):
    cuts = tmp_path / "cuts"
    cuts.mkdir()
    loose = cuts / "weird_005_name.mp4"
    loose.write_bytes(b"")
    assert library._find_segment_video(str(tmp_path), {}, 5) == str(loose)


def test_find_segment_video_returns_none_when_nothing_matches(tmp_path):
    (tmp_path / "burned_sub").mkdir()
    (tmp_path / "cuts").mkdir()
    assert library._find_segment_video(str(tmp_path), {}, 0) is None
    assert library._find_segment_video(str(tmp_path), {"filename": 123}, 0) is None


# ---------------------------------------------------------------------------
# generate_project_gallery — guards and fallbacks
# ---------------------------------------------------------------------------


def test_gallery_empty_project_name():
    html = library.generate_project_gallery("")
    assert "No project selected." in html


def test_gallery_none_project_name():
    assert "No project selected." in library.generate_project_gallery(None)


def test_gallery_path_traversal_name_rejected(virals_dir):
    html = library.generate_project_gallery("../evil")
    assert "Project path not found." in html
    assert "evil" not in html


def test_gallery_missing_folder_reports_path(virals_dir):
    html = library.generate_project_gallery("ghost")
    assert "Project path not found:" in html
    assert "ghost" in html


def test_gallery_full_path_outside_virals_rejected(virals_dir, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    html = library.generate_project_gallery(str(outside), is_full_path=True)
    assert "Project path not found." in html


def test_gallery_full_path_inside_virals_works(monkeypatch, virals_dir):
    project = _make_project(virals_dir, "proj")
    monkeypatch.setattr(library, "_project_summary", lambda p: {"status": "legacy"})
    monkeypatch.setattr(library, "_load_checkpoint", lambda p: {})
    html = library.generate_project_gallery(str(project), is_full_path=True)
    # A project with no segments still renders the grid: the state banner
    # alone makes html_cards non-empty, so the "No viral segments found."
    # fallback is only reached when the banner is empty too.
    assert "legacy" in html
    assert "No viral segments found." not in html


def test_gallery_no_segments_no_files(monkeypatch, virals_dir):
    _make_project(virals_dir, "proj")
    monkeypatch.setattr(
        library,
        "_project_summary",
        lambda p: (_ for _ in ()).throw(RuntimeError("no manifest")),
    )
    html = library.generate_project_gallery("proj")
    assert "No viral segments found." in html


def test_gallery_malformed_segments_json_reports_error(monkeypatch, virals_dir):
    project = _make_project(virals_dir, "proj")
    (project / "viral_segments.txt").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(library, "_project_summary", lambda p: {"status": "legacy"})
    html = library.generate_project_gallery("proj")
    assert html.startswith("Error loading gallery:")


# ---------------------------------------------------------------------------
# generate_project_gallery — segments, scores, escaping
# ---------------------------------------------------------------------------


def _gallery_project(monkeypatch, virals_dir, segments, files=()):
    project = _make_project(virals_dir, "proj")
    if segments is not None:
        (project / "viral_segments.txt").write_text(
            json.dumps({"segments": segments}), encoding="utf-8"
        )
    for name in files:
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    monkeypatch.setattr(library, "_project_summary", lambda p: {"status": "legacy"})
    monkeypatch.setattr(library, "_load_checkpoint", lambda p: {})
    return project


def test_gallery_renders_segments_from_json(monkeypatch, virals_dir):
    video = virals_dir / "proj" / "cuts" / "output000.mp4"
    _gallery_project(
        monkeypatch,
        virals_dir,
        [{"title": "Clip one", "score": 95, "filepath": str(video)}],
        files=["cuts/output000.mp4"],
    )
    html = library.generate_project_gallery("proj")
    assert "Clip one" in html
    assert "95" in html
    assert "/virals/proj/cuts/output000.mp4?t=" in html
    assert "download" in html.lower()
    # Quirk: the export href is interpolated raw, so the "&" separators
    # are NOT escaped to "&amp;" inside the attribute.
    assert "/export_xml_api?project=proj&segment=0&format=premiere" in html


def test_gallery_score_colours(monkeypatch, virals_dir):
    _gallery_project(
        monkeypatch,
        virals_dir,
        [
            {"title": "a", "score": 95},
            {"title": "b", "score": 80},
            {"title": "c", "score": 50},
            {"title": "d", "score": "92"},
            {"title": "e", "score": "N/A"},
            {"title": "f", "score": 85.5},
        ],
    )
    html = library.generate_project_gallery("proj")
    assert "#22c55e" in html  # >=85 and the N/A / float fallthrough
    assert "#eab308" in html  # 70-84
    assert "#ef4444" in html  # <70
    # Quirk: only int or digit-string scores recolour; "N/A" and floats
    # (e.g. 85.5) keep the default green even when below a threshold.
    assert html.count("#22c55e") >= 3


def test_gallery_title_defaults_to_segment_number(monkeypatch, virals_dir):
    _gallery_project(monkeypatch, virals_dir, [{"score": 10}])
    html = library.generate_project_gallery("proj")
    assert "Segment 1" in html


def test_gallery_title_is_html_escaped(monkeypatch, virals_dir):
    _gallery_project(monkeypatch, virals_dir, [{"title": "<script>alert(1)</script>"}])
    html = library.generate_project_gallery("proj")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_gallery_missing_video_shows_not_found(monkeypatch, virals_dir):
    _gallery_project(monkeypatch, virals_dir, [{"title": "lost", "score": 1}])
    html = library.generate_project_gallery("proj")
    assert "Not Found" in html


def test_gallery_discovers_mp4_files_without_metadata(monkeypatch, virals_dir):
    _gallery_project(
        monkeypatch,
        virals_dir,
        None,
        files=["burned_sub/output000.mp4", "cuts/input_raw.mp4", "cuts/a.mp4"],
    )
    html = library.generate_project_gallery("proj")
    assert "output000.mp4" in html
    assert "a.mp4" in html
    assert "input_raw.mp4" not in html  # "input" files are excluded
    assert "N/A" in html
    # Quirk: discovered segments carry description "No metadata found.",
    # but the card template never renders the description field at all.
    assert "No metadata found." not in html


def test_gallery_external_video_placeholder(monkeypatch, virals_dir, tmp_path):
    outside = tmp_path / "ext.mp4"
    outside.write_bytes(b"")
    _gallery_project(
        monkeypatch,
        virals_dir,
        [{"title": "ext", "score": 90, "filepath": str(outside)}],
    )
    html = library.generate_project_gallery("proj")
    assert "External Video" in html
    assert str(outside) not in html  # absolute path is never leaked


# ---------------------------------------------------------------------------
# generate_project_gallery — banners
# ---------------------------------------------------------------------------


def test_gallery_state_banner(monkeypatch, virals_dir):
    _gallery_project(monkeypatch, virals_dir, [{"title": "x", "score": 90}])
    monkeypatch.setattr(
        library,
        "_project_summary",
        lambda p: {
            "status": "done",
            "updated_at": "2026-11-24T00:00:00",
            "last_publish": {"video_id": "abc123"},
        },
    )
    monkeypatch.setattr(
        library,
        "_load_checkpoint",
        lambda p: {
            "active_stage": "burn",
            "last_error": {"message": "boom"},
        },
    )
    html = library.generate_project_gallery("proj")
    assert "done" in html
    assert "2026-11-24T00:00:00" in html
    assert "استئناف من: burn" in html
    assert "آخر خطأ: boom" in html
    assert "آخر نشر: abc123" in html


def test_gallery_state_banner_error_truncated_to_180_chars(monkeypatch, virals_dir):
    _gallery_project(monkeypatch, virals_dir, [{"title": "x", "score": 90}])
    long_msg = "e" * 250
    monkeypatch.setattr(library, "_project_summary", lambda p: {"status": "legacy"})
    monkeypatch.setattr(
        library,
        "_load_checkpoint",
        lambda p: {"active_stage": "", "last_error": {"message": long_msg}},
    )
    html = library.generate_project_gallery("proj")
    assert "e" * 180 in html
    assert "e" * 200 not in html


def test_gallery_state_banner_missing_when_summary_fails(monkeypatch, virals_dir):
    _gallery_project(monkeypatch, virals_dir, [{"title": "x", "score": 90}])
    monkeypatch.setattr(
        library,
        "_project_summary",
        lambda p: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    html = library.generate_project_gallery("proj")
    # The gallery still renders; only the status banner is dropped.
    assert "vc-project-status" not in html
    assert "x" in html


def test_gallery_report_banner_ready_and_not_ready(monkeypatch, virals_dir):
    project = _gallery_project(monkeypatch, virals_dir, [{"title": "x", "score": 90}])
    (project / "project_report.json").write_text(
        json.dumps(
            {
                "readiness": {"ready_for_publish": True},
                "safety": {"counts": {"blocked": 2, "manual_review": 1}},
            }
        ),
        encoding="utf-8",
    )
    (project / "project_report.html").write_text("<html/>", encoding="utf-8")
    html = library.generate_project_gallery("proj")
    assert "جاهز للنشر" in html
    assert "#22c55e" in html
    assert "محجوب: 2" in html
    assert "مراجعة يدوية: 1" in html
    assert "/virals/proj/project_report.html" in html

    library.invalidate_caches()
    (project / "project_report.json").write_text(
        json.dumps({"readiness": {"ready_for_publish": False}, "safety": {}}),
        encoding="utf-8",
    )
    (project / "project_report.html").unlink()
    # Bump the report mtime so the gallery cache key changes.
    os.utime(project / "project_report.json", (time.time() + 5, time.time() + 5))
    os.utime(project, (time.time() + 5, time.time() + 5))
    html = library.generate_project_gallery("proj")
    assert "يتطلب مراجعة" in html
    assert "#f59e0b" in html
    assert "عرض التقرير الكامل" not in html


def test_gallery_report_banner_non_dict_safety(monkeypatch, virals_dir):
    project = _gallery_project(monkeypatch, virals_dir, [{"title": "x", "score": 90}])
    (project / "project_report.json").write_text(
        json.dumps({"readiness": {}, "safety": "not-a-dict"}),
        encoding="utf-8",
    )
    html = library.generate_project_gallery("proj")
    assert "محجوب: 0" in html
    assert "مراجعة يدوية: 0" in html


def test_gallery_report_banner_malformed_json_is_dropped(monkeypatch, virals_dir):
    project = _gallery_project(monkeypatch, virals_dir, [{"title": "x", "score": 90}])
    (project / "project_report.json").write_text("{bad", encoding="utf-8")
    html = library.generate_project_gallery("proj")
    assert "تقرير الجاهزية" not in html
    assert "x" in html


def test_gallery_review_banner(monkeypatch, virals_dir):
    project = _gallery_project(monkeypatch, virals_dir, [{"title": "x", "score": 90}])
    (project / "review_queue.json").write_text(
        json.dumps({"total": 3, "high": 1}), encoding="utf-8"
    )
    (project / "review_queue.html").write_text("<html/>", encoding="utf-8")
    html = library.generate_project_gallery("proj")
    assert "مراجعة قبل النشر: 3" in html
    assert "منها 1 عالية الخطورة" in html
    assert "/virals/proj/review_queue.html" in html


def test_gallery_review_banner_hidden_when_total_zero(monkeypatch, virals_dir):
    project = _gallery_project(monkeypatch, virals_dir, [{"title": "x", "score": 90}])
    (project / "review_queue.json").write_text(
        json.dumps({"total": 0, "high": 0}), encoding="utf-8"
    )
    html = library.generate_project_gallery("proj")
    assert "مراجعة قبل النشر" not in html


def test_gallery_review_banner_malformed_json_is_dropped(monkeypatch, virals_dir):
    project = _gallery_project(monkeypatch, virals_dir, [{"title": "x", "score": 90}])
    (project / "review_queue.json").write_text("{bad", encoding="utf-8")
    html = library.generate_project_gallery("proj")
    assert "مراجعة قبل النشر" not in html


# ---------------------------------------------------------------------------
# generate_project_gallery — URL modes and caching
# ---------------------------------------------------------------------------


def test_gallery_gradio_mode_emits_empty_src_when_outside_allowed_dirs(
    monkeypatch, virals_dir
):
    # webui.app is not imported here; _allowed_dirs is unreachable, so no
    # /file/ URL is emitted for videos (the security fallback).
    library.set_url_mode("gradio")
    _gallery_project(
        monkeypatch,
        virals_dir,
        [{"title": "x", "score": 90}],
        files=["cuts/output000.mp4"],
    )
    html = library.generate_project_gallery("proj")
    assert '<source src=""' in html
    assert "/virals/" not in html


def test_gallery_cache_returns_same_html_without_rereading(monkeypatch, virals_dir):
    project = _gallery_project(
        monkeypatch, virals_dir, [{"title": "first", "score": 90}]
    )
    first = library.generate_project_gallery("proj")
    segments_path = project / "viral_segments.txt"
    # Capture the mtime BEFORE rewriting so it can be restored exactly.
    old_mtime = os.path.getmtime(segments_path)
    segments_path.write_text(
        json.dumps({"segments": [{"title": "second", "score": 90}]}),
        encoding="utf-8",
    )
    # Keep the mtime identical: the cache key is (folder mtime, file
    # mtime, URL mode) only, so a content change with a preserved mtime
    # is invisible to the cache. Quirk: no content hash is used.
    os.utime(segments_path, (old_mtime, old_mtime))
    assert library.generate_project_gallery("proj") == first
    assert "second" not in library.generate_project_gallery("proj")


def test_gallery_cache_regenerates_after_mtime_change(monkeypatch, virals_dir):
    project = _gallery_project(
        monkeypatch, virals_dir, [{"title": "first", "score": 90}]
    )
    library.generate_project_gallery("proj")
    (project / "viral_segments.txt").write_text(
        json.dumps({"segments": [{"title": "second", "score": 90}]}),
        encoding="utf-8",
    )
    future = time.time() + 10
    os.utime(project / "viral_segments.txt", (future, future))
    os.utime(project, (future, future))
    html = library.generate_project_gallery("proj")
    assert "second" in html
    assert "first" not in html


def test_gallery_cache_key_includes_url_mode(monkeypatch, virals_dir):
    _gallery_project(
        monkeypatch,
        virals_dir,
        [{"title": "x", "score": 90}],
        files=["cuts/output000.mp4"],
    )
    fastapi_html = library.generate_project_gallery("proj")
    library.set_url_mode("gradio")
    gradio_html = library.generate_project_gallery("proj")
    assert fastapi_html != gradio_html
    assert "/virals/" in fastapi_html
    assert "/virals/" not in gradio_html


def test_gallery_cache_never_expires_by_time(monkeypatch, virals_dir):
    _gallery_project(monkeypatch, virals_dir, [{"title": "x", "score": 90}])
    html = library.generate_project_gallery("proj")
    # Quirk: the stored "ts" is never consulted on read, so the gallery
    # cache has no TTL — only a signature change invalidates it.
    for entry in library._GALLERY_CACHE.values():
        entry["ts"] = time.time() - 10_000
    assert library.generate_project_gallery("proj") == html
