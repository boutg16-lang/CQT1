# -*- coding: utf-8 -*-
"""Direct tests for webui/style.py — the WebUI stylesheet (``CSS`` string).

Covered here: the structure and the content of ``webui.style.CSS`` — dark
theme, RTL layout, log/output sizing, tab pills, validation states, the
create-layout rules, the review table, scrollbars and the responsive
breakpoints — plus the cascade quirks where a selector is defined twice
and the later rule wins.

Deliberately excluded:
- ``tests/test_v733_3_style_selection.py`` covers *content-style learning*
  (``scripts/performance_weights`` title-style flags/bonuses and segment
  selection). It is about a different "style" and never touches
  ``webui.style`` — there is no overlap with this file.
- ``tests/test_v734_ui_shell.py`` already asserts five substrings of this
  stylesheet (``"tab-nav button"``, ``"vc-updates"``, ``"button.secondary"``,
  ``"direction: rtl"``, ``"#0b0b0b"``). Those five assertions are not
  repeated here; this file covers everything else in the stylesheet.
- Rendering the CSS in a real browser/Gradio server is out of scope: no
  Gradio server is launched and no network or real media is used. The
  checks are structural (balanced braces/comments, rule parsing) and
  content checks (selectors and their effective declarations).
"""

import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webui.style import CSS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# Minimal CSS reader (stdlib only): the stylesheet has no functions to call,
# so the tests parse its rules and check the effective (last-wins) values.
# ---------------------------------------------------------------------------


def _strip_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _top_blocks(css):
    """Yield ``(selector, body)`` for each top-level ``{...}`` block."""
    blocks = []
    depth = 0
    selector_start = 0
    body_start = 0
    selector = ""
    for index, char in enumerate(css):
        if char == "{":
            if depth == 0:
                selector = css[selector_start:index].strip()
                body_start = index + 1
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                blocks.append((selector, css[body_start:index]))
                selector_start = index + 1
    assert depth == 0, "unbalanced braces in webui.style.CSS"
    return blocks


def _all_rules(css):
    """Flattened ``(selector, body, media)`` list; ``media`` is None for
    top-level rules and the ``@media ...`` header for nested ones."""
    rules = []
    for selector, body in _top_blocks(_strip_comments(css)):
        if selector.startswith("@media"):
            for sub_selector, sub_body in _top_blocks(body):
                rules.append((sub_selector, sub_body, selector))
        else:
            rules.append((selector, body, None))
    return rules


RULES = _all_rules(CSS)


def _declarations(body):
    decls = {}
    for part in body.split(";"):
        part = part.strip()
        if not part:
            continue
        name, _, value = part.partition(":")
        decls[name.strip()] = value.strip()
    return decls


def _clean(value):
    """Drop a trailing ``!important`` for value comparison."""
    return value.replace("!important", "").strip()


def _rules_for(selector, media=None):
    return [
        _declarations(body)
        for sel, body, med in RULES
        if sel == selector and med == media
    ]


def _merged(selector):
    """Effective top-level declarations for ``selector`` (later rules win,
    as in the CSS cascade)."""
    merged = {}
    for decls in _rules_for(selector):
        merged.update(decls)
    return merged


def _media_rules(media_substring):
    return [
        (sel, _declarations(body))
        for sel, body, med in RULES
        if med is not None and media_substring in med
    ]


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_css_is_a_nonempty_string():
    assert isinstance(CSS, str)
    assert len(CSS) > 5000  # 9,671 chars at the time of writing
    assert CSS.strip(), "stylesheet must not be blank"
    assert CSS.startswith("\n")
    assert CSS.rstrip().endswith("}")


def test_braces_are_balanced():
    assert CSS.count("{") == CSS.count("}")
    assert CSS.count("{") > 50  # 67 rule blocks at the time of writing


def test_comments_are_balanced():
    assert CSS.count("/*") == CSS.count("*/")
    assert CSS.count("/*") > 0  # 16 comment blocks at the time of writing


def test_no_empty_rules_or_double_semicolons():
    assert "{}" not in CSS
    assert ";;" not in CSS
    for selector, body, _media in RULES:
        assert selector, "rule with an empty selector"
        assert body.strip(), "empty rule body for selector %r" % selector
        assert _declarations(body), "rule %r has no declarations" % selector


def test_rule_count_is_sane():
    # 66 rules (57 distinct selectors) at the time of writing: guards
    # against the stylesheet being accidentally truncated or duplicated.
    assert len(RULES) >= 60
    selectors = {sel for sel, _body, _media in RULES}
    assert len(selectors) >= 50


def test_important_is_used_throughout():
    # The stylesheet overrides Gradio defaults, so most declarations carry
    # !important (143 at the time of writing).
    assert CSS.count("!important") > 100


# ---------------------------------------------------------------------------
# Dark theme and RTL
# ---------------------------------------------------------------------------


def test_dark_theme_background_and_text():
    merged = _merged("body, .gradio-container")
    assert _clean(merged["background-color"]) == "#0b0b0b"
    assert _clean(merged["color"]) == "#ffffff"


def test_rtl_direction_and_arabic_fonts():
    merged = _merged("body, .gradio-container")
    assert _clean(merged["direction"]) == "rtl"
    family = merged["font-family"]
    assert "Cairo" in family
    assert "Tajawal" in family


def test_footer_is_hidden():
    assert _clean(_merged("footer")["visibility"]) == "hidden"


def test_inputs_keep_per_content_direction():
    merged = _merged(".gradio-container input,\n.gradio-container textarea")
    assert _clean(merged["unicode-bidi"]) == "plaintext"


def test_dark_inputs_and_selects():
    merged = _merged('input[type="password"], textarea, select')
    assert _clean(merged["background-color"]) == "#1f1f1f"
    assert _clean(merged["color"]) == "#ffffff"
    assert "#333" in _clean(merged["border"])


# ---------------------------------------------------------------------------
# Top bar (includes a documented cascade quirk)
# ---------------------------------------------------------------------------


def test_topbar_is_sticky():
    first = _rules_for(".vc-topbar")[0]
    assert _clean(first["position"]) == "sticky"
    assert _clean(first["top"]) == "0"
    assert _clean(first["z-index"]) == "20"


def test_topbar_background_is_overridden_by_later_rule():
    # QUIRK (documented, not fixed): ``.vc-topbar`` is defined twice. The
    # first rule sets a dark navy glass background (rgba(2, 6, 23, 0.92));
    # the later "glass card" rule replaces it with rgba(255,255,255,0.05)
    # and adds a border/radius. The sticky positioning from the first rule
    # survives because the second rule does not touch it.
    rules = _rules_for(".vc-topbar")
    assert len(rules) == 2
    assert "rgba(2, 6, 23, 0.92)" in _clean(rules[0]["background"])
    assert _clean(_merged(".vc-topbar")["background"]) == "rgba(255,255,255,0.05)"
    assert "border-radius" in _merged(".vc-topbar")
    assert _clean(_merged(".vc-topbar")["position"]) == "sticky"


def test_topbar_children_do_not_stretch():
    merged = _merged(".vc-topbar > div,\n.vc-topbar > button")
    assert _clean(merged["flex"]) == "0 0 auto"


def test_primary_cta_gets_orange_gradient():
    merged = _merged(".vc-topbar button.primary")
    assert _clean(merged["background"]) == "linear-gradient(90deg, #f97316, #ea580c)"
    assert _clean(merged["color"]) == "#fff"
    assert _clean(merged["font-weight"]) == "700"


# ---------------------------------------------------------------------------
# Tabs (includes documented cascade quirks)
# ---------------------------------------------------------------------------


def test_tab_nav_is_a_rounded_pill_container():
    merged = _merged(".gradio-container .tab-nav")
    assert _clean(merged["border-radius"]) == "14px"
    assert _clean(merged["display"]) == "flex"
    assert _clean(merged["flex-wrap"]) == "wrap"
    assert _clean(merged["justify-content"]) == "center"


def test_tab_button_pill_radius_wins_over_earlier_radius():
    # QUIRK (documented, not fixed): ``.gradio-container .tab-nav button``
    # is defined twice. The first (v6.15 polish) rule sets
    # ``border-radius: 10px`` and ``min-width: 118px``; the later v7.34
    # rule overrides the radius to 999px (pill) and the transition, while
    # the 118px min-width survives.
    rules = _rules_for(".gradio-container .tab-nav button")
    assert len(rules) == 2
    assert _clean(rules[0]["border-radius"]) == "10px"
    assert _clean(rules[1]["border-radius"]) == "999px"
    merged = _merged(".gradio-container .tab-nav button")
    assert _clean(merged["border-radius"]) == "999px"
    assert _clean(merged["min-width"]) == "118px"
    assert "background 0.15s ease" in _clean(merged["transition"])


def test_tab_button_hover_accent_wins_over_earlier_hover():
    # QUIRK (documented, not fixed): the hover rule is also defined twice;
    # the later v7.34 orange tint rgba(249,115,22,0.16) wins over the
    # earlier grey rgba(255,255,255,0.07), while the earlier text colour
    # #f1f5f9 survives.
    rules = _rules_for(".gradio-container .tab-nav button:hover")
    assert len(rules) == 2
    assert "rgba(255,255,255,0.07)" in _clean(rules[0]["background"])
    assert "rgba(249,115,22,0.16)" in _clean(rules[1]["background"])
    merged = _merged(".gradio-container .tab-nav button:hover")
    assert "rgba(249,115,22,0.16)" in _clean(merged["background"])
    assert _clean(merged["color"]) == "#f1f5f9"


def test_tab_button_selected_gradient():
    # Defined twice with the same gradient (harmless duplication).
    rules = _rules_for(".gradio-container .tab-nav button.selected")
    assert len(rules) == 2
    for rule in rules:
        assert _clean(rule["background"]) == "linear-gradient(90deg, #f97316, #ea580c)"
    assert _clean(_merged(".gradio-container .tab-nav button.selected")["color"]) in (
        "#fff",
        "#ffffff",
    )


def test_secondary_buttons_get_warm_outline():
    merged = _merged(".gradio-container button.secondary")
    assert "rgba(249,115,22,0.45)" in _clean(merged["border"])
    hover = _merged(".gradio-container button.secondary:hover")
    assert _clean(hover["border-color"]) == "#f97316"
    assert "rgba(249,115,22,0.12)" in _clean(hover["background"])


# ---------------------------------------------------------------------------
# Known duplicated selectors (layering, not a bug to fix here)
# ---------------------------------------------------------------------------


def test_duplicate_top_level_selectors_are_the_known_layering_set():
    # QUIRK (documented, not fixed): several selectors are defined twice
    # at the top level so a later "polish" block can override individual
    # properties while keeping the rest. The set below is the complete
    # list; if it changes, the override behaviour in the tests above
    # must be re-checked rather than silently assumed.
    from collections import Counter

    counts = Counter(sel for sel, _body, med in RULES if med is None)
    duplicates = {sel for sel, count in counts.items() if count > 1}
    assert duplicates == {
        ".vc-topbar",
        "body, .gradio-container",
        ".gradio-container .tab-nav button",
        ".gradio-container .tab-nav button:hover",
        ".gradio-container .tab-nav button.selected",
        ".vc-panels > div",
        "#vc-create-layout > div",
    }
    assert counts["#vc-create-layout > div"] == 2  # a third copy lives in @media


# ---------------------------------------------------------------------------
# Logs, header, help cards, section headings
# ---------------------------------------------------------------------------


def test_log_output_sizing_and_monospace():
    merged = _merged("#logs_output textarea")
    assert _clean(merged["min-height"]) == "300px"
    assert _clean(merged["max-height"]) == "520px"
    assert "monospace" in merged["font-family"]
    assert _clean(merged["line-height"]) == "1.6"


def test_header_card_keeps_light_text():
    merged = _merged(
        "#vc-header, #vc-header p, #vc-header li, #vc-header strong, #vc-header ul"
    )
    assert _clean(merged["color"]) == "#e2e8f0"
    assert _clean(_merged("#vc-header h1")["color"]) == "#f8fafc"


def test_help_card_is_rtl_with_orange_accent():
    merged = _merged(".vc-help-card")
    assert _clean(merged["direction"]) == "rtl"
    assert _clean(merged["text-align"]) == "right"
    assert "#f97316" in _clean(merged["border-right"])
    assert _clean(merged["color"]) == "#fed7aa"
    assert _clean(merged["line-height"]) == "1.7"


def test_section_headings_get_a_subtle_separator():
    merged = _merged(".gradio-container h3")
    assert _clean(merged["margin-top"]) == "16px"
    assert "1px solid" in _clean(merged["border-bottom"])


def test_updates_card_chips_have_no_trailing_divider():
    assert (
        _clean(_merged(".vc-updates li")["border-bottom"])
        == "1px dashed rgba(255,255,255,0.07)"
    )
    assert _clean(_merged(".vc-updates li:last-child")["border-bottom"]) == "none"


# ---------------------------------------------------------------------------
# Create layout
# ---------------------------------------------------------------------------


def test_create_layout_shell_is_ltr_with_rtl_content():
    shell = _merged("#vc-create-layout")
    assert _clean(shell["direction"]) == "ltr"
    assert _clean(shell["gap"]) == "22px"
    content = _merged("#vc-create-layout > div")
    assert _clean(content["direction"]) == "rtl"
    assert _clean(content["text-align"]) == "right"
    assert _clean(content["min-width"]) == "0"


def test_create_layout_fields_are_rtl():
    merged = _merged(
        '#vc-create-layout input:not([type="checkbox"]),\n'
        "#vc-create-layout textarea,\n"
        "#vc-create-layout select"
    )
    assert _clean(merged["direction"]) == "rtl"
    assert _clean(merged["text-align"]) == "right"


def test_youtube_fields_stay_ltr():
    # URLs must not be flipped by the surrounding RTL layout.
    merged = _merged(
        '#vc-create-layout textarea[placeholder*="youtube"],\n'
        '#vc-create-layout input[placeholder*="youtube"]'
    )
    assert _clean(merged["direction"]) == "ltr"
    assert _clean(merged["text-align"]) == "left"


def test_create_toolbar_buttons_have_a_minimum_height():
    assert _clean(_merged(".vc-create-toolbar button")["min-height"]) == "42px"
    assert _clean(_merged(".vc-create-toolbar button")["white-space"]) == "nowrap"


def test_bottom_actions_order_primary_before_stop():
    assert _clean(_merged("#vc-bottom-actions button.primary")["order"]) == "0"
    assert _clean(_merged("#vc-bottom-actions button.stop")["order"]) == "1"
    button = _merged("#vc-bottom-actions button")
    assert _clean(button["min-height"]) == "48px"
    assert _clean(button["font-weight"]) == "700"


def test_monitor_card_is_present():
    merged = _merged("#vc-monitor")
    assert "rgba(249,115,22,0.20)" in _clean(merged["border"])
    assert _clean(merged["border-radius"]) == "14px"
    label = _merged("#vc-monitor > .label-wrap")
    assert _clean(label["color"]) == "#fed7aa"
    assert _clean(label["font-weight"]) == "700"


# ---------------------------------------------------------------------------
# Validation states
# ---------------------------------------------------------------------------


def test_validation_base_and_all_four_states():
    base = _merged(".vc-validation")
    assert _clean(base["direction"]) == "rtl"
    assert _clean(base["text-align"]) == "right"
    assert _clean(base["border-radius"]) == "12px"
    states = {
        ".vc-validation-ok": ("#bbf7d0", "rgba(34,197,94,.4)"),
        ".vc-validation-warn": ("#fde68a", "rgba(245,158,11,.45)"),
        ".vc-validation-error": ("#fecaca", "rgba(239,68,68,.5)"),
        ".vc-validation-neutral": ("#cbd5e1", None),
    }
    colours = set()
    for selector, (colour, border) in states.items():
        merged = _merged(selector)
        assert _clean(merged["color"]) == colour, selector
        colours.add(colour)
        if border is not None:
            assert border in _clean(merged["border-color"]), selector
    assert len(colours) == 4, "each validation state must have a distinct colour"


# ---------------------------------------------------------------------------
# Review table and panels
# ---------------------------------------------------------------------------


def test_review_table_is_readable_rtl():
    merged = _merged("#review_segments_df")
    assert _clean(merged["direction"]) == "rtl"
    assert _clean(merged["text-align"]) == "right"
    assert _clean(_merged("#review_segments_df table")["min-width"]) == "1080px"
    cells = _merged("#review_segments_df th,\n#review_segments_df td")
    assert _clean(cells["white-space"]) == "normal"
    assert _clean(cells["vertical-align"]) == "top"


def test_review_table_wide_columns_have_a_minimum_width():
    merged = _merged(
        "#review_segments_df th:nth-child(10),\n"
        "#review_segments_df td:nth-child(10),\n"
        "#review_segments_df th:nth-child(11),\n"
        "#review_segments_df td:nth-child(11)"
    )
    assert _clean(merged["min-width"]) == "220px"


def test_panels_are_subtle_cards():
    merged = _merged(".vc-panels > div")
    assert _clean(merged["min-width"]) == "0"  # from the first definition
    assert "rgba(255,255,255,0.03)" in _clean(merged["background"])
    assert _clean(merged["border-radius"]) == "12px"
    heading = _merged(".vc-panels h3")
    assert _clean(heading["border-bottom"]) == "none"
    assert _clean(heading["margin-top"]) == "0"


# ---------------------------------------------------------------------------
# Scrollbars
# ---------------------------------------------------------------------------


def test_log_scrollbar_is_thin():
    assert _clean(_merged("#logs_output textarea::-webkit-scrollbar")["width"]) == "8px"
    thumb = _merged("#logs_output textarea::-webkit-scrollbar-thumb")
    assert _clean(thumb["background"]) == "#334155"
    assert _clean(thumb["border-radius"]) == "4px"


def test_global_scrollbar_uses_brand_orange_on_hover():
    assert _clean(_merged(".gradio-container ::-webkit-scrollbar")["width"]) == "9px"
    thumb = _merged(".gradio-container ::-webkit-scrollbar-thumb")
    assert _clean(thumb["background"]) == "#475569"
    hover = _merged(".gradio-container ::-webkit-scrollbar-thumb:hover")
    assert _clean(hover["background"]) == "#f97316"


# ---------------------------------------------------------------------------
# Responsive breakpoints
# ---------------------------------------------------------------------------


def test_two_breakpoints_both_at_900px():
    media_headers = sorted({med for _sel, _body, med in RULES if med is not None})
    assert media_headers == ["@media (max-width: 900px)"]
    blocks = [
        body
        for sel, body in _top_blocks(_strip_comments(CSS))
        if sel.startswith("@media")
    ]
    assert len(blocks) == 2


def test_small_screens_narrow_the_container_and_wrap_tabs():
    by_selector = dict(_media_rules("max-width: 900px"))
    assert _clean(by_selector[".gradio-container"]["max-width"]) == "96%"
    assert _clean(by_selector[".gradio-container .tab-nav button"]["flex"]) == "1 1 42%"


def test_small_screens_stack_the_create_layout():
    by_selector = dict(_media_rules("max-width: 900px"))
    assert _clean(by_selector["#vc-create-layout"]["flex-direction"]) == "column"
    assert _clean(by_selector["#vc-create-layout > div"]["width"]) == "100%"
    assert _clean(by_selector[".vc-create-toolbar"]["flex-direction"]) == "column"
    assert _clean(by_selector[".vc-create-toolbar button"]["width"]) == "100%"


def test_wide_container_default_is_98_percent():
    merged = _merged(".gradio-container")
    assert _clean(merged["max-width"]) == "98%"
    assert _clean(merged["width"]) == "98%"
    assert _clean(merged["margin"]) == "0 auto"


# ---------------------------------------------------------------------------
# Brand accent and wiring
# ---------------------------------------------------------------------------


def test_brand_accent_is_consistent_orange():
    assert CSS.count("#f97316") >= 5  # 6 at the time of writing
    assert "#ea580c" in CSS  # gradient end colour
    assert "linear-gradient(90deg, #f97316, #ea580c)" in CSS


def test_stylesheet_is_wired_into_the_app():
    # Static check only (importing webui.app would pull in Gradio and the
    # whole pipeline): app.py must pass this CSS to the Gradio Blocks.
    app_path = os.path.join(ROOT, "webui", "app.py")
    with open(app_path, encoding="utf-8") as handle:
        app_source = handle.read()
    assert "css = style.CSS" in app_source


def test_style_module_imports_without_gradio():
    # webui.style is a plain string constant: it must not drag Gradio (or
    # any heavy dependency) in. Block gradio in a subprocess and import.
    code = (
        "import sys; sys.modules['gradio'] = None; "
        "import webui.style; print(len(webui.style.CSS))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert int(result.stdout.strip()) > 5000
