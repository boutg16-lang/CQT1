"""Read a project's transcript (TSV/SRT) and format it for the AI selector.

Extracted verbatim from ``scripts/create_viral_segments.py`` (monolith
split, step 6). Filesystem reads of ``input.tsv`` / ``input.srt`` plus pure
string formatting: stdlib only (``os``, ``re``), no UI, no global state.
The original module re-exports these names so existing import paths keep
working unchanged.
"""

import os
import re


def preprocess_transcript_for_ai(segments):
    """
    Concatenates transcript segments into a single string with embedded time tags.
    """
    if not segments:
        return ""

    full_text = ""
    last_tag_time = -100  # Force first tag

    # Try to start with (0s) based on first segment
    first_start = segments[0].get("start", 0)
    full_text += f"({int(first_start)}s) "
    last_tag_time = first_start

    for seg in segments:
        text = seg.get("text", "").strip()
        end_time = seg.get("end", 0)

        full_text += text + " "

        if end_time - last_tag_time >= 4:
            full_text += f"({int(end_time)}s) "
            last_tag_time = end_time

    return full_text.strip()


def _parse_tsv_transcript(tsv_path):
    """Parse a WhisperX TSV (header + rows of start_ms<TAB>end_ms<TAB>text)."""
    segments = []
    try:
        with open(tsv_path, "r", encoding="utf-8") as f:
            lines = f.readlines()[1:]  # skip header
            for line in lines:
                parts = line.strip().split("\t")
                if len(parts) >= 3:
                    try:
                        start_ms = float(parts[0])
                        end_ms = float(parts[1])
                    except (TypeError, ValueError):
                        continue
                    segments.append(
                        {
                            "start": start_ms / 1000.0,
                            "end": end_ms / 1000.0,
                            "text": parts[2],
                        }
                    )
    except Exception as e:
        print("Error parsing TSV {}: {}".format(tsv_path, e))
    return segments


def _parse_srt_transcript(srt_path):
    """Parse a standard SRT file into transcript segments."""
    segments = []
    try:
        with open(srt_path, "r", encoding="utf-8") as f:
            srt_content = f.read()
        pattern = re.compile(
            r"(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n((?:(?!\n\n).)*)",
            re.DOTALL,
        )
        matches = pattern.findall(srt_content)

        def srt_time_to_seconds(t_str):
            h, m, s = t_str.replace(",", ".").split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)

        for m in matches:
            start_sec = srt_time_to_seconds(m[1])
            end_sec = srt_time_to_seconds(m[2])
            text = m[3].replace("\n", " ")
            segments.append({"start": start_sec, "end": end_sec, "text": text})
    except Exception as e:
        print("Error parsing SRT {}: {}".format(srt_path, e))
    return segments


def load_transcript(project_folder):
    """Parses input.tsv / input.srt from the project folder.

    Falls back to any top-level transcript file: local/external videos keep
    their original basename (transcription artifacts are not named input.*),
    so older projects must still be processable.
    """
    input_tsv = os.path.join(project_folder, "input.tsv")
    input_srt = os.path.join(project_folder, "input.srt")

    # Try to load TSV first (more reliable time)
    transcript_segments = (
        _parse_tsv_transcript(input_tsv) if os.path.exists(input_tsv) else []
    )

    # Fallback to SRT parser if TSV empty/failed
    if not transcript_segments and os.path.exists(input_srt):
        transcript_segments = _parse_srt_transcript(input_srt)

    # Last resort: any top-level transcript artifact (older broken projects
    # whose transcription was written under the video's own basename).
    if not transcript_segments:
        try:
            candidates = sorted(os.listdir(project_folder))
        except OSError:
            candidates = []
        for name in candidates:
            lower = name.lower()
            if lower.endswith(".tsv") and not lower.startswith("input."):
                transcript_segments = _parse_tsv_transcript(
                    os.path.join(project_folder, name)
                )
                if transcript_segments:
                    break
        if not transcript_segments:
            for name in candidates:
                lower = name.lower()
                if lower.endswith(".srt") and not lower.startswith("input."):
                    transcript_segments = _parse_srt_transcript(
                        os.path.join(project_folder, name)
                    )
                    if transcript_segments:
                        break

    if not transcript_segments:
        raise ValueError("Could not parse transcript from TSV or SRT.")

    # Defensive ordering: the timestamp-matching and speech-block logic assume
    # chronological lines; a muxed/edited SRT can arrive out of order.
    transcript_segments.sort(key=lambda s: float(s.get("start", 0.0) or 0.0))
    return transcript_segments
