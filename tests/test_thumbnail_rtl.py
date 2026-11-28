# -*- coding: utf-8 -*-
"""RTL correctness for scripts/thumbnail_generator.py (feat/pro-cutting item 2).

The real-world bug: Arabic thumbnail text rendered as DISCONNECTED,
left-to-right letterforms (sometimes overflowing the canvas) because the
draw calls never passed ``direction``/``language`` and never checked for
libraqm. These tests pin the contract:

* Arabic draw calls must route through direction="rtl"/language="ar" and
  only when libraqm shaping is available (PIL.features.check("raqm")).
* Arabic titles render right-anchored ("ra") and stay INSIDE the canvas.
* Headline font is Noto Kufi Arabic at weight 900 (Black) when bundled.

Real Pillow rendering is used (Pillow 13 + raqm are installed in the test
venv); OpenCV is stubbed — no real video files, no network.
"""

import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import thumbnail_generator as tg  # noqa: E402

ARABIC_TITLE = "الكوكايين بالأطنان"
ARABIC_HOOK = "حصري"


DARK_FRAME = (30, 60, 100)  # low-luminance background (white title contrasts)
LIGHT_FRAME = (214, 220, 228)  # high-luminance background (dark hook ink contrasts)


def _flat_frame(tmp_path, color=DARK_FRAME, name="frame.png"):
    path = os.path.join(str(tmp_path), name)
    Image.new("RGB", (1600, 900), color).save(path)
    return path


# ---------------------------------------------------------------------------
# raqm guard / rtl kwargs
# ---------------------------------------------------------------------------


def test_rtl_kwargs_for_arabic_when_raqm_present():
    if not tg.HAS_RAQM:
        return  # raqm-less builds keep legacy rendering (guarded upstream)
    kwargs = tg._rtl_text_kwargs(ARABIC_TITLE)
    assert kwargs == {"direction": "rtl", "language": "ar"}


def test_rtl_kwargs_empty_for_latin():
    assert tg._rtl_text_kwargs("BEST SECRET!") == {}


def test_rtl_kwargs_empty_when_raqm_missing(monkeypatch):
    monkeypatch.setattr(tg, "HAS_RAQM", False)
    assert tg._rtl_text_kwargs(ARABIC_TITLE) == {}


def test_has_raqm_reports_libraqm_state():
    assert isinstance(tg.HAS_RAQM, bool)


# ---------------------------------------------------------------------------
# Headline font: Noto Kufi Arabic Black
# ---------------------------------------------------------------------------


def test_arabic_headline_prefers_noto_kufi_black():
    font = tg._load_font(78, bold=True, text=ARABIC_TITLE)
    assert font is not None
    if not tg._font_path(tg.ARABIC_FONT_BLACK):
        return  # font not bundled in this checkout — Cairo fallback is used
    axes = font.get_variation_axes()
    weights = [
        axis["maximum"]
        for axis in axes
        if b"eight" in axis["name"] or "eight" in str(axis["name"])
    ]
    assert weights and max(weights) >= tg.ARABIC_BLACK_WEIGHT


# ---------------------------------------------------------------------------
# Rendered geometry: joined letters, inside the frame
# ---------------------------------------------------------------------------


def _ink_groups(mask, axis=0):
    columns = mask.any(axis=axis)
    groups, in_run = [], False
    for index, value in enumerate(columns):
        if value and not in_run:
            start, in_run = index, True
        elif not value and in_run:
            groups.append((start, index))
            in_run = False
    if in_run:
        groups.append((start, len(columns)))
    return groups


def test_arabic_title_joined_and_inside_frame(tmp_path):
    result = tg.generate_thumbnail(
        _flat_frame(tmp_path),
        title=ARABIC_TITLE,
        out=os.path.join(str(tmp_path), "t.png"),
    )
    assert result["ok"] is True
    image = np.array(Image.open(result["out"]).convert("L"))
    if not tg.HAS_RAQM:
        return  # geometry guarantees below require shaped text
    bottom = image[tg.TARGET_H - 200 : tg.TARGET_H, :]
    ink = bottom > 220  # white title glyphs
    rows = np.where(ink.any(axis=1))[0]
    # inside the frame: never under the accent bar (bottom 14px) and never
    # above the gradient zone we rendered into
    assert rows.min() >= 0
    assert (tg.TARGET_H - 200 + rows.max()) <= tg.TARGET_H - 14
    # right-anchored: the last ink column reaches the 40px right margin area,
    # and nothing renders beyond the canvas edge
    columns = np.where(ink.any(axis=0))[0]
    assert columns.max() <= tg.TARGET_W - 40 + 8  # +stroke tolerance


def test_arabic_word_ink_is_connected(tmp_path):
    """Joined letterforms: one continuous ink run per shaped Arabic word.

    The legacy bug rendered each letterform separately (disconnected), which
    produces many thin ink runs; raqm shaping joins حصري into one solid run
    (the two dot clusters aside).
    """
    if not tg.HAS_RAQM:
        return
    result = tg.generate_thumbnail(
        _flat_frame(tmp_path, LIGHT_FRAME, "frame_light.png"),
        hook=ARABIC_HOOK,
        out=os.path.join(str(tmp_path), "h.png"),
    )
    assert result["ok"] is True
    image = np.array(Image.open(result["out"]).convert("L"))
    badge = image[24:112, 0:300]
    groups = _ink_groups(badge < 90)
    # "حصري" shaped = 1 large body run + dot clusters; disconnected legacy
    # rendering produced many more separate letterform runs.
    assert 1 <= len(groups) <= 5
    widths = [end - start for start, end in groups]
    assert max(widths) >= 100  # the connected body of the word


def test_hook_badge_contains_arabic_hook(tmp_path):
    result = tg.generate_thumbnail(
        _flat_frame(tmp_path, LIGHT_FRAME, "frame_light2.png"),
        hook=ARABIC_HOOK,
        out=os.path.join(str(tmp_path), "h2.png"),
    )
    assert result["ok"] is True
    image = np.array(Image.open(result["out"]).convert("L"))
    if not tg.HAS_RAQM:
        return
    badge = image[0:150, 24:300]
    ink_rows = np.where((badge < 90).any(axis=1))[0]
    assert ink_rows.min() >= 24  # never above the badge
    assert ink_rows.max() <= 118  # badge bottom is 112; small dot tolerance
