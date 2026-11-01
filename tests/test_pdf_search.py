"""Focused tests for Stage 9.3 PDF keyword search."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from snapseek.unified_index import LocalIndex, PDFSearchResult


def _write_pdf(path: Path, pages: list[str]) -> None:
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=300, height=300)
        stream = DecodedStreamObject()
        stream.set_data(f"BT ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = stream
    with path.open("wb") as stream:
        writer.write(stream)


class PDFSearchTests(unittest.TestCase):
    def test_fts_capability_and_page_search(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Guide.pdf"
            _write_pdf(path, ["Build Pipeline overview", "Nothing relevant"])
            with LocalIndex(Path(temporary) / "index.sqlite3") as index:
                self.assertTrue(index._pdf_fts_available)
                index.index_pdf(path)
                results = index.search_pdfs("build pipeline")
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0].page_number, 1)
                self.assertIn("Build Pipeline", results[0].snippet)

    def test_case_insensitive_terms_prefix_and_punctuation_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Guide.pdf"
            _write_pdf(path, ["Error handling and deployment"])
            with LocalIndex(Path(temporary) / "index.sqlite3") as index:
                index.index_pdf(path)
                self.assertEqual(len(index.search_pdfs("ERROR")), 1)
                self.assertEqual(len(index.search_pdfs("deploy")), 1)
                self.assertEqual(len(index.search_pdfs('error " deploy')), 1)

    def test_results_are_ranked_and_capped_per_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Guide.pdf"
            _write_pdf(
                path,
                ["rare phrase", "common common", "rare phrase again", "rare phrase"],
            )
            with LocalIndex(Path(temporary) / "index.sqlite3") as index:
                index.index_pdf(path)
                results = index.search_pdfs("rare phrase", limit=20)
                self.assertEqual(len(results), 3)
                self.assertEqual([result.page_number for result in results], [1, 3, 4])
                self.assertGreaterEqual(results[0].score, results[-1].score)

    def test_non_searchable_statuses_and_empty_queries_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "empty.pdf"
            _write_pdf(path, [""])
            with LocalIndex(Path(temporary) / "index.sqlite3") as index:
                index.index_pdf(path)
                self.assertEqual(index.search_pdfs("anything"), [])
                self.assertEqual(index.search_pdfs(""), [])

    def test_search_does_not_extract_or_read_the_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Guide.pdf"
            _write_pdf(path, ["local text"])
            with LocalIndex(Path(temporary) / "index.sqlite3") as index:
                index.index_pdf(path)
                path.unlink()
                results = index.search_pdfs("local")
                self.assertEqual(len(results), 1)
                self.assertIsInstance(results[0], PDFSearchResult)

    def test_v2_database_migrates_to_v4_fts_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "index.sqlite3"
            with LocalIndex(database) as index:
                index.connection.execute(
                    "UPDATE schema_version SET version = 2"
                )
                index.connection.execute("DROP TABLE pdf_pages_fts")
                index.connection.commit()
            with LocalIndex(database) as index:
                self.assertEqual(
                    index.connection.execute(
                        "SELECT version FROM schema_version"
                    ).fetchone()["version"],
                    4,
                )
                self.assertTrue(index._pdf_fts_available)
                columns = {
                    row["name"]
                    for row in index.connection.execute(
                        "PRAGMA table_info(pdf_pages)"
                    ).fetchall()
                }
                self.assertTrue({"extracted_text", "ocr_text", "text_source"} <= columns)


if __name__ == "__main__":
    unittest.main()
