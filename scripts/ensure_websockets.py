import argparse
import importlib
import importlib.metadata
import os
import re
import shutil
import subprocess
import sys

MINIMUM_VERSION = "16.1.1"
COMPATIBILITY_SPEC = "websockets>=16.1.1,<17"


def is_compatible_version(version):
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version or "")
    if not match:
        return False
    parsed = tuple(int(part) for part in match.groups())
    return (16, 1, 1) <= parsed < (17, 0, 0)


def find_uv():
    uv = shutil.which("uv")
    if uv:
        return uv
    executable = "uv.exe" if sys.platform == "win32" else "uv"
    candidate = os.path.join(os.path.expanduser("~"), ".local", "bin", executable)
    return candidate if os.path.isfile(candidate) else None


def installed_version():
    try:
        return importlib.metadata.version("websockets")
    except (importlib.metadata.PackageNotFoundError, ValueError):
        return None


def has_connection_closed_export():
    try:
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "import websockets; assert hasattr(websockets, 'ConnectionClosed')",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def needs_repair():
    if not is_compatible_version(installed_version()):
        return True
    return not has_connection_closed_export()


def repair():
    if not needs_repair():
        return True
    uv = find_uv()
    spec = COMPATIBILITY_SPEC
    if uv:
        command = [
            uv,
            "pip",
            "install",
            "--python",
            sys.executable,
            "--reinstall-package",
            "websockets",
            "--no-deps",
            spec,
        ]
    else:
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--force-reinstall",
            "--no-deps",
            spec,
        ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    importlib.invalidate_caches()
    return is_compatible_version(installed_version()) and has_connection_closed_export()


def main(argv=None):
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--repair", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        if needs_repair():
            print("[ERROR] WebSockets must be within {}.".format(COMPATIBILITY_SPEC))
            return 1
        print("[OK] WebSockets within {} is compatible.".format(COMPATIBILITY_SPEC))
        return 0
    print("[INFO] Reinstalling WebSockets within {} without changing other packages.".format(COMPATIBILITY_SPEC))
    if repair():
        print("[OK] WebSockets within {} is compatible.".format(COMPATIBILITY_SPEC))
        return 0
    print("[ERROR] WebSockets repair failed. Close OUSSAMA Cutter and Python processes, then retry.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
