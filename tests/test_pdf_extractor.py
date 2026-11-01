"""Focused tests for standalone local PDF extraction."""

from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from pypdf import PdfWriter

from snapseek.pdf_extractor import (
    PDFExtractionCancelled,
    PDFExtractionStatus,
    extract_pdf_text,
)


def _write_pdf(path: Path, pages: list[str], *, title: str | None = None) -> None:
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=300, height=300)
        if text:
            from pypdf.generic import ContentStream, NameObject

            page[NameObject("/Contents")] = ContentStream([], writer)
    if title is not None:
        writer.add_metadata({"/Title": title, "/Author": "SnapSeek"})
    with path.open("wb") as stream:
        writer.write(stream)


class PDFExtractorTests(unittest.TestCase):
    def test_single_and_multiple_pages_preserve_page_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "document.pdf"
            writer = PdfWriter()
            first = writer.add_blank_page(width=300, height=300)
            second = writer.add_blank_page(width=300, height=300)
            from pypdf.generic import ArrayObject, DecodedStreamObject, NameObject

            for page, text in ((first, "first page"), (second, "second page")):
                stream = DecodedStreamObject()
                stream.set_data(f"BT ({text}) Tj ET".encode())
                page[NameObject("/Contents")] = stream
            with path.open("wb") as stream:
                writer.write(stream)

            result = extract_pdf_text(path)

            self.assertEqual(result.status, PDFExtractionStatus.OK)
            self.assertEqual([page.page_number for page in result.pages], [1, 2])
            self.assertEqual([page.text.strip() for page in result.pages], ["first page", "second page"])
            self.assertEqual(result.metadata.page_count, 2)

    def test_metadata_and_file_metadata_are_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "metadata.pdf"
            writer = PdfWriter()
            page = writer.add_blank_page(width=300, height=300)
            from pypdf.generic import DecodedStreamObject, NameObject

            content = DecodedStreamObject()
            content.set_data(b"BT (metadata text) Tj ET")
            page[NameObject("/Contents")] = content
            writer.add_metadata({"/Title": "A title", "/Author": "An author"})
            with path.open("wb") as stream:
                writer.write(stream)

            result = extract_pdf_text(path)
            stat = path.stat()

            self.assertEqual(result.metadata.title, "A title")
            self.assertEqual(result.metadata.author, "An author")
            self.assertEqual(result.metadata.size_bytes, stat.st_size)
            self.assertEqual(result.metadata.modified_ns, stat.st_mtime_ns)

    def test_empty_and_image_only_documents_require_distinct_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            empty = Path(temporary) / "empty.pdf"
            image_only = Path(temporary) / "image-only.pdf"
            _write_pdf(empty, [])
            _write_pdf(image_only, ["", ""])

            empty_result = extract_pdf_text(empty)
            image_result = extract_pdf_text(image_only)

            self.assertEqual(empty_result.status, PDFExtractionStatus.EMPTY)
            self.assertEqual(image_result.status, PDFExtractionStatus.OCR_REQUIRED)
            self.assertTrue(image_result.ocr_required)
            self.assertEqual(len(image_result.pages), 2)

    def test_mixed_text_and_empty_pages_are_ok(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "mixed.pdf"
            writer = PdfWriter()
            text_page = writer.add_blank_page(width=300, height=300)
            writer.add_blank_page(width=300, height=300)
            from pypdf.generic import DecodedStreamObject, NameObject

            content = DecodedStreamObject()
            content.set_data(b"BT (text page) Tj ET")
            text_page[NameObject("/Contents")] = content
            with path.open("wb") as stream:
                writer.write(stream)

            result = extract_pdf_text(path)

            self.assertEqual(result.status, PDFExtractionStatus.OK)
            self.assertTrue(result.pages[0].has_text)
            self.assertFalse(result.pages[1].has_text)

    def test_encrypted_corrupt_unsupported_and_missing_inputs_are_classified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            encrypted = root / "encrypted.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=300, height=300)
            writer.encrypt("secret")
            with encrypted.open("wb") as stream:
                writer.write(stream)
            corrupt = root / "corrupt.pdf"
            corrupt.write_bytes(b"%PDF-1.7\nnot a valid document")
            unsupported = root / "not-a-pdf.pdf"
            unsupported.write_bytes(b"plain text")

            self.assertEqual(extract_pdf_text(encrypted).status, PDFExtractionStatus.ENCRYPTED)
            self.assertEqual(extract_pdf_text(corrupt).status, PDFExtractionStatus.CORRUPT)
            self.assertEqual(extract_pdf_text(unsupported).status, PDFExtractionStatus.UNSUPPORTED)
            self.assertEqual(
                extract_pdf_text(root / "missing.pdf").status,
                PDFExtractionStatus.INACCESSIBLE,
            )
            self.assertEqual(
                extract_pdf_text(root / "file.txt").status,
                PDFExtractionStatus.UNSUPPORTED,
            )

    def test_inaccessible_file_is_classified_without_opening(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "locked.pdf"
            path.write_bytes(b"%PDF-1.7")
            with patch.object(Path, "stat", side_effect=PermissionError("locked")):
                result = extract_pdf_text(path)
            self.assertEqual(result.status, PDFExtractionStatus.INACCESSIBLE)

    def test_cancellation_before_and_during_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cancel.pdf"
            _write_pdf(path, ["", ""])
            with self.assertRaises(PDFExtractionCancelled):
                extract_pdf_text(path, cancelled=lambda: True)
            calls = 0

            def cancel_after_first_page() -> bool:
                nonlocal calls
                calls += 1
                return calls > 1

            with self.assertRaises(PDFExtractionCancelled):
                extract_pdf_text(path, cancelled=cancel_after_first_page)

    def test_source_bytes_are_unchanged_and_large_pdf_does_not_use_read_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "large.pdf"
            _write_pdf(path, [""] * 20)
            before = path.read_bytes()
            with patch.object(Path, "read_bytes", side_effect=AssertionError("not allowed")):
                result = extract_pdf_text(path)
            self.assertEqual(result.status, PDFExtractionStatus.OCR_REQUIRED)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
