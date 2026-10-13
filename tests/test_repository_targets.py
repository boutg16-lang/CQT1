from pathlib import Path

from scripts import auto_updater, safety_updater

REPOSITORY = "boutg16-lang/CAT"
ROOT = Path(__file__).resolve().parent.parent


def test_binary_updater_checks_releases_from_this_repository(monkeypatch):
    requested_paths = []

    def fake_github_api(path, timeout=8):
        requested_paths.append(path)
        return {"tag_name": "v7.51.0-pro", "assets": []}

    monkeypatch.setattr(auto_updater, "_update_channel", lambda: "github")
    monkeypatch.setattr(auto_updater, "_github_api", fake_github_api)
    monkeypatch.setattr(auto_updater, "LOCAL_VERSION", "7.51.0-pro")

    result = auto_updater.check_for_update(timeout=1)

    assert requested_paths == ["/repos/{}/releases/latest".format(REPOSITORY)]
    assert result["update_available"] is False


def test_safety_updater_uses_this_repository_as_canonical_source():
    expected = "https://raw.githubusercontent.com/{}/main/safety_blocklist.json".format(
        REPOSITORY)

    assert safety_updater.REMOTE_URL == expected
    assert safety_updater.REMOTE_SOURCES[0]["url"] == expected
    assert (ROOT / "safety_blocklist.json").is_file()


def test_windows_installer_links_to_this_repository():
    installer = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")

    assert '#define MyAppURL "https://github.com/{}"'.format(REPOSITORY) in installer
