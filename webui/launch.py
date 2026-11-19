"""Launch/auth helpers for the OUSSAMA Cutter WebUI.

UI-free helpers used by :func:`webui.app._launch`: resolving the bind host
and port, validating public/network exposure (a security gate — moved
verbatim, never relaxed), reading optional HTTP basic-auth credentials from
the environment, and installing the basic-auth ASGI middleware on a
FastAPI/Starlette app. Only the standard library is imported at module
level; FastAPI is imported lazily inside :func:`_install_basic_auth` so
this module stays importable without the WebUI stack.
"""

import os
import socket


def _resolve_webui_port(preferred=7860, search_limit=20):
    """Use the requested port or choose the first free local WebUI port."""
    configured = os.getenv("GRADIO_SERVER_PORT", "").strip()
    if configured:
        try:
            port = int(configured)
        except ValueError:
            raise RuntimeError("GRADIO_SERVER_PORT must be an integer") from None
        if not 1 <= port <= 65535:
            raise RuntimeError("GRADIO_SERVER_PORT must be between 1 and 65535")
        return port

    for port in range(preferred, preferred + search_limit):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("No free WebUI port found near {}".format(preferred))


def _resolve_webui_host():
    """Host to bind. Defaults to loopback; VIRALCUTTER_HOST overrides it.

    Binding to 0.0.0.0 exposes the WebUI — and any file it can serve — to the
    whole network. Only do that on a network you trust.
    """
    host = os.environ.get("VIRALCUTTER_HOST", "").strip() or "127.0.0.1"
    loopback = host in ("127.0.0.1", "::1", "localhost")
    if not loopback:
        print("[webui] WARNING: binding to {} — the WebUI is reachable from the "
              "network. Set VIRALCUTTER_HOST=127.0.0.1 to bind loopback only."
              .format(host))
    return host


def _validate_webui_exposure(host, auth=None, *, public_share=False):
    """Reject network/public exposure unless HTTP auth is configured.

    The WebUI can serve local project output and trigger processing actions;
    a warning is not sufficient protection when a user accidentally binds it
    to a LAN address or creates a public Gradio share link.
    """
    loopback = host in ("127.0.0.1", "::1", "localhost")
    if (not loopback or public_share) and not auth:
        if public_share:
            raise RuntimeError(
                "Public WebUI sharing requires VIRALCUTTER_WEBUI_USER and "
                "VIRALCUTTER_WEBUI_PASSWORD."
            )
        raise RuntimeError(
            "Binding the WebUI outside loopback requires "
            "VIRALCUTTER_WEBUI_USER and VIRALCUTTER_WEBUI_PASSWORD."
        )


def _webui_auth():
    """Optional HTTP basic auth from VIRALCUTTER_WEBUI_USER / VIRALCUTTER_WEBUI_PASSWORD.

    Returns a (user, password) tuple or None when not configured.
    """
    user = os.environ.get("VIRALCUTTER_WEBUI_USER", "").strip()
    password = os.environ.get("VIRALCUTTER_WEBUI_PASSWORD", "")
    if user and password:
        return (user, password)
    return None


def _install_basic_auth(fastapi_app, auth):
    """Install the HTTP basic-auth middleware on a FastAPI/Starlette app.

    Used by BOTH launch branches: Gradio's auth=... only protects the routes
    Gradio itself serves — routes mounted manually afterwards (/virals,
    /export_xml_api) are NOT covered, as verified by
    tests/test_custom_route_auth.py (unauthenticated GET returned HTTP 200
    before this middleware existed). Install it BEFORE attach_extra_routes.
    """
    import base64 as _b64
    import hmac
    import threading
    import time

    from fastapi.responses import JSONResponse

    _auth_user, _auth_pass = auth
    _auth_user_bytes = _auth_user.encode("utf-8")
    _auth_pass_bytes = _auth_pass.encode("utf-8")

    # In-process brute-force throttle: MAX_AUTH_FAILURES failures per IP
    # within AUTH_FAILURE_WINDOW seconds are answered with 429.
    MAX_AUTH_FAILURES = 5
    AUTH_FAILURE_WINDOW = 60.0
    _auth_failures = {}
    _auth_failures_lock = threading.Lock()

    class _BasicAuthMiddleware:
        """Pure ASGI middleware so it can also be attached post-startup
        (the Windows branch launches the server with prevent_thread_lock
        before attach_extra_routes runs)."""

        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return
            auth_header = ""
            for key, value in scope.get("headers", []):
                if key.decode("latin-1").lower() == "authorization":
                    auth_header = value.decode("latin-1")
                    break
            client_ip = (scope.get("client") or ("unknown", 0))[0] or "unknown"
            now = time.monotonic()
            with _auth_failures_lock:
                window = [t for t in _auth_failures.get(client_ip, []) if now - t < AUTH_FAILURE_WINDOW]
                if len(window) >= MAX_AUTH_FAILURES:
                    _auth_failures[client_ip] = window
                    throttled = True
                else:
                    _auth_failures[client_ip] = window
                    throttled = False
            if throttled:
                response = JSONResponse(
                    {"error": "too many failed attempts; try again later"},
                    status_code=429,
                    headers={"Retry-After": str(int(AUTH_FAILURE_WINDOW))},
                )
                await response(scope, receive, send)
                return

            ok = False
            if auth_header.lower().startswith("basic "):
                try:
                    decoded = _b64.b64decode(auth_header[6:]).decode("utf-8", "replace")
                    u, _, pw = decoded.partition(":")
                    # Constant-time comparison on both fields.
                    ok = (hmac.compare_digest(u.encode("utf-8"), _auth_user_bytes)
                          and hmac.compare_digest(pw.encode("utf-8"), _auth_pass_bytes))
                except Exception:
                    ok = False
            if not ok:
                with _auth_failures_lock:
                    _auth_failures.setdefault(client_ip, []).append(now)
                response = JSONResponse(
                    {"error": "unauthorized"},
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="OUSSAMA Cutter"'},
                )
                await response(scope, receive, send)
                return
            await self.app(scope, receive, send)

    try:
        fastapi_app.add_middleware(_BasicAuthMiddleware)
    except RuntimeError:
        # gr.launch(prevent_thread_lock=True) has already started serving;
        # drop the built stack so the next request rebuilds it with auth.
        fastapi_app.middleware_stack = None
        fastapi_app.add_middleware(_BasicAuthMiddleware)
