"""Build on the target OS. Model weights are never bundled or downloaded."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--without-model", action="store_true", help="Small keyword-only build for launcher checks"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onedir",
        "--name",
        "subject-to-change",
        "--paths",
        str(root / "src"),
        "--hidden-import",
        "pypdf",
    ]
    if args.without_model:
        for package in (
            "torch",
            "torchvision",
            "torchcodec",
            "sentence_transformers",
            "transformers",
            "numpy",
            "scipy",
            "sklearn",
        ):
            command += ["--exclude-module", package]
    else:
        for package in (
            "sentence_transformers",
            "transformers",
            "tokenizers",
            "safetensors",
            "torchcodec",
            "soundfile",
        ):
            command += ["--collect-all", package]
        if sys.platform == "win32":
            ffmpeg = root / "packaging/vendor/ffmpeg"
            binaries = list((ffmpeg / "bin").glob("*.dll"))
            if not binaries:
                raise RuntimeError(
                    "Run python packaging/prepare_ffmpeg.py before building on Windows"
                )
            binaries += list((ffmpeg / "bin").glob("*.exe"))
            for path in binaries:
                command += ["--add-binary", f"{path}{os.pathsep}ffmpeg"]
            for path in (ffmpeg / "doc", ffmpeg / "SOURCE.json", *ffmpeg.glob("LICENSE*")):
                command += ["--add-data", f"{path}{os.pathsep}ffmpeg/licenses"]
    command.append(str(root / "packaging/launcher.py"))
    subprocess.run(command, cwd=root, check=True)


if __name__ == "__main__":
    main()
