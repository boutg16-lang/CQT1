# -*- coding: utf-8 -*-
"""
المحرّك: يقرأ نصًا (ترجمة/بث/دردشة) ويطلع مخالفات محتملة مع طوابع زمنية.
Rule-based Arabic content-risk engine tuned to YouTube Community Guidelines.
"""
import html
import json
import logging
import math
import os
import re
from dataclasses import dataclass

from . import lexicon as policy
from . import text as arabic

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# بناء الفهرس
# ---------------------------------------------------------------------------
class Engine:
    def __init__(self):
        self.by_stem = {}      # جذع -> [entry]
        self.phrases = []      # (regex, entry)
        self.groups = set()
        for g in policy.GROUPS:
            self.groups.add(arabic.normalize(g))
        self.groups_stems = set()
        for g in self.groups:
            self.groups_stems |= arabic.stems_tight(g)
        # أسماء الدول والكيانات لا تُحتسب كمجموعة محميّة
        self.group_stop = set()
        for n in policy.STATE_NAMES:
            nn = arabic.normalize(n)
            self.group_stop.add(nn)
            self.group_stop.add("ال" + nn)

        for e in policy.LEXICON:
            term = arabic.normalize(e["t"])
            if not term:
                continue
            if " " in term:
                pat = r"(?<![\w\u0600-\u06FF])" + re.escape(term).replace(r"\ ", r"\s+") + \
                      r"(?![\w\u0600-\u06FF])"
                self.phrases.append((re.compile(pat), e))
            else:
                _st = arabic.stems(term) or {term}
                st = sorted(_st, key=len, reverse=True)[0]
                self.by_stem.setdefault(st, []).append(e)

        def _clean(lst):
            return [t for t in (arabic.normalize(x) for x in lst) if t.strip()]
        self.soft = _clean(policy.SOFTENERS)
        self.hard = _clean(policy.HARDENERS)
        self.neg = _clean(policy.NEGATIONS)
        self.fig = _clean(policy.FIGURATIVE)
        self.praise = _clean(PRAISE)
        self.superiority = _clean(SUPERIORITY)

    # -----------------------------------------------------------------------
    def matches(self, text):
        """يرجع [(entry, start, end, matched_term)] على النص المطبّع."""
        out = []
        for m in re.finditer(r"\S+", text):
            tok, s, e = m.group(0), m.start(), m.end()
            for st in arabic.stems(tok):
                for entry in self.by_stem.get(st, ()):
                    out.append((entry, s, e, tok))
        for pat, entry in self.phrases:
            for m in pat.finditer(text):
                out.append((entry, m.start(), m.end(), m.group(0)))
        return out

    # -----------------------------------------------------------------------
    def analyse(self, text, window=0):
        """يحلّل نصًا واحدًا (مقطع ترجمة/رسالة دردشة) ويرجع النتائج."""
        norm = arabic.normalize(text)
        if not norm:
            return []
        found = self.matches(norm)
        if not found:
            return []

        # --- تجميع الإشارات ---
        signals = {}   # kind -> أفضل عنصر
        term_positions = []

        for entry, s, e, tok in found:
            kind = entry["kind"]
            term_positions.append((s, e))
            prev = signals.get(kind)
            if prev is None or entry["w"] > prev[0]["w"]:
                signals[kind] = (entry, s, e, tok)
        # كشف المجموعات المحميّة (مسح كل الكلمات مقابل جذوع أسماء المجموعات)
        group_spans = []
        for m in re.finditer(r"\S+", norm):
            tok = m.group(0)
            if len(tok) < 3:
                continue
            if tok in self.group_stop or tok.lstrip("و") in self.group_stop:
                continue
            if tok in self.groups or (arabic.stems_tight(tok) & self.groups_stems):
                group_spans.append((m.start(), m.end(), tok))
        has_group = bool(group_spans)

        # --- سياق ---
        soft = self._ctx(norm, self.soft, policy.SOFTEN_WINDOW, term_positions)
        hard = self._ctx(norm, self.hard, policy.SOFTEN_WINDOW, term_positions)
        neg = self._ctx(norm, self.neg, policy.NEG_WINDOW, term_positions)
        fig = self._ctx(norm, self.fig, policy.SOFTEN_WINDOW, term_positions)
        praise = self._ctx(norm, self.praise, 60, term_positions)
        sup = self._ctx(norm, self.superiority, 90, term_positions)

        results = []

        def add(sev, cat, entry, s, e, reason):
            results.append({
                "severity": sev,
                "category": cat,
                "category_label": policy.CATEGORY_LABEL.get(cat, cat),
                "term": entry["t"] if entry else "",
                "kind": entry["kind"] if entry else "",
                "reason": reason,
                "span": [s, e],
            })

        # إشارات "صارمة": تلك التي تكفي بمفردها
        strict = {k: v for k, v in signals.items() if not v[0].get("needs_group")}

        dehuman = signals.get("dehuman")
        slur = signals.get("slur")
        exclude = signals.get("exclusion")
        viol = signals.get("violence")
        ext = signals.get("extremism")
        s_dehuman = strict.get("dehuman")
        s_slur = strict.get("slur")
        adult = strict.get("adult")
        selfharm = strict.get("selfharm")
        spam = strict.get("spam")
        misinfo = strict.get("misinfo")
        prof = strict.get("profanity")

        # 1) مجموعة محميّة + إهانة/تجريد/إقصاء/عنف  => خطاب كراهية
        viol_real = viol if (viol and not fig) else None
        if has_group and (dehuman or slur or exclude or viol_real):
            top = dehuman or exclude or viol_real or slur
            sev = "critical"
            if soft or (neg and not hard):
                sev = "medium"
                reason = ("عبارة تحقيرية موجّهة لمجموعة محميّة، لكن سياقها يبدو "
                          "اقتباسًا/نقلًا أو نفيًا — تحتاج مراجعة بشرية.")
            elif neg:
                sev = "high"
                reason = ("إهانة موجّهة لمجموعة محميّة داخل سياق نفي — "
                          "احتمال استشهاد بالكلام لنقده.")
            else:
                reason = ("مجموعة محميّة + %s — هذا هو التعريف المباشر لخطاب "
                          "الكراهية في سياسات يوتيوب." % _kind_ar(top[0]["kind"]))
            add(sev, "hate", top[0], top[1], top[2], reason)

        # 2) تجريد من الإنسانية بدون تسمية مجموعة
        elif s_dehuman:
            dehuman = s_dehuman
            sev = "high" if not soft else "medium"
            add(sev, "hate", dehuman[0], dehuman[1], dehuman[2],
                "وصف مُجرِّد من الإنسانية (تشبيه بجراثيم/حشرات/مرض) — "
                "يُصنَّف خطاب كراهية حتى دون ذكر المجموعة صراحة.")

        # 3) دعوة للإقصاء/الطرد
        elif exclude:
            sev = "high" if not (soft or neg) else "medium"
            add(sev, "hate", exclude[0], exclude[1], exclude[2],
                "دعوة لطرد/إقصاء/تمييز — محظورة بغضّ النظر عن المجموعة المستهدَفة.")

        # 4) عنف صريح
        elif viol:
            sev = "high"
            if fig:
                sev = "medium"
            if soft or neg:
                sev = "medium" if sev == "high" else "low"
            add(sev, "violence", viol[0], viol[1], viol[2],
                "عنف صريح/تهديد — قد يُصنَّف تهديدًا أو تحريضًا على العنف.")

        # 5) تطرّف عنيف
        elif ext:
            if praise:
                add("critical", "violent_extremism", ext[0], ext[1], ext[2],
                    "إشادة/دعم صريح لتنظيم أو خطاب متطرّف — مخالفة مباشرة "
                    "(الترويج للتطرّف العنيف).")
            elif soft or neg:
                add("info", "violent_extremism", ext[0], ext[1], ext[2],
                    "ذكر التنظيم في سياق إدانة/نقل خبر — لا مخالفة.")
            else:
                add("low", "violent_extremism", ext[0], ext[1], ext[2],
                    "ذكر تنظيم متطرّف دون إدانة صريحة — راجع النبرة.")

        # 6) إهانة بدون مجموعة => تحرّش
        elif s_slur:
            slur = s_slur
            add("medium", "harassment", slur[0], slur[1], slur[2],
                "وصف مهين لشخص/جهة — قد يُصنَّف تحرّشًا أو تنمّرًا.")

        # 7) محتوى للبالغين
        elif adult:
            add("high", "adult", adult[0], adult[1], adult[2],
                "إيحاء/محتوى جنسي — محتوى مقيَّد، خصوصًا في بث مباشر.")

        # 8) إيذاء الذات
        elif selfharm:
            add("high", "self_harm", selfharm[0], selfharm[1], selfharm[2],
                "محتوى إيذاء ذات/انتحار — يحتاج سياقًا وتحذيرًا صريحًا.")

        # 9) تضليل
        elif misinfo:
            add("low" if not hard else "medium", "misinfo", misinfo[0],
                misinfo[1], misinfo[2],
                "ادّعاء قابل للتفنيد — خطر تصنيفه تضليلًا.")

        # 10) سبام/احتيال
        elif spam:
            add("medium", "spam", spam[0], spam[1], spam[2],
                "دعوة لربح/تواصل خارجي أو محتوى مشبوه — قد يُصنَّف احتيالًا.")

        # 11) ألفاظ نابية
        elif prof:
            add("low", "profanity", prof[0], prof[1], prof[2],
                "لفظ نابٍ — لا يخالف وحده، لكنه يرفع احتمال المراجعة.")
        else:
            if has_group and sup:
                g = group_spans[0]
                add("medium", "hate", {"t": g[2], "kind": "superiority"},
                    g[0], g[1],
                    "تعالٍ/احتقار لمجموعة محميّة (أنا أفضل منهم) — "
                    "قد يُصنَّف خطاب كراهية إذا نقص التقليل من كرامة المجموعة.")

        return results

    # -----------------------------------------------------------------------
    @staticmethod
    def _near(s, e, positions, win):
        for ps, pe in positions:
            if abs(s - pe) <= win or abs(ps - e) <= win:
                return True
        return False

    def _ctx(self, norm, words, win, positions):
        """كشف سياق مع تجاوز سوابق/لواحق الكلمات العربية."""
        if not positions:
            return False
        ts = []
        for m in re.finditer(r"\S+", norm):
            ts.append((arabic.stems(m.group(0)), m.start(), m.end()))
        for w in words:
            parts = w.split()
            if not parts:
                continue
            if len(parts) == 1:
                pst = arabic.stems(parts[0])
                for st, s, e in ts:
                    if (st & pst) and self._near(s, e, positions, win):
                        return True
            else:
                cand = []
                for p in parts:
                    pst = arabic.stems(p)
                    cand.append([(s, e) for st, s, e in ts if st & pst])
                if all(cand):
                    for a in cand[0]:
                        for b in cand[-1]:
                            if b[0] >= a[0] and (b[1] - a[0]) <= win * 2 \
                               and self._near(a[0], b[1], positions, win):
                                return True
        return False


def _kind_ar(kind):
    return {"slur": "وصف مهين", "dehuman": "تجريد من الإنسانية",
            "exclusion": "دعوة للإقصاء", "violence": "عنف/تهديد"}.get(kind, kind)


# --- أنماط مساعدة ----------------------------------------------------------
PRAISE = [
    "الله يرحمهم", "ابطال", "نفتخر", "نفتخر بهم", "تحيا", "يدافع",
    "نصره", "ندعم", "دعم", "شجعان", "يحترمون", "رحمهم الله", "رجال مخلصين",
    "معهم حق", "نؤيد", "بورك فيهم", "عظماء",
]
SUPERIORITY = [
    "احمد الله انني لست", "الحمد لله اني مش", "نحن افضل من", "احنا خير من",
    "شعب افضل", "الافضل", "ما تسواش", "اقل منا", "احسن منهم",
]


# ---------------------------------------------------------------------------
# تمثيل المقاطع
# ---------------------------------------------------------------------------
@dataclass
class Cue:
    start: float
    end: float
    text: str
    speaker: str = ""


@dataclass
class Finding:
    cue: Cue
    severity: str
    category: str
    category_label: str
    term: str
    reason: str
    kind: str = ""
    source: str = "stream"   # stream | chat


SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


# ---------------------------------------------------------------------------
# قراءة الملفات
# ---------------------------------------------------------------------------
_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?")


def _to_sec(h, m, s, ms=None):
    hours, minutes, seconds = int(h or 0), int(m), int(s)
    if minutes > 59 or seconds > 59:
        raise ValueError("subtitle timestamp components must be below 60")
    total = hours * 3600 + minutes * 60 + seconds
    if ms:
        total += int(ms.ljust(3, "0")[:3]) / 1000.0
    return total


def parse_srt(text):
    cues, current = [], None

    def flush():
        if current and current.text.strip():
            cues.append(current)

    for line in text.splitlines():
        line = line.rstrip("\n")
        if "-->" in line:
            flush()
            left, right = line.split("-->", 1)
            start_match, end_match = _TIME.search(left), _TIME.search(right)
            if not start_match or not end_match:
                logger.warning("Skipping malformed subtitle timestamp line")
                current = None
                continue
            try:
                start = _to_sec(*start_match.groups())
                end = _to_sec(*end_match.groups())
            except ValueError:
                logger.warning("Skipping subtitle cue with invalid timestamp components")
                current = None
                continue
            if end <= start:
                logger.warning("Skipping subtitle cue with non-positive duration")
                current = None
                continue
            current = Cue(start, end, "")
        elif not line.strip():
            flush()
            current = None
        elif current is not None:
            current.text += " " + line.strip()
    flush()
    return cues


def parse_vtt(text):
    return parse_srt(text)


def parse_timed_text(text):
    """[00:12:30] نص   أو   00:12:30 نص"""
    cues = []
    lines = [line for line in text.splitlines() if line.strip()]
    for line in lines:
        m = re.match(r"^\s*\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?\s*(.*)$", line)
        if m:
            parts = [int(part) for part in m.group(1).split(":")]
            if parts[-1] > 59 or (len(parts) == 3 and parts[-2] > 59):
                logger.warning("Skipping timed-text line with invalid timestamp")
                continue
            if len(parts) == 2:
                t = parts[0] * 60 + parts[1]
            else:
                t = parts[0] * 3600 + parts[1] * 60 + parts[2]
            cues.append(Cue(float(t), float(t) + 5.0, m.group(2).strip()))
        elif cues:
            cues[-1].text += " " + line.strip()
    if not cues and lines:
        for i, line in enumerate(lines):
            cues.append(Cue(i * 5.0, i * 5.0 + 5.0, line.strip()))
    return cues


def _chat_offset(value):
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def parse_chat_jsonl(path):
    cues, malformed = [], 0
    with open(path, encoding="utf-8-sig", errors="replace") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(record, dict):
                malformed += 1
                continue
            message = record.get("message")
            raw_text = record.get("text")
            if raw_text is None and isinstance(message, str):
                raw_text = message
            text = str(raw_text or "").strip()
            if text:
                raw_time = record.get("time", record.get("offset"))
                timestamp = _chat_offset(raw_time)
                if timestamp is None:
                    malformed += 1
                    continue
                author = str(record.get("author") or record.get("name") or "")
                cues.append(Cue(timestamp, timestamp, text, author))
                continue
            try:
                before = len(cues)
                _collect_chat(record, cues)
                if len(cues) == before:
                    malformed += 1
            except RecursionError:
                malformed += 1
    if malformed:
        logger.warning("Skipped %d malformed live-chat JSONL records in %s", malformed, path)
    return cues


def _collect_chat(obj, cues, ctx=None):
    """يجمع رسائل الدردشة مع إزاحة الفيديو الموروثة من replayChatItemAction."""
    if isinstance(obj, dict):
        parent_offset = ctx
        if obj.get("videoOffsetTimeMsec") is not None:
            parsed = _chat_offset(obj["videoOffsetTimeMsec"])
            if parsed is not None:
                parent_offset = parsed / 1000.0
            else:
                logger.warning("Ignoring malformed live-chat offset")
        for key in ("liveChatTextMessageRenderer", "liveChatPaidMessageRenderer"):
            renderer = obj.get(key)
            if not isinstance(renderer, dict):
                continue
            message = renderer.get("message")
            message = message if isinstance(message, dict) else {}
            runs = message.get("runs")
            text = "".join(
                str(run.get("text", "")) for run in runs
                if isinstance(run, dict)
            ) if isinstance(runs, list) else ""
            if not text:
                text = str(message.get("simpleText") or "")
            author_value = renderer.get("authorName")
            author = (str(author_value.get("simpleText") or "")
                      if isinstance(author_value, dict) else str(author_value or ""))
            offset = parent_offset
            if renderer.get("videoOffsetTimeMsec") is not None:
                parsed = _chat_offset(renderer["videoOffsetTimeMsec"])
                if parsed is not None:
                    offset = parsed / 1000.0
                else:
                    logger.warning("Ignoring malformed message offset")
            if text.strip() and offset is not None:
                cues.append(Cue(offset, offset, text.strip(), author))
        for value in obj.values():
            _collect_chat(value, cues, parent_offset)
    elif isinstance(obj, list):
        for value in obj:
            _collect_chat(value, cues, ctx)


def parse_chat_json(path):
    """Parse yt-dlp JSONL chat or a single JSON document."""
    with open(path, encoding="utf-8-sig", errors="replace") as stream:
        raw = stream.read()
    try:
        document = json.loads(raw)
    except (json.JSONDecodeError, RecursionError):
        cues, malformed = [], 0
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, RecursionError):
                malformed += 1
                continue
            try:
                _collect_chat(record, cues)
            except RecursionError:
                malformed += 1
        if malformed:
            logger.warning("Skipped %d malformed live-chat JSON records in %s", malformed, path)
        return cues
    cues = []
    try:
        _collect_chat(document, cues)
    except RecursionError:
        logger.warning("Skipped deeply nested live-chat JSON in %s", path)
        return []
    return cues


def load_cues(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        return parse_chat_json(path)
    if ext == ".jsonl":
        return parse_chat_jsonl(path)
    with open(path, encoding="utf-8-sig", errors="replace") as stream:
        raw = stream.read()
    if ext == ".srt":
        return parse_srt(raw)
    if ext == ".vtt":
        return parse_vtt(raw)
    return parse_timed_text(raw)


# ---------------------------------------------------------------------------
# التحليل + التجميع
# ---------------------------------------------------------------------------
def scan_cues(engine, cues, source="stream"):
    out = []
    for c in cues:
        if not c.text.strip():
            continue
        for r in engine.analyse(c.text):
            out.append(Finding(c, r["severity"], r["category"],
                               r["category_label"], r["term"], r["reason"],
                               r["kind"], source))
    return out


def merge_incidents(findings, gap=30.0):
    """يدمج النتائج المتقاربة زمنيًا في حوادث واحدة."""
    gap = float(gap)
    if not math.isfinite(gap) or gap < 0:
        raise ValueError("gap must be a finite non-negative number")
    if not findings:
        return []
    if any(f.severity not in SEV_ORDER for f in findings):
        raise ValueError("findings contain an unknown severity")
    ranked = sorted(findings, key=lambda f: f.cue.start)
    incidents, cur = [], None
    for f in ranked:
        if f.severity == "info":
            continue
        if cur and (f.cue.start - cur["end"]) <= gap and \
           SEV_ORDER[f.severity] <= SEV_ORDER[cur["severity"]] + 2:
            cur["end"] = max(cur["end"], f.cue.end)
            cur["findings"].append(f)
            if SEV_ORDER[f.severity] < SEV_ORDER[cur["severity"]]:
                cur["severity"] = f.severity
        else:
            if cur:
                incidents.append(cur)
            cur = {"start": f.cue.start, "end": f.cue.end,
                   "severity": f.severity, "findings": [f]}
    if cur:
        incidents.append(cur)
    incidents.sort(key=lambda i: (SEV_ORDER[i["severity"]], i["start"]))
    return incidents


# ---------------------------------------------------------------------------
# التقارير
# ---------------------------------------------------------------------------
def fmt_t(sec):
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


CHAT_ACTION = {
    "critical": "إشارة عالية الخطورة؛ راجع الرسالة كاملة ولا تُحذف أو يُحظر الحساب تلقائيًا.",
    "high": "راجع الرسالة والسياق قبل أي إجراء إداري.",
    "medium": "تحقّق من الاقتباس أو النفي قبل أي إجراء.",
    "low": "إشارة ضعيفة؛ لا إجراء دون سياق إضافي.",
}


def build_report(stream_inc, chat_findings, findings, meta):
    counts = {}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1

    def inc_json(i):
        return {
            "start": i["start"], "end": i["end"],
            "start_hms": fmt_t(i["start"]), "end_hms": fmt_t(i["end"]),
            "severity": i["severity"],
            "severity_label": policy.SEVERITY_LABEL[i["severity"]],
            "action": policy.SEVERITY_ACTION[i["severity"]],
            "items": [{
                "time": fmt_t(f.cue.start), "text": f.cue.text.strip(),
                "term": f.term, "category": f.category_label,
                "reason": f.reason, "kind": f.kind,
            } for f in i["findings"]],
        }

    chat = [{
        "time": fmt_t(f.cue.start), "seconds": f.cue.start,
        "author": f.cue.speaker or "؟", "text": f.cue.text.strip(),
        "severity": f.severity,
        "severity_label": policy.SEVERITY_LABEL[f.severity],
        "category": f.category_label, "term": f.term, "reason": f.reason,
        "action": CHAT_ACTION.get(f.severity, ""),
    } for f in sorted(chat_findings,
                      key=lambda f: (SEV_ORDER[f.severity], f.cue.start))]

    authors = {}
    for f in chat_findings:
        if f.severity in ("low", "info"):
            continue
        a = authors.setdefault(f.cue.speaker or "؟",
                               {"author": f.cue.speaker or "؟", "count": 0,
                                "worst": "low"})
        a["count"] += 1
        if SEV_ORDER[f.severity] < SEV_ORDER[a["worst"]]:
            a["worst"] = f.severity
    authors = sorted(authors.values(), key=lambda a: (SEV_ORDER[a["worst"]], -a["count"]))

    return {
        "meta": meta,
        "summary": {k: counts.get(k, 0) for k in
                    ["critical", "high", "medium", "low", "info"]},
        "incidents": [inc_json(i) for i in stream_inc],
        "chat": chat,
        "chat_authors": authors,
    }


def write_markdown(rep, path):
    s = rep["summary"]
    L = ["# تقرير فحص المخالفات — %s\n" % _markdown_text(rep["meta"].get("title", "")),
         "**المصدر:** `%s`  " % _markdown_text(rep["meta"].get("source", "")),
         "**المدة:** %s  " % _markdown_text(rep["meta"].get("duration", "")),
         "**التاريخ:** %s\n" % _markdown_text(rep["meta"].get("date", "")),
         "## الملخّص\n", "| الخطورة | العدد |", "|---|---|"]
    for k in ["critical", "high", "medium", "low", "info"]:
        L.append("| %s | %d |" % (policy.SEVERITY_LABEL[k], s[k]))
    L.append("")

    L.append("## أولًا: كلام البث/الفيديو (يُقتطع أو يُكتم)\n")
    if not rep["incidents"]:
        L.append("✅ لم يُرصد أي مقطع يستدعي تعديلًا في كلام البث.\n")
    for i, inc in enumerate(rep["incidents"], 1):
        L.append("### %d) %s → %s — **%s**\n" %
                 (i, inc["start_hms"], inc["end_hms"], inc["severity_label"]))
        L.append("> **الإجراء:** %s\n" % inc["action"])
        for it in inc["items"]:
            L.append("- `%s` — **%s**  \n  «%s»  \n  ↳ %s" %
                     (it["time"], _markdown_text(it["category"]), _markdown_text(it["text"]),
                      _markdown_text(it["reason"])))
        L.append("")

    L.append("## ثانيًا: رسائل الدردشة (تُراجع قبل الحذف أو الحظر)\n")
    if not rep["chat"]:
        L.append("✅ لا رسائل تستدعي حذفًا.\n")
    else:
        L.append("**أكثر الحسابات تكرارًا:**\n")
        L.append("| الحساب | عدد الرسائل المخالفة | أقصى خطورة |")
        L.append("|---|---|---|")
        for a in rep["chat_authors"][:15]:
            L.append("| %s | %d | %s |" % (_markdown_text(a["author"]), a["count"],
                                           policy.SEVERITY_LABEL[a["worst"]]))
        L.append("")
        for c in rep["chat"][:120]:
            L.append("- `%s` **%s** (%s) — «%s»  \n  ↳ %s" %
                     (c["time"], _markdown_text(c["author"]), _markdown_text(c["category"]),
                      _markdown_text(c["text"]), _markdown_text(c["reason"])))
    if len(rep["chat"]) > 120:
        L.append("\n*عُرضت أول 120 رسالة فقط؛ التفاصيل الكاملة في `report.json`.*")
    L.append("\n---\n*أداة مساعدة للفحص آليًا — لا تُغني عن المراجعة البشرية، "
             "والنتائج تشمل إيجابيات كاذبة (اقتباس/نقد مشروع).*")
    with open(path, "w", encoding="utf-8") as report_file:
        report_file.write("\n".join(L))
    return path


def _html_text(value):
    return html.escape(str(value), quote=True)


def _markdown_text(value):
    text = html.escape(str(value), quote=True).replace("\r", " ").replace("\n", " ")
    return re.sub(r"([\\`*_{}\[\]()#+!>|~])", r"\\\1", text)


def write_html(rep, path):
    s = rep["summary"]
    color = {"critical": "#c0392b", "high": "#e67e22", "medium": "#f1c40f",
             "low": "#7f8c8d", "info": "#95a5a6"}
    rows = []
    for i, inc in enumerate(rep["incidents"], 1):
        items = "".join(
            "<li><code>%s</code> <b>%s</b><br><span class=tx>«%s»</span>"
            "<br><span class=rs>↳ %s</span></li>" %
            (_html_text(it["time"]), _html_text(it["category"]),
             _html_text(it["text"]), _html_text(it["reason"]))
            for it in inc["items"])
        rows.append(
            '<div class="inc" style="border-right:6px solid %s">'
            "<h3>%d) %s → %s <span class=badge style=\"background:%s\">%s</span></h3>"
            '<p class="act">%s</p><ul>%s</ul></div>' %
            (color[inc["severity"]], i, inc["start_hms"], inc["end_hms"],
             color[inc["severity"]], inc["severity_label"], inc["action"], items))
    chats = []
    for c in rep["chat"][:150]:
        chats.append(
            '<li><span class=badge style="background:%s">%s</span> '
            "<code>%s</code> <b>%s</b> — «%s»<br><span class=rs>↳ %s</span></li>" %
            (color[c["severity"]], _html_text(c["severity_label"]), _html_text(c["time"]),
             _html_text(c["author"]), _html_text(c["text"]), _html_text(c["reason"])))
    chat_output = "".join(chats) or "<p>✅ لا رسائل مخالفة.</p>"
    if len(rep["chat"]) > 150:
        chat_output += "<p class=rs>عُرضت أول 150 رسالة فقط؛ التفاصيل الكاملة في report.json.</p>"

    arows = "".join("<tr><td>%s</td><td>%d</td><td>%s</td></tr>" %
                    (_html_text(a["author"]), a["count"],
                     _html_text(policy.SEVERITY_LABEL[a["worst"]]))
                    for a in rep["chat_authors"][:20])
    html = """<!doctype html><html dir="rtl" lang="ar"><meta charset="utf-8">
<title>تقرير فحص المخالفات</title>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<style>
body{font-family:system-ui,'Segoe UI',Tahoma,sans-serif;background:#f5f6f8;color:#1c1e21;margin:0;padding:24px}
.wrap{max-width:940px;margin:auto}
h1{font-size:22px} h2{font-size:19px;margin-top:34px}
.cards{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}
.card{background:#fff;border-radius:12px;padding:14px 18px;min-width:110px;box-shadow:0 1px 4px #0001}
.card b{display:block;font-size:26px}
.inc{background:#fff;border-radius:12px;padding:14px 18px;margin:14px 0;box-shadow:0 1px 4px #0001}
.inc h3{margin:0 0 6px;font-size:16px}
.badge{color:#fff;border-radius:20px;padding:2px 10px;font-size:12px;vertical-align:middle}
.act{color:#444;font-size:13px;margin:4px 0 10px}
li{margin:8px 0;font-size:14px} .tx{color:#333} .rs{color:#666;font-size:12.5px}
code{background:#eef0f3;padding:1px 6px;border-radius:5px;font-size:12.5px}
table{border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden}
th,td{border:1px solid #e6e8ec;padding:7px 12px;font-size:13.5px;text-align:right}
th{background:#fafbfc}
</style><div class="wrap">
<h1>تقرير فحص المخالفات</h1>
<p>%s · المدة: %s · %s</p>
<div class="cards">%s</div>
<h2>أولًا: كلام البث (يُقتطع/يُكتم)</h2>%s
<h2>ثانيًا: رسائل الدردشة — مراجعة قبل أي إجراء</h2>
%s
%s
<p style="color:#777;font-size:12px">أداة مساعدة للفحص آليًا — لا تُغني عن المراجعة البشرية.</p>
</div></html>""" % (
        _html_text(rep["meta"].get("title", "")),
        _html_text(rep["meta"].get("duration", "")),
        _html_text(rep["meta"].get("date", "")),
        "".join('<div class="card"><b style="color:%s">%d</b>%s</div>' %
                (color[k], s[k], policy.SEVERITY_LABEL[k])
                for k in ["critical", "high", "medium", "low"]),
        "".join(rows) or "<p>✅ لا مخالفات في كلام البث.</p>",
        chat_output,
        ("<h3>أكثر الحسابات تكرارًا</h3><table><tr><th>الحساب</th>"
         "<th>عدد الرسائل</th><th>أقصى خطورة</th></tr>%s</table>" % arows)
        if arows else "")
    with open(path, "w", encoding="utf-8") as report_file:
        report_file.write(html)
    return path


def write_cutlist(rep, path, only=("critical", "high", "medium")):
    lines = ["# start\tend\tالخطورة\tالسبب"]
    for inc in rep["incidents"]:
        if inc["severity"] in only:
            lines.append("%.2f\t%.2f\t%s\t%s" % (
                inc["start"], inc["end"], inc["severity_label"],
                inc["items"][0]["category"]))
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return path
