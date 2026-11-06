"""Human-approved, non-destructive edits for reviewed videos."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from scripts.media_validation import validate_media_file

ACTIONS = {"mute", "bleep", "cut"}


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_edits(edits, duration):
    if not isinstance(edits, list) or not edits:
        raise ValueError("أدخل تعديلاً معتمداً واحداً على الأقل")
    clean = []
    for index, item in enumerate(edits):
        if not isinstance(item, dict):
            raise ValueError(f"التعديل {index + 1} ليس كائناً صالحاً")
        action = str(item.get("action", "")).strip().lower()
        if action not in ACTIONS:
            raise ValueError("الإجراء يجب أن يكون mute أو bleep أو cut")
        try:
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"توقيت التعديل {index + 1} غير صالح") from exc
        if start < 0 or end <= start or end > duration + 0.05:
            raise ValueError(f"الفترة الزمنية {index + 1} خارج مدة الفيديو")
        if end - start > 120:
            raise ValueError("الحد الأقصى للفترة الواحدة 120 ثانية")
        clean.append(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "action": action,
                "reason": str(item.get("reason") or "human approved")[:500],
                "transcript": str(item.get("transcript") or "")[:1000],
            }
        )
    clean.sort(key=lambda row: (row["start"], row["end"]))
    if any(row["action"] == "cut" for row in clean) and not all(
        row["action"] == "cut" for row in clean
    ):
        raise ValueError("لا تخلط القص مع الكتم أو الصفارة في التصدير نفسه")
    for previous, current in zip(clean, clean[1:], strict=False):
        if current["start"] < previous["end"]:
            raise ValueError("توجد فترات متداخلة؛ ادمجها قبل التصدير")
    if (
        clean[0]["action"] == "cut"
        and sum(x["end"] - x["start"] for x in clean) >= duration - 0.5
    ):
        raise ValueError("القص سيزيل معظم الفيديو")
    return clean


def _srt_time(seconds):
    ms = max(0, int(round(seconds * 1000)))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def process_srt(source, target, edits):
    import re

    text = Path(source).read_text(encoding="utf-8-sig", errors="replace")
    pattern = re.compile(
        r"(\d{2}:\d{2}:\d{2}[,.]\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}[,.]\d{3})"
    )

    def sec(value):
        h, m, s = value.replace(",", ".").split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    cuts = [(e["start"], e["end"]) for e in edits if e["action"] == "cut"]
    cens = [(e["start"], e["end"]) for e in edits if e["action"] in ("mute", "bleep")]
    blocks = []
    number = 1
    for block in re.split(r"\n\s*\n", text.strip()):
        match = pattern.search(block)
        if not match:
            continue
        start, end = sec(match.group(1)), sec(match.group(2))
        lines = block.splitlines()
        idx = next((i for i, line in enumerate(lines) if "-->" in line), -1)
        if idx < 0:
            continue
        payload = lines[:idx] + lines[idx + 1 :]
        if any(start < b and end > a for a, b in cens):
            payload = ["[تم حجب العبارة بعد المراجعة]"]
        pieces = [(start, end)]
        for a, b in cuts:
            next_pieces = []
            for x, y in pieces:
                if b <= x or a >= y:
                    next_pieces.append((x, y))
                else:
                    if x < a:
                        next_pieces.append((x, a))
                    if b < y:
                        next_pieces.append((b, y))
            pieces = next_pieces
        for x, y in pieces:
            shift_x = sum(max(0, min(x, b) - a) for a, b in cuts if a < x)
            shift_y = sum(max(0, min(y, b) - a) for a, b in cuts if a < y)
            if y - shift_y <= x - shift_x:
                continue
            blocks.append(
                "\n".join(
                    [
                        str(number),
                        f"{_srt_time(x - shift_x)} --> {_srt_time(y - shift_y)}",
                    ]
                    + payload
                )
            )
            number += 1
    Path(target).write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return target


def sanitize_video(video_path, edits, output_path, subtitle_path=None):
    source = os.path.abspath(str(video_path))
    target = os.path.abspath(str(output_path))
    if not os.path.isfile(source):
        raise FileNotFoundError("الفيديو الأصلي غير موجود")
    if os.path.realpath(source) == os.path.realpath(target):
        raise ValueError("يجب أن يكون الناتج ملفاً مستقلاً")
    if os.path.exists(target):
        raise FileExistsError("ملف الناتج موجود؛ اختر اسماً آخر")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg غير مثبت أو غير موجود في PATH")
    media = validate_media_file(source, min_duration=1, require_audio=True)
    if not media.get("ok"):
        raise ValueError("فشل فحص الوسائط: " + str(media.get("errors")))
    duration = float(media["duration"])
    clean = validate_edits(edits, duration)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    original_hash = sha256(source)
    temp = target + ".partial.mp4"
    try:
        if clean[0]["action"] == "cut":
            keep = []
            cursor = 0.0
            for edit in clean:
                if edit["start"] > cursor:
                    keep.append((cursor, edit["start"]))
                cursor = edit["end"]
            if cursor < duration:
                keep.append((cursor, duration))
            chains = []
            labels = []
            for i, (a, b) in enumerate(keep):
                chains += [
                    f"[0:v]trim=start={a}:end={b},setpts=PTS-STARTPTS[v{i}]",
                    f"[0:a]atrim=start={a}:end={b},asetpts=PTS-STARTPTS[a{i}]",
                ]
                labels += [f"[v{i}]", f"[a{i}]"]
            chains.append("".join(labels) + f"concat=n={len(keep)}:v=1:a=1[vout][aout]")
            cmd = [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                source,
                "-filter_complex",
                ";".join(chains),
                "-map",
                "[vout]",
                "-map",
                "[aout]",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "21",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                temp,
            ]
        else:
            ranges = "+".join(f"between(t\\,{e['start']}\\,{e['end']})" for e in clean)
            if any(e["action"] == "bleep" for e in clean):
                bleeps = "+".join(
                    f"between(t\\,{e['start']}\\,{e['end']})"
                    for e in clean
                    if e["action"] == "bleep"
                )
                graph = f"[0:a]volume=0:enable='{ranges}'[base];sine=frequency=1000:sample_rate=48000:duration={duration},volume=0.18:enable='{bleeps}'[tone];[base][tone]amix=inputs=2:duration=first:dropout_transition=0[aout]"
            else:
                graph = f"[0:a]volume=0:enable='{ranges}'[aout]"
            cmd = [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                source,
                "-filter_complex",
                graph,
                "-map",
                "0:v:0",
                "-map",
                "[aout]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                temp,
            ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=max(180, int(duration * 8))
        )
        if result.returncode:
            raise RuntimeError("فشل FFmpeg: " + (result.stderr or "")[-1200:])
        validation = validate_media_file(temp, min_duration=0.5, require_audio=True)
        if not validation.get("ok"):
            raise RuntimeError(
                "الملف الناتج غير صالح: " + str(validation.get("errors"))
            )
        os.replace(temp, target)
        srt = None
        if subtitle_path:
            srt = os.path.splitext(target)[0] + ".reviewed.srt"
            process_srt(subtitle_path, srt, clean)
        report = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_sha256": original_hash,
            "output_sha256": sha256(target),
            "source_path": source,
            "output_path": target,
            "source_duration": duration,
            "output_duration": validation.get("duration"),
            "edits": clean,
            "original_preserved": sha256(source) == original_hash,
            "output_validation": {
                "ok": True,
                "video": validation.get("video"),
                "audio": validation.get("audio"),
            },
            "subtitle_output": srt,
            "visual_text_status": "not_processed_ocr_review_required",
            "post_render_review_status": "pending_reupload_and_review",
            "publish_status": "HOLD",
        }
        audit = os.path.splitext(target)[0] + ".audit.json"
        with open(audit, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        return {"video": target, "audit": audit, "report": report}
    finally:
        if os.path.exists(temp):
            os.remove(temp)
