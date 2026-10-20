"""Helper launch commands and native library setup for source and frozen applications."""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from pathlib import Path

_DLL_HANDLES = []
_DLL_PATHS: set[str] = set()


def helper_command(kind: str, *paths: str | Path) -> list[str]:
    arguments = [f"--{kind}-worker", *(str(path) for path in paths)]
    if getattr(sys, "frozen", False):
        return [sys.executable, *arguments]
    executable = sys.executable
    if sys.platform == "win32":
        windowed = Path(executable).with_name("pythonw.exe")
        if windowed.exists():
            executable = str(windowed)
    return [executable, "-m", "subject_to_change.launch", *arguments]


def process_options() -> dict:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def configure_native_libraries() -> None:
    if sys.platform != "win32":
        return
    directories = []
    if custom := os.environ.get("SUBJECT_TO_CHANGE_FFMPEG_DIR"):
        directories.append(Path(custom))
    if getattr(sys, "frozen", False):
        directories.append(Path(sys._MEIPASS) / "ffmpeg")
    else:
        directories.append(Path(__file__).resolve().parents[2] / "packaging/vendor/ffmpeg/bin")
    if executable := shutil.which("ffmpeg"):
        directories.append(Path(executable).parent)
    for directory in directories:
        value = str(directory.resolve())
        if value in _DLL_PATHS or not directory.is_dir():
            continue
        _DLL_HANDLES.append(os.add_dll_directory(value))
        _DLL_PATHS.add(value)
        os.environ["PATH"] = value + os.pathsep + os.environ.get("PATH", "")
        # Load the chosen FFmpeg build before Qt can load its own DLLs with the same names.
        for stem in (
            "avutil",
            "swresample",
            "swscale",
            "avcodec",
            "avformat",
            "avfilter",
            "avdevice",
        ):
            for library in sorted(directory.glob(f"{stem}-*.dll")):
                try:
                    _DLL_HANDLES.append(ctypes.CDLL(str(library.resolve())))
                except OSError:
                    # Codec initialization will report missing dependencies if media is used.
                    pass


def ensure_standard_streams() -> None:
    # Windows windowed executables have no Python standard streams, even with redirection.
    for name, mode in (("stdin", "r"), ("stdout", "w"), ("stderr", "w")):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, mode, encoding="utf-8"))
