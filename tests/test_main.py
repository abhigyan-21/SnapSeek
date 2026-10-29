"""Integration tests for the application workflow with OCR mocked."""

import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import call, patch

import main
from snapseek.config import ConfigurationError
from snapseek.ocr import (
    ImageOCRError,
    OCRDependencyError,
    TesseractExecutableError,
)
from snapseek.scanner import ImageMetadata
from snapseek.semantic import (
    SemanticDependencyError,
    SemanticInferenceError,
    SemanticModelError,
)


class FakeSemanticEncoder:
    def __init__(self) -> None:
        self.documents: list[str] = []
        self.queries: list[str] = []

    def embed(self, documents):
        self.documents.extend(documents)
        return [(1.0, 0.0) if "first" in text else (0.8, 0.6) for text in self.documents]

    def query_embed(self, query):
        self.queries.append(query)
        return [(1.0, 0.0)]


class MainOCRIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp_dir.name)
        self.images = [
            ImageMetadata("first.png", self.directory / "first.png", ".png", 11),
            ImageMetadata("second.jpg", self.directory / "second.jpg", ".jpg", 22),
        ]
        self.config_patch = patch(
            "main.get_screenshot_directory", return_value=self.directory
        )
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.encoder = FakeSemanticEncoder()
        self.encoder_patch = patch("main.load_semantic_encoder", return_value=self.encoder)
        self.encoder_patch.start()
        self.addCleanup(self.encoder_patch.stop)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_scans_and_ocr_processes_each_image_with_associated_text(self) -> None:
        output = StringIO()
        with patch("main.scan_images", return_value=self.images) as scan_mock, \
             patch("main.extract_text", side_effect=["first needle text", "second NEEDLE text"]) as ocr_mock, \
             redirect_stdout(output):
            status = main.main(["needle"])

        scan_mock.assert_called_once_with(self.directory)
        ocr_mock.assert_has_calls([call(self.images[0]), call(self.images[1])])
        self.assertEqual(ocr_mock.call_count, 2)
        self.assertIn(
            f"{self.images[0].filename} | {self.images[0].path} | Similarity: ",
            output.getvalue(),
        )
        self.assertIn(
            f"OCR text: first needle text",
            output.getvalue(),
        )
        self.assertIn(
            f"{self.images[1].filename} | {self.images[1].path} | Similarity: ",
            output.getvalue(),
        )
        self.assertIn("OCR text: second NEEDLE text", output.getvalue())
        self.assertRegex(output.getvalue(), r"Similarity: 0\.\d{4}")
        self.assertEqual(self.encoder.documents, ["first needle text", "second NEEDLE text"])
        self.assertEqual(self.encoder.queries, ["needle"])
        self.assertIn("Total images: 2", output.getvalue())
        self.assertIn("OCR summary: 2 succeeded, 0 failed, 0 not processed.", output.getvalue())
        self.assertEqual(status, 0)

    def test_empty_ocr_text_is_counted_as_success(self) -> None:
        output = StringIO()
        with patch("main.scan_images", return_value=[self.images[0]]), \
             patch("main.extract_text", return_value=""), \
             redirect_stdout(output):
            status = main.main(["needle"])

        self.assertIn("No matching images found.", output.getvalue())
        self.assertIn("OCR summary: 1 succeeded, 0 failed, 0 not processed.", output.getvalue())
        self.assertEqual(status, 0)

    def test_image_failure_warns_and_later_images_are_processed(self) -> None:
        output = StringIO()
        errors = StringIO()
        third_image = ImageMetadata("third.webp", self.directory / "third.webp", ".webp", 33)
        images = [self.images[0], self.images[1], third_image]

        def fake_extract(image: ImageMetadata) -> str:
            if image == self.images[1]:
                raise ImageOCRError(f"OCR failed for image {image.path}: bad image")
            return f"text for {image.filename}"

        with patch("main.scan_images", return_value=images), \
             patch("main.extract_text", side_effect=fake_extract) as ocr_mock, \
             redirect_stdout(output), redirect_stderr(errors):
            status = main.main(["text"])

        self.assertEqual(ocr_mock.call_args_list, [call(image) for image in images])
        self.assertIn(f"Warning: OCR failed for image {self.images[1].path}", errors.getvalue())
        self.assertIn("text for first.png", output.getvalue())
        self.assertIn("text for third.webp", output.getvalue())
        self.assertIn("OCR summary: 2 succeeded, 1 failed, 0 not processed.", output.getvalue())
        self.assertEqual(status, 1)

    def test_setup_errors_are_reported_once_and_stop_processing(self) -> None:
        cases = [
            (OCRDependencyError, "pytesseract package is unavailable"),
            (TesseractExecutableError, "Tesseract executable could not be launched"),
        ]
        for error_type, message in cases:
            with self.subTest(error_type=error_type.__name__):
                errors = StringIO()
                output = StringIO()
                with patch("main.scan_images", return_value=self.images), \
                     patch("main.extract_text", side_effect=error_type(message)) as ocr_mock, \
                     redirect_stdout(output), redirect_stderr(errors):
                    status = main.main(["text"])

                self.assertEqual(status, 1)
                self.assertEqual(ocr_mock.call_count, 1)
                self.assertEqual(errors.getvalue().count("Error: OCR setup failed"), 1)
                self.assertIn(message, errors.getvalue())
                self.assertIn("2 not processed", output.getvalue())

    def test_missing_semantic_dependency_is_reported_before_ocr(self) -> None:
        errors = StringIO()
        output = StringIO()
        missing_dependency = SemanticDependencyError(
            "Semantic search requires FastEmbed. Install SnapSeek's Python dependencies."
        )
        with patch("main.scan_images", return_value=self.images), \
             patch("main.extract_text") as ocr_mock, \
             patch("main.load_semantic_encoder", side_effect=missing_dependency), \
             redirect_stdout(output), redirect_stderr(errors):
            status = main.main(["payment"])

        self.assertEqual(status, 1)
        ocr_mock.assert_not_called()
        self.assertEqual(errors.getvalue().count("Error: Semantic search setup failed"), 1)
        self.assertIn("FastEmbed", errors.getvalue())
        self.assertNotIn("No matching images found", output.getvalue())
        self.assertIn("OCR summary: 0 succeeded, 0 failed, 2 not processed.", output.getvalue())

    def test_model_initialization_failure_is_reported_before_ocr(self) -> None:
        errors = StringIO()
        output = StringIO()
        model_error = SemanticModelError("model download failed")
        with patch("main.scan_images", return_value=self.images), \
             patch("main.extract_text") as ocr_mock, \
             patch("main.load_semantic_encoder", side_effect=model_error), \
             redirect_stdout(output), redirect_stderr(errors):
            status = main.main(["payment"])

        self.assertEqual(status, 1)
        ocr_mock.assert_not_called()
        self.assertEqual(errors.getvalue().count("Semantic search setup failed"), 1)
        self.assertIn("model download failed", errors.getvalue())
        self.assertIn("2 not processed", output.getvalue())

    def test_semantic_inference_failure_returns_nonzero(self) -> None:
        errors = StringIO()
        output = StringIO()
        with patch("main.scan_images", return_value=[self.images[0]]), \
             patch("main.extract_text", return_value="some OCR text"), \
             patch("main.search_semantic", side_effect=SemanticInferenceError("inference failed")), \
             redirect_stdout(output), redirect_stderr(errors):
            status = main.main(["query"])

        self.assertEqual(status, 1)
        self.assertIn("Semantic search failed", errors.getvalue())
        self.assertIn("inference failed", errors.getvalue())
        self.assertIn("OCR summary: 1 succeeded, 0 failed, 0 not processed.", output.getvalue())

    def test_configuration_error_keeps_nonzero_status(self) -> None:
        output = StringIO()
        with patch("main.get_screenshot_directory", side_effect=ConfigurationError("bad config")), \
             patch("main.scan_images") as scan_mock, redirect_stdout(output):
            status = main.main(["text"])

        self.assertEqual(status, 1)
        self.assertIn("Error: bad config", output.getvalue())
        scan_mock.assert_not_called()

    def test_directory_scanning_error_keeps_nonzero_status(self) -> None:
        output = StringIO()
        with patch("main.scan_images", side_effect=PermissionError("permission denied")), \
             redirect_stdout(output):
            status = main.main(["text"])

        self.assertEqual(status, 1)
        self.assertIn("Could not scan image directory", output.getvalue())
        self.assertIn("readable", output.getvalue())

    def test_empty_query_is_rejected_before_configuration_or_ocr(self) -> None:
        errors = StringIO()
        with patch("main.get_screenshot_directory") as config_mock, \
             patch("main.extract_text") as ocr_mock, \
             redirect_stderr(errors):
            with self.assertRaises(SystemExit) as exit_info:
                main.main(["   "])

        self.assertEqual(exit_info.exception.code, 2)
        self.assertIn("must not be empty or whitespace-only", errors.getvalue())
        config_mock.assert_not_called()
        ocr_mock.assert_not_called()

    def test_empty_directory_reports_no_matches_and_zero_counts(self) -> None:
        output = StringIO()
        with patch("main.get_screenshot_directory", return_value=self.directory), \
             patch("main.scan_images", return_value=[]) as scan_mock, \
             patch("main.extract_text") as ocr_mock, \
             redirect_stdout(output):
            status = main.main(["needle"])

        self.assertEqual(status, 0)
        scan_mock.assert_called_once_with(self.directory)
        ocr_mock.assert_not_called()
        self.assertIn("Total images: 0", output.getvalue())
        self.assertIn("OCR summary: 0 succeeded, 0 failed, 0 not processed.", output.getvalue())
        self.assertIn("No matching images found.", output.getvalue())


if __name__ == "__main__":
    unittest.main()
