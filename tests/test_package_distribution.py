import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

from app_assets import RUNTIME_ASSETS

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
        wheel_members = set(archive.namelist())
        assert "scripts/export_xml_lib/exporter.py" in wheel_members
        assert set(RUNTIME_ASSETS).issubset(wheel_members)

    with tarfile.open(sdists[0], "r:gz") as archive:
        sdist_members = archive.getnames()
        assert any(
            member.endswith("/scripts/export_xml_lib/exporter.py")
            for member in sdist_members
        )
        for asset in RUNTIME_ASSETS:
            assert any(member.endswith(f"/{asset}") for member in sdist_members), asset

    sdist_wheel_dir = tmp_path / "sdist-wheel"
    sdist_build = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(sdist_wheel_dir),
            str(sdists[0]),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert sdist_build.returncode == 0, f"{sdist_build.stdout}\n{sdist_build.stderr}"
    sdist_wheels = list(sdist_wheel_dir.glob("*.whl"))
    assert len(sdist_wheels) == 1
    with zipfile.ZipFile(sdist_wheels[0]) as archive:
        assert set(RUNTIME_ASSETS).issubset(archive.namelist())
        assert "scripts/export_xml_lib/exporter.py" in archive.namelist()

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
            str(sdist_wheels[0]),
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
            "from scripts.preflight import check_assets; "
            "from i18n.i18n import I18nAuto; "
            "assert callable(exporter.export_pack); "
            "assert check_assets()['status'] == 'ok'; "
            "assert I18nAuto('ar_SA').language == 'ar_SA'; "
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
