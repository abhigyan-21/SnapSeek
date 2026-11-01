"""Focused scanned-PDF OCR orchestration tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from snapseek.ocr import ImageOCRError
from snapseek.pdf_extractor import PDFExtractionStatus
from snapseek.unified_index import LocalIndex


class PDFOCRTests(unittest.TestCase):
    def test_meaningful_text_is_preserved_without_renderer_or_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "text.pdf"
            path.write_bytes(b"%PDF-ignored-for-mocked-extraction")
            result = type(
                "Result",
                (),
                {
                    "status": PDFExtractionStatus.OK,
                    "metadata": type(
                        "Metadata",
                        (),
                        {"title": None, "author": None, "page_count": 1},
                    )(),
                    "pages": (
                        type(
                            "Page",
                            (),
                            {
                                "page_number": 1,
                                "text": "Selectable text",
                                "has_text": True,
                            },
                        )(),
                    ),
                    "error": None,
                },
            )()
            with LocalIndex(Path(temporary) / "index.sqlite3") as index:
                with patch("snapseek.unified_index.extract_pdf_text", return_value=result), \
                     patch("snapseek.unified_index.PDFRenderer") as renderer, \
                     patch("snapseek.unified_index.extract_text_from_image") as ocr:
                    outcome = index.index_pdf(path, force_integrity_check=True)
            self.assertEqual(outcome.status, PDFExtractionStatus.OK)
            renderer.assert_not_called()
            ocr.assert_not_called()

    def test_transient_ocr_failure_preserves_previous_complete_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scan.pdf"
            path.write_bytes(b"%PDF-1.7 initial")
            with LocalIndex(Path(temporary) / "index.sqlite3") as index:
                with patch("snapseek.unified_index.extract_pdf_text") as extract, \
                     patch("snapseek.unified_index.PDFRenderer") as renderer, \
                     patch("snapseek.unified_index.extract_text_from_image") as ocr:
                    page = type(
                        "Page", (), {"page_number": 1, "text": "", "has_text": False}
                    )()
                    metadata = type(
                        "Metadata", (), {"title": None, "author": None, "page_count": 1}
                    )()
                    extract.return_value = type(
                        "Result",
                        (),
                        {
                            "status": PDFExtractionStatus.OCR_REQUIRED,
                            "metadata": metadata,
                            "pages": (page,),
                            "error": None,
                        },
                    )()
                    image = Image.new("RGB", (2, 2))
                    renderer.return_value.__enter__.return_value.render_page.return_value = image
                    ocr.side_effect = ImageOCRError("temporary OCR failure")
                    first = index.index_pdf(path, force_integrity_check=True)
                    self.assertEqual(first.action, "status-only")
                    row = index.connection.execute(
                        "SELECT extraction_status FROM pdf_documents"
                    ).fetchone()
                    self.assertEqual(row["extraction_status"], "ocr_required")
                    self.assertIsNone(
                        index.connection.execute("SELECT 1 FROM pdf_pages").fetchone()
                    )

    def test_ocr_text_is_stored_as_searchable_page_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scan.pdf"
            path.write_bytes(b"%PDF-1.7 scanned")
            with LocalIndex(Path(temporary) / "index.sqlite3") as index:
                with patch("snapseek.unified_index.extract_pdf_text") as extract, \
                     patch("snapseek.unified_index.PDFRenderer") as renderer, \
                     patch("snapseek.unified_index.extract_text_from_image", return_value="OCR words"):
                    extract.return_value = type(
                        "Result",
                        (),
                        {
                            "status": PDFExtractionStatus.OCR_REQUIRED,
                            "metadata": type(
                                "Metadata",
                                (),
                                {"title": None, "author": None, "page_count": 1},
                            )(),
                            "pages": (
                                type(
                                    "Page",
                                    (),
                                    {
                                        "page_number": 1,
                                        "text": "\n\t",
                                        "has_text": False,
                                    },
                                )(),
                            ),
                            "error": None,
                        },
                    )()
                    image = Image.new("RGB", (2, 2))
                    renderer.return_value.__enter__.return_value.render_page.return_value = image
                    outcome = index.index_pdf(path, force_integrity_check=True)
                self.assertEqual(outcome.status, PDFExtractionStatus.OK)
                row = index.connection.execute(
                    """
                    SELECT text, extracted_text, ocr_text, text_source
                    FROM pdf_pages
                    """
                ).fetchone()
                self.assertEqual(
                    (row["text"], row["extracted_text"], row["ocr_text"], row["text_source"]),
                    ("OCR words", "", "OCR words", "ocr"),
                )
