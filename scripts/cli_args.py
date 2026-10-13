# -*- coding: utf-8 -*-
"""CLI argument parser for the OUSSAMA Cutter pipeline (extracted from
main_improved.py — second modularization step). Pure builder, no side effects:
``build_parser()`` returns the configured ``argparse.ArgumentParser`` and
main() calls ``parse_args()`` on it exactly as before.
"""

import argparse


def build_parser():
    parser = argparse.ArgumentParser(description="ViralCutter CLI")
    parser.add_argument("--url", help="YouTube Video URL")
    parser.add_argument("--segments", type=int, help="Number of segments to create")
    parser.add_argument("--viral", action="store_true", help="Enable viral mode")
    parser.add_argument("--themes", help="Comma-separated themes (if not viral mode)")
    parser.add_argument("--burn-only", action="store_true", help="Skip processing and only burn subtitles")
    parser.add_argument("--min-duration", type=int, default=None, help="Minimum segment duration (seconds; default from --platform template or 15)")
    parser.add_argument("--max-duration", type=int, default=None, help="Maximum segment duration (seconds; default from --platform template or 90)")
    parser.add_argument("--platform", choices=["yt_shorts", "tiktok", "reels", "yt_standard"], default=None,
                        help="Platform output template (Roadmap 5.2): sets duration defaults + aspect hint. yt_shorts (9:16 ≤60s), tiktok/reels (9:16 ≤90s), yt_standard (16:9 ≤10min)")
    parser.add_argument("--model", default="large-v3-turbo", help="Whisper model to use")
    parser.add_argument("--transcription-device", choices=["auto", "cpu", "cuda"], default="auto", help="WhisperX device: auto, cpu, or cuda")
    
    parser.add_argument("--ai-backend", choices=["manual", "gemini", "g4f", "local"], help="AI backend for viral analysis; moderation is selected separately with --safety-backend")
    parser.add_argument("--api-key", help="API key for the selected viral-analysis backend")
    
    parser.add_argument("--chunk-size", help="Override Chunk Size")
    parser.add_argument("--ai-model-name", help="Override AI Model Name")

    parser.add_argument("--project-path", help="Path to existing project folder (overrides URL/Latest)")
    parser.add_argument("--local-video", help="Path to a local video; it is referenced, not copied into VIRALS")
    parser.add_argument("--workflow", choices=["1", "2", "3"], default="1", help="Workflow choice: 1=Full, 2=Cut Only, 3=Subtitles Only")
    parser.add_argument("--face-model", choices=["insightface", "mediapipe"], default="insightface", help="Face detection model")
    parser.add_argument(
        "--face-mode",
        choices=["auto", "1", "2", "3", "4", "multi", "grid"],
        default="auto",
        help="Face tracking mode: auto, 1, 2, 3, 4, multi/grid",
    )
    parser.add_argument("--subtitle-config", help="Path to subtitle configuration JSON file")
    parser.add_argument("--caption-animation", choices=["none", "pop", "scale", "pop_scale", "bounce"], default=None,
                        help="Word-timed ASS caption animation; overrides subtitle config when supplied")
    parser.add_argument("--auto-emoji", action="store_true",
                        help="Add conservative keyword emojis to burned captions")
    parser.add_argument("--no-face-mode", choices=["padding", "zoom"], default="padding", help="Method to handle segments with no face detected: 'padding' (9:16 frame with black bars) or 'zoom' (Center Crop Zoom)")
    parser.add_argument("--face-detect-interval", type=str, default="0.17,1.0", help="Face detection interval in seconds. Single value or 'interval_1face,interval_2face'")
    parser.add_argument("--face-filter-threshold", type=float, default=0.35, help="Relative area threshold to ignore background faces (default: 0.35)")
    parser.add_argument("--face-two-threshold", type=float, default=0.60, help="Relative area threshold to trigger 2-face mode (default: 0.60)")
    parser.add_argument("--face-confidence-threshold", type=float, default=0.30, help="Face detection confidence threshold (0.0 - 1.0) (default: 0.30)")
    parser.add_argument("--face-dead-zone", type=str, default="40", help="Camera movement dead zone in pixels (default: 40)") # str to support future "auto"
    parser.add_argument("--focus-active-speaker", action="store_true", help="Enable experimental active speaker focus (InsightFace only)")
    parser.add_argument("--active-speaker-mar", type=float, default=0.03, help="Mouth Aspect Ratio threshold for active speaker (0.0 - 1.0) (default: 0.03)")
    parser.add_argument("--active-speaker-score-diff", type=float, default=1.5, help="Score difference to focus on active speaker (default: 1.5)")
    parser.add_argument("--include-motion", action="store_true", help="Include motion (body/head movement) in activity score")
    parser.add_argument("--active-speaker-motion-threshold", type=float, default=3.0, help="Motion deadzone in pixels (default: 3.0)")
    parser.add_argument("--active-speaker-motion-sensitivity", type=float, default=0.05, help="Motion sensitivity multiplier (default: 0.05)")
    parser.add_argument("--active-speaker-decay", type=float, default=2.0, help="Activity score decay rate (default: 2.0)")
    parser.add_argument("--face-smoothing", type=float, default=0.55, help="EMA smoothing of the crop box (0.05-1.0; 1.0 = no smoothing) (default: 0.55)")
    parser.add_argument("--face-headroom", type=float, default=0.12, help="Talking-head headroom: shift crop up so the face sits in the upper third (0.0-0.35) (default: 0.12)")
    parser.add_argument("--face-zoom", type=float, default=0.0, help="Face-size-aware crop: face fills this fraction of the frame height (0 = legacy full-height crop; 0.33 = classic talking-head zoom)")
    parser.add_argument("--crop-scene-reset", choices=["on", "off"], default="on", help="Reset face tracking/smoothing at scene cuts so the crop never carries old-shot boxes across a camera change (default: on)")
    parser.add_argument("--voice-face-link", choices=["on", "off"], default="off", help="Learn online which face owns the active speaker (speech turns + mouth activity correlation; pyannote optional) and bias the crop toward it (default: off)")
    parser.add_argument("--skip-prompts", action="store_true", help="Skip interactive prompts and use defaults/existing files")
    parser.add_argument("--video-quality", choices=["best", "1080p", "720p", "480p"], default="best", help="Video download quality")
    parser.add_argument("--skip-youtube-subs", action="store_true", help="Skip downloading YouTube subtitles")
    parser.add_argument("--translate-target", help="Target language code for subtitle translation (e.g. 'pt', 'en').")
    parser.add_argument("--workers", type=int, help="Number of parallel workers for segment cutting")
    parser.add_argument("--prefer-hardware-acceleration", action="store_true", default=None, help="Prefer hardware video encoding when available")
    parser.add_argument("--verbose", action="store_true", help="Print extra debug information")
    parser.add_argument("--safety-mode", choices=["block", "flag", "censor", "off"], default="block",
                        help="Policy safety filter (hate speech / violence): 'block' removes violating segments before cutting (default), 'flag' only annotates them, 'censor' keeps segments but BLEEPs the violating words (mute audio + mask subtitles), 'off' disables the filter")
    parser.add_argument("--safety-min-severity", choices=["low", "medium", "high"], default="medium",
                        help="Minimum severity that blocks a segment in 'block' mode (default: medium)")
    parser.add_argument("--safety-extra-terms", help="Path to a safety_terms.json file with extra blocked terms")
    parser.add_argument("--safety-ai", choices=["on", "off"], default="on",
                        help="Second-pass policy review of surviving segments. Supports Gemini, G4F, and openai-moderation. Default: on")
    parser.add_argument("--safety-backend", choices=["auto", "local", "gemini", "g4f", "openai-moderation"], default="auto",
                        help="Contextual safety backend. auto prefers OpenAI moderation when OPENAI_API_KEY exists, then Gemini/G4F, otherwise local checks.")
    parser.add_argument("--autopilot", action="store_true",
                        help="Run the end-to-end safety autopilot: AI review, OCR, visual checks, provenance, audio QC, metadata, and hard publish gates.")
    parser.add_argument("--safety-fail-closed", choices=["on", "off"], default="on",
                        help="Refuse to continue when an enabled safety check errors (default: on)")
    parser.add_argument("--safety-autoupdate", choices=["on", "off"], default="on",
                        help="Auto-update the hate-speech word list from GitHub once a day (offline-safe). Default: on")
    parser.add_argument("--risk-scorecard", choices=["on", "off"], default="on",
                        help="Per-clip YouTube risk scorecard (reused-content / monetization / visual warnings) after rendering. Default: on")
    parser.add_argument("--audio-qc", choices=["on", "off"], default="on",
                        help="Measure rendered audio with FFmpeg and write audio_qc_report.json. Default: on")
    parser.add_argument("--audio-qc-gate", choices=["warn", "block"], default="warn",
                        help="Audio QC behavior: warn records review and lets local processing continue; block stops the pipeline on non-pass")
    parser.add_argument("--risk-gate", choices=["off", "warn", "block"], default="warn",
                        help="What to do when a clip fails the compliance gate: 'warn' prints warnings and writes publish_blocklist.json (default), 'block' stops the run, 'off' does nothing")
    parser.add_argument("--music-check", choices=["on", "off", "auto"], default="auto",
                        help="Chromaprint music fingerprint check (Roadmap 2.3): 'auto' runs it only when fpcalc/pyacoustid is installed. Default: auto")
    parser.add_argument("--music-gate", choices=["warn", "block", "off"], default="warn",
                        help="How to treat audio fingerprint matches in the upload gate: 'warn' flags (default), 'block' refuses publishing matched clips, 'off' ignores")
    parser.add_argument("--music-local-db", default=None,
                        help="Local reference-music DB: JSON cache from 'python -m scripts.music_fingerprint --build-local-db' or a folder of songs to fingerprint on the fly")
    parser.add_argument("--acoustid-key", default=None, help="AcoustID API key (or ACOUSTID_API_KEY env) for the music check")

    # --- Sprint 3/4/5 features (added in v6) ---
    parser.add_argument("--checkpoint", choices=["on", "off"], default="on",
                        help="Crash-safe resume: skip stages completed in a previous run (checkpoint.json per project). Default: on")
    parser.add_argument("--check-updates", action="store_true",
                        help="Check GitHub Releases for a newer ViralCutter build at startup")
    parser.add_argument("--polish", choices=["on", "off"], default="off",
                        help="Run the professional polish pass (jump cuts + punch zoom + music + optional B-Roll + branding) after editing, before subtitles. Default: off")
    parser.add_argument("--polish-stages", default="jump_cuts,punch_zoom,background_music,visual_hooks,broll,auto_sfx,branding",
                        help="Comma-separated polish stages; broll/auto_sfx skip cleanly without assets")
    parser.add_argument("--music", default=None, help="Background music file (with --polish; default: <project>/music/ folder)")
    parser.add_argument("--music-volume", type=float, default=0.15, help="Background music volume (0..1)")
    parser.add_argument("--logo", default=None, help="Channel logo PNG for the watermark (with --polish)")
    parser.add_argument("--watermark-position", choices=["top-left", "top-right", "bottom-left", "bottom-right", "center"], default="bottom-right",
                        help="Watermark position (with --polish)")
    parser.add_argument("--watermark-size", type=float, default=0.12,
                        help="Watermark width as a fraction of video width, from 0.05 to 0.30")
    parser.add_argument("--watermark-opacity", type=float, default=0.9,
                        help="Watermark opacity from 0.10 to 1.00")
    parser.add_argument("--intro", default=None, help="Intro clip to prepend (with --polish)")
    parser.add_argument("--outro", default=None, help="Outro clip to append (with --polish)")
    parser.add_argument("--broll", default=None,
                        help="Local B-Roll video asset (with --polish and broll stage)")
    parser.add_argument("--broll-query", default=None,
                        help="Pexels B-Roll query (key comes from PEXELS_API_KEY)")
    parser.add_argument("--broll-opacity", type=float, default=0.28,
                        help="B-Roll opacity from 0.05 to 0.85")
    parser.add_argument("--visual-hook-max", type=int, default=8,
                        help="Maximum automatic visual hook moments")
    parser.add_argument("--visual-hook-accent", default="0x00d9ff",
                        help="FFmpeg accent color for visual hook frame")
    parser.add_argument("--sfx-dir", default=None,
                        help="Local folder containing pop/whoosh/impact audio assets")
    parser.add_argument("--sfx-volume", type=float, default=0.22,
                        help="Automatic SFX volume from 0.02 to 1.0")
    parser.add_argument("--zoom-keywords", default=None,
                        help="Comma-separated keywords that trigger punch-in zoom (with --polish)")
    parser.add_argument("--metadata-gate", choices=["off", "warn", "block"], default="warn",
                        help="Metadata compliance gate (title/caption/hashtags): 'warn' flags + writes to the scorecard (default), 'block' stops the run when any clip has risky metadata, 'off' skips it")
    parser.add_argument("--provenance-gate", choices=["warn", "block"], default="warn",
                        help="Rights and transformation evidence policy: warn records review, block refuses clips without declared rights and meaningful editorial transformation")
    parser.add_argument("--auto-download-visual", action="store_true",
                        help="Download the small ONNX visual classifier into models/ when missing (Roadmap 2.1)")
    parser.add_argument("--visual-check", choices=["off", "auto", "on"], default="auto",
                        help="Visual safety scan: off, auto when a local model exists, or on (fail closed if unavailable)")
    parser.add_argument("--visual-gate", choices=["off", "warn", "block"], default="warn",
                        help="Visual safety policy: off, warn, or block graphic visual findings")
    parser.add_argument("--visual-frames", type=int, default=4,
                        help="Number of frames sampled per rendered clip (default: 4)")
    parser.add_argument("--visual-model", default=None,
                        help="Path to a local ONNX visual safety classifier")
    parser.add_argument("--ocr-check", choices=["off", "auto", "on"], default="auto",
                        help="OCR scan for hateful/inciting text burned into video frames")
    parser.add_argument("--ocr-gate", choices=["off", "warn", "block"], default="warn",
                        help="OCR safety policy: warn records findings, block refuses OCR-detected violations")
    parser.add_argument("--ocr-frames", type=int, default=4,
                        help="Number of video frames sampled by OCR (default: 4)")
    parser.add_argument("--ocr-lang", default="ara+eng",
                        help="Tesseract language pack for OCR (default: ara+eng)")
    parser.add_argument("--allow-placeholder-transcription", action="store_true",
                        help="When whisperx/torch are missing, continue with placeholder subtitles (for testing editing/safety only — NOT for real viral-segment selection)")
    parser.add_argument("--cookies-from-browser", choices=["chrome", "firefox", "edge", "safari", "brave", "opera", "vivaldi"], default=None,
                        help="Use your browser's login cookies to download private / age-restricted videos (e.g. --cookies-from-browser chrome)")
    parser.add_argument("--cookies", default=None,
                        help="Path to a Netscape-format cookies.txt file exported for yt-dlp (alternative to --cookies-from-browser)")
    parser.add_argument("--sponsorblock", default=None,
                        help="Remove in-video sponsor segments at download time using SponsorBlock (comma-separated categories: sponsor,intro,outro,selfpromo,interaction,music_offtopic — or 'all'). Makes cuts cleaner and avoids ad content in clips.")
    parser.add_argument("--scene-snap", action="store_true",
                        help="v7.23: snap cut points to detected shot boundaries (PySceneDetect when installed, OpenCV fallback) so clips never cut mid-shot")
    parser.add_argument("--live-wait", type=float, default=None, metavar="MINUTES",
                        help="v7.20: if the URL is a live stream / premiere (e.g. https://youtube.com/live/ID), wait up to this many minutes for it to END, then download the resulting VOD automatically.")
    parser.add_argument("--title-language", default="auto",
                        help="Output language for titles/captions: 'auto' (match the transcript, default) or a code like 'ar', 'en', 'fr', 'es', 'pt', 'de', 'tr', 'ru', 'hi'")
    parser.add_argument("--webui", action="store_true",
                        help="Launch the Gradio WebUI (GUI). This is also the default when "
                             "the app is opened with NO arguments (double-click) — both in "
                             "the packaged exe and from source.")
    parser.add_argument("--self-check", action="store_true",
                        help="Verify the packaged bundles: import the heavy optional stacks "
                             "(whisperx/torch transcription, faster_whisper) and exit 0/1. "
                             "Used by the CI smoke test before every release.")
    parser.add_argument("--preflight", choices=["auto", "check", "off"], default="auto",
                        help="Pre-flight environment check: 'auto' checks everything and "
                             "auto-installs missing core dependencies (default), 'check' only "
                             "reports, 'off' skips the check entirely "
                             "(env: VIRALCUTTER_SKIP_PREFLIGHT=1).")
    parser.add_argument("--output-aspect", choices=["9:16", "4:5", "1:1", "16:9"], default=None,
                        help="Reframe the FINAL clips to this aspect ratio after subtitle "
                             "burning (9:16 is the native crop). 4:5/1:1 center-crop, 16:9 "
                             "blur-pads. Auto-set to 16:9 with --platform yt_standard.")
    parser.add_argument("--reframe-mode", choices=["crop", "pad"], default=None,
                        help="Reframe method: crop=fill+center-crop (default for 4:5/1:1), "
                             "pad=blurred bars (default for 16:9).")
    parser.add_argument("--force-new-segments", "--force-regenerate", dest="force_new_segments",
                        action="store_true",
                        help="Ignore an existing viral_segments.txt and generate fresh "
                             "segments (the WebUI 'generate new segments' checkbox; "
                             "--force-regenerate is the explicit CLI alias).")
    parser.add_argument("--auto-learn-blocked", action="store_true",
                        help="After the risk scorecard, automatically teach the safety terms "
                             "the patterns that got clips blocked (strike-feedback loop, 5.1). "
                             "See scripts/strike_feedback.py.")
    return parser
