from __future__ import annotations

import hashlib
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event

from .config import AppPaths, canonical_folders
from .embeddings import Embedder, LocalEmbedder
from .media import MediaReadError, read_media
from .readers import (
    MAX_FILE_BYTES,
    MAX_MEDIA_BYTES,
    Passage,
    chunk_passage,
    media_kind,
    read_document,
    walk_files,
)
from .storage import SearchResult, Store


@dataclass
class IndexReport:
    indexed: int = 0
    unchanged: int = 0
    removed: int = 0
    skipped: int = 0
    cancelled: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IndexProgress:
    phase: str
    completed: int = 0
    total: int = 0
    filename: str = ""
    passages_done: int = 0
    passages_total: int = 0


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
        on_progress: Callable[[IndexProgress], None] = lambda _: None,
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
            candidates = []
            on_progress(IndexProgress("Scanning folders"))
            for path in walk_files(roots, [data], scan_error, cancel.is_set):
                candidates.append(path)
                if len(candidates) % 100 == 0:
                    progress(f"Scanning folders · {len(candidates)} files found")
            total = len(candidates)

            def update(phase: str, path: Path | None = None, done: int = 0, count: int = 0):
                on_progress(
                    IndexProgress(
                        phase,
                        report.indexed + report.unchanged + report.skipped,
                        total,
                        path.name if path else "",
                        done,
                        count,
                    )
                )

            update("Indexing")
            for path in candidates:
                if cancel.is_set():
                    break
                seen.add(str(path))
                try:
                    info = path.stat(follow_symlinks=False)
                    kind = media_kind(path)
                    size_limit = MAX_MEDIA_BYTES if kind in {"audio", "video"} else MAX_FILE_BYTES
                    if not stat.S_ISREG(info.st_mode) or info.st_size > size_limit:
                        report.skipped += 1
                        # Do not keep old contents for files that are now excluded or too large.
                        store.forget([str(path)])
                        update("Skipped", path)
                        continue
                    old = store.document(path)
                    same = (
                        old and old["modified"] == info.st_mtime_ns and old["size"] == info.st_size
                    )
                    embedded = old and old["embedded_identity"] == self.embedder.identity
                    if same and (not semantic or embedded):
                        report.unchanged += 1
                        update("Unchanged", path)
                        continue
                    progress(f"Reading {path.name}")
                    update("Reading", path)
                    # File-level atomic replacement means a cancelled run can be resumed safely.
                    passages = []
                    if kind:
                        passages = [Passage(path.stem.replace("_", " "), kind.title())]
                    for section in () if kind else read_document(path):
                        if cancel.is_set():
                            break
                        passages.extend(chunk_passage(section))
                    if cancel.is_set():
                        break
                except Exception as error:
                    # A malformed or unreadable document must not stop the rest of the folder.
                    report.errors.append(f"{path.name}: {error}")
                    report.skipped += 1
                    update("Skipped", path)
                    continue
                # Model failures are job errors, not unreadable-file errors. Preserve the previous
                # complete document and report the failure instead of silently skipping it.
                vectors = None
                if semantic and kind:
                    passages = []
                    vectors = []
                    try:
                        for part in read_media(path, cancel.is_set):
                            if cancel.is_set():
                                break
                            update(f"Embedding {kind}", path, part.number - 1, part.total)
                            vector = self.embedder.encode_media(part.inputs)
                            vectors.append(vector)
                            passages.append(Passage(path.stem.replace("_", " "), part.location))
                            update(f"Embedding {kind}", path, part.number, part.total)
                    except MediaReadError as error:
                        report.errors.append(f"{path.name}: {error}")
                        report.skipped += 1
                        update("Skipped", path)
                        continue
                    if cancel.is_set():
                        break
                elif semantic and passages:
                    progress(f"Embedding {path.name} ({len(passages)} passages)")
                    vectors = []
                    update("Embedding", path, 0, len(passages))
                    for start in range(0, len(passages), 16):
                        if cancel.is_set():
                            break
                        vectors.extend(
                            self.embedder.encode(
                                [passage.text for passage in passages[start : start + 16]]
                            )
                        )
                        update("Embedding", path, len(vectors), len(passages))
                    if cancel.is_set():
                        break
                try:
                    after = path.stat(follow_symlinks=False)
                    if (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
                        raise ValueError("File changed while it was being read; retry indexing")
                except (OSError, ValueError) as error:
                    report.errors.append(f"{path.name}: {error}")
                    report.skipped += 1
                    update("Skipped", path)
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
                update("Indexed", path)
                progress(f"Indexed {report.indexed} · unchanged {report.unchanged} · {path.name}")
            report.cancelled = cancel.is_set()
            if not report.cancelled:
                update("Removing stale entries")
                report.removed = store.prune(roots, seen, incomplete)
            update("Stopped" if report.cancelled else "Finished")
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
