"""Unit tests for the desktop scan/OCR/search coordination."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from snapseek.desktop_workflow import (
    DirectorySelectionError,
    OperationCancelled,
    ScanSnapshot,
    scan_and_extract,
    search_snapshot,
)
from snapseek.ocr import ImageOCRError, OCRDependencyError
from snapseek.scanner import ImageMetadata
from snapseek.search import OCRTextRecord
from snapseek.semantic import SemanticDependencyError


class FakeEncoder:
    def embed(self, documents):
        self.documents = list(documents)
        return [(1.0, 0.0) for _ in self.documents]

    def query_embed(self, query):
        self.query = query
        return [(1.0, 0.0)]


class DesktopWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.images = [
            ImageMetadata(name, self.directory / name, ".png", index + 1)
            for index, name in enumerate(("first.png", "broken.png", "last.png"))
        ]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_invalid_directory_is_rejected_before_scanning(self) -> None:
        missing = self.directory / "missing"
        with patch("snapseek.desktop_workflow.scan_images") as scanner:
            with self.assertRaisesRegex(DirectorySelectionError, "accessible directory"):
                scan_and_extract(missing)
        scanner.assert_not_called()

    def test_empty_directory_does_not_load_model_or_run_ocr(self) -> None:
        with patch("snapseek.desktop_workflow.scan_images", return_value=[]), \
             patch("snapseek.desktop_workflow.load_semantic_encoder") as loader, \
             patch("snapseek.desktop_workflow.extract_text") as extractor:
            snapshot = scan_and_extract(self.directory)

        self.assertEqual(snapshot.images, ())
        self.assertEqual(snapshot.records, ())
        self.assertEqual(snapshot.unprocessed_count, 0)
        loader.assert_not_called()
        extractor.assert_not_called()

    def test_model_load_precedes_ocr_and_image_failure_does_not_stop_later_images(self) -> None:
        events: list[str] = []
        encoder = FakeEncoder()

        def load_encoder():
            events.append("model")
            return encoder

        def extract(image: ImageMetadata) -> str:
            events.append(image.filename)
            if image == self.images[1]:
                raise ImageOCRError("bad image data")
            return f"text from {image.filename}"

        with patch("snapseek.desktop_workflow.scan_images", return_value=self.images):
            snapshot = scan_and_extract(
                self.directory,
                encoder_loader=load_encoder,
                text_extractor=extract,
            )

        self.assertEqual(events[0], "model")
        self.assertEqual(set(events[1:]), {"first.png", "broken.png", "last.png"})
        self.assertEqual([record.image for record in snapshot.records], [self.images[0], self.images[2]])
        self.assertEqual(
            [record.ocr_text for record in snapshot.records],
            ["text from first.png", "text from last.png"],
        )
        self.assertEqual(snapshot.image_failures[0].image_path, self.images[1].path)
        self.assertEqual(snapshot.unprocessed_count, 0)
        self.assertIs(snapshot.encoder, encoder)

    def test_ocr_setup_error_stops_and_counts_remaining_images(self) -> None:
        calls: list[str] = []

        def extract(image: ImageMetadata) -> str:
            calls.append(image.filename)
            if image == self.images[1]:
                raise OCRDependencyError("pytesseract missing")
            return "recognized"

        with patch("snapseek.desktop_workflow.scan_images", return_value=self.images):
            snapshot = scan_and_extract(
                self.directory,
                encoder=FakeEncoder(),
                text_extractor=extract,
            )

        self.assertIn("first.png", calls)
        self.assertIn("broken.png", calls)
        self.assertEqual(snapshot.setup_error_kind, "ocr")
        self.assertIn("pytesseract missing", snapshot.setup_error)
        self.assertEqual(len(snapshot.records), 1)
        self.assertEqual(snapshot.unprocessed_count, 2)

    def test_semantic_setup_error_is_reported_before_any_ocr(self) -> None:
        error = SemanticDependencyError("FastEmbed unavailable")
        with patch("snapseek.desktop_workflow.scan_images", return_value=self.images), \
             patch("snapseek.desktop_workflow.extract_text") as extractor:
            snapshot = scan_and_extract(
                self.directory,
                encoder_loader=lambda: (_ for _ in ()).throw(error),
            )

        self.assertEqual(snapshot.setup_error_kind, "semantic")
        self.assertIn("FastEmbed", snapshot.setup_error)
        self.assertEqual(snapshot.unprocessed_count, len(self.images))
        extractor.assert_not_called()

    def test_cancellation_after_discovery_prevents_model_load(self) -> None:
        with patch("snapseek.desktop_workflow.scan_images", return_value=self.images), \
             patch("snapseek.desktop_workflow.load_semantic_encoder") as loader:
            with self.assertRaises(OperationCancelled):
                scan_and_extract(self.directory, cancelled=lambda: True)
        loader.assert_not_called()

    def test_search_uses_snapshot_records_without_rescanning_or_ocr(self) -> None:
        encoder = FakeEncoder()
        record = OCRTextRecord(self.images[0], "recognized words")
        snapshot = ScanSnapshot(self.directory, tuple(self.images[:1]), (record,), (), encoder=encoder)
        with patch("snapseek.desktop_workflow.scan_images") as scanner, \
             patch("snapseek.desktop_workflow.extract_text") as extractor:
            results = search_snapshot("recognized", snapshot)

        self.assertEqual(len(results), 1)
        self.assertIs(results[0].record, record)
        self.assertEqual(encoder.documents, ["recognized words"])
        self.assertEqual(encoder.query, "recognized")
        scanner.assert_not_called()
        extractor.assert_not_called()

    def test_cached_ocr_is_reused_without_extracting_again(self) -> None:
        cached = {image.path: f"cached {image.filename}" for image in self.images}
        with patch("snapseek.desktop_workflow.scan_images", return_value=self.images), \
             patch("snapseek.desktop_workflow.extract_text") as extractor:
            snapshot = scan_and_extract(
                self.directory,
                cached_ocr=cached,
            )

        self.assertEqual(
            [record.ocr_text for record in snapshot.records],
            ["cached first.png", "cached broken.png", "cached last.png"],
        )
        extractor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
