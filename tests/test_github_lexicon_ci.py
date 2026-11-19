# -*- coding: utf-8 -*-
"""Direct tests for scripts/github_lexicon_ci.py.

Covers _github_output (GITHUB_OUTPUT file vs legacy ::set-output print)
and main() with a stubbed urlopen: stored hash differs / matches /
missing, and a fetch failure propagating. No network.
"""

import hashlib
import json
import os
import sys
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import github_lexicon_ci as glc  # noqa: E402

UPSTREAM_BYTES = b"# lexicon readme\n"
UPSTREAM_DIGEST = hashlib.sha256(UPSTREAM_BYTES).hexdigest()


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def read(self, *args, **kwargs):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _stub_urlopen(monkeypatch, data=UPSTREAM_BYTES):
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda req, timeout=None: _FakeResponse(data)
    )


class TestGithubOutput:
    def test_writes_to_github_output_file(self, tmp_path, monkeypatch):
        out_path = tmp_path / "gh_out"
        monkeypatch.setenv("GITHUB_OUTPUT", str(out_path))
        glc._github_output("changed", "yes")
        glc._github_output("upstream_sha256", UPSTREAM_DIGEST)
        assert out_path.read_text(encoding="utf-8").splitlines() == [
            "changed=yes",
            "upstream_sha256=" + UPSTREAM_DIGEST,
        ]

    def test_appends_to_existing_file(self, tmp_path, monkeypatch):
        out_path = tmp_path / "gh_out"
        out_path.write_text("previous=1\n", encoding="utf-8")
        monkeypatch.setenv("GITHUB_OUTPUT", str(out_path))
        glc._github_output("changed", "no")
        assert out_path.read_text(encoding="utf-8").splitlines() == [
            "previous=1",
            "changed=no",
        ]

    def test_prints_legacy_set_output_without_env(self, monkeypatch, capsys):
        monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
        glc._github_output("changed", "yes")
        assert capsys.readouterr().out == "::set-output name=changed::yes\n"


class TestMain:
    def test_changed_when_stored_differs(self, monkeypatch, capsys):
        _stub_urlopen(monkeypatch)
        assert glc.main() == 0
        out = capsys.readouterr().out
        # repo root pack has a different stored digest (44403508...)
        assert "changed=yes" in out
        assert "upstream=" + UPSTREAM_DIGEST[:16] in out
        assert "stored=4440350840076921" in out

    def test_unchanged_when_stored_matches(self, monkeypatch, capsys):
        _stub_urlopen(monkeypatch)
        monkeypatch.setattr(
            json, "load", lambda f: {"upstream_sha256": UPSTREAM_DIGEST}
        )
        assert glc.main() == 0
        assert "changed=no" in capsys.readouterr().out

    def test_changed_when_pack_missing(self, monkeypatch, capsys):
        _stub_urlopen(monkeypatch)

        def _raise(f):
            raise FileNotFoundError("no pack")

        monkeypatch.setattr(json, "load", _raise)
        assert glc.main() == 0
        out = capsys.readouterr().out
        assert "changed=yes" in out
        assert "stored=none" in out

    def test_changed_when_pack_malformed(self, monkeypatch, capsys):
        _stub_urlopen(monkeypatch)

        def _raise(f):
            raise ValueError("bad json")

        monkeypatch.setattr(json, "load", _raise)
        assert glc.main() == 0
        assert "changed=yes" in capsys.readouterr().out

    def test_writes_outputs_to_github_output(self, tmp_path, monkeypatch):
        _stub_urlopen(monkeypatch)
        out_path = tmp_path / "gh_out"
        monkeypatch.setenv("GITHUB_OUTPUT", str(out_path))
        assert glc.main() == 0
        lines = out_path.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "changed=yes"
        assert lines[1] == "upstream_sha256=" + UPSTREAM_DIGEST

    def test_fetch_failure_propagates(self, monkeypatch):
        def _raise(req, timeout=None):
            raise RuntimeError("offline")

        monkeypatch.setattr(urllib.request, "urlopen", _raise)
        with pytest.raises(RuntimeError, match="offline"):
            glc.main()
