import hashlib
import json
import zipfile

from scripts.package_release import package_release


def test_release_package_includes_source_and_examples_but_excludes_local_state(tmp_path):
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "tests" / "fixtures").mkdir(parents=True)
    (source / "VIRALS" / "project").mkdir(parents=True)
    (source / "keys").mkdir()
    (source / ".aws").mkdir()
    (source / ".ssh").mkdir()
    (source / "main.py").write_text("print('safe')\n", encoding="utf-8")
    (source / ".env.example").write_text("GEMINI_API_KEY=\n", encoding="utf-8")
    (source / "api_config.example.json").write_text("{}\n", encoding="utf-8")
    (source / "api_config.json").write_text('{"api_key":"secret"}\n', encoding="utf-8")
    (source / ".env").write_text("GEMINI_API_KEY=secret\n", encoding="utf-8")
    (source / ".env.production").write_text("GEMINI_API_KEY=secret\n", encoding="utf-8")
    (source / "production.env").write_text("GEMINI_API_KEY=secret\n", encoding="utf-8")
    (source / "api_keys.txt").write_text("AIza_private_key\n", encoding="utf-8")
    (source / "oauth_token.json").write_text('{"access_token":"secret"}\n', encoding="utf-8")
    (source / "service-account.json").write_text('{"private_key":"secret"}\n', encoding="utf-8")
    (source / "production.env").write_text("GEMINI_API_KEY=secret\n", encoding="utf-8")
    (source / "api-key.txt").write_text("AIza_secret\n", encoding="utf-8")
    (source / "service-account.yaml").write_text("private_key: secret\n", encoding="utf-8")
    (source / "client_secrets.json").write_text('{"client_secret":"secret"}\n', encoding="utf-8")
    (source / "credentials.json").write_text('{"token":"secret"}\n', encoding="utf-8")
    (source / "token.pickle").write_bytes(b"private token")
    (source / "workspace_api_config.backup.json").write_text('{"api_key":"secret"}\n', encoding="utf-8")
    (source / "webui_settings.local.json").write_text('{"session":"private"}\n', encoding="utf-8")
    (source / "client_secrets.example.json").write_text("{}\n", encoding="utf-8")
    (source / "tests" / "fixtures" / "stream_ar.srt").write_text("safe fixture\n", encoding="utf-8")
    (source / "VIRALS" / "project" / "private.mp4").write_bytes(b"private media")
    (source / "gpu_profile.json").write_text("{}\n", encoding="utf-8")
    (source / "keys" / "update.pub").write_text("public key\n", encoding="utf-8")
    (source / "keys" / "update.key").write_text("private key\n", encoding="utf-8")
    (source / ".aws" / "credentials").write_text("access_key=secret\n", encoding="utf-8")
    (source / ".ssh" / "id_ed25519").write_text("private key\n", encoding="utf-8")

    archive_path = tmp_path / "release.zip"
    manifest = package_release(source, archive_path)

    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        names = set(archive.namelist())
        assert "OUSSAMA-Cutter-8.0.0-ultimate/main.py" in names
        assert "OUSSAMA-Cutter-8.0.0-ultimate/.env.example" in names
        assert "OUSSAMA-Cutter-8.0.0-ultimate/api_config.example.json" in names
        assert "OUSSAMA-Cutter-8.0.0-ultimate/client_secrets.example.json" in names
        assert "OUSSAMA-Cutter-8.0.0-ultimate/tests/fixtures/stream_ar.srt" in names
        assert "OUSSAMA-Cutter-8.0.0-ultimate/keys/update.pub" in names
        assert not any(name.endswith(("/.env", "/.env.production", "/production.env",
                                     "/api-key.txt", "/service-account.yaml",
                                     "/api_config.json", ".mp4", ".key")) for name in names)
        assert not any(name.endswith(("/client_secrets.json", "/credentials.json", "/token.pickle",
                                     "/production.env", "/api_keys.txt", "/oauth_token.json",
                                     "/service-account.json")) for name in names)
        assert not any(name.endswith(("/workspace_api_config.backup.json",
                                      "/webui_settings.local.json")) for name in names)

        assert not any("/VIRALS/" in name or "/gpu_profile.json" in name for name in names)
        assert b"GEMINI_API_KEY=secret" not in archive.read(
            "OUSSAMA-Cutter-8.0.0-ultimate/main.py")

    assert manifest["file_count"] == 6
    assert manifest["sha256"] == hashlib.sha256(archive_path.read_bytes()).hexdigest()
    assert json.loads(archive_path.with_suffix(".manifest.json").read_text(encoding="utf-8")) == manifest
    assert archive_path.with_suffix(".zip.sha256").exists()


def test_release_package_refuses_output_inside_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_text("pass\n", encoding="utf-8")

    try:
        package_release(source, source / "release.zip")
    except ValueError as exc:
        assert "outside the source tree" in str(exc)
    else:
        raise AssertionError("Expected an output path inside the source tree to be refused")
