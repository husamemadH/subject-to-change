from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from subject_to_change.config import AppPaths
from subject_to_change.engine import Engine
from subject_to_change.storage import Store


class FixtureEncoder:
    """Fixed concept vectors for integration tests; never offered as a production model."""

    identity = "fixture-concepts-v1"
    dimensions = 4

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], bool]] = []

    def encode(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        self.calls.append((texts, query))
        return [
            [
                float(any(word in text.lower() for word in group))
                for group in (
                    ("cat", "kitten", "قط"),
                    ("rent", "lease", "إيجار"),
                    ("invoice", "payment"),
                    ("garden", "flowers"),
                )
            ]
            if any(
                word in text.lower()
                for word in (
                    "cat",
                    "kitten",
                    "قط",
                    "rent",
                    "lease",
                    "إيجار",
                    "invoice",
                    "payment",
                    "garden",
                    "flowers",
                )
            )
            else [0.1, 0.1, 0.1, 1.0]
            for text in texts
        ]


class EngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.root = base / "files"
        self.root.mkdir()
        self.paths = AppPaths(base / "data")
        self.encoder = FixtureEncoder()
        self.engine = Engine(self.paths, self.encoder)

    def file(self, name: str, content: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_progress_counts_unchanged_and_unreadable_files(self) -> None:
        self.file("ready.txt", "cat")
        self.engine.index([self.root], semantic=False)
        self.file("broken.pdf", "not a pdf")
        events = []
        report = self.engine.index([self.root], semantic=False, on_progress=events.append)
        self.assertEqual((report.unchanged, report.skipped), (1, 1))
        self.assertEqual((events[-1].completed, events[-1].total), (2, 2))
        self.assertEqual(events[-1].phase, "Finished")
        self.assertEqual([e.completed for e in events], sorted(e.completed for e in events))

    def test_progress_updates_each_embedding_batch(self) -> None:
        self.file("long.txt", "cat garden " * 3000)
        events = []
        self.engine.index([self.root], on_progress=events.append)
        batches = [e for e in events if e.phase == "Embedding"]
        self.assertGreater(len(batches), 2)
        self.assertEqual(batches[0].passages_done, 0)
        self.assertEqual(batches[1].passages_done, 16)
        self.assertEqual(batches[-1].passages_done, batches[-1].passages_total)
        self.assertTrue(all(e.completed == 0 and e.total == 1 for e in batches))
        self.assertEqual(events[-1].completed, 1)

    def test_cancelled_progress_does_not_claim_completion(self) -> None:
        self.file("long.txt", "cat garden " * 3000)
        cancel = Event()
        events = []

        def update(event):
            events.append(event)
            if event.passages_done >= 16:
                cancel.set()

        report = self.engine.index([self.root], cancel=cancel, on_progress=update)
        self.assertTrue(report.cancelled)
        self.assertEqual(events[-1].phase, "Stopped")
        self.assertEqual(events[-1].completed, 0)
        self.assertEqual(self.engine.stats()["files"], 0)

    def test_native_media_vectors_and_timestamps_are_indexed(self) -> None:
        from subject_to_change.media import MediaPart

        self.file("recording.mp4", "video fixture")
        encoder = self.encoder
        encoder.encode_media = lambda inputs: [0.0, 1.0, 0.0, 0.0]
        parts = [
            MediaPart({"video": ["frame1"]}, "Video · 00:00:00–00:00:10", 1, 2),
            MediaPart({"video": ["frame2"]}, "Video · 00:00:10–00:00:20", 2, 2),
        ]
        events = []
        with patch("subject_to_change.engine.read_media", return_value=iter(parts)):
            report = self.engine.index([self.root], on_progress=events.append)
        self.assertEqual(report.indexed, 1)
        self.assertEqual(self.engine.stats()["embedded"], 2)
        results = self.engine.search("lease")
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].location.startswith("Video · "))
        clips = [e for e in events if e.phase == "Embedding video"]
        self.assertEqual(clips[-1].passages_done, 2)
        with patch("subject_to_change.engine.read_media", side_effect=AssertionError("Unchanged")):
            self.assertEqual(self.engine.index([self.root]).unchanged, 1)

    def test_corrupt_media_is_reported_without_stopping_other_files(self) -> None:
        from subject_to_change.media import MediaReadError

        self.file("broken.jpg", "corrupt image")
        self.file("note.txt", "cat")
        with patch(
            "subject_to_change.engine.read_media", side_effect=MediaReadError("Invalid image")
        ):
            report = self.engine.index([self.root])
        self.assertEqual((report.indexed, report.skipped), (1, 1))
        self.assertIn("Invalid image", report.errors[0])

    def test_keyword_mode_indexes_media_filenames_without_decoding(self) -> None:
        self.file("garden_flowers.jpg", "not decoded")
        with patch("subject_to_change.engine.read_media", side_effect=AssertionError("No decoder")):
            report = self.engine.index([self.root], semantic=False)
        self.assertEqual(report.indexed, 1)
        self.assertEqual(len(self.engine.search("flowers", semantic=False)), 1)
        self.assertEqual(self.engine.stats()["embedded"], 0)

    def test_stats_remain_readable_while_another_connection_is_writing(self) -> None:
        self.file("note.txt", "invoice")
        self.engine.index([self.root], semantic=False)
        with Store(self.paths.database) as writer:
            writer.connection.execute("BEGIN IMMEDIATE")
            self.assertEqual(self.engine.stats()["files"], 1)
            writer.connection.rollback()

    def test_keyword_index_search_requires_no_model(self) -> None:
        path = self.file("فاتورة invoice.txt", "Payment declined. Invoice ABC-123 is overdue.")
        with patch("socket.create_connection", side_effect=AssertionError("Network forbidden")):
            report = self.engine.index([self.root], semantic=False)
            results = self.engine.search('"ABC-123"', semantic=False)
        self.assertEqual(report.indexed, 1)
        self.assertEqual(results[0].path, str(path))
        self.assertEqual(results[0].match, "Exact words")
        self.assertEqual(self.encoder.calls, [])
        self.assertFalse(self.paths.model.exists())

    def test_semantic_search_finds_synonym_and_arabic_query(self) -> None:
        target = self.file("pets.txt", "A kitten sleeps on the sofa.")
        self.file("plants.txt", "Garden flowers need water.")
        self.engine.index([self.root])
        self.assertEqual(self.engine.search("cat")[0].path, str(target))
        self.assertEqual(self.engine.search("قط")[0].path, str(target))
        self.assertTrue(self.encoder.calls[-1][1])

    def test_incremental_index_does_not_reembed_unchanged_files(self) -> None:
        self.file("pets.txt", "A cat lives here.")
        self.engine.index([self.root])
        calls = len(self.encoder.calls)
        report = self.engine.index([self.root])
        self.assertEqual(report.unchanged, 1)
        self.assertEqual(report.indexed, 0)
        self.assertEqual(len(self.encoder.calls), calls)

    def test_changed_file_replaces_text_and_keywords(self) -> None:
        path = self.file("notes.txt", "Invoice identifier OLD123.")
        self.engine.index([self.root], semantic=False)
        path.write_text("Lease identifier NEW456 with additional text.", encoding="utf-8")
        self.engine.index([self.root], semantic=False)
        self.assertEqual(self.engine.search("OLD123", semantic=False), [])
        self.assertEqual(len(self.engine.search("NEW456", semantic=False)), 1)

    def test_deleted_files_are_removed_from_both_indexes(self) -> None:
        path = self.file("notes.txt", "Invoice ABC123.")
        self.engine.index([self.root])
        path.unlink()
        report = self.engine.index([self.root])
        self.assertEqual(report.removed, 1)
        self.assertEqual(self.engine.stats()["passages"], 0)
        self.assertEqual(self.engine.search("ABC123", semantic=False), [])

    def test_keyword_index_can_be_upgraded_without_changing_files(self) -> None:
        self.file("notes.txt", "The rent is due.")
        self.engine.index([self.root], semantic=False)
        with self.assertRaisesRegex(ValueError, "Exact words"):
            self.engine.search("lease")
        report = self.engine.index([self.root])
        self.assertEqual(report.indexed, 1)
        self.assertGreater(self.engine.stats()["embedded"], 0)
        self.assertEqual(len(self.engine.search("lease")), 1)

    def test_cancel_does_not_prune_unvisited_files(self) -> None:
        self.file("one.txt", "Invoice ABC123")
        self.file("two.txt", "Rent is due")
        self.engine.index([self.root], semantic=False)
        cancel = Event()
        cancel.set()
        report = self.engine.index([self.root], semantic=False, cancel=cancel)
        self.assertTrue(report.cancelled)
        self.assertEqual(self.engine.stats()["files"], 2)

    def test_cancel_during_embedding_keeps_last_complete_document(self) -> None:
        self.file("notes.txt", "Original lease terms.")
        self.engine.index([self.root])
        self.file("notes.txt", "Revised lease terms with many extra words.")
        cancel = Event()
        original = self.encoder.encode

        def stop(texts, *, query=False):
            cancel.set()
            return original(texts, query=query)

        with patch.object(self.encoder, "encode", side_effect=stop):
            report = self.engine.index([self.root], cancel=cancel)
        self.assertTrue(report.cancelled)
        self.assertEqual(len(self.engine.search("Original", semantic=False)), 1)
        self.assertEqual(self.engine.search("Revised", semantic=False), [])

    def test_model_failure_preserves_previous_file(self) -> None:
        self.file("notes.txt", "Original lease terms.")
        self.engine.index([self.root])
        self.file("notes.txt", "New lease terms with different contents.")
        with patch.object(self.encoder, "encode", side_effect=RuntimeError("model failed")):
            with self.assertRaisesRegex(RuntimeError, "model failed"):
                self.engine.index([self.root])
        self.assertEqual(len(self.engine.search("Original", semantic=False)), 1)

    def test_invalid_embedding_is_reported_as_a_model_failure(self) -> None:
        self.file("notes.txt", "Invoice ABC123")
        with patch.object(self.encoder, "encode", return_value=[[float("nan")] * 4]):
            with self.assertRaisesRegex(ValueError, "invalid embedding"):
                self.engine.index([self.root])
        self.assertEqual(self.engine.stats()["files"], 0)

    def test_bad_document_does_not_stop_other_files(self) -> None:
        self.file("broken.docx", "This is not a ZIP archive")
        self.file("notes.txt", "Invoice ABC123")
        report = self.engine.index([self.root], semantic=False)
        self.assertEqual(report.indexed, 1)
        self.assertEqual(report.skipped, 1)
        self.assertTrue(report.errors)
        self.assertEqual(len(self.engine.search("ABC123", semantic=False)), 1)

    def test_hidden_symlink_private_and_build_files_are_skipped(self) -> None:
        self.file("public.txt", "Public invoice")
        self.file(".hidden.txt", "Secret needle")
        self.file(".ssh/notes.txt", "Secret needle")
        self.file("node_modules/notes.txt", "Secret needle")
        self.file("credentials.json", "Secret needle")
        self.file("unsupported.bin", "Secret needle")
        outside = self.root.parent / "outside.txt"
        outside.write_text("Secret needle", encoding="utf-8")
        try:
            (self.root / "link.txt").symlink_to(outside)
        except OSError:
            pass  # Windows runners may not grant symlink privileges.
        self.engine.index([self.root], semantic=False)
        self.assertEqual(self.engine.stats()["files"], 1)
        self.assertEqual(self.engine.search("needle", semantic=False), [])

    def test_empty_selection_does_not_clear_index(self) -> None:
        self.file("notes.txt", "Invoice ABC123")
        self.engine.index([self.root], semantic=False)
        with self.assertRaises(ValueError):
            self.engine.index([], semantic=False)
        self.assertEqual(self.engine.stats()["files"], 1)

    def test_missing_root_does_not_clear_index(self) -> None:
        self.file("notes.txt", "Invoice ABC123")
        self.engine.index([self.root], semantic=False)
        with self.assertRaises(FileNotFoundError):
            self.engine.index([self.root, self.root / "offline"], semantic=False)
        self.assertEqual(self.engine.stats()["files"], 1)

    def test_scan_error_does_not_prune_inaccessible_subtree(self) -> None:
        self.file("blocked/notes.txt", "Invoice ABC123")
        self.engine.index([self.root], semantic=False)

        def denied(roots, excluded, on_error, cancelled):
            on_error(PermissionError(13, "Permission denied", str(self.root / "blocked")))
            return iter(())

        with patch("subject_to_change.engine.walk_files", side_effect=denied):
            report = self.engine.index([self.root], semantic=False)
        self.assertTrue(report.errors)
        self.assertEqual(self.engine.stats()["files"], 1)

    def test_removed_folder_disappears_without_touching_originals(self) -> None:
        path = self.file("notes.txt", "Invoice ABC123")
        self.engine.index([self.root], semantic=False)
        self.engine.retain_folders([])
        self.assertEqual(self.engine.stats()["files"], 0)
        self.assertTrue(path.exists())

    def test_data_directory_is_never_indexed(self) -> None:
        self.paths.data.mkdir()
        (self.paths.data / "private.txt").write_text("private needle", encoding="utf-8")
        self.file("notes.txt", "Invoice ABC123")
        self.engine.index([self.root.parent], semantic=False)
        self.assertEqual(self.engine.search("needle", semantic=False), [])

    def test_model_identity_mismatch_fails_before_search(self) -> None:
        self.file("notes.txt", "Invoice ABC123")
        self.engine.index([self.root])
        self.encoder.identity = "another-model"
        with self.assertRaisesRegex(ValueError, "different model"):
            self.engine.search("payment")
        with self.assertRaisesRegex(ValueError, "different model"):
            self.engine.index([self.root])

    def test_search_input_is_not_fts_syntax(self) -> None:
        self.file("notes.txt", "Invoice ABC123")
        self.engine.index([self.root], semantic=False)
        for query in ['"', "*", "invoice:ABC123", "NOT OR AND", "-()", "' OR 1=1 --"]:
            self.engine.search(query, semantic=False)

    def test_overlapping_roots_do_not_duplicate_files(self) -> None:
        self.file("nested/notes.txt", "Invoice ABC123")
        report = self.engine.index([self.root / "nested", self.root], semantic=False)
        self.assertEqual(report.indexed, 1)

    def test_clear_preserves_source_files(self) -> None:
        path = self.file("notes.txt", "Invoice ABC123")
        self.engine.index([self.root])
        self.engine.clear()
        self.assertTrue(path.exists())
        with Store(self.paths.database) as store:
            self.assertIsNone(store.model_identity())
        self.assertEqual(self.engine.stats()["files"], 0)

    def test_atomic_failure_cannot_replace_document(self) -> None:
        path = self.file("notes.txt", "Invoice ABC123")
        self.engine.index([self.root])
        with Store(self.paths.database) as store:
            from subject_to_change.readers import Passage

            with self.assertRaises(ValueError):
                store.replace(
                    path, self.root, 1, 1, "new", [Passage("bad")], [[float("nan")]], "new", 4
                )
        self.assertEqual(len(self.engine.search("ABC123", semantic=False)), 1)


if __name__ == "__main__":
    unittest.main()
