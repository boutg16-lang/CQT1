# -*- coding: utf-8 -*-
"""Tests for webui/header.py — header HTML, env status card, quick-start.

Covers: the module-level `description` HTML, LOGO_DATA_URI / VERSION,
env_status_html (all three status branches, caching, force, error path),
home_quickstart, home_panel, and the parts of recent_updates_html not
already covered by tests/test_v734_ui_shell.py and
tests/test_v736_voice_face_link_ui.py (item count, heading format).

Deliberately excluded: recent_updates_html version/item basics (already
covered by those two files) and the real scripts.preflight checks (mocked
here — this file tests header.py's rendering, not preflight itself).
"""

import base64
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app_version import VERSION as APP_VERSION  # noqa: E402
from webui import header  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_env_status_cache():
    header.env_status_html._cached = None
    yield
    header.env_status_html._cached = None


@pytest.fixture
def _identity_i18n(monkeypatch):
    """Render English keys verbatim instead of the Arabic translations."""
    monkeypatch.setattr(header, "i18n", lambda text, *a, **k: text)


def _fake_collect(checks, critical, warn):
    def _collect():
        return (checks, critical, warn)

    return _collect


# ---------------------------------------------------------------------------
# module-level constants and the description HTML
# ---------------------------------------------------------------------------


def test_version_matches_app_version():
    assert header.VERSION == APP_VERSION
    assert header.VERSION == "8.10.1-ultimate"


def test_logo_data_uri_is_a_valid_png():
    assert header.LOGO_DATA_URI.startswith("data:image/png;base64,")
    raw = base64.b64decode(header.LOGO_DATA_URI.split(",", 1)[1])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(raw) > 1000


def test_description_contains_branding():
    html = header.description
    assert 'id="vc-header"' in html
    assert "OUSSAMA Cutter" in html
    assert "v8.10.1-ultimate" in html
    assert header.LOGO_DATA_URI in html
    assert "direction: rtl" in html
    # The tagline is translated at import time (DEFAULT_LANGUAGE ar_SA).
    assert "بديل Opus Clip مفتوح المصدر" in html
    assert "طبقة حماية من ضربات يوتيوب" in html
    # Arabic brand description from app_brand.
    assert "استوديو عربي احترافي" in html


def test_i18n_uses_default_language():
    from i18n.i18n import DEFAULT_LANGUAGE, I18nAuto

    assert isinstance(header.i18n, I18nAuto)
    assert header.i18n("Start in One Minute")
    assert DEFAULT_LANGUAGE == "ar_SA"


# ---------------------------------------------------------------------------
# env_status_html
# ---------------------------------------------------------------------------


def test_env_status_all_ok_is_green(monkeypatch, _identity_i18n):
    monkeypatch.setattr(
        "scripts.preflight.collect_checks",
        _fake_collect([{"status": "ok"}] * 3, [], []),
    )
    html = header.env_status_html()
    assert "#4ade80" in html
    assert "✅" in html
    assert "Everything in place" in html
    assert "3 checks passed · 0 warnings · 0 critical" in html


def test_env_status_all_ok_uses_real_arabic_labels(monkeypatch):
    """Without the identity patch the real I18nAuto renders Arabic."""
    monkeypatch.setattr(
        "scripts.preflight.collect_checks",
        _fake_collect([{"status": "ok"}] * 3, [], []),
    )
    html = header.env_status_html()
    assert "كل شيء في مكانه — النظام جاهز" in html
    assert "3 فحصاً ناجحاً · 0 تحذيراً · 0 حرجاً" in html


def test_env_status_warnings_are_amber(monkeypatch, _identity_i18n):
    monkeypatch.setattr(
        "scripts.preflight.collect_checks",
        _fake_collect([{"status": "ok"}, {"status": "warn"}], [], ["ffmpeg"]),
    )
    html = header.env_status_html()
    assert "#fbbf24" in html
    assert "⚠️" in html
    assert "System ready with optional warnings" in html
    assert "1 checks passed · 1 warnings · 0 critical" in html


def test_env_status_critical_is_red(monkeypatch, _identity_i18n):
    monkeypatch.setattr(
        "scripts.preflight.collect_checks",
        _fake_collect([{"status": "ok"}, {"status": "fail"}], ["python"], ["ffmpeg"]),
    )
    html = header.env_status_html()
    assert "#f87171" in html
    assert "❌" in html
    assert "Critical issues" in html
    assert "1 checks passed · 1 warnings · 1 critical" in html


def test_env_status_ok_count_only_counts_ok_status(monkeypatch, _identity_i18n):
    # Quirk: n_ok counts checks whose status is exactly "ok"; a "warn"
    # check is not counted anywhere in the detail line except via the
    # warn list length, and a "fail" check only via the critical list.
    monkeypatch.setattr(
        "scripts.preflight.collect_checks",
        _fake_collect(
            [{"status": "ok"}, {"status": "warn"}, {"status": "fail"}],
            ["c1"],
            ["w1"],
        ),
    )
    html = header.env_status_html()
    assert "1 checks passed · 1 warnings · 1 critical" in html


def test_env_status_result_is_cached(monkeypatch):
    calls = []

    def _collect():
        calls.append(1)
        return ([{"status": "ok"}], [], [])

    monkeypatch.setattr("scripts.preflight.collect_checks", _collect)
    first = header.env_status_html()
    second = header.env_status_html()
    assert first == second
    assert len(calls) == 1


def test_env_status_force_bypasses_cache(monkeypatch, _identity_i18n):
    calls = []

    def _collect():
        calls.append(1)
        return ([{"status": "ok"}] * len(calls), [], [])

    monkeypatch.setattr("scripts.preflight.collect_checks", _collect)
    header.env_status_html()
    html = header.env_status_html(force=True)
    assert len(calls) == 2
    assert "2 checks passed" in html


def test_env_status_exception_returns_empty_and_caches_it(monkeypatch):
    calls = []

    def _boom():
        calls.append(1)
        raise RuntimeError("preflight exploded")

    monkeypatch.setattr("scripts.preflight.collect_checks", _boom)
    assert header.env_status_html() == ""
    # Quirk: the empty result is cached too, so a later call does not
    # retry unless force=True.
    assert header.env_status_html() == ""
    assert len(calls) == 1
    assert header.env_status_html(force=True) == ""
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# home_quickstart / home_panel
# ---------------------------------------------------------------------------


def test_home_quickstart_has_three_steps(_identity_i18n):
    html = header.home_quickstart()
    assert html.count("<li") == 3
    assert "1️⃣" in html and "2️⃣" in html and "3️⃣" in html
    assert "Start in One Minute" in html
    assert "Paste a YouTube video link" in html
    assert "Start Processing" in html
    assert "direction:rtl" in html


def test_home_panel_is_quickstart_plus_env_status(monkeypatch, _identity_i18n):
    monkeypatch.setattr(
        "scripts.preflight.collect_checks",
        _fake_collect([{"status": "ok"}], [], []),
    )
    panel = header.home_panel()
    assert panel.startswith(header.home_quickstart())
    assert panel == header.home_quickstart() + header.env_status_html()
    assert "Everything in place" in panel


def test_home_panel_with_failed_env_status_still_renders_quickstart(
    monkeypatch, _identity_i18n
):
    monkeypatch.setattr(
        "scripts.preflight.collect_checks",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    panel = header.home_panel()
    assert panel == header.home_quickstart()
    assert "Start in One Minute" in panel


# ---------------------------------------------------------------------------
# recent_updates_html — beyond the v7.34 / v7.36 coverage
# ---------------------------------------------------------------------------


def test_recent_updates_has_exactly_five_items():
    html = header.recent_updates_html(version="8.10.1")
    assert html.count("<li") == 5


def test_recent_updates_heading_names_version():
    html = header.recent_updates_html(version="8.10.1")
    assert "ما الجديد في v8.10.1" in html
    assert "(آخر 5 إصدارات)" in html


def test_recent_updates_items_cover_shipped_features():
    html = header.recent_updates_html(version="8.10.1")
    assert "ربط الصوت بالوجه" in html
    assert "رفع الدفعة" in html
    assert "دروس قناتي" in html
    assert "بوابة جودة" in html
    assert "استئناف سليم بعد الانقطاع" in html
