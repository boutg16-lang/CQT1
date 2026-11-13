import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from scripts import cut_json
from scripts.media_validation import probe_media, validate_media_file

# v7.41: the reusable final validator runs before any FFmpeg cut, so an
# out-of-range or malformed window can never be rendered. Imported
# defensively to keep cutting operational if the module is missing.
try:
    from scripts import segment_validator
except Exception:
    segment_validator = None


def _has_stored_transcript(segment):
    """True when the segment carries any stored transcript text field."""
    if not isinstance(segment, dict):
        return False
    if str(segment.get("transcript_text") or "").strip():
        return True
    analysis = segment.get("window_analysis")
    if isinstance(analysis, dict) and str(analysis.get("text") or "").strip():
        return True
    return bool(str(segment.get("text") or "").strip())


def _parse_time_value(value, treat_int_as_ms=False):
    # ``treat_int_as_ms`` is accepted for backward compatibility but no longer
    # reinterprets magnitudes. The pipeline stores SECONDS; dividing a valid
    # ``start_time: 90`` by 1000 sought to 0.09 s, and a 1500 s clip was cut
    # to 1.5 s — silent wrong cuts that no validator caught.
    if isinstance(value, (int, float)):
        return float(value)

    try:
        return float(value)
    except Exception:
        parts = str(value).strip().split(":")
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        if len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
        return 0.0


def _duration_to_seconds(duration):
    if isinstance(duration, (int, float)):
        return float(duration)
    try:
        return float(duration)
    except Exception:
        return 0.0


def _detect_best_encoder():
    try:
        from scripts.ffmpeg_toolchain import encoder_works
    except Exception:
        def encoder_works(_codec):
            return False

    try:
        result = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
        output = result.stdout or ""
    except Exception as e:
        print(f"Error checking encoders: {e}")
        output = ""

    # A compiled-in encoder is not a usable encoder. Probe each one with a
    # one-frame encode so a GPU-less machine never selects h264_nvenc.
    candidates = [
        ("h264_nvenc", "p1", ["-b:v", "5M"], "NVIDIA"),
        ("h264_amf", "quality", ["-b:v", "5M"], "AMD"),
        ("h264_qsv", "veryfast", ["-b:v", "5M"], "Intel QSV"),
        ("h264_videotoolbox", "default", ["-b:v", "5M"], "Apple VideoToolbox"),
    ]
    for codec, preset, extra, label in candidates:
        if codec in output and encoder_works(codec):
            print("Encoder Detected: {} ({})".format(label, codec))
            return codec, preset, extra

    print("Encoder Detected: CPU (libx264)")
    return "libx264", "ultrafast", ["-crf", "23"]


def segment_window_seconds(segment):
    """(start, duration) for a segment, with the window authoritative.

    ``duration`` goes stale whenever a scene snap rewrites start/end (or a
    hand-edited file disagrees); cutting by it produced clips that did not
    match the validated window. Returns ``(start, 0.0)`` when unusable.
    """
    if not isinstance(segment, dict):
        return 0.0, 0.0
    start = _parse_time_value(segment.get("start_time", segment.get("start", 0.0)))
    end_raw = segment.get("end_time", segment.get("end"))
    if end_raw not in (None, ""):
        try:
            end = _parse_time_value(end_raw)
            if end > start:
                return start, end - start
        except (TypeError, ValueError):
            pass
    return start, _duration_to_seconds(segment.get("duration", 0))


def _safe_duration(start_seconds, duration_seconds):
    start_seconds = max(0.0, float(start_seconds))
    duration_seconds = max(0.1, float(duration_seconds))
    return start_seconds, duration_seconds


def _accept_scene_snap(original_duration, new_duration):
    """Decide whether a scene-snapped window keeps an acceptable duration.

    Snap-to-scene may pull an edge to a distant shot boundary, silently
    growing (or shrinking) a clip far past its requested length. Accept only
    when the snapped duration stays within ``[max(1.0, 0.5*orig),
    max(1.0, 1.5*orig)]`` — the 1.0s floor keeps tiny clips from being judged
    against a near-zero original. Returns False for unparsable/negative input.
    """
    try:
        original_duration = float(original_duration)
        new_duration = float(new_duration)
    except (TypeError, ValueError):
        return False
    if original_duration < 0.0 or new_duration < 0.0:
        return False
    lower = max(1.0, 0.5 * original_duration)
    upper = max(1.0, 1.5 * original_duration)
    return lower <= new_duration <= upper


def _persist_cut_validation(project_folder, allowed_segments, allowed, refused):
    """Store the pre-cut verdict on every segment, preserving file order.

    A refusal used to exist only in this process: ``viral_segments.txt`` kept
    ``final_validation.ok=true``, the review table still listed the clip and
    the publish audit reported it as allowed. Writes atomically and leaves the
    file untouched when it does not match the in-memory list.
    """
    import tempfile
    path = os.path.join(project_folder, "viral_segments.txt")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
            return
        merged = [seg for _, seg in sorted(list(allowed) + list(refused),
                                           key=lambda pair: pair[0])]
        if len(merged) != len(payload["segments"]):
            return
        payload["segments"] = merged
        directory = os.path.dirname(os.path.abspath(path)) or "."
        fd, temp_path = tempfile.mkstemp(prefix=".cut_validation.", suffix=".tmp",
                                         dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=4)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
    except Exception as exc:
        print("[WARN] Could not persist cut validation: {}".format(exc))

def _process_segment(segment_index, segment, project_folder, input_file, cuts_folder, subs_folder, skip_video, video_codec, video_preset, video_extra_args):
    start_time = segment.get("start_time", segment.get("start", "00:00:00"))
    title = segment.get("title", f"Segment_{segment_index}")
    safe_title = "".join([c for c in title if c.isalnum() or c in " _-"]).strip().replace(" ", "_")[:60]
    base_name = f"{segment_index:03d}_{safe_title}"

    output_filename = f"{base_name}_original_scale.mp4"
    output_path = os.path.join(cuts_folder, output_filename)
    json_output_filename = f"{base_name}_processed.json"
    json_output_path = os.path.join(subs_folder, json_output_filename)

    start_time_seconds, duration_seconds = segment_window_seconds(segment)
    if duration_seconds <= 0:
        return {"ok": False, "index": segment_index, "segment": segment,
                "error": "non-positive duration (start={}, end={})".format(
                    start_time, segment.get("end_time", segment.get("end")))}
    start_time_seconds, duration_seconds = _safe_duration(start_time_seconds, duration_seconds)
    start_time_str = f"{start_time_seconds:.3f}"
    duration_str = f"{duration_seconds:.3f}"
    temporary_output = output_path + ".tmp.mp4"

    try:
        if not skip_video:
            command = [
                "ffmpeg",
                "-y",
                "-loglevel", "error",
                "-hide_banner",
                "-ss", start_time_str,
                "-i", input_file,
                "-t", duration_str,
                "-c:v", video_codec,
                "-preset", video_preset,
                *video_extra_args,
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",
                # v7.18: prevents common ffmpeg failures on long/short clips.
                "-max_muxing_queue_size", "1024",
                "-avoid_negative_ts", "make_zero",
                temporary_output,
            ]
            subprocess.run(command, check=True, capture_output=True, text=True)
            if not os.path.isfile(temporary_output) or os.path.getsize(temporary_output) <= 1024:
                raise RuntimeError("ffmpeg produced a missing or empty segment")
            media = validate_media_file(temporary_output, min_duration=0.05)
            if not media.get("ok"):
                raise RuntimeError("segment validation failed: {}".format(
                    "; ".join(media.get("errors", [media.get("error", "invalid media")]))))
            os.replace(temporary_output, output_path)
            print(f"Generated segment: {output_filename}, Size: {os.path.getsize(output_path)} bytes")
        else:
            if not os.path.isfile(output_path) or os.path.getsize(output_path) <= 1024:
                raise FileNotFoundError("existing segment is missing or empty")
            media = validate_media_file(output_path, min_duration=0.05)
            if not media.get("ok"):
                raise RuntimeError("existing segment validation failed: {}".format(
                    "; ".join(media.get("errors", [media.get("error", "invalid media")]))))
            print(f"Skipping video generation for {output_filename} (using existing).")

        end_time_seconds = start_time_seconds + duration_seconds
        input_json_path = os.path.join(project_folder, "input.json")
        cut_json.cut_json_transcript(input_json_path, json_output_path, start_time_seconds, end_time_seconds)
        return {
            "index": segment_index,
            "ok": True,
            "output": output_filename,
            "json": json_output_filename,
        }
    except subprocess.CalledProcessError as e:
        if os.path.exists(temporary_output):
            try:
                os.remove(temporary_output)
            except OSError:
                pass
        print(f"Error executing ffmpeg for segment {segment_index + 1}: {e}")
        try:
            input_json_path = os.path.join(project_folder, "input.json")
            end_time_seconds = start_time_seconds + duration_seconds
            cut_json.cut_json_transcript(input_json_path, json_output_path, start_time_seconds, end_time_seconds)
        except Exception as json_error:
            print(f"Error generating JSON for segment {segment_index + 1}: {json_error}")
        return {
            "index": segment_index,
            "ok": False,
            "output": output_filename,
            "json": json_output_filename,
            "error": str(e),
        }
    except Exception as e:
        if os.path.exists(temporary_output):
            try:
                os.remove(temporary_output)
            except OSError:
                pass
        print(f"Error processing segment {segment_index + 1}: {e}")
        return {
            "index": segment_index,
            "ok": False,
            "output": output_filename,
            "json": json_output_filename,
            "error": str(e),
        }


def _clear_downstream_artifacts(project_folder):
    """Remove generated outputs that belong to the previous segment list."""
    removed = []
    for folder_name in ("final", "final_polished", "burned_sub"):
        folder = os.path.join(project_folder, folder_name)
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                continue
            if not name.lower().endswith((".mp4", ".json", ".ass", ".srt")):
                continue
            try:
                os.remove(path)
                removed.append(path)
            except OSError as exc:
                print(f"[WARN] Could not remove stale downstream artifact {path}: {exc}")
    for name in (
        "polish_report.json", "audio_qc_report.json", "tracking_report.json", "face_modes.json",
        "publish_batch_report.json", "project_report.html", "project_report.json",
    ):
        path = os.path.join(project_folder, name)
        if os.path.isfile(path):
            try:
                os.remove(path)
                removed.append(path)
            except OSError as exc:
                print(f"[WARN] Could not remove stale project report {path}: {exc}")
    if removed:
        print(f"[cut] Cleared {len(removed)} stale downstream artifact(s).")
    return removed


def cut(response, project_folder="tmp", skip_video=False, workers=None, source_video=None, force=False, scene_snap=False):
    def generate_segments(response, project_folder, skip_video, workers, source_video):
        input_file = os.path.abspath(source_video) if source_video else os.path.join(project_folder, "input.mp4")
        if not os.path.exists(input_file):
            input_file_legacy = os.path.join(project_folder, "input_video.mp4")
            if os.path.exists(input_file_legacy):
                input_file = input_file_legacy
            else:
                raise FileNotFoundError(f"Input file not found in {project_folder}")

        cuts_folder = os.path.join(project_folder, "cuts")
        os.makedirs(cuts_folder, exist_ok=True)

        subs_folder = os.path.join(project_folder, "subs")
        os.makedirs(subs_folder, exist_ok=True)

        video_codec, video_preset, video_extra_args = _detect_best_encoder()
        segments_list = response.get("segments", [])
        if not segments_list:
            raise ValueError("No safe segments to process.")

        # v7.23: optional scene-aware snapping — detect shot boundaries once
        # and snap every segment's edges to the nearest scene change, so cuts
        # land on clean boundaries instead of mid-shot.
        scene_boundaries = []
        if scene_snap:
            try:
                from scripts.scene_detect import find_scene_cuts, snap_to_scene
                scene_boundaries = find_scene_cuts(input_file)
                if scene_boundaries:
                    print("[cut] scene detection: {} boundaries — snapping cuts".format(
                        len(scene_boundaries)))
                    snapped = 0
                    for seg in segments_list:
                        try:
                            s0 = float(seg.get("start_time", 0))
                            s1 = float(seg.get("end_time", s0))
                        except (TypeError, ValueError):
                            continue
                        original_duration = max(0.0, s1 - s0)
                        new_s0, new_s1 = snap_to_scene(s0, s1, scene_boundaries)
                        # Bounded drift: accept the snap only when the window
                        # stays non-empty and its duration stays within
                        # [0.5x, 1.5x] of the original — otherwise revert to
                        # the clamped times instead of silently growing or
                        # crushing the clip on a distant shot boundary.
                        if (
                            (new_s0, new_s1) != (s0, s1)
                            and new_s1 > new_s0
                            and _accept_scene_snap(original_duration, new_s1 - new_s0)
                        ):
                            seg["start_time"], seg["end_time"] = new_s0, new_s1
                            seg["scene_snapped"] = True
                            snapped += 1
                    print("[cut] snapped {} segment(s) to scene boundaries".format(snapped))
            except Exception as exc:
                print("[cut] scene detection skipped: {}".format(exc))

        # v7.41: final validation BEFORE cutting (spec F). Windows whose fatal
        # errors survive scene snapping are dropped here rather than handed to
        # ffmpeg; review-flagged (non-fatal) windows still cut but stay marked
        # so the publish gate refuses them.
        if segment_validator is not None:
            media_duration = None
            try:
                probe_report = probe_media(input_file)
                if isinstance(probe_report, dict) and probe_report.get("ok"):
                    media_duration = float(probe_report.get("duration") or 0) or None
            except Exception:
                media_duration = None
            allowed = []  # (original_index, segment)
            refused = []  # (original_index, segment)
            for original_index, seg in enumerate(segments_list):
                if not isinstance(seg, dict):
                    allowed.append((original_index, seg))
                    continue
                report = segment_validator.validate_final_segment(
                    seg, media_duration=media_duration, require_title=False)
                seg["final_validation"] = report
                fatal = [item for item in report.get("errors", [])
                         if item.get("code") in segment_validator.FATAL_ERROR_CODES]
                # Legacy / hand-edited segment without any stored transcript:
                # the missing-transcript check is not actionable here (the
                # pipeline validated the window when it was generated), so
                # only the other fatal codes block the cut.
                if fatal and not _has_stored_transcript(seg):
                    fatal = [item for item in fatal
                             if item.get("code") != "empty_transcript"]
                if fatal:
                    print("[cut] Refusing to cut invalid segment '{}': {}".format(
                        seg.get("title", "Untitled"),
                        "; ".join(str(item.get("message")) for item in fatal)))
                    seg["cut_status"] = "refused"
                    seg["export_blocked"] = True
                    seg["requires_review"] = True
                    refused.append((original_index, seg))
                    continue
                seg["cut_status"] = "allowed"
                allowed.append((original_index, seg))
            segments_list = [seg for _, seg in allowed]
            if not segments_list:
                _persist_cut_validation(project_folder, segments_list, allowed, refused)
                raise ValueError("No safe segments to process.")
            # Keep the refusal evidence on disk so the publish gate can refuse
            # the same segment later (the in-memory flag died with the run).
            _persist_cut_validation(project_folder, segments_list, allowed, refused)

        if not skip_video:
            # A rerun must reflect the current safe segment list, not stale cuts
            # or downstream files that could otherwise be selected for upload.
            _clear_downstream_artifacts(project_folder)
            stale_cuts = [name for name in os.listdir(cuts_folder) if name.endswith("_original_scale.mp4")]
            stale_subs = [name for name in os.listdir(subs_folder) if name.endswith("_processed.json")]
            for name in stale_cuts:
                try:
                    os.remove(os.path.join(cuts_folder, name))
                except OSError as exc:
                    print(f"[WARN] Could not remove stale cut {name}: {exc}")
            for name in stale_subs:
                try:
                    os.remove(os.path.join(subs_folder, name))
                except OSError as exc:
                    print(f"[WARN] Could not remove stale subtitle JSON {name}: {exc}")
            if stale_cuts or stale_subs:
                print(f"[cut] Cleared {len(stale_cuts)} stale video cut(s) and {len(stale_subs)} stale subtitle file(s).")
        else:
            # skip_video reruns must still drop stale artifacts of segments
            # that are now refused; otherwise the publish stage can upload a
            # previously rendered video under the new (blocked) segment.
            for original_index, _seg in refused:
                prefix = f"{original_index:03d}_"
                for folder, suffix in ((cuts_folder, "_original_scale.mp4"),
                                       (subs_folder, "_processed.json")):
                    if not os.path.isdir(folder):
                        continue
                    for name in os.listdir(folder):
                        if name.startswith(prefix) and name.endswith(suffix):
                            try:
                                os.remove(os.path.join(folder, name))
                            except OSError as exc:
                                print(f"[WARN] Could not remove stale {name}: {exc}")

        if workers is None or workers <= 0:
            cpu_count = os.cpu_count() or 4
            workers = max(1, min(len(segments_list), max(2, cpu_count // 2)))
        else:
            workers = max(1, workers)

        print(f"Processing {len(segments_list)} segments with {workers} worker(s)...")
        results = []

        if workers == 1 or len(segments_list) == 1:
            for i, segment in allowed:
                result = _process_segment(i, segment, project_folder, input_file, cuts_folder, subs_folder, skip_video, video_codec, video_preset, video_extra_args)
                results.append(result)
                if result.get("ok"):
                    print("\n" + "=" * 50 + "\n")
                else:
                    print(f"Segment {i} failed: {result.get('error', 'Unknown error')}")
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(
                        _process_segment,
                        i,
                        segment,
                        project_folder,
                        input_file,
                        cuts_folder,
                        subs_folder,
                        skip_video,
                        video_codec,
                        video_preset,
                        video_extra_args,
                    ): i
                    for i, segment in allowed
                }

                for future in as_completed(futures):
                    result = future.result()
                    results.append(result)
                    if result.get("ok"):
                        print(f"Finished segment {result['index'] + 1}/{len(segments_list)}")
                    else:
                        print(f"Segment {result['index'] + 1} failed: {result.get('error', 'Unknown error')}")
                    print("\n" + "=" * 50 + "\n")

        failures = [result for result in results if not result.get("ok")]
        if failures:
            details = "; ".join(
                "#{}: {}".format(result.get("index", "?"), result.get("error", "unknown error"))
                for result in failures[:5]
            )
            raise RuntimeError(
                "{} segment(s) failed; downstream stages were stopped. {}".format(
                    len(failures), details)
            )
        return results

    if response is None:
        json_path = os.path.join(project_folder, 'viral_segments.txt')
        with open(json_path, 'r', encoding='utf-8') as file:
            response = json.load(file)

    generate_segments(response, project_folder, skip_video, workers, source_video)
    if scene_snap:
        print("[cut] scene-aware cutting enabled (scene_snap=True)")
