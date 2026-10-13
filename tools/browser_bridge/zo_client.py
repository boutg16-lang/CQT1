import argparse
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

from tools.browser_bridge.agent import (
    BridgeError,
    create_session,
    perform_action,
    session_status,
    stop_session,
)
from tools.browser_bridge.credentials import CredentialError, read_control_token

PROJECT_ROOT = Path(__file__).resolve().parents[2]

STATE_DIR = PROJECT_ROOT / ".browser-bridge-state"
STATE_FILE = STATE_DIR / "session.json"
SCREENSHOT_FILE = STATE_DIR / "latest.jpg"
CONTROL_TOKEN_FILE = STATE_DIR / "control.token"


def _ensure_state_dir():
    if STATE_DIR.is_symlink():
        raise BridgeError("The browser bridge state directory cannot be a symbolic link")
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)


def _atomic_write(path, data):
    _ensure_state_dir()
    if path.is_symlink():
        raise BridgeError("Refusing to write through a symbolic link")
    descriptor, temporary = tempfile.mkstemp(prefix=".bridge-", dir=str(STATE_DIR))
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _save_state(state):
    payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    _atomic_write(STATE_FILE, payload)


def _read_state():
    if not STATE_FILE.exists() or STATE_FILE.is_symlink():
        raise BridgeError("No local CAT browser session. Start one first.")
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeError("The local browser session file is invalid") from exc
    if not isinstance(state, dict) or not all(
        isinstance(state.get(key), str) and state[key]
        for key in ("session_id", "agent_token")
    ):
        raise BridgeError("The local browser session file is invalid")
    return state


def _remove_state():
    if STATE_DIR.is_symlink():
        raise BridgeError("The browser bridge state directory cannot be a symbolic link")
    for path in (STATE_FILE, SCREENSHOT_FILE):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _read_control_token():
    try:
        return read_control_token(CONTROL_TOKEN_FILE)
    except CredentialError as exc:
        raise BridgeError(str(exc)) from exc


def _start():
    if STATE_FILE.exists():
        previous = _read_state()
        try:
            status = session_status(previous["session_id"], previous["agent_token"])
        except BridgeError as exc:
            if exc.status not in {401, 403, 404, 410}:
                raise
        else:
            if status.get("active"):
                raise BridgeError("A browser session is already active; use status or stop first.")
        _remove_state()
    session = create_session(control_token=_read_control_token())
    state = {
        "session_id": session["session_id"],
        "agent_token": session["agent_token"],
    }
    try:
        _save_state(state)
    except Exception as exc:
        try:
            stop_session(state["session_id"], state["agent_token"])
        except Exception as cleanup_error:
            print("Could not stop the unpaired session: {}".format(type(cleanup_error).__name__), file=sys.stderr)
        raise BridgeError("Could not safely store the temporary session token") from exc
    print("Pairing code: {}".format(session["pairing_code"]))
    print("Enter this one-time code in Start_Browser_Test.bat on the computer running CAT.")
    print("The pairing code is valid for {} minutes.".format(max(1, int(session.get("expires_in_seconds", 900) / 60))))
    print("The paired session has no fixed time limit while the local bridge stays connected. If the relay restarts or the heartbeat is lost, reconnect with a new code.")
    return 0


def _status():
    state = _read_state()
    status = session_status(state["session_id"], state["agent_token"])
    visible = {
        key: status[key]
        for key in ("active", "paired", "project_url", "lease_expires_at", "actions_used", "actions_remaining", "actions_limit_per_minute")
        if key in status
    }
    print(json.dumps(visible, ensure_ascii=False, indent=2))
    return 0


def _action(action):
    state = _read_state()
    result = perform_action(state["session_id"], state["agent_token"], action)
    if action["type"] == "screenshot" and result.get("image_base64"):
        try:
            image = base64.b64decode(result.pop("image_base64"), validate=True)
        except (ValueError, TypeError) as exc:
            raise BridgeError("The local browser returned an invalid screenshot") from exc
        if not image or len(image) > 5_000_000:
            raise BridgeError("The local browser screenshot exceeded the safe size limit")
        _atomic_write(SCREENSHOT_FILE, image)
        result["screenshot_path"] = str(SCREENSHOT_FILE)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", True) else 1


def _stop():
    state = _read_state()
    try:
        stop_session(state["session_id"], state["agent_token"])
    except BridgeError as exc:
        if exc.status not in {401, 403, 404, 410}:
            raise
    _remove_state()
    print("Browser session stopped; local tokens and screenshot removed.")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="Safely control the paired local CAT browser.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("start")
    subparsers.add_parser("status")
    subparsers.add_parser("snapshot")
    subparsers.add_parser("screenshot")
    subparsers.add_parser("stop")

    navigate = subparsers.add_parser("navigate")
    navigate.add_argument("--path", required=True)

    click = subparsers.add_parser("click")
    click.add_argument("--role", required=True)
    click.add_argument("--name", required=True)

    fill = subparsers.add_parser("fill")
    fill.add_argument("--name", required=True)
    fill.add_argument("--value", required=True)

    select = subparsers.add_parser("select")
    select.add_argument("--name", required=True)
    select.add_argument("--label", required=True)

    press = subparsers.add_parser("press")
    press.add_argument("--name", required=True)
    press.add_argument("--key", required=True)

    scroll = subparsers.add_parser("scroll")
    scroll.add_argument("--direction", choices=("up", "down"), required=True)
    scroll.add_argument("--pixels", type=int, required=True)

    wait = subparsers.add_parser("wait")
    wait.add_argument("--milliseconds", type=int, required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "start":
            return _start()
        if args.command == "status":
            return _status()
        if args.command == "stop":
            return _stop()
        action = {"type": args.command}
        if args.command in {"navigate", "click", "fill", "select", "press", "scroll", "wait"}:
            action.update(
                {
                    key: value
                    for key, value in vars(args).items()
                    if key != "command"
                }
            )
        return _action(action)
    except BridgeError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
