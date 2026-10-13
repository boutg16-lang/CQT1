"""Offline Ed25519 signing for OUSSAMA Cutter release artifacts.

The gap analysis (``docs/PRODUCT_GAP_ANALYSIS_AR.md`` — «التوزيع والاعتمادات»)
lists *signed artifacts* as a remaining hardening item, and
:mod:`scripts.auto_updater` already refuses release binaries that arrive
without a checksum manifest. This module gives maintainers a fully offline
signing flow today — the only dependency is ``cryptography``, which is
already an optional project requirement.

Maintainer flow (private key never leaves the maintainer machine)::

    python -m scripts.sign_artifacts init --key-dir keys
    python -m scripts.sign_artifacts sign dist/OUSSAMA-Cutter.exe --key keys/maintainer-signing.key
    # → writes dist/SHA256SUMS and dist/SHA256SUMS.sig next to the binary

Verification (users now, CI later)::

    python -m scripts.sign_artifacts verify dist/SHA256SUMS --key keys/maintainer-signing.pub

The signature covers the exact bytes of the ``SHA256SUMS`` file (GNU
``sha256sum`` format — the same manifest ``auto_updater`` parses), so the
hashes and their signature always travel together and cannot be mixed
across releases. Set ``OUSSAMA_SIGNING_PASSPHRASE`` to use an encrypted
private key; without it the key is stored unencrypted with mode 0600.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import stat
import sys
from typing import Any, Dict, List, Optional, Sequence

PRIVATE_KEY_NAME = "maintainer-signing.key"
PUBLIC_KEY_NAME = "maintainer-signing.pub"
SUMS_NAME = "SHA256SUMS"
SIG_SUFFIX = ".sig"
SIG_FORMAT = "oussama-ed25519-v1"
PASSPHRASE_ENV = "OUSSAMA_SIGNING_PASSPHRASE"
_CHUNK = 1 << 16


class SigningError(RuntimeError):
    """Raised for any key, manifest, or verification failure."""


def _crypto():
    """Import cryptography lazily with a friendly error for minimal installs."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise SigningError(
            "the 'cryptography' package is required for signing: "
            "pip install cryptography") from exc
    return serialization, Ed25519PrivateKey, Ed25519PublicKey


def _passphrase(explicit: Optional[str]) -> Optional[bytes]:
    value = explicit if explicit is not None else os.environ.get(PASSPHRASE_ENV)
    value = (value or "").strip()
    return value.encode("utf-8") if value else None


def generate_keypair(
    key_dir: str,
    *,
    passphrase: Optional[str] = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Generate an Ed25519 keypair in *key_dir*; returns the written paths."""
    serialization, Ed25519PrivateKey, _ = _crypto()
    folder = os.path.abspath(os.path.expanduser(os.fspath(key_dir)))
    os.makedirs(folder, exist_ok=True)
    private_path = os.path.join(folder, PRIVATE_KEY_NAME)
    public_path = os.path.join(folder, PUBLIC_KEY_NAME)
    for path in (private_path, public_path):
        if os.path.exists(path) and not overwrite:
            raise SigningError(
                "refusing to overwrite existing key {} — pass overwrite=True "
                "(CLI: --force) if you really want a new identity".format(path))

    secret = _passphrase(passphrase)
    private_key = Ed25519PrivateKey.generate()
    encryption = (serialization.BestAvailableEncryption(secret) if secret
                  else serialization.NoEncryption())
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        encryption)
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo)

    with open(private_path, "wb") as handle:
        handle.write(private_pem)
    try:  # best effort outside POSIX (Windows ACLs differ)
        os.chmod(private_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    with open(public_path, "wb") as handle:
        handle.write(public_pem)
    return {
        "private_key": private_path,
        "public_key": public_path,
        "encrypted": bool(secret),
    }


def _load_private_key(path: str, *, passphrase: Optional[str] = None):
    serialization, _, _ = _crypto()
    try:
        with open(os.path.abspath(os.path.expanduser(os.fspath(path))), "rb") as handle:
            pem = handle.read()
    except OSError as exc:
        raise SigningError("cannot read private key {}: {}".format(path, exc)) from exc
    try:
        return serialization.load_pem_private_key(pem, password=_passphrase(passphrase))
    except TypeError as exc:
        raise SigningError(
            "private key is passphrase-protected — set {} or pass --passphrase".format(
                PASSPHRASE_ENV)) from exc
    except ValueError as exc:
        raise SigningError(
            "cannot load private key {} (wrong passphrase or corrupt PEM)".format(path)) from exc


def _load_public_key(path: str):
    serialization, _, _ = _crypto()
    try:
        with open(os.path.abspath(os.path.expanduser(os.fspath(path))), "rb") as handle:
            pem = handle.read()
    except OSError as exc:
        raise SigningError("cannot read public key {}: {}".format(path, exc)) from exc
    try:
        return serialization.load_pem_public_key(pem)
    except ValueError as exc:
        raise SigningError("cannot load public key {} (corrupt PEM)".format(path)) from exc


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def build_sums(files: Sequence[str]) -> str:
    """Build GNU sha256sum-formatted lines (``<hash>  <basename>``).

    Basenames must be unique — the manifest travels next to the artifacts,
    so names are always relative to its own folder.
    """
    names: Dict[str, str] = {}
    for path in files:
        name = os.path.basename(os.fspath(path))
        if name in names:
            raise SigningError(
                "duplicate artifact basename {} — the manifest lives next to "
                "the artifacts, so names must be unique".format(name))
        names[name] = os.fspath(path)
    lines = []
    for name in sorted(names):
        if not os.path.isfile(names[name]):
            raise SigningError("artifact does not exist: {}".format(names[name]))
        lines.append("{}  {}".format(_sha256_file(names[name]), name))
    return "\n".join(lines) + "\n"


def sign_files(
    files: Sequence[str],
    *,
    key_path: str,
    passphrase: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Write ``SHA256SUMS`` + ``SHA256SUMS.sig`` for *files*; returns paths."""
    if not files:
        raise SigningError("no artifacts given to sign")
    private_key = _load_private_key(key_path, passphrase=passphrase)
    sums_text = build_sums(files)
    folder = os.path.abspath(os.path.expanduser(os.fspath(
        output_dir or os.path.dirname(os.path.abspath(os.fspath(files[0]))) or ".")))
    os.makedirs(folder, exist_ok=True)
    sums_path = os.path.join(folder, SUMS_NAME)
    sig_path = sums_path + SIG_SUFFIX
    signature = private_key.sign(sums_text.encode("utf-8"))
    with open(sums_path, "w", encoding="utf-8") as handle:
        handle.write(sums_text)
    with open(sig_path, "w", encoding="utf-8") as handle:
        handle.write("{}:{}\n".format(SIG_FORMAT, base64.b64encode(signature).decode("ascii")))
    return {"sums": sums_path, "signature": sig_path, "files": len(files)}


def sign_detached(data: bytes, *, key_path: str,
                  passphrase: Optional[str] = None) -> bytes:
    """Sign raw bytes with the maintainer key; returns the raw signature.

    Used for artifacts that are not files (e.g. the canonical JSON of an
    update manifest in ``scripts/update_manifest.py``).
    """
    private_key = _load_private_key(key_path, passphrase=passphrase)
    return private_key.sign(bytes(data))


def verify_detached(data: bytes, signature: bytes, *, key_path: str) -> bool:
    """Verify a detached signature over raw bytes. Never raises."""
    try:
        public_key = _load_public_key(key_path)
        public_key.verify(bytes(signature), bytes(data))
        return True
    except Exception:
        return False


def _read_signature(sig_path: str) -> bytes:
    try:
        with open(sig_path, "r", encoding="utf-8") as handle:
            content = handle.read().strip()
    except OSError as exc:
        raise SigningError("cannot read signature {}: {}".format(sig_path, exc)) from exc
    prefix = SIG_FORMAT + ":"
    if not content.startswith(prefix):
        raise SigningError(
            "unsupported signature format in {} (expected '{}:<base64>')".format(
                sig_path, SIG_FORMAT))
    try:
        return base64.b64decode(content[len(prefix):], validate=True)
    except ValueError as exc:
        raise SigningError("corrupt base64 signature in {}".format(sig_path)) from exc


def verify_sums(
    sums_path: str,
    *,
    key_path: str,
    base_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Verify the signature on *sums_path*, then every listed artifact hash.

    Returns a structured report; never raises for verification failures
    (they are data: ``ok`` is False and each file carries a status).
    """
    public_key = _load_public_key(key_path)
    sums_path = os.path.abspath(os.path.expanduser(os.fspath(sums_path)))
    try:
        with open(sums_path, "r", encoding="utf-8") as handle:
            sums_text = handle.read()
    except OSError as exc:
        raise SigningError("cannot read manifest {}: {}".format(sums_path, exc)) from exc

    signature_valid = True
    signature_error = None
    try:
        signature = _read_signature(sums_path + SIG_SUFFIX)
        public_key.verify(signature, sums_text.encode("utf-8"))
    except SigningError as exc:
        signature_valid = False
        signature_error = str(exc)
    except Exception:  # InvalidSignature — do not import the class just for this
        signature_valid = False
        signature_error = "signature does not match the manifest (tampered or wrong key)"

    folder = os.path.abspath(os.path.expanduser(os.fspath(
        base_dir or os.path.dirname(sums_path))))
    entries: List[Dict[str, Any]] = []
    for line in sums_text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2 or len(parts[0]) != 64:
            entries.append({"name": line[:40], "status": "unparsable_line"})
            continue
        expected, name = parts[0].lower(), parts[1].lstrip("*")
        artifact = os.path.join(folder, name)
        if not os.path.isfile(artifact):
            entries.append({"name": name, "status": "missing"})
            continue
        actual = _sha256_file(artifact)
        entries.append({
            "name": name,
            "status": "ok" if actual == expected else "hash_mismatch",
            "sha256": actual,
        })
    files_ok = bool(entries) and all(item["status"] == "ok" for item in entries)
    return {
        "ok": signature_valid and files_ok,
        "signature_valid": signature_valid,
        "signature_error": signature_error,
        "manifest": sums_path,
        "base_dir": folder,
        "files": entries,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sign and verify OUSSAMA Cutter release artifacts (Ed25519, offline).")
    commands = parser.add_subparsers(dest="command", required=True)

    init_cmd = commands.add_parser("init", help="generate a maintainer keypair")
    init_cmd.add_argument("--key-dir", default="keys", help="key folder (default: %(default)s)")
    init_cmd.add_argument("--force", action="store_true", help="overwrite existing keys")

    sign_cmd = commands.add_parser("sign", help="write SHA256SUMS + signature for artifacts")
    sign_cmd.add_argument("files", nargs="+", help="artifact files to sign")
    sign_cmd.add_argument("--key", default=os.path.join("keys", PRIVATE_KEY_NAME),
                          help="private key path (default: %(default)s)")
    sign_cmd.add_argument("--passphrase", default=None,
                          help="key passphrase (default: ${} or unencrypted)".format(PASSPHRASE_ENV))
    sign_cmd.add_argument("--output-dir", default=None,
                          help="where to write the manifest (default: next to the first artifact)")

    verify_cmd = commands.add_parser("verify", help="verify a SHA256SUMS manifest and its files")
    verify_cmd.add_argument("sums", nargs="?", default=SUMS_NAME,
                            help="manifest path (default: ./%(default)s)")
    verify_cmd.add_argument("--key", default=os.path.join("keys", PUBLIC_KEY_NAME),
                            help="public key path (default: %(default)s)")
    verify_cmd.add_argument("--base-dir", default=None,
                            help="artifact folder (default: the manifest's own folder)")

    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = generate_keypair(args.key_dir, overwrite=args.force)
            print("[sign] keypair ready: {} (private, keep secret) / {}".format(
                result["private_key"], result["public_key"]))
            if not result["encrypted"]:
                print("[sign] NOTE: private key is unencrypted (mode 0600) — set {} "
                      "at init time for passphrase protection".format(PASSPHRASE_ENV))
            return 0
        if args.command == "sign":
            result = sign_files(
                args.files, key_path=args.key,
                passphrase=args.passphrase, output_dir=args.output_dir)
            print("[sign] wrote {} + {} ({} artifact(s))".format(
                result["sums"], result["signature"], result["files"]))
            return 0
        report = verify_sums(args.sums, key_path=args.key, base_dir=args.base_dir)
        for entry in report["files"]:
            print("[verify] {:<40} {}".format(entry["name"], entry["status"]))
        if not report["signature_valid"]:
            print("[verify] SIGNATURE INVALID: {}".format(report["signature_error"]))
        print("[verify] {}".format("OK — signature and all hashes match" if report["ok"]
                                   else "FAILED — do not ship these artifacts"))
        return 0 if report["ok"] else 1
    except SigningError as exc:
        print("[sign] error: {}".format(exc), file=sys.stderr)
        return 2


__all__ = [
    "PASSPHRASE_ENV", "PRIVATE_KEY_NAME", "PUBLIC_KEY_NAME", "SIG_FORMAT",
    "SIG_SUFFIX", "SUMS_NAME", "SigningError", "build_sums", "generate_keypair",
    "main", "sign_files", "verify_sums",
]


if __name__ == "__main__":
    raise SystemExit(main())
