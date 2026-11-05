import sys

from scripts import secure_config


def test_set_prompts_and_never_echoes_the_key(monkeypatch, capsys):
    secret = "AIza_cli_secret_not_in_args_123456"
    calls = []
    monkeypatch.setenv("VIRALCUTTER_CONFIG_PASSPHRASE", "unit-test-passphrase")
    monkeypatch.setattr("getpass.getpass", lambda prompt: secret)
    monkeypatch.setattr(secure_config, "set_key", lambda key, **kwargs: calls.append((key, kwargs)))

    assert secure_config.main(["--set"]) == 0

    assert calls == [(secret, {"passphrase": "unit-test-passphrase"})]
    assert secret not in " ".join(sys.argv)
    assert secret not in capsys.readouterr().out


def test_set_rejects_legacy_key_argument_without_echo(monkeypatch, capsys):
    secret = "AIza_legacy_cli_secret_123456"
    monkeypatch.setenv("VIRALCUTTER_CONFIG_PASSPHRASE", "unit-test-passphrase")

    assert secure_config.main(["--set", secret]) == 2

    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err
    assert "command-line" in captured.err


def test_set_requires_passphrase(monkeypatch, capsys):
    monkeypatch.delenv("VIRALCUTTER_CONFIG_PASSPHRASE", raising=False)
    monkeypatch.setattr("getpass.getpass", lambda prompt: "must-not-be-read")

    assert secure_config.main(["--set"]) == 2

    assert "VIRALCUTTER_CONFIG_PASSPHRASE" in capsys.readouterr().err


def test_get_masks_key_by_default(monkeypatch, capsys):
    monkeypatch.setattr(secure_config, "resolve_api_key", lambda **kwargs: "AIza1234567890")

    assert secure_config.main(["--get"]) == 0

    output = capsys.readouterr().out
    assert "AIza…7890" in output
    assert "AIza1234567890" not in output


def test_show_key_is_explicit(monkeypatch, capsys):
    monkeypatch.setattr(secure_config, "resolve_api_key", lambda **kwargs: "AIza1234567890")

    assert secure_config.main(["--show-key"]) == 0

    assert capsys.readouterr().out.strip() == "AIza1234567890"
