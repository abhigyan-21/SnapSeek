"""Focused tests for persistent incremental PDF indexing."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from snapseek.pdf_extractor import PDFExtractionCancelled, PDFExtractionStatus
from snapseek.filesystem_monitor import ChangeEvent
from snapseek.ui.operations import build_reconcile_task, build_watch_task
from snapseek.unified_index import LocalIndex


def _write_pdf(path: Path, pages: list[str]) -> None:
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=300, height=300)
        if text:
            stream = DecodedStreamObject()
            stream.set_data(f"BT ({text}) Tj ET".encode())
            page[NameObject("/Contents")] = stream
    with path.open("wb") as stream:
        writer.write(stream)


class PDFIndexingTests(unittest.TestCase):
    def test_schema_migration_and_reopen_preserve_existing_items(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "index.sqlite3"
            with LocalIndex(database) as index:
                file_path = Path(temporary) / "notes.txt"
                file_path.write_text("notes", encoding="utf-8")
                index.index_single_file(file_path, stability_check=False)
                version = index.connection.execute(
                    "SELECT version FROM schema_version"
                ).fetchone()["version"]
                self.assertEqual(version, 4)
                self.assertIsNotNone(
                    index.connection.execute(
                        "SELECT name FROM sqlite_master WHERE name='pdf_pages'"
                    ).fetchone()
                )
            with LocalIndex(database) as reopened:
                self.assertEqual(len(reopened.search("notes", "files")), 1)

    def test_version_one_database_migrates_without_losing_items(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "legacy.sqlite3"
            import sqlite3

            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE schema_version (version INTEGER NOT NULL);
                INSERT INTO schema_version VALUES (1);
                CREATE TABLE items (
                    path TEXT PRIMARY KEY, item_type TEXT NOT NULL,
                    name TEXT NOT NULL, extension TEXT NOT NULL,
                    parent_path TEXT NOT NULL, size_bytes INTEGER NOT NULL,
                    modified_ns INTEGER NOT NULL, launch_target TEXT,
                    ocr_text TEXT NOT NULL DEFAULT '', embedding BLOB,
                    indexed_at REAL NOT NULL
                );
                INSERT INTO items VALUES
                    ('c:\\legacy.txt', 'file', 'legacy.txt', '.txt', 'c:',
                     6, 1, NULL, 'legacy', NULL, 1);
                """
            )
            connection.commit()
            connection.close()
            with LocalIndex(database) as index:
                self.assertEqual(
                    index.connection.execute(
                        "SELECT version FROM schema_version"
                    ).fetchone()["version"],
                    4,
                )
                self.assertEqual(len(index.search("legacy", "files")), 1)

    def test_indexes_pages_and_skips_unchanged_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manual.pdf"
            _write_pdf(path, ["first page", "second page"])
            with LocalIndex(Path(temporary) / "index.sqlite3") as index:
                first = index.index_pdf(path)
                second = index.index_pdf(path)
                self.assertEqual(first.action, "indexed")
                self.assertEqual(second.action, "skipped")
                rows = index.connection.execute(
                    """
                    SELECT page_number, text, has_text FROM pdf_pages
                    WHERE document_path = ? ORDER BY page_number
                    """,
                    (LocalIndex._key(path),),
                ).fetchall()
                self.assertEqual([row["page_number"] for row in rows], [1, 2])
                self.assertTrue(all(row["has_text"] for row in rows))

    def test_modified_pdf_replaces_old_pages_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manual.pdf"
            _write_pdf(path, ["old", "page"])
            with LocalIndex(Path(temporary) / "index.sqlite3") as index:
                index.index_pdf(path)
                _write_pdf(path, ["new"])
                outcome = index.index_pdf(path)
                rows = index.connection.execute(
                    "SELECT page_number, text FROM pdf_pages WHERE document_path = ?",
                    (LocalIndex._key(path),),
                ).fetchall()
                self.assertEqual(outcome.action, "indexed")
                self.assertEqual(len(rows), 1)
                self.assertIn("new", rows[0]["text"])

    def test_force_integrity_check_detects_same_metadata_content_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manual.pdf"
            _write_pdf(path, ["old"])
            with LocalIndex(Path(temporary) / "index.sqlite3") as index:
                index.index_pdf(path)
                _write_pdf(path, ["new"])
                outcome = index.index_pdf(path, force_integrity_check=True)
                self.assertEqual(outcome.action, "indexed")

    def test_statuses_persist_without_false_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            encrypted = root / "encrypted.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=300, height=300)
            writer.encrypt("secret")
            with encrypted.open("wb") as stream:
                writer.write(stream)
            corrupt = root / "corrupt.pdf"
            corrupt.write_bytes(b"%PDF-1.7\ninvalid")
            image_only = root / "image-only.pdf"
            _write_pdf(image_only, [""])
            with LocalIndex(root / "index.sqlite3") as index:
                for path, status in (
                    (encrypted, PDFExtractionStatus.ENCRYPTED),
                    (corrupt, PDFExtractionStatus.CORRUPT),
                    (image_only, PDFExtractionStatus.OCR_REQUIRED),
                ):
                    outcome = index.index_pdf(path)
                    self.assertEqual(outcome.status, status)
                    row = index.connection.execute(
                        "SELECT extraction_status FROM pdf_documents WHERE path = ?",
                        (LocalIndex._key(path),),
                    ).fetchone()
                    self.assertEqual(row["extraction_status"], status.value)

    def test_transient_extraction_failure_preserves_previous_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manual.pdf"
            _write_pdf(path, ["keep this"])
            with LocalIndex(Path(temporary) / "index.sqlite3") as index:
                index.index_pdf(path)
                with patch(
                    "snapseek.unified_index.extract_pdf_text",
                    return_value=type(
                        "Result",
                        (),
                        {
                            "status": PDFExtractionStatus.INACCESSIBLE,
                            "error": "locked",
                        },
                    )(),
                ), patch.object(
                    index,
                    "_pdf_fingerprint",
                    return_value=(path.stat().st_size, path.stat().st_mtime_ns, "changed"),
                ):
                    outcome = index.index_pdf(path, force_integrity_check=True)
                self.assertEqual(outcome.action, "deferred")
                row = index.connection.execute(
                    "SELECT text FROM pdf_pages WHERE document_path = ?",
                    (LocalIndex._key(path),),
                ).fetchone()
                self.assertIn("keep", row["text"])

    def test_pdf_delete_removes_document_pages_and_generic_item(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manual.pdf"
            _write_pdf(path, ["remove me"])
            with LocalIndex(Path(temporary) / "index.sqlite3") as index:
                index.index_pdf(path)
                path.unlink()
                self.assertTrue(index.remove_item(path))
                key = LocalIndex._key(path)
                self.assertIsNone(
                    index.connection.execute(
                        "SELECT 1 FROM pdf_documents WHERE path = ?", (key,)
                    ).fetchone()
                )
                self.assertIsNone(
                    index.connection.execute(
                        "SELECT 1 FROM pdf_pages WHERE document_path = ?", (key,)
                    ).fetchone()
                )

    def test_rename_indexes_destination_before_removing_pdf_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "before.pdf"
            destination = root / "after.pdf"
            _write_pdf(source, ["renamed"])
            with LocalIndex(root / "index.sqlite3") as index:
                index.index_pdf(source)
                source.rename(destination)
                from snapseek.filesystem_monitor import ChangeEvent
                from snapseek.ui.operations import build_watch_task

                result = build_watch_task(
                    root,
                    index=index,
                    pending_events=frozenset(
                        [ChangeEvent("moved", str(source), str(destination))]
                    ),
                )(threading.Event())
                self.assertEqual(result, 1)
                self.assertIsNone(
                    index.connection.execute(
                        "SELECT 1 FROM pdf_documents WHERE path = ?",
                        (LocalIndex._key(source),),
                    ).fetchone()
                )
                self.assertIsNotNone(
                    index.connection.execute(
                        "SELECT 1 FROM pdf_documents WHERE path = ?",
                        (LocalIndex._key(destination),),
                    ).fetchone()
                )

    def test_cancellation_during_hash_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manual.pdf"
            _write_pdf(path, ["cancel"])
            cancel = threading.Event()
            cancel.set()
            with LocalIndex(Path(temporary) / "index.sqlite3") as index:
                with self.assertRaises(PDFExtractionCancelled):
                    index.index_pdf(path, cancelled=cancel.is_set)
                self.assertIsNone(
                    index.connection.execute(
                        "SELECT 1 FROM pdf_documents"
                    ).fetchone()
                )

    def test_watch_and_reconcile_index_pdf_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "watched.pdf"
            _write_pdf(path, ["watched"])
            with LocalIndex(root / "index.sqlite3") as index:
                task = build_watch_task(
                    root,
                    index=index,
                    pending_events=frozenset(
                        [ChangeEvent("created", str(path))]
                    ),
                )
                self.assertEqual(task(threading.Event()), 1)
                path.unlink()
                self.assertEqual(
                    build_watch_task(
                        root,
                        index=index,
                        pending_events=frozenset(
                            [ChangeEvent("deleted", str(path))]
                        ),
                    )(threading.Event()),
                    1,
                )
                self.assertIsNone(
                    index.connection.execute(
                        "SELECT 1 FROM pdf_documents"
                    ).fetchone()
                )
                _write_pdf(path, ["reconciled"])
                self.assertGreaterEqual(
                    build_reconcile_task(root, index=index)(threading.Event()),
                    1,
                )
                self.assertIsNotNone(
                    index.connection.execute(
                        "SELECT 1 FROM pdf_documents"
                    ).fetchone()
                )


if __name__ == "__main__":
    unittest.main()
