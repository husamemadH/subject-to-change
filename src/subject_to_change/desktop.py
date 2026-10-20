from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path


def reveal_file(path: Path, open_folder: Callable[[Path], bool]) -> bool:
    """Ask the desktop to select a file; fall back to opening its containing folder."""
    path = path.resolve(strict=True)
    if sys.platform == "win32":
        try:
            subprocess.Popen(["explorer.exe", f"/select,{path}"])
            return True
        except OSError:
            return open_folder(path.parent)
    if sys.platform.startswith("linux"):
        try:
            subprocess.run(
                [
                    "dbus-send",
                    "--session",
                    "--print-reply",
                    "--reply-timeout=2000",
                    "--dest=org.freedesktop.FileManager1",
                    "/org/freedesktop/FileManager1",
                    "org.freedesktop.FileManager1.ShowItems",
                    f"array:string:{path.as_uri()}",
                    "string:",
                ],
                check=True,
                timeout=3,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except (OSError, subprocess.SubprocessError):
            pass
    return open_folder(path.parent)
