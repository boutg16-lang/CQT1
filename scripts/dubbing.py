# -*- coding: utf-8 -*-
"""Dubbing: give a translated clip a voice in the target language.

The project already translates subtitles (``scripts/translate_json.py``) but the
clip stayed in the creator's own voice, so a translated clip could never reach
another-language audience. This module closes that gap using the TTS provider
layer in :mod:`scripts.tts_providers` (design taken from MoneyPrinterTurbo,
MIT; rewritten for this project's constraints).

Design rules that keep it honest with the rest of OUSSAMA:

* **Opt-in and off the default path.** Nothing here runs unless a caller asks;
  the network provider additionally needs ``VIRALCUTTER_TTS_ALLOW_NETWORK=1``.
* **Fail-open per segment.** A segment that cannot be synthesised is skipped
  and recorded; the original audio stays in place. If every segment fails the
  clip is untouched and ``ok`` is False.
* **Timing first.** Each synthesised segment is time-fitted into its subtitle
  window with ``atempo`` (bounded) so the dub tracks the picture instead of
  drifting.
* **Disclosure.** The report always states ``synthetic_voice`` /
  ``disclosure_required`` — YouTube expects synthetic-voice/modified-content
  disclosure, and ``originality``/``provenance`` still treat a dub as a
  meaningful transformation, never as an original upload.
* **No gate bypass.** Dubbing only writes files; publishing still goes through
  ``upload_gate``/``content_guard`` with Dry Run and public confirmation.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Sequence

from scripts import tts_providers

DEFAULT_MODE = "replace"          # "replace" (dub only) | "mix" (dub over ducked original)
DEFAULT_ORIGINAL_VOLUME = 0.12
MIN_RATIO, MAX_RATIO = 0.5, 3.0   # clamp for atempo fitting
REPORT_NAME = "dubbing_report.json"
CLIP_SUFFIX = "_dubbed"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd: Sequence[str], *, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)


def probe_duration(path: str, ffprobe: str = "ffprobe") -> float:
    try:
        proc = _run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", path], timeout=60)
        return max(0.0, float((proc.stdout or "").strip() or 0.0))
    except Exception:
        return 0.0


def load_subtitle_segments(subs_json: str) -> List[Dict[str, Any]]:
    """Read WhisperX-style cut subtitles → [{index, start, end, text}]."""
    with open(subs_json, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    raw = data.get("segments") if isinstance(data, dict) else None
    segments: List[Dict[str, Any]] = []
    for index, entry in enumerate(raw or []):
        if not isinstance(entry, dict):
            continue
        try:
            start = max(0.0, float(entry.get("start")))
            end = max(start, float(entry.get("end")))
        except (TypeError, ValueError):
            continue
        text = str(entry.get("text") or "").strip()
        if not text or end <= start:
            continue
        segments.append({"index": index, "start": start, "end": end, "text": text})
    return segments


def _atempo_filter(ratio: float) -> tuple:
    """Build an atempo chain that fits audio of *ratio* × the window into it.

    ``ratio`` = synthesised duration / subtitle window, so the required tempo
    factor IS the ratio (atempo 2.0 plays twice as fast → half the length).
    Bounded to avoid comic speed-ups/slowdowns on bad provider output.
    """
    warnings: List[str] = []
    if ratio <= 0:
        return "", ["invalid duration ratio"]
    clamped = min(max(ratio, MIN_RATIO), MAX_RATIO)
    if abs(clamped - ratio) > 1e-6:
        warnings.append("needed {:.2f}x speed (clamped to {:.2f}x)".format(ratio, clamped))
    tempo = clamped
    chain = []
    while tempo > 2.0:
        chain.append("atempo=2.0")
        tempo /= 2.0
    while tempo < 0.5:
        chain.append("atempo=0.5")
        tempo /= 0.5
    if abs(tempo - 1.0) > 0.01:
        chain.append("atempo={:.4f}".format(tempo))
    return (",".join(chain), warnings)


def _mean_volume(out_path: str, ffmpeg: str = "ffmpeg") -> Optional[float]:
    """Overall mean volume of a file's audio (dBFS); None when unmeasurable."""
    import re as _re

    try:
        proc = _run([ffmpeg, "-hide_banner", "-nostats", "-loglevel", "info",
                     "-i", out_path, "-vn", "-af", "volumedetect", "-f", "null", "-"],
                    timeout=300)
    except Exception:
        return None
    match = _re.search(r"mean_volume:\s*(-?[\d.]+) dB",
                       (proc.stderr or "") + (proc.stdout or ""))
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def plan_dub(clip_path: str, subs_json: str, *, provider: str = "edge",
             voice: Optional[str] = None, mode: str = DEFAULT_MODE,
             ffprobe: str = "ffprobe") -> Dict[str, Any]:
    """Describe what dubbing would do (no synthesis, no writes)."""
    info = tts_providers.provider_info(provider)
    if info is None:
        return {"ok": False, "error": "unknown TTS provider {!r}".format(provider),
                "providers": tts_providers.provider_names()}
    if not os.path.isfile(clip_path):
        return {"ok": False, "error": "clip not found: {}".format(clip_path)}
    if not os.path.isfile(subs_json):
        return {"ok": False, "error": "subtitle file not found: {}".format(subs_json)}
    mode = str(mode or DEFAULT_MODE).lower()
    if mode not in {"replace", "mix"}:
        return {"ok": False, "error": "mode must be 'replace' or 'mix'"}
    segments = load_subtitle_segments(subs_json)
    return {
        "ok": bool(segments),
        "clip": os.path.abspath(clip_path),
        "subtitles": os.path.abspath(subs_json),
        "duration": round(probe_duration(clip_path, ffprobe), 3),
        "mode": mode,
        "provider": provider,
        "provider_network": bool(info["network"]),
        "voice": voice,
        "segments": [
            {"index": item["index"], "start": round(item["start"], 3),
             "end": round(item["end"], 3), "chars": len(item["text"])}
            for item in segments
        ],
        "segment_count": len(segments),
        "error": None if segments else "no usable subtitle segments",
    }


# ---------------------------------------------------------------------------
# Dubbing one clip
# ---------------------------------------------------------------------------

def dub_clip(clip_path: str, subs_json: str, out_path: str, *,
             provider: str = "edge", voice: Optional[str] = None,
             mode: str = DEFAULT_MODE, original_volume: float = DEFAULT_ORIGINAL_VOLUME,
             work_dir: Optional[str] = None, ffmpeg: str = "ffmpeg",
             ffprobe: str = "ffprobe", timeout: int = 120,
             allow_network: Optional[bool] = None) -> Dict[str, Any]:
    """Synthesise the translated lines and mux them onto the clip."""
    plan = plan_dub(clip_path, subs_json, provider=provider, voice=voice,
                    mode=mode, ffprobe=ffprobe)
    if not plan.get("ok"):
        return {"ok": False, "error": plan.get("error"), "plan": plan,
                "synthetic_voice": True, "disclosure_required": True}

    mode = plan["mode"]
    duration = float(plan["duration"] or 0.0)
    segments = load_subtitle_segments(subs_json)

    work = work_dir or tempfile.mkdtemp(prefix="dubbing_")
    os.makedirs(work, exist_ok=True)
    keep_work = work_dir is not None

    placed: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    warnings: List[str] = []

    try:
        for item in segments:
            raw_audio = os.path.join(work, "seg_{:03d}.mp3".format(item["index"]))
            result = tts_providers.synthesize(
                item["text"], raw_audio, provider=provider, voice=voice,
                timeout=timeout, allow_network=allow_network)
            if not result.get("ok"):
                failed.append({"index": item["index"], "error": result.get("error")})
                continue
            window = max(0.05, item["end"] - item["start"])
            ratio = (result.get("duration") or window) / window
            atempo, ratio_warnings = _atempo_filter(ratio)
            warnings.extend("segment {}: {}".format(item["index"], w) for w in ratio_warnings)
            placed.append({
                "index": item["index"],
                "start": item["start"],
                "end": item["end"],
                "audio": result["path"],
                "atempo": atempo,
                "source_duration": round(float(result.get("duration") or 0.0), 3),
            })

        if not placed:
            return {"ok": False, "error": "no segment could be synthesised",
                    "failures": failed, "plan": plan,
                    "synthetic_voice": True, "disclosure_required": True}

        # ---- build the filter graph -------------------------------------
        parts: List[str] = []
        labels: List[str] = []
        for offset, entry in enumerate(placed):
            label = "d{}".format(offset)
            chain = []
            if entry["atempo"]:
                chain.append(entry["atempo"])
            delay_ms = int(round(float(entry["start"]) * 1000))
            chain.append("adelay={}:all=1".format(delay_ms))
            parts.append("[{}:a]{}[{}]".format(offset + 1, ",".join(chain), label))
            labels.append("[{}]".format(label))
        # Reset timestamps after delaying and mixing.  Without this, FFmpeg can
        # emit the padded audio with timestamps that move backwards; AAC then
        # drops the frames (the output looks valid but is effectively silent).
        dub_chain = "{}amix=inputs={}:normalize=0:dropout_transition=0,apad,asetpts=PTS-STARTPTS[dub]".format(
            "".join(labels), len(labels))
        parts.append(dub_chain)

        if mode == "mix":
            volume = min(max(float(original_volume), 0.0), 1.0)
            parts.append("[0:a]volume={:.3f}[orig]".format(volume))
            parts.append("[orig][dub]amix=inputs=2:normalize=0:dropout_transition=0[mixed]")
            audio_label = "[mixed]"
        else:
            audio_label = "[dub]"

        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
               "-i", clip_path]
        for entry in placed:
            cmd += ["-i", entry["audio"]]
        cmd += [
            "-filter_complex", ";".join(parts),
            "-map", "0:v:0", "-map", audio_label,
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart",
        ]
        if duration > 0:
            cmd += ["-t", "{:.3f}".format(duration)]
        tmp_out = out_path + ".tmp.mp4"
        cmd.append(tmp_out)

        try:
            proc = _run(cmd, timeout=timeout * 10)
        except Exception as exc:
            return {"ok": False, "error": "ffmpeg failed: {}".format(exc),
                    "failures": failed, "plan": plan,
                    "synthetic_voice": True, "disclosure_required": True}
        if proc.returncode != 0 or not os.path.isfile(tmp_out) or os.path.getsize(tmp_out) == 0:
            return {"ok": False,
                    "error": "ffmpeg mux failed: {}".format((proc.stderr or proc.stdout)[-400:]),
                    "failures": failed, "plan": plan,
                    "synthetic_voice": True, "disclosure_required": True}

        # verify before adopting the result (never ship a broken clip)
        try:
            from scripts.media_validation import validate_media_file
            check = validate_media_file(tmp_out, min_duration=0.05, require_audio=True)
        except Exception as exc:
            check = {"ok": False, "error": str(exc)}
        if not check.get("ok"):
            try:
                os.remove(tmp_out)
            except OSError:
                pass
            return {"ok": False,
                    "error": "dubbed output failed validation: {}".format(
                        check.get("error") or check.get("errors")),
                    "failures": failed, "plan": plan,
                    "synthetic_voice": True, "disclosure_required": True}

        # self-check: segments were placed, so the dub must not be silent.
        # (A wrong adelay unit once produced a fully silent track that still
        # passed ffmpeg and media validation — measure, never assume.)
        measured = _mean_volume(out_path=tmp_out)
        if measured is not None and measured < -80.0:
            warnings.append(
                "the dubbed track measures {:.1f} dBFS overall — check the "
                "provider output and the adelay filters".format(measured))

        os.replace(tmp_out, out_path)
        return {
            "ok": True,
            "output": os.path.abspath(out_path),
            "mode": mode,
            "provider": provider,
            "voice": voice,
            "duration": duration,
            "segments_dubbed": len(placed),
            "segments_failed": len(failed),
            "failures": failed,
            "warnings": warnings,
            "mean_volume_db": measured,
            "offsets": [{"index": e["index"], "start": e["start"]} for e in placed],
            "command": cmd,          # kept for auditing/tests
            "synthetic_voice": True,
            "disclosure_required": True,
            "error": None,
        }
    finally:
        if not keep_work:
            shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# Project-level orchestration
# ---------------------------------------------------------------------------

def _clip_targets(project_folder: str, indices: Optional[Sequence[int]] = None):
    """Rendered clips + their subtitle JSON (same index mapping everywhere)."""
    subs_dir = os.path.join(project_folder, "subs")
    targets = []
    for folder in ("final_polished", "final", "cuts"):
        directory = os.path.join(project_folder, folder)
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not name.lower().endswith(".mp4") or name.endswith(".orig.mp4"):
                continue
            base = name.rsplit(".", 1)[0]
            prefix = base.split("_", 1)[0]
            try:
                index = int(prefix)
            except ValueError:
                continue
            if indices is not None and index not in indices:
                continue
            subs = os.path.join(subs_dir, "{}_processed.json".format(base))
            if not os.path.isfile(subs):
                candidates = sorted(
                    path for path in os.listdir(subs_dir)
                    if path.startswith(prefix) and path.endswith(".json")
                ) if os.path.isdir(subs_dir) else []
                subs = os.path.join(subs_dir, candidates[0]) if candidates else ""
            targets.append({"index": index, "clip": os.path.join(directory, name),
                            "subtitles": subs, "source_folder": folder})
        if targets:            # first non-empty source wins (same precedence as publish)
            break
    return targets


def dub_project(project_folder: str, *, provider: str = "edge",
                voice: Optional[str] = None, mode: str = DEFAULT_MODE,
                indices: Optional[Sequence[int]] = None,
                output_dir: Optional[str] = None,
                work_dir: Optional[str] = None, timeout: int = 120,
                allow_network: Optional[bool] = None) -> Dict[str, Any]:
    """Dub every rendered clip that has subtitles. Writes dubbing_report.json."""
    project_folder = os.path.abspath(str(project_folder))
    if not os.path.isdir(project_folder):
        return {"ok": False, "error": "project not found: {}".format(project_folder)}
    destinations = output_dir or os.path.join(project_folder, "final_dubbed")
    os.makedirs(destinations, exist_ok=True)

    targets = _clip_targets(project_folder, indices)
    results: List[Dict[str, Any]] = []
    for target in targets:
        if not target["subtitles"]:
            results.append({"index": target["index"], "ok": False,
                            "error": "no subtitle JSON found", "clip": target["clip"]})
            continue
        out_path = os.path.join(destinations, os.path.basename(target["clip"]))
        if CLIP_SUFFIX not in os.path.basename(out_path):
            stem, ext = os.path.splitext(os.path.basename(target["clip"]))
            out_path = os.path.join(destinations, "{}{}{}".format(stem, CLIP_SUFFIX, ext))
        result = dub_clip(target["clip"], target["subtitles"], out_path,
                          provider=provider, voice=voice, mode=mode,
                          work_dir=work_dir, timeout=timeout,
                          allow_network=allow_network)
        results.append({"index": target["index"], **{k: v for k, v in result.items()
                                                     if k != "command"}})

    report = {
        "ok": bool(results) and all(item.get("ok") for item in results),
        "project": project_folder,
        "provider": provider,
        "voice": voice,
        "mode": mode,
        "clips": len(results),
        "dubbed": sum(1 for item in results if item.get("ok")),
        "failed": sum(1 for item in results if not item.get("ok")),
        "results": results,
        "synthetic_voice": True,
        "disclosure_required": True,
        "note": ("الصوت المُصنَّع يحتاج إفصاحاً عند النشر؛ المقطع المدبلج تحويل جوهري "
                 "لكنه لا يُعد أصلاً جديداً ولا يتجاوز بوابات النشر."),
    }
    path = os.path.join(project_folder, REPORT_NAME)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass
    report["report"] = path
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Dub rendered clips with a translated subtitle track (opt-in).")
    parser.add_argument("--project", default=None,
                        help="project folder (required unless --providers)")
    parser.add_argument("--provider", default="edge", choices=tts_providers.provider_names())
    parser.add_argument("--voice", default=None,
                        help="edge voice name, or a folder of audio files for the 'file' provider")
    parser.add_argument("--mode", default=DEFAULT_MODE, choices=["replace", "mix"])
    parser.add_argument("--index", type=int, nargs="*", default=None,
                        help="only these clip indices (default: all)")
    parser.add_argument("--providers", action="store_true",
                        help="list available TTS providers and exit")
    args = parser.parse_args(argv)

    if args.providers:
        for name in tts_providers.provider_names():
            info = tts_providers.provider_info(name)
            print("{:<8} {}{}".format(name, info["description"],
                                      "  [sends text to an external service]" if info["network"] else ""))
        return 0

    if not args.project:
        parser.error("--project is required (or use --providers)")

    report = dub_project(args.project, provider=args.provider, voice=args.voice,
                         mode=args.mode, indices=args.index)
    print("[dubbing] {}/{} clip(s) dubbed (provider={}, mode={})".format(
        report["dubbed"], report["clips"], args.provider, args.mode))
    for item in report["results"]:
        print("  {} #{}: {}".format("✓" if item.get("ok") else "✗",
                                    item.get("index"), item.get("error") or item.get("output")))
    if report["synthetic_voice"]:
        print("[dubbing] تنبيه: صوت مُصنَّع — فعّل إفصاح المحتوى المُعدّل عند النشر.")
    return 0 if report["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
