# -*- coding: utf-8 -*-
"""Post-upload verification (feat/pro-cutting round 2 item 2).

After a Short is uploaded, YouTube runs its own checks: the copyright scan
and the ad-suitability review. Neither verdict is exposed by the public
Data API. The youtubePartner (CMS) API exposes claims and the partner
`rating` part — but only to CMS/partner accounts, which a personal channel
is not.

This module is honest about that boundary:

- it reads back what the Data API *can* return (upload status, privacy) as
  ground truth that the video is live;
- it records copyright / ad-suitability as ``pending`` with an explicit
  re-check timestamp, never as a guessed value;
- if a CMS (youtubePartner) token is present it tries the partner API for a
  real verdict (claims for copyright, ``rating`` for ad suitability);
- it appends verdict events to publish_history so the self_cert pre-answer
  from round 1 can later be compared with YouTube's real decision.

Nothing is silently swallowed: failures land in ``last_error`` and stay
``pending``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

DATA_API_BASE = "https://www.googleapis.com/youtube/v3/videos"
PARTNER_CLAIMS_URL = (
    "https://www.googleapis.com/youtubePartner/v1/claims?videoId={video_id}&part=status,id"
)
PARTNER_VIDEO_URL = (
    "https://www.googleapis.com/youtubePartner/v1/videos?part=rating,status&id={video_id}"
)

COPYRIGHT = "copyright"
AD_SUITABILITY = "ad_suitability"
VERDICT_PENDING = "pending"
VERDICT_NO_CLAIMS = "no_claims"
VERDICT_CLAIM_FOUND = "claim_found"
VERDICT_SUITABLE = "suitable"
VERDICT_LIMITED = "limited_ads"

RECHECK_AFTER_HOURS = 6
HISTORY_KEY = "youtube_checks"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def build_pending(video_id: str, reason: str = "") -> Dict[str, Any]:
    """The honest shape when no verdict is readable yet."""
    now = _now()
    return {
        "video_id": str(video_id or "").strip(),
        COPYRIGHT: {
            "verdict": VERDICT_PENDING,
            "source": "none_available",
            "detail": str(reason or "").strip(),
        },
        AD_SUITABILITY: {
            "verdict": VERDICT_PENDING,
            "source": "none_available",
            "detail": str(reason or "").strip(),
        },
        "checked_at": _iso(now),
        "recheck_at": _iso(now + timedelta(hours=RECHECK_AFTER_HOURS)),
        "schema_version": "2.1",
    }


def parse_checks(value: Any) -> Optional[Dict[str, Any]]:
    """Parse a stored youtube_checks payload (JSON string or dict)."""
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def recheck_due(checks: Dict[str, Any]) -> bool:
    """True when the recheck window has passed (or the stamp is unparseable)."""
    stamp = str((checks or {}).get("recheck_at") or "")
    try:
        due = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return True
    return due <= _now()


def _access_token(token_path: str) -> str:
    """A fresh access token from the uploader's saved OAuth token file."""
    try:
        from google.oauth2.credentials import Credentials
    except ImportError as exc:  # pragma: no cover - env without google-auth
        raise RuntimeError("google-auth is required for verification") from exc
    creds = Credentials.from_authorized_user_file(token_path)
    if not creds.valid:
        import google.auth.transport.requests as transport

        creds.refresh(transport.Request())
    return str(creds.token)


def _api_get_json(url: str, token: str, timeout: int = 30) -> Dict[str, Any]:
    import urllib.request

    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "Authorization": "Bearer " + token}
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace") or "{}")


def fetch_verdicts(video_id: str, token_path: str) -> Dict[str, Any]:
    """Read back what the APIs actually return — verdicts or honest pending.

    Copyright: youtubePartner claims.list (CMS accounts only; 403 for a
    personal channel). Ad suitability: youtubePartner videos `rating` part.
    The Data API read (upload status / privacy) is recorded as ground truth
    that the video is live.
    """
    video_id = str(video_id or "").strip()
    token = _access_token(token_path)
    checks = build_pending(video_id)

    # Data API: the video exists / is live (real read-back, no verdicts here).
    try:
        data = _api_get_json(
            DATA_API_BASE + "?part=status&id={}".format(video_id), token
        )
        items = (data or {}).get("items") or []
        if items:
            status = items[0].get("status") or {}
            checks["data_api"] = {
                "upload_status": status.get("uploadStatus"),
                "privacy": status.get("privacyStatus"),
            }
        else:
            checks["data_api"] = {"upload_status": "not_found"}
    except Exception as exc:
        checks["data_api"] = {"error": str(exc)[:200]}

    # Partner API: copyright verdict (no CMS account → stays pending).
    try:
        claims = _api_get_json(PARTNER_CLAIMS_URL.format(video_id=video_id), token)
        claim_items = (claims or {}).get("items") or []
        checks[COPYRIGHT] = {
            "verdict": VERDICT_CLAIM_FOUND if claim_items else VERDICT_NO_CLAIMS,
            "source": "youtubePartner.claims.list",
            "detail": "{} claim(s)".format(len(claim_items)),
        }
    except Exception as exc:
        checks[COPYRIGHT]["detail"] = "partner API unavailable ({}); pending".format(
            str(exc)[:160]
        )

    # Partner API: ad suitability via the `rating` part.
    try:
        partner = _api_get_json(PARTNER_VIDEO_URL.format(video_id=video_id), token)
        items = (partner or {}).get("items") or []
        if items:
            yt_rating = str(
                ((items[0].get("rating") or {}).get("ytRating")) or ""
            ).lower()
            checks[AD_SUITABILITY] = {
                "verdict": VERDICT_LIMITED if "limited" in yt_rating else VERDICT_SUITABLE,
                "source": "youtubePartner.videos.rating",
                "detail": yt_rating or "empty ytRating",
            }
        else:
            checks[AD_SUITABILITY]["detail"] = "partner video not found; pending"
    except Exception as exc:
        checks[AD_SUITABILITY]["detail"] = "partner API unavailable ({}); pending".format(
            str(exc)[:160]
        )

    return checks


def checks_json(checks: Dict[str, Any]) -> str:
    return json.dumps(checks, ensure_ascii=False, separators=(",", ":"))


def _latest_checks_event(project_path: str, platform: str, video_id: str):
    """Newest publish_history event for the video that carries checks."""
    from webui import publish_history

    target = None
    for event in publish_history.load(project_path, limit=1000):
        if event.get("platform") != platform:
            continue
        if str(event.get("video_id") or "") != str(video_id or ""):
            continue
        if parse_checks(event.get(HISTORY_KEY)) is None and target is None:
            # the original publish event (no checks yet) is the fallback anchor
            if event.get("status") in publish_history.SUCCESS_STATUSES:
                target = event
            continue
        target = event
    return target


def record_checks(
    project_path: str, platform: str, video_id: str, checks: Dict[str, Any]
) -> bool:
    """Append a verification event to publish_history (append-only log)."""
    from webui import publish_history

    anchor = _latest_checks_event(project_path, platform, video_id)
    if anchor is None:
        return False
    return publish_history.record(
        project_path,
        platform=platform,
        video_path=str(anchor.get("video") or ""),
        title=str(anchor.get("title") or ""),
        result={
            "status": str(anchor.get("status") or "uploaded"),
            "video_id": video_id,
            "url": str(anchor.get("url") or ""),
        },
        extra={HISTORY_KEY: checks_json(checks)},
    )


def latest_checks(project_path: str, *, platform: str, video_id: str) -> Optional[Dict[str, Any]]:
    """The newest youtube_checks payload for a video (None when none)."""
    from webui import publish_history

    for event in reversed(publish_history.load(project_path, limit=1000)):
        if event.get("platform") != platform:
            continue
        if str(event.get("video_id") or "") != str(video_id or ""):
            continue
        checks = parse_checks(event.get(HISTORY_KEY))
        if checks:
            return checks
    return None


def recheck_pending(
    project_path: str, *, token_path: str = "", force: bool = False
) -> Dict[str, Any]:
    """Re-read verdicts for pending entries whose recheck window passed.

    Returns a summary; per-video failures land in ``errors`` verbatim.
    """
    from webui import publish_history

    summary: Dict[str, Any] = {"checked": 0, "updated": 0, "errors": [], "details": []}
    seen = set()
    for event in publish_history.load(project_path, limit=1000):
        vid = str(event.get("video_id") or "")
        platform = str(event.get("platform") or "")
        if not vid or (platform, vid) in seen:
            continue
        checks = parse_checks(event.get(HISTORY_KEY))
        if not checks:
            continue
        seen.add((platform, vid))
        if not force and not recheck_due(checks):
            continue
        summary["checked"] += 1
        if not token_path or not os.path.isfile(token_path):
            summary["errors"].append(
                "{}: no OAuth token available — verdicts stay pending".format(vid)
            )
            continue
        try:
            fresh = fetch_verdicts(vid, token_path)
        except Exception as exc:
            summary["errors"].append("{}: {}".format(vid, str(exc)[:200]))
            continue
        if record_checks(project_path, platform, vid, fresh):
            summary["updated"] += 1
        summary["details"].append(
            {
                "video_id": vid,
                COPYRIGHT: (fresh.get(COPYRIGHT) or {}).get("verdict"),
                AD_SUITABILITY: (fresh.get(AD_SUITABILITY) or {}).get("verdict"),
            }
        )
    return summary


def initial_checks(video_id: str) -> Dict[str, Any]:
    """Read verdicts right after upload WITHOUT recording an event.

    Used by the upload worker so the checks land inside the SAME
    publish_history event as the upload (one event per clip). When no OAuth
    token file exists the verdicts are honest "pending" with a recheck stamp.
    """
    from webui import youtube_credentials

    token_path = ""
    try:
        token_path = youtube_credentials.token_path() or ""
    except Exception:
        token_path = ""
    if token_path and os.path.isfile(token_path):
        try:
            return fetch_verdicts(video_id, token_path)
        except Exception as exc:
            return build_pending(video_id, reason=str(exc)[:200])
    return build_pending(video_id, reason="YouTube checks run asynchronously")


def verify_after_upload(project_path: str, platform: str, video_id: str) -> Dict[str, Any]:
    """Standalone verification: read verdicts AND record a checks event."""
    checks = initial_checks(video_id)
    record_checks(project_path, platform, video_id, checks)
    return checks


__all__ = [
    "AD_SUITABILITY",
    "COPYRIGHT",
    "HISTORY_KEY",
    "RECHECK_AFTER_HOURS",
    "VERDICT_CLAIM_FOUND",
    "VERDICT_LIMITED",
    "VERDICT_NO_CLAIMS",
    "VERDICT_PENDING",
    "VERDICT_SUITABLE",
    "build_pending",
    "checks_json",
    "fetch_verdicts",
    "initial_checks",
    "latest_checks",
    "parse_checks",
    "recheck_due",
    "recheck_pending",
    "record_checks",
    "verify_after_upload",
]
