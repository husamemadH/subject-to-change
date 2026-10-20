from __future__ import annotations

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

from subject_to_change.readers import Passage, allowed, chunk_passage, media_kind, read_document


class ReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_media_and_code_extensions_are_accepted(self) -> None:
        for name, kind in (("photo.JPG", "image"), ("clip.webm", "video"), ("sound.flac", "audio")):
            self.assertTrue(allowed(Path(name)))
            self.assertEqual(media_kind(Path(name)), kind)
        for name in ("app.py", "app.ts", "config.yaml", "data.json"):
            self.assertTrue(allowed(Path(name)))
            self.assertIsNone(media_kind(Path(name)))
        self.assertFalse(allowed(Path("program.exe")))

    def test_chunks_cover_all_words_with_overlap_and_page_locations(self) -> None:
        text = " ".join(f"word{i:04}" for i in range(500))
        chunks = list(chunk_passage(Passage(text, "Page 3")))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.text) <= 900 for chunk in chunks))
        self.assertTrue(all(chunk.location == "Page 3" for chunk in chunks))
        self.assertEqual(set(text.split()), set(" ".join(c.text for c in chunks).split()))

    def test_html_does_not_index_scripts_or_styles(self) -> None:
        path = self.root / "note.html"
        path.write_text("<style>hidden</style><p>Lease &amp; rent</p><script>secret</script>")
        text = next(read_document(path)).text
        self.assertIn("Lease & rent", text)
        self.assertNotIn("secret", text)
        self.assertNotIn("hidden", text)

    def test_docx_run_boundaries_do_not_split_words(self) -> None:
        path = self.root / "note.docx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "word/document.xml",
                "<document><p><r><t>in</t></r><r><t>voice</t></r></p></document>",
            )
        self.assertEqual(next(read_document(path)).text, "invoice")

    def test_odt_reads_paragraphs(self) -> None:
        path = self.root / "note.odt"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("content.xml", "<document><p>First</p><p>Second</p></document>")
        self.assertEqual(next(read_document(path)).text, "First\nSecond")

    def test_utf16_text_is_supported(self) -> None:
        path = self.root / "note.txt"
        path.write_text("عقد إيجار", encoding="utf-16")
        self.assertEqual(next(read_document(path)).text, "عقد إيجار")

    def test_binary_mislabeled_as_text_is_rejected(self) -> None:
        path = self.root / "binary.txt"
        path.write_bytes(b"not\x00text")
        with self.assertRaisesRegex(ValueError, "Binary"):
            list(read_document(path))

    def test_plain_text_has_line_locations(self) -> None:
        chunks = list(
            chunk_passage(Passage("First line\nSecond line\nThird line"), size=16, overlap=0)
        )
        self.assertEqual(chunks[0].location, "Line 1")
        self.assertTrue(any(chunk.location != "Line 1" for chunk in chunks))

    @unittest.skipUnless(importlib.util.find_spec("pypdf"), "pypdf is not installed")
    def test_pdf_extraction_retains_page_number(self) -> None:
        # A minimal self-contained PDF fixture with actual text operators and a correct xref table.
        path = self.root / "note.pdf"
        text = b"BT /F1 12 Tf 72 720 Td (Invoice PDF123) Tj ET"
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length " + str(len(text)).encode() + b" >>\nstream\n" + text + b"\nendstream",
        ]
        data = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for number, content in enumerate(objects, 1):
            offsets.append(len(data))
            data.extend(f"{number} 0 obj\n".encode() + content + b"\nendobj\n")
        xref = len(data)
        data.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
        for offset in offsets[1:]:
            data.extend(f"{offset:010} 00000 n \n".encode())
        data.extend(
            f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
        )
        path.write_bytes(data)
        passages = list(read_document(path))
        self.assertIn("Invoice PDF123", passages[0].text)
        self.assertEqual(passages[0].location, "Page 1")


if __name__ == "__main__":
    unittest.main()
