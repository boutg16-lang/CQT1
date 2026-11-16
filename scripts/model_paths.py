"""Keep downloaded models on the project drive, not the Windows C: cache.

Hugging Face defaults to ``%USERPROFILE%\\.cache\\huggingface`` and Torch to
``%USERPROFILE%\\.cache\\torch``. On a machine where C: is small and the app
lives on D:, that fills the system drive. This module points every cache at
``<project>/.models`` before the heavy libraries import, while respecting an
explicit custom path the user already configured.

Override with:  VIRALCUTTER_MODELS_DIR=D:\\Models
Inspect with:   python -m scripts.model_paths --show
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Dict

CACHE_DIRNAME = ".models"


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _fallback_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / "OUSSAMA Cutter" / "models"


def models_root() -> Path:
    override = os.environ.get("VIRALCUTTER_MODELS_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (app_root() / CACHE_DIRNAME).resolve()


def _ensure_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _default_hf_home() -> Path:
    return Path(os.path.expanduser("~")) / ".cache" / "huggingface"


def _set_cache(env_name: str, target: Path, default: Path | None, force: bool) -> None:
    current = os.environ.get(env_name, "").strip()
    if force or not current:
        os.environ[env_name] = str(target)
        return
    if default is not None:
        try:
            if Path(current).expanduser().resolve() == default.expanduser().resolve():
                # The user never chose this path; it is Hugging Face's default.
                os.environ[env_name] = str(target)
        except OSError:
            pass


def configure_model_cache(root: str | os.PathLike[str] | None = None,
                          force: bool = False) -> Dict[str, str]:
    """Point HF/Torch caches at the project drive. Idempotent and safe."""
    base = Path(root).expanduser().resolve() if root else models_root()
    if os.environ.get("VIRALCUTTER_MODELS_DIR", "").strip():
        # An explicit project root overrides even a custom HF_HOME.
        force = True
    if not _ensure_writable(base):
        base = _fallback_root()
        _ensure_writable(base)

    # Xet's parallel downloader can stall on Windows behind a proxy or a
    # filtered network; HTTP is slower but completes. VIRALCUTTER_ENABLE_XET=1
    # opts back in. Timeouts are raised from 10s because model shards are big.
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")
    if os.environ.get("VIRALCUTTER_ENABLE_XET", "").strip().lower() not in {
            "1", "true", "yes", "on"}:
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

    hf_home = base / "huggingface"
    hf_hub = hf_home / "hub"
    torch_home = base / "torch"
    for path in (hf_home, hf_hub, torch_home):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    _set_cache("HF_HOME", hf_home, _default_hf_home(), force)
    _set_cache("HUGGINGFACE_HUB_CACHE", hf_hub, _default_hf_home() / "hub", force)
    _set_cache("HF_HUB_CACHE", hf_hub, _default_hf_home() / "hub", force)
    _set_cache("TRANSFORMERS_CACHE", hf_hub, _default_hf_home() / "hub", force)
    _set_cache("HF_DATASETS_CACHE", hf_home / "datasets", _default_hf_home() / "datasets", force)
    _set_cache("TORCH_HOME", torch_home,
               Path(os.path.expanduser("~")) / ".cache" / "torch", force)

    return {
        "models_root": str(base),
        "hf_home": str(hf_home),
        "hf_hub_cache": str(hf_hub),
        "torch_home": str(torch_home),
    }


def migrate_cache(source: str | os.PathLike[str] | None = None,
                  move: bool = False,
                  destination: str | os.PathLike[str] | None = None) -> Dict[str, str]:
    """Copy the old Hugging Face cache to the project drive.

    Default: copy only; the old cache stays as a fallback. With move=True the
    source is removed after a successful copy. Symlinks are preserved first;
    if Windows refuses, file contents are copied instead.
    """
    src = Path(source).expanduser().resolve() if source else _default_hf_home()
    dst = Path(destination).expanduser().resolve() if destination else Path(
        configure_model_cache()["hf_home"])
    if src == dst:
        return {"ok": True, "copied": False, "reason": "source == destination",
                "source": str(src), "destination": str(dst)}
    if not src.is_dir():
        return {"ok": False, "copied": False, "reason": "source does not exist",
                "source": str(src), "destination": str(dst)}
    dst.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=True)
        mode = "symlinks"
    except OSError:
        shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=False)
        mode = "files"
    removed = False
    if move:
        try:
            shutil.rmtree(src)
            removed = True
        except OSError:
            removed = False
    return {"ok": True, "copied": True, "mode": mode, "moved": removed,
            "source": str(src), "destination": str(dst)}


def current_paths() -> Dict[str, str]:
    return {
        "models_root": str(models_root()),
        "hf_home": os.environ.get("HF_HOME", str(_default_hf_home())),
        "hf_hub_cache": os.environ.get(
            "HUGGINGFACE_HUB_CACHE", str(_default_hf_home() / "hub")),
        "torch_home": os.environ.get(
            "TORCH_HOME", str(Path(os.path.expanduser("~")) / ".cache" / "torch")),
    }


def _main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="OUSSAMA Cutter model cache paths")
    parser.add_argument("--show", action="store_true", help="print the active paths")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--migrate", action="store_true",
                        help="copy the old user cache into the project cache")
    parser.add_argument("--move", action="store_true",
                        help="with --migrate: remove the old cache after copying")
    parser.add_argument("--from", dest="source", default=None,
                        help="old cache path (default: ~/.cache/huggingface)")
    args = parser.parse_args()
    configure_model_cache()
    if args.migrate:
        result = migrate_cache(source=args.source, move=args.move)
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json
              else "\n".join("{}: {}".format(k, v) for k, v in result.items()))
        return 0 if result.get("ok") else 1
    paths = current_paths()
    if args.json:
        print(json.dumps(paths, ensure_ascii=False, indent=2))
    else:
        for key, value in paths.items():
            print("{}: {}".format(key, value))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
