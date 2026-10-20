from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "Subject to Change"


@dataclass(frozen=True)
class AppPaths:
    data: Path

    @classmethod
    def default(cls) -> AppPaths:
        if override := os.environ.get("SUBJECT_TO_CHANGE_DATA"):
            return cls(Path(override).expanduser().resolve())
        if sys.platform == "win32":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        else:
            base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        return cls(base / "subject-to-change")

    @property
    def database(self) -> Path:
        return self.data / "index.sqlite3"

    @property
    def settings(self) -> Path:
        return self.data / "settings.json"

    @property
    def model(self) -> Path:
        return self.data / "models" / "embeddinggemma-2"


def write_json(path: Path, value: dict) -> None:
    """An interrupted settings write must not replace the last valid settings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            name = stream.name
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name and Path(name).exists():
            Path(name).unlink()


@dataclass
class Settings:
    folders: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> Settings:
        if not path.exists():
            return cls()
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            folders = value["folders"]
            if not isinstance(folders, list) or not all(isinstance(p, str) for p in folders):
                raise ValueError("folders must be a list of paths")
            return cls(list(dict.fromkeys(folders)))
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError(f"Cannot read settings at {path}: {error}") from error

    def save(self, path: Path) -> None:
        write_json(path, {"folders": self.folders})


def canonical_folders(folders: list[str | Path]) -> list[Path]:
    """Validate roots and collapse overlaps, using platform-aware Path semantics."""
    roots: list[Path] = []
    for folder in folders:
        root = Path(folder).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError(f"Not a folder: {root}")
        if not any(root.is_relative_to(parent) for parent in roots):
            roots = [parent for parent in roots if not parent.is_relative_to(root)]
            roots.append(root)
    return roots
