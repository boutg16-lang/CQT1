# -*- coding: utf-8 -*-
"""stream_guard — حراسة البثّ المباشر بعد انتهائه داخل OUSSAMA Cutter.

يجمع: مراقبة انتهاء البثّ (download_live) + فحص الترجمة والدردشة
+ تقاطع مع نوافذ القصّ + تقرير عربي.

    python -m scripts.stream_guard --help
"""
from .scan import Engine, load_cues, merge_incidents, scan_cues  # noqa: F401

__all__ = ["Engine", "load_cues", "scan_cues", "merge_incidents"]
