import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_built_distributions_include_importable_exporter(tmp_path):
    dist_dir = tmp_path / "dist"
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--sdist",
            "--outdir",
            str(dist_dir),
            str(ROOT),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert build.returncode == 0, f"{build.stdout}\n{build.stderr}"

    wheels = list(dist_dir.glob("*.whl"))
    sdists = list(dist_dir.glob("*.tar.gz"))
    assert len(wheels) == 1
    assert len(sdists) == 1

    with zipfile.ZipFile(wheels[0]) as archive:
        assert "scripts/export_xml_lib/exporter.py" in archive.namelist()

    with tarfile.open(sdists[0], "r:gz") as archive:
        assert any(
            member.endswith("/scripts/export_xml_lib/exporter.py")
            for member in archive.getnames()
        )

    site = tmp_path / "wheel-site"
    install = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(site),
            str(wheels[0]),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert install.returncode == 0, f"{install.stdout}\n{install.stderr}"

    env = os.environ.copy()
    env["PYTHONPATH"] = str(site)
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from pathlib import Path; "
            "import scripts.export_xml_lib.exporter as exporter; "
            "assert callable(exporter.export_pack); "
            "assert Path(exporter.__file__).resolve().is_relative_to("
            "Path(sys.argv[1]).resolve())",
            str(site),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert probe.returncode == 0, f"{probe.stdout}\n{probe.stderr}"
