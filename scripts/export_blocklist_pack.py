# -*- coding: utf-8 -*-
"""
Export the built-in BLOCKLIST from safety_filter.py into the versioned
canonical pack ``safety_blocklist.json`` (repo root).

Every installation auto-updates from that file (scripts/safety_updater.py),
so this is how maintainers push new words to all users:

    python scripts/export_blocklist_pack.py --version N

(remember to bump N on every change — equal or lower versions are ignored
by clients)

The export also writes a SHA-256 sidecar ``safety_blocklist.json.sha256``
(sha256sum format) next to the pack, computed from the exact bytes written.
The updater verifies every download against that sidecar before installing
it, so commit BOTH files together — a pack without its sidecar is rejected
by clients. Update procedure: run this script, then commit the pack and
its sidecar in the same commit.
"""

import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import safety_filter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", type=int, required=True,
                        help="Pack version (bump on every change)")
    parser.add_argument("--updated", default=None,
                        help="Content update date in YYYY-MM-DD format (default: today)")
    parser.add_argument("--reviewed-at", default=None,
                        help="Human review date in YYYY-MM-DD format (default: today)")
    parser.add_argument("--out", default=None,
                        help="Output path (default: repo root safety_blocklist.json)")
    args = parser.parse_args()

    import datetime

    today = datetime.date.today().isoformat()
    updated = args.updated or today
    reviewed_at = args.reviewed_at or today
    for name, value in (("--updated", updated), ("--reviewed-at", reviewed_at)):
        try:
            datetime.date.fromisoformat(value)
        except ValueError:
            parser.error("{} must be YYYY-MM-DD".format(name))

    terms = [
        {"term": term, "lang": lang, "severity": severity, "category": category}
        for term, lang, severity, category in safety_filter.BLOCKLIST
    ]

    pack = {
        "version": args.version,
        "updated": updated,
        "reviewed_at": reviewed_at,
        "source": "ViralCutter built-in blocklist",
        "terms": terms,
    }

    out = args.out or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "safety_blocklist.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, indent=2)
    # SHA-256 sidecar: computed from the exact bytes just written, so the
    # digest matches what clients download. Keep the pack and its sidecar
    # in the same commit — clients reject a pack without a matching sidecar.
    with open(out, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    sidecar = out + ".sha256"
    with open(sidecar, "w", encoding="utf-8") as f:
        f.write("{}  {}\n".format(digest, os.path.basename(out)))
    print(f"Exported {len(terms)} terms (v{args.version}) -> {out}")
    print(f"SHA-256 sidecar ({digest[:12]}…) -> {sidecar}")


if __name__ == "__main__":
    main()
