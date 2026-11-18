# -*- coding: utf-8 -*-
"""Guard against drift between duplicated root and Android asset copies.

The repo ships the same files twice: the Python app reads the copies at
the repo root (``fonts/``, ``safety_*.json``) while the Android app reads
its own copies under ``app/src/main/res/font/`` and
``app/src/main/assets/``. Nothing syncs them, so an edit to one copy used
to silently diverge from the other.

These tests compare bytes (md5), not parsed content, so even a
whitespace-only or binary drift fails fast. Do NOT delete either copy:
the Android build may rely on its own tree.

Note: ``fonts/Montserrat-ExtraBold.ttf`` ships only under ``fonts/``
(no Android copy) and is intentionally not listed here — do not
force-sync it.
"""

import hashlib
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (repo-root copy, Android copy)
DUPLICATED_FILES = (
    ("fonts/Cairo-Bold.ttf", "app/src/main/res/font/cairo_bold.ttf"),
    ("fonts/Montserrat-Bold.ttf", "app/src/main/res/font/montserrat_bold.ttf"),
    ("fonts/Montserrat-Regular.ttf", "app/src/main/res/font/montserrat_regular.ttf"),
    ("safety_blocklist.json", "app/src/main/assets/safety_blocklist.json"),
    ("safety_terms_arabic.json", "app/src/main/assets/safety_terms_arabic.json"),
)


def _md5(path):
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.mark.parametrize("root_rel,android_rel", DUPLICATED_FILES)
def test_duplicated_asset_matches_android_copy(root_rel, android_rel):
    root_path = os.path.join(ROOT, root_rel)
    android_path = os.path.join(ROOT, android_rel)
    assert os.path.isfile(root_path), f"missing root copy: {root_rel}"
    assert os.path.isfile(android_path), f"missing Android copy: {android_rel}"
    assert _md5(root_path) == _md5(android_path), (
        f"{root_rel} drifted from {android_rel} — update both copies "
        "together (see test_lexicon_sync.py for the safety lexicons)."
    )
