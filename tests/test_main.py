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

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_scans_and_ocr_processes_each_image_with_associated_text(self) -> None:
        output = StringIO()
        with patch("main.scan_images", return_value=self.images) as scan_mock, \
             patch("main.extract_text", side_effect=["first text", "second text"]) as ocr_mock, \
             redirect_stdout(output):
            status = main.main()

        scan_mock.assert_called_once_with(self.directory)
        ocr_mock.assert_has_calls([call(self.images[0]), call(self.images[1])])
        self.assertEqual(ocr_mock.call_count, 2)
        self.assertIn(
            f"{self.images[0].path} | .png | 11 bytes | OCR text: first text",
            output.getvalue(),
        )
        self.assertIn(
            f"{self.images[1].path} | .jpg | 22 bytes | OCR text: second text",
            output.getvalue(),
        )
        self.assertIn("Total images: 2", output.getvalue())
        self.assertIn("OCR summary: 2 succeeded, 0 failed, 0 not processed.", output.getvalue())
        self.assertEqual(status, 0)

    def test_empty_ocr_text_is_counted_as_success(self) -> None:
        output = StringIO()
        with patch("main.scan_images", return_value=[self.images[0]]), \
             patch("main.extract_text", return_value=""), \
             redirect_stdout(output):
            status = main.main()

        self.assertIn("OCR text: (no text detected)", output.getvalue())
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
            status = main.main()

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
                    status = main.main()

                self.assertEqual(status, 1)
                self.assertEqual(ocr_mock.call_count, 1)
                self.assertEqual(errors.getvalue().count("Error: OCR setup failed"), 1)
                self.assertIn(message, errors.getvalue())
                self.assertIn("2 not processed", output.getvalue())

    def test_configuration_error_keeps_nonzero_status(self) -> None:
        output = StringIO()
        with patch("main.get_screenshot_directory", side_effect=ConfigurationError("bad config")), \
             patch("main.scan_images") as scan_mock, redirect_stdout(output):
            status = main.main()

        self.assertEqual(status, 1)
        self.assertIn("Error: bad config", output.getvalue())
        scan_mock.assert_not_called()

    def test_directory_scanning_error_keeps_nonzero_status(self) -> None:
        output = StringIO()
        with patch("main.scan_images", side_effect=PermissionError("permission denied")), \
             redirect_stdout(output):
            status = main.main()

        self.assertEqual(status, 1)
        self.assertIn("Could not scan image directory", output.getvalue())
        self.assertIn("readable", output.getvalue())


if __name__ == "__main__":
    unittest.main()
