"""Isolated indexing process; progress files also work in Windows windowed executables."""

from __future__ import annotations

import contextlib
import faulthandler
import json
from dataclasses import asdict
from pathlib import Path
from typing import TextIO

from .config import AppPaths
from .embeddings import LocalEmbedder
from .engine import Engine


def run(request: dict, output: TextIO) -> int:
    def send(kind, value):
        output.write(json.dumps({"kind": kind, "value": value}) + "\n")
        output.flush()

    try:
        paths = AppPaths(Path(request["data"]))
        embedder = LocalEmbedder(paths.model, on_device=lambda label: send("device", label))
        report = Engine(paths, embedder).index(
            request["folders"],
            semantic=request["semantic"],
            progress=lambda value: send("progress", value),
            on_progress=lambda value: send("indexing_progress", asdict(value)),
        )
        send("succeeded", asdict(report))
        return 0
    except Exception as error:
        send("failed", str(error))
        return 1


def file_main(request: Path, events: Path, errors: Path) -> int:
    with errors.open("a", encoding="utf-8") as diagnostics:
        faulthandler.enable(file=diagnostics)
        try:
            with contextlib.redirect_stdout(diagnostics), contextlib.redirect_stderr(diagnostics):
                with events.open("a", encoding="utf-8") as output:
                    return run(json.loads(request.read_text(encoding="utf-8")), output)
        finally:
            faulthandler.disable()
