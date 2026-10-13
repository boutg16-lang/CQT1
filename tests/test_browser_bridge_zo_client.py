import base64
import json
import os
import stat
import time

from tools.browser_bridge import zo_client


def _patch_state(monkeypatch, tmp_path):
    monkeypatch.setattr(zo_client, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(zo_client, "STATE_FILE", tmp_path / "state" / "session.json")
    monkeypatch.setattr(zo_client, "SCREENSHOT_FILE", tmp_path / "state" / "latest.jpg")
    monkeypatch.setattr(zo_client, "CONTROL_TOKEN_FILE", tmp_path / "state" / "control.token")


def test_old_pairing_deadline_does_not_expire_persistent_session(monkeypatch, tmp_path):
    _patch_state(monkeypatch, tmp_path)
    zo_client._save_state(
        {"session_id": "test-session", "agent_token": "private-agent-token", "expires_at": time.time() - 1}
    )

    assert zo_client._read_state()["session_id"] == "test-session"


def test_start_shows_pairing_code_but_never_agent_token(monkeypatch, tmp_path, capsys):
    _patch_state(monkeypatch, tmp_path)
    (tmp_path / "state").mkdir(mode=0o700)
    (tmp_path / "state" / "control.token").write_text("x" * 48, encoding="ascii")
    (tmp_path / "state" / "control.token").chmod(0o600)
    monkeypatch.setattr(
        zo_client,
        "create_session",
        lambda control_token: {
            "session_id": "test-session",
            "agent_token": "private-agent-token-value",
            "pairing_code": "ABCD-EFGH",
            "expires_at": time.time() + 900,
            "expires_in_seconds": 900,
        },
    )

    assert zo_client._start() == 0
    output = capsys.readouterr().out
    assert "ABCD-EFGH" in output
    assert "private-agent-token-value" not in output
    assert "The pairing code is valid for 15 minutes." in output
    assert "no fixed time limit" in output
    state = json.loads(zo_client.STATE_FILE.read_text(encoding="utf-8"))
    assert "expires_at" not in state
    assert state["agent_token"] == "private-agent-token-value"
    if os.name == "posix":
        assert stat.S_IMODE(zo_client.STATE_FILE.stat().st_mode) == 0o600
        assert stat.S_IMODE(zo_client.STATE_DIR.stat().st_mode) == 0o700


def test_screenshot_is_saved_without_printing_base64(monkeypatch, tmp_path, capsys):
    _patch_state(monkeypatch, tmp_path)
    zo_client._save_state(
        {"session_id": "test-session", "agent_token": "private-agent-token", "expires_at": time.time() + 900}
    )
    image = b"jpeg-test-content"
    monkeypatch.setattr(
        zo_client,
        "perform_action",
        lambda *args: {
            "ok": True,
            "type": "screenshot",
            "image_base64": base64.b64encode(image).decode("ascii"),
        },
    )

    assert zo_client._action({"type": "screenshot"}) == 0
    output = capsys.readouterr().out
    assert base64.b64encode(image).decode("ascii") not in output
    assert str(zo_client.SCREENSHOT_FILE) in output
    assert zo_client.SCREENSHOT_FILE.read_bytes() == image


def test_stop_removes_session_state_and_screenshot(monkeypatch, tmp_path):
    _patch_state(monkeypatch, tmp_path)
    zo_client._save_state(
        {"session_id": "test-session", "agent_token": "private-agent-token", "expires_at": time.time() + 900}
    )
    zo_client.SCREENSHOT_FILE.write_bytes(b"image")
    stopped = []
    monkeypatch.setattr(zo_client, "stop_session", lambda session_id, token: stopped.append((session_id, token)))

    assert zo_client._stop() == 0
    assert stopped == [("test-session", "private-agent-token")]
    assert not zo_client.STATE_FILE.exists()
    assert not zo_client.SCREENSHOT_FILE.exists()
