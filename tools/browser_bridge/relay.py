import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from tools.browser_bridge.credentials import CredentialError, read_control_token
from tools.browser_bridge.policy import (
    project_origin,
    validate_action,
    validate_navigation_path,
    validate_project_url,
)

LOGGER = logging.getLogger("cat.browser_bridge")
TTL_SECONDS = 900
CLIENT_LEASE_SECONDS = 120
ACTION_WINDOW_SECONDS = 60
MAX_SESSIONS = 64
MAX_ACTIONS_PER_MINUTE = 30
MAX_PENDING_ACTIONS = 1
MAX_BODY_BYTES = 3_000_000
LONG_POLL_SECONDS = 20
ACTION_TIMEOUT_SECONDS = 30
CREATE_WINDOW_SECONDS = 60
CREATE_LIMIT_PER_ADDRESS = 5
PAIR_LIMIT_PER_ADDRESS = 20
CONTROL_TOKEN_FILE_ENV = "CAT_BROWSER_BRIDGE_CONTROL_TOKEN_FILE"



class Session:
    def __init__(self, session_id, agent_token, pairing_code, now):
        self.session_id = session_id
        self.agent_token_hash = _digest(agent_token)
        self.pairing_code_hash = _digest(_normalize_pairing_code(pairing_code))
        self.client_token_hash = None
        self.project_url = None
        self.origin = None
        self.created_at = now
        self.expires_at = now + TTL_SECONDS
        self.active = True
        self.ended_at = None
        self.action_count = 0
        self.action_times = deque()
        self.pending = deque()
        self.in_flight = None
        self.results = {}
        self.condition = threading.Condition(STATE_LOCK)


STATE_LOCK = threading.RLock()
SESSIONS = {}
CREATE_REQUESTS = {}
PAIR_REQUESTS = {}


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).digest()


def _matches(stored, value):
    return stored is not None and isinstance(value, str) and hmac.compare_digest(stored, _digest(value))


def _new_pairing_code():
    raw = secrets.token_bytes(10)
    code = base64.b32encode(raw).decode("ascii").rstrip("=")
    return "-".join(code[index:index + 4] for index in range(0, len(code), 4))


def _normalize_pairing_code(value):
    return "".join(value.upper().split()).replace("-", "") if isinstance(value, str) else ""


def _prune_locked(now=None):
    now = time.time() if now is None else now
    for session_id, session in tuple(SESSIONS.items()):
        if session.expires_at <= now or (session.ended_at and session.ended_at + 120 <= now):
            with session.condition:
                session.active = False
                session.condition.notify_all()
            SESSIONS.pop(session_id, None)
    for address, requests in tuple(CREATE_REQUESTS.items()):
        recent = [timestamp for timestamp in requests if timestamp > now - CREATE_WINDOW_SECONDS]
        if recent:
            CREATE_REQUESTS[address] = recent
        else:
            CREATE_REQUESTS.pop(address, None)
    for address, requests in tuple(PAIR_REQUESTS.items()):
        recent = [timestamp for timestamp in requests if timestamp > now - CREATE_WINDOW_SECONDS]
        if recent:
            PAIR_REQUESTS[address] = recent
        else:
            PAIR_REQUESTS.pop(address, None)


def _public_status(session):
    now = time.time()
    actions_in_window = sum(timestamp > now - ACTION_WINDOW_SECONDS for timestamp in session.action_times)
    return {
        "session_id": session.session_id,
        "paired": session.client_token_hash is not None,
        "active": session.active,
        "project_url": session.project_url,
        "origin": session.origin,
        "lease_expires_at": int(session.expires_at),
        "actions_used": session.action_count,
        "actions_remaining": max(0, MAX_ACTIONS_PER_MINUTE - actions_in_window),
        "actions_limit_per_minute": MAX_ACTIONS_PER_MINUTE,
    }


def _renew_client_lease(session, now=None):
    if session.active and session.client_token_hash is not None:
        session.expires_at = (time.time() if now is None else now) + CLIENT_LEASE_SECONDS


def _take_action_slot(session, now=None):
    now = time.time() if now is None else now
    cutoff = now - ACTION_WINDOW_SECONDS
    while session.action_times and session.action_times[0] <= cutoff:
        session.action_times.popleft()
    if len(session.action_times) >= MAX_ACTIONS_PER_MINUTE:
        return False
    session.action_times.append(now)
    session.action_count += 1
    return True









def _configured_control_token():
    try:
        return read_control_token(os.environ.get(CONTROL_TOKEN_FILE_ENV))
    except CredentialError:
        return None


class RelayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format_string, *args):
        LOGGER.info("http_request status=%s", args[1] if len(args) > 1 else "unknown")

    def _send(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length is required")
        length = int(raw_length)
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("Request body is too large")
        raw = self.rfile.read(length)
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _discard_rejected_body(self):
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return
        try:
            length = int(raw_length)
        except ValueError:
            self.close_connection = True
            return
        if length < 0 or length > 64 * 1024:
            self.close_connection = True
            return
        previous_timeout = self.connection.gettimeout()
        try:
            self.connection.settimeout(2)
            remaining = length
            while remaining:
                chunk = self.rfile.read(min(remaining, 8192))
                if not chunk:
                    self.close_connection = True
                    return
                remaining -= len(chunk)
        except OSError:
            self.close_connection = True
        finally:
            self.connection.settimeout(previous_timeout)

    def _path_parts(self):
        parsed = urlsplit(self.path)
        if parsed.query or parsed.fragment:
            raise ValueError("Queries and fragments are not supported")
        return [part for part in parsed.path.split("/") if part]

    def _get_session(self, session_id):
        with STATE_LOCK:
            _prune_locked()
            session = SESSIONS.get(session_id)
        if session is None:
            raise KeyError("Session not found or expired")
        return session

    def _authorized(self, session, token, actor):
        if actor == "agent":
            return _matches(session.agent_token_hash, token)
        if actor == "client":
            return _matches(session.client_token_hash, token)
        return _matches(session.agent_token_hash, token) or _matches(session.client_token_hash, token)

    def _require_auth(self, session, actor="agent"):
        header = self.headers.get("Authorization", "")
        scheme, separator, token = header.partition(" ")
        if scheme.lower() != "bearer" or not separator or not self._authorized(session, token.strip(), actor):
            raise PermissionError("Unauthorized")

    def do_GET(self):
        try:
            parts = self._path_parts()
            if parts == ["healthz"]:
                self._send(200, {"ok": True, "service": "cat-browser-bridge"})
                return
            if len(parts) == 3 and parts[0] == "v1" and parts[1] == "sessions":
                session = self._get_session(parts[2])
                self._require_auth(session, "agent")
                with session.condition:
                    self._send(200, _public_status(session))
                return
            if len(parts) == 4 and parts[0] == "v1" and parts[1] == "sessions" and parts[3] == "next":
                session = self._get_session(parts[2])
                self._require_auth(session, "client")
                deadline = time.monotonic() + LONG_POLL_SECONDS
                with session.condition:
                    _renew_client_lease(session)
                    while session.active and not session.pending and time.monotonic() < deadline:
                        session.condition.wait(timeout=max(0, deadline - time.monotonic()))
                    _renew_client_lease(session)
                    if not session.active:
                        self._send(200, {"command": None, "stop": True})
                    elif session.pending:
                        command = session.pending.popleft()
                        session.in_flight = command["command_id"]
                        self._send(200, {"command": command, "stop": False})
                    else:
                        self._send(200, {"command": None, "stop": False})
                return
            self._send(404, {"error": "Not found"})
        except PermissionError as exc:
            self._send(401, {"error": str(exc)})
        except KeyError as exc:
            self._send(404, {"error": str(exc).strip("'")})
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(400, {"error": str(exc)})
        except Exception:
            LOGGER.exception("request_failed method=GET")
            self._send(500, {"error": "Internal server error"})

    def do_POST(self):
        try:
            parts = self._path_parts()
            if parts == ["v1", "sessions"]:
                self._create_session()
                return
            if parts == ["v1", "pair"]:
                self._pair()
                return
            if len(parts) == 4 and parts[:2] == ["v1", "sessions"] and parts[3] == "actions":
                self._run_action(parts[2])
                return
            if len(parts) == 4 and parts[:2] == ["v1", "sessions"] and parts[3] == "results":
                self._complete_action(parts[2])
                return
            if len(parts) == 4 and parts[:2] == ["v1", "sessions"] and parts[3] == "stop":
                self._stop(parts[2])
                return
            self._send(404, {"error": "Not found"})
        except PermissionError as exc:
            self._send(401, {"error": str(exc)})
        except KeyError as exc:
            self._send(404, {"error": str(exc).strip("'")})
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(400, {"error": str(exc)})
        except Exception:
            LOGGER.exception("request_failed method=POST")
            self._send(500, {"error": "Internal server error"})

    def _require_control(self):
        expected = _configured_control_token()
        if expected is None:
            self._discard_rejected_body()
            self._send(503, {"error": "Bridge control authentication is unavailable"})
            return False
        scheme, separator, supplied = self.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not separator or not hmac.compare_digest(supplied, expected):
            self._discard_rejected_body()
            self._send(401, {"error": "Unauthorized"})
            return False
        return True

    def _create_session(self):
        if not self._require_control():
            return

        body = self._read_json()
        if body:
            raise ValueError("Session creation does not accept options")
        address = self.client_address[0]
        now = time.time()
        with STATE_LOCK:
            _prune_locked(now)
            requests = CREATE_REQUESTS.setdefault(address, [])
            requests[:] = [timestamp for timestamp in requests if timestamp > now - CREATE_WINDOW_SECONDS]
            if len(requests) >= CREATE_LIMIT_PER_ADDRESS:
                self._send(429, {"error": "Session creation rate limit reached"})
                return
            if len(SESSIONS) >= MAX_SESSIONS:
                self._send(503, {"error": "Bridge is at its temporary session limit"})
                return
            requests.append(now)
            session_id = secrets.token_urlsafe(18)
            agent_token = secrets.token_urlsafe(32)
            pairing_code = _new_pairing_code()
            session = Session(session_id, agent_token, pairing_code, now)
            SESSIONS[session_id] = session
        self._send(201, {
            "session_id": session_id,
            "agent_token": agent_token,
            "pairing_code": pairing_code,
            "expires_at": int(session.expires_at),
            "expires_in_seconds": TTL_SECONDS,
        })

    def _pair(self):
        body = self._read_json()
        code = _normalize_pairing_code(body.get("pairing_code"))
        project_url = validate_project_url(body.get("project_url"))
        address = self.client_address[0]
        now = time.time()
        with STATE_LOCK:
            _prune_locked(now)
            requests = PAIR_REQUESTS.setdefault(address, [])
            requests[:] = [timestamp for timestamp in requests if timestamp > now - CREATE_WINDOW_SECONDS]
            if len(requests) >= PAIR_LIMIT_PER_ADDRESS:
                self._send(429, {"error": "Pairing rate limit reached"})
                return
            requests.append(now)
            code_hash = _digest(code)
            session = next((
                item for item in SESSIONS.values()
                if hmac.compare_digest(item.pairing_code_hash, code_hash)
            ), None)
        if session is None:
            raise PermissionError("Pairing code is invalid or expired")
        with session.condition:
            if not session.active or time.time() >= session.expires_at:
                raise KeyError("Session expired")
            if session.client_token_hash is not None:
                raise PermissionError("Session has already been paired")
            client_token = secrets.token_urlsafe(32)
            session.client_token_hash = _digest(client_token)
            session.project_url = project_url
            session.origin = project_origin(project_url)
            _renew_client_lease(session, now)
        self._send(200, {
            "session_id": session.session_id,
            "client_token": client_token,
            "project_url": session.project_url,
            "origin": session.origin,
            "lease_expires_at": int(session.expires_at),
        })

    def _run_action(self, session_id):
        body = self._read_json()
        session = self._get_session(session_id)
        self._require_auth(session, "agent")
        action = validate_action(body.get("action"))
        if action["type"] == "navigate":
            validate_navigation_path(action["path"], session.project_url)
        command_id = secrets.token_urlsafe(12)
        deadline = time.monotonic() + ACTION_TIMEOUT_SECONDS
        with session.condition:
            if not session.active or session.client_token_hash is None:
                raise PermissionError("Local browser is not connected")
            if session.in_flight is not None or session.pending:
                self._send(409, {"error": "Another browser action is still in progress"})
                return
            if not _take_action_slot(session):
                self._send(429, {"error": "Browser action rate limit reached; wait before retrying (limit is per minute)"})
                return
            session.pending.append({"command_id": command_id, "action": action})
            session.condition.notify_all()
            while session.active and command_id not in session.results and time.monotonic() < deadline:
                session.condition.wait(timeout=max(0, deadline - time.monotonic()))
            result = session.results.pop(command_id, None)
            if result is None:
                session.active = False
                session.ended_at = time.time()
                session.pending.clear()
                session.condition.notify_all()
                self._send(504, {"error": "Browser action timed out; the session was closed"})
                return
            session.in_flight = None
        self._send(200, result)

    def _complete_action(self, session_id):
        body = self._read_json()
        session = self._get_session(session_id)
        self._require_auth(session, "client")
        command_id = body.get("command_id")
        result = body.get("result")
        if not isinstance(command_id, str) or not isinstance(result, dict) or type(result.get("ok")) is not bool:
            raise ValueError("Action result is invalid")
        encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_BODY_BYTES:
            raise ValueError("Action result is too large")
        with session.condition:
            if not session.active or session.in_flight != command_id:
                raise KeyError("No matching browser action")
            _renew_client_lease(session)
            session.results[command_id] = result
            session.condition.notify_all()
        self._send(200, {"accepted": True})

    def _stop(self, session_id):
        body = self._read_json()
        if body:
            raise ValueError("Stop request does not accept options")
        session = self._get_session(session_id)
        self._require_auth(session, "either")
        with session.condition:
            session.active = False
            session.ended_at = time.time()
            session.condition.notify_all()
        self._send(200, {"stopped": True})


def serve(port=None):
    port = int(port or os.environ.get("PORT", "0"))
    if not 1024 <= port <= 65535:
        raise ValueError("PORT must be between 1024 and 65535")
    server = ThreadingHTTPServer(("0.0.0.0", port), RelayHandler)
    server.daemon_threads = True
    LOGGER.info("browser_bridge_relay_started port=%s", port)
    server.serve_forever(poll_interval=0.25)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    serve()
