# -*- coding: utf-8 -*-
"""
Safety blocklist auto-updater (v7.18 — multi-source, continuous).

YouTube's policies and the evasion vocabulary evolve constantly. Instead of
waiting for a code release, ViralCutter keeps a *versioned blocklist pack*
in the GitHub repo and every installation updates itself from it:

    repo: safety_blocklist.json            (canonical, versioned)
    user: safety_blocklist_cache.json      (downloaded copy, git-ignored)

v7.18 adds:
* **Multi-source merge** — the updater can combine packs from several
  remotes (canonical repo + mirrors + community packs). Terms are merged
  per (term, lang): the highest severity wins, newer packs win ties.
* **Continuous watch mode** — ``python scripts/safety_updater.py --watch 6``
  re-checks every 6 hours so a long-running session always uses fresh
  policy data without restarting.
* **Source provenance** — every cached pack records which URL it came from.

Guarantees (unchanged):
* Offline-safe — any network/parse failure keeps the previous cache (and if
  there is no cache at all, the built-in list still protects the user).
* Only *newer* versions replace the cache.
* Cheap — a daily stamp file prevents hammering GitHub on every run.
* No dependencies — pure urllib (works on Windows out of the box).

SHA-256 verification (added 2026-11-19):
* Every downloaded pack is verified against a ``.sha256`` sidecar published
  next to it (``<pack-url>.sha256``, sha256sum format — see
  ``scripts/export_blocklist_pack.py``, which writes the sidecar whenever a
  pack is exported). The digest is checked over the raw downloaded bytes
  *before* the JSON is parsed, and a pack whose sidecar is missing, corrupt
  or mismatched is rejected — the previous cache is left untouched.
* Verification is required for *every* source (canonical, mirrors and
  explicit ``--url`` packs alike). A source that fails verification is
  skipped and the run reports status ``"error"`` (not ``"offline"``) so a
  tampered/corrupted pack is never mistaken for a network problem.
* Trust model, stated honestly: the sidecar lives in the same repo as the
  pack, so this protects against corrupted/truncated downloads and
  proxy/CDN tampering — not against a compromised repo (an attacker who can
  rewrite the pack can rewrite its sidecar too). Transport is protected by
  HTTPS. A hash pinned in the app code was considered and rejected: it
  would break the whole point of remote packs, because installed apps
  would reject every newer pack until a code release shipped the new hash.
* Update procedure for maintainers: run
  ``python scripts/export_blocklist_pack.py --version N+1`` and commit
  *both* ``safety_blocklist.json`` and ``safety_blocklist.json.sha256``.
  The exporter computes the sidecar from the exact bytes it writes.
"""

import datetime
import hashlib
import hmac
import json
import os
import time
import urllib.request

REMOTE_URL = ("https://raw.githubusercontent.com/"
              "boutg16-lang/CQT1/main/safety_blocklist.json")

# v7.18: fallback mirrors / community packs merged on top of the canonical
# pack. Each entry may also carry "required": False to tolerate a dead mirror.
REMOTE_SOURCES = [
    {"url": REMOTE_URL, "required": True, "label": "canonical"},
    {
        "url": ("https://raw.githubusercontent.com/"
                "eldjazaireldjadida4-web/ViralCutter/main/safety_blocklist.json"),
        "required": False,
        "label": "legacy-mirror",
    },
]

CACHE_FILENAME = "safety_blocklist_cache.json"
STAMP_FILENAME = ".safety_update_stamp"
FETCH_TIMEOUT = 10          # seconds
MAX_TERMS = 20000           # sanity limit for a downloaded pack
ONE_DAY = 24 * 3600
MAX_SOURCES = 8             # sanity limit for merged packs
SIDECAR_SUFFIX = ".sha256"  # hash sidecar published next to each pack
SIDECAR_MAX_BYTES = 4096    # a sidecar is one short "<hex>  <name>" line


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def cache_path(base_dir=None):
    return os.path.join(base_dir or _repo_root(), CACHE_FILENAME)


def stamp_path(base_dir=None):
    return os.path.join(base_dir or _repo_root(), STAMP_FILENAME)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_pack(data):
    """Return the normalized {"version": int, "terms": [...]} or None."""
    if not isinstance(data, dict):
        return None
    terms = data.get("terms")
    if not isinstance(terms, list) or not terms or len(terms) > MAX_TERMS:
        return None
    clean = []
    for t in terms:
        if not isinstance(t, dict) or not t.get("term"):
            continue
        clean.append({
            "term": str(t["term"])[:200],
            "lang": str(t.get("lang", "multi"))[:20],
            "severity": t.get("severity") if t.get("severity") in ("low", "medium", "high") else "high",
            "category": str(t.get("category", "custom"))[:50],
        })
    if not clean:
        return None
    try:
        version = int(data.get("version", 0))
    except Exception:
        version = 0
    return {"version": version, "terms": clean,
            "updated": str(data.get("updated", ""))[:40],
            "reviewed_at": str(data.get("reviewed_at", ""))[:40],
            "source": str(data.get("source", ""))[:200]}


def load_cached_pack(base_dir=None):
    """Read the downloaded cache (None if missing/corrupt)."""
    path = cache_path(base_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return validate_pack(json.load(f))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Update logic
# ---------------------------------------------------------------------------

def _fetch_bytes(url, timeout=FETCH_TIMEOUT, max_bytes=2 * 1024 * 1024):
    """Download raw bytes from ``url`` (2 MB cap by default)."""
    req = urllib.request.Request(url, headers={"User-Agent": "ViralCutter-safety-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(max_bytes + 1)[:max_bytes + 1]


def _fetch_json(url, timeout=FETCH_TIMEOUT):
    return json.loads(_fetch_bytes(url, timeout=timeout).decode("utf-8"))


# ---------------------------------------------------------------------------
# SHA-256 verification
# ---------------------------------------------------------------------------

def parse_sidecar(text):
    """Parse a ``.sha256`` sidecar and return the expected hex digest.

    Accepts the ``sha256sum`` format (``"<hex>  <filename>"``) or a bare hex
    digest. Returns ``None`` when the sidecar is empty or malformed — the
    caller treats that as a verification failure, never as "no hash".
    """
    if not isinstance(text, str):
        return None
    token = text.strip().split()
    if not token:
        return None
    digest = token[0].lower()
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        return None
    return digest


def verify_pack_bytes(raw, expected_hex):
    """True when the SHA-256 of ``raw`` matches ``expected_hex``.

    Constant-time comparison; a malformed expected digest never matches.
    """
    if not isinstance(raw, (bytes, bytearray)) or not expected_hex:
        return False
    expected = parse_sidecar(str(expected_hex))
    if expected is None:
        return False
    return hmac.compare_digest(hashlib.sha256(bytes(raw)).hexdigest(), expected)


def _fetch_pack(source):
    """Fetch one pack and verify it against its ``.sha256`` sidecar.

    Returns ``(pack, error)`` — ``pack`` is the validated pack dict (or
    ``None``) and ``error`` is a human-readable reason (or ``None``). The
    pack bytes are verified *before* the JSON is parsed, so a corrupt or
    tampered download never reaches the parser or the cache.
    """
    label = source["label"]
    try:
        raw = _fetch_bytes(source["url"])
    except Exception as e:
        return None, "{}: {}".format(label, e)
    try:
        sidecar = _fetch_bytes(source["url"] + SIDECAR_SUFFIX,
                               max_bytes=SIDECAR_MAX_BYTES)
    except Exception as e:
        return None, "{}: hash sidecar unavailable ({})".format(label, e)
    if len(sidecar) > SIDECAR_MAX_BYTES:
        return None, "{}: hash sidecar is corrupt".format(label)
    try:
        expected = parse_sidecar(sidecar.decode("utf-8", "replace"))
    except Exception:
        expected = None
    if expected is None:
        return None, "{}: hash sidecar is corrupt".format(label)
    if not verify_pack_bytes(raw, expected):
        return None, "{}: SHA-256 mismatch — pack rejected".format(label)
    try:
        pack = validate_pack(json.loads(raw.decode("utf-8")))
    except Exception as e:
        return None, "{}: invalid pack ({})".format(label, e)
    if not pack:
        return None, "{}: invalid pack".format(label)
    return pack, None


def merge_packs(packs):
    """Merge validated packs: highest severity per (term, lang) wins.

    ``packs`` is an ordered list of dicts from :func:`validate_pack`. Later
    packs win version ties. Returns a single normalized pack.
    """
    merged = {}
    max_version = 0
    updated = ""
    reviewed_dates = []
    source_labels = []
    for pack in packs:
        if not pack:
            continue
        version = pack["version"]
        if version > max_version:
            max_version = version
            reviewed_dates = [pack.get("reviewed_at", "")]
        elif version == max_version:
            reviewed_dates.append(pack.get("reviewed_at", ""))
        if pack.get("updated", "").upper() > updated.upper():
            updated = pack["updated"]
        if pack.get("source"):
            source_labels.append(pack["source"])
        for term in pack["terms"]:
            key = (term["term"].casefold(), term["lang"])
            previous = merged.get(key)
            order = {"low": 0, "medium": 1, "high": 2}
            if previous is None or order.get(term["severity"], 2) > order.get(previous["severity"], 2):
                merged[key] = term
    reviewed_at = ""
    if reviewed_dates and all(reviewed_dates):
        try:
            reviewed_at = min(
                datetime.date.fromisoformat(value[:10])
                for value in reviewed_dates
            ).isoformat()
        except (TypeError, ValueError):
            pass
    return {
        "version": max_version,
        "updated": updated,
        "reviewed_at": reviewed_at,
        "source": "; ".join(dict.fromkeys(source_labels)) or "merged packs",
        "terms": list(merged.values()),
    }


def check_and_update(base_dir=None, url=None, force=False, extra_urls=None):
    """Fetch remote pack(s) and replace the cache if a newer one is available.

    Every pack is SHA-256 verified against its ``.sha256`` sidecar before
    it is parsed or merged (see :func:`_fetch_pack`); a pack that fails
    verification is rejected and the previous cache is kept. A run where
    packs were reachable but none verified reports status ``"error"``.

    Returns a status dict: {status: updated|up-to-date|offline|error,
                            version, previous_version, message}
    Never raises.

    * ``url=None`` (default) → multi-source mode: canonical repo + legacy
      mirrors merged automatically.
    * ``url=<explicit>`` → single-source mode: only that URL is used (plus
      any ``extra_urls``). An unreachable explicit URL reports ``offline``
      without falling back to mirrors — callers who pin a source get that
      source, not a surprise.
    """
    cached = load_cached_pack(base_dir)
    cached_version = cached["version"] if cached else 0

    # daily throttle (unless forced)
    stamp = stamp_path(base_dir)
    if not force and os.path.exists(stamp):
        try:
            last = float(open(stamp, encoding="utf-8").read().strip() or "0")
            if time.time() - last < ONE_DAY:
                return {"status": "up-to-date", "version": cached_version,
                        "previous_version": cached_version,
                        "message": "checked recently (daily throttle)"}
        except Exception:
            pass

    if url is not None:
        sources = [{"url": url, "required": True, "label": "primary"}]
        sources.extend({"url": u, "required": False, "label": "extra"} for u in (extra_urls or []))
    else:
        sources = [{"url": REMOTE_URL, "required": True, "label": "canonical"}]
        for source in REMOTE_SOURCES:
            if all(source["url"] != s["url"] for s in sources):
                sources.append(source)
    sources = sources[:MAX_SOURCES]

    fetched = []
    verified_labels = []
    errors = []
    verification_failed = False
    for source in sources:
        pack, error = _fetch_pack(source)
        if pack:
            fetched.append(pack)
            verified_labels.append(source["label"])
        else:
            errors.append(error)
            if "hash sidecar" in error or "SHA-256 mismatch" in error:
                verification_failed = True

    try:
        with open(stamp, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except Exception:
        pass

    if not fetched:
        if verification_failed:
            # Fail closed, and say so: a pack was reachable but its hash
            # did not check out — that is not a network problem.
            return {"status": "error", "version": cached_version,
                    "previous_version": cached_version,
                    "message": "hash verification failed ({}) — keeping previous list".format(
                        "; ".join(errors[:2]))}
        return {"status": "offline", "version": cached_version,
                "previous_version": cached_version,
                "message": "could not reach any update source ({}) — using local list".format(
                    "; ".join(errors[:2]))}

    merged = merge_packs(fetched)
    # Provenance: every merged pack was SHA-256 verified, record that in
    # the cached pack itself (merge_packs only keeps the packs' own
    # "source" strings, which say nothing about verification).
    merged["source"] = "; ".join(
        "{} [sha256 verified]".format(label) for label in verified_labels)
    if merged["version"] <= cached_version and cached is not None:
        return {"status": "up-to-date", "version": cached_version,
                "previous_version": cached_version,
                "message": "list is current (v{})".format(cached_version)}

    try:
        with open(cache_path(base_dir), "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return {"status": "error", "version": cached_version,
                "previous_version": cached_version,
                "message": "could not write cache ({})".format(e)}

    message = "updated v{} → v{} ({} terms, sources: {})".format(
        cached_version, merged["version"], len(merged["terms"]),
        merged.get("source", "?"))
    if errors:
        # A source was skipped (e.g. a mirror without a sidecar) — say so
        # instead of silently merging a subset.
        message += "; skipped: {}".format("; ".join(errors))
    return {"status": "updated", "version": merged["version"],
            "previous_version": cached_version,
            "message": message}


def watch(base_dir=None, interval_hours=6, max_cycles=None):
    """Continuous update loop: re-check every ``interval_hours`` hours.

    Returns after ``max_cycles`` iterations (None = run forever). Designed
    for long-running WebUI sessions and always-on boxes.
    """
    import time as _time
    interval = max(1.0, float(interval_hours)) * 3600
    cycle = 0
    while max_cycles is None or cycle < max_cycles:
        cycle += 1
        result = check_and_update(base_dir=base_dir, force=True)
        print("[safety-updater] cycle {}: {} — {}".format(
            cycle, result["status"], result["message"]))
        if max_cycles is not None and cycle >= max_cycles:
            break
        _time.sleep(interval)
    return cycle


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Update the hate-speech blocklist from GitHub (multi-source).")
    parser.add_argument("--force", action="store_true", help="ignore the daily throttle")
    parser.add_argument("--url", default=REMOTE_URL, help="override the primary pack URL")
    parser.add_argument("--extra-url", action="append", default=[],
                        help="additional pack URL to merge (repeatable)")
    parser.add_argument("--watch", type=float, default=None, metavar="HOURS",
                        help="continuous mode: re-check every N hours")
    parser.add_argument("--cycles", type=int, default=None,
                        help="with --watch: stop after N cycles (default: forever)")
    args = parser.parse_args()

    if args.watch is not None:
        count = watch(interval_hours=args.watch, max_cycles=args.cycles)
        print("[safety-updater] watch finished after {} cycle(s)".format(count))
        return

    result = check_and_update(force=args.force, url=args.url,
                              extra_urls=args.extra_url)
    print("[safety-updater] {}: {}".format(result["status"], result["message"]))


if __name__ == "__main__":
    main()
