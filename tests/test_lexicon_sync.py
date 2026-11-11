"""The Android app ships copies of the safety lexicons; they must not drift."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "app" / "src" / "main" / "assets"
FILES = ("safety_blocklist.json", "safety_terms_arabic.json")


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_android_lexicons_match_root():
    for name in FILES:
        root_file, android_file = ROOT / name, ASSETS / name
        assert root_file.is_file(), f"missing {root_file}"
        assert android_file.is_file(), f"missing {android_file}"
        assert _load(root_file) == _load(android_file), (
            f"{name} differs between repo root and app/src/main/assets; "
            "update both copies together."
        )
