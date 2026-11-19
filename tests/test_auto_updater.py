"""Direct tests for scripts/auto_updater.py.

No network is ever touched: _github_api / urlopen / urllib are stubbed,
and downloads use a fake streaming response into tmp_path. The updater's
fail-closed integrity policy is exercised end to end.
"""

import hashlib
import io
import json
import os
import sys

import pytest

from scripts import auto_updater

# ------------------------------------------------------------ version parsing


@pytest.mark.parametrize("tag,expected", [
    ("v0.9.0", (0, 9, 0)),
    ("0.9.0", (0, 9, 0)),
    ("7.51.0", (7, 51, 0)),
    ("7.51.0-rc1", (7, 51, 0)),  # suffix digits must NOT leak into the core
    ("7.51.0-pro", (7, 51, 0)),
    ("v1.2.3-final", (1, 2, 3)),
    ("7.51", (7, 51, 0)),
    ("7", (7, 0, 0)),
    ("  v2.0.1  ", (2, 0, 1)),
    ("", (0, 0, 0)),
    (None, (0, 0, 0)),
    ("garbage", (0, 0, 0)),
    ("1.2.3.4", (0, 0, 0)),
    ("v1.2.x", (0, 0, 0)),
])
def test_parse_version(tag, expected):
    assert auto_updater._parse_version(tag) == expected


@pytest.mark.parametrize("tag,expected", [
    ("7.51.0", 1),
    ("7.51.0-pro", 1),
    ("7.51.0-final", 1),
    ("7.51.0-stable", 1),
    ("7.51.0-release", 1),
    ("7.51.0-rc1", 0),
    ("7.51.0-beta", 0),
    ("7.51.0-alpha", 0),
    ("7.51.0-dev", 0),
    ("garbage", -1),
    ("", -1),
    (None, -1),
])
def test_version_rank(tag, expected):
    assert auto_updater._version_rank(tag) == expected


def test_version_key_prerelease_ranks_below_final():
    assert auto_updater._version_key("7.51.0-rc1") < auto_updater._version_key("7.51.0")
    assert auto_updater._version_key("7.51.0-pro") == auto_updater._version_key("7.51.0")
    assert auto_updater._version_key("7.51.0") < auto_updater._version_key("7.52.0")
    assert auto_updater._version_key("garbage") < auto_updater._version_key("0.0.1")


def test_newer_uses_local_version(monkeypatch):
    monkeypatch.setattr(auto_updater, "LOCAL_VERSION", "8.10.1")
    assert auto_updater._newer("8.10.2") is True
    assert auto_updater._newer("8.10.1") is False
    assert auto_updater._newer("8.10.1-rc1") is False
    assert auto_updater._newer("8.9.9") is False
    assert auto_updater._newer("garbage") is False


# ---------------------------------------------------------------- _tag_from_url


def test_tag_from_url():
    url = "https://github.com/boutg16-lang/CQT1/releases/download/v8.10.1/app.exe"
    assert auto_updater._tag_from_url(url) == "v8.10.1"
    assert auto_updater._tag_from_url("https://example.com/file.bin") == ""
    assert auto_updater._tag_from_url("") == ""
    assert auto_updater._tag_from_url(None) == ""


# ---------------------------------------------------------------------- _sha256


def test_sha256_matches_hashlib(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"abc" * 1000)
    assert auto_updater._sha256(str(path)) == hashlib.sha256(b"abc" * 1000).hexdigest()


# ------------------------------------------------------------- check_for_update


def _release_json(**overrides):
    data = {"tag_name": "v9.0.0", "body": "notes", "assets": []}
    data.update(overrides)
    return json.dumps(data)


def test_check_for_update_urlopen_newer_release(monkeypatch):
    monkeypatch.setattr(auto_updater, "LOCAL_VERSION", "8.10.1")
    monkeypatch.setattr(auto_updater, "_update_channel", lambda: "github")
    payload = _release_json(assets=[
        {"name": "app-linux", "browser_download_url": "https://x/app-linux"},
        {"name": "app.exe", "browser_download_url": "https://x/app.exe"},
    ])
    info = auto_updater.check_for_update(urlopen=lambda timeout: payload)
    assert info["update_available"] is True
    assert info["latest_version"] == "v9.0.0"
    assert info["notes"] == "notes"
    assert info["error"] is None
    if os.name == "nt":
        assert info["download_url"] == "https://x/app.exe"
    else:
        assert info["download_url"] == "https://x/app-linux"


def test_check_for_update_same_version_not_newer(monkeypatch):
    monkeypatch.setattr(auto_updater, "LOCAL_VERSION", "9.0.0")
    monkeypatch.setattr(auto_updater, "_update_channel", lambda: "github")
    info = auto_updater.check_for_update(urlopen=lambda timeout: _release_json())
    assert info["update_available"] is False
    assert info["latest_version"] == "v9.0.0"


def test_check_for_update_no_matching_asset(monkeypatch):
    monkeypatch.setattr(auto_updater, "LOCAL_VERSION", "8.10.1")
    monkeypatch.setattr(auto_updater, "_update_channel", lambda: "github")
    # Only a .exe on a POSIX box (or only a .bin on Windows): no fallback
    # to a random asset — download_url stays None.
    name = "app.exe" if os.name != "nt" else "app-linux"
    payload = _release_json(assets=[
        {"name": name, "browser_download_url": "https://x/" + name}])
    info = auto_updater.check_for_update(urlopen=lambda timeout: payload)
    assert info["update_available"] is True
    assert info["download_url"] is None


def test_check_for_update_notes_truncated_to_400(monkeypatch):
    monkeypatch.setattr(auto_updater, "LOCAL_VERSION", "8.10.1")
    monkeypatch.setattr(auto_updater, "_update_channel", lambda: "github")
    payload = _release_json(body="n" * 1000)
    info = auto_updater.check_for_update(urlopen=lambda timeout: payload)
    assert len(info["notes"]) == 400


def test_check_for_update_urlopen_failure_reports_error(monkeypatch):
    monkeypatch.setattr(auto_updater, "LOCAL_VERSION", "8.10.1")
    monkeypatch.setattr(auto_updater, "_update_channel", lambda: "github")

    def boom(timeout):
        raise RuntimeError("offline")

    info = auto_updater.check_for_update(urlopen=boom)
    assert info["update_available"] is False
    assert info["latest_version"] is None
    assert "offline" in info["error"]


def test_check_for_update_falls_back_to_latest_tag(monkeypatch):
    monkeypatch.setattr(auto_updater, "LOCAL_VERSION", "8.10.1")
    monkeypatch.setattr(auto_updater, "_update_channel", lambda: "github")

    def boom(path, timeout=8):
        raise RuntimeError("no releases")

    monkeypatch.setattr(auto_updater, "_github_api", boom)
    monkeypatch.setattr(auto_updater, "_latest_tag", lambda repo, timeout=8: "v9.1.0")
    info = auto_updater.check_for_update()
    assert info["update_available"] is True
    assert info["latest_version"] == "v9.1.0"
    assert info["download_url"] is None
    assert info["error"] is None


def test_check_for_update_tag_fallback_empty(monkeypatch):
    monkeypatch.setattr(auto_updater, "LOCAL_VERSION", "8.10.1")
    monkeypatch.setattr(auto_updater, "_update_channel", lambda: "github")
    monkeypatch.setattr(auto_updater, "_github_api",
                        lambda path, timeout=8: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(auto_updater, "_latest_tag", lambda repo, timeout=8: "")
    info = auto_updater.check_for_update()
    assert info["update_available"] is False
    assert info["latest_version"] is None


def test_check_for_update_channel_off(monkeypatch):
    monkeypatch.setattr(auto_updater, "_update_channel", lambda: "off")
    info = auto_updater.check_for_update()
    assert info["update_available"] is False
    assert info["channel"] == "off"
    assert "disabled" in info["error"]


def test_check_for_update_current_version_overrides_local(monkeypatch):
    monkeypatch.setattr(auto_updater, "_update_channel", lambda: "github")
    payload = _release_json(tag_name="v9.0.0")
    info = auto_updater.check_for_update(current_version="8.0.0",
                                         urlopen=lambda timeout: payload)
    assert info["update_available"] is True
    assert auto_updater.LOCAL_VERSION == "8.0.0"


# -------------------------------------------------------------- _fetch_checksums


class _FakeResponse:
    def __init__(self, text):
        self._buf = io.BytesIO(text.encode("utf-8"))

    def read(self, *args):
        return self._buf.read(*args)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _stub_urlopen(monkeypatch, mapping):
    """mapping: url substring -> response text (or exception to raise)."""
    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        for key, value in mapping.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return _FakeResponse(value)
        raise RuntimeError("unexpected url: " + url)

    monkeypatch.setattr(auto_updater.urllib.request, "urlopen", fake_urlopen)


def test_fetch_checksums_parses_manifest(monkeypatch):
    digest = "a" * 64
    _stub_urlopen(monkeypatch, {"checksums.txt":
        "{}  app-linux\n{} *app.exe\nshort line\n".format(digest, "b" * 64)})
    out = auto_updater._fetch_checksums("boutg16-lang/CQT1", "v9.0.0")
    assert out == {"app-linux": digest, "app.exe": "b" * 64}


def test_fetch_checksums_falls_back_to_sha256sums(monkeypatch):
    digest = "c" * 64
    _stub_urlopen(monkeypatch, {
        "checksums.txt": RuntimeError("404"),
        "SHA256SUMS": "{}  app-linux\n".format(digest)})
    out = auto_updater._fetch_checksums("boutg16-lang/CQT1", "v9.0.0")
    assert out == {"app-linux": digest}


def test_fetch_checksums_empty_when_no_manifest(monkeypatch):
    _stub_urlopen(monkeypatch, {"releases/download": RuntimeError("404")})
    assert auto_updater._fetch_checksums("boutg16-lang/CQT1", "v9.0.0") == {}
    assert auto_updater._fetch_checksums("", "v9.0.0") == {}
    assert auto_updater._fetch_checksums("boutg16-lang/CQT1", "") == {}


# --------------------------------------------------------------- download_update


def _stub_download(monkeypatch, body=b"binary-bytes"):
    class _Stream:
        def __init__(self, data):
            self._buf = io.BytesIO(data)

        def read(self, n=-1):
            return self._buf.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def stream_urlopen(req, timeout=None):
        return _Stream(body)

    monkeypatch.setattr(auto_updater.urllib.request, "urlopen", stream_urlopen)


def test_download_update_verified_by_expected_sha(monkeypatch, tmp_path):
    body = b"binary-bytes"
    _stub_download(monkeypatch, body)
    monkeypatch.setattr(auto_updater, "LOCAL_VERSION", "8.10.1")
    dest = auto_updater.download_update(
        "https://example.com/app-linux", dest_dir=str(tmp_path),
        expected_sha256=hashlib.sha256(body).hexdigest())
    assert dest == str(tmp_path / "app-linux")
    assert (tmp_path / "app-linux").read_bytes() == body
    info = json.loads((tmp_path / "update_info.json").read_text(encoding="utf-8"))
    assert info["asset"] == "app-linux"
    assert info["sha256"] == hashlib.sha256(body).hexdigest()


def test_download_update_checksum_mismatch_refused(monkeypatch, tmp_path):
    _stub_download(monkeypatch, b"binary-bytes")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        auto_updater.download_update(
            "https://example.com/app-linux", dest_dir=str(tmp_path),
            expected_sha256="0" * 64)
    assert not (tmp_path / "app-linux").exists()
    assert not (tmp_path / "app-linux.part").exists()


def test_download_update_no_manifest_refused(monkeypatch, tmp_path):
    _stub_download(monkeypatch, b"binary-bytes")
    monkeypatch.setattr(auto_updater, "_fetch_checksums", lambda repo, tag: {})
    monkeypatch.setattr(auto_updater, "_allow_unsigned", lambda: False)
    with pytest.raises(RuntimeError, match="no checksum manifest"):
        auto_updater.download_update(
            "https://github.com/boutg16-lang/CQT1/releases/download/v9.0.0/app-linux",
            dest_dir=str(tmp_path))
    assert not (tmp_path / "app-linux").exists()


def test_download_update_unsigned_allowed_by_opt_in(monkeypatch, tmp_path):
    _stub_download(monkeypatch, b"binary-bytes")
    monkeypatch.setattr(auto_updater, "_fetch_checksums", lambda repo, tag: {})
    monkeypatch.setattr(auto_updater, "_allow_unsigned", lambda: True)
    dest = auto_updater.download_update(
        "https://github.com/boutg16-lang/CQT1/releases/download/v9.0.0/app-linux",
        dest_dir=str(tmp_path))
    assert dest == str(tmp_path / "app-linux")
    assert (tmp_path / "app-linux").read_bytes() == b"binary-bytes"


def test_download_update_uses_manifest_checksum(monkeypatch, tmp_path):
    body = b"binary-bytes"
    _stub_download(monkeypatch, body)
    monkeypatch.setattr(auto_updater, "_fetch_checksums",
                        lambda repo, tag: {"app-linux": hashlib.sha256(body).hexdigest()})
    dest = auto_updater.download_update(
        "https://github.com/boutg16-lang/CQT1/releases/download/v9.0.0/app-linux",
        dest_dir=str(tmp_path))
    assert dest == str(tmp_path / "app-linux")


def test_download_update_dash_underscore_drift(monkeypatch, tmp_path):
    body = b"binary-bytes"
    _stub_download(monkeypatch, body)
    monkeypatch.setattr(auto_updater, "_fetch_checksums",
                        lambda repo, tag: {"app_linux": hashlib.sha256(body).hexdigest()})
    dest = auto_updater.download_update(
        "https://github.com/boutg16-lang/CQT1/releases/download/v9.0.0/app-linux",
        dest_dir=str(tmp_path))
    assert dest == str(tmp_path / "app-linux")


# ------------------------------------------------------------------ update_info


def test_update_info_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_updater, "LOCAL_VERSION", "8.10.1")
    assert auto_updater.update_info(str(tmp_path)) == ("8.10.1", None)


def test_update_info_reads_json(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_updater, "LOCAL_VERSION", "8.10.1")
    (tmp_path / "update_info.json").write_text(
        json.dumps({"local_version": "8.10.0", "asset": "app-linux"}),
        encoding="utf-8")
    assert auto_updater.update_info(str(tmp_path)) == ("8.10.0", "app-linux")


def test_update_info_malformed_json(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_updater, "LOCAL_VERSION", "8.10.1")
    (tmp_path / "update_info.json").write_text("{not json", encoding="utf-8")
    assert auto_updater.update_info(str(tmp_path)) == ("8.10.1", None)


# --------------------------------------------------------- apply_pending_update


def _stage_update(tmp_path, body=b"binary-bytes", tag="v9.0.0", sha=None):
    asset = tmp_path / "app-linux"
    asset.write_bytes(body)
    info = {"downloaded": str(asset), "asset": "app-linux",
            "local_version": "8.10.1", "tag": tag}
    if sha:
        info["sha256"] = sha
    (tmp_path / "update_info.json").write_text(json.dumps(info), encoding="utf-8")
    return str(asset)


def test_apply_pending_nothing_pending(tmp_path):
    assert auto_updater.apply_pending_update(str(tmp_path)) is None


def test_apply_pending_anti_rollback(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_updater, "LOCAL_VERSION", "9.0.0")
    _stage_update(tmp_path, tag="v8.10.1")
    assert auto_updater.apply_pending_update(str(tmp_path)) is None


def test_apply_pending_force_overrides_rollback(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_updater, "LOCAL_VERSION", "9.0.0")
    asset = _stage_update(tmp_path, tag="v8.10.1")
    assert auto_updater.apply_pending_update(str(tmp_path), force=True) == asset


def test_apply_pending_sha_mismatch_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_updater, "LOCAL_VERSION", "8.10.1")
    _stage_update(tmp_path, tag="v9.0.0", sha="0" * 64)
    assert auto_updater.apply_pending_update(str(tmp_path)) is None


def test_apply_pending_source_install_returns_path(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_updater, "LOCAL_VERSION", "8.10.1")
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    body = b"binary-bytes"
    asset = _stage_update(tmp_path, body=body, tag="v9.0.0",
                          sha=hashlib.sha256(body).hexdigest())
    assert auto_updater.apply_pending_update(str(tmp_path)) == asset


def test_apply_pending_missing_asset_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_updater, "LOCAL_VERSION", "8.10.1")
    (tmp_path / "update_info.json").write_text(
        json.dumps({"downloaded": str(tmp_path / "gone.bin"), "tag": "v9.0.0"}),
        encoding="utf-8")
    assert auto_updater.apply_pending_update(str(tmp_path)) is None


# ------------------------------------------------------------------------ main


def test_main_no_args_prints_local_version(monkeypatch, capsys):
    monkeypatch.setattr(auto_updater, "LOCAL_VERSION", "8.10.1")
    monkeypatch.setattr(sys, "argv", ["auto_updater.py"])
    assert auto_updater.main() == 0
    assert "local version: 8.10.1" in capsys.readouterr().out


def test_main_check_reports_update(monkeypatch, capsys):
    monkeypatch.setattr(auto_updater, "LOCAL_VERSION", "8.10.1")
    monkeypatch.setattr(sys, "argv", ["auto_updater.py", "--check"])
    monkeypatch.setattr(auto_updater, "check_for_update",
                        lambda **k: {"update_available": True,
                                     "latest_version": "v9.0.0",
                                     "download_url": "https://x/app-linux",
                                     "error": None})
    assert auto_updater.main() == 0
    out = capsys.readouterr().out
    assert "UPDATE AVAILABLE: v9.0.0" in out
    assert "download: https://x/app-linux" in out


def test_main_check_failure_is_not_fatal(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["auto_updater.py", "--check"])
    monkeypatch.setattr(auto_updater, "check_for_update",
                        lambda **k: {"update_available": False,
                                     "latest_version": None,
                                     "download_url": None,
                                     "error": "offline"})
    assert auto_updater.main() == 0
    assert "update check failed: offline" in capsys.readouterr().out
