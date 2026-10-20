"""Build on the target OS: python packaging/build.py. Never bundles or downloads model weights."""

import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
subprocess.run(
    [
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
        "--collect-all",
        "sentence_transformers",
        "--collect-all",
        "transformers",
        "--collect-all",
        "tokenizers",
        "--collect-all",
        "safetensors",
        "--hidden-import",
        "pypdf",
        str(root / "packaging" / "launcher.py"),
    ],
    cwd=root,
    check=True,
)
