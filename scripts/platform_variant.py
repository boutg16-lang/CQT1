# -*- coding: utf-8 -*-
"""Cross-platform republish planning with an original-contribution gate.

Publishing the same rendered clip on YouTube Shorts and then on TikTok can be
legitimate only when the target copy has a meaningful original contribution.
Byte-identical or lightly edited re-uploads are not made compliant by changing
speed, mirroring, cropping, or color alone.

When the project documents commentary, voiceover, or B-roll, this module can
make the target-platform copy editorially different using the deterministic
seeded presets already shipped in
``scripts/originality.py``: micro speed shift, horizontal mirror, crop-offset
jitter and a micro color grade. The seed is derived from the clip path +
target platform + number of previous publishes, so:

* the same clip republished to two platforms gets two different variants;
* re-running the pipeline is reproducible for the same inputs;
* a *third* attempt at the same target gets a fresh variant instead of
  silently re-uploading the same bytes.

A missing contribution produces a manual-review decision; an ffmpeg failure
falls back to the original only after that compliance gate has passed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from typing import Any

try:
    from webui import publish_history  # noqa: F401  (repo-root runs)
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from webui import publish_history  # noqa: F401

VARIANT_POLICIES = ("off", "auto", "always")
# Platforms whose feeds aggressively throttle re-uploads of identical bytes.
VARIANT_PLATFORMS = {"tiktok", "instagram", "reels"}
# Distinctness verification (v7.33.3): a variant whose perceptual similarity
# to the original stays above this is not "a different copy" yet - retry with
# another seed, up to VERIFY_ATTEMPTS total renders.
VERIFY_MAX_SIMILARITY = 0.50
VERIFY_ATTEMPTS = 4


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_platform(platform: str | None) -> str:
    return str(platform or "").strip().lower()


def is_variant_platform(platform: str | None) -> bool:
    return normalize_platform(platform) in VARIANT_PLATFORMS


def _clip_index(video_path: str) -> int | None:
    stem = os.path.splitext(os.path.basename(str(video_path or "")))[0]
    match = re.match(r"^(\d{1,4})(?:_|$)", stem)
    return int(match.group(1)) if match else None


def _original_contribution(project_path: str, video_path: str) -> dict[str, Any]:
    """Return whether the project documents a meaningful original contribution."""
    index = _clip_index(video_path)
    manifest_path = os.path.join(str(project_path), "project_manifest.json")
    try:
        with open(manifest_path, "r", encoding="utf-8") as stream:
            manifest = json.load(stream)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        manifest = {}
    transformation = manifest.get("transformation") if isinstance(manifest, dict) else {}
    if isinstance(transformation, dict) and isinstance(transformation.get("clips"), dict):
        clips = transformation["clips"]
        transformation = clips.get(str(index), clips.get(index, {}))
    if not isinstance(transformation, dict):
        transformation = {}
    signals = {
        "commentary": bool(transformation.get("commentary") or transformation.get("original_analysis")),
        "voiceover": bool(transformation.get("voiceover") or transformation.get("voiceover_path")),
        "broll": bool(transformation.get("broll")),
    }
    if any(signals.values()):
        return {
            "meaningful": True,
            "index": index,
            "signals": [name for name, present in signals.items() if present],
            "source": "project_manifest",
            "reason": "documented original commentary, voiceover, or B-roll",
        }

    report_path = os.path.join(str(project_path), "provenance_report.json")
    try:
        with open(report_path, "r", encoding="utf-8") as stream:
            report = json.load(stream)
        entries = report.get("clips", []) if isinstance(report, dict) else []
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("index") != index:
                continue
            evidence = entry.get("transformation") or {}
            meaningful = evidence.get("status") == "meaningful" or evidence.get("action") == "allow"
            if meaningful:
                return {
                    "meaningful": True,
                    "index": index,
                    "signals": [item.get("name") for item in evidence.get("evidence", [])
                                if isinstance(item, dict) and item.get("name")],
                    "source": "provenance_report",
                    "reason": "provenance report confirms meaningful editorial contribution",
                }
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass

    return {
        "meaningful": False,
        "index": index,
        "signals": [],
        "source": None,
        "reason": "⛔ أضف تعليقاً أصلياً أو تعليقاً صوتياً أو تحليلاً تحريرياً قبل إعادة النشر؛ تغيير السرعة أو المرآة أو القص وحده لا يكفي.",
    }


def _load_history(project_path: str) -> list[dict[str, Any]]:
    try:
        from webui import publish_history
        return publish_history.load(project_path, limit=2000)
    except Exception:
        return []


def prior_other_platform_publishes(project_path: str, video_path: str,
                                   target_platform: str,
                                   events: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Prior successful publishes of this same clip on *other* platforms.

    Match on either the file fingerprint (rendered bytes) or the clip file
    name (same source window index). YouTube-Success followed by a TikTok
    publish of the same clip is the exact scenario this module addresses.
    """
    target = normalize_platform(target_platform)
    if events is None:
        events = _load_history(project_path)
    base_name = os.path.basename(str(video_path or ""))
    fingerprint = None
    if video_path and os.path.isfile(video_path):
        try:
            from webui import publish_history
            fingerprint = publish_history.file_fingerprint(video_path)
        except Exception:
            fingerprint = None
    matches = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        platform = normalize_platform(event.get("platform"))
        if platform == target or platform not in VARIANT_PLATFORMS | {"youtube", "yt_shorts", "shorts"}:
            continue
        if str(event.get("status") or "") not in {"uploaded", "scheduled"}:
            continue
        same_file = fingerprint is not None and event.get("file_fingerprint") == fingerprint
        same_clip = base_name and (
            event.get("video") == base_name or event.get("variant_of") == base_name)
        if same_file or same_clip:
            matches.append(event)
    return matches


def _previous_target_attempts(project_path: str, video_path: str,
                              target_platform: str,
                              events: list[dict[str, Any]] | None = None) -> int:
    """How many successful publishes this exact clip already had on target."""
    target = normalize_platform(target_platform)
    if events is None:
        events = _load_history(project_path)
    base_name = os.path.basename(str(video_path or ""))
    count = 0
    for event in events or []:
        if not isinstance(event, dict):
            continue
        if (normalize_platform(event.get("platform")) == target
                and str(event.get("status") or "") in {"uploaded", "scheduled"}
                and (event.get("video") == base_name or event.get("variant_of") == base_name)):
            count += 1
    return count


def choose_seed(project_path: str, video_path: str, target_platform: str,
                attempt: int = 1) -> int:
    """Deterministic seed for a (clip, platform, attempt) combination."""
    raw = "|".join((os.path.abspath(str(video_path or "")),
                    normalize_platform(target_platform), str(int(attempt))))
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def plan_variant(project_path: str, video_path: str, target_platform: str,
                 policy: str = "auto",
                 events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Pure decision: should this clip get a platform-diversified copy?

    Never touches the disk. Returns a decision dict with the chosen seed and
    preset when ``action == "variate"`` so callers can preview (dry-run) or
    apply the transform themselves.
    """
    target = normalize_platform(target_platform)
    base = {
        "path": video_path, "action": "none", "platform": target,
        "policy": str(policy or "auto").strip().lower(), "seed": None,
        "transforms": [], "checked_at": _now(),
    }
    if not is_variant_platform(target):
        base["reason"] = "platform {} does not need cross-platform variance".format(target or "?")
        return base
    if not video_path or not os.path.isfile(video_path):
        base["reason"] = "clip file not found"
        return base
    policy = base["policy"]
    if policy not in VARIANT_POLICIES:
        base["reason"] = "unknown variant_policy {!r} (use off|auto|always)".format(policy)
        return base
    if policy == "off":
        base["reason"] = "variant_policy=off"
        return base

    siblings = prior_other_platform_publishes(project_path, video_path, target, events)
    if policy == "auto" and not siblings:
        base["reason"] = "no prior publish of this clip on another platform — original is fine"
        return base
    contribution = _original_contribution(project_path, video_path)
    base["contribution"] = contribution
    if not contribution["meaningful"]:
        base["action"] = "review"
        base["requires_original_contribution"] = True
        base["reason"] = contribution["reason"]
        return base
    attempt = 1 + _previous_target_attempts(project_path, video_path, target, events)
    seed = choose_seed(project_path, video_path, target, attempt)
    try:
        from scripts import originality
        preset = originality.build_preset(seed)
    except Exception as error:  # pragma: no cover - optional dependency
        base["reason"] = "preset builder unavailable ({}); uploading the original".format(error)
        return base
    reason = ("republishing a clip already published on {} — diversifying the copy".format(
        ", ".join(sorted({normalize_platform(item.get("platform")) for item in siblings}))) if siblings
        else "variant_policy=always — platform-appropriate copy for {}".format(target))
    base.update({
        "action": "variate", "reason": reason, "seed": seed, "preset": preset,
        "siblings": len(siblings), "attempt": attempt,
    })
    return base


def maybe_variant(project_path: str, video_path: str, target_platform: str,
                  policy: str = "auto", ffmpeg: str = "ffmpeg",
                  variant_dir: str | None = None,
                  events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Apply ``plan_variant`` when needed and render the variant file.

    Since v7.33.3 the render is **verified distinct**: after transforming,
    the variant is visually compared with the original clip (perceptual
    d-hash fingerprint). While the variant is still too similar, new seeds
    are tried (up to ``VERIFY_ATTEMPTS``); the first sufficiently distinct
    copy wins. Verification needs OpenCV — when it is unavailable the first
    variant is accepted as best-effort (never a hard block).

    Non-fatal overall: a transform failure falls back to the original path
    with an explanatory ``reason``.
    """
    decision = plan_variant(project_path, video_path, target_platform, policy, events)
    if decision["action"] != "variate":
        return decision
    try:
        from scripts import originality
        directory = variant_dir or os.path.join(str(project_path), "variants")
        os.makedirs(directory, exist_ok=True)
        stem, ext = os.path.splitext(os.path.basename(str(video_path)))
        base_seed = int(decision["seed"])
        verify = str(os.getenv("VIRALCUTTER_VARIANT_VERIFY", "1")).strip().lower() not in {
            "0", "false", "no", "off"}
        best_effort = None
        for attempt_offset in range(VERIFY_ATTEMPTS):
            seed = base_seed + attempt_offset
            output_path = os.path.join(directory, "{}__{}_{}.mp4".format(
                stem, target_platform, seed))
            result = originality.transform_with_seed(
                str(video_path), output_path, seed=seed,
                preset=decision.get("preset") if attempt_offset == 0
                else originality.build_preset(seed),
                ffmpeg=ffmpeg)
            if not result.get("ok") or not os.path.isfile(output_path):
                continue
            best_effort = {
                "path": output_path, "seed": seed,
                "transforms": result.get("transforms") or [], "similarity": None,
            }
            if not verify:
                break
            try:
                comparison = originality.compare_clips(str(video_path), output_path)
            except Exception:
                comparison = {}
            similarity = comparison.get("similarity")
            best_effort["similarity"] = similarity
            if similarity is None or float(similarity) <= VERIFY_MAX_SIMILARITY:
                break  # distinct enough (or unverifiable -> best effort)
        if best_effort is None:
            decision["reason"] = "variant transform reported failure; uploading the original"
            decision["action"] = "none"
            decision["path"] = video_path
            return decision
        decision["path"] = best_effort["path"]
        decision["seed"] = best_effort["seed"]
        decision["transforms"] = best_effort["transforms"]
        if best_effort.get("similarity") is not None:
            decision["similarity"] = float(best_effort["similarity"])
            if float(best_effort["similarity"]) > VERIFY_MAX_SIMILARITY:
                decision["reason"] = ("best-effort variant: visual similarity {:.2f} after {} "
                                      "attempts (could not get below {:.2f}) — review before "
                                      "publishing".format(best_effort["similarity"],
                                                          VERIFY_ATTEMPTS,
                                                          VERIFY_MAX_SIMILARITY))
        return decision
    except Exception as error:  # pragma: no cover - depends on local ffmpeg
        decision["reason"] = "variant transform unavailable ({}); uploading the original".format(error)
        decision["action"] = "none"
        decision["path"] = video_path
        return decision
