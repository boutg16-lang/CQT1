"""Tests for the launch/auth helpers extracted into webui/launch.py.

The helpers moved verbatim from webui/app.py (step 4 of the monolith
split). They are tested through the new module path; webui.app cannot be
imported in the light venv (it needs gradio/fastapi), so the re-export
identity checks use importorskip and run in CI, while a static wiring
test guards the re-export in this environment.

No network, no real server launch, no real browser. FastAPI is stubbed
via sys.modules for the middleware tests so they run without fastapi
installed; one smoke test uses the real FastAPI when available.
"""

import asyncio
import base64
import json
import os
import socket
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webui.launch as launch

APP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "webui",
    "app.py",
)
LAUNCH_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "webui",
    "launch.py",
)

MOVED = (
    "_resolve_webui_port",
    "_resolve_webui_host",
    "_validate_webui_exposure",
    "_webui_auth",
    "_install_basic_auth",
)


# ---------------------------------------------------------------- re-export
def test_reexport_identity():
    """Each moved name imported from webui.app is the same object."""
    app = pytest.importorskip("webui.app")
    for name in MOVED:
        assert getattr(app, name) is getattr(launch, name), name


def test_reexport_wiring_static():
    """app.py re-exports the moved names and no longer defines them."""
    source = open(APP_PATH, encoding="utf-8").read()
    assert "from .launch import" in source
    for name in MOVED:
        assert "def {}(".format(name) not in source, name
        assert name in source  # still referenced (re-export + call sites)
    # _allowed_dirs deliberately stayed in app.py (it reads app.VIRALS_DIR).
    assert "def _allowed_dirs():" in source


def test_launch_module_is_gradio_free():
    source = open(LAUNCH_PATH, encoding="utf-8").read()
    assert "import gradio" not in source
    assert "from gradio" not in source


# ------------------------------------------------------- _resolve_webui_port
def test_port_from_env(monkeypatch):
    monkeypatch.setenv("GRADIO_SERVER_PORT", " 7999 ")
    assert launch._resolve_webui_port() == 7999


def test_port_from_env_invalid(monkeypatch):
    monkeypatch.setenv("GRADIO_SERVER_PORT", "not-a-port")
    with pytest.raises(RuntimeError, match="must be an integer"):
        launch._resolve_webui_port()


@pytest.mark.parametrize("bad", ["0", "65536", "-1"])
def test_port_from_env_out_of_range(monkeypatch, bad):
    monkeypatch.setenv("GRADIO_SERVER_PORT", bad)
    with pytest.raises(RuntimeError, match="between 1 and 65535"):
        launch._resolve_webui_port()


def _occupy(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(1)
    return sock


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_port_skips_occupied(monkeypatch):
    monkeypatch.delenv("GRADIO_SERVER_PORT", raising=False)
    preferred = _free_port()
    held = [_occupy(preferred), _occupy(preferred + 1)]
    try:
        assert launch._resolve_webui_port(preferred, search_limit=5) == preferred + 2
    finally:
        for sock in held:
            sock.close()


def test_port_exhausted_raises(monkeypatch):
    monkeypatch.delenv("GRADIO_SERVER_PORT", raising=False)
    preferred = _free_port()
    held = [_occupy(preferred + i) for i in range(3)]
    try:
        with pytest.raises(RuntimeError, match="No free WebUI port"):
            launch._resolve_webui_port(preferred, search_limit=3)
    finally:
        for sock in held:
            sock.close()


def test_port_returns_preferred_when_free(monkeypatch):
    monkeypatch.delenv("GRADIO_SERVER_PORT", raising=False)
    preferred = _free_port()
    assert launch._resolve_webui_port(preferred, search_limit=5) == preferred


# ------------------------------------------------------- _resolve_webui_host
def test_host_defaults_to_loopback(monkeypatch, capsys):
    monkeypatch.delenv("VIRALCUTTER_HOST", raising=False)
    assert launch._resolve_webui_host() == "127.0.0.1"
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_host_loopback_is_quiet(monkeypatch, capsys, host):
    monkeypatch.setenv("VIRALCUTTER_HOST", host)
    assert launch._resolve_webui_host() == host
    assert capsys.readouterr().out == ""


def test_host_non_loopback_warns(monkeypatch, capsys):
    monkeypatch.setenv("VIRALCUTTER_HOST", "0.0.0.0")
    assert launch._resolve_webui_host() == "0.0.0.0"
    out = capsys.readouterr().out
    assert "WARNING" in out and "0.0.0.0" in out


def test_host_env_is_stripped(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_HOST", "  localhost  ")
    assert launch._resolve_webui_host() == "localhost"


# --------------------------------------------------- _validate_webui_exposure
@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_exposure_loopback_without_auth_allowed(host):
    launch._validate_webui_exposure(host, None)


def test_exposure_non_loopback_requires_auth():
    with pytest.raises(RuntimeError, match="requires.*WEBUI_USER"):
        launch._validate_webui_exposure("0.0.0.0", None)
    with pytest.raises(RuntimeError, match="requires.*WEBUI_USER"):
        launch._validate_webui_exposure("192.168.1.5", None)


def test_exposure_non_loopback_with_auth_allowed():
    launch._validate_webui_exposure("0.0.0.0", ("user", "password"))


def test_exposure_public_share_requires_auth():
    with pytest.raises(RuntimeError, match="Public WebUI sharing requires"):
        launch._validate_webui_exposure("127.0.0.1", None, public_share=True)
    launch._validate_webui_exposure(
        "127.0.0.1", ("user", "password"), public_share=True
    )


def test_exposure_public_share_without_auth_beats_loopback():
    # public_share=True on loopback still requires auth (not the bind error).
    with pytest.raises(RuntimeError, match="Public WebUI sharing"):
        launch._validate_webui_exposure("localhost", None, public_share=True)


# ---------------------------------------------------------------- _webui_auth
def test_auth_unset_is_none(monkeypatch):
    monkeypatch.delenv("VIRALCUTTER_WEBUI_USER", raising=False)
    monkeypatch.delenv("VIRALCUTTER_WEBUI_PASSWORD", raising=False)
    assert launch._webui_auth() is None


@pytest.mark.parametrize(
    "user,password",
    [("alice", ""), ("", "secret"), ("   ", "secret")],
)
def test_auth_needs_both_parts(monkeypatch, user, password):
    monkeypatch.setenv("VIRALCUTTER_WEBUI_USER", user)
    monkeypatch.setenv("VIRALCUTTER_WEBUI_PASSWORD", password)
    assert launch._webui_auth() is None


def test_auth_returns_tuple(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_WEBUI_USER", "  alice  ")
    monkeypatch.setenv("VIRALCUTTER_WEBUI_PASSWORD", "s3cr3t")
    assert launch._webui_auth() == ("alice", "s3cr3t")


def test_auth_password_kept_verbatim(monkeypatch):
    # Verified behaviour: the user is stripped, the password is not.
    monkeypatch.setenv("VIRALCUTTER_WEBUI_USER", "alice")
    monkeypatch.setenv("VIRALCUTTER_WEBUI_PASSWORD", "  padded  ")
    assert launch._webui_auth() == ("alice", "  padded  ")


# --------------------------------------------------------- _install_basic_auth
class _FakeJSONResponse:
    """Minimal stand-in for fastapi.responses.JSONResponse (ASGI callable)."""

    def __init__(self, content, status_code=200, headers=None):
        self.content = content
        self.status_code = status_code
        self.headers = dict(headers or {})

    async def __call__(self, scope, receive, send):
        headers = [
            (k.encode("latin-1"), v.encode("latin-1")) for k, v in self.headers.items()
        ]
        await send(
            {
                "type": "http.response.start",
                "status": self.status_code,
                "headers": headers,
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": json.dumps(self.content).encode("utf-8"),
            }
        )


@pytest.fixture
def stub_fastapi(monkeypatch):
    """Install fake fastapi/fastapi.responses modules in sys.modules."""
    fastapi = types.ModuleType("fastapi")
    responses = types.ModuleType("fastapi.responses")
    responses.JSONResponse = _FakeJSONResponse
    fastapi.responses = responses
    monkeypatch.setitem(sys.modules, "fastapi", fastapi)
    monkeypatch.setitem(sys.modules, "fastapi.responses", responses)
    return fastapi


class _FakeApp:
    """Records add_middleware calls; inner app records invocations."""

    def __init__(self, fail_first_add=False):
        self.added = []
        self.attempts = 0
        self.fail_first_add = fail_first_add
        self.calls = 0

    def add_middleware(self, middleware_cls):
        self.attempts += 1
        if self.fail_first_add and self.attempts == 1:
            raise RuntimeError("already started")
        self.added.append(middleware_cls)

    async def __call__(self, scope, receive, send):
        self.calls += 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _run_middleware(middleware, scope, headers=None):
    """Drive one ASGI call; returns (status, headers, body, inner_calls)."""
    sent = []
    inner = {"calls": 0}

    async def inner_app(scope, receive, send):
        inner["calls"] += 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def go():
        app = middleware(inner_app)
        raw_headers = [
            (k.encode("latin-1"), v.encode("latin-1"))
            for k, v in (headers or {}).items()
        ]
        call_scope = dict(scope)
        call_scope.setdefault("type", "http")
        call_scope["headers"] = raw_headers
        call_scope.setdefault("client", ("10.0.0.1", 1234))

        async def send(message):
            sent.append(message)

        await app(call_scope, lambda: None, send)

    asyncio.run(go())
    status = next(
        (m["status"] for m in sent if m["type"] == "http.response.start"), None
    )
    resp_headers = next(
        (m.get("headers", []) for m in sent if m["type"] == "http.response.start"), []
    )
    body = b"".join(
        m.get("body", b"") for m in sent if m["type"] == "http.response.body"
    )
    return (
        status,
        {k.decode(): v.decode() for k, v in resp_headers},
        body,
        inner["calls"],
    )


@pytest.fixture
def middleware(stub_fastapi):
    app = _FakeApp()
    launch._install_basic_auth(app, ("alice", "s3cr3t"))
    assert len(app.added) == 1
    return app.added[0]


def _basic(user, password):
    token = base64.b64encode("{}:{}".format(user, password).encode()).decode()
    return {"Authorization": "Basic " + token}


def test_middleware_allows_correct_credentials(middleware):
    status, _, body, calls = _run_middleware(middleware, {}, _basic("alice", "s3cr3t"))
    assert (status, body, calls) == (200, b"ok", 1)


def test_middleware_rejects_missing_credentials(middleware):
    status, headers, body, calls = _run_middleware(middleware, {})
    assert status == 401
    assert headers.get("WWW-Authenticate") == 'Basic realm="OUSSAMA Cutter"'
    assert json.loads(body) == {"error": "unauthorized"}
    assert calls == 0


@pytest.mark.parametrize("user,password", [("alice", "wrong"), ("mallory", "s3cr3t")])
def test_middleware_rejects_wrong_credentials(middleware, user, password):
    status, _, _, calls = _run_middleware(middleware, {}, _basic(user, password))
    assert (status, calls) == (401, 0)


def test_middleware_rejects_malformed_header(middleware):
    status, _, _, calls = _run_middleware(
        middleware, {}, {"Authorization": "Basic !!!not-base64!!!"}
    )
    assert (status, calls) == (401, 0)
    status, _, _, calls = _run_middleware(
        middleware, {}, {"Authorization": "Bearer abc"}
    )
    assert (status, calls) == (401, 0)


def test_middleware_header_name_case_insensitive(middleware):
    status, _, _, calls = _run_middleware(
        middleware, {}, {"AUTHORIZATION": _basic("alice", "s3cr3t")["Authorization"]}
    )
    assert (status, calls) == (200, 1)


def test_middleware_passes_non_http_scope(middleware):
    sent = []

    async def go():
        async def inner_app(scope, receive, send):
            sent.append("called")

        app = middleware(inner_app)
        await app({"type": "lifespan"}, lambda: None, lambda m: None)

    asyncio.run(go())
    assert sent == ["called"]


def test_middleware_throttles_after_five_failures(middleware):
    for _ in range(5):
        status, _, _, _ = _run_middleware(middleware, {})
        assert status == 401
    status, headers, body, calls = _run_middleware(middleware, {})
    assert status == 429
    assert headers.get("Retry-After") == "60"
    assert "too many failed attempts" in json.loads(body)["error"]
    assert calls == 0


def test_install_basic_auth_retries_after_started_server(stub_fastapi):
    app = _FakeApp(fail_first_add=True)
    launch._install_basic_auth(app, ("alice", "s3cr3t"))
    assert app.middleware_stack is None
    assert len(app.added) == 1


def test_install_basic_auth_with_real_fastapi():
    fastapi = pytest.importorskip("fastapi")
    real_app = fastapi.FastAPI()
    launch._install_basic_auth(real_app, ("alice", "s3cr3t"))
    assert any(
        getattr(m, "cls", None) is not None and m.cls.__name__ == "_BasicAuthMiddleware"
        for m in real_app.user_middleware
    )
