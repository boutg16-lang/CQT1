import sys
from pathlib import Path
from types import SimpleNamespace

from scripts.stream_guard import cli


def test_fetch_tracks_does_not_return_stale_files(tmp_path, monkeypatch):
    outdir = tmp_path / "tracks"
    outdir.mkdir()
    stale = outdir / "old.srt"
    stale.write_text("stale", encoding="utf-8")
    downloads = []

    class NoTracks:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def download(self, urls):
            downloads.extend(urls)

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=NoTracks))
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    assert cli.fetch_tracks("https://www.youtube.com/watch?v=abcdefghijk",
                            str(outdir), retries=2, wait=0) == []
    assert downloads == ["https://www.youtube.com/watch?v=abcdefghijk"] * 2
    assert stale.read_text(encoding="utf-8") == "stale"
    assert list(outdir.iterdir()) == [stale]


def test_fetch_tracks_returns_only_current_run_files(tmp_path, monkeypatch):
    outdir = tmp_path / "tracks"
    outdir.mkdir()
    stale = outdir / "old.srt"
    stale.write_text("stale", encoding="utf-8")
    attempts = []

    class DelayedTracks:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def download(self, _urls):
            attempts.append(True)
            if len(attempts) == 2:
                path = self.options["outtmpl"]
                for placeholder, value in (("%(id)s", "abcdefghijk"),
                                           ("%(language)s", "ar"), ("%(ext)s", "srt")):
                    path = path.replace(placeholder, value)
                with open(path, "w", encoding="utf-8") as stream:
                    stream.write("fresh")

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=DelayedTracks))
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    files = cli.fetch_tracks("https://www.youtube.com/watch?v=abcdefghijk",
                             str(outdir), retries=3, wait=0)

    assert len(attempts) == 2
    assert len(files) == 1
    assert files[0] != str(stale)
    assert Path(files[0]).read_text(encoding="utf-8") == "fresh"
    assert Path(files[0]).parent.name.startswith("run-")
