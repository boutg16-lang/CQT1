# -*- coding: utf-8 -*-
"""Tests for SHA-256 verification of safety-pack downloads.

Every pack is verified against a ``.sha256`` sidecar published next to it
(see scripts/safety_updater.py) before it is parsed or installed. All
downloads here are ``file://`` URLs — no network, no real downloads.
"""

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import safety_updater as su


def _term(term, severity="high"):
    return {"term": term, "lang": "test", "severity": severity, "category": "test"}


def _pack(version, terms):
    return {"version": version, "updated": "2026-11-19", "terms": terms}


def _write_pack(
    tmp_path, pack, name="pack.json", sidecar=True, digest=None, sidecar_text=None
):
    """Write pack bytes + optional sidecar; return the pack ``file://`` URL.

    ``sidecar=True`` writes a sidecar with the correct digest of the bytes
    written; ``digest=<hex>`` writes a sidecar with that (wrong) digest;
    ``sidecar_text=<str>`` writes exactly that sidecar content;
    ``sidecar=False`` writes no sidecar at all.
    """
    p = tmp_path / name
    raw = json.dumps(pack, ensure_ascii=False).encode("utf-8")
    p.write_bytes(raw)
    if sidecar_text is not None:
        (tmp_path / (name + ".sha256")).write_text(sidecar_text, encoding="utf-8")
    elif sidecar:
        d = digest or hashlib.sha256(raw).hexdigest()
        (tmp_path / (name + ".sha256")).write_text(
            "{}  {}\n".format(d, name), encoding="utf-8"
        )
    return p.as_uri()


def _cache(tmp_path, version=0, terms=None):
    base = tmp_path / "base"
    base.mkdir(exist_ok=True)
    if version:
        pack = _pack(version, terms or [_term("cache-term")])
        (base / su.CACHE_FILENAME).write_text(
            json.dumps(pack, ensure_ascii=False), encoding="utf-8"
        )
    return str(base)


# ---------------------------------------------------------------------------
# Sidecar parsing + digest comparison
# ---------------------------------------------------------------------------


class TestParseSidecar:
    def test_sha256sum_format(self):
        digest = "a" * 64
        assert su.parse_sidecar("{}  safety_blocklist.json\n".format(digest)) == digest

    def test_bare_hex_normalized_to_lowercase(self):
        assert su.parse_sidecar("A" * 64) == "a" * 64

    def test_rejects_empty(self):
        assert su.parse_sidecar("") is None
        assert su.parse_sidecar("   \n") is None

    def test_rejects_short_or_non_hex(self):
        assert su.parse_sidecar("abc123") is None
        assert su.parse_sidecar("g" * 64) is None
        assert su.parse_sidecar("not a hash at all") is None

    def test_rejects_non_string(self):
        assert su.parse_sidecar(None) is None


class TestVerifyPackBytes:
    def test_match(self):
        raw = b'{"version": 8}'
        assert su.verify_pack_bytes(raw, hashlib.sha256(raw).hexdigest())

    def test_mismatch(self):
        raw = b'{"version": 8}'
        assert not su.verify_pack_bytes(raw, "0" * 64)

    def test_malformed_expected_never_matches(self):
        assert not su.verify_pack_bytes(b"x", "garbage")
        assert not su.verify_pack_bytes(b"x", "")

    def test_non_bytes_never_matches(self):
        assert not su.verify_pack_bytes("text", "a" * 64)


# ---------------------------------------------------------------------------
# Update flow with verification
# ---------------------------------------------------------------------------


class TestVerifiedUpdateFlow:
    def test_matching_hash_updates_cache(self, tmp_path):
        base = _cache(tmp_path, version=1)
        url = _write_pack(tmp_path, _pack(2, [_term("new-word")]))
        r = su.check_and_update(base_dir=base, url=url, force=True)
        assert r["status"] == "updated"
        assert r["version"] == 2 and r["previous_version"] == 1
        cached = su.load_cached_pack(base)
        assert cached["version"] == 2
        assert any(t["term"] == "new-word" for t in cached["terms"])
        assert "[sha256 verified]" in cached["source"]

    def test_mismatched_hash_rejects_pack_and_keeps_cache(self, tmp_path):
        base = _cache(tmp_path, version=3)
        url = _write_pack(tmp_path, _pack(9, [_term("evil-word")]), digest="0" * 64)
        r = su.check_and_update(base_dir=base, url=url, force=True)
        assert r["status"] == "error"
        assert "SHA-256 mismatch" in r["message"]
        assert "keeping previous list" in r["message"]
        cached = su.load_cached_pack(base)
        assert cached["version"] == 3
        assert all(t["term"] != "evil-word" for t in cached["terms"])

    def test_mismatched_hash_first_run_creates_no_cache(self, tmp_path):
        base = _cache(tmp_path)  # no cache yet
        url = _write_pack(tmp_path, _pack(1, [_term("evil-word")]), digest="0" * 64)
        r = su.check_and_update(base_dir=base, url=url, force=True)
        assert r["status"] == "error"
        assert not os.path.exists(os.path.join(base, su.CACHE_FILENAME))

    def test_missing_sidecar_rejects_pack(self, tmp_path):
        base = _cache(tmp_path, version=3)
        url = _write_pack(tmp_path, _pack(9, [_term("no-sidecar")]), sidecar=False)
        r = su.check_and_update(base_dir=base, url=url, force=True)
        assert r["status"] == "error"
        assert "hash sidecar unavailable" in r["message"]
        assert su.load_cached_pack(base)["version"] == 3

    def test_missing_sidecar_is_error_not_offline(self, tmp_path):
        # A reachable pack with no sidecar is a verification failure, not
        # a network problem — callers must be able to tell them apart.
        base = _cache(tmp_path)
        url = _write_pack(tmp_path, _pack(1, [_term("x")]), sidecar=False)
        r = su.check_and_update(base_dir=base, url=url, force=True)
        assert r["status"] == "error"

    def test_corrupt_sidecar_rejects_pack(self, tmp_path):
        base = _cache(tmp_path, version=3)
        url = _write_pack(
            tmp_path, _pack(9, [_term("x")]), sidecar_text="this is not a digest\n"
        )
        r = su.check_and_update(base_dir=base, url=url, force=True)
        assert r["status"] == "error"
        assert "hash sidecar is corrupt" in r["message"]
        assert su.load_cached_pack(base)["version"] == 3

    def test_tampered_bytes_rejected(self, tmp_path):
        # Sidecar computed for the original bytes; the pack file is then
        # rewritten (same version, different terms) without touching it.
        pack = _pack(4, [_term("original")])
        url = _write_pack(tmp_path, pack)
        tampered = _pack(4, [_term("tampered")])
        (tmp_path / "pack.json").write_text(
            json.dumps(tampered, ensure_ascii=False), encoding="utf-8"
        )
        base = _cache(tmp_path, version=1)
        r = su.check_and_update(base_dir=base, url=url, force=True)
        assert r["status"] == "error"
        assert "SHA-256 mismatch" in r["message"]
        assert su.load_cached_pack(base)["version"] == 1

    def test_verified_primary_updates_while_sidecarless_mirror_is_skipped(
        self, tmp_path
    ):
        # Mirrors without a sidecar are skipped (with a note), not merged
        # unverified — the verified canonical pack still updates the cache.
        base = _cache(tmp_path, version=1)
        url = _write_pack(tmp_path, _pack(2, [_term("from-canonical")]), name="a.json")
        mirror = _write_pack(
            tmp_path, _pack(2, [_term("from-mirror")]), name="b.json", sidecar=False
        )
        r = su.check_and_update(base_dir=base, force=True, extra_urls=[mirror], url=url)
        assert r["status"] == "updated"
        terms = {t["term"] for t in su.load_cached_pack(base)["terms"]}
        assert "from-canonical" in terms
        assert "from-mirror" not in terms
        assert "skipped" in r["message"]
        assert "hash sidecar" in r["message"]

    def test_unreachable_url_is_still_offline(self, tmp_path):
        # Network failure (no pack bytes at all) keeps the old "offline"
        # status — only verification failures become "error".
        base = _cache(tmp_path, version=2)
        r = su.check_and_update(
            base_dir=base, url="file:///nonexistent/x.json", force=True
        )
        assert r["status"] == "offline"
        assert su.load_cached_pack(base)["version"] == 2


# ---------------------------------------------------------------------------
# The published sidecar in the repo matches the published pack
# ---------------------------------------------------------------------------


class TestCommittedSidecar:
    def test_repo_sidecar_matches_repo_pack(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "safety_blocklist.json"), "rb") as f:
            raw = f.read()
        with open(
            os.path.join(root, "safety_blocklist.json.sha256"), encoding="utf-8"
        ) as f:
            sidecar = f.read()
        expected = su.parse_sidecar(sidecar)
        assert expected is not None, "committed sidecar is malformed"
        assert su.verify_pack_bytes(raw, expected), (
            "safety_blocklist.json.sha256 does not match safety_blocklist.json — "
            "re-run `python scripts/export_blocklist_pack.py --version N` "
            "and commit both files together."
        )
