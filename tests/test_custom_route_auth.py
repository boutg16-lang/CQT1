# -*- coding: utf-8 -*-
"""Does HTTP basic auth actually protect routes attached after gr.launch()?

The Windows branch of webui/app.py launches Gradio with auth=... and then
attaches /virals + /export_xml_api manually via attach_extra_routes(app).
This test reproduces that sequence and probes /virals/... with no
credentials. It reports the observed status so the fix can be evidence-based.
"""
import base64
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


def test_failed_attempts_are_rate_limited(tmp_path):
    app, port = _launch_windows_style(tmp_path)
    bad = base64.b64encode(b"tester:wrong-password").decode("ascii")
    statuses = []
    for _ in range(6):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/virals/secret.txt")
        req.add_header("Authorization", f"Basic {bad}")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                statuses.append(resp.status)
        except urllib.error.HTTPError as e:
            statuses.append(e.code)
    try:
        app.server.close()
    except Exception:
        pass
    # 5 failures allowed within the window, then a 429 throttle.
    assert statuses[:5] == [401] * 5, statuses
    assert statuses[5] == 429, statuses


def _capture_launch_app(monkeypatch, webui_app):
    """Wrap demo.queue().launch so the test gets the real FastAPI app."""
    captured = {}
    original_queue = webui_app.demo.queue

    def queue_wrapper(*args, **kwargs):
        queue_obj = original_queue(*args, **kwargs)
        original_launch = queue_obj.launch

        def launch_wrapper(*la, **lk):
            app, local_url, share_url = original_launch(*la, **lk)
            captured["app"] = app
            captured["local_url"] = local_url
            return app, local_url, share_url

        queue_obj.launch = launch_wrapper
        return queue_obj

    monkeypatch.setattr(webui_app.demo, "queue", queue_wrapper)
    return captured


def _port_from_url(local_url):
    return int(str(local_url).rsplit(":", 1)[1].rstrip("/"))


def _write_viral_secret(webui_app):
    secret_path = os.path.join(webui_app.VIRALS_DIR, "secret.txt")
    with open(secret_path, "w", encoding="utf-8") as fh:
        fh.write("top-secret")
    return secret_path


def _cleanup_viral_secret(path):
    try:
        os.remove(path)
    except OSError:
        pass


def test_real_launch_windows_branch_protects_virals(monkeypatch, tmp_path):
    """End-to-end over the REAL Windows branch of _launch().

    CI runs on Linux, so the branch selection itself is forced: is_windows is
    computed inside _launch as (os.name == 'nt'), and os is the shared stdlib
    module — monkeypatching os.name flips the branch without touching the
    filesystem or the OS. demo.block_thread() is no-op'd so _launch returns
    instead of blocking forever, and webbrowser.open is stubbed because the
    branch passes inbrowser=True. webui.app is imported BEFORE os.name is
    patched so module import still sees a POSIX filesystem.
    """
    from webui import app as webui_app

    monkeypatch.setenv("VIRALCUTTER_WEBUI_USER", AUTH_USER)
    monkeypatch.setenv("VIRALCUTTER_WEBUI_PASSWORD", AUTH_PASS)
    monkeypatch.setenv("VIRALCUTTER_DISABLE_SAFETY_WATCHER", "1")
    monkeypatch.delenv("VIRALCUTTER_HOST", raising=False)
    monkeypatch.setattr("os.name", "nt")
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: None)    # os.name='nt' also flips pathlib.Path() into WindowsPath on Linux, which
    # crashes on .resolve(). Force the POSIX flavour for the duration of the
    # launch so the simulation exercises the auth wiring, not the FS flavour.
    import pathlib

    _orig_path_new = pathlib.Path.__new__

    def _posix_path_new(cls, *args, **kwargs):
        if cls is pathlib.Path:
            return pathlib.PosixPath(*args, **kwargs)
        return _orig_path_new(cls, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "__new__", _posix_path_new)

    monkeypatch.setattr(webui_app.demo, "block_thread", lambda: None)
    captured = _capture_launch_app(monkeypatch, webui_app)

    secret_path = _write_viral_secret(webui_app)
    try:
        result = webui_app._launch(["--preflight", "off"])
        assert result in (None, 0, 1), result
        app = captured["app"]
        assert app is not None, "launch() app was not captured"
        port = _port_from_url(captured["local_url"])

        status, body = _probe(app, port)
        assert status == 401, f"UNAUTHENTICATED ACCESS on real Windows branch: HTTP {status}"
        status, body = _probe(app, port, creds=(AUTH_USER, AUTH_PASS))
        assert status == 200 and "top-secret" in body, (status, body[:60])
    finally:
        _cleanup_viral_secret(secret_path)
        _shutdown(captured.get("app"))


def test_real_launch_colab_branch_protects_virals(monkeypatch):
    """End-to-end over the --colab branch (OS-independent; runs on Linux CI).

    Before the fix this branch mounted /virals with no basic-auth middleware,
    so a public --colab share would have leaked the folder. The gradio launch
    here ignores GRADIO_SERVER_PORT (no server_port kwarg), so the probe port
    comes from the launch()'s local_url.
    """
    from webui import app as webui_app

    monkeypatch.setenv("VIRALCUTTER_WEBUI_USER", AUTH_USER)
    monkeypatch.setenv("VIRALCUTTER_WEBUI_PASSWORD", AUTH_PASS)
    monkeypatch.setenv("VIRALCUTTER_DISABLE_SAFETY_WATCHER", "1")
    monkeypatch.delenv("VIRALCUTTER_HOST", raising=False)

    monkeypatch.setattr(webui_app.demo, "block_thread", lambda: None)
    captured = _capture_launch_app(monkeypatch, webui_app)

    secret_path = _write_viral_secret(webui_app)
    try:
        result = webui_app._launch(["--preflight", "off", "--colab"])
        assert result in (None, 0, 1), result
        app = captured["app"]
        assert app is not None, "launch() app was not captured"
        port = _port_from_url(captured["local_url"])

        status, body = _probe(app, port)
        assert status == 401, f"UNAUTHENTICATED ACCESS on real colab branch: HTTP {status}"
        status, body = _probe(app, port, creds=(AUTH_USER, AUTH_PASS))
        assert status == 200 and "top-secret" in body, (status, body[:60])
    finally:
        _cleanup_viral_secret(secret_path)
        _shutdown(captured.get("app"))
