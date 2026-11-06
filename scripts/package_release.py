from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app_version import __version__

EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "env", "build", "dist", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".gradio", ".cache", "cache",
    "virals", "inbox", "models", "output", "tmp", "updates", "logs",
    ".watch-folder", ".browser-bridge-state", ".viralcutter", "htmlcov",
    ".aws", ".ssh", "credentials", "secrets", "tokens", "private_keys",
}
EXCLUDED_FILES = {
    ".env", "api_config.json", "api_config.secure.json", "api_config.local.json",
    "client_secrets.json", "yt_token.json", "token.json", "tokens.json",
    "credentials.json", "service_account.json", "webui_settings.json",
    "gpu_profile.json", "safety_blocklist_cache.json", "pipeline_runner_state.json",
    "strike_feedback.json", "safety_terms.json", "analytics_report.json",
    "windows_diagnostics.json", ".windows-diagnostics.json",
    ".oussama_policy_lexicon.sqlite3", "id_rsa", "id_ecdsa", "id_ed25519",
}
SENSITIVE_CONFIG_SUFFIXES = (
    ".json", ".json5", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".conf", ".txt", ".pickle", ".pkl", ".dat", ".env",
)
SECRET_NAME_MARKERS = (
    "credential", "secret", "token", "private_key", "private-key",
    "service_account", "service-account", "api_key", "api-key", "apikey", "oauth",
    "api_config", "api-config", "webui_settings", "webui-settings",
)
MEDIA_SUFFIXES = {
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".mp3", ".wav", ".m4a",
    ".aac", ".flac", ".ogg", ".opus", ".wma",
}


def excluded_path(relative: Path) -> bool:
    if any(part.lower() in EXCLUDED_DIRS for part in relative.parts):
        return True
    lowered = relative.name.lower()
    if lowered in {value.lower() for value in EXCLUDED_FILES}:
        return True
    if lowered.startswith(".env.") and lowered != ".env.example":
        return True
    if lowered.endswith(".env"):
        return True
    if lowered in {".envrc", ".secrets", ".secret"}:
        return True
    if lowered.endswith((".example.json", ".example.json5", ".example.yaml",
                         ".example.yml", ".example.toml", ".example.ini", ".example.cfg")) and any(
            marker in lowered for marker in SECRET_NAME_MARKERS):
        return False
    if relative.suffix.lower() in SENSITIVE_CONFIG_SUFFIXES and any(
            marker in lowered for marker in SECRET_NAME_MARKERS):
        return True
    if lowered.endswith((".log", ".pyc", ".pyo", ".tmp", ".bak", ".sqlite",
                         ".sqlite3", ".sqlite-wal", ".sqlite3-wal", ".sqlite3-shm",
                         ".db", ".db-wal", ".db-shm", ".zip", ".pem", ".p12",
                         ".pfx", ".ppk", ".key")):
        return True
    if relative.suffix.lower() in MEDIA_SUFFIXES:
        return True
    if relative.suffix.lower() == ".srt" and relative.parts[:2] != ("tests", "fixtures"):
        return True
    if any(part.lower().endswith(".egg-info") for part in relative.parts):
        return True
    return False


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_release(source_root: Path, output_path: Path) -> dict:
    source_root = source_root.resolve()
    output_path = output_path.resolve()
    if not source_root.is_dir():
        raise NotADirectoryError(source_root)
    if output_path.is_relative_to(source_root):
        raise ValueError("The release archive must be outside the source tree.")
    files = []
    for path in source_root.rglob("*"):
        relative = path.relative_to(source_root)
        if path.is_symlink() or not path.is_file() or excluded_path(relative):
            continue
        files.append((relative, path))
    files.sort(key=lambda item: item[0].as_posix().casefold())
    if not files:
        raise ValueError("No source files were selected for packaging.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(dir=output_path.parent, suffix=".tmp")
    os.close(fd)
    root_name = "OUSSAMA-Cutter-{}".format(__version__)
    try:
        with zipfile.ZipFile(temporary_name, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6) as archive:
            for relative, path in files:
                info = zipfile.ZipInfo("{}/{}".format(root_name, relative.as_posix()))
                info.date_time = (2026, 11, 5, 0, 0, 0)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IMODE(path.stat().st_mode) & 0xFFFF) << 16
                archive.writestr(info, path.read_bytes())
        os.replace(temporary_name, output_path)
    finally:
        if os.path.exists(temporary_name):
            os.remove(temporary_name)

    digest = sha256_file(output_path)
    manifest = {
        "product": "OUSSAMA Cutter",
        "version": __version__,
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "archive": output_path.name,
        "sha256": digest,
        "file_count": len(files),
        "uncompressed_bytes": sum(path.stat().st_size for _, path in files),
        "files": [{"path": relative.as_posix(), "size": path.stat().st_size}
                  for relative, path in files],
        "excluded_categories": [
            "credentials and local configuration",
            "models, generated outputs, logs, caches, and build environments",
            "local video and audio media",
            "symbolic links",
        ],
    }
    manifest_path = output_path.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    output_path.with_suffix(output_path.suffix + ".sha256").write_text(
        "{}  {}\n".format(digest, output_path.name), encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    source_root = Path(__file__).resolve().parents[1]
    project_dir = source_root.parent
    parser = argparse.ArgumentParser(description="Build a clean OUSSAMA Cutter source archive.")
    parser.add_argument("--source", type=Path, default=source_root)
    parser.add_argument("--output", type=Path,
                        default=project_dir / "OUSSAMA_Cutter_8.0_ULTIMATE_FINAL.zip")
    args = parser.parse_args(argv)
    manifest = package_release(args.source, args.output)
    print(json.dumps({
        "archive": str(args.output.resolve()),
        "manifest": str(args.output.resolve().with_suffix(".manifest.json")),
        "sha256": manifest["sha256"],
        "file_count": manifest["file_count"],
        "version": manifest["version"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
