# -*- coding: utf-8 -*-
"""Direct tests for scripts/secure_config.py (pure/deterministic behaviour).

The CLI surface is covered by tests/test_secure_config_cli.py; these tests
cover the storage and resolution logic: key cleaning, encrypt/decrypt
round-trips, the env > encrypted-store > legacy priority order, fail-closed
behaviour, and the merged config dict. No real keys are used — every value
here is a synthetic test string — and no network is involved.
"""

import json
import os
import stat

import pytest

from scripts import secure_config
from scripts.secure_config import (
    _clean_keys,
    _decrypt_blob,
    _encrypt_blob,
    _masked_key,
    get_key,
    get_keys,
    load_api_config,
    resolve_api_key,
    resolve_api_keys,
    set_key,
    set_keys,
)

PASSPHRASE = "test-passphrase-for-unit-tests-only"
KEY_A = "AIzaSyTestKeyAAAAAAAAAAAAAAA0001"
KEY_B = "AIzaSyTestKeyBBBBBBBBBBBBBBB0002"
KEY_C = "AIzaSyTestKeyCCCCCCCCCCCCCCC0003"
KEY_D = "AIzaSyTestKeyDDDDDDDDDDDDDDD0004"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Resolution must not see keys from the ambient environment."""
    for name in (
        "VIRALCUTTER_GEMINI_KEY",
        "VIRALCUTTER_GEMINI_KEYS",
        "GEMINI_API_KEY",
        "VIRALCUTTER_CONFIG_PASSPHRASE",
    ):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# _clean_keys
# ---------------------------------------------------------------------------


def test_clean_keys_dedupes_strips_and_caps_at_three():
    assert _clean_keys([KEY_A, "  " + KEY_A + " ", KEY_B, "", None, KEY_C, KEY_D]) == [
        KEY_A,
        KEY_B,
        KEY_C,
    ]


def test_clean_keys_accepts_a_single_string():
    assert _clean_keys(KEY_A) == [KEY_A]


def test_clean_keys_empty_inputs():
    assert _clean_keys([]) == []
    assert _clean_keys(None) == []
    assert _clean_keys("") == []


# ---------------------------------------------------------------------------
# _encrypt_blob / _decrypt_blob
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_round_trip():
    blob = _encrypt_blob(KEY_A.encode("utf-8"), PASSPHRASE)
    payload = json.loads(blob)
    assert payload["v"] == 2
    assert _decrypt_blob(blob, PASSPHRASE) == KEY_A.encode("utf-8")


def test_decrypt_with_wrong_passphrase_fails():
    from cryptography.fernet import InvalidToken

    blob = _encrypt_blob(KEY_A.encode("utf-8"), PASSPHRASE)
    with pytest.raises(InvalidToken):
        _decrypt_blob(blob, "wrong-passphrase")


def test_decrypt_rejects_insecure_legacy_format():
    payload = json.dumps({"v": 1, "salt": "AA==", "token": "AAAA"})
    with pytest.raises(RuntimeError, match="insecure legacy"):
        _decrypt_blob(payload, PASSPHRASE)


def test_decrypt_rejects_malformed_payload():
    with pytest.raises(ValueError):  # json.JSONDecodeError from json.loads
        _decrypt_blob("not-json", PASSPHRASE)


def test_encrypt_fails_closed_without_cryptography(monkeypatch):
    monkeypatch.setattr(secure_config, "HAS_CRYPTOGRAPHY", False)
    with pytest.raises(RuntimeError, match="cryptography is required"):
        _encrypt_blob(b"secret", PASSPHRASE)


# ---------------------------------------------------------------------------
# set_keys / get_keys round-trip (tmp dir as base_dir)
# ---------------------------------------------------------------------------


def test_set_and_get_keys_round_trip(tmp_path):
    path = set_keys([KEY_A, KEY_B], passphrase=PASSPHRASE, base_dir=str(tmp_path))
    assert os.path.isfile(path)
    assert get_keys(passphrase=PASSPHRASE, base_dir=str(tmp_path)) == [KEY_A, KEY_B]
    assert get_key(passphrase=PASSPHRASE, base_dir=str(tmp_path)) == KEY_A


def test_secure_file_is_created_with_restricted_permissions(tmp_path):
    path = set_keys([KEY_A], passphrase=PASSPHRASE, base_dir=str(tmp_path))
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_set_keys_caps_at_three(tmp_path):
    set_keys(
        [KEY_A, KEY_B, KEY_C, KEY_D], passphrase=PASSPHRASE, base_dir=str(tmp_path)
    )
    assert get_keys(passphrase=PASSPHRASE, base_dir=str(tmp_path)) == [
        KEY_A,
        KEY_B,
        KEY_C,
    ]


def test_set_keys_requires_a_passphrase(tmp_path):
    with pytest.raises(ValueError, match="passphrase is required"):
        set_keys([KEY_A], base_dir=str(tmp_path))


def test_set_key_single_wrapper_round_trip(tmp_path):
    set_key(KEY_A, passphrase=PASSPHRASE, base_dir=str(tmp_path))
    assert get_key(passphrase=PASSPHRASE, base_dir=str(tmp_path)) == KEY_A


def test_get_keys_without_passphrase_returns_empty(tmp_path):
    set_keys([KEY_A], passphrase=PASSPHRASE, base_dir=str(tmp_path))
    assert get_keys(base_dir=str(tmp_path)) == []


def test_get_keys_with_wrong_passphrase_returns_empty(tmp_path):
    set_keys([KEY_A], passphrase=PASSPHRASE, base_dir=str(tmp_path))
    assert get_keys(passphrase="wrong-passphrase", base_dir=str(tmp_path)) == []


def test_get_keys_reads_legacy_single_key_format(tmp_path):
    """The encrypted store's legacy single-key shape (api_key, not api_keys)."""
    blob = _encrypt_blob(KEY_A.encode("utf-8"), PASSPHRASE)
    data = {"gemini": {"api_key": blob}}
    (tmp_path / "api_config.secure.json").write_text(json.dumps(data), encoding="utf-8")
    assert get_keys(passphrase=PASSPHRASE, base_dir=str(tmp_path)) == [KEY_A]


def test_get_keys_missing_file_returns_empty(tmp_path):
    assert get_keys(passphrase=PASSPHRASE, base_dir=str(tmp_path)) == []


def test_passphrase_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_CONFIG_PASSPHRASE", PASSPHRASE)
    set_keys([KEY_A], base_dir=str(tmp_path))
    assert get_keys(base_dir=str(tmp_path)) == [KEY_A]


# ---------------------------------------------------------------------------
# resolve_api_keys priority: env > encrypted store > legacy
# ---------------------------------------------------------------------------


def test_resolve_prefers_single_env_key(tmp_path, monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_GEMINI_KEY", KEY_B)
    set_keys([KEY_A], passphrase=PASSPHRASE, base_dir=str(tmp_path))
    assert resolve_api_keys(base_dir=str(tmp_path)) == [KEY_B]


def test_resolve_prefers_json_env_key_list(tmp_path, monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_GEMINI_KEYS", json.dumps([KEY_B, KEY_C]))
    assert resolve_api_keys(base_dir=str(tmp_path)) == [KEY_B, KEY_C]


def test_resolve_parses_delimited_env_key_list(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_GEMINI_KEYS", "{},;{}".format(KEY_A, KEY_B))
    assert resolve_api_keys() == [KEY_A, KEY_B]


def test_resolve_falls_back_to_gemini_api_key_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", KEY_C)
    assert resolve_api_key() == KEY_C


def test_resolve_uses_encrypted_store_when_no_env(tmp_path, monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_CONFIG_PASSPHRASE", PASSPHRASE)
    set_keys([KEY_A], passphrase=PASSPHRASE, base_dir=str(tmp_path))
    assert resolve_api_keys(base_dir=str(tmp_path)) == [KEY_A]


def test_resolve_fails_closed_when_secure_store_is_undecryptable(tmp_path, monkeypatch):
    """A present-but-unreadable secure store must NOT fall back to plaintext."""
    set_keys([KEY_A], passphrase=PASSPHRASE, base_dir=str(tmp_path))
    (tmp_path / "api_config.json").write_text(
        json.dumps({"gemini": {"api_key": KEY_B}}), encoding="utf-8"
    )
    # No passphrase in env: the secure store exists but cannot be decrypted.
    assert resolve_api_keys(base_dir=str(tmp_path)) == []


def test_resolve_reads_legacy_plaintext_as_last_resort(tmp_path):
    (tmp_path / "api_config.json").write_text(
        json.dumps({"gemini": {"api_key": KEY_B}}), encoding="utf-8"
    )
    assert resolve_api_keys(base_dir=str(tmp_path), warn=False) == [KEY_B]


def test_resolve_ignores_legacy_placeholder_keys(tmp_path):
    (tmp_path / "api_config.json").write_text(
        json.dumps({"gemini": {"api_keys": ["YOUR_KEY_HERE", "SUA_KEY_AQUI"]}}),
        encoding="utf-8",
    )
    assert resolve_api_keys(base_dir=str(tmp_path), warn=False) == []


def test_resolve_with_malformed_legacy_file_returns_empty(tmp_path):
    (tmp_path / "api_config.json").write_text("{not json", encoding="utf-8")
    assert resolve_api_keys(base_dir=str(tmp_path), warn=False) == []


def test_resolve_with_no_source_returns_empty(tmp_path):
    assert resolve_api_keys(base_dir=str(tmp_path)) == []
    assert resolve_api_key(base_dir=str(tmp_path)) is None


# ---------------------------------------------------------------------------
# load_api_config
# ---------------------------------------------------------------------------


def test_load_api_config_defaults_without_files(tmp_path, monkeypatch):
    config = load_api_config(base_dir=str(tmp_path))
    assert config["selected_api"] == "auto"
    assert config["gemini"]["api_key"] == ""
    assert config["gemini"]["api_keys"] == []
    assert config["gemini"]["model"]  # default model resolved
    assert config["g4f"]["model"] == "gpt-4o-mini"


def test_load_api_config_merges_legacy_file(tmp_path, monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_GEMINI_KEY", KEY_A)
    (tmp_path / "api_config.json").write_text(
        json.dumps(
            {
                "selected_api": "gemini",
                "gemini": {"chunk_size": 12345},
                "g4f": {"model": "gpt-4o"},
            }
        ),
        encoding="utf-8",
    )
    config = load_api_config(base_dir=str(tmp_path))
    assert config["selected_api"] == "gemini"
    assert config["gemini"]["chunk_size"] == 12345
    assert config["g4f"]["model"] == "gpt-4o"
    assert config["gemini"]["api_key"] == KEY_A
    assert config["gemini"]["api_keys"] == [KEY_A]


def test_load_api_config_rejects_unsupported_model(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VIRALCUTTER_GEMINI_KEY", KEY_A)
    (tmp_path / "api_config.json").write_text(
        json.dumps({"gemini": {"model": "not-a-real-model"}}), encoding="utf-8"
    )
    config = load_api_config(base_dir=str(tmp_path))
    assert config["gemini"]["model"] != "not-a-real-model"
    assert "Unsupported Gemini model" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _masked_key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("", "(no key configured)"),
        (None, "(no key configured)"),
        ("short", "****"),
        ("12345678", "****"),
        (KEY_A, "AIza…0001"),
    ],
)
def test_masked_key(key, expected):
    assert _masked_key(key) == expected
