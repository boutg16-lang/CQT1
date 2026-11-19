# -*- coding: utf-8 -*-
"""A Google 500 during channel verification is retried, not fatal.

channels.list(mine=true) returns transient 500/503 often enough that one
failure must not abort a local run. After retries, an actual upload still
fails clearly; a local-only run continues (see webui/app.py).
"""

import os
import sys
import types
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import upload_gate


class HttpError(Exception):
    def __init__(self, status, message=None):
        self.resp = SimpleNamespace(status=status)
        super().__init__(message or "HttpError %d: backend error" % status)


class FakeService:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def channels(self):
        return self

    def list(self, **kwargs):
        return self

    def execute(self):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _install_service(monkeypatch, service):
    package = types.ModuleType("googleapiclient")
    package.__path__ = []
    discovery = types.ModuleType("googleapiclient.discovery")
    discovery.build = lambda *args, **kwargs: service
    monkeypatch.setitem(sys.modules, "googleapiclient", package)
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", discovery)


def _uploader(tmp_path, monkeypatch):
    uploader = upload_gate.YouTubeUploader(str(tmp_path), dry_run=True)
    monkeypatch.setattr(uploader, "ensure_authenticated", lambda: object())
    return uploader


def test_channel_verification_retries_a_transient_500(tmp_path, monkeypatch):
    service = FakeService(
        [
            HttpError(500),
            HttpError(500),
            {"items": [{"id": "UC123", "snippet": {"title": "قناتي"}}]},
        ]
    )
    _install_service(monkeypatch, service)
    sleeps = []
    monkeypatch.setattr(upload_gate.time, "sleep", sleeps.append)
    channel = _uploader(tmp_path, monkeypatch).verify_channel()
    assert channel == {"id": "UC123", "title": "قناتي"}
    assert service.calls == 3
    assert sleeps == [1.5, 3.0]


def test_persistent_500_raises_a_typed_transient_error(tmp_path, monkeypatch):
    service = FakeService([HttpError(500), HttpError(503), HttpError(500)])
    _install_service(monkeypatch, service)
    monkeypatch.setattr(upload_gate.time, "sleep", lambda seconds: None)
    with pytest.raises(upload_gate.YouTubeTransientError):
        _uploader(tmp_path, monkeypatch).verify_channel()
    assert service.calls == 3


def test_non_transient_error_is_not_retried(tmp_path, monkeypatch):
    service = FakeService([HttpError(400, "bad request")])
    _install_service(monkeypatch, service)
    monkeypatch.setattr(upload_gate.time, "sleep", lambda seconds: None)
    with pytest.raises(RuntimeError) as caught:
        _uploader(tmp_path, monkeypatch).verify_channel()
    assert not isinstance(caught.value, upload_gate.YouTubeTransientError)
    assert service.calls == 1


def test_transient_error_has_a_local_continuation_contract():
    error = upload_gate.YouTubeTransientError("YouTube API is temporarily unavailable")
    assert isinstance(error, RuntimeError)
    assert upload_gate.is_transient_youtube_error(
        HttpError(500, "Internal error encountered")
    )
    assert not upload_gate.is_transient_youtube_error(HttpError(400, "bad request"))
