"""Run the frozen application with spaces/non-ASCII paths and inspect its result file."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--media", action="store_true")
    args = parser.parse_args()
    executable = args.executable or Path("dist/subject-to-change") / (
        "subject-to-change.exe" if os.name == "nt" else "subject-to-change"
    )
    with tempfile.TemporaryDirectory(prefix="subject to change check ") as directory:
        report = Path(directory) / "結果.json"
        command = [str(executable.resolve()), "--smoke-test", str(report)]
        if args.media:
            command.append("--media")
        completed = subprocess.run(
            command, timeout=180, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}
        )
        if not report.exists():
            raise RuntimeError(
                f"Packaged launcher produced no result (exit {completed.returncode})"
            )
        result = json.loads(report.read_text(encoding="utf-8"))
        print(json.dumps(result, indent=2))
        if completed.returncode or not result.get("ok"):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
