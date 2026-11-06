# -*- coding: utf-8 -*-
"""Non-destructive editor state and render-plan builder."""

from __future__ import annotations

import copy
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass

ASPECTS = {"original", "9:16", "4:5", "1:1", "16:9"}


def _number(value, field):
    if isinstance(value, bool):
        raise ValueError(f"{field} must be finite")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


@dataclass
class Clip:
    source: str
    start: float = 0.0
    end: float | None = None
    x: float = 0.5
    y: float = 0.5
    zoom: float = 1.0
    rotation: float = 0.0
    aspect: str = "original"
    text: str = ""
    logo: str | None = None


class EditorState:
    def __init__(self, source=None):
        self.version = 1
        self.source = source
        self.clips = []
        self._undo = []
        self._redo = []

    def _snapshot(self):
        return copy.deepcopy((self.source, self.clips))

    def _restore(self, snap):
        self.source, self.clips = copy.deepcopy(snap)

    def _commit(self):
        self._undo.append(self._snapshot())
        self._redo.clear()

    def add_clip(self, source, start=0.0, end=None):
        before = self._snapshot()
        item = asdict(Clip(source, float(start), None if end is None else float(end)))
        self.clips.append(item)
        try:
            self.validate()
        except Exception:
            self._restore(before)
            raise
        self._undo.append(before)
        self._redo.clear()

    def update_clip(self, index, **changes):
        if not 0 <= index < len(self.clips):
            raise IndexError("clip index")
        before = self._snapshot()
        item = dict(self.clips[index])
        item.update(changes)
        self.clips[index] = item
        try:
            self.validate()
        except Exception:
            self._restore(before)
            raise
        self._undo.append(before)
        self._redo.clear()

    def trim(self, index, start, end):
        if end <= start or start < 0:
            raise ValueError("invalid trim range")
        self.update_clip(index, start=float(start), end=float(end))

    def set_transform(
        self, index, *, x=None, y=None, zoom=None, rotation=None, aspect=None
    ):
        changes = {}
        if x is not None:
            x = float(x)
            if not 0.0 <= x <= 1.0:
                raise ValueError("x must be between 0 and 1")
            changes["x"] = x
        if y is not None:
            y = float(y)
            if not 0.0 <= y <= 1.0:
                raise ValueError("y must be between 0 and 1")
            changes["y"] = y
        if zoom is not None:
            zoom = float(zoom)
            if not 0.1 <= zoom <= 8.0:
                raise ValueError("zoom out of range")
            changes["zoom"] = zoom
        if rotation is not None:
            rotation = float(rotation)
            if not -360.0 <= rotation <= 360.0:
                raise ValueError("rotation out of range")
            changes["rotation"] = rotation
        if aspect is not None:
            if aspect not in ASPECTS:
                raise ValueError("unsupported aspect ratio")
            changes["aspect"] = aspect
        self.update_clip(index, **changes)

    def undo(self):
        if not self._undo:
            return False
        self._redo.append(self._snapshot())
        self._restore(self._undo.pop())
        return True

    def redo(self):
        if not self._redo:
            return False
        self._undo.append(self._snapshot())
        self._restore(self._redo.pop())
        return True

    def validate(self):
        if not isinstance(self.clips, list):
            raise ValueError("clips must be a list")
        if self.source is not None and (
            not isinstance(self.source, str) or not self.source.strip()
        ):
            raise ValueError("project source must be a non-empty path or None")
        for index, c in enumerate(self.clips):
            if not isinstance(c, dict):
                raise ValueError(f"clip {index} must be an object")
            if not isinstance(c.get("source"), str) or not c["source"].strip():
                raise ValueError("clip source is required")
            start = _number(c.get("start", 0), "start")
            if start < 0:
                raise ValueError("negative start")
            if c.get("end") is not None and _number(c["end"], "end") <= start:
                raise ValueError("end must be greater than start")
            if not 0.1 <= _number(c.get("zoom", 1), "zoom") <= 8:
                raise ValueError("zoom out of range")
            for field in ("x", "y"):
                if not 0 <= _number(c.get(field, 0.5), field) <= 1:
                    raise ValueError(f"{field} out of range")
            if not -360 <= _number(c.get("rotation", 0), "rotation") <= 360:
                raise ValueError("rotation out of range")
            if c.get("aspect", "original") not in ASPECTS:
                raise ValueError("unsupported aspect ratio")
            if not isinstance(c.get("text", ""), str):
                raise ValueError("clip text must be a string")
            logo = c.get("logo")
            if logo is not None and (not isinstance(logo, str) or not logo.strip()):
                raise ValueError("logo must be a non-empty path or None")
        return True

    def render_plan(self):
        self.validate()
        return {
            "version": self.version,
            "source": self.source,
            "clips": copy.deepcopy(self.clips),
        }

    def save(self, path):
        payload = self.render_plan()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=os.path.dirname(os.path.abspath(path)), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, allow_nan=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        obj = cls(data.get("source"))
        obj.version = int(data.get("version", 1))
        if obj.version != 1:
            raise ValueError("unsupported editor project version")
        if not isinstance(data.get("clips", []), list):
            raise ValueError("project clips must be a list")
        obj.clips = list(data.get("clips", []))
        obj.validate()
        return obj
