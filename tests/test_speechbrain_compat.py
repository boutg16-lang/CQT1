# -*- coding: utf-8 -*-
"""Direct tests for scripts/speechbrain_compat.py.

Covers patch_speechbrain_inspect_compat with a fake
speechbrain.utils.importutils module (no speechbrain installed here):
no module, non-type LazyModule, missing/unpatchable ensure_module,
successful patching, idempotence, delegation for normal callers, the
inspect-caller AttributeError path, and the deep-stack ValueError path.
"""

import inspect
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import speechbrain_compat as sc  # noqa: E402

_MODULE_NAME = "speechbrain.utils.importutils"


def _install_fake(monkeypatch, lazy_cls):
    fake = types.ModuleType(_MODULE_NAME)
    fake.LazyModule = lazy_cls
    monkeypatch.setitem(sys.modules, _MODULE_NAME, fake)
    return fake


def _recording_lazy():
    calls = []

    class _Lazy:
        def ensure_module(self, stacklevel):
            calls.append(stacklevel)
            return "ORIGINAL"

    return _Lazy, calls


class TestPatchSpeechbrainInspectCompat:
    def test_no_module_returns_false(self, monkeypatch):
        monkeypatch.delitem(sys.modules, _MODULE_NAME, raising=False)
        assert sc.patch_speechbrain_inspect_compat() is False

    def test_lazy_module_not_a_type_returns_false(self, monkeypatch):
        _install_fake(monkeypatch, lazy_cls="not-a-class")
        assert sc.patch_speechbrain_inspect_compat() is False

    def test_lazy_module_without_ensure_module_returns_false(self, monkeypatch):
        class _Lazy:
            pass

        _install_fake(monkeypatch, _Lazy)
        assert sc.patch_speechbrain_inspect_compat() is False

    def test_lazy_module_with_non_callable_ensure_returns_false(self, monkeypatch):
        class _Lazy:
            ensure_module = 42

        _install_fake(monkeypatch, _Lazy)
        assert sc.patch_speechbrain_inspect_compat() is False

    def test_patch_succeeds_and_is_idempotent(self, monkeypatch):
        lazy_cls, calls = _recording_lazy()
        _install_fake(monkeypatch, lazy_cls)
        assert sc.patch_speechbrain_inspect_compat() is True
        assert sc.patch_speechbrain_inspect_compat() is True
        # idempotence: the second call found the marker and did not re-wrap
        assert getattr(lazy_cls.ensure_module, "_cqt_inspect_compat", False) is True
        assert calls == []

    def test_normal_caller_delegates_to_original(self, monkeypatch):
        lazy_cls, calls = _recording_lazy()
        _install_fake(monkeypatch, lazy_cls)
        assert sc.patch_speechbrain_inspect_compat() is True
        instance = lazy_cls()
        assert instance.ensure_module(0) == "ORIGINAL"
        # patched wrapper forwards with stacklevel + 1
        assert calls == [1]

    def test_inspect_caller_gets_attribute_error(self, monkeypatch):
        lazy_cls, calls = _recording_lazy()
        _install_fake(monkeypatch, lazy_cls)
        # Make the patched wrapper believe *this* file is inspect.py, so a
        # call from this test function takes the inspect-caller branch.
        monkeypatch.setattr(inspect, "__file__", os.path.abspath(__file__))
        assert sc.patch_speechbrain_inspect_compat() is True
        with pytest.raises(AttributeError):
            lazy_cls().ensure_module(0)
        assert calls == []

    def test_deep_stack_value_error_delegates(self, monkeypatch):
        lazy_cls, calls = _recording_lazy()
        _install_fake(monkeypatch, lazy_cls)
        assert sc.patch_speechbrain_inspect_compat() is True
        # stacklevel so deep that sys._getframe raises ValueError
        assert lazy_cls().ensure_module(10_000) == "ORIGINAL"
        assert calls == [10_001]

    def test_patch_does_not_touch_other_attributes(self, monkeypatch):
        class _Lazy:
            marker = "keep-me"

            def ensure_module(self, stacklevel):
                return "ORIGINAL"

        _install_fake(monkeypatch, _Lazy)
        assert sc.patch_speechbrain_inspect_compat() is True
        assert _Lazy.marker == "keep-me"
