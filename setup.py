from pathlib import Path
from runpy import run_path

from setuptools import setup
from setuptools.command.build_py import build_py

ROOT = Path(__file__).resolve().parent
RUNTIME_ASSETS = run_path(str(ROOT / "app_assets.py"))["RUNTIME_ASSETS"]


class BuildPyWithRuntimeAssets(build_py):
    def run(self):
        super().run()
        for relative in RUNTIME_ASSETS:
            source = ROOT / relative
            if not source.is_file():
                raise FileNotFoundError(f"Missing runtime asset: {relative}")
            destination = Path(self.build_lib) / relative
            self.mkpath(str(destination.parent))
            self.copy_file(str(source), str(destination))


setup(cmdclass={"build_py": BuildPyWithRuntimeAssets})
