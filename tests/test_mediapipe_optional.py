# -*- coding: utf-8 -*-
"""v6.9.1: mediapipe must be OPTIONAL across the editing modules.

Before the fix, `import mediapipe as mp` at the top of edit_video.py /
one_face.py / two_face.py meant a missing mediapipe install killed the
whole pipeline mid-run with a bare ModuleNotFoundError. The usage site in
edit_video.py already falls back to OpenCV Haar Cascade — these tests pin
the import-time guard.
"""

import importlib
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _import_with_fake_cv2(block_mediapipe):
    """Import the three editing modules with cv2/numpy faked and mediapipe
    optionally blocked. Returns the imported modules.

    Every module first imported while the fakes are installed is evicted
    from ``sys.modules`` afterwards: ``edit_video`` pulls in its own deps
    at import time (``scripts.face_detection_insightface``,
    ``scripts.audio_analysis``, ...), and any of them cached with the fake
    ``cv2``/``numpy`` bound in its globals stays broken for the rest of
    the pytest process — e.g. the v729 crop tests failed with
    ``AttributeError`` on ``cv2.resize`` whenever the mediapipe tests ran
    before the first real import of ``face_detection_insightface``.
    """
    # Snapshot before touching anything: modules absent from this set were
    # (re)imported under the fakes and must not survive the helper.
    before = set(sys.modules)
    saved = {}
    names = ["cv2", "numpy", "scripts.edit_video", "scripts.one_face", "scripts.two_face"]
    for n in names:
        saved[n] = sys.modules.get(n)
        sys.modules.pop(n, None)
    # Remove an already-imported package and its submodules. This matters when
    # the optional dependency is installed: reloading only the top-level name
    # can otherwise reuse cached mediapipe.* modules and bypass the blocker.
    for n in list(sys.modules):
        if n == "mediapipe" or n.startswith("mediapipe."):
            saved[n] = sys.modules.pop(n)

    fake_cv2 = types.ModuleType("cv2")
    fake_np = types.ModuleType("numpy")
    sys.modules["cv2"] = fake_cv2
    sys.modules["numpy"] = fake_np

    class _Blocker:
        def find_spec(self, name, path=None, target=None):
            if name == "mediapipe" or name.startswith("mediapipe."):
                raise ImportError("mediapipe blocked for test")
            return None

        # Keep compatibility with Python versions that still consult the
        # legacy import-hook protocol.
        def find_module(self, name, path=None):
            if name == "mediapipe" or name.startswith("mediapipe."):
                return self
            return None

        def load_module(self, name):
            raise ImportError("mediapipe blocked for test")

    blocker = _Blocker() if block_mediapipe else None
    if blocker:
        sys.meta_path.insert(0, blocker)
    try:
        import scripts.edit_video as ev
        import scripts.one_face as of
        import scripts.two_face as tf
        importlib.reload(of)
        importlib.reload(tf)
        importlib.reload(ev)
        return ev, of, tf
    finally:
        if blocker:
            sys.meta_path.remove(blocker)
        for n, mod in saved.items():
            sys.modules.pop(n, None)
            if mod is not None:
                sys.modules[n] = mod
        # Evict everything that only appeared while the fakes were
        # installed, so a later import re-executes it against the real
        # cv2/numpy instead of reusing the fake-bound module object.
        for n in list(sys.modules):
            if n not in before:
                sys.modules.pop(n, None)


def test_imports_survive_without_mediapipe():
    ev, of, tf = _import_with_fake_cv2(block_mediapipe=True)
    assert ev.MEDIAPIPE_AVAILABLE is False
    assert ev.mp is None
    # the fallback gate in edit_video relies on this being False for mp=None
    assert hasattr(ev.mp, "solutions") is False


def test_modules_expose_expected_functions():
    ev, of, tf = _import_with_fake_cv2(block_mediapipe=True)
    for fn in ("crop_and_resize_single_face", "resize_with_padding",
               "detect_face_or_body", "crop_center_zoom"):
        assert callable(getattr(of, fn)), fn
    assert callable(tf.detect_face_or_body_two_faces)
    assert callable(ev.get_best_encoder)


def test_fake_cv2_does_not_leak_into_later_imports():
    """Modules first imported while the fake cv2 is installed must be
    importable again against the real cv2 afterwards.

    Regression test for the order-dependent v729 failures: when this file
    ran before the first real import of ``scripts.face_detection_insightface``,
    the crop helper stayed cached with the fake ``cv2`` bound and the three
    headroom/face_zoom tests failed with ``AttributeError`` on
    ``cv2.resize``. The module is evicted first so the test exercises the
    leak deterministically regardless of file order, then restored so
    later tests are unaffected."""
    import numpy as np

    target = "scripts.face_detection_insightface"
    saved = sys.modules.pop(target, None)
    try:
        _import_with_fake_cv2(block_mediapipe=True)
        mod = importlib.import_module(target)
        assert hasattr(mod.cv2, "resize"), "cv2 leaked the test fake"
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        out = mod.crop_and_resize_insightface(
            frame, [90, 40, 110, 60], headroom=0.0, face_zoom=0.0)
        assert out.shape == (1920, 1080, 3)
    finally:
        sys.modules.pop(target, None)
        if saved is not None:
            sys.modules[target] = saved
