import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from tools.browser_bridge import client, credentials, policy, relay


def _http(relay_url, path, method="GET", token=None, body=None, timeout=35):
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(relay_url + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


CONTROL_TOKEN = "test-control-token-0123456789abcdef0123456789abcdef"


@pytest.fixture
def relay_url(tmp_path, monkeypatch):
    token_file = tmp_path / "control.token"
    token_file.write_text(CONTROL_TOKEN, encoding="ascii")
    token_file.chmod(0o600)
    monkeypatch.setenv(relay.CONTROL_TOKEN_FILE_ENV, str(token_file))
    with relay.STATE_LOCK:
        relay.SESSIONS.clear()
        relay.CREATE_REQUESTS.clear()
        relay.PAIR_REQUESTS.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), relay.RelayHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:{}".format(server.server_address[1])
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    with relay.STATE_LOCK:
        relay.SESSIONS.clear()
        relay.CREATE_REQUESTS.clear()
        relay.PAIR_REQUESTS.clear()


def _create_and_pair(relay_url):
    status, session = _http(
        relay_url,
        "/v1/sessions",
        method="POST",
        token=CONTROL_TOKEN,
        body={},
    )
    assert status == 201
    status, paired = _http(
        relay_url,
        "/v1/pair",
        method="POST",
        body={"pairing_code": session["pairing_code"], "project_url": "http://127.0.0.1:7860"},
    )
    assert status == 200
    return session, paired


def test_session_creation_requires_control_token(relay_url):
    status, _ = _http(relay_url, "/v1/sessions", method="POST", body={})
    assert status == 401
    status, _ = _http(
        relay_url,
        "/v1/sessions",
        method="POST",
        token="wrong-control-token",
        body={},
    )
    assert status == 401
    status, created = _http(
        relay_url,
        "/v1/sessions",
        method="POST",
        token=CONTROL_TOKEN,
        body={},
    )
    assert status == 201
    assert created["session_id"]


def test_relay_fails_closed_when_control_token_is_not_configured(relay_url, monkeypatch):
    monkeypatch.delenv(relay.CONTROL_TOKEN_FILE_ENV)
    status, _ = _http(
        relay_url,
        "/v1/sessions",
        method="POST",
        token=CONTROL_TOKEN,
        body={},
    )
    assert status == 503


def test_control_token_file_must_be_private_and_valid(tmp_path):
    path = tmp_path / "control.token"
    path.write_text(CONTROL_TOKEN, encoding="ascii")
    path.chmod(0o600)
    assert credentials.read_control_token(path) == CONTROL_TOKEN
    with pytest.raises(credentials.CredentialError, match="not configured"):
        credentials.read_control_token(None)
    if os.name == "posix":
        path.chmod(0o644)
        with pytest.raises(credentials.CredentialError, match="permissions"):
            credentials.read_control_token(path)


def test_session_creation_fails_closed_without_control_file(relay_url, monkeypatch):
    monkeypatch.delenv(relay.CONTROL_TOKEN_FILE_ENV, raising=False)
    status, result = _http(
        relay_url,
        "/v1/sessions",
        method="POST",
        token=CONTROL_TOKEN,
        body={},
    )
    assert status == 503
    assert "authentication is unavailable" in result["error"]


def test_agent_relay_url_is_pinned_to_the_owner_service():
    from tools.browser_bridge import agent

    assert agent.validate_relay_url(agent.DEFAULT_RELAY_URL) == agent.DEFAULT_RELAY_URL
    for value in (
        "http://cat-browser-bridge-2nnhh.zocomputer.io",
        "https://example.com",
        agent.DEFAULT_RELAY_URL + "/other",
        agent.DEFAULT_RELAY_URL + "?token=secret",
    ):
        with pytest.raises(agent.BridgeError):
            agent.validate_relay_url(value)


def test_project_url_accepts_only_loopback_http():
    assert policy.validate_project_url("http://localhost:7860/") == "http://localhost:7860"
    assert policy.validate_project_url("http://[::1]:7860") == "http://[::1]:7860"
    for value in (
        "https://127.0.0.1:7860",
        "http://example.com:7860",
        "http://127.0.0.1:0",
        "http://user@127.0.0.1:7860",
        "http://127.0.0.1:7860/app",
        "http://127.0.0.1:7860/?token=secret",
    ):
        with pytest.raises(policy.PolicyError):
            policy.validate_project_url(value)


def test_navigation_and_actions_are_confined_to_local_project():
    base = "http://127.0.0.1:7860"
    assert policy.validate_navigation_path("/settings", base) == base + "/settings"
    for path in ("//example.com", "https://example.com", "/../secret", "/x#fragment", "/\\evil"):
        with pytest.raises(policy.PolicyError):
            policy.validate_navigation_path(path, base)
    with pytest.raises(policy.PolicyError):
        policy.validate_action({"type": "evaluate", "script": "fetch('https://example.com')"})
    with pytest.raises(policy.PolicyError):
        policy.validate_action({"type": "fill", "name": "API token", "value": "secret"})
    assert policy.validate_action({"type": "fill", "name": "Project title", "value": "Test"})["value"] == "Test"


def test_secret_strings_are_redacted_from_page_output():
    output = policy.redact_sensitive_text(
        "API_KEY=privatevalue Bearer abcdefghijklmnop ghp_abcdefghijklmnopqrstuvwxyz Token=private_token_value Secret:private_secret_value"
    )
    assert "privatevalue" not in output
    assert "abcdefghijklmnop" not in output
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in output
    assert "private_token_value" not in output
    assert "private_secret_value" not in output


def test_high_impact_actions_require_a_local_manual_click():
    generate = {"type": "click", "role": "button", "name": "Generate clips"}
    submit = {"type": "press", "name": "Project title", "key": "Enter"}
    assert policy.requires_manual_approval(generate)
    assert policy.requires_manual_approval(submit)
    for name in ("Create project", "Confirm upload", "إنشاء مشروع"):
        assert policy.requires_manual_approval(
            {"type": "click", "role": "button", "name": name}
        )
    with pytest.raises(policy.PolicyError, match="manually"):
        client.perform_action(None, None, "http://127.0.0.1:7860", generate)
    with pytest.raises(policy.PolicyError, match="manually"):
        client.perform_action(None, None, "http://127.0.0.1:7860", submit)
    assert not policy.requires_manual_approval(
        {"type": "click", "role": "button", "name": "Open settings"}
    )


def test_browser_request_filter_blocks_other_origins():
    origin = "http://127.0.0.1:7860"
    assert client._safe_request_url(origin + "/assets/app.js", origin)
    assert client._safe_request_url("data:image/png;base64,AA==", origin)
    assert client._safe_request_url("about:blank", origin)
    assert client._safe_request_url("blob:" + origin + "/blob-id", origin)
    assert not client._safe_request_url("about:config", origin)
    assert not client._safe_request_url("blob:https://example.com/blob-id", origin)
    assert not client._safe_request_url("http://127.0.0.1:7861/", origin)
    assert not client._safe_request_url("https://example.com/", origin)


def test_external_links_are_rejected_before_clicking():
    class Locator:
        clicked = False

        def count(self):
            return 1

        def is_visible(self):
            return True

        def is_enabled(self):
            return True

        def get_attribute(self, name):
            return "https://example.com/" if name == "href" else None

        def click(self, **kwargs):
            self.clicked = True

    class Page:
        url = "http://127.0.0.1:7860/"

        def __init__(self):
            self.locator = Locator()

        def get_by_role(self, *args, **kwargs):
            return self.locator

        def evaluate(self, script, href):
            return href

    class Context:
        def __init__(self, page):
            self.pages = [page]

    page = Page()
    with pytest.raises(policy.PolicyError, match="outside the local project"):
        client.perform_action(
            page,
            Context(page),
            "http://127.0.0.1:7860",
            {"type": "click", "role": "link", "name": "External"},
        )
    assert not page.locator.clicked


def test_session_requires_pairing_and_bearer_auth(relay_url):
    session, paired = _create_and_pair(relay_url)
    status, _ = _http(relay_url, "/v1/sessions/{}".format(session["session_id"]))
    assert status == 401
    status, state = _http(
        relay_url,
        "/v1/sessions/{}".format(session["session_id"]),
        token=session["agent_token"],
    )
    assert status == 200
    assert state["paired"] is True
    assert state["origin"] == "http://127.0.0.1:7860"
    assert "lease_expires_at" in state
    assert "expires_at" not in state
    status, _ = _http(
        relay_url,
        "/v1/sessions/{}".format(session["session_id"]),
        token="wrong-token",
    )
    assert status == 401
    status, _ = _http(
        relay_url,
        "/v1/pair",
        method="POST",
        body={"pairing_code": session["pairing_code"], "project_url": "http://127.0.0.1:7860"},
    )
    assert status == 401
    assert paired["client_token"]


def test_pairing_code_does_not_accept_remote_project_url(relay_url):
    status, session = _http(
        relay_url,
        "/v1/sessions",
        method="POST",
        token=CONTROL_TOKEN,
        body={},
    )
    assert status == 201
    status, _ = _http(
        relay_url,
        "/v1/pair",
        method="POST",
        body={"pairing_code": session["pairing_code"], "project_url": "http://example.com:7860"},
    )
    assert status == 400


def test_action_round_trip_returns_only_matching_browser_result(relay_url):
    session, paired = _create_and_pair(relay_url)
    action_result = {}

    def run_agent_action():
        action_result["response"] = _http(
            relay_url,
            "/v1/sessions/{}/actions".format(session["session_id"]),
            method="POST",
            token=session["agent_token"],
            body={"action": {"type": "snapshot"}},
        )

    action_thread = threading.Thread(target=run_agent_action, daemon=True)
    action_thread.start()
    command_status, command_payload = _http(
        relay_url,
        "/v1/sessions/{}/next".format(session["session_id"]),
        token=paired["client_token"],
        timeout=25,
    )
    assert command_status == 200
    command = command_payload["command"]
    assert command["action"] == {"type": "snapshot"}
    result_status, _ = _http(
        relay_url,
        "/v1/sessions/{}/results".format(session["session_id"]),
        method="POST",
        token=paired["client_token"],
        body={"command_id": command["command_id"], "result": {"ok": True, "text": "CAT ready"}},
    )
    assert result_status == 200
    action_thread.join(timeout=3)
    assert not action_thread.is_alive()
    assert action_result["response"] == (200, {"ok": True, "text": "CAT ready"})


def test_pairing_rate_limit_and_action_rate_limits_are_bounded(relay_url):
    assert relay.TTL_SECONDS <= 900
    assert relay.CLIENT_LEASE_SECONDS <= 300
    assert relay.MAX_ACTIONS_PER_MINUTE <= 60
    codes = []
    for _ in range(relay.CREATE_LIMIT_PER_ADDRESS):
        status, created = _http(
            relay_url,
            "/v1/sessions",
            method="POST",
            token=CONTROL_TOKEN,
            body={},
        )
        assert status == 201
        codes.append(created["pairing_code"])
    status, _ = _http(
        relay_url,
        "/v1/sessions",
        method="POST",
        token=CONTROL_TOKEN,
        body={},
    )
    assert status == 429
    assert len(set(codes)) == len(codes)


def test_client_poll_renews_lease_without_a_fixed_session_deadline(relay_url, monkeypatch):
    monkeypatch.setattr(relay, "LONG_POLL_SECONDS", 0)
    session, paired = _create_and_pair(relay_url)
    state = relay.SESSIONS[session["session_id"]]
    initial_expiry = state.expires_at

    status, result = _http(
        relay_url,
        "/v1/sessions/{}/next".format(session["session_id"]),
        token=paired["client_token"],
    )

    assert status == 200
    assert result == {"command": None, "stop": False}
    assert state.expires_at > initial_expiry
    assert state.expires_at <= time.time() + relay.CLIENT_LEASE_SECONDS


def test_session_expires_if_client_heartbeat_stops(relay_url):
    session, _ = _create_and_pair(relay_url)
    state = relay.SESSIONS[session["session_id"]]

    with relay.STATE_LOCK:
        relay._prune_locked(state.expires_at + 1)

    assert not state.active
    assert session["session_id"] not in relay.SESSIONS


def test_action_rate_limit_slides_and_does_not_end_session(relay_url, monkeypatch):
    monkeypatch.setattr(relay, "MAX_ACTIONS_PER_MINUTE", 1)
    session, _ = _create_and_pair(relay_url)
    state = relay.SESSIONS[session["session_id"]]

    assert relay._take_action_slot(state, 1000)
    assert not relay._take_action_slot(state, 1030)
    assert state.active
    assert relay._take_action_slot(state, 1061)
    assert state.active


def test_action_rate_limit_returns_429_without_stopping_browser(relay_url, monkeypatch):
    monkeypatch.setattr(relay, "MAX_ACTIONS_PER_MINUTE", 0)
    session, _ = _create_and_pair(relay_url)

    status, result = _http(
        relay_url,
        "/v1/sessions/{}/actions".format(session["session_id"]),
        method="POST",
        token=session["agent_token"],
        body={"action": {"type": "snapshot"}},
    )

    assert status == 429
    assert "per minute" in result["error"]
    assert relay.SESSIONS[session["session_id"]].active


def test_timed_out_action_closes_session_and_drops_pending_command(relay_url, monkeypatch):
    monkeypatch.setattr(relay, "ACTION_TIMEOUT_SECONDS", 0.05)
    session, paired = _create_and_pair(relay_url)
    status, result = _http(
        relay_url,
        "/v1/sessions/{}/actions".format(session["session_id"]),
        method="POST",
        token=session["agent_token"],
        body={"action": {"type": "snapshot"}},
    )
    assert status == 504
    assert "session was closed" in result["error"]
    assert not relay.SESSIONS[session["session_id"]].pending
    status, next_result = _http(
        relay_url,
        "/v1/sessions/{}/next".format(session["session_id"]),
        token=paired["client_token"],
    )
    assert status == 200
    assert next_result == {"command": None, "stop": True}
