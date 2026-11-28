# -*- coding: utf-8 -*-
"""
Thumbnail generator (v7.23) — صور مصغرة احترافية للمقاطع.

Creates YouTube-ready 1280x720 thumbnails from a video frame (or an image)
with:

* **Face-aware framing** — when OpenCV finds a face, the frame is cropped so
  the face sits in the upper-third (classic YouTube thumbnail composition).
* **Arabic/English text** — title + optional hook line, rendered with the
  bundled Montserrat fonts (Arabic falls back to DejaVu if needed), with
  smart line wrapping.
* **Professional styling** — optional gradient overlay for readability,
  accent bar, and a hook badge.

Dependencies: Pillow + OpenCV (both already in requirements). Everything
degrades gracefully (no face -> center crop; missing font -> default).

    python scripts/thumbnail_generator.py video.mp4 --title "كسب المال" --out thumb.png
    python scripts/thumbnail_generator.py frame.jpg --title "Title" --hook "SECRET!" --out t.png
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    from PIL import features as pil_features

    HAS_PIL = True
except Exception:  # pragma: no cover
    Image = ImageDraw = ImageFilter = ImageFont = None
    pil_features = None
    HAS_PIL = False

# RTL correctness (feat/pro-cutting item 2): Pillow only shapes Arabic
# (joining + bidi reordering) through libraqm. Without it, every Arabic
# draw.text call renders DISCONNECTED letterforms left-to-right — the real
# bug this module ships against. Guarded once; every Arabic draw call routes
# through _rtl_text_kwargs so text can never silently render unshaped.
try:
    HAS_RAQM = bool(pil_features.check("raqm")) if HAS_PIL else False
except Exception:  # pragma: no cover
    HAS_RAQM = False

ARABIC_FONT_BLACK = "NotoKufiArabic-VF.ttf"  # variable, weight set to 900
ARABIC_BLACK_WEIGHT = 900


def _has_arabic(text) -> bool:
    return any("\u0600" <= ch <= "\u06ff" for ch in str(text or ""))


def _rtl_text_kwargs(text):
    """Common kwargs for Arabic draw.text/textlength calls.

    Returns ``{'direction': 'rtl', 'language': 'ar'}`` when libraqm is
    available and the text contains Arabic script; ``{}`` otherwise so Latin
    text and raqm-less builds keep byte-identical legacy behavior.
    """
    if HAS_RAQM and _has_arabic(text):
        return {"direction": "rtl", "language": "ar"}
    return {}


try:
    import cv2

    HAS_CV2 = True
except Exception:  # pragma: no cover
    cv2 = None
    HAS_CV2 = False

TARGET_W, TARGET_H = 1280, 720
FONT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts"
)


def _font_path(name: str) -> str:
    path = os.path.join(FONT_DIR, name)
    return path if os.path.isfile(path) else ""


def _load_font(size: int, bold: bool = True, text: str = ""):
    if not HAS_PIL:
        return None
    # Arabic text needs an Arabic font (Montserrat has no Arabic glyphs).
    # Headline preference (feat/pro-cutting item 2): Noto Kufi Arabic at
    # weight 900 (Black) — heavy kufi letterforms survive 720p compression
    # and read instantly at thumbnail scale. Cairo-Bold stays as fallback.
    if _has_arabic(text):
        if bold:
            path = _font_path(ARABIC_FONT_BLACK)
            if path:
                try:
                    font = ImageFont.truetype(path, size)
                    font.set_variation_by_axes([ARABIC_BLACK_WEIGHT])
                    return font
                except Exception:
                    pass
        candidates = [
            _font_path("Cairo-Bold.ttf") if bold else _font_path("Cairo-Regular.ttf"),
            _font_path("Cairo-Bold.ttf"),
        ]
        for path in candidates:
            if path:
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue
    candidates = [
        _font_path("Montserrat-ExtraBold.ttf")
        if bold
        else _font_path("Montserrat-Regular.ttf"),
        _font_path("Montserrat-Bold.ttf"),
        _font_path("Montserrat-Regular.ttf"),
    ]
    for path in candidates:
        if path:
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def extract_frame(video_path: str, at_seconds: float = 0.0):
    """Return a PIL image from a video frame (or load an image directly)."""
    if not HAS_CV2:
        return None
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        if fps <= 0 or fps > 240:
            fps = 30.0
        frame_index = int(at_seconds * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = cap.read()
        if not ok or frame is None:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = cap.read()
        if not ok or frame is None:
            return None
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb)
    finally:
        cap.release()


def _face_bbox(image):
    """Return (x1, y1, x2, y2) of the largest face, or None.

    Tries OpenCV's FaceDetectorYN (works in cv2 4.5+ and 5.x) first, then
    the legacy Haar cascade path (cv2 < 5.0). Returns None when no face is
    found or OpenCV is unavailable — the caller falls back to center crop.
    """
    if not HAS_CV2:
        return None
    import numpy as np

    rgb = np.array(image.convert("RGB"))
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    h, w = bgr.shape[:2]

    # Preferred: FaceDetectorYN (works on cv2 5.x)
    detector = None
    try:
        detector = cv2.FaceDetectorYN_create(
            "", "", (w, h), score_threshold=0.6, nms_threshold=0.3, top_k=5
        )
        detector.setInputSize((w, h))
        _ok, faces = detector.detect(bgr)
        if faces is not None and len(faces) > 0:
            # faces: N x 15 (bbox + landmarks + score); take the largest
            best = max(faces, key=lambda f: f[2] * f[3])
            x, y, fw, fh = (int(v) for v in best[:4])
            return (x, y, x + fw, y + fh)
    except Exception:
        pass

    # Legacy fallback: Haar cascade (cv2 < 5.0)
    try:
        cascade = cv2.CascadeClassifier(
            os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
        )
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60)
        )
        if len(faces) == 0:
            return None
        x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        return (int(x), int(y), int(x + fw), int(y + fh))
    except Exception:
        return None


def _cover_crop(image, target_w=TARGET_W, target_h=TARGET_H, face_box=None):
    """Crop to 16:9, favoring the face in the upper-third when known."""
    w, h = image.size
    target_ratio = target_w / target_h
    img_ratio = w / h

    if img_ratio > target_ratio:
        crop_w = int(h * target_ratio)
        crop_h = h
    else:
        crop_w = w
        crop_h = int(w / target_ratio)

    # horizontal center by default, or face center
    center_x = w // 2
    if face_box:
        center_x = int((face_box[0] + face_box[2]) / 2)
    crop_x = max(0, min(center_x - crop_w // 2, w - crop_w))

    # vertical: face in the upper third (y ≈ 30% of crop height)
    if face_box:
        face_cy = int((face_box[1] + face_box[3]) / 2)
        crop_y = int(face_cy - crop_h * 0.30)
    else:
        crop_y = (h - crop_h) // 2
    crop_y = max(0, min(crop_y, h - crop_h))

    return image.crop((crop_x, crop_y, crop_x + crop_w, crop_y + crop_h))


def _wrap_text(text: str, font, max_width: int) -> list[str]:
    if not HAS_PIL:
        return [text]
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    words = str(text or "").split()
    lines, current = [], ""
    rtl_kwargs = _rtl_text_kwargs(text)
    for word in words:
        candidate = (current + " " + word).strip()
        if draw.textlength(candidate, font=font, **rtl_kwargs) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:3]  # max 3 lines


def generate_thumbnail(
    source: str,
    *,
    title: str = "",
    hook: str = "",
    out: str = "thumbnail.png",
    at_seconds: float = 0.0,
    accent: str = "#FFD400",
) -> dict[str, Any]:
    """Create a YouTube thumbnail from a video or image source."""
    if not HAS_PIL:
        return {"ok": False, "error": "Pillow is required (pip install Pillow)"}

    image = (
        extract_frame(source, at_seconds)
        if source.lower().endswith((".mp4", ".mov", ".mkv", ".webm", ".avi"))
        else None
    )
    if image is None and os.path.isfile(source):
        try:
            image = Image.open(source).convert("RGB")
        except Exception as exc:
            return {"ok": False, "error": "cannot open source: {}".format(exc)}
    if image is None:
        return {"ok": False, "error": "no frame/image extracted from {}".format(source)}

    face_box = _face_bbox(image)
    cropped = _cover_crop(image, face_box=face_box).resize(
        (TARGET_W, TARGET_H), Image.LANCZOS
    )

    # subtle dark gradient at the bottom for text readability
    overlay = Image.new("RGBA", (TARGET_W, TARGET_H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for y in range(int(TARGET_H * 0.45), TARGET_H):
        alpha = int(130 * (y - TARGET_H * 0.45) / (TARGET_H * 0.55))
        od.line([(0, y), (TARGET_W, y)], fill=(0, 0, 0, min(150, alpha)))
    cropped = Image.alpha_composite(cropped.convert("RGBA"), overlay).convert("RGB")

    draw = ImageDraw.Draw(cropped)

    # accent bar
    try:
        accent_rgb = tuple(int(accent.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    except Exception:
        accent_rgb = (255, 212, 0)
    draw.rectangle([0, TARGET_H - 14, TARGET_W, TARGET_H], fill=accent_rgb)

    # hook badge (top-left). RTL (item 2): Arabic hook text is shaped
    # right-to-left and anchored "ra" (right/ascender) so the badge padding
    # stays identical for Arabic and Latin text.
    if hook:
        hook_font = _load_font(64, bold=True, text=hook)
        hook_kwargs = _rtl_text_kwargs(hook)
        hw = draw.textlength(hook, font=hook_font, **hook_kwargs) + 48
        draw.rounded_rectangle([24, 24, 24 + hw, 112], radius=16, fill=accent_rgb)
        hook_x = 24 + hw - 24 if hook_kwargs else 48
        # Center the (shaped) glyphs vertically in the 88px badge: anchor "ra"
        # fixes the ascender top at y, so a kufi ascender/descender run taller
        # than the legacy assumption would hang below the badge edge.
        hook_y = 40
        try:
            bbox = draw.textbbox(
                (0, 0), hook, font=hook_font, anchor="ra", **hook_kwargs
            )
            # Center the INK (not the anchor line) in the 88px badge. With
            # anchor "ra" the ascender reference can sit far above the kufi
            # glyph tops, so the anchor y itself may legitimately go negative
            # — clamping it to the badge would push the ink out the bottom.
            hook_y = 24 + (88 - (bbox[3] - bbox[1])) // 2 - bbox[1]
        except Exception:
            pass
        draw.text(
            (hook_x, hook_y),
            hook,
            font=hook_font,
            fill=(10, 10, 10),
            anchor="ra" if hook_kwargs else None,
            **hook_kwargs,
        )

    # title (bottom, wrapped). RTL (item 2): Arabic titles render shaped
    # right-to-left, anchored "ra" and right-aligned to the 40px margin so
    # long Arabic lines can never run off-canvas (the legacy left-anchored
    # draw could overflow the right edge and disconnect letterforms).
    if title:
        title_font = _load_font(78, bold=True, text=title)
        lines = _wrap_text(title, title_font, TARGET_W - 80)
        title_kwargs = _rtl_text_kwargs(title)
        # Kufi ink runs ~94px tall at 78pt: a 92px baseline step collides
        # between wrapped lines and the dot descenders touch the accent bar
        # (measured 2026-11-25 on "الكوكايين بالأطنان"). Lift the block and
        # step 96px so ink clears both.
        line_step = 96
        y = TARGET_H - 48 - len(lines) * line_step
        for line in lines:
            draw.text(
                (TARGET_W - 40 if title_kwargs else 40, y),
                line,
                font=title_font,
                fill=(255, 255, 255),
                stroke_width=6,
                stroke_fill=(0, 0, 0),
                anchor="ra" if title_kwargs else None,
                **title_kwargs,
            )
            y += line_step

    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    cropped.save(out, "PNG")
    return {
        "ok": True,
        "out": out,
        "size": (TARGET_W, TARGET_H),
        "face_detected": face_box is not None,
        "engine": "pillow+opencv",
    }


def generate_variants(
    source: str,
    *,
    titles: Optional[List[str]] = None,
    hook: str = "",
    out_dir: str = ".",
    base_name: str = "thumbnail",
    at_seconds: float = 0.0,
    accent: str = "#FFD400",
    max_variants: int = 3,
) -> dict[str, Any]:
    """Generate 2-3 thumbnail variants for YouTube Studio Test & Compare.

    Variants differ in headline wording (``titles[0]`` / ``titles[1]``),
    frame time (video sources get a second frame at ``+2.0s``), and hook
    badge presence. Every variant lands next to the others in ``out_dir``
    as ``<base_name>_v1.png`` … plus a combined ``<base_name>_variants.png``
    contact sheet so the user can eyeball them side by side.
    """
    if not HAS_PIL:
        return {"ok": False, "error": "Pillow is required (pip install Pillow)"}

    candidate_titles = [t for t in (titles or []) if str(t or "").strip()]
    if not candidate_titles:
        candidate_titles = [""]

    is_video = source.lower().endswith((".mp4", ".mov", ".mkv", ".webm", ".avi"))
    specs: List[Dict[str, Any]] = [
        {
            "title": candidate_titles[0],
            "at_seconds": at_seconds,
            "hook": str(hook or "").strip(),
        }
    ]
    if len(candidate_titles) > 1:
        specs.append(
            {
                "title": candidate_titles[1],
                "at_seconds": at_seconds,
                "hook": "",
            }
        )
    elif str(hook or "").strip():
        # Single headline + hook badge: the second variant drops the badge so
        # the user can compare badge on/off in Studio's Test & Compare.
        specs.append(
            {"title": candidate_titles[0], "at_seconds": at_seconds, "hook": ""}
        )
    if is_video and len(specs) < max_variants:
        specs.append(
            {
                "title": candidate_titles[min(1, len(candidate_titles) - 1)],
                "at_seconds": at_seconds + 2.0,
                "hook": str(hook or "").strip(),
            }
        )
    specs = specs[: max(1, int(max_variants))]

    os.makedirs(out_dir, exist_ok=True)
    results: List[Dict[str, Any]] = []
    for index, spec in enumerate(specs, start=1):
        out_path = os.path.join(out_dir, "{}_v{}.png".format(base_name, index))
        result = generate_thumbnail(
            source,
            title=str(spec["title"]),
            hook=str(spec["hook"]),
            out=out_path,
            at_seconds=float(spec["at_seconds"]),
            accent=accent,
        )
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error"), "variants": results}
        results.append(
            {
                "out": result["out"],
                "title": str(spec["title"]),
                "at_seconds": float(spec["at_seconds"]),
                "hook": str(spec["hook"]),
                "hook_badge": bool(spec["hook"]),
                "face_detected": result.get("face_detected", False),
            }
        )

    sheet_path = ""
    paths = [item["out"] for item in results]
    try:
        images = [Image.open(path).convert("RGB") for path in paths]
        total_w = sum(image.width for image in images)
        height = max(image.height for image in images)
        sheet = Image.new("RGB", (total_w, height), (16, 16, 16))
        x = 0
        for image in images:
            sheet.paste(image, (x, 0))
            x += image.width
        sheet_path = os.path.join(out_dir, "{}_variants.png".format(base_name))
        sheet.save(sheet_path, "PNG")
    except Exception:
        sheet_path = ""

    return {"ok": True, "variants": results, "sheet": sheet_path}


def main(argv=None) -> int:

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="video file or image")
    parser.add_argument("--title", default="", help="main title (bottom)")
    parser.add_argument("--hook", default="", help="hook badge (top-left)")
    parser.add_argument("--out", default="thumbnail.png")
    parser.add_argument(
        "--at", type=float, default=0.0, help="video frame time in seconds (default 0)"
    )
    parser.add_argument("--accent", default="#FFD400", help="accent hex color")
    parser.add_argument(
        "--variants",
        type=int,
        default=0,
        help="generate 2-3 Test & Compare variants instead of one thumbnail "
        "(--out becomes the base name; a <base>_variants.png sheet is written)",
    )
    args = parser.parse_args(argv)

    if args.variants > 0:
        out_dir = os.path.dirname(os.path.abspath(args.out)) or "."
        base = os.path.splitext(os.path.basename(args.out))[0]
        result = generate_variants(
            args.source,
            titles=[args.title],
            hook=args.hook,
            out_dir=out_dir,
            base_name=base,
            at_seconds=args.at,
            accent=args.accent,
            max_variants=args.variants,
        )
        if not result["ok"]:
            print("[thumbnail] ERROR: {}".format(result.get("error")))
            return 1
        for variant in result["variants"]:
            print(
                "[thumbnail] variant -> {} (title={!r}, hook={!r}, frame={}s)".format(
                    variant["out"],
                    variant["title"],
                    variant["hook"],
                    variant["at_seconds"],
                )
            )
        if result.get("sheet"):
            print("[thumbnail] sheet -> {}".format(result["sheet"]))
        return 0

    result = generate_thumbnail(
        args.source,
        title=args.title,
        hook=args.hook,
        out=args.out,
        at_seconds=args.at,
        accent=args.accent,
    )
    if not result["ok"]:
        print("[thumbnail] ERROR: {}".format(result["error"]))
        return 1
    print(
        "[thumbnail] OK -> {} (face detected: {})".format(
            result["out"], result["face_detected"]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
