# -*- coding: utf-8 -*-
"""Tests for the signed-manifest update channel (issue #27).

Covers the alternative to GitHub Releases: build → sign → verify → tamper,
the fail-closed rules for unsigned manifests, platform asset selection, and
the integration with ``scripts.auto_updater`` (default channel untouched).
"""
import json
import sys

import pytest

from scripts import auto_updater, sign_artifacts
from scripts import update_manifest as um

HEX64 = "a" * 64


@pytest.fixture()
def keys(tmp_path):
    pytest.importorskip("cryptography")
    generated = sign_artifacts.generate_keypair(str(tmp_path / "keys"))
    return generated


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (um.CHANNEL_ENV, um.MANIFEST_ENV, um.PUBLIC_KEY_ENV, um.ALLOW_UNSIGNED_ENV):
        monkeypatch.delenv(name, raising=False)


def _asset(tmp_path, name="OUSSAMA-Cutter.exe"):
    path = tmp_path / name
    path.write_bytes(b"MZ" + b"\x00" * 2048)
    return {"url": "https://example.com/{}".format(name),
            "sha256": um._sha256_file(str(path))}


# ---------------------------------------------------------------------------
# Build / validate
# ---------------------------------------------------------------------------

def test_build_manifest_shape(tmp_path):
    manifest = um.build_manifest("7.50.0-pro", {"windows": _asset(tmp_path)},
                                 notes="hardening")
    assert manifest["schema"] == um.SCHEMA
    assert manifest["version"] == "7.50.0-pro"
    assert manifest["notes"] == "hardening"
    assert set(manifest["assets"]) == {"windows"}


@pytest.mark.parametrize("assets,fragment", [
    ({}, "at least one"),
    ({"windows": {"url": "u", "sha256": "short"}}, "64-hex"),
    ({"windows": {"sha256": HEX64}}, "no url"),
    ({"solaris": {"url": "u", "sha256": HEX64}}, "unknown platform"),
])
def test_build_manifest_rejects_bad_input(assets, fragment):
    with pytest.raises(um.ManifestError) as caught:
        um.build_manifest("1.0.0", assets)
    assert fragment in str(caught.value)


def test_build_manifest_requires_a_version():
    with pytest.raises(um.ManifestError):
        um.build_manifest("", {"windows": {"url": "u", "sha256": HEX64}})


# ---------------------------------------------------------------------------
# Sign / verify
# ---------------------------------------------------------------------------

def test_signed_manifest_verifies(tmp_path, keys, monkeypatch):
    manifest = um.sign_manifest(
        um.build_manifest("7.50.0-pro", {"windows": _asset(tmp_path)}),
        key_path=keys["private_key"])
    assert um.ASSET_SIGNATURE_KEY in manifest

    monkeypatch.setenv(um.PUBLIC_KEY_ENV, keys["public_key"])
    report = um.verify_manifest(manifest)
    assert report["ok"] is True
    assert report["signature_valid"] is True
    assert report["signed"] is True


def test_tampering_is_detected(tmp_path, keys, monkeypatch):
    manifest = um.sign_manifest(
        um.build_manifest("7.50.0-pro", {"windows": _asset(tmp_path)}),
        key_path=keys["private_key"])
    monkeypatch.setenv(um.PUBLIC_KEY_ENV, keys["public_key"])

    # Swap the advertised hash — that is the attack the signature must stop.
    tampered = json.loads(json.dumps(manifest))
    tampered["assets"]["windows"]["sha256"] = "b" * 64
    report = um.verify_manifest(tampered)
    assert report["ok"] is False
    assert report["signature_valid"] is False

    bumped = json.loads(json.dumps(manifest))
    bumped["version"] = "99.0.0"
    assert um.verify_manifest(bumped)["ok"] is False


def test_unsigned_manifest_is_refused_by_default(tmp_path):
    manifest = um.build_manifest("7.50.0-pro", {"windows": _asset(tmp_path)})
    report = um.verify_manifest(manifest)
    assert report["ok"] is False
    assert "not signed" in report["error"]


def test_unsigned_manifest_needs_explicit_opt_in(tmp_path, monkeypatch):
    manifest = um.build_manifest("7.50.0-pro", {"windows": _asset(tmp_path)})
    monkeypatch.setenv(um.ALLOW_UNSIGNED_ENV, "1")
    report = um.verify_manifest(manifest)
    assert report["ok"] is True
    assert report["signature_valid"] is False
    assert "unsigned" in report["warning"]


def test_signed_manifest_without_a_key_is_refused(tmp_path, keys):
    manifest = um.sign_manifest(
        um.build_manifest("7.50.0-pro", {"windows": _asset(tmp_path)}),
        key_path=keys["private_key"])
    report = um.verify_manifest(manifest)
    assert report["ok"] is False
    assert um.PUBLIC_KEY_ENV in report["error"]


def test_wrong_key_is_detected(tmp_path, keys):
    other = sign_artifacts.generate_keypair(str(tmp_path / "other-keys"))
    manifest = um.sign_manifest(
        um.build_manifest("7.50.0-pro", {"windows": _asset(tmp_path)}),
        key_path=keys["private_key"])
    report = um.verify_manifest(manifest, public_key_path_override=other["public_key"])
    assert report["ok"] is False


def test_non_object_manifest_is_rejected():
    assert um.verify_manifest(["nope"])["ok"] is False
    assert um.verify_manifest({"version": "1.0.0"})["ok"] is False


# ---------------------------------------------------------------------------
# Loading and platform selection
# ---------------------------------------------------------------------------

def test_load_manifest_from_a_local_path(tmp_path):
    manifest = um.build_manifest("7.50.0-pro", {"windows": _asset(tmp_path)})
    path = um.write_manifest(str(tmp_path / "m.json"), manifest)
    assert um.load_manifest(path)["version"] == "7.50.0-pro"
    assert json.loads(open(path, encoding="utf-8").read())["schema"] == um.SCHEMA


def test_load_manifest_missing_file_raises(tmp_path):
    with pytest.raises(um.ManifestError):
        um.load_manifest(str(tmp_path / "nope.json"))
    with pytest.raises(um.ManifestError):
        um.load_manifest("")


def test_load_manifest_invalid_json_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(um.ManifestError):
        um.load_manifest(str(path))


def test_load_manifest_from_url_uses_the_injected_opener():
    payload = json.dumps({"schema": 1, "version": "1.0.0", "assets": {}}).encode()

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return payload

    assert um.load_manifest("https://example.com/m.json",
                            urlopen=lambda request, timeout=0: _Response())["version"] == "1.0.0"


def test_asset_for_platform(tmp_path):
    manifest = um.build_manifest("1.0.0", {
        "windows": _asset(tmp_path, "a.exe"),
        "linux": {"url": "https://example.com/a-linux", "sha256": HEX64},
    })
    assert um.asset_for_platform(manifest, "windows")["url"].endswith(".exe")
    assert um.asset_for_platform(manifest, "linux")["url"].endswith("a-linux")
    assert um.asset_for_platform(manifest, "macos") is None


def test_current_platform_is_known():
    assert um.current_platform() in {"windows", "linux", "macos"}


def test_channel_resolution(monkeypatch):
    assert um.channel() == "github"
    for raw, expected in (("manifest", "manifest"), ("URL", "manifest"),
                          ("off", "off"), ("none", "off"), ("weird", "github")):
        monkeypatch.setenv(um.CHANNEL_ENV, raw)
        assert um.channel() == expected


# ---------------------------------------------------------------------------
# auto_updater integration
# ---------------------------------------------------------------------------

def _write_signed(tmp_path, keys, version="9.9.9-pro", platforms=("windows", "linux")):
    assets = {}
    for platform in platforms:
        name = "OUSSAMA-Cutter.exe" if platform == "windows" else "oussama-cutter-linux"
        assets[platform] = _asset(tmp_path, name)
    manifest = um.sign_manifest(
        um.build_manifest(version, assets), key_path=keys["private_key"])
    return um.write_manifest(str(tmp_path / "update_manifest.json"), manifest)


def test_updater_reports_the_manifest_channel(tmp_path, keys, monkeypatch):
    monkeypatch.setenv(um.CHANNEL_ENV, "manifest")
    monkeypatch.setenv(um.MANIFEST_ENV, _write_signed(tmp_path, keys))
    monkeypatch.setenv(um.PUBLIC_KEY_ENV, keys["public_key"])

    info = auto_updater.check_for_update(current_version="7.49.0-pro")
    assert info["channel"] == "manifest"
    assert info["update_available"] is True
    assert info["latest_version"] == "9.9.9-pro"
    assert info["download_url"]
    assert len(info["sha256"]) == 64


def test_updater_reports_up_to_date(tmp_path, keys, monkeypatch):
    monkeypatch.setenv(um.CHANNEL_ENV, "manifest")
    monkeypatch.setenv(um.MANIFEST_ENV, _write_signed(tmp_path, keys))
    monkeypatch.setenv(um.PUBLIC_KEY_ENV, keys["public_key"])
    assert auto_updater.check_for_update(current_version="9.9.9-pro")["update_available"] is False


def test_updater_refuses_an_unsigned_manifest(tmp_path, monkeypatch):
    manifest = um.build_manifest("9.9.9-pro", {"linux": {"url": "u", "sha256": HEX64}})
    path = um.write_manifest(str(tmp_path / "m.json"), manifest)
    monkeypatch.setenv(um.CHANNEL_ENV, "manifest")
    monkeypatch.setenv(um.MANIFEST_ENV, path)
    info = auto_updater.check_for_update()
    assert info["update_available"] is False
    assert "not signed" in info["error"]


def test_updater_handles_a_missing_manifest_path(tmp_path, monkeypatch):
    monkeypatch.setenv(um.CHANNEL_ENV, "manifest")
    info = auto_updater.check_for_update()
    assert "VIRALCUTTER_UPDATE_MANIFEST" in info["error"]

    monkeypatch.setenv(um.MANIFEST_ENV, str(tmp_path / "gone.json"))
    assert "not found" in auto_updater.check_for_update()["error"]


def test_updater_reports_when_no_asset_matches_the_platform(tmp_path, keys, monkeypatch):
    other = "windows" if um.current_platform() != "windows" else "linux"
    monkeypatch.setenv(um.CHANNEL_ENV, "manifest")
    monkeypatch.setenv(um.MANIFEST_ENV, _write_signed(tmp_path, keys, platforms=(other,)))
    monkeypatch.setenv(um.PUBLIC_KEY_ENV, keys["public_key"])
    info = auto_updater.check_for_update()
    assert info["update_available"] is False
    assert "no asset for platform" in info["error"]


def test_channel_off_disables_checking(monkeypatch):
    monkeypatch.setenv(um.CHANNEL_ENV, "off")
    info = auto_updater.check_for_update()
    assert info["channel"] == "off"
    assert "disabled" in info["error"]


def test_github_channel_still_default(tmp_path, monkeypatch):
    """No manifest env: the original GitHub path must run unchanged."""
    monkeypatch.setenv(um.CHANNEL_ENV, "github")
    info = auto_updater.check_for_update(
        urlopen=lambda timeout: '{"tag_name": "v1.0.0", "assets": []}')
    assert info["latest_version"] == "v1.0.0"
    assert "channel" not in info


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_create_and_verify(tmp_path, keys, capsys):
    asset = tmp_path / "OUSSAMA-Cutter.exe"
    asset.write_bytes(b"MZ" + b"\x00" * 512)
    out = tmp_path / "update_manifest.json"

    rc = um.main([
        "create", "--version", "7.50.0-pro", "--windows", str(asset),
        "--base-url", "https://example.com/dl", "--notes", "n",
        "--out", str(out), "--sign", "--key", keys["private_key"],
    ])
    assert rc == 0
    created = json.loads(out.read_text(encoding="utf-8"))
    assert created["assets"]["windows"]["url"].startswith("https://example.com/dl/")
    assert um.ASSET_SIGNATURE_KEY in created

    assert um.main(["verify", str(out), "--key", keys["public_key"]]) == 0
    assert "OK" in capsys.readouterr().out

    # an unsigned manifest fails verification without the opt-in
    unsigned = tmp_path / "unsigned.json"
    rc = um.main(["create", "--version", "1.0.0", "--windows", str(asset),
                  "--out", str(unsigned)])
    assert rc == 0
    assert um.main(["verify", str(unsigned), "--key", keys["public_key"]]) == 1


def test_cli_rejects_a_url_asset_without_a_hash(tmp_path, capsys):
    assert um.main(["create", "--version", "1.0.0",
                    "--windows", "https://example.com/x.exe"]) == 2
    assert "needs --sha256" in capsys.readouterr().err


def test_cli_show_prints_the_report(tmp_path, keys, capsys):
    asset = tmp_path / "a.exe"
    asset.write_bytes(b"MZ")
    out = tmp_path / "m.json"
    um.main(["create", "--version", "1.0.0", "--windows", str(asset), "--out", str(out)])
    capsys.readouterr()  # discard the create output
    assert um.main(["show", str(out)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["manifest"]["version"] == "1.0.0"
    assert "verification" in payload


@pytest.mark.skipif(not hasattr(sys, "platform"), reason="sanity")
def test_module_imports_without_optional_dependencies(monkeypatch):
    """update_manifest must import with stdlib only (no cryptography needed)."""
    import importlib

    monkeypatch.setitem(sys.modules, "scripts.sign_artifacts", None)
    importlib.reload(um)
    assert um.SCHEMA == 1
    importlib.reload(um)


# ---------------------------------------------------------------------------
# Hardening: normalized output, size caps, shared opt-in parser
# ---------------------------------------------------------------------------

def test_fetch_verified_manifest_returns_the_normalized_copy(tmp_path, keys, monkeypatch):
    """Only signature-covered bytes reach the updater — stray keys are dropped."""
    monkeypatch.setenv(um.PUBLIC_KEY_ENV, keys["public_key"])
    asset = _asset(tmp_path)
    signed = um.sign_manifest(
        um.build_manifest("7.51.0-pro", {"windows": asset}, notes="n"),
        key_path=keys["private_key"])
    raw = json.loads(json.dumps(signed))
    raw["extra"] = {"mirror": "https://evil.example/x.exe"}     # unauthenticated key
    raw["assets"]["windows"]["url"] = "  " + asset["url"] + "  "  # padded but signed
    path = tmp_path / "m.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    out = um.fetch_verified_manifest(source=str(path))
    assert "extra" not in out
    assert out["assets"]["windows"]["url"] == asset["url"]      # stripped form
    assert out[um.ASSET_SIGNATURE_KEY] == raw[um.ASSET_SIGNATURE_KEY]


def test_load_manifest_rejects_oversized_url_payload():
    big = b" " * (um.MAX_MANIFEST_BYTES + 10)

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n=-1):
            return big

    with pytest.raises(um.ManifestError, match="too large"):
        um.load_manifest("https://example.com/big.json",
                         urlopen=lambda request, timeout=0: _Response())


def test_load_manifest_rejects_oversized_local_file(tmp_path):
    path = tmp_path / "big.json"
    path.write_bytes(b" " * (um.MAX_MANIFEST_BYTES + 10))
    with pytest.raises(um.ManifestError, match="too large"):
        um.load_manifest(str(path))


def test_real_urlopen_typeerror_is_not_retried_without_timeout(monkeypatch):
    """A TypeError from the real opener must not re-issue the request bare."""
    calls = []

    def broken(*args, **kwargs):
        calls.append((args, kwargs))
        raise TypeError("boom")

    monkeypatch.setattr(um.urllib.request, "urlopen", broken)
    with pytest.raises(um.ManifestError, match="cannot fetch"):
        um.load_manifest("https://example.com/m.json")
    assert len(calls) == 1            # no silent retry without the timeout
    assert calls[0][1].get("timeout") == 8


def test_allow_unsigned_parser_is_shared(monkeypatch):
    from scripts import auto_updater
    for value, expected in (("1", True), ("true", True), ("YES", True),
                            ("on", True), ("0", False), ("", False)):
        monkeypatch.setenv(um.ALLOW_UNSIGNED_ENV, value)
        assert um.allow_unsigned_update() is expected
        assert auto_updater._allow_unsigned() is expected


# ---------------------------------------------------------------------------
# Pinned (bundled) trust root
# ---------------------------------------------------------------------------

def test_pinned_key_is_the_default_trust_root(tmp_path, keys, monkeypatch):
    """A bundled maintainer-signing.pub verifies manifests with NO env var."""
    monkeypatch.setattr(um, "pinned_public_key_path",
                        lambda: keys["public_key"])
    assert um.public_key_path() == keys["public_key"]

    signed = um.sign_manifest(
        um.build_manifest("7.51.0-pro", {"windows": _asset(tmp_path)}),
        key_path=keys["private_key"])
    path = tmp_path / "m.json"
    path.write_text(json.dumps(signed), encoding="utf-8")
    out = um.fetch_verified_manifest(source=str(path))
    assert out["version"] == "7.51.0-pro"


def test_env_key_is_an_explicit_override_of_the_pinned_key(tmp_path, keys, monkeypatch):
    other = tmp_path / "other.pub"
    other.write_text("not-a-real-key", encoding="utf-8")
    monkeypatch.setattr(um, "pinned_public_key_path",
                        lambda: keys["public_key"])
    monkeypatch.setenv(um.PUBLIC_KEY_ENV, str(other))
    assert um.public_key_path() == str(other)


def test_no_key_anywhere_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(um, "pinned_public_key_path", lambda: None)
    assert um.public_key_path() is None
    signed_like = um.build_manifest("1.0.0", {"windows": _asset(tmp_path)})
    signed_like[um.ASSET_SIGNATURE_KEY] = "AAAA"
    report = um.verify_manifest(signed_like)
    assert report["ok"] is False
    assert "cannot be verified" in report["error"]
