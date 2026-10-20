from __future__ import annotations

import math
import re
import sqlite3
import struct
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

from .embeddings import normalize
from .readers import Passage

SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    root TEXT NOT NULL,
    size INTEGER NOT NULL,
    modified INTEGER NOT NULL,
    digest TEXT NOT NULL,
    embedded_identity TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    text TEXT NOT NULL,
    location TEXT NOT NULL,
    vector BLOB
);
CREATE INDEX IF NOT EXISTS chunks_document ON chunks(document_id);
CREATE VIRTUAL TABLE IF NOT EXISTS keywords USING fts5(
    text, path, content='chunks', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS chunks_insert AFTER INSERT ON chunks BEGIN
    INSERT INTO keywords(rowid, text, path) VALUES (new.id, new.text, new.path);
END;
CREATE TRIGGER IF NOT EXISTS chunks_delete AFTER DELETE ON chunks BEGIN
    INSERT INTO keywords(keywords, rowid, text, path) VALUES ('delete', old.id, old.text, old.path);
END;
"""


@dataclass(frozen=True)
class SearchResult:
    path: str
    text: str
    location: str
    score: float
    match: str


class Store(AbstractContextManager):
    """Each worker owns its connection. Transactions replace one complete document at a time."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=20)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript(SCHEMA)
        version = self.connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()
        if version is not None and version[0] != "1":
            self.connection.close()
            raise ValueError("This index was created by a different application version")
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO metadata VALUES ('schema_version', '1')")

    def __exit__(self, *args) -> None:
        self.connection.close()

    def model_identity(self) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = 'model_identity'"
        ).fetchone()
        return row[0] if row else None

    def ensure_model(self, identity: str, dimensions: int) -> None:
        previous = self.model_identity()
        if previous is not None and previous != identity:
            raise ValueError(
                "This index uses a different model. Clear the index before switching models."
            )
        with self.connection:
            for key, value in (("model_identity", identity), ("dimensions", str(dimensions))):
                self.connection.execute(
                    "INSERT OR IGNORE INTO metadata VALUES (?, ?)", (key, value)
                )

    def document(self, path: Path) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM documents WHERE path = ?", (str(path),)
        ).fetchone()

    def replace(
        self,
        path: Path,
        root: Path,
        size: int,
        modified: int,
        digest: str,
        passages: list[Passage],
        vectors: list[list[float]] | None,
        identity: str | None,
        dimensions: int,
    ) -> None:
        if vectors is not None and len(passages) != len(vectors):
            raise ValueError("Every passage must have an embedding")
        blobs = (
            [struct.pack(f"<{dimensions}f", *normalize(v, dimensions)) for v in vectors]
            if vectors is not None
            else [None] * len(passages)
        )
        with self.connection:
            self.connection.execute("DELETE FROM documents WHERE path = ?", (str(path),))
            cursor = self.connection.execute(
                "INSERT INTO documents(path, root, size, modified, digest, embedded_identity) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (str(path), str(root), size, modified, digest, identity),
            )
            self.connection.executemany(
                "INSERT INTO chunks(document_id, path, text, location, vector) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (cursor.lastrowid, str(path), p.text, p.location, blob)
                    for p, blob in zip(passages, blobs, strict=True)
                ],
            )

    def forget(self, paths: list[str]) -> None:
        with self.connection:
            self.connection.executemany(
                "DELETE FROM documents WHERE path = ?", [(p,) for p in paths]
            )

    def prune(self, roots: list[Path], seen: set[str], incomplete: list[Path]) -> int:
        removed = []
        for row in self.connection.execute("SELECT path FROM documents"):
            path = Path(row[0])
            selected = any(path.is_relative_to(root) for root in roots)
            # A failed scan or disconnected drive is not evidence that files were deleted.
            uncertain = any(path.is_relative_to(folder) for folder in incomplete)
            if not selected or (str(path) not in seen and not uncertain):
                removed.append(str(path))
        self.forget(removed)
        return len(removed)

    def retain_roots(self, roots: list[Path]) -> None:
        self.forget(
            [
                row[0]
                for row in self.connection.execute("SELECT path FROM documents")
                if not any(Path(row[0]).is_relative_to(root) for root in roots)
            ]
        )

    def clear(self) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM documents")
            self.connection.execute("DELETE FROM metadata WHERE key != 'schema_version'")

    def stats(self) -> dict[str, int]:
        return {
            "files": self.connection.execute("SELECT count(*) FROM documents").fetchone()[0],
            "passages": self.connection.execute("SELECT count(*) FROM chunks").fetchone()[0],
            "embedded": self.connection.execute(
                "SELECT count(*) FROM chunks WHERE vector IS NOT NULL"
            ).fetchone()[0],
        }

    def search(
        self,
        query: str,
        vector: list[float] | None = None,
        limit: int = 30,
    ) -> list[SearchResult]:
        if not query.strip() or not 1 <= limit <= 100:
            return []
        candidates: dict[int, sqlite3.Row] = {}
        scores: dict[int, float] = {}
        lexical: set[int] = set()
        semantic: set[int] = set()
        # Treat input as words, not as executable FTS syntax (quotes, colons, NOT, etc.).
        words = list(dict.fromkeys(re.findall(r"\w+", query, flags=re.UNICODE)))[:32]
        if words:
            expression = " OR ".join('"' + word + '"' for word in words)
            rows = self.connection.execute(
                "SELECT chunks.*, bm25(keywords, 1.0, 3.0) AS rank FROM keywords "
                "JOIN chunks ON chunks.id = keywords.rowid WHERE keywords MATCH ? "
                "ORDER BY rank LIMIT 100",
                (expression,),
            ).fetchall()
            for rank, row in enumerate(rows, 1):
                candidates[row["id"]] = row
                scores[row["id"]] = 1 / (60 + rank)
                lexical.add(row["id"])
        if vector is not None:
            vector = normalize(vector, len(vector))
            # Exact cosine search is deliberately simple for the initial local-library version.
            # Keep just the best candidates; swap in an ANN index when measured sizes require it.
            import heapq

            def semantic_rows():
                for row in self.connection.execute("SELECT * FROM chunks WHERE vector IS NOT NULL"):
                    if len(row["vector"]) != len(vector) * 4:
                        raise ValueError("Embedding dimension mismatch; rebuild the index")
                    stored = struct.unpack(f"<{len(vector)}f", row["vector"])
                    similarity = sum(a * b for a, b in zip(stored, vector, strict=True))
                    if math.isfinite(similarity):
                        yield similarity, row["id"], row

            nearest = heapq.nlargest(100, semantic_rows(), key=lambda item: item[0])
            for rank, (_, chunk_id, row) in enumerate(nearest, 1):
                candidates[chunk_id] = row
                semantic.add(chunk_id)
                scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (60 + rank)
        ordered = sorted(candidates, key=lambda key: scores[key], reverse=True)
        results, used = [], set()
        for key in ordered:
            row = candidates[key]
            if row["path"] in used:
                continue
            used.add(row["path"])
            match = (
                "Meaning + words"
                if key in semantic and key in lexical
                else ("Exact words" if key in lexical else "Meaning")
            )
            results.append(
                SearchResult(row["path"], row["text"], row["location"], scores[key], match)
            )
            if len(results) == limit:
                break
        return results
