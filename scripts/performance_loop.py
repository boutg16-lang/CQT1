"""Close the performance loop: link real YouTube outcomes to clip features.

Reads publish_history.jsonl (which clips were uploaded, with their video IDs
and the segment features frozen at publish time), merges in real YouTube
Analytics numbers, and produces `performance_insights.json` + a human-readable
console summary: which clip traits actually correlate with views and retention.
Pure stdlib + optional googleapiclient — never mutates any pipeline decision.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import statistics
import tempfile
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

HISTORY_NAME = "publish_history.jsonl"
REPORT_NAME = "performance_insights.json"
MARKDOWN_NAME = "performance_insights.md"
DUE_NAME = "performance_refresh_due.json"

# All clips are measured over the SAME recent window. A 365-day total gives
# an old clip 300 days of views and a new one 10 — the "learning" then mostly
# learns clip age. 28 days matches YouTube's own recent-performance view and
# is the default; clamp to a sane range.
DEFAULT_WINDOW_DAYS = 28
MIN_WINDOW_DAYS = 7
MAX_WINDOW_DAYS = 365

# Segment features copied into publish events (see publish_panel.record).
FEATURE_KEYS = (
    "duration", "hook_strength", "narrative_completeness", "clarity_score",
    "novelty_score", "selection_score", "title_quality_score",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _window_days() -> int:
    try:
        value = int(float(os.getenv("VIRALCUTTER_PERF_WINDOW_DAYS", "")))
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_DAYS
    return max(MIN_WINDOW_DAYS, min(MAX_WINDOW_DAYS, value))


def _read_history(project_folder: str) -> list[dict[str, Any]]:
    path = os.path.join(project_folder, HISTORY_NAME)
    events = []
    if not os.path.exists(path):
        return events
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except (ValueError, TypeError, json.JSONDecodeError):
                    continue
                if isinstance(item, dict):
                    events.append(item)
    except OSError:
        return []
    return events


def _publish_hour(value: Any) -> int | None:
    """Local hour (0-23) of a publish timestamp, for schedule correlation."""
    try:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        moment = datetime.fromisoformat(text)
        return int(moment.astimezone().hour)
    except (ValueError, TypeError, OverflowError):
        return None


def _published_videos(project_folder: str) -> list[dict[str, Any]]:
    """Published events enriched with segment features from the scorecard."""
    segments = {}
    try:
        with open(os.path.join(project_folder, "risk_scorecard.json"), "r", encoding="utf-8") as fh:
            scorecard = json.load(fh)
        for entry in scorecard.get("segments", []) or []:
            if isinstance(entry, dict):
                segments[entry.get("index")] = entry
    except FileNotFoundError:
        logger.debug("No risk scorecard is available for performance enrichment.")
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError) as exc:
        logger.warning("Could not read risk scorecard (%s); using publish history only.",
                       type(exc).__name__)
    published = []
    for event in _read_history(project_folder):
        video_id = event.get("video_id")
        if not video_id or event.get("status") not in {"uploaded", "scheduled"}:
            continue
        entry = {}
        match = re.search(r"#?(\d+)", str(event.get("video", "")))
        if match:
            entry = segments.get(int(match.group(1)), {}) or {}
        features = {key: entry.get(key) for key in FEATURE_KEYS if entry.get(key) is not None}
        hour = _publish_hour(event.get("publish_at") or event.get("timestamp"))
        if hour is not None:
            features["publish_hour"] = hour
        published.append({
            "video_id": video_id,
            "title": event.get("title", ""),
            "platform": event.get("platform", "youtube"),
            "published_at": event.get("timestamp"),
            "features": features,
            # Content markers recorded since v7.33.1 (publish_history.record
            # extra): hashtags/topic/angle/hook_type + derived title styles.
            "content": _content_markers(event),
        })
    return published


_QUESTION_SUFFIXES = ("?", "؟")
_ARABIC_HOOK_WORDS = ("كيف", "كيفاش", "علاش", "شنو", "لماذا", "طريقة",
                      "أفضل", "سر", "خطأ", "جديد", "شحال")
_LATIN_HOOK_WORDS = ("how", "why", "best", "secret", "mistake", "trick",
                     "tips", "top")


def _content_markers(event: dict[str, Any]) -> dict[str, Any]:
    """Structured content markers of a publish event (empty-safe)."""
    title = str(event.get("title") or "").strip()
    lowered = title.casefold()
    question = any(ch in title for ch in ("؟", "?"))
    has_number = any(ch.isdigit() for ch in title)
    hook_word = any(w in lowered for w in _LATIN_HOOK_WORDS) or any(
        w in title for w in _ARABIC_HOOK_WORDS)
    hashtags_raw = str(event.get("hashtags") or "").strip()
    hashtag_count = len([h for h in re.split(r"[,;\s]+", hashtags_raw)
                         if h.strip().lstrip("#")]) if hashtags_raw else 0
    return {
        "topic": str(event.get("topic") or "").strip(),
        "angle": str(event.get("angle") or "").strip(),
        "hook_type": str(event.get("hook_type") or "").strip(),
        "hashtag_count": hashtag_count,
        "title_question": bool(question),
        "title_number": bool(has_number),
        "title_hook_word": bool(hook_word),
    }


def _fetch_real_metrics(video_ids: list[str]) -> dict[str, dict[str, float]]:
    """Pull views/retention via scripts.analytics. Empty when unavailable."""
    try:
        from scripts import analytics as analytics_mod
        ya, yt = analytics_mod._build_services()
    except Exception:
        return {}
    metrics: dict[str, dict[str, float]] = {}
    start, end = analytics_mod._date_range(_window_days())
    for index in range(0, len(video_ids), 40):
        chunk = video_ids[index:index + 40]
        filters = "video==" + ";".join(chunk)
        try:
            resp = ya.reports().query(
                ids="channel==MINE", startDate=start, endDate=end,
                metrics=("views,averageViewDuration,averageViewPercentage,"
                         "likes,shares,subscribersGained"),
                dimensions="video", filters=filters,
                maxResults=40,
            ).execute()
        except Exception:
            continue
        for row in resp.get("rows", []) or []:
            row = list(row) + [None] * 6
            vid, views, avg_dur, retention, likes, shares, subs = row[:7]
            metrics[vid] = {
                "views": int(views or 0),
                "avg_view_duration": round(float(avg_dur or 0), 1),
                "retention": round(float(retention or 0), 2),
                "likes": int(likes or 0),
                "shares": int(shares or 0),
                "subscribers_gained": int(subs or 0),
            }
    return metrics


def _pearson(points: list[tuple[float, float]]) -> float | None:
    if len(points) < 3:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    denom = (sum(a * a for a in dx) * sum(b * b for b in dy)) ** 0.5
    if not denom:
        return None
    return round(sum(a * b for a, b in zip(dx, dy, strict=False)) / denom, 3)


def _ranks(values: list[float]) -> list[float]:
    """Average ranks (ties share the mean rank)."""
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index
        while (end + 1 < len(order)
               and values[order[end + 1]] == values[order[index]]):
            end += 1
        rank = (index + end) / 2.0 + 1.0
        for position in range(index, end + 1):
            ranks[order[position]] = rank
        index = end + 1
    return ranks


def _spearman(pairs: list[tuple[float, float]]) -> float | None:
    """Rank correlation — resistant to one viral clip dominating the sample.

    Pearson on raw views lets a single 1M-view outlier flip the sign of a
    feature that is neutral everywhere else. Rank correlation asks the
    question the editor actually cares about: "do higher-scoring clips tend
    to rank higher?", not "does one clip dwarf the rest?".
    """
    points = [(float(x), float(y)) for x, y in pairs
              if x is not None and y is not None]
    if len(points) < 3:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return _pearson(list(zip(_ranks(xs), _ranks(ys), strict=False)))


def _corr(pairs: list[tuple[float, float]]) -> float | None:
    """Backward-compatible name: correlation is Spearman from 8.4 on."""
    return _spearman(pairs)


def _strength(value: float | None) -> str:
    if value is None:
        return "insufficient_data"
    magnitude = abs(value)
    if magnitude >= 0.7:
        return "strong"
    if magnitude >= 0.4:
        return "moderate"
    if magnitude >= 0.2:
        return "weak"
    return "negligible"


def _best_hours(measured: list[dict[str, Any]], *, min_samples: int = 2,
                top: int = 3) -> list[int]:
    """Hours whose clips averaged the most views (needs min_samples each)."""
    by_hour: dict[int, list[float]] = {}
    for item in measured:
        hour = item.get("features", {}).get("publish_hour")
        views = item.get("metrics", {}).get("views")
        if hour is None or views is None:
            continue
        by_hour.setdefault(int(hour), []).append(float(views))
    scored = [(sum(v) / len(v), h) for h, v in by_hour.items()
              if len(v) >= min_samples]
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [h for _mean, h in scored[:top]]


def _overall_avg_views(measured: list[dict[str, Any]]) -> float | None:
    values = [float(item["metrics"]["views"])
              for item in measured if (item.get("metrics") or {}).get("views") is not None]
    return round(statistics.fmean(values), 1) if values else None


def _overall_avg_retention(measured: list[dict[str, Any]]) -> float | None:
    values = [float(item["metrics"]["retention"])
              for item in measured
              if (item.get("metrics") or {}).get("retention") is not None]
    return round(statistics.fmean(values), 2) if values else None


def _category_performance(measured: list[dict[str, Any]], field: str,
                          *, min_samples: int = 2) -> list[dict[str, Any]]:
    """Per-value avg views + delta vs the overall average for one marker."""
    overall = _overall_avg_views(measured)
    if overall is None:
        return []
    overall_retention = _overall_avg_retention(measured)
    buckets: dict[str, list[float]] = {}
    retention_buckets: dict[str, list[float]] = {}
    for item in measured:
        value = str((item.get("content") or {}).get(field) or "").strip()
        if not value:
            continue
        views = (item.get("metrics") or {}).get("views")
        if views is not None:
            buckets.setdefault(value, []).append(float(views))
        retention = (item.get("metrics") or {}).get("retention")
        if retention is not None:
            retention_buckets.setdefault(value, []).append(float(retention))
    results = []
    for value, values in buckets.items():
        if len(values) < min_samples:
            continue
        average = statistics.fmean(values)
        delta_pct = round((average - overall) / overall * 100.0, 1) if overall else 0.0
        row = {"value": value, "samples": len(values),
               "avg_views": round(average, 1), "delta_pct": delta_pct}
        retention_values = retention_buckets.get(value) or []
        if retention_values and overall_retention:
            retention_avg = statistics.fmean(retention_values)
            row["avg_retention"] = round(retention_avg, 2)
            row["delta_retention_pct"] = round(
                (retention_avg - overall_retention) / overall_retention * 100.0, 1)
        results.append(row)
    # Winners first for display; the absolute delta is still visible.
    results.sort(key=lambda item: (-item["delta_pct"], item["value"]))
    return results


def _style_performance(measured: list[dict[str, Any]], field: str,
                       *, min_samples: int = 2) -> dict[str, Any] | None:
    """Compare avg views when a boolean title style is present vs absent."""
    yes, no = [], []
    for item in measured:
        views = (item.get("metrics") or {}).get("views")
        if views is None:
            continue
        bucket = yes if (item.get("content") or {}).get(field) else no
        bucket.append(float(views))
    if len(yes) < min_samples or len(no) < min_samples:
        return None
    avg_yes = statistics.fmean(yes)
    avg_no = statistics.fmean(no)
    delta_pct = round((avg_yes - avg_no) / avg_no * 100.0, 1) if avg_no else 0.0
    row = {"style": field, "samples_yes": len(yes), "samples_no": len(no),
           "avg_views_yes": round(avg_yes, 1), "avg_views_no": round(avg_no, 1),
           "delta_pct": delta_pct}
    retention_yes = [float(item["metrics"]["retention"]) for item in measured
                     if (item.get("content") or {}).get(field)
                     and (item.get("metrics") or {}).get("retention") is not None]
    retention_no = [float(item["metrics"]["retention"]) for item in measured
                    if not (item.get("content") or {}).get(field)
                    and (item.get("metrics") or {}).get("retention") is not None]
    if retention_yes and retention_no:
        avg_r_yes = statistics.fmean(retention_yes)
        avg_r_no = statistics.fmean(retention_no)
        row["avg_retention_yes"] = round(avg_r_yes, 2)
        row["avg_retention_no"] = round(avg_r_no, 2)
        row["delta_retention_pct"] = round(
            (avg_r_yes - avg_r_no) / avg_r_no * 100.0, 1) if avg_r_no else 0.0
    return row


def _content_insights(measured: list[dict[str, Any]]) -> dict[str, Any]:
    """Learn which content markers (topics/angles/hook styles) perform."""
    result: dict[str, Any] = {"overall_avg_views": _overall_avg_views(measured)}
    categories = {}
    for field in ("hook_type", "angle", "topic"):
        rows = _category_performance(measured, field)
        if rows:
            categories[field] = rows
    if categories:
        result["categories"] = categories
    styles = []
    for field in ("title_question", "title_number", "title_hook_word"):
        row = _style_performance(measured, field)
        if row:
            styles.append(row)
    if styles:
        styles.sort(key=lambda row: -abs(row["delta_pct"]))
        result["title_styles"] = styles
    pairs = [(float((item.get("content") or {}).get("hashtag_count"))
              if (item.get("content") or {}).get("hashtag_count") is not None else None,
              float(item["metrics"]["views"]))
             for item in measured if (item.get("metrics") or {}).get("views") is not None]
    hashtag_r = _corr(pairs)
    if hashtag_r is not None:
        result["hashtag_count_correlation"] = {
            "r": hashtag_r, "strength": _strength(hashtag_r)}
    return result


def _style_label(field: str) -> str:
    return {
        "title_question": "question titles (ending ?/؟)",
        "title_number": "titles containing a number",
        "title_hook_word": "titles with a hook word (كيف/لماذا/how/why/…)",
    }.get(field, field)


def analyze(project_folder: str, fetch_live: bool = True) -> dict[str, Any]:
    """Build the performance-loop report. Never raises."""
    project_folder = os.path.abspath(os.fspath(project_folder))
    published = _published_videos(project_folder)
    report: dict[str, Any] = {
        "generated_at": _now(),
        "project": project_folder,
        "window_days": _window_days(),
        "published_count": len(published),
        "with_metrics": 0,
        "correlations": {},
        "insights": [],
        "clips": [],
    }
    if not published:
        report["insights"].append(
            "No successful uploads with video IDs found in publish_history.jsonl yet.")
        return report

    video_ids = [item["video_id"] for item in published]
    metrics = _fetch_real_metrics(video_ids) if fetch_live else {}
    for item in published:
        item["metrics"] = metrics.get(item["video_id"])
        report["clips"].append(item)
    measured = [item for item in published if item.get("metrics")]
    report["with_metrics"] = len(measured)
    if not measured:
        report["insights"].append(
            "Uploads found but YouTube Analytics returned no numbers yet "
            "(OAuth not configured, or videos are too recent).")
        return report

    correlations = {}
    for feature in FEATURE_KEYS:
        pairs = [(item["features"].get(feature), item["metrics"]["views"])
                 for item in measured]
        value = _spearman(pairs)
        retention_pairs = [
            (item["features"].get(feature), item["metrics"].get("retention"))
            for item in measured]
        retention = _spearman(retention_pairs)
        correlations[feature] = {
            "vs_views": value,
            "vs_retention": retention,
            "samples": len([1 for x, y in pairs if x is not None and y is not None]),
            "retention_samples": len([1 for x, y in retention_pairs
                                      if x is not None and y is not None]),
            "method": "spearman",
            "strength": _strength(value),
        }
    report["correlations"] = correlations
    report["window_days"] = _window_days()
    report["metric_notes"] = {
        "views": "views inside the shared {}d window".format(_window_days()),
        "retention": "YouTube averageViewPercentage inside the same window",
        "correlation": "Spearman rank (robust to a single viral outlier)",
    }

    # Content-aware learning (v7.33.2): which hook types / angles / topics /
    # title styles actually brought views. Only meaningful once clips carry
    # the content markers (publish_history events recorded since v7.33.1).
    content = _content_insights(measured)
    report["content_insights"] = content
    overall = content.get("overall_avg_views")
    if overall is not None:
        for field, rows in (content.get("categories") or {}).items():
            for row in rows[:3]:
                direction = "+" if row["delta_pct"] >= 0 else ""
                report["insights"].append(
                    "{}={}: avg {} views ({} vs overall, n={})".format(
                        field, row["value"], row["avg_views"],
                        "{}{}%".format(direction, row["delta_pct"]), row["samples"]))
        for row in content.get("title_styles") or []:
            direction = "+" if row["delta_pct"] >= 0 else ""
            report["insights"].append(
                "{}: {} views vs {} without ({}%, n={} vs {})".format(
                    _style_label(row["style"]), row["avg_views_yes"],
                    row["avg_views_no"], "{}{}".format(direction, row["delta_pct"]),
                    row["samples_yes"], row["samples_no"]))
        for field, rows in (content.get("categories") or {}).items():
            for row in rows[:3]:
                if row.get("delta_retention_pct") is None:
                    continue
                direction = "+" if row["delta_retention_pct"] >= 0 else ""
                report["insights"].append(
                    "{}={} retention: {:.2f}% ({}% vs overall)".format(
                        field, row["value"], row["avg_retention"],
                        "{}{}".format(direction, row["delta_retention_pct"])))
        hashtag = content.get("hashtag_count_correlation") or {}
        if hashtag.get("r") is not None and hashtag.get("strength") not in {
                "negligible", "insufficient_data"}:
            report["insights"].append(
                "hashtag count vs views: r={} ({})".format(
                    hashtag["r"], hashtag["strength"]))

    best_hours = _best_hours(measured)
    if best_hours:
        report["best_hours"] = best_hours
        report["insights"].append(
            "Measured best publish hours (local time): {} — from {} measured clips.".format(
                ", ".join(str(h) for h in best_hours), len(measured)))

    for feature, result in sorted(
            correlations.items(), key=lambda kv: -(abs(kv[1]["vs_views"] or 0))):
        value = result["vs_views"]
        if value is None or result["strength"] in {"negligible", "insufficient_data"}:
            continue
        direction = "higher" if value > 0 else "lower"
        report["insights"].append(
            "{}: clips with {} {} {} get {} views "
            "(rho={}, {} — Spearman rank, not causation; {} samples).".format(
                result["strength"].upper(), feature, "↑" if value > 0 else "↓",
                "more" if value > 0 else "fewer", direction, value,
                result["strength"], len(measured)))
    for feature, result in sorted(
            correlations.items(),
            key=lambda kv: -(abs(kv[1].get("vs_retention") or 0))):
        value = result.get("vs_retention")
        if value is None or _strength(value) in {"negligible", "insufficient_data"}:
            continue
        report["insights"].append(
            "{}: {} retention vs {} (rho={}, Spearman, n={}).".format(
                feature, "higher" if value > 0 else "lower",
                "viewer hold" if value > 0 else "drop-off", value,
                result.get("retention_samples", 0)))
    if not report["insights"]:
        report["insights"].append(
            "Metrics present but no feature shows a meaningful correlation yet "
            "— publish more clips to build signal.")
    return report


def write_report(project_folder: str, report: dict[str, Any]) -> str:
    path = os.path.join(os.path.abspath(os.fspath(project_folder)), REPORT_NAME)
    fd, temporary = tempfile.mkstemp(prefix=".perf-", suffix=".tmp",
                                     dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)
    return path


def write_markdown(project_folder: str, report: dict[str, Any]) -> str:
    """Human-readable Arabic/English summary next to the JSON report."""
    path = os.path.join(os.path.abspath(os.fspath(project_folder)), MARKDOWN_NAME)
    lines = [
        "# دروس أداء القناة — Channel performance lessons",
        "",
        "- النافذة المقاسة: {} يوماً (نفس النافذة لكل المقاطع).".format(
            report.get("window_days") or _window_days()),
        "- المنشور: {} · المقاس: {}.".format(
            report.get("published_count", 0), report.get("with_metrics", 0)),
        "- الارتباط: Spearman rank — لا يقلبه مقطع واحد بمليون مشاهدة.",
        "",
    ]
    correlations = report.get("correlations") or {}
    if correlations:
        lines.append("## ارتباط الخصائص")
        for feature, row in sorted(
                correlations.items(),
                key=lambda kv: -(abs(kv[1].get("vs_views") or 0))):
            lines.append("- `{}`: views rho={} ({}) · retention rho={} · n={}".format(
                feature, row.get("vs_views"), row.get("strength"),
                row.get("vs_retention"), row.get("samples", 0)))
        lines.append("")
    content = report.get("content_insights") or {}
    categories = content.get("categories") or {}
    if categories:
        lines.append("## ما ينجح على قناتك")
        for field, rows in categories.items():
            for row in rows[:3]:
                extra = ""
                if row.get("delta_retention_pct") is not None:
                    extra = " · احتفاظ {}{}%".format(
                        "+" if row["delta_retention_pct"] >= 0 else "",
                        row["delta_retention_pct"])
                lines.append("- {}={} · متوسط {} مشاهدة ({}{}%) · n={}{}".format(
                    field, row.get("value"), row.get("avg_views"),
                    "+" if row.get("delta_pct", 0) >= 0 else "",
                    row.get("delta_pct"), row.get("samples"), extra))
        lines.append("")
    if report.get("best_hours"):
        lines.append("## أفضل ساعات النشر (محلي)")
        lines.append("- " + ", ".join(str(hour) for hour in report["best_hours"]))
        lines.append("")
    try:
        from scripts import performance_weights
        weights = performance_weights.load_weights(
            os.path.abspath(os.fspath(project_folder)))
        if weights.get("basis") == "performance_insights":
            lines.append("## ما تغيّر في الاختيار")
            lines.append("- الثقة: {} (n={}).".format(
                weights.get("confidence"), weights.get("samples")))
            for shift in weights.get("shifted") or []:
                lines.append("- {} → {}: rho={} · إزاحة {}".format(
                    shift.get("feature"), shift.get("component"),
                    shift.get("r"), shift.get("shift")))
            if weights.get("title_boost"):
                lines.append("- دفعة العنوان: {}".format(weights["title_boost"]))
            if weights.get("duration_bonus"):
                lines.append("- دفعة المدة: {}".format(weights["duration_bonus"]))
            if weights.get("needed_samples"):
                lines.append("- يحتاج {} مقطعاً مقاساً إضافياً لثقة كاملة.".format(
                    weights["needed_samples"]))
            lines.append("")
            lines.append("## التوصيات للجولة القادمة")
            for shift in weights.get("shifted") or []:
                direction = "ارفع" if float(shift.get("shift") or 0) > 0 else "اخفض"
                lines.append("- {} وزن «{}» (rho={}).".format(
                    direction, shift.get("component"), shift.get("r")))
            if weights.get("title_boost"):
                lines.append("- {}".format(
                    "قدّم نمط العنوان الأفضل أداءً (دفعة موجبة)."
                    if weights["title_boost"] > 0
                    else "قلّل النمط الحالي للعناوين (دفعة سالبة)."))
            if weights.get("duration_bonus"):
                lines.append("- {}".format(
                    "فضّل مقاطع أطول قليلاً." if weights["duration_bonus"] > 0
                    else "فضّل مقاطع أقصر."))
            lines.append("")
    except Exception:
        pass
    if report.get("insights"):
        lines.append("## خلاصات")
        lines.extend("- " + str(item) for item in report["insights"])
        lines.append("")
    lines.append("_تم التوليد: {}_".format(report.get("generated_at", _now())))
    fd, temporary = tempfile.mkstemp(prefix=".perf-md-", suffix=".tmp",
                                     dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines).strip() + "\n")
    os.replace(temporary, path)
    return path


def mark_refresh_due(project_folder: str) -> str | None:
    """Mark the loop stale after a successful upload (best-effort)."""
    folder = os.path.abspath(os.fspath(project_folder))
    if not os.path.isdir(folder):
        return None
    path = os.path.join(folder, DUE_NAME)
    fd, temporary = tempfile.mkstemp(prefix=".perf-due-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"due_at": _now()}, handle, ensure_ascii=False)
        os.replace(temporary, path)
        return path
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        return None


def _refresh_ttl_hours() -> float:
    try:
        value = float(os.getenv("VIRALCUTTER_PERF_REFRESH_HOURS", ""))
    except (TypeError, ValueError):
        return 6.0
    return max(1.0, min(168.0, value))


def _load_report(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def auto_refresh(project_folder: str, *, force: bool = False) -> dict[str, Any]:
    """Refresh the loop only when it is stale or marked due.

    A failed analytics fetch must never replace a good report with an empty
    one; the previous metrics stay. Never raises.
    """
    folder = os.path.abspath(os.fspath(project_folder))
    report_path = os.path.join(folder, REPORT_NAME)
    previous = _load_report(report_path)
    due = os.path.exists(os.path.join(folder, DUE_NAME))
    published = _published_videos(folder)
    if not published:
        return {"refreshed": False, "reason": "no_published_videos",
                "report": previous}
    if not force and not due and previous:
        try:
            age_hours = ((datetime.now(timezone.utc).timestamp()
                          - os.path.getmtime(report_path)) / 3600.0)
            history_path = os.path.join(folder, HISTORY_NAME)
            history_age = (os.path.getmtime(history_path)
                           if os.path.exists(history_path) else 0.0)
            if age_hours < _refresh_ttl_hours() and (
                    not history_age or os.path.getmtime(report_path) >= history_age):
                return {"refreshed": False, "reason": "fresh",
                        "report": previous}
        except OSError:
            pass
    report = analyze(folder, fetch_live=True)
    if report.get("with_metrics", 0) == 0 and previous.get("with_metrics", 0):
        return {"refreshed": False, "reason": "kept_previous_metrics",
                "report": previous}
    report_path = write_report(folder, report)
    markdown_path = write_markdown(folder, report)
    if report.get("with_metrics", 0) > 0:
        try:
            os.remove(os.path.join(folder, DUE_NAME))
        except OSError:
            pass
    return {"refreshed": True, "reason": "updated", "report": report,
            "path": report_path, "markdown": markdown_path}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Link real YouTube outcomes to clip features (performance loop)")
    parser.add_argument("--project", required=True)
    parser.add_argument("--offline", action="store_true",
                        help="Skip YouTube Analytics fetch; analyze local data only")
    parser.add_argument("--auto", action="store_true",
                        help="Refresh only when stale or marked due (respects the TTL)")
    parser.add_argument("--force", action="store_true",
                        help="Refresh even when the cached report is fresh")
    args = parser.parse_args(argv)
    if args.auto and not args.offline:
        status = auto_refresh(args.project, force=args.force)
        report = status.get("report") or {}
        print("performance auto-refresh: {} ({})".format(
            status.get("reason"), status.get("path") or "no report"))
    else:
        report = analyze(args.project, fetch_live=not args.offline)
        path = write_report(args.project, report)
        write_markdown(args.project, report)
        print("performance insights → {}".format(path))
    print("published: {} · with metrics: {}".format(
        report["published_count"], report["with_metrics"]))
    for insight in report["insights"]:
        print("  • {}".format(insight))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
