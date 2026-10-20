from __future__ import annotations

import os
import re
import stat
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".rst",
    ".log",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".ini",
    ".cfg",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".css",
    ".scss",
    ".sql",
    ".sh",
    ".bash",
    ".rs",
    ".go",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".java",
    ".kt",
    ".swift",
    ".rb",
    ".php",
    ".vue",
    ".svelte",
    ".ipynb",
    ".tex",
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif"}
AUDIO_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".flac",
    ".ogg",
    ".opus",
    ".m4a",
    ".aac",
    ".aiff",
    ".aif",
    ".wma",
}
VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".mov",
    ".webm",
    ".avi",
    ".m4v",
    ".mpeg",
    ".mpg",
    ".wmv",
    ".mts",
}
EXTENSIONS = (
    TEXT_EXTENSIONS
    | IMAGE_EXTENSIONS
    | AUDIO_EXTENSIONS
    | VIDEO_EXTENSIONS
    | {".html", ".htm", ".docx", ".odt", ".pdf"}
)
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_MEDIA_BYTES = 2 * 1024 * 1024 * 1024
MAX_TEXT_CHARS = 1_000_000
MAX_PDF_PAGES = 500
SKIP_DIRS = {
    "node_modules",
    "venv",
    "__pycache__",
    "target",
    "dist",
    "build",
    "vendor",
    "$RECYCLE.BIN",
    "System Volume Information",
}
PRIVATE_NAMES = {"credentials", "credentials.json", "secrets.json", "id_rsa", "id_ed25519"}


@dataclass(frozen=True)
class Passage:
    text: str
    location: str = ""


def media_kind(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return None


def allowed(path: Path) -> bool:
    return (
        not path.name.startswith(".")
        and path.name.lower() not in PRIVATE_NAMES
        and path.suffix.lower() in EXTENSIONS
    )


def walk_files(
    roots: list[Path],
    excluded: list[Path],
    on_error: Callable[[OSError], None],
    cancelled: Callable[[], bool] = lambda: False,
) -> Iterator[Path]:
    for root in roots:
        for current, dirs, files in os.walk(root, followlinks=False, onerror=on_error):
            if cancelled():
                return
            directory = Path(current)
            dirs[:] = sorted(
                name
                for name in dirs
                if not name.startswith(".")
                and name not in SKIP_DIRS
                and not (directory / name).is_symlink()
                and not any((directory / name).is_relative_to(item) for item in excluded)
            )
            for name in sorted(files):
                if cancelled():
                    return
                path = directory / name
                if allowed(path) and not path.is_symlink():
                    yield path


def read_plain(path: Path) -> str:
    # Never follow a symlink, even if a file changed between the directory scan and open.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Not a regular file")
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("File exceeds the 25 MB limit")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    if b"\x00" in data:
        raise ValueError("Binary data in a text file")
    return data.decode("utf-8-sig", errors="replace")


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def read_document(path: Path) -> Iterator[Passage]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError("Password-protected PDF")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError("PDF exceeds the 500-page limit")
        total = 0
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            total += len(text)
            if total > MAX_TEXT_CHARS:
                raise ValueError("Document exceeds the one-million-character limit")
            yield Passage(text, f"Page {number}")
        return
    if suffix in {".docx", ".odt"}:
        member = "word/document.xml" if suffix == ".docx" else "content.xml"
        with zipfile.ZipFile(path) as archive:
            if archive.getinfo(member).file_size > MAX_FILE_BYTES:
                raise ValueError("Expanded document exceeds the 25 MB limit")
            root = ET.fromstring(archive.read(member))
        # Preserve paragraph boundaries; join Word runs without inserting spaces inside words.
        paragraphs = []
        for element in root.iter():
            tag = element.tag.rsplit("}", 1)[-1]
            if tag in ({"p"} if suffix == ".docx" else {"p", "h"}):
                paragraphs.append("".join(element.itertext()))
        text = "\n".join(paragraphs)
    else:
        text = read_plain(path)
        if suffix in {".html", ".htm"}:
            parser = _HTMLText()
            parser.feed(text)
            text = "".join(parser.parts)
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("Document exceeds the one-million-character limit")
    yield Passage(text)


def chunk_passage(passage: Passage, size: int = 900, overlap: int = 120) -> Iterator[Passage]:
    """Bounded, overlapping passages; retain PDF page and plain-text line locations."""
    if not 0 <= overlap < size:
        raise ValueError("Overlap must be smaller than chunk size")
    text = passage.text.replace("\r\n", "\n").replace("\r", "\n")
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = max(
                text.rfind("\n", start + size // 2, end), text.rfind(" ", start + size // 2, end)
            )
            if boundary > start:
                end = boundary
        content = re.sub(r"[ \t]+", " ", text[start:end]).strip()
        if content:
            location = passage.location or f"Line {text.count(chr(10), 0, start) + 1}"
            yield Passage(content, location)
        if end == len(text):
            break
        next_start = max(start + 1, end - overlap)
        if next_start > 0 and not text[next_start - 1].isspace():
            boundary = re.search(r"\s+", text[next_start:end])
            if boundary:
                next_start += boundary.end()
        start = next_start
