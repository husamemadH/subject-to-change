"""Dispatch helper modes before importing the desktop UI, including in a frozen executable."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

from .runtime import configure_native_libraries, ensure_standard_streams


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    ensure_standard_streams()
    configure_native_libraries()
    if args and args[0] == "--index-worker":
        from .index_worker import file_main

        return file_main(*(Path(value) for value in args[1:]))
    if args and args[0] == "--device-worker":
        from .device import detect_device

        Path(args[1]).write_text(json.dumps(asdict(detect_device())), encoding="utf-8")
        return 0
    if args and args[0] == "--media-worker":
        from .media_check import main as check_media

        return check_media(Path(args[1]), Path(args[2]))
    if args and args[0] == "--smoke-test":
        from .package_check import main as check

        return check(Path(args[1]), media="--media" in args[2:])
    from .ui import main as gui

    return gui()


if __name__ == "__main__":
    raise SystemExit(main())
