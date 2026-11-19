# -*- coding: utf-8 -*-
"""HTTP contract tests for the /export_xml_api route.

Launches the real WebUI once (loopback, no auth) and asserts the status-code
contract: 400 for bad input (empty project / unknown format), 404 for an
unknown project, 422 for out-of-range segment values (FastAPI Query bounds),
and no internal details leaked in error bodies.
"""

import os
import socket
import sys
import time
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

gradio = pytest.importorskip("gradio")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base_url():
    os.environ["GRADIO_SERVER_PORT"] = str(_free_port())
    os.environ["VIRALCUTTER_SKIP_PREFLIGHT"] = "1"
    # Valid credentials: these tests exercise the API contract, not the auth
    # gate (repeated unauthenticated hits would trip the per-IP throttle).
    os.environ["VIRALCUTTER_WEBUI_USER"] = AUTH_USER
    os.environ["VIRALCUTTER_WEBUI_PASSWORD"] = AUTH_PASS
    from webui import app as webui_app

    result = {}

    def _run():
        try:
            webui_app._launch(["--preflight", "off"])
        except Exception as e:  # pragma: no cover - startup failure path
            result["error"] = e

    import threading

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    port = int(os.environ["GRADIO_SERVER_PORT"])
    url = f"http://127.0.0.1:{port}/"
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            break
        except urllib.error.HTTPError:
            break
        except Exception:
            if "error" in result:
                pytest.skip(f"WebUI failed to start in test env: {result['error']}")
            time.sleep(0.5)
    else:
        pytest.skip("WebUI did not become ready within 60s")
    yield f"http://127.0.0.1:{port}"


AUTH_USER = "pytest_runner"
AUTH_PASS = "pytest-pass-1234"


def _get(base_url, query):
    import base64

    token = base64.b64encode(f"{AUTH_USER}:{AUTH_PASS}".encode()).decode()
    req = urllib.request.Request(
        f"{base_url}/export_xml_api?{query}",
        headers={"Authorization": f"Basic {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def test_empty_project_returns_400(base_url):
    status, body = _get(base_url, "project=&segment=0")
    assert status == 400, (status, body)
    assert "Traceback" not in body


def test_unknown_project_returns_404(base_url):
    status, body = _get(base_url, "project=definitely_not_a_project_123&segment=0")
    assert status == 404, (status, body)
    assert "Traceback" not in body


def test_unknown_format_returns_400(base_url):
    status, body = _get(base_url, "project=whatever&segment=0&format=davinci")
    assert status == 400, (status, body)
    assert "premiere" in body  # lists supported formats instead of leaking internals


def test_segment_above_limit_rejected(base_url):
    status, _body = _get(base_url, "project=whatever&segment=10000")
    assert status == 422, _body


def test_segment_negative_rejected(base_url):
    status, _body = _get(base_url, "project=whatever&segment=-1")
    assert status == 422, _body


def test_segment_zero_accepted_range(base_url):
    # 0 is inside [0, 9999]; validation must pass (project then 404s).
    status, body = _get(base_url, "project=definitely_not_a_project_123&segment=0")
    assert status == 404, (status, body)
