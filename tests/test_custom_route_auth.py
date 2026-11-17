# -*- coding: utf-8 -*-
"""Does HTTP basic auth actually protect routes attached after gr.launch()?

The Windows branch of webui/app.py launches Gradio with auth=... and then
attaches /virals + /export_xml_api manually via attach_extra_routes(app).
This test reproduces that sequence and probes /virals/... with no
credentials. It reports the observed status so the fix can be evidence-based.
"""
import os
import socket
import sys
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

gradio = pytest.importorskip("gradio")

AUTH_USER = "tester"
AUTH_PASS = "secret-pass-1234"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _launch_windows_style(tmp_path, install_middleware=True):
    """Reproduce the Windows branch: launch with auth, then mount /virals."""
    os.environ["VIRALCUTTER_WEBUI_USER"] = AUTH_USER
    os.environ["VIRALCUTTER_WEBUI_PASSWORD"] = AUTH_PASS
    from webui import app as webui_app

    auth = webui_app._webui_auth()
    assert auth == (AUTH_USER, AUTH_PASS), auth

    demo = gradio.Blocks()
    with demo:
        gradio.Markdown("auth probe")
    app, local_url, _share = demo.queue().launch(
        share=False,
        prevent_thread_lock=True,
        server_name="127.0.0.1",
        server_port=_free_port(),
        auth=auth,
    )
    secret = tmp_path / "secret.txt"
    secret.write_text("top-secret", encoding="utf-8")
    from starlette.staticfiles import StaticFiles

    if install_middleware:
        # Same order as the fixed Windows branch: install basic auth BEFORE
        # attach_extra_routes mounts /virals and /export_xml_api.
        webui_app._install_basic_auth(app, auth)
    app.mount("/virals", StaticFiles(directory=str(tmp_path)), name="virals")
    port = str(local_url).rsplit(":", 1)[1].rstrip("/")
    return app, port


def _probe(app, port, path="/virals/secret.txt", creds=None):
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url)
    if creds:
        import base64
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{creds[0]}:{creds[1]}".encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _shutdown(app):
    try:
        app.server.close()
    except Exception:
        pass


def test_probe_virals_mount_without_credentials(tmp_path):
    app, port = _launch_windows_style(tmp_path, install_middleware=True)
    status, body = _probe(app, port)
    print(f"\nPROBE RESULT: GET /virals/secret.txt without credentials -> HTTP {status}")
    print(f"BODY: {body[:80]!r}")
    _shutdown(app)
    assert status == 401, f"UNAUTHENTICATED ACCESS CONFIRMED: HTTP {status}, body={body[:60]!r}"


def test_probe_virals_mount_with_valid_credentials(tmp_path):
    app, port = _launch_windows_style(tmp_path, install_middleware=True)
    status, body = _probe(app, port, creds=(AUTH_USER, AUTH_PASS))
    _shutdown(app)
    assert status == 200 and "top-secret" in body, (status, body[:60])


def test_probe_virals_mount_without_middleware_leaks(tmp_path):
    # Documents the pre-fix behaviour on the raw gr.launch() app: Gradio's
    # auth=... does NOT cover manually mounted FastAPI routes. This test is
    # skipped when gradio hardens this; it guards the regression rationale.
    app, port = _launch_windows_style(tmp_path, install_middleware=False)
    status, body = _probe(app, port)
    _shutdown(app)
    print(f"\nBASELINE (no middleware): HTTP {status}, body={body[:40]!r}")
    assert status != 401, "gr.launch(auth=...) started protecting mounted routes"
