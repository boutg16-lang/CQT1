import pytest

from tools.browser_bridge import agent, client, health, policy


def test_browser_bridge_health_route_identifies_the_cat_webui():
    from fastapi import FastAPI

    app = FastAPI()
    health.install_health_route(app)
    route = next(route for route in app.routes if route.path == health.HEALTH_PATH)

    assert route.include_in_schema is False
    assert route.endpoint() == {"service": "oussama-cutter", "ok": True}



def test_project_preflight_auto_detects_cat_when_default_port_is_busy(monkeypatch):
    calls = []

    class Response:
        def __init__(self, status, body):
            self.status = status
            self.body = body

        def read(self, limit):
            return self.body

    class FakeConnection:
        def __init__(self, host, port, timeout):
            self.port = port

        def request(self, method, path, headers):
            calls.append((self.port, path))

        def getresponse(self):
            if self.port == 7862:
                return Response(200, b'{"service":"oussama-cutter","ok":true}')
            return Response(404, b'{"detail":"Not Found"}')

        def close(self):
            pass

    monkeypatch.setattr(client.http.client, "HTTPConnection", FakeConnection)

    assert client.probe_project_url("http://127.0.0.1:7860") == "http://127.0.0.1:7862"
    assert [port for port, _ in calls] == [7860, 7860, 7861, 7861, 7862]


def test_project_preflight_finds_older_cat_page_by_title(monkeypatch):
    calls = []

    class Response:
        def __init__(self, status, body):
            self.status = status
            self.body = body

        def read(self, limit):
            return self.body

    class FakeConnection:
        def __init__(self, host, port, timeout):
            self.port = port
            self.path = ""

        def request(self, method, path, headers):
            self.path = path
            calls.append((self.port, path))

        def getresponse(self):
            if self.port == 7861 and self.path == "/":
                return Response(200, b"<html><title>OUSSAMA Cutter</title></html>")
            return Response(404, b"Not Found")

        def close(self):
            pass

    monkeypatch.setattr(client.http.client, "HTTPConnection", FakeConnection)

    assert client.probe_project_url("http://127.0.0.1:7860") == "http://127.0.0.1:7861"
    assert (7861, "/__cat_browser_bridge/health") in calls
    assert (7861, "/") in calls


def test_project_preflight_rejects_non_cat_local_services(monkeypatch):
    class Response:
        status = 200

        def read(self, limit):
            return b'{"service":"another-local-service","ok":true}'

    class FakeConnection:
        def __init__(self, host, port, timeout):
            pass

        def request(self, method, path, headers):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(client.http.client, "HTTPConnection", FakeConnection)

    with pytest.raises(client.BridgeError) as error:
        client.probe_project_url("http://127.0.0.1:9000")

    assert "127.0.0.1:9000" in str(error.value)
    assert client._t("The pairing code has not been used.") in str(error.value)


def test_project_preflight_explains_unreachable_local_app_without_consuming_code(monkeypatch):
    class FakeConnection:
        def __init__(self, host, port, timeout):
            pass

        def request(self, method, path, headers):
            raise ConnectionRefusedError("connection refused")

        def close(self):
            pass

    monkeypatch.setattr(client.http.client, "HTTPConnection", FakeConnection)

    with pytest.raises(client.BridgeError) as error:
        client.probe_project_url("http://127.0.0.1:7860")

    assert "127.0.0.1:7860" in str(error.value)
    assert client._t("The pairing code has not been used.") in str(error.value)


def test_browser_preflight_failure_does_not_submit_pairing_code(monkeypatch):
    import queue
    import threading

    calls = []
    monkeypatch.setattr(
        client,
        "probe_project_url",
        lambda _: (_ for _ in ()).throw(client.BridgeError("local app is unavailable")),
    )
    monkeypatch.setattr(client, "request", lambda *args, **kwargs: calls.append(args))
    events = queue.Queue()

    client.run_browser_session(
        client.DEFAULT_RELAY_URL,
        "one-time-pairing-code",
        "http://127.0.0.1:7860",
        events,
        threading.Event(),
        {},
    )

    assert calls == []
    assert events.get_nowait()[0] == "error"
    assert events.get_nowait()[0] == "stopped"


def test_agent_cannot_open_the_native_file_picker():
    class Locator:
        clicked = False

        def count(self):
            return 1

        def is_visible(self):
            return True

        def is_enabled(self):
            return True

        def get_attribute(self, name):
            return "file" if name == "type" else None

        def click(self, **kwargs):
            self.clicked = True

    class Page:
        url = "http://127.0.0.1:7860/"

        def __init__(self):
            self.control = Locator()

        def get_by_role(self, *args, **kwargs):
            return self.control

    class Context:
        def __init__(self, page):
            self.pages = [page]

    page = Page()
    with pytest.raises(policy.PolicyError, match="File selection"):
        client.perform_action(
            page,
            Context(page),
            "http://127.0.0.1:7860",
            {"type": "click", "role": "button", "name": "Browse"},
        )
    assert not page.control.clicked


def test_action_result_redacts_secret_text_before_sharing():
    text = "API_KEY=privatevalue Bearer abcdefghijklmnop"
    result = policy.redact_sensitive_text(text)
    assert "privatevalue" not in result
    assert "abcdefghijklmnop" not in result

def test_relay_url_is_pinned_to_the_owned_https_service():
    assert agent.validate_relay_url(agent.DEFAULT_RELAY_URL) == agent.DEFAULT_RELAY_URL
    for value in (
        "http://cat-browser-bridge-2nnhh.zocomputer.io",
        "https://attacker.example",
        "https://cat-browser-bridge-2nnhh.zocomputer.io/other-path",
        "https://user@cat-browser-bridge-2nnhh.zocomputer.io",
    ):
        with pytest.raises(agent.BridgeError):
            agent.validate_relay_url(value)


def test_secret_fields_are_never_controlled():
    class Locator:
        filled = False

        def count(self):
            return 1

        def is_visible(self):
            return True

        def is_enabled(self):
            return True

        def get_attribute(self, name):
            return {"type": "text", "name": "openai_api_key"}.get(name)

        def fill(self, value, **kwargs):
            self.filled = True

    class Page:
        url = "http://127.0.0.1:7860/"

        def __init__(self):
            self.control = Locator()

        def get_by_label(self, *args, **kwargs):
            return self.control

    class Context:
        def __init__(self, page):
            self.pages = [page]

    page = Page()
    with pytest.raises(policy.PolicyError, match="Secret.*fields"):
        client.perform_action(
            page,
            Context(page),
            "http://127.0.0.1:7860",
            {"type": "fill", "name": "API key", "value": "test-value"},
        )
    assert not page.control.filled
