from __future__ import annotations

import hashlib
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event

from .config import AppPaths, canonical_folders
from .embeddings import Embedder, LocalEmbedder
from .readers import MAX_FILE_BYTES, chunk_passage, read_document, walk_files
from .storage import SearchResult, Store


@dataclass
class IndexReport:
    indexed: int = 0
    unchanged: int = 0
    removed: int = 0
    skipped: int = 0
    cancelled: bool = False
    errors: list[str] = field(default_factory=list)


class Engine:
    def __init__(self, paths: AppPaths, embedder: Embedder | None = None) -> None:
        self.paths = paths
        self.embedder = embedder if embedder is not None else LocalEmbedder(paths.model)

    def index(
        self,
        folders: list[str | Path],
        *,
        semantic: bool = True,
        progress: Callable[[str], None] = lambda _: None,
        cancel: Event | None = None,
    ) -> IndexReport:
        cancel = cancel if cancel is not None else Event()
        roots = canonical_folders(folders)
        if not roots:
            raise ValueError("Choose at least one folder before indexing")
        data = self.paths.data.resolve()
        if any(root.is_relative_to(data) for root in roots):
            raise ValueError("Choose a folder outside the application's own data directory")
        report = IndexReport()
        incomplete: list[Path] = []
        seen: set[str] = set()

        def scan_error(error: OSError) -> None:
            report.errors.append(str(error))
            if error.filename:
                incomplete.append(Path(error.filename).absolute())
            else:
                incomplete.extend(roots)

        with Store(self.paths.database) as store:
            if semantic:
                store.ensure_model(self.embedder.identity, self.embedder.dimensions)
            for path in walk_files(roots, [data], scan_error, cancel.is_set):
                if cancel.is_set():
                    break
                seen.add(str(path))
                try:
                    info = path.stat(follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                        report.skipped += 1
                        # Do not keep old contents for files that are now excluded or too large.
                        store.forget([str(path)])
                        continue
                    old = store.document(path)
                    same = (
                        old and old["modified"] == info.st_mtime_ns and old["size"] == info.st_size
                    )
                    embedded = old and old["embedded_identity"] == self.embedder.identity
                    if same and (not semantic or embedded):
                        report.unchanged += 1
                        continue
                    progress(f"Reading {path.name}")
                    # File-level atomic replacement means a cancelled run can be resumed safely.
                    passages = []
                    for section in read_document(path):
                        if cancel.is_set():
                            break
                        passages.extend(chunk_passage(section))
                    if cancel.is_set():
                        break
                except Exception as error:
                    # A malformed or unreadable document must not stop the rest of the folder.
                    report.errors.append(f"{path.name}: {error}")
                    report.skipped += 1
                    continue
                # Model failures are job errors, not unreadable-file errors. Preserve the previous
                # complete document and report the failure instead of silently skipping it.
                vectors = None
                if semantic and passages:
                    progress(f"Embedding {path.name} ({len(passages)} passages)")
                    vectors = []
                    for start in range(0, len(passages), 16):
                        if cancel.is_set():
                            break
                        vectors.extend(
                            self.embedder.encode(
                                [passage.text for passage in passages[start : start + 16]]
                            )
                        )
                    if cancel.is_set():
                        break
                try:
                    after = path.stat(follow_symlinks=False)
                    if (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
                        raise ValueError("File changed while it was being read; retry indexing")
                except (OSError, ValueError) as error:
                    report.errors.append(f"{path.name}: {error}")
                    report.skipped += 1
                    continue
                digest = hashlib.sha256(
                    "\n".join(p.text for p in passages).encode("utf-8")
                ).hexdigest()
                root = next(root for root in roots if path.is_relative_to(root))
                store.replace(
                    path,
                    root,
                    info.st_size,
                    info.st_mtime_ns,
                    digest,
                    passages,
                    vectors,
                    self.embedder.identity if semantic else None,
                    self.embedder.dimensions,
                )
                report.indexed += 1
                progress(f"Indexed {report.indexed} · unchanged {report.unchanged} · {path.name}")
            report.cancelled = cancel.is_set()
            if not report.cancelled:
                report.removed = store.prune(roots, seen, incomplete)
        return report

    def search(self, query: str, *, semantic: bool = True, limit: int = 30) -> list[SearchResult]:
        if not query.strip():
            return []
        with Store(self.paths.database) as store:
            vector = None
            if semantic:
                identity = store.model_identity()
                if identity is None or store.stats()["embedded"] == 0:
                    raise ValueError(
                        "Index your folders with the model first, or choose Exact words"
                    )
                if identity != self.embedder.identity:
                    raise ValueError(
                        "This index uses a different model. Rebuild it before searching."
                    )
                vector = self.embedder.encode([query], query=True)[0]
            return store.search(query, vector, limit)

    def stats(self) -> dict[str, int]:
        with Store(self.paths.database) as store:
            return store.stats()

    def retain_folders(self, folders: list[str]) -> None:
        # Missing/offline roots still belong to the user. Removal is an explicit settings action.
        with Store(self.paths.database) as store:
            store.retain_roots([Path(folder) for folder in folders])

    def clear(self) -> None:
        with Store(self.paths.database) as store:
            store.clear()
