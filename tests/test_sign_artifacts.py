import json
import os

import pytest

pytest.importorskip("cryptography")

from scripts import sign_artifacts  # noqa: E402


def _artifact(folder, name, content=b"artifact-bytes"):
    path = folder / name
    path.write_bytes(content)
    return str(path)


@pytest.fixture()
def keypair(tmp_path):
    key_dir = tmp_path / "keys"
    result = sign_artifacts.generate_keypair(str(key_dir))
    return result


def test_generate_keypair_writes_both_keys(tmp_path):
    result = sign_artifacts.generate_keypair(str(tmp_path / "keys"))
    assert os.path.isfile(result["private_key"])
    assert os.path.isfile(result["public_key"])
    assert result["encrypted"] is False
    if os.name != "nt":
        mode = os.stat(result["private_key"]).st_mode & 0o777
        assert mode == 0o600


def test_generate_keypair_refuses_overwrite(tmp_path):
    sign_artifacts.generate_keypair(str(tmp_path))
    with pytest.raises(sign_artifacts.SigningError, match="refusing to overwrite"):
        sign_artifacts.generate_keypair(str(tmp_path))
    # ... unless explicitly forced
    sign_artifacts.generate_keypair(str(tmp_path), overwrite=True)


def test_sign_and_verify_roundtrip(tmp_path, keypair):
    artifact = _artifact(tmp_path, "OUSSAMA-Cutter.exe")
    result = sign_artifacts.sign_files([artifact], key_path=keypair["private_key"])
    assert os.path.isfile(result["sums"])
    assert os.path.isfile(result["signature"])

    with open(result["sums"], "r", encoding="utf-8") as handle:
        line = handle.read().strip()
    digest, name = line.split()
    assert name == "OUSSAMA-Cutter.exe"
    assert len(digest) == 64

    report = sign_artifacts.verify_sums(result["sums"], key_path=keypair["public_key"])
    assert report["ok"] is True
    assert report["signature_valid"] is True
    assert report["files"] == [
        {"name": "OUSSAMA-Cutter.exe", "status": "ok", "sha256": digest}]


def test_verify_detects_tampered_artifact(tmp_path, keypair):
    artifact = _artifact(tmp_path, "clip.exe", content=b"original")
    result = sign_artifacts.sign_files([artifact], key_path=keypair["private_key"])
    _artifact(tmp_path, "clip.exe", content=b"tampered")

    report = sign_artifacts.verify_sums(result["sums"], key_path=keypair["public_key"])
    assert report["ok"] is False
    assert report["signature_valid"] is True  # manifest itself is untouched
    assert report["files"][0]["status"] == "hash_mismatch"


def test_verify_detects_tampered_manifest(tmp_path, keypair):
    artifact = _artifact(tmp_path, "clip.exe")
    result = sign_artifacts.sign_files([artifact], key_path=keypair["private_key"])
    with open(result["sums"], "a", encoding="utf-8") as handle:
        handle.write("0" * 64 + "  evil.exe\n")

    report = sign_artifacts.verify_sums(result["sums"], key_path=keypair["public_key"])
    assert report["ok"] is False
    assert report["signature_valid"] is False


def test_verify_detects_missing_artifact(tmp_path, keypair):
    artifact = _artifact(tmp_path, "clip.exe")
    result = sign_artifacts.sign_files([artifact], key_path=keypair["private_key"])
    os.remove(artifact)

    report = sign_artifacts.verify_sums(result["sums"], key_path=keypair["public_key"])
    assert report["ok"] is False
    assert report["files"][0]["status"] == "missing"


def test_verify_rejects_wrong_public_key(tmp_path, keypair):
    other = sign_artifacts.generate_keypair(str(tmp_path / "other"))
    artifact = _artifact(tmp_path, "clip.exe")
    result = sign_artifacts.sign_files([artifact], key_path=keypair["private_key"])

    report = sign_artifacts.verify_sums(result["sums"], key_path=other["public_key"])
    assert report["ok"] is False
    assert report["signature_valid"] is False


def test_build_sums_rejects_duplicate_basenames(tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    paths = [_artifact(first, "same.exe", b"1"), _artifact(second, "same.exe", b"2")]
    with pytest.raises(sign_artifacts.SigningError, match="duplicate artifact basename"):
        sign_artifacts.build_sums(paths)


def test_build_sums_rejects_missing_files(tmp_path):
    with pytest.raises(sign_artifacts.SigningError, match="does not exist"):
        sign_artifacts.build_sums([str(tmp_path / "ghost.exe")])


def test_encrypted_keypair_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv(sign_artifacts.PASSPHRASE_ENV, "s3cret")
    result = sign_artifacts.generate_keypair(str(tmp_path / "keys"))
    assert result["encrypted"] is True

    artifact = _artifact(tmp_path, "clip.exe")
    signed = sign_artifacts.sign_files([artifact], key_path=result["private_key"])
    report = sign_artifacts.verify_sums(signed["sums"], key_path=result["public_key"])
    assert report["ok"] is True


def test_encrypted_key_requires_passphrase(tmp_path, monkeypatch):
    monkeypatch.setenv(sign_artifacts.PASSPHRASE_ENV, "s3cret")
    result = sign_artifacts.generate_keypair(str(tmp_path / "keys"))
    monkeypatch.delenv(sign_artifacts.PASSPHRASE_ENV)

    artifact = _artifact(tmp_path, "clip.exe")
    with pytest.raises(sign_artifacts.SigningError, match="passphrase"):
        sign_artifacts.sign_files([artifact], key_path=result["private_key"])


def test_cli_full_flow(tmp_path, capsys):
    artifact = _artifact(tmp_path, "OUSSAMA-Cutter.exe")
    key_dir = str(tmp_path / "keys")

    assert sign_artifacts.main(["init", "--key-dir", key_dir]) == 0
    assert sign_artifacts.main(["init", "--key-dir", key_dir]) == 2  # no --force

    private_key = os.path.join(key_dir, sign_artifacts.PRIVATE_KEY_NAME)
    public_key = os.path.join(key_dir, sign_artifacts.PUBLIC_KEY_NAME)
    assert sign_artifacts.main(["sign", artifact, "--key", private_key]) == 0

    sums = os.path.join(tmp_path, sign_artifacts.SUMS_NAME)
    assert sign_artifacts.main(["verify", sums, "--key", public_key]) == 0
    out = capsys.readouterr().out
    assert "OK — signature and all hashes match" in out

    _artifact(tmp_path, "OUSSAMA-Cutter.exe", b"tampered")
    assert sign_artifacts.main(["verify", sums, "--key", public_key]) == 1


def test_cli_sign_reports_errors_cleanly(tmp_path, capsys):
    key_dir = str(tmp_path / "keys")
    assert sign_artifacts.main(["init", "--key-dir", key_dir]) == 0
    private_key = os.path.join(key_dir, sign_artifacts.PRIVATE_KEY_NAME)
    assert sign_artifacts.main(
        ["sign", str(tmp_path / "ghost.exe"), "--key", private_key]) == 2
    assert "error" in capsys.readouterr().err


def test_manifest_matches_auto_updater_parsing(tmp_path, keypair):
    """SHA256SUMS lines must parse exactly like auto_updater._fetch_checksums."""
    artifact = _artifact(tmp_path, "OUSSAMA-Cutter.exe")
    result = sign_artifacts.sign_files([artifact], key_path=keypair["private_key"])
    parsed = {}
    with open(result["sums"], "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2 and len(parts[0]) == 64:
                parsed[parts[1].lstrip("*")] = parts[0].lower()
    assert "OUSSAMA-Cutter.exe" in parsed
    assert len(parsed["OUSSAMA-Cutter.exe"]) == 64


def test_sums_file_is_valid_json_free_text(tmp_path, keypair):
    # sanity: the manifest is plain text, one line per artifact, sorted
    one = _artifact(tmp_path, "b.exe", b"1")
    two = _artifact(tmp_path, "a.exe", b"2")
    result = sign_artifacts.sign_files([one, two], key_path=keypair["private_key"])
    with open(result["sums"], "r", encoding="utf-8") as handle:
        names = [line.split()[1] for line in handle.read().splitlines()]
    assert names == ["a.exe", "b.exe"]
    with pytest.raises(json.JSONDecodeError):
        json.loads("".join(names))
