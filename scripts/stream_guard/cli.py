# -*- coding: utf-8 -*-
"""
stream_guard.cli — واجهة سطر الأوامر لحراسة البثّ.

    python -m scripts.stream_guard check <video_id|url>
    python -m scripts.stream_guard watch <channel|video url>
    python -m scripts.stream_guard apply <risk_windows.json> --project DIR
    python -m scripts.stream_guard scan FILE... --out DIR
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import math
import os
import re
import shutil
import stat
import tempfile
import time
from urllib.parse import parse_qs, urlparse

from . import lexicon as policy
from . import scan
from . import text as arabic

logger = logging.getLogger(__name__)

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
SEV_LABEL_AR = {"critical": "حرِج", "high": "مرتفع", "medium": "متوسط",
                "low": "منخفض", "info": "معلومة"}

def _write_json(path: str, payload: dict) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=".tmp",
                                     dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=1)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
        temp_path = ""
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)



# ---------------------------------------------------------------------------
# جلب الترجمة والدردشة
# ---------------------------------------------------------------------------
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
_YOUTUBE_HOSTS = frozenset({
    "youtu.be", "www.youtu.be", "youtube.com", "www.youtube.com",
    "m.youtube.com", "music.youtube.com", "youtube-nocookie.com",
    "www.youtube-nocookie.com",
})


def _video_id(value: str) -> str:
    value = str(value or "").strip()
    if _VIDEO_ID.fullmatch(value):
        return value
    candidate = value if "://" in value else "https://" + value
    try:
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").lower()
    except ValueError as exc:
        raise ValueError("Expected a YouTube video ID or URL") from exc
    if parsed.scheme != "https" or host not in _YOUTUBE_HOSTS or parsed.username or parsed.password:
        raise ValueError("Expected a YouTube video ID or URL")
    video_id = ""
    if host in {"youtu.be", "www.youtu.be"}:
        video_id = parsed.path.strip("/").split("/", 1)[0]
    elif parsed.path == "/watch":
        video_id = parse_qs(parsed.query).get("v", [""])[0]
    else:
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 2 and parts[0] in {"live", "shorts", "embed"}:
            video_id = parts[1]
    if not _VIDEO_ID.fullmatch(video_id):
        raise ValueError("Expected a YouTube video ID or URL")
    return video_id


def fetch_tracks(url: str, outdir: str, chat: bool = True,
                 retries: int = 3, wait: int = 300) -> list[str]:
    """ينزّل الترجمة والدردشة إلى مجلد محاولة فريد ويعيد مسارات الملفات."""
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 1:
        raise ValueError("retries must be a positive integer")
    if isinstance(wait, bool) or not isinstance(wait, int) or wait < 0:
        raise ValueError("wait must be a non-negative integer")
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("yt-dlp is required to download YouTube tracks") from exc

    os.makedirs(outdir, exist_ok=True)
    run_dir = tempfile.mkdtemp(prefix="run-", dir=outdir)
    options = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "outtmpl": os.path.join(run_dir, "%(id)s.%(language)s.%(ext)s"),
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["ar.*", "ar", "live_chat"] if chat else ["ar.*", "ar"],
        "subtitlesformat": "srt/vtt/best",
    }
    keep_run_dir = False
    last_error = None
    try:
        for attempt in range(retries):
            try:
                with yt_dlp.YoutubeDL(options) as ydl:
                    ydl.download([url])
                last_error = None
            except Exception as exc:
                last_error = exc
                logger.warning("Track download attempt %d failed (%s)",
                               attempt + 1, type(exc).__name__)
            files = sorted(
                glob.glob(os.path.join(run_dir, "*.srt"))
                + glob.glob(os.path.join(run_dir, "*.vtt"))
                + glob.glob(os.path.join(run_dir, "*.json"))
                + glob.glob(os.path.join(run_dir, "*.jsonl"))
            )
            if files:
                keep_run_dir = True
                return files
            if attempt + 1 < retries:
                print(f"[guard] لا ترجمة بعد — انتظار {wait} ثانية…")
                time.sleep(wait)
        if last_error is not None:
            raise RuntimeError("yt-dlp failed to download YouTube tracks") from last_error
        return []
    finally:
        if not keep_run_dir:
            try:
                shutil.rmtree(run_dir)
            except OSError as exc:
                logger.warning("Could not remove empty track attempt directory (%s)",
                               type(exc).__name__)


# ---------------------------------------------------------------------------
# الفحص
# ---------------------------------------------------------------------------
def _layer2(text: str, safety_filter, index, allow_terms, min_severity: str) -> list[dict]:
    try:
        matches = safety_filter.find_matches(
            text, index=index, allow_terms=allow_terms, min_severity=min_severity)
    except Exception as exc:
        raise RuntimeError("safety_filter failed while scanning transcript text") from exc
    if not isinstance(matches, list):
        raise RuntimeError("safety_filter returned a malformed result list")
    actionable = []
    for match in matches:
        if not isinstance(match, dict):
            raise RuntimeError("safety_filter returned a malformed match")
        context_only = match.get("context_only", False)
        if not isinstance(context_only, bool):
            raise RuntimeError("safety_filter returned malformed context metadata")
        if not context_only:
            actionable.append(match)
    return actionable


def scan_files(files: list[str], min_sev: str = "medium") -> list:
    if min_sev not in {"critical", "high", "medium", "low"}:
        raise ValueError("min_sev must be critical, high, medium, or low")
    if not files:
        return []
    try:
        from scripts import safety_filter
    except Exception as exc:
        raise RuntimeError("safety_filter could not be imported") from exc

    eng = scan.Engine()
    out = []
    indexes = {}
    safety_minimum = "high" if min_sev in {"critical", "high"} else min_sev
    for filename in files:
        source = "chat" if "live_chat" in os.path.basename(filename).lower() else "stream"
        folder = os.path.dirname(os.path.abspath(filename))
        if folder not in indexes:
            try:
                custom = safety_filter.load_custom_terms(
                    project_folder=folder, sync=False, include_policy=False)
                extra = safety_filter.load_remote_terms()
                extra.extend(custom.get("extra_terms", []))
                from scripts import policy_lexicon
                seen_policy_paths = set()
                for policy_folder in (None, folder):
                    policy_path = policy_lexicon.db_path(policy_folder)
                    if policy_path in seen_policy_paths or not os.path.isfile(policy_path):
                        continue
                    seen_policy_paths.add(policy_path)
                    extra.extend(policy_lexicon.load_terms(policy_folder, sync=False))
                indexes[folder] = (
                    safety_filter._build_index(extra_terms=extra),
                    custom.get("allow_terms", []),
                )
            except Exception as exc:
                raise RuntimeError("safety_filter index could not be initialized") from exc
        safety_index, allow_terms = indexes[folder]
        cues = scan.load_cues(filename)
        primary = scan.scan_cues(eng, cues, source=source)
        out.extend(primary)
        for cue in cues:
            matches = _layer2(cue.text, safety_filter, safety_index, allow_terms, safety_minimum)
            for match in matches:
                if not isinstance(match, dict):
                    raise RuntimeError("safety_filter returned a malformed match")
                severity = match.get("severity", "high")
                if not isinstance(severity, str) or severity not in SEV_ORDER:
                    raise RuntimeError("safety_filter returned an unknown severity")
                category = str(match.get("category") or "custom")
                if SEV_ORDER[severity] > SEV_ORDER[min_sev]:
                    continue
                mapped_category = {
                    "hate_slur": "hate", "hate_dehumanize": "hate",
                    "violence_threat": "violence", "profanity": "profanity",
                    "harassment": "harassment",
                }.get(category, category)
                out.append(scan.Finding(
                    cue=cue,
                    severity=severity,
                    category=mapped_category,
                    category_label=policy.CATEGORY_LABEL.get(mapped_category, mapped_category),
                    term=str(match.get("term") or ""),
                    reason="مطابقة من safety_filter المدمج؛ تحقّق من السياق قبل الإجراء.",
                    kind=category,
                    source=source,
                ))

    filtered = [finding for finding in out
                if finding.severity in SEV_ORDER
                and SEV_ORDER[finding.severity] <= SEV_ORDER[min_sev]]
    unique = {}
    for finding in filtered:
        key = (finding.source, id(finding.cue), arabic.normalize(finding.term))
        previous = unique.get(key)
        if previous is None or SEV_ORDER[finding.severity] < SEV_ORDER[previous.severity]:
            unique[key] = finding
    return list(unique.values())


def build_windows(findings: list, gap: float = 20.0) -> list[dict]:
    if isinstance(gap, bool) or not isinstance(gap, (int, float)) or not math.isfinite(gap) or gap < 0:
        raise ValueError("gap must be a finite non-negative number")
    stream_f = [f for f in findings if f.source == "stream"]
    incidents = scan.merge_incidents(stream_f, gap=gap)
    windows = []
    for incident in incidents:
        top_findings = [finding for finding in incident["findings"]
                        if finding.severity == incident["severity"]]
        lead = top_findings[0] if top_findings else incident["findings"][0]
        windows.append({
            "start": round(incident["start"], 2),
            "end": round(incident["end"], 2),
            "severity": incident["severity"],
            "category": lead.category_label,
            "term": lead.term,
            "reason": lead.reason,
            "text": lead.cue.text.strip()[:200],
        })
    return windows


def safe_windows(windows: list[dict], duration: float) -> list[dict]:
    if isinstance(duration, bool):
        raise ValueError("duration must be a finite non-negative number")
    try:
        duration = float(duration)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("duration must be a finite non-negative number") from exc
    if not math.isfinite(duration) or duration < 0:
        raise ValueError("duration must be a finite non-negative number")
    if not isinstance(windows, list):
        raise ValueError("windows must be a list")
    validated = []
    for index, window in enumerate(windows):
        if not isinstance(window, dict):
            raise ValueError(f"window {index} must be an object")
        if isinstance(window.get("start"), bool) or isinstance(window.get("end"), bool):
            raise ValueError(f"window {index} has invalid start/end")
        try:
            start, end = float(window["start"]), float(window["end"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"window {index} has invalid start/end") from exc
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError(f"window {index} must have finite start < end")
        start, end = min(duration, start), min(duration, end)
        if end > start:
            validated.append((start, end))

    out, cursor = [], 0.0
    for start, end in sorted(validated):
        if start > cursor:
            out.append({"start": round(cursor, 2), "end": round(start, 2)})
        cursor = max(cursor, end)
    if cursor < duration:
        out.append({"start": round(cursor, 2), "end": round(duration, 2)})
    return out


def check(url_or_id: str, outdir: str | None = None, min_sev: str = "medium",
          gap: float = 20.0, retries: int = 3, wait: int = 300) -> dict:
    vid = _video_id(url_or_id)
    url = f"https://www.youtube.com/watch?v={vid}"
    outdir = outdir or os.path.join("stream_reports", vid)
    os.makedirs(outdir, exist_ok=True)

    print(f"[guard] فحص {vid} — تنزيل الترجمة والدردشة…")
    files = fetch_tracks(url, os.path.join(outdir, "tracks"), retries=retries, wait=wait)
    if not files:
        print("[guard] لم أجد ترجمة. قد يكون البثّ لم ينتهِ بعد أو الترجمة غير متاحة.")
        return {}

    findings = scan_files(files, min_sev)
    windows = build_windows(findings, gap)

    duration = 0.0
    for f in files:
        if "live_chat" in os.path.basename(f).lower():
            continue
        cues = scan.load_cues(f)
        duration = max([duration] + [c.end for c in cues])

    meta = {"title": vid, "duration": scan.fmt_t(duration),
            "date": time.strftime("%Y-%m-%d %H:%M"), "source": vid}
    incidents = scan.merge_incidents([f for f in findings if f.source == "stream"], gap=gap)
    rep = scan.build_report(incidents, [f for f in findings if f.source != "stream"],
                            findings, meta)
    _write_json(os.path.join(outdir, "report.json"), rep)

    scan.write_html(rep, os.path.join(outdir, "report.html"))
    scan.write_markdown(rep, os.path.join(outdir, "report.md"))

    safe = safe_windows(windows, duration)
    payload = {"source": vid, "duration": duration, "windows": windows,
               "safe_windows": safe,
               "chat_findings": len([f for f in findings if f.source != "stream"])}
    _write_json(os.path.join(outdir, "risk_windows.json"), payload)
    _write_json(os.path.join(outdir, "safe_windows.json"),
                {"source": vid, "duration": duration, "windows": safe})

    print(f"\n✔ {len(windows)} نافذة خطر · {len(payload['safe_windows'])} نافذة آمنة")
    print("التقرير:", os.path.join(outdir, "report.html"))
    for w in windows:
        print(f"   {scan.fmt_t(w['start'])}–{scan.fmt_t(w['end'])}  "
              f"[{SEV_LABEL_AR[w['severity']]}] {w['category']}: {w['term']}")
    return payload


def scan_local(files: list[str], outdir: str, min_sev: str, gap: float) -> dict:
    """منطق الفحص نفسه لكن على ملفات محلية (يعمل بلا إنترنت)."""
    if not files:
        raise ValueError("at least one local transcript/chat file is required")
    os.makedirs(outdir, exist_ok=True)
    findings = scan_files(files, min_sev)
    windows = build_windows(findings, gap)
    duration = 0.0
    for f in files:
        if "live_chat" in os.path.basename(f).lower():
            continue
        duration = max([duration] + [c.end for c in scan.load_cues(f)])
    meta = {"title": os.path.basename(files[0]), "duration": scan.fmt_t(duration),
            "date": time.strftime("%Y-%m-%d %H:%M"), "source": files[0]}
    inc = scan.merge_incidents([f for f in findings if f.source == "stream"], gap=gap)
    rep = scan.build_report(inc, [f for f in findings if f.source != "stream"],
                            findings, meta)
    _write_json(os.path.join(outdir, "report.json"), rep)

    scan.write_html(rep, os.path.join(outdir, "report.html"))
    scan.write_markdown(rep, os.path.join(outdir, "report.md"))
    safe = safe_windows(windows, duration)
    payload = {"source": files[0], "duration": duration, "windows": windows,
               "safe_windows": safe}
    _write_json(os.path.join(outdir, "risk_windows.json"), payload)
    _write_json(os.path.join(outdir, "safe_windows.json"),
                {"source": files[0], "duration": duration, "windows": safe})
    print(f"✔ {len(windows)} نافذة خطر · {len(payload['safe_windows'])} نافذة آمنة")
    for w in windows:
        print(f"   {scan.fmt_t(w['start'])}–{scan.fmt_t(w['end'])} "
              f"[{SEV_LABEL_AR[w['severity']]}] {w['category']}: {w['term']}")
    return payload


# ---------------------------------------------------------------------------
# التطبيق على مشروع القصّ
# ---------------------------------------------------------------------------
def _next_backup_path(segment_path: str) -> str:
    candidate = segment_path + ".bak"
    index = 1
    while os.path.lexists(candidate):
        candidate = f"{segment_path}.bak.{index}"
        index += 1
    return candidate


def apply_to_project(project_dir: str, risk_path: str, margin: float = 3.0) -> int:
    if isinstance(margin, bool):
        raise ValueError("margin must be a finite non-negative number")
    try:
        margin = float(margin)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("margin must be a finite non-negative number") from exc
    if not math.isfinite(margin) or margin < 0:
        raise ValueError("margin must be a finite non-negative number")

    project_root = os.path.realpath(project_dir)
    segment_path = os.path.join(project_root, "viral_segments.txt")
    if os.path.islink(segment_path) or not os.path.isfile(segment_path):
        raise FileNotFoundError(f"A regular viral_segments.txt is required in {project_root}")

    with open(risk_path, "r", encoding="utf-8-sig") as stream:
        risk = json.load(stream)
    if not isinstance(risk, dict) or not isinstance(risk.get("windows"), list):
        raise ValueError("risk file must contain a windows list")

    windows = []
    for index, window in enumerate(risk["windows"]):
        if not isinstance(window, dict):
            raise ValueError(f"risk window {index} must be an object")
        if isinstance(window.get("start"), bool) or isinstance(window.get("end"), bool):
            raise ValueError(f"risk window {index} has invalid start/end")
        try:
            start = float(window["start"])
            end = float(window["end"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"risk window {index} has invalid start/end") from exc
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError(f"risk window {index} must have finite start < end")
        windows.append((max(0.0, start - margin), end + margin, start, end))

    if not windows:
        print("لا نوافذ خطر — لا تغيير.")
        return 0

    with open(segment_path, "rb") as stream:
        original = stream.read()
    try:
        data = json.loads(original.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("viral_segments.txt is not valid UTF-8 JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        raise ValueError("viral_segments.txt must contain a segments list")

    kept, dropped = [], []
    for index, segment in enumerate(data["segments"]):
        if not isinstance(segment, dict):
            raise ValueError(f"segment {index} must be an object")
        if isinstance(segment.get("start_time"), bool) or isinstance(segment.get("end_time"), bool):
            raise ValueError(f"segment {index} has invalid start_time/end_time")
        try:
            start = float(segment["start_time"])
            end = float(segment["end_time"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"segment {index} has invalid start_time/end_time") from exc
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError(f"segment {index} must have finite start_time < end_time")
        overlap = next((window for window in windows
                        if start < window[1] and end > window[0]), None)
        if overlap:
            dropped.append({
                "segment": str(segment.get("title") or f"{start:.0f}-{end:.0f}"),
                "overlaps": [round(overlap[2], 1), round(overlap[3], 1)],
            })
        else:
            kept.append(segment)

    if not dropped:
        print("لا مقاطع تتقاطع مع النوافذ — لا تغيير.")
        return 0

    data["segments"] = kept
    data["stream_guard"] = {"dropped": dropped, "source": risk.get("source")}
    serialized = (json.dumps(data, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    backup_path = None
    while backup_path is None:
        candidate = _next_backup_path(segment_path)
        try:
            backup_fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        backup_path = candidate
    try:
        with os.fdopen(backup_fd, "wb") as backup_stream:
            backup_stream.write(original)
            backup_stream.flush()
            os.fsync(backup_stream.fileno())
    except BaseException:
        try:
            os.unlink(backup_path)
        except OSError as cleanup_error:
            logger.error("Could not remove incomplete backup (%s)", type(cleanup_error).__name__)
        raise

    temp_path = None
    try:
        temp_fd, temp_path = tempfile.mkstemp(prefix=".viral_segments.", suffix=".tmp",
                                              dir=project_root)
        original_mode = stat.S_IMODE(os.stat(segment_path, follow_symlinks=False).st_mode)
        if hasattr(os, "fchmod"):
            os.fchmod(temp_fd, original_mode)
        else:
            os.chmod(temp_path, original_mode)
        with os.fdopen(temp_fd, "wb") as output:
            output.write(serialized)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, segment_path)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)

    print(f"✔ أُبقي {len(kept)} مقطعًا · حُذف {len(dropped)} متقاطع مع الخطر")
    print("النسخة الاحتياطية:", backup_path)
    return len(dropped)


# ---------------------------------------------------------------------------
# المراقبة
# ---------------------------------------------------------------------------
def _watch_target(target: str) -> tuple[str, str | None]:
    try:
        video_id = _video_id(target)
    except ValueError:
        return _channel_live_url(target), None
    return f"https://www.youtube.com/watch?v={video_id}", video_id


def _channel_live_url(target: str) -> str:
    value = str(target or "").strip()
    candidate = value if "://" in value else f"https://{value}"
    try:
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Expected a YouTube channel URL") from exc
    if (parsed.scheme != "https" or host not in _YOUTUBE_HOSTS
            or parsed.username or parsed.password or port not in (None, 443)
            or parsed.query or parsed.fragment):
        raise ValueError("Expected a YouTube channel URL")
    parts = [part for part in parsed.path.split("/") if part]
    if parts and parts[-1] == "live":
        parts.pop()
    if len(parts) == 1 and re.fullmatch(r"@[A-Za-z0-9_.-]{3,30}", parts[0]):
        channel_path = f"/{parts[0]}"
    elif len(parts) == 2 and parts[0] in {"channel", "c", "user"}:
        channel_id = parts[1]
        valid_channel = (
            re.fullmatch(r"UC[A-Za-z0-9_-]{22}", channel_id)
            if parts[0] == "channel"
            else re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", channel_id)
        )
        if not valid_channel:
            raise ValueError("Expected a YouTube channel URL")
        channel_path = f"/{parts[0]}/{channel_id}"
    else:
        raise ValueError("Expected a YouTube channel URL")
    return f"https://www.youtube.com{channel_path}/live"


def watch(target: str, poll: int = 120, wait_after: int = 600) -> None:
    if isinstance(poll, bool) or not isinstance(poll, int) or poll < 1:
        raise ValueError("poll must be a positive integer")
    if isinstance(wait_after, bool) or not isinstance(wait_after, int) or wait_after < 0:
        raise ValueError("wait_after must be a non-negative integer")
    monitor_url, known_video_id = _watch_target(target)
    try:
        from scripts import download_live
    except ImportError as exc:
        raise RuntimeError("download_live is unavailable") from exc
    if not getattr(download_live, "HAS_YTDLP", True):
        raise RuntimeError("yt-dlp is required for live monitoring")

    print(f"[guard] مراقبة {monitor_url} كل {poll} ثانية…")
    seen = set()
    retry_at = {}
    while True:
        try:
            status = download_live.fetch_status(monitor_url)
            state = status.get("status") if isinstance(status, dict) else "unknown"
            if state == "ended":
                video_id = str(status.get("id") or known_video_id or "")
                if _VIDEO_ID.fullmatch(video_id) and video_id not in seen:
                    if video_id not in retry_at:
                        print("[guard] انتهى البثّ — انتظار تجهيز الترجمة…")
                        if wait_after:
                            time.sleep(wait_after)
                        retry_at[video_id] = time.monotonic()
                    if time.monotonic() >= retry_at[video_id]:
                        try:
                            result = check(video_id)
                        except Exception as exc:
                            logger.warning("Post-live scan failed (%s)", type(exc).__name__)
                            result = {}
                        if isinstance(result, dict) and "windows" in result:
                            seen.add(video_id)
                            retry_at.pop(video_id, None)
                        else:
                            retry_at[video_id] = time.monotonic() + max(poll, wait_after)
            elif state == "unknown":
                logger.warning("Live status unavailable (%s)",
                               type(status.get("error")).__name__ if isinstance(status, dict) else "invalid result")
            time.sleep(poll)
        except KeyboardInterrupt:
            return
        except Exception as exc:
            logger.warning("Live monitor poll failed (%s)", type(exc).__name__)
            try:
                time.sleep(poll)
            except KeyboardInterrupt:
                return

# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m scripts.stream_guard",
        description="حراسة البثّ المباشر: فحص بعد الانتهاء + تقاطع مع القصّ")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="افحص ترجمة بثّ YouTube ودردشته بعد انتهائه")
    p.add_argument("target")
    p.add_argument("--out")
    p.add_argument("--min-severity", default="medium",
                   choices=["critical", "high", "medium", "low"])
    p.add_argument("--gap", type=float, default=20.0)
    p.add_argument("--wait", type=int, default=300, help="ثواني الانتظار بين محاولات الترجمة")

    p = sub.add_parser("watch", help="راقب قناة وافحص البثّ بعد انتهائه")
    p.add_argument("target")
    p.add_argument("--poll", type=int, default=120)
    p.add_argument("--wait-after", type=int, default=600)

    p = sub.add_parser("scan", help="افحص ملفات ترجمة/دردشة محلية دون تنزيل")
    p.add_argument("files", nargs="+")
    p.add_argument("--out", required=True)
    p.add_argument("--min-severity", default="medium",
                   choices=["critical", "high", "medium", "low"])
    p.add_argument("--gap", type=float, default=20.0)

    p = sub.add_parser("apply", help="احذف المقاطع المتقاطعة مع نوافذ تمت مراجعتها")
    p.add_argument("risk_json")
    p.add_argument("--project", required=True)
    p.add_argument("--margin", type=float, default=3.0)

    a = ap.parse_args(argv)
    if a.cmd == "check":
        check(a.target, a.out, a.min_severity, a.gap, wait=a.wait)
    elif a.cmd == "watch":
        watch(a.target, a.poll, a.wait_after)
    elif a.cmd == "scan":
        scan_local(a.files, a.out, a.min_severity, a.gap)
    elif a.cmd == "apply":
        apply_to_project(a.project, a.risk_json, a.margin)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
