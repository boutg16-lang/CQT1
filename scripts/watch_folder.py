# Python 3.10+, file: scripts/watch_folder.py, target: Windows 10/11 and Linux
"""Durable watch-folder worker for unattended local-video processing.

Files are processed only after their size/mtime remain stable. A SQLite ledger
prevents duplicate runs and survives restarts. This worker never uploads or
publishes; it creates local review-ready projects only.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts", ".mts"}


class WatchQueue:
    def __init__(self, database: Path):
        database.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(database), timeout=30)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("""CREATE TABLE IF NOT EXISTS jobs (
            path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
            status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            started_at REAL, finished_at REAL, return_code INTEGER, log_path TEXT,
            updated_at REAL NOT NULL)""")
        self.connection.execute("UPDATE jobs SET status='interrupted', updated_at=? WHERE status='running' AND started_at < ?", (time.time(), time.time() - 21600))
        self.connection.commit()

    def status(self, path: Path, stat: os.stat_result) -> str | None:
        row = self.connection.execute(
            "SELECT size, mtime_ns, status FROM jobs WHERE path=?", (str(path.resolve()),)
        ).fetchone()
        if not row:
            return None
        if row[0] != stat.st_size or row[1] != stat.st_mtime_ns:
            return None
        return str(row[2])

    def claim(self, path: Path, stat: os.stat_result) -> bool:
        now = time.time()
        with self.connection:
            cursor = self.connection.execute("""INSERT INTO jobs
                (path,size,mtime_ns,status,attempts,started_at,updated_at)
                VALUES (?,?,?,'running',1,?,?)
                ON CONFLICT(path) DO UPDATE SET size=excluded.size,
                mtime_ns=excluded.mtime_ns,status='running',attempts=jobs.attempts+1,
                started_at=excluded.started_at,finished_at=NULL,return_code=NULL,
                updated_at=excluded.updated_at
                WHERE jobs.status IN ('failed','interrupted') OR
                jobs.size<>excluded.size OR jobs.mtime_ns<>excluded.mtime_ns""",
                (str(path.resolve()), stat.st_size, stat.st_mtime_ns, now, now))
            return cursor.rowcount == 1

    def finish(self, path: Path, status: str, return_code: int, log_path: Path) -> None:
        with self.connection:
            self.connection.execute("""UPDATE jobs SET status=?,finished_at=?,
                return_code=?,log_path=?,updated_at=? WHERE path=?""",
                (status, time.time(), return_code, str(log_path), time.time(), str(path.resolve())))

    def close(self) -> None:
        self.connection.close()


def discover(folder: Path, recursive: bool) -> Iterable[Path]:
    iterator = folder.rglob("*") if recursive else folder.iterdir()
    for path in iterator:
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
            yield path


def is_stable(path: Path, settle_seconds: float) -> os.stat_result | None:
    try:
        first = path.stat()
        if time.time() - first.st_mtime < settle_seconds:
            return None
        time.sleep(min(1.0, max(0.1, settle_seconds / 4)))
        second = path.stat()
        if first.st_size <= 0 or (first.st_size, first.st_mtime_ns) != (second.st_size, second.st_mtime_ns):
            return None
        return second
    except OSError:
        return None


def resolve_runtime(args: argparse.Namespace) -> tuple[str, str, str]:
    """Resolve auto device/model from a saved hardware profile, with safe fallbacks."""
    profile_path = args.gpu_profile.expanduser().resolve() if args.gpu_profile else None
    profile: dict = {}
    if profile_path and profile_path.is_file():
        try:
            loaded = json.loads(profile_path.read_text(encoding="utf-8"))
            profile = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            profile = {}
    transcription = profile.get("transcription", {}) if isinstance(profile, dict) else {}
    if not isinstance(transcription, dict):
        transcription = {}
    device = args.device
    if device == "auto":
        device = str(transcription.get("device") or "auto")
        if device not in {"auto", "cuda", "cpu"}:
            device = "auto"
    model = args.model
    if model == "auto":
        model = str(transcription.get("model") or "medium")
        if not model or any(ch.isspace() for ch in model):
            model = "medium"
    tier = str(profile.get("vram_tier") or "unknown")
    return device, model, tier


def process_file(path: Path, args: argparse.Namespace, queue: WatchQueue) -> int | None:
    stat = is_stable(path, args.settle_seconds)
    if stat is None:
        return None
    current = queue.status(path, stat)
    if current in {"success", "running"}:
        return None
    if not queue.claim(path, stat):
        return None

    logs = args.state_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    safe_stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in path.stem)[:80] or "video"
    log_path = logs / f"{safe_stem}-{stat.st_mtime_ns}.log"
    device, model, _tier = resolve_runtime(args)
    command = [sys.executable, str(args.app), "--local-video", str(path.resolve()),
               "--autopilot", "--viral", "--skip-prompts", "--platform", args.platform,
               "--prefer-hardware-acceleration", "--transcription-device", device,
               "--model", model, "--safety-mode", "block", "--risk-gate", "block"]
    if args.segments:
        command.extend(["--segments", str(args.segments)])
    env = os.environ.copy()
    env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    env.setdefault("CUDA_MODULE_LOADING", "LAZY")
    env.setdefault("OMP_NUM_THREADS", str(max(2, min(8, (os.cpu_count() or 4) // 2))))
    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as log:
            log.write("COMMAND: " + json.dumps(command, ensure_ascii=False) + "\n\n")
            log.flush()
            result = subprocess.run(command, cwd=str(args.app.parent), env=env,
                                    stdout=log, stderr=subprocess.STDOUT,
                                    timeout=args.job_timeout if args.job_timeout > 0 else None,
                                    check=False)
        code = int(result.returncode)
        queue.finish(path, "success" if code == 0 else "failed", code, log_path)
        return code
    except subprocess.TimeoutExpired:
        with log_path.open("a", encoding="utf-8") as log:
            log.write("\n[watch-folder] Job timed out.\n")
        queue.finish(path, "failed", 124, log_path)
        return 124
    except OSError as exc:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n[watch-folder] Could not start worker: {exc}\n")
        queue.finish(path, "failed", 125, log_path)
        return 125


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OUSSAMA Cutter unattended watch-folder worker")
    parser.add_argument("--input", type=Path, required=True, help="Folder receiving source videos")
    parser.add_argument("--state-dir", type=Path, default=Path(".watch-folder"))
    parser.add_argument("--app", type=Path, default=Path("main_improved.py"))
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--settle-seconds", type=float, default=15.0)
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--once", action="store_true", help="Scan once and exit")
    parser.add_argument("--platform", choices=["yt_shorts", "tiktok", "reels", "yt_standard"], default="yt_shorts")
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    parser.add_argument("--model", default="auto", help="Whisper model, or auto to use the detected GPU profile")
    parser.add_argument("--gpu-profile", type=Path, default=Path("gpu_profile.json"), help="Hardware profile JSON used by auto device/model selection")
    parser.add_argument("--segments", type=int, default=10)
    parser.add_argument("--job-timeout", type=int, default=0, help="Seconds; 0 means unlimited")
    parser.add_argument("--retry-failed", action="store_true", help="Retry failed jobs on subsequent scans")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.input = args.input.expanduser().resolve()
    args.state_dir = args.state_dir.expanduser().resolve()
    args.app = args.app.expanduser().resolve()
    if not args.input.is_dir():
        print(f"[watch-folder] Input folder does not exist: {args.input}", file=sys.stderr)
        return 2
    if not args.app.is_file():
        print(f"[watch-folder] Application entry point not found: {args.app}", file=sys.stderr)
        return 2
    if args.poll_seconds < 1 or args.settle_seconds < 0:
        print("[watch-folder] poll-seconds must be >=1 and settle-seconds >=0", file=sys.stderr)
        return 2
    args.state_dir.mkdir(parents=True, exist_ok=True)
    queue = WatchQueue(args.state_dir / "jobs.sqlite3")
    device, model, tier = resolve_runtime(args)
    print(f"[watch-folder] Watching {args.input} | device={device} | model={model} | GPU tier={tier} | platform={args.platform}")
    try:
        while True:
            for path in sorted(discover(args.input, args.recursive)):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                state = queue.status(path, stat)
                if state == "failed" and not args.retry_failed:
                    continue
                result = process_file(path, args, queue)
                if result is not None:
                    print(f"[watch-folder] {path.name}: {'completed' if result == 0 else 'failed'} (exit {result})")
            if args.once:
                return 0
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        print("\n[watch-folder] Stopped. Queue state is saved; completed jobs will not repeat.")
        return 0
    finally:
        queue.close()


if __name__ == "__main__":
    raise SystemExit(main())
