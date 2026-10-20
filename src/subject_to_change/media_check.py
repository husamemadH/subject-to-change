"""Validate bundled codecs in a helper, separate from Qt's own FFmpeg libraries."""

from __future__ import annotations

import json
import os
import traceback
from pathlib import Path

from .media import read_media
from .readers import media_kind


def main(request: Path, result: Path) -> int:
    report = {}
    try:
        if os.environ.get("SUBJECT_TO_CHANGE_SMOKE_MODEL"):
            from sentence_transformers import SentenceTransformer

            report["embedding_runtime"] = SentenceTransformer.__name__
        for filename in json.loads(request.read_text(encoding="utf-8")):
            path = Path(filename)
            parts = list(read_media(path))
            assert parts and all(part.inputs for part in parts), f"No media decoded: {path}"
            report[media_kind(path)] = len(parts)
        report["ok"] = True
        code = 0
    except Exception:
        report.update(ok=False, error=traceback.format_exc())
        code = 1
    result.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return code
