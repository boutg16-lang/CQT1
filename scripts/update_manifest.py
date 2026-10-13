# -*- coding: utf-8 -*-
"""Signed update manifests — a release channel that does not need GitHub.

Issue #27: ``auto_updater`` reads GitHub Releases anonymously, so it cannot work
while this repository is private, and issues #25/#26 show how fragile the
Release pipeline itself is. This module adds an independent channel:

* the maintainer publishes the binary **anywhere** (own site, object storage,
  a public release repo, a shared drive) plus a small ``update_manifest.json``;
* the manifest lists each platform's download URL *and its SHA-256*, so the
  existing fail-closed download check still applies;
* if a verification key is available, the manifest must carry a valid
  detached Ed25519 signature over its canonical bytes — signing the manifest
  signs the hashes, which signs the binary. Key resolution: a build-pinned
  ``keys/maintainer-signing.pub`` (bundled by ``packaging/viralcutter.spec``)
  is the default trust root; ``VIRALCUTTER_UPDATE_PUBLIC_KEY`` is an explicit
  override (noted on stderr). Without any key, the manifest is refused unless
  the operator explicitly opts in with ``VIRALCUTTER_ALLOW_UNSIGNED_UPDATE=1``.

Configuration (environment):

* ``VIRALCUTTER_UPDATE_CHANNEL``   ``github`` (default) | ``manifest`` | ``off``
* ``VIRALCUTTER_UPDATE_MANIFEST``  URL (http/https) or local path to the manifest
* ``VIRALCUTTER_UPDATE_PUBLIC_KEY`` public key path for signature verification
* ``VIRALCUTTER_ALLOW_UNSIGNED_UPDATE`` ``1`` to accept an unsigned manifest

CLI examples::

    # maintainer: build + optionally sign a manifest next to the artifacts
    python -m scripts.update_manifest create --version 7.50.0-pro \
        --windows dist/OUSSAMA-Cutter.exe \
        --base-url https://example.com/downloads \
        --notes "hardening release" --out update_manifest.json \
        --sign --key keys/maintainer-signing.key

    # anyone: verify a manifest before shipping it
    python -m scripts.update_manifest verify update_manifest.json --key keys/maintainer-signing.pub
"""

from __future__ import annotations

import argparse
import base64
import datetime as _dt
import json
import os
import sys
import tempfile
import urllib.request
from typing import Any, Dict, Optional, Sequence

SCHEMA = 1
DEFAULT_NAME = "update_manifest.json"
CHANNEL_ENV = "VIRALCUTTER_UPDATE_CHANNEL"
MANIFEST_ENV = "VIRALCUTTER_UPDATE_MANIFEST"
PUBLIC_KEY_ENV = "VIRALCUTTER_UPDATE_PUBLIC_KEY"
ALLOW_UNSIGNED_ENV = "VIRALCUTTER_ALLOW_UNSIGNED_UPDATE"
ASSET_SIGNATURE_KEY = "signature"
PLATFORMS = ("windows", "linux", "macos")


class ManifestError(Exception):
    """Raised for malformed manifests and unreachable sources."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def channel() -> str:
    """Active update channel: ``github`` (default), ``manifest`` or ``off``."""
    raw = str(os.getenv(CHANNEL_ENV, "github") or "github").strip().lower()
    if raw in {"manifest", "file", "url"}:
        return "manifest"
    if raw in {"off", "none", "disabled"}:
        return "off"
    return "github"


def manifest_source() -> Optional[str]:
    value = str(os.getenv(MANIFEST_ENV, "") or "").strip()
    return value or None


# Bundled trust root: a build that ships keys/maintainer-signing.pub verifies
# updates against THAT key by default — the environment no longer decides.
PINNED_KEY_REL = os.path.join("keys", "maintainer-signing.pub")


def _pinned_key_candidates() -> Sequence[str]:
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)          # PyInstaller onefile
    if meipass:
        candidates.append(os.path.join(meipass, PINNED_KEY_REL))
    if getattr(sys, "frozen", False):                  # onedir / sidecar
        candidates.append(os.path.join(os.path.dirname(sys.executable), PINNED_KEY_REL))
    # source checkout: <repo root>/keys/maintainer-signing.pub
    candidates.append(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), PINNED_KEY_REL))
    return candidates


def pinned_public_key_path() -> Optional[str]:
    """Path of the build-pinned update key, or None when none is bundled."""
    for path in _pinned_key_candidates():
        if os.path.isfile(path):
            return path
    return None


def public_key_path() -> Optional[str]:
    """Effective verification key: env override first, then the pinned key.

    ``VIRALCUTTER_UPDATE_PUBLIC_KEY`` is an *explicit operator override* —
    honoured, but noted once per process because it re-roots update trust.
    Without it, a build-pinned key is used; without either, signed manifests
    cannot be verified and are refused (fail-closed).
    """
    value = str(os.getenv(PUBLIC_KEY_ENV, "") or "").strip()
    if value:
        return value
    return pinned_public_key_path()


def allow_unsigned_update() -> bool:
    """Shared opt-in parser: ``1/true/yes/on`` (case-insensitive).

    ``scripts.auto_updater`` uses this same helper so the env var behaves
    identically on every path that can replace the executable (previously
    this module accepted only the exact string ``"1"`` while the downloader
    accepted four spellings).
    """
    return str(os.getenv(ALLOW_UNSIGNED_ENV, "") or "").strip().lower() in {
        "1", "true", "yes", "on"}


def _allow_unsigned() -> bool:
    return allow_unsigned_update()


_WARNED_ENV_TRUST_ROOT = False


def _warn_env_trust_root_once() -> None:
    """Note (once per process) when the update trust root comes from the env.

    With ``VIRALCUTTER_UPDATE_PUBLIC_KEY`` + ``VIRALCUTTER_UPDATE_MANIFEST``
    three environment variables fully re-root the update channel, so any
    local script can silently redirect updates. A packaged build should pin
    the maintainer key; until then the operator deserves a visible hint.
    """
    global _WARNED_ENV_TRUST_ROOT
    if _WARNED_ENV_TRUST_ROOT:
        return
    if str(os.getenv(PUBLIC_KEY_ENV, "") or "").strip():
        overrode = " (overrides the build-pinned key)" if pinned_public_key_path() else ""
        print("note: update verification trusts the key named by {}{} — any "
              "local process can repoint it".format(PUBLIC_KEY_ENV, overrode),
              file=sys.stderr)
        _WARNED_ENV_TRUST_ROOT = True


def current_platform() -> str:
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


# ---------------------------------------------------------------------------
# Build / sign / verify
# ---------------------------------------------------------------------------

def build_manifest(version: str, assets: Dict[str, Dict[str, str]], *,
                   notes: str = "", published_at: Optional[str] = None) -> Dict[str, Any]:
    """Assemble a manifest. ``assets`` maps platform → {"url", "sha256"}."""
    if not str(version or "").strip():
        raise ManifestError("version is required")
    clean: Dict[str, Dict[str, str]] = {}
    for name, asset in (assets or {}).items():
        platform = str(name).strip().lower()
        if platform not in PLATFORMS:
            raise ManifestError("unknown platform {!r} (expected {})".format(
                name, "/".join(PLATFORMS)))
        if not isinstance(asset, dict):
            raise ManifestError("asset {} must be an object".format(platform))
        url = str(asset.get("url") or "").strip()
        sha = str(asset.get("sha256") or "").strip().lower()
        if not url:
            raise ManifestError("asset {} has no url".format(platform))
        if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha):
            raise ManifestError(
                "asset {} needs a 64-hex sha256 (got {!r})".format(platform, sha))
        clean[platform] = {"url": url, "sha256": sha}
    if not clean:
        raise ManifestError("a manifest needs at least one platform asset")
    return {
        "schema": SCHEMA,
        "version": str(version).strip(),
        "published_at": published_at or _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
        "notes": str(notes or "")[:1000],
        "assets": clean,
    }


def canonical_bytes(manifest: Dict[str, Any]) -> bytes:
    """Deterministic bytes that the signature covers (no ``signature`` field)."""
    payload = {key: value for key, value in manifest.items()
               if key != ASSET_SIGNATURE_KEY}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def sign_manifest(manifest: Dict[str, Any], *, key_path: str,
                  passphrase: Optional[str] = None) -> Dict[str, Any]:
    """Return a copy of *manifest* carrying an Ed25519 detached signature."""
    from scripts import sign_artifacts

    signature = sign_artifacts.sign_detached(
        canonical_bytes(manifest), key_path=key_path, passphrase=passphrase)
    signed = dict(manifest)
    signed[ASSET_SIGNATURE_KEY] = base64.b64encode(signature).decode("ascii")
    return signed


def verify_manifest(manifest: Dict[str, Any], *, public_key_path_override: Optional[str] = None,
                    allow_unsigned: Optional[bool] = None) -> Dict[str, Any]:
    """Validate shape + signature. Never raises for verification failures."""
    report: Dict[str, Any] = {
        "ok": False,
        "structure_ok": False,
        "signed": False,
        "signature_valid": False,
        "error": None,
    }
    if not isinstance(manifest, dict):
        report["error"] = "manifest is not a JSON object"
        return report
    try:
        rebuilt = build_manifest(
            manifest.get("version", ""),
            manifest.get("assets") or {},
            notes=manifest.get("notes", ""),
            published_at=manifest.get("published_at"))
    except ManifestError as exc:
        report["error"] = str(exc)
        return report
    report["structure_ok"] = True
    report["version"] = rebuilt["version"]
    report["assets"] = sorted(rebuilt["assets"])
    # The normalized copy is what the signature actually covers; callers
    # must consume THIS (via fetch_verified_manifest), never the raw dict,
    # so stray unauthenticated keys cannot ride along.
    report["manifest"] = rebuilt

    signature_b64 = str(manifest.get(ASSET_SIGNATURE_KEY) or "").strip()
    key_path = public_key_path_override or public_key_path()
    report["signed"] = bool(signature_b64)

    if not signature_b64:
        if allow_unsigned is None:
            allow_unsigned = _allow_unsigned()
        if allow_unsigned:
            report.update({"ok": True, "signature_valid": False,
                           "error": None, "warning": "unsigned manifest accepted via {}".format(
                               ALLOW_UNSIGNED_ENV)})
        else:
            report["error"] = ("manifest is not signed — set {} to a public key, "
                               "or opt in with {}=1".format(PUBLIC_KEY_ENV, ALLOW_UNSIGNED_ENV))
        return report

    if not key_path:
        report["error"] = ("manifest is signed but {} is not set, so it cannot "
                           "be verified".format(PUBLIC_KEY_ENV))
        return report
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except Exception:
        report["error"] = "signature is not valid base64"
        return report

    from scripts import sign_artifacts

    valid = sign_artifacts.verify_detached(
        canonical_bytes(rebuilt), signature, key_path=key_path)
    report["signature_valid"] = bool(valid)
    if not valid:
        report["error"] = "signature does not match the manifest (tampered or wrong key)"
        return report
    report["ok"] = True
    return report


def asset_for_platform(manifest: Dict[str, Any],
                       platform: Optional[str] = None) -> Optional[Dict[str, str]]:
    assets = (manifest or {}).get("assets") or {}
    if not isinstance(assets, dict):
        return None
    wanted = platform or current_platform()
    asset = assets.get(wanted)
    return asset if isinstance(asset, dict) else None


# ---------------------------------------------------------------------------
# Loading a manifest from a URL or a local path
# ---------------------------------------------------------------------------

# A manifest is a few KB of JSON; anything past this is treated as hostile.
MAX_MANIFEST_BYTES = 2 * 1024 * 1024


def load_manifest(source: str, *, timeout: int = 8, urlopen=None) -> Dict[str, Any]:
    """Read a manifest from ``http(s)://`` or a local path. Raises ManifestError."""
    text = str(source or "").strip()
    if not text:
        raise ManifestError("no manifest source configured")
    if text.lower().startswith(("http://", "https://")):
        opener = urlopen or urllib.request.urlopen
        request = urllib.request.Request(text, headers={"User-Agent": "OUSSAMA-update-manifest"})
        try:
            with opener(request, timeout=timeout) as response:
                try:
                    raw = response.read(MAX_MANIFEST_BYTES + 1)
                except TypeError:
                    raw = response.read()  # minimal test doubles; capped below
        except TypeError as exc:
            if urlopen is None:
                # the real urlopen raised TypeError — do NOT retry without a
                # timeout (that turns a bug into an unbounded hang)
                raise ManifestError("cannot fetch manifest: {}".format(exc)) from exc
            # minimal injected callables (timeout kwarg unsupported)
            raw = opener(text)
        except Exception as exc:
            raise ManifestError("cannot fetch manifest: {}".format(exc)) from exc
        if isinstance(raw, (bytes, bytearray)) and len(raw) > MAX_MANIFEST_BYTES:
            raise ManifestError("manifest is too large (>{} bytes)".format(MAX_MANIFEST_BYTES))
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    else:
        path = os.path.abspath(os.path.expanduser(text))
        if not os.path.isfile(path):
            raise ManifestError("manifest not found: {}".format(path))
        if os.path.getsize(path) > MAX_MANIFEST_BYTES:
            raise ManifestError("manifest is too large (>{} bytes)".format(MAX_MANIFEST_BYTES))
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    if len(text) > MAX_MANIFEST_BYTES * 2:
        raise ManifestError("manifest is too large (>{} bytes)".format(MAX_MANIFEST_BYTES))
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ManifestError("manifest is not valid JSON: {}".format(exc)) from exc
    if not isinstance(data, dict):
        raise ManifestError("manifest must be a JSON object")
    return data


def fetch_verified_manifest(*, source: Optional[str] = None, timeout: int = 8,
                            allow_unsigned: Optional[bool] = None) -> Dict[str, Any]:
    """Load + verify in one step. Raises ManifestError with a human reason.

    Returns the *normalized* manifest the signature was verified over (with
    the ``signature`` field re-attached), not the raw parsed JSON — only
    authenticated bytes are ever handed to the update logic.
    """
    _warn_env_trust_root_once()
    raw = load_manifest(source or (manifest_source() or ""), timeout=timeout)
    report = verify_manifest(raw, allow_unsigned=allow_unsigned)
    if not report.get("ok"):
        raise ManifestError(report.get("error") or "manifest verification failed")
    verified = dict(report["manifest"])
    signature = str(raw.get(ASSET_SIGNATURE_KEY) or "").strip()
    if signature:
        verified[ASSET_SIGNATURE_KEY] = signature
    return verified


def write_manifest(path: str, manifest: Dict[str, Any]) -> str:
    """Atomically write a manifest (LF, utf-8)."""
    target = os.path.abspath(os.path.expanduser(os.fspath(path)))
    folder = os.path.dirname(target) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".update_manifest_", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return target


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _sha256_file(path: str) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_asset(value: Optional[str], explicit_sha: Optional[str],
                   base_url: Optional[str]) -> Optional[Dict[str, str]]:
    if not value:
        return None
    text = str(value).strip()
    if text.lower().startswith(("http://", "https://")):
        if not explicit_sha:
            raise ManifestError("{}: a URL asset needs --sha256-… (the hash cannot "
                                "be computed locally)".format(text))
        return {"url": text, "sha256": str(explicit_sha).strip().lower()}
    path = os.path.abspath(os.path.expanduser(text))
    if not os.path.isfile(path):
        raise ManifestError("asset not found: {}".format(path))
    url = os.path.basename(path)
    if base_url:
        url = str(base_url).rstrip("/") + "/" + url
    return {"url": url, "sha256": _sha256_file(path)}


def _cmd_create(args: argparse.Namespace) -> int:
    assets: Dict[str, Dict[str, str]] = {}
    for platform in PLATFORMS:
        asset = _resolve_asset(getattr(args, platform, None),
                               getattr(args, "sha256_" + platform, None),
                               args.base_url)
        if asset:
            assets[platform] = asset
    manifest = build_manifest(args.version, assets, notes=args.notes,
                              published_at=args.published_at)
    if args.sign:
        manifest = sign_manifest(manifest, key_path=args.key,
                                 passphrase=args.passphrase)
    target = write_manifest(args.out or DEFAULT_NAME, manifest)
    report = verify_manifest(manifest)
    print("[manifest] {} (v{}, {} platform asset(s), {})".format(
        target, manifest["version"], len(manifest["assets"]),
        "signed" if report["signed"] else "UNSIGNED"))
    if not report["signed"]:
        print("[manifest] NOTE: unsigned manifests require {}=1 on the client".format(
            ALLOW_UNSIGNED_ENV))
        print("[manifest] sign with: --sign --key <private key from scripts.sign_artifacts>")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    raw = load_manifest(args.manifest)
    report = verify_manifest(raw, public_key_path_override=args.key,
                             allow_unsigned=args.allow_unsigned)
    if report.get("error"):
        print("[manifest] {}".format(report["error"]), file=sys.stderr)
    if report.get("ok"):
        print("[manifest] OK — v{} ({}), signature {}".format(
            report.get("version"), ", ".join(report.get("assets") or []),
            "valid" if report["signature_valid"] else "not required"))
        return 0
    return 1


def _cmd_show(args: argparse.Namespace) -> int:
    raw = load_manifest(args.manifest)
    report = verify_manifest(raw, allow_unsigned=True)
    print(json.dumps({"manifest": raw, "verification": report},
                     ensure_ascii=False, indent=2))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build, sign and verify signed update manifests (channel: manifest)")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="build a manifest for release assets")
    create.add_argument("--version", required=True)
    create.add_argument("--windows", default=None, help="local path or URL of the Windows build")
    create.add_argument("--linux", default=None)
    create.add_argument("--macos", default=None)
    create.add_argument("--sha256-windows", default=None, dest="sha256_windows")
    create.add_argument("--sha256-linux", default=None, dest="sha256_linux")
    create.add_argument("--sha256-macos", default=None, dest="sha256_macos")
    create.add_argument("--base-url", default=None,
                        help="prefix the local file names to form the download URL")
    create.add_argument("--notes", default="")
    create.add_argument("--published-at", default=None)
    create.add_argument("--out", default=None)
    create.add_argument("--sign", action="store_true")
    create.add_argument("--key", default=os.path.join("keys", "maintainer-signing.key"))
    create.add_argument("--passphrase", default=None)
    create.set_defaults(func=_cmd_create)

    verify = sub.add_parser("verify", help="verify a manifest (URL or path)")
    verify.add_argument("manifest")
    verify.add_argument("--key", default=None,
                        help="public key path (default: ${})".format(PUBLIC_KEY_ENV))
    verify.add_argument("--allow-unsigned", action="store_true")
    verify.set_defaults(func=_cmd_verify)

    show = sub.add_parser("show", help="print a manifest with its verification report")
    show.add_argument("manifest")
    show.set_defaults(func=_cmd_show)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ManifestError as exc:
        print("[manifest] error: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
