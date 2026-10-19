"""Regression tests for WebUI exposure defaults and authentication gates."""

import pytest

app = pytest.importorskip("webui.app")


def test_loopback_without_auth_is_allowed():
    app._validate_webui_exposure("127.0.0.1", None)
    app._validate_webui_exposure("localhost", None)
    app._validate_webui_exposure("::1", None)


def test_non_loopback_requires_auth():
    with pytest.raises(RuntimeError, match="requires.*WEBUI_USER"):
        app._validate_webui_exposure("0.0.0.0", None)


def test_non_loopback_with_auth_is_allowed():
    app._validate_webui_exposure("0.0.0.0", ("user", "password"))


def test_public_share_requires_auth():
    with pytest.raises(RuntimeError, match="Public WebUI sharing requires"):
        app._validate_webui_exposure("127.0.0.1", None, public_share=True)
    app._validate_webui_exposure(
        "127.0.0.1", ("user", "password"), public_share=True
    )
