import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

RELAY_HOST = "cat-browser-bridge-2nnhh.zocomputer.io"
DEFAULT_RELAY_URL = "https://{}".format(RELAY_HOST)


class BridgeError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def validate_relay_url(value):
    if not isinstance(value, str) or len(value) > 300:
        raise BridgeError("Relay URL is invalid")
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError as exc:
        raise BridgeError("Relay URL is invalid") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != RELAY_HOST
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port not in {None, 443}
    ):
        raise BridgeError("The relay must use the official CAT HTTPS endpoint")
    return DEFAULT_RELAY_URL


def request(relay_url, path, method="GET", token=None, body=None, timeout=35):
    relay_url = validate_relay_url(relay_url)
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 CATBrowserBridge/1.0",
    }
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(relay_url + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {"error": "Relay returned HTTP {}".format(exc.code)}
        raise BridgeError(payload.get("error", "Relay request failed"), status=exc.code) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise BridgeError("Relay request failed: {}".format(exc)) from exc
    if not isinstance(payload, dict):
        raise BridgeError("Relay returned an invalid response")
    return payload


def create_session(relay_url=DEFAULT_RELAY_URL, control_token=None):
    if not isinstance(control_token, str) or len(control_token) < 40:
        raise BridgeError("The bridge control credential is missing")
    return request(
        relay_url,
        "/v1/sessions",
        method="POST",
        token=control_token,
        body={},
    )


def session_status(session_id, token, relay_url=DEFAULT_RELAY_URL):
    return request(relay_url, "/v1/sessions/{}".format(session_id), token=token)


def perform_action(session_id, token, action, relay_url=DEFAULT_RELAY_URL):
    return request(
        relay_url,
        "/v1/sessions/{}/actions".format(session_id),
        method="POST",
        token=token,
        body={"action": action},
        timeout=35,
    )


def stop_session(session_id, token, relay_url=DEFAULT_RELAY_URL):
    return request(
        relay_url,
        "/v1/sessions/{}/stop".format(session_id),
        method="POST",
        token=token,
        body={},
    )


def main():
    parser = argparse.ArgumentParser(description="Create and control an isolated local CAT browser test session.")
    parser.add_argument("--relay-url", default=os.environ.get("CAT_BROWSER_BRIDGE_URL", DEFAULT_RELAY_URL))
    parser.add_argument("command", choices=("create", "status", "action", "stop"))
    parser.add_argument("--session-id")
    parser.add_argument("--action-json")
    args = parser.parse_args()
    try:
        if args.command == "create":
            control_token = os.environ.get("CAT_BROWSER_BRIDGE_CONTROL_TOKEN", "")
            result = create_session(args.relay_url, control_token)
        else:
            session_id = args.session_id
            token = os.environ.get("CAT_BROWSER_AGENT_TOKEN", "")
            if not session_id or not token:
                raise BridgeError("Set CAT_BROWSER_AGENT_TOKEN and provide --session-id")
            if args.command == "status":
                result = session_status(session_id, token, args.relay_url)
            elif args.command == "stop":
                result = stop_session(session_id, token, args.relay_url)
            else:
                if not args.action_json:
                    raise BridgeError("--action-json is required for action")
                result = perform_action(session_id, token, json.loads(args.action_json), args.relay_url)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (BridgeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
