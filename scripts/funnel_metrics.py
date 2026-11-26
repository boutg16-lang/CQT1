# -*- coding: utf-8 -*-
"""Per-Short funnel metrics (round 2 item 6b).

Answers, for every successfully published Short of a project:

* Short views (YouTube Analytics, shared window);
* related-video clicks (Analytics ``insightTrafficSourceType == RELATED_VIDEO``
  for the Short itself);
* source-video views BEFORE the Short was published (baseline recorded by the
  publish worker as ``source_views_at_publish``) vs AFTER (live Data API
  ``videos.list?part=statistics``) and the delta between them;
* subscribers gained while the Short was live (Analytics).

The rows feed the WebUI funnel view AND ``performance_weights`` (the funnel
columns become learnable style categories — a hook/topic that consistently
pulls source-video views and subscribers is worth more than views alone).

Every network call is best-effort and injectable for tests: a missing OAuth
token produces an explicit ``unavailable`` reason, never a fake zero. Stdlib
networking only (``urllib``), following ``scripts.verify_youtube``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_TOKEN_PATH = os.path.join(
    os.path.expanduser("~"), ".viralcutter", "youtube", "token.json"
)

DATA_API_BASE = "https://www.googleapis.com/youtube/v3"
ANALYTICS_BASE = "https://youtubeanalytics.googleapis.com/v2"

# Attribution window for "related-video clicks": same rolling window the
# performance loop uses unless the caller pins one.
DEFAULT_WINDOW_DAYS = 28

__all__ = [
    "DEFAULT_TOKEN_PATH",
    "DEFAULT_WINDOW_DAYS",
    "funnel_rows",
    "funnel_score",
    "related_video_clicks",
    "source_video_id",
    "source_view_count",
]


def source_video_id(url: str) -> str:
    """The source video id inside a YouTube URL (``""`` when absent)."""
    from scripts import yt_transcript

    try:
        return str(yt_transcript.video_id_from_url(str(url or "")) or "")
    except Exception:
        return ""


def _access_token(token_path: str = "") -> str:
    """A fresh access token from the uploader's saved OAuth token file."""
    path = str(token_path or "").strip() or DEFAULT_TOKEN_PATH
    if not os.path.isfile(path):
        raise RuntimeError(
            "OAuth token file not found: {}".format(path)
        )
    try:
        from google.oauth2.credentials import Credentials
    except ImportError as exc:  # pragma: no cover - env without google-auth
        raise RuntimeError("google-auth is required for funnel metrics") from exc
    creds = Credentials.from_authorized_user_file(path)
    if not creds.valid:
        import google.auth.transport.requests as transport

        creds.refresh(transport.Request())
    return str(creds.token)


def _api_get_json(url: str, token: str, timeout: int = 30) -> Dict[str, Any]:
    import urllib.request

    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "Authorization": "Bearer " + token},
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace") or "{}")


def source_view_count(video_id: str, token_path: str = "") -> Optional[int]:
    """Live view count of the source video (``None`` when unavailable)."""
    video_id = str(video_id or "").strip()
    if not video_id:
        return None
    try:
        token = _access_token(token_path)
    except Exception as exc:
        logger.warning("funnel: source views unavailable (%s)", str(exc)[:120])
        return None
    url = "{}?part=statistics&{}".format(
        DATA_API_BASE,
        urllib.parse.urlencode({"id": video_id}),
    )
    try:
        payload = _api_get_json(url, token)
    except Exception as exc:
        logger.warning(
            "funnel: source view fetch failed for %s (%s)",
            video_id,
            str(exc)[:120],
        )
        return None
    items = payload.get("items") or []
    if not items:
        return None
    try:
        return int(float((items[0].get("statistics") or {}).get("viewCount")))
    except (TypeError, ValueError):
        return None


def related_video_clicks(
    video_id: str, token_path: str = "", window_days: int = DEFAULT_WINDOW_DAYS
) -> Optional[int]:
    """Views the Short received from the RELATED_VIDEO traffic source.

    This is the closest measurable signal for "related-video clicks" — the
    Analytics API counts a click-through as a view from that traffic source.
    ``None`` means the API did not answer (no OAuth, no data yet), never 0.
    """
    video_id = str(video_id or "").strip()
    if not video_id:
        return None
    try:
        token = _access_token(token_path)
    except Exception as exc:
        logger.warning("funnel: related clicks unavailable (%s)", str(exc)[:120])
        return None
    from scripts import analytics as analytics_mod

    start, end = analytics_mod._date_range(int(window_days or DEFAULT_WINDOW_DAYS))
    url = "{}/reports?{}".format(
        ANALYTICS_BASE,
        urllib.parse.urlencode(
            {
                "ids": "channel==MINE",
                "startDate": start,
                "endDate": end,
                "metrics": "views",
                "dimensions": "video",
                "filters": "video=={};insightTrafficSourceType==RELATED_VIDEO".format(
                    video_id
                ),
            }
        ),
    )
    try:
        payload = _api_get_json(url, token)
    except Exception as exc:
        logger.warning(
            "funnel: related-click query failed for %s (%s)",
            video_id,
            str(exc)[:120],
        )
        return None
    rows = payload.get("rows") or []
    if not rows:
        return None
    try:
        return int(float(rows[0][1]))
    except (IndexError, TypeError, ValueError):
        return None


def funnel_score(row: Dict[str, Any]) -> Optional[float]:
    """0-1 funnel score for one Short (``None`` when nothing is measurable).

    Equal-weight blend of the three funnel signals that exist for the row:
    source-view delta, related clicks and subscribers gained. A Short that
    pulls the channel forward (source views + subs) scores high even when its
    own view count is modest.
    """
    parts: List[float] = []
    for key in ("source_delta", "related_clicks", "subscribers_gained"):
        value = row.get(key)
        if value is None:
            continue
        try:
            parts.append(max(0.0, float(value)))
        except (TypeError, ValueError):
            continue
    if not parts:
        return None
    # Log-ish squash so a single viral outlier cannot pin the scale: each
    # signal maps through 1 - 1/(1 + x/25).
    squashed = [1.0 - 1.0 / (1.0 + value / 25.0) for value in parts]
    return round(sum(squashed) / len(squashed), 3)


def funnel_rows(
    project_folder: str,
    *,
    token_path: str = "",
    window_days: int = DEFAULT_WINDOW_DAYS,
    fetch_metrics: Optional[Callable[[Iterable[str]], Dict[str, Dict[str, Any]]]] = None,
    fetch_source_views: Optional[Callable[[str], Optional[int]]] = None,
    fetch_related: Optional[Callable[[str], Optional[int]]] = None,
) -> Dict[str, Any]:
    """Build the per-Short funnel report for one project. Never raises."""
    from webui import publish_history

    events = [
        event
        for event in (publish_history.load(project_folder) or [])
        if str(event.get("status") or "").strip() == "uploaded"
        and str(event.get("video_id") or "").strip()
    ]
    report: Dict[str, Any] = {
        "window_days": int(window_days or DEFAULT_WINDOW_DAYS),
        "published_count": len(events),
        "rows": [],
        "totals": {},
        "unavailable_reason": "",
    }
    if not events:
        report["unavailable_reason"] = "no successfully uploaded Short with a video id"
        return report

    if fetch_metrics is None:
        def fetch_metrics(video_ids, project_folder=None):  # noqa: F811
            from scripts import performance_loop

            return performance_loop._fetch_real_metrics(
                list(video_ids), project_folder
            )

    if fetch_source_views is None:
        def fetch_source_views(video_id):  # noqa: F811
            return source_view_count(video_id, token_path)

    if fetch_related is None:
        def fetch_related(video_id):  # noqa: F811
            return related_video_clicks(video_id, token_path, window_days)

    video_ids = [str(event["video_id"]).strip() for event in events]
    try:
        metrics = fetch_metrics(video_ids) or {}
    except Exception as exc:
        logger.warning("funnel: metrics fetch failed (%s)", str(exc)[:160])
        metrics = {}

    rows: List[Dict[str, Any]] = []
    for event in events:
        video_id = str(event["video_id"]).strip()
        short_metrics = metrics.get(video_id) or {}
        source_url = str(event.get("source_video_url") or "").strip()
        source_id = str(event.get("source_video_id") or "").strip() or source_video_id(
            source_url
        )
        before_raw = event.get("source_views_at_publish")
        before: Optional[int]
        try:
            before = int(before_raw) if before_raw is not None else None
        except (TypeError, ValueError):
            before = None
        after: Optional[int] = None
        if source_id:
            try:
                after = fetch_source_views(source_id)
            except Exception as exc:  # injected fetchers may raise
                logger.warning(
                    "funnel: source fetch failed for %s (%s)", source_id, str(exc)[:120]
                )
        delta: Optional[int] = None
        if before is not None and after is not None:
            delta = max(0, int(after) - int(before))
        try:
            related = fetch_related(video_id)
        except Exception as exc:
            logger.warning(
                "funnel: related fetch failed for %s (%s)", video_id, str(exc)[:120]
            )
            related = None
        try:
            subs = short_metrics.get("subscribers_gained")
            subs = int(subs) if subs is not None else None
        except (TypeError, ValueError):
            subs = None
        row = {
            "video_id": video_id,
            "title": str(event.get("title") or "").strip(),
            "url": str(event.get("url") or "").strip(),
            "short_views": short_metrics.get("views"),
            "related_clicks": related,
            "source_video_id": source_id,
            "source_before": before,
            "source_after": after,
            "source_delta": delta,
            "subscribers_gained": subs,
        }
        row["funnel_score"] = funnel_score(row)
        row["missing"] = [
            key
            for key in ("short_views", "related_clicks", "source_delta", "subscribers_gained")
            if row.get(key) is None
        ]
        rows.append(row)

    def _sum(key: str) -> Optional[int]:
        values = [row.get(key) for row in rows if row.get(key) is not None]
        return int(sum(values)) if values else None

    report["rows"] = rows
    report["totals"] = {
        "short_views": _sum("short_views"),
        "related_clicks": _sum("related_clicks"),
        "source_delta": _sum("source_delta"),
        "subscribers_gained": _sum("subscribers_gained"),
    }
    # Honest availability note: which pieces could not be measured and why.
    missing = sorted({key for row in rows for key in row["missing"]})
    if missing:
        report["unavailable_reason"] = (
            "not measured in this run: {} (OAuth token at {} must exist and "
            "be valid; source baseline needs an earlier upload)".format(
                ", ".join(missing), token_path or DEFAULT_TOKEN_PATH
            )
        )
    return report
