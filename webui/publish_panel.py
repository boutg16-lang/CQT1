"""Publish panel — per-clip play / translate / music-check / upload.

Pure logic extracted from the WebUI so it stays unit-testable:
- list rendered clips of a project (final/ first, then cuts/)
- find the subtitle JSON for a clip
- translate one clip's subtitles (reuses scripts/translate_json)
- run the music fingerprint check (scripts/music_fingerprint)
- upload one clip through the safety gate (scripts/upload_gate)

No gradio imports in this module.
"""

import asyncio
import datetime
import json
import math
import os
import queue
import re
import sys
import threading

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Clip discovery
# ---------------------------------------------------------------------------

CLIP_SOURCES = ("auto", "final_polished", "final", "cuts", "specific_file")


def source_video_id(url):
    """Extract a YouTube video id from watch / youtu.be / shorts / embed URLs.

    Returns '' for anything else (including an already-bare id). Best-effort:
    used to pin a Short's related video to its source video.
    """
    text = str(url or "").strip()
    if not text:
        return ""
    patterns = (
        r"(?:youtube\.com/watch\?(?:.*&)?v=)([\w-]{11})",
        r"(?:youtu\.be/)([\w-]{11})",
        r"(?:youtube\.com/shorts/)([\w-]{11})",
        r"(?:youtube\.com/embed/)([\w-]{11})",
        r"(?:youtube\.com/live/)([\w-]{11})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def project_source_url(project_path):
    """The source video's watch URL from the project manifest.

    Returns '' for local-file sources (no watch URL exists) so the funnel
    never prints a fake link.
    """
    try:
        from webui import project_store

        manifest = project_store.load_manifest(project_path) or {}
    except Exception:
        return ""
    source = manifest.get("source") or {}
    if not isinstance(source, dict) or str(source.get("type") or "") != "youtube":
        return ""
    url = str(source.get("url") or "").strip()
    return url if source_video_id(url) else ""


def project_channel_url(project_path):
    """Channel link for the funnel description (settings.funnel.channel_url).

    Empty when unset — the funnel line for the video itself always wins.
    """
    try:
        from webui import project_store

        manifest = project_store.load_manifest(project_path) or {}
    except Exception:
        return ""
    settings = manifest.get("settings") or {}
    funnel = settings.get("funnel") if isinstance(settings, dict) else None
    if isinstance(funnel, dict):
        return str(funnel.get("channel_url") or "").strip()
    return ""


def _mp4_files(folder):
    if not folder or not os.path.isdir(folder):
        return []
    return sorted(
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if name.lower().endswith(".mp4") and os.path.isfile(os.path.join(folder, name))
    )


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _usable_polished_clips(project_path, clips):
    """Return polished clips only when the persisted report validates them."""
    report = _load_json(os.path.join(project_path, "polish_report.json"))
    if not report:
        # Legacy projects have no report; keep the old read-only behavior.
        return clips
    entries = report.get("clips") or []
    by_output = {
        os.path.abspath(str(item.get("output"))): item
        for item in entries
        if isinstance(item, dict) and item.get("output")
    }
    by_name = {
        str(item.get("video")): item for item in entries if isinstance(item, dict)
    }
    usable = []
    for path in clips:
        item = by_output.get(os.path.abspath(path)) or by_name.get(
            os.path.basename(path)
        )
        if not item:
            return []
        status = item.get("quality_status")
        if (
            not item.get("media_validated")
            or item.get("fallback_used")
            or item.get("failed_stages")
            or status not in {"enhanced", "partial"}
        ):
            return []
        usable.append(path)
    return usable


def list_clips(project_path, source="auto", selected_file=None):
    """Return rendered clips from an explicit source.

    ``auto`` keeps ``final_polished`` → ``final`` → ``cuts`` but falls back to
    the next source when the polish report marks any polished output as
    fallback, failed, invalid or missing. Explicit ``final_polished`` remains
    available for inspection; the upload worker blocks an unsafe real upload.
    """
    if not project_path or not os.path.isdir(project_path):
        return []
    source = str(source or "auto").strip().lower()
    if source == "specific_file":
        path = os.path.abspath(os.fspath(selected_file or "")) if selected_file else ""
        return [path] if path.lower().endswith(".mp4") and os.path.isfile(path) else []
    names = ["final_polished", "final", "cuts"] if source == "auto" else [source]
    for name in names:
        clips = _mp4_files(os.path.join(project_path, name))
        if not clips:
            continue
        if name == "final_polished" and source == "auto":
            clips = _usable_polished_clips(project_path, clips)
            if not clips:
                continue
        return clips
    return []


def list_clips_by_source(project_path):
    """Return all source buckets for UI diagnostics without changing precedence."""
    return {
        name: _mp4_files(os.path.join(project_path, name))
        for name in ("final_polished", "final", "cuts")
    }


def clip_index(video_path):
    """Leading digits of the filename → segment index (for gate checks)."""
    import re

    m = re.match(r"(\d+)", os.path.basename(video_path))
    return int(m.group(1)) if m else None


def _subtitle_files_for_clip(project_path, video_path):
    """Candidate subtitle JSONs for a clip (subs/<stem>*_processed.json etc)."""
    stem = os.path.splitext(os.path.basename(video_path))[0]
    subs_dir = os.path.join(project_path, "subs")
    if not os.path.isdir(subs_dir):
        return []
    candidates = []
    for name in sorted(os.listdir(subs_dir)):
        if not name.endswith(".json"):
            continue
        base = os.path.splitext(name)[0]
        if base.startswith(stem):
            candidates.append(os.path.join(subs_dir, name))
    return candidates


def find_subs_for_clip(project_path, video_path):
    """Best subtitle JSON for a clip (prefer *_processed.json)."""
    candidates = _subtitle_files_for_clip(project_path, video_path)
    if not candidates:
        return None
    processed = [c for c in candidates if "_processed" in os.path.basename(c)]
    return (processed or candidates)[0]


def segments_for_project(project_path):
    """The viral_segments.txt segments list (for title/caption suggestions)."""
    path = os.path.join(project_path, "viral_segments.txt")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        segments = data.get("segments", [])
        return segments if isinstance(segments, list) else []
    except Exception:
        return []


def clip_metadata(project_path, video_path):
    """Full publish metadata for a clip from its viral segment entry.

    Returns a dict with ``title``, ``caption``, normalized ``hashtags`` and
    the segment's editorial markers (``topic``/``angle``/``hook_type``, for
    the performance-learning loop) — the hashtags the LLM generated for the
    segment (3-5 tags) were previously dropped on the floor here: they never
    reached the uploader, so YouTube videos shipped with empty ``tags`` and
    no #hashtags in the description.
    """
    idx = clip_index(video_path)
    segments = segments_for_project(project_path)
    if idx is None or idx >= len(segments):
        return {
            "title": "",
            "caption": "",
            "hashtags": [],
            "topic": "",
            "angle": "",
            "hook_type": "",
            "title_template": "",
            "alt_titles": [],
            "transcript_text": "",
        }
    seg = segments[idx]
    title = seg.get("recommended_title") or seg.get("title") or ""
    caption = seg.get("caption") or ""
    hashtags = seg.get("hashtags") or []
    if isinstance(hashtags, str):
        hashtags = re.split(r"[,\s]+", hashtags)
    normalized = []
    for tag in hashtags:
        cleaned = str(tag).strip().lstrip("#")
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    # YouTube snippet tags are capped at 500 chars total — never flood them.
    return {
        "title": title,
        "caption": caption,
        "hashtags": normalized[:15],
        "topic": str(seg.get("topic") or ""),
        "angle": str(seg.get("angle") or ""),
        "hook_type": str(seg.get("hook_type") or ""),
        # Item 6: the title pattern that built this title, recorded at
        # publish time so the loop can measure which templates win.
        "title_template": str(seg.get("title_template") or ""),
        # Thumbnail A/B variants (round 2 item 4): alternate headline
        # wordings measured by the winning-title loop.
        "alt_titles": list(seg.get("alt_titles") or []),
        # Self-certification scan (feat/pro-cutting item 7): the transcript
        # rides along so the preflight questionnaire scan sees real words.
        "transcript_text": str(seg.get("transcript_text") or ""),
        # v7.41 review gating surfaced to the publish UI.
        "requires_review": bool(
            seg.get("requires_review") or seg.get("title_review_required")
        ),
        "publish_blocked_reason": str(seg.get("publish_blocked_reason") or ""),
        "title_validation_status": str(
            (
                seg.get("title_validation")
                or (seg.get("title_data") or {}).get("title_validation")
                or {}
            ).get("status")
            or ""
        ),
    }


def clip_suggestion(project_path, video_path):
    """Suggested title+caption for a clip from its viral segment entry."""
    meta = clip_metadata(project_path, video_path)
    return meta["title"], meta["caption"]


# ---------------------------------------------------------------------------
# Per-clip translate
# ---------------------------------------------------------------------------


def translate_clip(project_path, video_path, target_lang):
    """Translate one clip's subtitles using the built-in Google adapter.

    Returns (ok: bool, message: str). Writes <stem>_<lang>.json in subs/.
    """
    if not video_path or not os.path.exists(video_path):
        return False, "Clip not found."
    if not target_lang or target_lang.strip() == "":
        return False, "Target language is required (e.g. en, ar, fr)."

    src = find_subs_for_clip(project_path, video_path)
    if not src:
        return False, "No subtitle file for this clip."
    lang = target_lang.strip().lower().split("-")[0]
    dst = os.path.join(
        project_path,
        "subs",
        "{}_translated_{}.json".format(
            os.path.splitext(os.path.basename(src))[0], lang
        ),
    )
    try:
        from scripts.translate_json import translate_json_file
    except Exception as e:
        return False, (
            "Translation unavailable — install deps first: "
            "install the translation prerequisites ({})".format(e)
        )
    try:
        data = asyncio.run(translate_json_file(src, dst, lang))
        count = len(data.get("segments", []))
        return True, "Copied: {} (translation for {})".format(
            os.path.basename(dst), lang
        ) + " — {} segments".format(count)
    except Exception as e:
        return False, "Translation failed: {}".format(str(e)[:300])


def dub_clip(
    project_path,
    video_path,
    provider="file",
    voice="",
    mode="replace",
    allow_network=False,
    work_dir=None,
):
    """Dub one clip with a TTS provider (v7.50).

    Returns ``(ok, message)`` like :func:`translate_clip`. The subtitle file
    used is the translated one when present, else the clip's own subtitles —
    so "translate then dub" is two clicks in the same panel.

    ``allow_network`` is the explicit, *visible* consent for the only provider
    that sends text off the machine; it is never enabled implicitly here.
    """
    if not video_path or not os.path.exists(video_path):
        return False, "Clip not found."

    subs = _translated_or_original_subs(project_path, video_path)
    if not subs:
        return (
            False,
            "No subtitle file for this clip — run transcription (or translate) first.",
        )

    try:
        from scripts import dubbing, tts_providers
    except Exception as exc:
        return False, "Dubbing unavailable: {}".format(str(exc)[:200])

    info = tts_providers.provider_info(provider)
    if info is None:
        return False, "Unknown TTS provider {!r} (available: {})".format(
            provider, ", ".join(tts_providers.provider_names())
        )
    if info["network"] and not allow_network:
        return False, (
            "المزوّد {} يرسل النص إلى خدمة خارجية. "
            "فعّل خيار «السماح بالشبكة» صراحةً للمتابعة."
        ).format(provider)
    if provider == "file" and not str(voice or "").strip():
        return False, "Provide a folder of pre-recorded audio files (voice)."

    out_dir = os.path.join(project_path, "final_dubbed")
    os.makedirs(out_dir, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(video_path))
    out_path = os.path.join(out_dir, "{}{}{}".format(stem, dubbing.CLIP_SUFFIX, ext))

    try:
        result = dubbing.dub_clip(
            video_path,
            subs,
            out_path,
            provider=provider,
            voice=voice or None,
            mode=mode,
            work_dir=work_dir,
            allow_network=allow_network,
        )
    except Exception as exc:
        return False, "Dubbing failed: {}".format(str(exc)[:300])

    if not result.get("ok"):
        return False, "تعذّرت الدبلجة: {}".format(result.get("error") or "unknown error")

    return True, (
        "تم إنشاء المقطع المدبلج: {} (#{} سطر مُدبلج{}{}). "
        "تنبيه: صوت مُصنَّع — فعّل الإفصاح عن المحتوى المُعدّل عند النشر."
    ).format(
        result.get("output"),
        result.get("segments_dubbed"),
        "، {} فشل".format(result.get("segments_failed"))
        if result.get("segments_failed")
        else "",
        "، " + "; ".join(result.get("warnings") or [])
        if result.get("warnings")
        else "",
    )


def _translated_or_original_subs(project_path, video_path):
    """Prefer a translated subtitle file, else the clip's own subtitles."""
    subs_dir = os.path.join(project_path, "subs")
    stem = os.path.splitext(os.path.basename(video_path))[0]
    prefix = stem.split("_")[0]
    if os.path.isdir(subs_dir):
        translated = sorted(
            os.path.join(subs_dir, name)
            for name in os.listdir(subs_dir)
            if name.startswith(prefix)
            and "_translated_" in name
            and name.endswith(".json")
        )
        if translated:
            return translated[-1]
    return find_subs_for_clip(project_path, video_path)


def clip_subtitle_preview(project_path, video_path):
    """First lines of the clip's subtitle text for the UI preview."""
    src = find_subs_for_clip(project_path, video_path)
    if not src:
        return ""
    try:
        with open(src, "r", encoding="utf-8") as f:
            data = json.load(f)
        texts = [s.get("text", "") for s in data.get("segments", []) if s.get("text")]
        return " | ".join(texts[:8])
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Music check
# ---------------------------------------------------------------------------


def run_music_check(project_path, local_db_path=""):
    """Run the Chromaprint check on the project. Returns a readable report."""
    try:
        from scripts import music_fingerprint as mf
    except Exception as e:
        return "Music fingerprint module unavailable: {}".format(e)

    local_db = None
    if local_db_path and os.path.isdir(local_db_path):
        cache = os.path.join(os.path.expanduser("~"), ".viralcutter", "music_db.json")
        try:
            local_db = mf.build_local_db(local_db_path, cache_path=cache)
        except Exception as e:
            return "Local DB build failed: {}".format(e)
    elif local_db_path:
        local_db = mf.load_local_db(local_db_path)

    try:
        report = mf.analyze_project(project_path, local_db=local_db, gate="warn")
    except Exception as e:
        return "Music check failed: {}".format(e)

    s = report["summary"]
    lines = [
        "Music check: {} clips checked, {} matched.".format(
            s.get("checked", 0), s.get("matched", 0)
        )
    ]
    if s.get("no_fpcalc"):
        lines.append(
            "⚠️ {} clips: Chromaprint not installed (see docs).".format(s["no_fpcalc"])
        )
    for clip in report.get("clips", []):
        verdict = clip.get("verdict", "?")
        mark = {
            "clean": "✅",
            "acoustid_match": "🎵⚠️",
            "local_match": "🎵⚠️",
            "no_fpcalc": "⚠️",
            "error": "❌",
        }.get(verdict, "?")
        lines.append(
            "  {} #{} {} — {}".format(
                mark,
                clip.get("index", "?"),
                os.path.basename(clip.get("video", "")),
                verdict,
            )
        )
        if clip.get("suggestion"):
            lines.append("      ↳ {}".format(clip["suggestion"]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Upload through the safety gate (streaming)
# ---------------------------------------------------------------------------

# Publish-title character caps per platform, used when a batch clip has no
# segment suggestion and its publish title must be derived from the filename.
# YouTube hard-caps titles at 100 chars; TikTok's Content Posting API caps
# post_info.title at 150. Unknown platforms default to the YouTube cap.
_PUBLISH_TITLE_LIMITS = {"youtube": 100, "tiktok": 150}


def _title_limit_for_platform(platform):
    return _PUBLISH_TITLE_LIMITS.get(str(platform or "").strip().lower(), 100)


def seo_title_score(title):
    """Offline 0-100 SEO heuristic for a publish title (no network calls).

    Uses the v7.22 SEO engine (scripts/seo_titles.score_title): length sweet
    spot, hook phrasing, keyword presence and clickbait penalties. Returns
    None when the title is empty or the engine is unavailable — advisory
    only, never a hard gate.
    """
    title = str(title or "").strip()
    if not title:
        return None
    try:
        from scripts import seo_titles

        result = seo_titles.score_title(title)
    except Exception:
        return None
    if not isinstance(result, dict):
        return None
    try:
        score = float(result.get("score"))
    except (TypeError, ValueError):
        return None
    return {"score": score, "breakdown": result.get("breakdown") or {}}


def next_best_publish_at(platform="youtube"):
    """Nearest future best-time slot (ISO-8601) for a platform, or None.

    Uses the static algorithm window table (seo_titles.suggest_next_slots) —
    fully offline, deterministic. The publish panel uses it to prefill the
    schedule field; the autopilot/scheduler can call it too.
    """
    try:
        from scripts import seo_titles

        slots = seo_titles.suggest_next_slots(
            str(platform or "youtube").lower(), count=1
        )
    except Exception:
        return None
    return slots[0] if slots else None


_STYLE_AR_NAMES = {
    "title_question": "العناوين بصيغة سؤال (؟)",
    "title_number": "العناوين التي تحتوي رقماً",
    "title_hook_word": "العناوين بكلمة خطاف (كيف/لماذا/سر/how/why…)",
}


def format_channel_lessons(project_path):
    """Read-only Arabic summary of what the channel's analytics taught us.

    Reads ``performance_insights.json`` (written by
    ``python -m scripts.performance_loop --project X`` after YouTube
    Analytics is enabled) and renders the content-aware lessons + measured
    best hours as a human text block for the WebUI. Never raises.
    """
    path = os.path.join(project_path, "performance_insights.json")
    insights = _load_json(path)
    if not insights:
        return (
            "📊 لا يوجد ملف تحليلات بعد في هذا المشروع.\n"
            "لتفعيل التعلّم من نتائج قناتك:\n"
            "1) فعّل YouTube Analytics API (نفس إعداد الـ OAuth للرفع).\n"
            '2) شغّل: python -m scripts.performance_loop --project "{}"\n'
            "3) بعدها سيظهر هنا: أي صيغة عناوين/مواضيع تجلب مشاهدات، "
            "وأفضل ساعات النشر المقاسة على قناتك.".format(project_path)
        )
    lines = ["📊 **دروس قناتك (من أداء فيديوهاتك المنشورة):**"]
    with_metrics = int(insights.get("with_metrics") or 0)
    lines.append("— الفيديوهات المقاسة: {}".format(with_metrics))
    content = insights.get("content_insights") or {}
    overall = content.get("overall_avg_views")
    if overall is not None:
        lines.append("— معدل مشاهداتك: {:.0f}".format(float(overall)))
        categories = content.get("categories") or {}
        field_names = {
            "hook_type": "صيغة الخطاف (hook_type)",
            "angle": "الزاوية التحريرية (angle)",
            "topic": "الموضوع (topic)",
        }
        for field, label in field_names.items():
            rows = categories.get(field) or []
            if not rows:
                continue
            top = sorted(rows, key=lambda row: -abs(row.get("delta_pct") or 0))
            best = top[0] if top else None
            if best and (best.get("delta_pct") or 0) > 0:
                lines.append(
                    "• أفضل {}: «{}» — {}% عن معدلك (من {} فيديو)".format(
                        label,
                        best.get("value"),
                        best.get("delta_pct"),
                        best.get("samples"),
                    )
                )
        styles = content.get("title_styles") or []
        if styles:
            styles = sorted(styles, key=lambda row: -abs(row.get("delta_pct") or 0))
            winner = styles[0]
            lines.append(
                "• {}: {} مشاهدة معها مقابل {} بدونها ({}% — {} ضد {} فيديو)".format(
                    _STYLE_AR_NAMES.get(winner.get("style"), winner.get("style")),
                    winner.get("avg_views_yes"),
                    winner.get("avg_views_no"),
                    winner.get("delta_pct"),
                    winner.get("samples_yes"),
                    winner.get("samples_no"),
                )
            )
    best_hours = insights.get("best_hours")
    if best_hours:
        lines.append(
            "— أفضل ساعات النشر المقاسة (بتوقيتك المحلي): {}".format(
                ", ".join(str(h) for h in best_hours)
            )
        )
    return "\n".join(lines)


def _seo_advisory_line(title):
    """One human line summarizing the SEO check of a publish title."""
    check = seo_title_score(title)
    if check is None:
        return ""
    score = check["score"]
    hints = []
    breakdown = check.get("breakdown") or {}
    if breakdown.get("length", 25.0) < 8:
        hints.append("الطول الأمثل 35-70 حرفاً")
    if breakdown.get("hook", 0.0) < 4:
        hints.append("أضف صيغة سؤال أو رقم أو كلمة قوية (كيف/لماذا/سر/خطأ)")
    if breakdown.get("penalty", 0.0) >= 5:
        hints.append("تجنب التكرار والعناوين الكبيرة كلها")
    suffix = (" — " + "؛ ".join(hints)) if hints else ""
    verdict = "قوي" if score >= 60 else ("متوسط" if score >= 40 else "ضعيف")
    return "[seo] جودة عنوان SEO: {:.0f}/100 ({}){}".format(score, verdict, suffix)


def _publish_result(status, video_path, title="", publish_at=None, **extra):
    result = {
        "status": status,
        "video": os.path.basename(video_path or ""),
        "video_path": os.path.abspath(video_path) if video_path else "",
        "title": title or "",
        "publish_at": publish_at,
    }
    result.update({key: value for key, value in extra.items() if value is not None})
    return result


def effective_privacy(privacy_status=None, env=None):
    """Resolve the privacy status an upload will actually use.

    The uploader falls back to ``YT_PRIVACY`` when it is not handed a value,
    so the panel must reason about the *effective* privacy — not just the
    argument — or the public-confirmation guard and the recorded status can
    disagree with what is really uploaded.
    """
    explicit = str(privacy_status or "").strip().lower()
    if explicit:
        return explicit
    fallback = os.getenv("YT_PRIVACY") if env is None else env
    return str(fallback or "private").strip().lower() or "private"


def _resolve_privacy(privacy_status):
    """Prefer a shared resolver from scripts.upload_gate when one exists."""
    try:
        from scripts import upload_gate as ug

        resolver = getattr(ug, "effective_privacy", None)
        if callable(resolver):
            resolved = resolver(privacy_status)
            if resolved:
                return str(resolved).strip().lower()
    except Exception:
        pass
    return effective_privacy(privacy_status)


def _audio_qc_upload_allowed(project_path, video_path):
    """Real uploads require a current, passing per-file Audio QC result."""
    try:
        from scripts import audio_qc

        report = audio_qc.ensure_file_report(project_path, video_path)
        return audio_qc.gate_allows(report)
    except Exception as exc:
        return False, "Audio QC failed closed: {}".format(str(exc)[:300])


def _polish_upload_allowed(project_path, video_path):
    """Explicit final_polished selection must not bypass a failed report."""
    normal = os.path.normcase(os.path.abspath(video_path or ""))
    if os.path.normcase(os.sep + "final_polished" + os.sep) not in normal:
        return True, ""
    report = _load_json(os.path.join(project_path, "polish_report.json"))
    if not report:
        return True, ""
    for item in report.get("clips", []) or []:
        if os.path.abspath(str(item.get("output") or "")) == os.path.abspath(
            video_path
        ) or item.get("video") == os.path.basename(video_path):
            safe = (
                item.get("media_validated")
                and not item.get("fallback_used")
                and not item.get("failed_stages")
                and item.get("quality_status") in {"enhanced", "partial"}
            )
            if safe:
                return True, ""
            return (
                False,
                "final_polished output is fallback/failed/invalid according to polish_report.json",
            )
    return False, "final_polished output has no matching entry in polish_report.json"


def _thumbnail_paths_for(project_path, video_path):
    """Candidate thumbnail locations for a clip, in preference order."""
    stem = os.path.splitext(os.path.basename(video_path or ""))[0]
    index = clip_index(video_path)
    directory = os.path.join(project_path, "thumbnails")
    candidates = []
    for name in (
        "{}.png".format(stem),
        "{}_thumbnail.png".format(stem),
        "{}.jpg".format(stem),
        "{}_thumbnail.jpg".format(stem),
    ):
        candidates.append(os.path.join(directory, name))
    if index is not None:
        for name in (
            "{:03d}_thumbnail.png".format(index),
            "{:03d}_thumbnail.jpg".format(index),
            "{:03d}.png".format(index),
        ):
            candidates.append(os.path.join(directory, name))
    for number in (1, 2, 3):
        candidates.append(os.path.join(directory, "{}_v{}.png".format(stem, number)))
    seen = []
    for candidate in candidates:
        if candidate not in seen and os.path.isfile(candidate):
            seen.append(candidate)
    return seen


def _thumbnail_variant_kind(path):
    """Test & Compare variant kind of a thumbnail path (v1..v3)."""
    match = re.search(r"_v(\d+)\.(?:png|jpg)$", os.path.basename(path or ""))
    return "v{}".format(match.group(1)) if match else ""


def _preferred_thumbnail_variant(project_path):
    """Best-measured Test & Compare variant kind from the insights file."""
    try:
        from scripts import performance_weights

        with open(
            os.path.join(project_path, "performance_insights.json"),
            "r",
            encoding="utf-8",
        ) as f:
            insights = json.load(f)
        return str(performance_weights.preferred_thumbnail_variant(insights) or "")
    except (OSError, ValueError, Exception):
        return ""


def _thumbnail_for_clip(project_path, video_path, title=""):
    """Resolve an existing thumbnail, or auto-generate one (best-effort).

    Returns a path (str) or None. Generation is opt-out via
    ``VIRALCUTTER_UPLOAD_THUMBNAIL=0``; a missing Pillow/ffmpeg/frame simply
    returns None — attaching a thumbnail must never block publishing.
    """
    existing = _thumbnail_paths_for(project_path, video_path)
    # Round 2 item 6: when the performance loop has measured which Test &
    # Compare variant wins on CTR, prefer that variant kind over the default
    # order. Unmeasured projects keep the primary-first behaviour.
    preferred_kind = _preferred_thumbnail_variant(project_path)
    if preferred_kind:
        for candidate in existing:
            if _thumbnail_variant_kind(candidate) == preferred_kind:
                return candidate
    if existing:
        return existing[0]
    if os.getenv("VIRALCUTTER_UPLOAD_THUMBNAIL", "1").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return None
    stem = os.path.splitext(os.path.basename(video_path or ""))[0]
    output = os.path.join(project_path, "thumbnails", "{}.png".format(stem))
    try:
        from scripts import thumbnail_generator

        variants_env = os.getenv("VIRALCUTTER_THUMBNAIL_VARIANTS", "1")
        variants_on = variants_env.strip().lower() not in {"0", "false", "no", "off"}
        if variants_on:
            # Round 2 item 4: keep the primary thumbnail path but also write
            # 2-3 Test & Compare variants next to it. Attaching still uses
            # the primary; the user picks a winner in YouTube Studio.
            meta = clip_metadata(project_path, video_path)
            alt_titles = [
                str(t).strip()
                for t in (meta.get("alt_titles") or [])
                if str(t or "").strip()
            ]
            var_result = thumbnail_generator.generate_variants(
                video_path,
                titles=[str(title or "")[:80]] + alt_titles[:2],
                out_dir=os.path.join(project_path, "thumbnails"),
                base_name=stem,
                at_seconds=0.0,
                max_variants=3,
            )
            if var_result.get("ok") and var_result.get("variants"):
                primary = var_result["variants"][0]["out"]
                if os.path.isfile(primary):
                    if primary != output:
                        try:
                            import shutil

                            shutil.copyfile(primary, output)
                        except OSError:
                            pass
                    return output
        result = thumbnail_generator.generate_thumbnail(
            video_path, title=str(title or "")[:80], out=output, at_seconds=0.0
        )
        if result.get("ok") and os.path.isfile(output):
            return output
    except Exception:
        pass
    return None


def _upload_worker(
    project_path,
    platform,
    video_path,
    title,
    caption,
    hashtags,
    dry_run,
    music_gate,
    client_secrets_path,
    privacy_status,
    publish_at,
    out_queue,
    oauth_full_access=False,
    require_existing_auth=False,
    public_confirm=False,
    full_video_url="",
    channel_url="",
    variant_policy="off",
):
    final_result = None

    def emit(msg):
        out_queue.put({"type": "log", "message": str(msg)})

    def finish(result):
        nonlocal final_result
        final_result = result
        out_queue.put({"type": "result", "result": result})

    try:
        resolved_privacy = _resolve_privacy(privacy_status)
        polish_ok, polish_detail = _polish_upload_allowed(project_path, video_path)
        if not polish_ok and not dry_run:
            emit("⛔ تم منع رفع final_polished غير الموثوق: {}".format(polish_detail))
            finish(
                _publish_result(
                    "blocked",
                    video_path,
                    title,
                    publish_at,
                    error=polish_detail,
                    reason="polish_report",
                )
            )
            return
        if platform == "youtube" and not dry_run:
            from scripts import content_guard

            channel_state = content_guard.channel_status(project_path, "youtube")
            if channel_state.get("locked"):
                emit(
                    "⛔ تم إيقاف الرفع قبل OAuth: قاطع دائرة القناة مقفول بسبب حادثة سياسة مسجلة."
                )
                finish(
                    _publish_result(
                        "blocked",
                        video_path,
                        title,
                        publish_at,
                        error="channel circuit breaker locked",
                        reason="channel_circuit_breaker",
                    )
                )
                return
        if (
            platform == "youtube"
            and resolved_privacy == "public"
            and not dry_run
            and not public_confirm
        ):
            emit("❌ تم إيقاف الرفع العام: فعّل تأكيد النشر العام أولاً.")
            finish(
                _publish_result(
                    "blocked",
                    video_path,
                    title,
                    publish_at,
                    error="public confirmation required",
                    reason="public_confirmation",
                )
            )
            return
        if not dry_run:
            audio_ok, audio_detail = _audio_qc_upload_allowed(project_path, video_path)
            if not audio_ok:
                emit("⛔ تم منع الرفع بسبب Audio QC: {}".format(audio_detail))
                finish(
                    _publish_result(
                        "blocked",
                        video_path,
                        title,
                        publish_at,
                        error=audio_detail,
                        reason="audio_qc",
                    )
                )
                return
        from scripts import upload_gate as ug
        from webui import publish_history

        # Chapters (round 2 item 5): long-form (yt_standard) uploads append
        # the project's chapter block to the description when one was written.
        if platform == "yt_standard":
            try:
                from scripts import chapters as _chapters

                _chapters_text = _chapters.chapters_block_file(project_path)
                if _chapters_text and not _chapters.contains_chapter_block(caption):
                    caption = "\n\n".join(
                        part for part in (caption, _chapters_text) if part
                    )
                    emit(
                        "[chapters] chapter block appended to the yt_standard description"
                    )
            except Exception as chapters_exc:
                emit(
                    "[chapters] chapter block not appended: {}".format(
                        str(chapters_exc)[:160]
                    )
                )

        if platform == "youtube" and client_secrets_path:
            from webui.youtube_credentials import (
                replace_client_secrets,
                store_client_secrets,
            )

            stored = store_client_secrets(client_secrets_path)
            if stored.get("changed"):
                stored = replace_client_secrets(
                    client_secrets_path, invalidate_token=True
                )
            client_secrets_path = stored["path"]
            emit("[oauth] client secrets validated and stored securely")
        emit("[gate] running safety checks for #{} ...".format(clip_index(video_path)))
        uploader_kwargs = {"dry_run": dry_run, "music_gate": music_gate}
        upload_kwargs = {}
        if platform == "youtube":
            if client_secrets_path:
                uploader_kwargs["client_secrets_path"] = client_secrets_path
            if oauth_full_access:
                uploader_kwargs["oauth_full_access"] = True
            # Forward the EFFECTIVE privacy so the uploader never silently falls
            # back to os.getenv("YT_PRIVACY", ...) and disagrees with the guard
            # above or the history entry below. The explicit value is always
            # applied to the uploader instance; the extra call kwargs are only
            # added when it differs from the uploader's env-derived default, to
            # keep the adapter call signature stable for the common case.
            if resolved_privacy != effective_privacy(None):
                uploader_kwargs["privacy_status"] = resolved_privacy
                upload_kwargs["privacy_status"] = resolved_privacy
            if publish_at:
                uploader_kwargs["publish_at"] = publish_at
                upload_kwargs["publish_at"] = publish_at
            # Funnel template (feat/pro-cutting item 1): when the source
            # full-video URL is known, the description MUST open with the
            # funnel line and the Short's related video targets the source.
            funnel_source_url = str(full_video_url or "").strip() or project_source_url(
                project_path
            )
            channel_url = str(channel_url or "").strip() or project_channel_url(
                project_path
            )
            if funnel_source_url:
                upload_kwargs["source_url"] = funnel_source_url
                upload_kwargs["channel_url"] = str(channel_url or "").strip()
                emit(
                    "[funnel] the description starts with the full-video link: {}".format(
                        funnel_source_url
                    )
                )
                from scripts import platform_templates as _funnel_templates

                funnel_title = _funnel_templates.apply_title_pattern(title)
                if funnel_title and funnel_title != str(title or "").strip():
                    title = funnel_title
                    emit("[funnel] title pattern applied: {}".format(title))

                if _funnel_templates.related_video_policy("yt_shorts") == "source":
                    source_id = source_video_id(funnel_source_url)
                    if source_id:
                        upload_kwargs["related_video_source"] = source_id
                        # YouTube Data API v3 exposes NO related-video field:
                        # the pin is recorded and surfaced as a Studio deep
                        # link instead of pretending the API set it.
                        emit(
                            "[related-video] اربط الفيديو المرتبط يدوياً بعدها: "
                            "https://studio.youtube.com/video/{}/edit".format(source_id)
                        )
        prior = None
        if not dry_run:
            prior = publish_history.find_success(
                project_path, platform=platform, video_path=video_path
            )
        if prior:
            prior_id = prior.get("video_id") or prior.get("url") or "سجل سابق"
            emit("⚠️ تم تخطي الرفع: هذا الملف رُفع سابقاً بنجاح ({})".format(prior_id))
            finish(
                _publish_result(
                    "skipped_duplicate",
                    video_path,
                    title,
                    publish_at,
                    prior_id=prior_id,
                    reason="publish_history",
                )
            )
            return

        # SEO advisory (YouTube only, never blocking): surface the offline
        # 0-100 SEO score of the effective title before the uploader runs.
        if platform == "youtube" and title:
            seo_line = _seo_advisory_line(title)
            if seo_line:
                emit(seo_line)

        # Cross-platform variants are allowed only after the project documents
        # meaningful original commentary, voiceover, analysis, or B-roll.
        # Speed, crop, mirror, and color changes alone do not make a repost
        # original. Dry runs preview the compliance decision without rendering.
        upload_video = video_path
        variant_line = ""
        if str(variant_policy or "off").strip().lower() != "off":
            try:
                from scripts import platform_variant

                if dry_run:
                    decision = platform_variant.plan_variant(
                        project_path,
                        video_path,
                        platform,
                        policy=str(variant_policy or "off"),
                    )
                else:
                    decision = platform_variant.maybe_variant(
                        project_path,
                        video_path,
                        platform,
                        policy=str(variant_policy or "off"),
                    )
                if decision and decision.get("action") == "review":
                    variant_line = "[variant] {}".format(
                        decision.get(
                            "reason", "original editorial contribution is required"
                        )
                    )
                    emit(variant_line)
                    finish(
                        _publish_result(
                            "blocked",
                            video_path,
                            title,
                            publish_at,
                            error=decision.get(
                                "reason", "original contribution required"
                            ),
                            reason="original_contribution_required",
                        )
                    )
                    return
                if decision and decision.get("action") == "variate":
                    if dry_run:
                        variant_line = (
                            "[variant] سيُرفع هذا المقطع كنسخة مختلفة على {} "
                            "(seed {}) بدل البايتات نفسها لتجنب تكرار المحتوى."
                        ).format(platform, decision.get("seed"))
                    else:
                        upload_video = decision["path"]
                        variant_line = (
                            "[variant] نسخة مختلفة لمنصة {} جاهزة: {} "
                            "(تحويلات: {})".format(
                                platform,
                                os.path.basename(upload_video),
                                ", ".join(decision.get("transforms") or []) or "بدون",
                            )
                        )
                elif decision and decision.get("reason"):
                    variant_line = "[variant] {}".format(decision.get("reason"))
            except Exception as error:
                variant_line = (
                    "[variant] تعذّر التباين المتقاطع للمنصات ({}); سيُرفع الأصل.".format(
                        str(error)[:200]
                    )
                )
            if variant_line:
                emit(variant_line)

        try:
            from scripts import seo_titles as _seo

            title_variants = _seo.title_candidates(
                clip_metadata(project_path, video_path)
            )
        except Exception:
            title_variants = [title]
        if len(title_variants) > 1:
            emit(
                "[title] مرشحات العنوان (A/B): {} — المنشور: {}".format(
                    " | ".join(title_variants), title
                )
            )
        uploader = ug.UPLOADERS[platform](project_path, **uploader_kwargs)
        if platform == "youtube":
            # Belt-and-braces: pin the effective value on the uploader so a real
            # adapter can never substitute the YT_PRIVACY fallback.
            uploader.privacy_status = resolved_privacy
        if require_existing_auth and platform == "youtube":
            uploader.ensure_authenticated()
            emit("[oauth] قناة YouTube متصلة والتوكن صالح قبل الرفع")
        # WebUI uploads must validate the actual rendered file and have both
        # safety and risk reports before any real API call.
        uploader.validate_video = True
        uploader.require_preflight_reports = not dry_run
        uploader.require_quality_gate = not dry_run
        result = uploader.upload(
            upload_video,
            title,
            caption,
            hashtags,
            index=clip_index(upload_video),
            **upload_kwargs,
        )

        # Attach a project thumbnail to the fresh YouTube video (best-effort,
        # never blocking): thumbnails drive CTR, and the generator existed
        # standalone since v7.23 without ever reaching the upload.
        thumb_attached = False
        if (
            platform == "youtube"
            and not dry_run
            and (result or {}).get("video_id") is not None
        ):
            thumb_path = _thumbnail_for_clip(project_path, upload_video, title)
            attach = getattr(uploader, "attach_thumbnail", None)
            if thumb_path and callable(attach):
                try:
                    attach(str(result.get("video_id")), thumb_path)
                    thumb_attached = True
                    emit(
                        "[thumbnail] ✅ صورة مصغرة مرفوعة مع الفيديو: {}".format(
                            os.path.basename(thumb_path)
                        )
                    )
                except Exception as exc:
                    emit(
                        "[thumbnail] ⚠️ تعذّر رفع الصورة المصغرة (لا يمنع النشر): {}".format(
                            str(exc)[:200]
                        )
                    )

        meta = clip_metadata(project_path, video_path)
        # A/B title variants (round 2 item 7): record the full candidate list
        # and the exact variant that shipped so the performance loop can learn
        # which headline shape earns the views.
        record_extra = {
            "hashtags": ",".join(hashtags or meta.get("hashtags") or []),
            "topic": meta.get("topic") or "",
            "angle": meta.get("angle") or "",
            "hook_type": meta.get("hook_type") or "",
            "title_template": meta.get("title_template") or "",
            "title_variant_used": title,
            "title_variants": json.dumps(title_variants, ensure_ascii=False)[:1000],
        }
        # Self-certification preflight (feat/pro-cutting item 7): scan the
        # real content (title + caption + segment transcript) against the
        # questionnaire categories BEFORE shipping. WARN only — the scan is
        # heuristic, the upload gate (content_guard) stays the blocker — but
        # the answer set is recorded so the loop can learn which clips the
        # channel keeps certifying as clean.
        try:
            from scripts import self_certify

            cert = self_certify.self_certify(
                meta.get("transcript_text") or "",
                title=title,
                caption=caption,
            )
            if not cert.get("clean"):
                for flag in (cert.get("recommended") or {}).get("contains_yes") or []:
                    emit(
                        "⚠️ [self-cert] فئة «{}» — تأكد من إجابة الاستبيان في "
                        "Studio قبل النشر.".format(flag)
                    )
            record_extra["self_cert"] = json.dumps(
                cert, ensure_ascii=False, separators=(",", ":")
            )
        except Exception as exc:
            emit(
                "[self-cert] ⚠️ تعذّر الفحص المسبق للاستبيان (لا يمنع النشر): {}".format(
                    str(exc)[:200]
                )
            )
        if thumb_attached:
            record_extra["thumbnail"] = os.path.basename(thumb_path)
        if str(funnel_source_url or "").strip():
            record_extra["source_video_url"] = str(
                funnel_source_url
            ).strip()  # Funnel baseline (round 2 item 6b): the source video's view count at
        # publish time. funnel_metrics later diffs it against the live count
        # ("source views before vs after the Short"). Best-effort: a failed
        # lookup records nothing instead of a fake zero.
        try:
            from scripts import funnel_metrics as _funnel_metrics

            _source_id = _funnel_metrics.source_video_id(
                str(funnel_source_url or "").strip()
            )
            if _source_id and not dry_run:
                _baseline = _funnel_metrics.source_view_count(_source_id)
                if _baseline is not None:
                    record_extra["source_views_at_publish"] = int(_baseline)
                    record_extra["source_video_id"] = _source_id
        except Exception as baseline_exc:
            emit(
                "[funnel] source-view baseline not captured: {}".format(
                    str(baseline_exc)[:120]
                )
            )
        related_source = (result or {}).get("related_video_source")
        if related_source:
            record_extra["related_video_source"] = str(related_source)
        if upload_video != video_path:
            record_extra["variant_of"] = os.path.basename(video_path)
        # Studio checklist (feat/pro-cutting round 2 item 1): the four
        # manual Studio steps as one-click deep links, stored with the event.
        try:
            from scripts import studio_checklist

            checklist_items = studio_checklist.build_checklist(
                str((result or {}).get("video_id") or ""),
                related_video_id=str(record_extra.get("related_video_source") or ""),
                thumbnail_uploaded=bool(thumb_attached),
                source_video_url=str(funnel_source_url or "").strip(),
            )
            record_extra["studio_checklist"] = studio_checklist.checklist_json(
                checklist_items
            )
            emit(studio_checklist.checklist_markdown(checklist_items))
        except Exception as exc:
            emit(
                "[studio-checklist] ⚠️ تعذّر إنشاء قائمة الاستوديو (لا يمنع النشر): {}".format(
                    str(exc)[:200]
                )
            )
        # Item 2 (round 2): first "what does YouTube think?" read-back. When
        # an OAuth token exists the verdicts are read NOW and stored in the
        # same event as the upload; otherwise honest "pending" with a 24h
        # recheck stamp (performance_loop rechecks due entries).
        if str((result or {}).get("video_id") or "").strip():
            try:
                from scripts import verify_youtube

                verdict_checks = verify_youtube.initial_checks(str(result["video_id"]))
                record_extra[verify_youtube.HISTORY_KEY] = verify_youtube.checks_json(
                    verdict_checks
                )
                emit(
                    "[verify] ⚖️ حقوق النشر: {} — ملاءمة الإعلانات: {}".format(
                        (verdict_checks.get(verify_youtube.COPYRIGHT) or {}).get(
                            "verdict"
                        )
                        or "pending",
                        (verdict_checks.get(verify_youtube.AD_SUITABILITY) or {}).get(
                            "verdict"
                        )
                        or "pending",
                    )
                )
            except Exception as exc:
                emit(
                    "[verify] ⚠️ تعذّر التحقق بعد الرفع (لا يمنع النشر): {}".format(
                        str(exc)[:200]
                    )
                )
        publish_history.record(
            project_path,
            platform=platform,
            video_path=upload_video,
            title=title,
            result=result,
            privacy_status=resolved_privacy,
            publish_at=publish_at,
            extra=record_extra,
        )
        # Close the loop automatically: the next Learn/Insights refresh will
        # re-query YouTube even if the TTL has not expired.
        try:
            from scripts import performance_loop

            if str(result.get("status") or "").lower() in {"uploaded", "scheduled"}:
                performance_loop.mark_refresh_due(project_path)
        except Exception:
            pass
        try:
            from webui import project_store

            project_store.update_manifest(
                project_path,
                publish_status=result.get("status", "uploaded"),
                last_publish={
                    "platform": platform,
                    "video": os.path.basename(upload_video),
                    "video_id": result.get("video_id"),
                    "url": result.get("url"),
                    "privacy_status": resolved_privacy,
                    "publish_at": publish_at,
                    "variant_of": os.path.basename(video_path)
                    if upload_video != video_path
                    else None,
                },
            )
        except Exception:
            pass
        # Item 3 (round 2): upload the clip's Arabic caption track (.ar.srt)
        # when one was exported with the sentence-style captions.
        if platform == "youtube" and str((result or {}).get("video_id") or "").strip():
            try:
                from scripts import arabic_captions

                srt_path = arabic_captions.arabic_srt_for_clip(project_path, video_path)
                if srt_path:
                    caption_state = arabic_captions.upload_caption_track(
                        str(result["video_id"]), srt_path
                    )
                    emit(
                        "[captions] 🈶 مسار الترجمة العربية: {}".format(
                            caption_state.get("state")
                        )
                    )
            except Exception as exc:
                emit(
                    "[captions] ⚠️ تعذر رفع الترجمة العربية (لا يمنع النشر): {}".format(
                        str(exc)[:200]
                    )
                )
        status = str(
            (result or {}).get("status") or ("scheduled" if publish_at else "uploaded")
        )
        if status == "dry-run":
            status = "dry_run"
        base_publish_at = (result or {}).get("publish_at") or publish_at
        extras = {
            key: value
            for key, value in (result or {}).items()
            if key not in {"status", "video", "video_path", "title", "publish_at"}
        }
        normalized = _publish_result(
            status, upload_video, title, base_publish_at, **extras
        )
        emit("✅ {}".format(json.dumps(normalized, ensure_ascii=False)))
        finish(normalized)
    except Exception as e:
        if hasattr(e, "reasons"):
            source_labels = {
                "publish_blocklist": "قائمة منع النشر",
                "safety_report": "تقرير الأمان",
                "semantic_safety": "الأمان الدلالي",
                "metadata_compliance": "بيانات النشر",
                "missing_video": "الفيديو النهائي",
                "media_validation": "فحص ملف الفيديو",
                "music_fingerprint": "بصمة الموسيقى",
                "visual_safety": "الفحص البصري للمحتوى الحساس",
                "content_guard": "حارس المصدر ومنع التكرار",
            }
            emit("❌ تم منع الرفع بواسطة بوابة الأمان قبل الاتصال بالمنصة.")
            for reason in getattr(e, "reasons", []):
                label = source_labels.get(
                    reason.get("source"), reason.get("source", "فحص")
                )
                emit("  • {}: {}".format(label, reason.get("detail", "راجع التقرير")))
            finish(
                _publish_result(
                    "blocked",
                    video_path,
                    title,
                    publish_at,
                    error=str(e)[:1000],
                    reason="safety_gate",
                    reasons=getattr(e, "reasons", []),
                )
            )
        try:
            from webui import publish_history

            publish_history.record(
                project_path,
                platform=platform,
                video_path=video_path,
                title=title,
                error=e,
                privacy_status=resolved_privacy,
                publish_at=publish_at,
            )
            try:
                from webui import project_store

                project_store.update_manifest(
                    project_path,
                    publish_status="failed",
                    last_publish_error=str(e)[:1000],
                )
            except Exception:
                pass
        except Exception:
            pass
        if final_result is None:
            finish(
                _publish_result(
                    "failed", video_path, title, publish_at, error=str(e)[:1000]
                )
            )
        emit("❌ {}".format(e))
    finally:
        if final_result is None:
            finish(
                _publish_result(
                    "failed",
                    video_path,
                    title,
                    publish_at,
                    error="upload worker ended without a result",
                )
            )
        out_queue.put({"type": "done"})


def studio_checklist_view(project_path, platform, video_path):
    """Markdown checklist of the manual Studio steps for a published clip."""
    from scripts import studio_checklist
    from webui import publish_history

    event = publish_history.find_success(
        project_path, platform=platform, video_path=video_path
    )
    if not event:
        return "لم يُنشر هذا المقطع بعد — لا توجد قائمة استوديو."
    items = studio_checklist.checklist_for_event(event)
    if not items:
        return (
            "لا توجد قائمة استوديو محفوظة لهذا النشر (نشر سابق لنظام قائمة الاستوديو)."
        )
    return studio_checklist.checklist_markdown(items)


FUNNEL_HEADERS = [
    "Short",
    "Views",
    "Related clicks",
    "Source before",
    "Source after",
    "Source delta",
    "Subs gained",
    "Funnel score",
]


def funnel_view(project_path):
    """Per-Short funnel rows for the WebUI (round 2 item 6b)."""
    from scripts import funnel_metrics

    if not str(project_path or "").strip() or not os.path.isdir(project_path):
        return [], "اختر مشروعاً أولاً."
    try:
        report = funnel_metrics.funnel_rows(project_path)
    except Exception as exc:
        return [], "⚠️ تعذّر جمع مقاييس القمع: {}".format(str(exc)[:160])
    rows = []
    for row in report.get("rows") or []:
        rows.append(
            [
                str(row.get("title") or row.get("video_id") or "")[:60],
                row.get("short_views"),
                row.get("related_clicks"),
                row.get("source_before"),
                row.get("source_after"),
                row.get("source_delta"),
                row.get("subscribers_gained"),
                row.get("funnel_score"),
            ]
        )
    note = ""
    totals = report.get("totals") or {}
    if rows:
        note = "**الإجمالي:** مشاهدات الشورتات {} — نقرات مرتبط {} — مشاهدات المصدر {} — مشتركون {}".format(
            totals.get("short_views") if totals.get("short_views") is not None else "—",
            totals.get("related_clicks")
            if totals.get("related_clicks") is not None
            else "—",
            totals.get("source_delta")
            if totals.get("source_delta") is not None
            else "—",
            totals.get("subscribers_gained")
            if totals.get("subscribers_gained") is not None
            else "—",
        )
    if report.get("unavailable_reason"):
        note = "ℹ️ {}\n\n{}".format(report["unavailable_reason"], note).strip()
    if not rows:
        return [], note or "لا توجد مقاطع منشورة بنجاح بعد."
    return rows, note


def stream_upload(
    project_path,
    platform,
    video_path,
    title,
    caption,
    hashtags,
    dry_run,
    music_gate,
    client_secrets_path=None,
    privacy_status="private",
    publish_at=None,
    oauth_full_access=False,
    require_existing_auth=False,
    public_confirm=False,
    full_video_url="",
    channel_url="",
    variant_policy="off",
):
    """Yield log lines and return one structured result to a batch caller."""
    out_queue = queue.Queue()
    if not video_path or not os.path.exists(video_path):
        result = _publish_result(
            "failed", video_path, title, publish_at, error="Clip not found"
        )
        yield "Clip not found."
        return result
    thread = threading.Thread(
        target=_upload_worker,
        args=(
            project_path,
            platform,
            video_path,
            title,
            caption,
            hashtags,
            dry_run,
            music_gate,
            client_secrets_path,
            privacy_status,
            publish_at,
            out_queue,
            oauth_full_access,
            require_existing_auth,
            public_confirm,
            full_video_url,
            channel_url,
            variant_policy,
        ),
        daemon=True,
    )
    thread.start()

    lines = []
    result = None
    while True:
        try:
            event = out_queue.get(timeout=0.5)
        except queue.Empty:
            if not thread.is_alive():
                break
            yield "\n".join(lines)
            continue
        if isinstance(event, dict) and event.get("type") == "done":
            break
        if isinstance(event, dict) and event.get("type") == "result":
            result = event.get("result")
            continue
        message = event.get("message") if isinstance(event, dict) else str(event)
        lines.append(str(message))
        yield "\n".join(lines)
    result = result or _publish_result(
        "failed",
        video_path,
        title,
        publish_at,
        error="upload worker returned no structured result",
    )
    lines.append("Upload finished. [{}]".format(result.get("status", "failed")))
    yield "\n".join(lines)
    return result


def _write_batch_report(project_path, report):
    path = os.path.join(project_path, "publish_batch_report.json")
    temp = path + ".tmp"
    try:
        with open(temp, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        os.replace(temp, path)
    except OSError:
        try:
            if os.path.exists(temp):
                os.remove(temp)
        except OSError:
            pass


def _batch_summary(items):
    counts = dict.fromkeys(
        ("uploaded", "scheduled", "dry_run", "skipped_duplicate", "blocked", "failed"),
        0,
    )
    for item in items:
        status = str(item.get("status") or "failed")
        counts[status] = counts.get(status, 0) + 1
    return {
        "total": len(items),
        "counts": counts,
        "successful": counts["uploaded"] + counts["scheduled"],
        "failed": counts["failed"],
        "blocked": counts["blocked"],
        "skipped_duplicate": counts["skipped_duplicate"],
    }


def stream_upload_batch(
    project_path,
    platform,
    video_paths,
    dry_run,
    music_gate,
    client_secrets_path=None,
    privacy_status="private",
    publish_at=None,
    oauth_full_access=False,
    require_existing_auth=False,
    public_confirm=False,
    schedule_interval_minutes=60,
    retry_failed_only=False,
    full_video_url="",
    channel_url="",
    variant_policy="off",
):
    """Upload every selected clip and persist exact per-clip outcomes."""
    all_paths = [
        os.path.abspath(os.fspath(path))
        for path in (video_paths or [])
        if path and os.path.isfile(path) and str(path).lower().endswith(".mp4")
    ]
    previous = _load_json(os.path.join(project_path, "publish_batch_report.json")) or {}
    if retry_failed_only:
        failed_paths = {
            os.path.abspath(str(item.get("video_path")))
            for item in previous.get("items", []) or []
            if isinstance(item, dict)
            and item.get("status") == "failed"
            and item.get("video_path")
        }
        paths = [path for path in all_paths if path in failed_paths]
    else:
        paths = all_paths
    if not paths:
        if retry_failed_only:
            yield "✅ لا توجد عناصر فاشلة في آخر دفعة تحتاج إلى retry."
        else:
            yield "❌ لم يتم العثور على ملفات MP4 صالحة لمصدر الرفع."
        return
    try:
        interval_raw = (
            60 if schedule_interval_minutes is None else schedule_interval_minutes
        )
        interval = float(interval_raw)
        if not math.isfinite(interval) or interval < 1 or interval > 10080:
            raise ValueError("invalid interval")
    except (TypeError, ValueError):
        yield "❌ الفاصل يجب أن يكون بين 1 و10080 دقيقة؛ أوقف النظام هذه الدفعة قبل الرفع."
        return
    schedule_start = None
    if publish_at:
        try:
            schedule_start = datetime.datetime.fromisoformat(
                str(publish_at).strip().replace("Z", "+00:00")
            )
            if schedule_start.tzinfo is None:
                raise ValueError("missing timezone")
        except (TypeError, ValueError):
            yield "❌ وقت بداية الجدولة أو الفاصل غير صالح؛ أوقف النظام هذه الدفعة قبل الرفع."
            return
    report = {
        "version": 1,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "platform": platform,
        "dry_run": bool(dry_run),
        "retry_failed_only": bool(retry_failed_only),
        "requested_paths": all_paths,
        "retry_paths": paths if retry_failed_only else [],
        "resumed_from": "publish_batch_report.json" if retry_failed_only else None,
        "items": list(previous.get("items", []) or []) if retry_failed_only else [],
    }
    _write_batch_report(project_path, report)
    yield "[upload] تجهيز {} مقطعاً من المصدر المحدد...".format(len(paths))
    if schedule_start and len(paths) > 1:
        yield "[schedule] جدولة تلقائية: البداية {} — الفاصل {} دقيقة — {} مقاطع.".format(
            schedule_start.isoformat(),
            int(interval) if interval.is_integer() else interval,
            len(paths),
        )
    for number, path in enumerate(paths, 1):
        meta = clip_metadata(project_path, path)
        title, caption, tags = meta["title"], meta["caption"], meta["hashtags"]
        if not title:
            # Filename-derived fallback title. The file itself is untouched;
            # only the *title text* is fitted to the platform cap so a long
            # clip name never ships as a mid-word-split publish title.
            from scripts.title_text import fit_publish_title

            stem = os.path.splitext(os.path.basename(path))[0]
            title = fit_publish_title(stem, _title_limit_for_platform(platform))
        yield "\n[upload] ({}/{}) {}".format(number, len(paths), os.path.basename(path))
        if tags:
            yield "[upload] هاشتاغات المقطع: {}".format(
                " ".join("#" + tag for tag in tags)
            )
        item_publish_at = None
        if schedule_start:
            item_publish_at = (
                schedule_start + datetime.timedelta(minutes=interval * (number - 1))
            ).isoformat()
        result = yield from stream_upload(
            project_path,
            platform,
            path,
            title,
            caption,
            tags,
            dry_run,
            music_gate,
            client_secrets_path,
            privacy_status,
            item_publish_at,
            oauth_full_access,
            require_existing_auth,
            public_confirm,
            full_video_url,
            channel_url,
            variant_policy,
        )
        result = result or _publish_result(
            "failed", path, title, item_publish_at, error="missing structured result"
        )
        replaced = False
        for index, previous_item in enumerate(report["items"]):
            if previous_item.get("video_path") == result.get("video_path"):
                report["items"][index] = result
                replaced = True
                break
        if not replaced:
            report["items"].append(result)
        report["summary"] = _batch_summary(report["items"])
        _write_batch_report(project_path, report)
        yield "[upload] نتيجة {}: {}".format(
            os.path.basename(path), result.get("status", "failed")
        )
    summary = report["summary"]
    if summary["failed"] or summary["blocked"]:
        yield "⚠️ اكتملت الدفعة مع مشاكل: uploaded={} scheduled={} dry_run={} skipped_duplicate={} blocked={} failed={}. أعد المحاولة للفاشل فقط بعد إصلاح السبب.".format(
            summary["counts"].get("uploaded", 0),
            summary["counts"].get("scheduled", 0),
            summary["counts"].get("dry_run", 0),
            summary["counts"].get("skipped_duplicate", 0),
            summary["blocked"],
            summary["failed"],
        )
    elif summary["counts"].get("dry_run", 0) == summary["total"]:
        yield "✅ اكتملت المحاكاة لكل الملفات (Dry Run) — لم يُرفع أي فيديو فعلياً."
    else:
        yield "✅ اكتمل رفع/جدولة كل الملفات المحددة: {} عنصر ناجح.".format(
            summary["successful"]
        )
