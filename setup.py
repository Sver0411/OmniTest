"""Include a prebuilt SPA when producing an installable platform wheel."""

import os
import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPlatform(build_py):
    def run(self):
        if getattr(self, "editable_mode", False):
            super().run()
            return
        root = Path(__file__).resolve().parent
        output = Path(self.build_lib).resolve()
        if output.exists():
            if not output.is_relative_to(root / "build") or output == root / "build":
                raise RuntimeError("Build output must remain inside the project build directory")
            shutil.rmtree(output)
        super().run()
        source = Path(__file__).parent / "frontend" / "dist"
        if (source / "index.html").is_file():
            shutil.copytree(
                source, Path(self.build_lib) / "omnitest" / "static", dirs_exist_ok=True
            )
        elif os.getenv("OMNITEST_REQUIRE_UI") == "1":
            raise RuntimeError("Build the frontend before building the platform release")


setup(cmdclass={"build_py": BuildPlatform})
