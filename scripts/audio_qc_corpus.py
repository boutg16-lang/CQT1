# -*- coding: utf-8 -*-
"""Licensed-corpus management for audio-QC calibration.

``scripts/audio_qc_calibrate.py`` closes the *measurement* half of the audio-QC
gap; this module closes the *corpus* half. It builds a provenance-tracked
reference corpus that the calibrator can trust:

* every corpus carries a ``corpus_manifest.json`` that declares the source and
  the **license** — calibration refuses to run without one (fail closed on
  provenance, same philosophy as ``scripts/provenance.py``);
* clips are normalised to small canonical MP4s (black 1 fps frame + mono audio)
  so the calibrator's video-file scan and FFmpeg measurements work uniformly
  on voice recordings, downloaded datasets, or the creator's own narration;
* a Mozilla Common Voice export (CC0) can be imported straight from its
  standard layout (``clips/*.mp3`` + ``validated.tsv``).

Nothing here downloads anything: dataset acquisition stays a deliberate user
step, which keeps licensing decisions with the human who accepted the terms.

Typical flow::

    python -m scripts.audio_qc_corpus init corpus_ar --source "Own recordings" \
        --license own-recording
    python -m scripts.audio_qc_corpus add corpus_ar D:/audio/take1.wav take2.m4a
    python -m scripts.audio_qc_corpus status corpus_ar
    python -m scripts.audio_qc_corpus calibrate corpus_ar

Exit codes: 0 on success, 1 on a validation/license failure, 2 on usage errors.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence

SCHEMA_VERSION = 1
MANIFEST_NAME = "corpus_manifest.json"
CLIPS_SUBDIR = "clips"
CANONICAL_WIDTH, CANONICAL_HEIGHT = 320, 180

KNOWN_LICENSES = {
    "cc0": {
        "label": "CC0 1.0 (public domain)",
        "attribution_required": False,
    },
    "cc-by-4.0": {
        "label": "Creative Commons Attribution 4.0",
        "attribution_required": True,
    },
    "own-recording": {
        "label": "تسجيلات خاصة بمالك القناة (لا مشكلة ترخيص)",
        "attribution_required": False,
    },
    "custom": {
        "label": "ترخيص مخصص — يتطلب ملاحظة تصف الشروط",
        "attribution_required": False,
    },
}


def _now() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def manifest_path(corpus_dir: str) -> str:
    return os.path.join(os.path.abspath(os.fspath(corpus_dir)), MANIFEST_NAME)


def load_manifest(corpus_dir: str) -> Optional[Dict[str, Any]]:
    path = manifest_path(corpus_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".corpus_", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def init_corpus(corpus_dir: str, *, source: str, license_id: str,
                source_url: str = "", attribution: str = "",
                license_note: str = "", name: str = "") -> Dict[str, Any]:
    """Create a corpus folder with its provenance manifest."""
    if os.path.exists(manifest_path(corpus_dir)):
        raise SystemExit("corpus already initialised: {}".format(manifest_path(corpus_dir)))
    license_id = str(license_id or "").strip().lower()
    if license_id not in KNOWN_LICENSES:
        raise SystemExit(
            "unknown license id {!r} — known ids: {}".format(
                license_id, ", ".join(sorted(KNOWN_LICENSES))))
    if license_id == "custom" and not str(license_note).strip():
        raise SystemExit(
            "--license custom requires --license-note describing the terms "
            "(fail closed on provenance)")
    if KNOWN_LICENSES[license_id]["attribution_required"] and not attribution.strip():
        raise SystemExit(
            "license {} requires --attribution text".format(license_id))
    if not str(source).strip():
        raise SystemExit("--source is required (where did these clips come from?)")

    manifest = {
        "schema": SCHEMA_VERSION,
        "name": name or os.path.basename(os.path.abspath(corpus_dir)),
        "source": {
            "name": str(source).strip(),
            "url": str(source_url or "").strip(),
            "registered_at": _now(),
        },
        "license": {
            "id": license_id,
            "label": KNOWN_LICENSES[license_id]["label"],
            "attribution": str(attribution or "").strip(),
            "note": str(license_note or "").strip(),
        },
        "clips_subdir": CLIPS_SUBDIR,
        "created_at": _now(),
    }
    os.makedirs(os.path.join(corpus_dir, CLIPS_SUBDIR), exist_ok=True)
    _write_json(manifest_path(corpus_dir), manifest)
    return manifest


def require_license(corpus_dir: str) -> Dict[str, Any]:
    """Return the manifest or raise SystemExit — calibration must not proceed
    on an unlicensed corpus."""
    manifest = load_manifest(corpus_dir)
    if manifest is None:
        raise SystemExit(
            "no corpus manifest at {} — run `init` first and declare the "
            "source license".format(manifest_path(corpus_dir)))
    license_id = str((manifest.get("license") or {}).get("id") or "").lower()
    if license_id not in KNOWN_LICENSES:
        raise SystemExit(
            "corpus manifest has no recognised license id ({!r}) — "
            "calibration is refused on unlicensed data".format(license_id))
    return manifest


# ---------------------------------------------------------------------------
# Clip normalisation
# ---------------------------------------------------------------------------

def _slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", str(text or "")).strip("-").lower()
    return slug[:48] or "clip"


def _next_index(clips_dir: str) -> int:
    highest = 0
    for name in os.listdir(clips_dir):
        match = re.match(r"(\d+)_", name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def normalize_clip(source: str, target: str, *, timeout: int = 300) -> Dict[str, Any]:
    """Convert any audio/video file into the canonical corpus clip.

    Canonical = tiny black 1 fps video + mono 16 kHz AAC audio: identical
    measurement surface for loudness/true-peak/silence, minimal size, and a
    video container the calibrator's scan accepts.
    """
    if shutil.which("ffmpeg") is None:
        return {"ok": False, "error": "ffmpeg not found on PATH"}
    if not os.path.isfile(source):
        return {"ok": False, "error": "file not found: {}".format(source)}
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", source,
        "-f", "lavfi", "-i", "color=c=black:s={}x{}:r=1".format(
            CANONICAL_WIDTH, CANONICAL_HEIGHT),
        "-map", "0:a:0?", "-map", "1:v:0",
        "-shortest",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "40",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ac", "1", "-ar", "16000", "-b:a", "48k",
        target,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}
    if proc.returncode != 0 or not os.path.isfile(target) or os.path.getsize(target) == 0:
        return {"ok": False,
                "error": (proc.stderr or proc.stdout or "ffmpeg failed")[-400:]}
    return {"ok": True, "path": target, "size": os.path.getsize(target)}


def add_clips(corpus_dir: str, sources: Sequence[str]) -> Dict[str, Any]:
    """Normalise files into the corpus (requires an initialised manifest)."""
    require_license(corpus_dir)
    clips_dir = os.path.join(corpus_dir, CLIPS_SUBDIR)
    os.makedirs(clips_dir, exist_ok=True)
    index = _next_index(clips_dir)
    added, failed = [], []
    for source in sources:
        source = os.path.abspath(os.path.expanduser(source))
        base = _slug(os.path.splitext(os.path.basename(source))[0])
        target = os.path.join(clips_dir, "{:03d}_{}.mp4".format(index, base))
        result = normalize_clip(source, target)
        if result.get("ok"):
            added.append(os.path.basename(target))
            index += 1
        else:
            failed.append({"file": source, "error": result.get("error", "unknown")})
    return {"added": added, "failed": failed}


# ---------------------------------------------------------------------------
# Mozilla Common Voice import (CC0)
# ---------------------------------------------------------------------------

def import_common_voice(corpus_dir: str, cv_dir: str, *, limit: Optional[int] = None) -> Dict[str, Any]:
    """Import a Mozilla Common Voice export (``clips/*.mp3`` + TSV files).

    Common Voice is CC0; the manifest is created automatically when missing.
    Only clips listed in ``validated.tsv`` are imported when that file exists
    (falling back to every row of ``dev.tsv``/``train.tsv``/``test.tsv``).
    """
    cv_dir = os.path.abspath(os.path.expanduser(cv_dir))
    clips_src = os.path.join(cv_dir, "clips")
    if not os.path.isdir(clips_src):
        raise SystemExit("Common Voice export not found (no clips/ dir): {}".format(cv_dir))

    if load_manifest(corpus_dir) is None:
        init_corpus(
            corpus_dir,
            source="Mozilla Common Voice (Arabic)",
            license_id="cc0",
            source_url="https://commonvoice.mozilla.org/ar/datasets",
            attribution="Mozilla Common Voice contributors",
            name="common-voice-ar",
        )
    else:
        require_license(corpus_dir)

    rows: List[str] = []
    for tsv_name in ("validated.tsv", "dev.tsv", "train.tsv", "test.tsv"):
        tsv_path = os.path.join(cv_dir, tsv_name)
        if not os.path.isfile(tsv_path):
            continue
        with open(tsv_path, "r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            for row in reader:
                rel = (row or {}).get("path")
                if rel:
                    rows.append(rel)
        if rows:
            break
    if not rows:
        # No TSV metadata: take every mp3 in clips/.
        rows = sorted(name for name in os.listdir(clips_src) if name.endswith(".mp3"))
    if limit is not None:
        rows = rows[: max(0, int(limit))]

    sources = []
    skipped = 0
    for rel in rows:
        candidate = os.path.join(clips_src, os.path.basename(rel))
        if os.path.isfile(candidate):
            sources.append(candidate)
        else:
            skipped += 1
    result = add_clips(corpus_dir, sources)
    result["skipped_missing_files"] = skipped
    return result


# ---------------------------------------------------------------------------
# Status + calibration
# ---------------------------------------------------------------------------

def corpus_status(corpus_dir: str) -> Dict[str, Any]:
    manifest = load_manifest(corpus_dir)
    clips_dir = os.path.join(corpus_dir, CLIPS_SUBDIR)
    clips = sorted(
        name for name in os.listdir(clips_dir)
        if name.endswith(".mp4")) if os.path.isdir(clips_dir) else []
    license_id = str(((manifest or {}).get("license") or {}).get("id") or "")
    return {
        "corpus_dir": os.path.abspath(corpus_dir),
        "manifest": manifest is not None,
        "license": license_id or None,
        "source": ((manifest or {}).get("source") or {}).get("name"),
        "clips": len(clips),
        "clip_names": clips[:10],
        "calibration_ready": manifest is not None and len(clips) >= 5,
        "thresholds_present": os.path.isfile(
            os.path.join(corpus_dir, "audio_qc_thresholds.json")),
    }


def calibrate_corpus(corpus_dir: str, *, output: Optional[str] = None,
                     min_files: int = 5, limit: Optional[int] = None) -> Dict[str, Any]:
    """License-gated calibration: refuse unlicensed corpora, then delegate to
    ``scripts.audio_qc_calibrate``."""
    manifest = require_license(corpus_dir)
    from scripts import audio_qc_calibrate

    clips_dir = os.path.join(corpus_dir, CLIPS_SUBDIR)
    if not os.path.isdir(clips_dir):
        raise SystemExit("corpus has no clips/ folder: {}".format(corpus_dir))
    report = audio_qc_calibrate.calibrate(
        clips_dir, min_files=min_files, limit=limit)
    report["corpus_manifest"] = {
        "source": (manifest.get("source") or {}).get("name"),
        "license": (manifest.get("license") or {}).get("id"),
    }
    target = output or os.path.join(corpus_dir, "audio_qc_thresholds.json")
    audio_qc_calibrate.write_report(target, report)
    report["output"] = target
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_init(args: argparse.Namespace) -> int:
    manifest = init_corpus(
        args.corpus_dir, source=args.source, license_id=args.license,
        source_url=args.source_url, attribution=args.attribution,
        license_note=args.license_note, name=args.name)
    print("تم إنشاء corpus بترخيص {} في {}".format(
        manifest["license"]["id"], os.path.abspath(args.corpus_dir)))
    return 0


def _cmd_add(args: argparse.Namespace) -> int:
    result = add_clips(args.corpus_dir, args.files)
    for name in result["added"]:
        print("  ✓ {}".format(name))
    for item in result["failed"]:
        print("  ✗ {} — {}".format(os.path.basename(item["file"]), item["error"]))
    print("أُضيف {} مقطعاً، فشل {}.".format(len(result["added"]), len(result["failed"])))
    return 0 if not result["failed"] else 1


def _cmd_import_cv(args: argparse.Namespace) -> int:
    result = import_common_voice(args.corpus_dir, args.cv_dir, limit=args.limit)
    print("Common Voice: أُضيف {} مقطعاً، فشل {}، غير موجود {}".format(
        len(result["added"]), len(result["failed"]),
        result.get("skipped_missing_files", 0)))
    return 0 if not result["failed"] else 1


def _cmd_status(args: argparse.Namespace) -> int:
    status = corpus_status(args.corpus_dir)
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0
    print("corpus: {}".format(status["corpus_dir"]))
    print("  الترخيص: {} — المصدر: {}".format(status["license"], status["source"]))
    print("  المقاطع: {} — جاهز للمعايرة: {}".format(
        status["clips"], "نعم" if status["calibration_ready"] else "لا (يحتاج 5 على الأقل)"))
    print("  thresholds موجودة: {}".format("نعم" if status["thresholds_present"] else "لا"))
    return 0 if status["calibration_ready"] else 2


def _cmd_calibrate(args: argparse.Namespace) -> int:
    report = calibrate_corpus(
        args.corpus_dir, output=args.output, min_files=args.min_files,
        limit=args.limit)
    if report.get("ok"):
        recommended = report.get("recommended") or {}
        print("المعايرة نجحت: {} مقطعاً مقاساً — العتبات المقترحة: {}".format(
            report["corpus"]["files_measured"],
            json.dumps(recommended, ensure_ascii=False)))
        print("التقرير: {}".format(report["output"]))
        print("لاستخدامها: python -m scripts.audio_qc --project <مشروع> --thresholds-file {}".format(
            report["output"]))
        return 0
    print("فشلت المعايرة: {}".format(report.get("error", "unknown")), file=sys.stderr)
    return 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Licensed-corpus management for audio-QC calibration")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="create a corpus with its license manifest")
    p_init.add_argument("corpus_dir")
    p_init.add_argument("--source", required=True)
    p_init.add_argument("--license", required=True, dest="license",
                        choices=sorted(KNOWN_LICENSES))
    p_init.add_argument("--source-url", default="")
    p_init.add_argument("--attribution", default="")
    p_init.add_argument("--license-note", default="")
    p_init.add_argument("--name", default="")
    p_init.set_defaults(func=_cmd_init)

    p_add = sub.add_parser("add", help="normalise audio/video files into the corpus")
    p_add.add_argument("corpus_dir")
    p_add.add_argument("files", nargs="+")
    p_add.set_defaults(func=_cmd_add)

    p_cv = sub.add_parser("import-cv", help="import a Mozilla Common Voice export")
    p_cv.add_argument("corpus_dir")
    p_cv.add_argument("cv_dir")
    p_cv.add_argument("--limit", type=int, default=None)
    p_cv.set_defaults(func=_cmd_import_cv)

    p_status = sub.add_parser("status", help="summarise the corpus")
    p_status.add_argument("corpus_dir")
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=_cmd_status)

    p_cal = sub.add_parser("calibrate", help="license-gated threshold calibration")
    p_cal.add_argument("corpus_dir")
    p_cal.add_argument("--output", default=None)
    p_cal.add_argument("--min-files", type=int, default=5)
    p_cal.add_argument("--limit", type=int, default=None)
    p_cal.set_defaults(func=_cmd_calibrate)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
