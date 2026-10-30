"""Tests for the standalone OCR component without requiring Tesseract."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from snapseek.ocr import (
    ImageOCRError,
    OCRDependencyError,
    TesseractExecutableError,
    extract_text,
)
from snapseek.scanner import ImageMetadata


class ExtractTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "sample.png"
        self.image_bytes = b"unchanged source image bytes"
        self.path.write_bytes(self.image_bytes)
        self.image = ImageMetadata(
            filename=self.path.name,
            path=self.path,
            extension=".png",
            size_bytes=len(self.image_bytes),
        )
        self.tesseract_not_found = type("TesseractNotFoundError", (OSError,), {})
        self.tesseract_error = type("TesseractError", (Exception,), {})

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _mock_pytesseract(
        self, image_to_string: Mock, *, tesseract_cmd: str = "tesseract"
    ) -> SimpleNamespace:
        return SimpleNamespace(
            image_to_string=image_to_string,
            TesseractNotFoundError=self.tesseract_not_found,
            TesseractError=self.tesseract_error,
            pytesseract=SimpleNamespace(tesseract_cmd=tesseract_cmd),
        )

    def test_explicit_tesseract_configuration_is_preserved(self) -> None:
        configured_path = r"D:\Tools\Tesseract\tesseract.exe"
        pytesseract = self._mock_pytesseract(
            Mock(return_value="text"), tesseract_cmd=configured_path
        )

        with patch("snapseek.ocr.import_module", return_value=pytesseract), patch(
            "snapseek.ocr.shutil.which"
        ) as which:
            self.assertEqual(extract_text(self.image), "text")

        self.assertEqual(pytesseract.pytesseract.tesseract_cmd, configured_path)
        which.assert_not_called()

    def test_default_command_uses_path_discovery_without_reconfiguration(self) -> None:
        pytesseract = self._mock_pytesseract(Mock(return_value="text"))

        with patch("snapseek.ocr.import_module", return_value=pytesseract), patch(
            "snapseek.ocr.shutil.which", return_value=r"C:\Tools\tesseract.exe"
        ) as which, patch("snapseek.ocr.sys.platform", "win32"), patch(
            "snapseek.ocr.Path.is_file"
        ) as is_file:
            self.assertEqual(extract_text(self.image), "text")

        which.assert_called_once_with("tesseract")
        is_file.assert_not_called()
        self.assertEqual(pytesseract.pytesseract.tesseract_cmd, "tesseract")

    def test_windows_standard_install_is_used_when_path_lookup_fails(self) -> None:
        pytesseract = self._mock_pytesseract(Mock(return_value="text"))
        standard_path = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")

        with patch("snapseek.ocr.import_module", return_value=pytesseract), patch(
            "snapseek.ocr.shutil.which", return_value=None
        ), patch("snapseek.ocr.sys.platform", "win32"), patch.object(
            Path, "is_file", autospec=True, return_value=True
        ) as is_file:
            self.assertEqual(extract_text(self.image), "text")

        is_file.assert_called_once_with(standard_path)
        self.assertEqual(pytesseract.pytesseract.tesseract_cmd, str(standard_path))

    def test_non_windows_does_not_check_windows_standard_install(self) -> None:
        pytesseract = self._mock_pytesseract(Mock(return_value="text"))

        with patch("snapseek.ocr.import_module", return_value=pytesseract), patch(
            "snapseek.ocr.shutil.which", return_value=None
        ), patch("snapseek.ocr.sys.platform", "linux"), patch.object(
            Path, "is_file", autospec=True
        ) as is_file:
            self.assertEqual(extract_text(self.image), "text")

        is_file.assert_not_called()
        self.assertEqual(pytesseract.pytesseract.tesseract_cmd, "tesseract")

    def test_returns_text_for_the_metadata_path(self) -> None:
        image_to_string = Mock(return_value="Found text")
        pytesseract = self._mock_pytesseract(image_to_string)

        with patch("snapseek.ocr.import_module", return_value=pytesseract):
            text = extract_text(self.image)

        self.assertEqual(text, "Found text")
        image_to_string.assert_called_once_with(str(self.path))
        self.assertEqual(self.path.read_bytes(), self.image_bytes)

    def test_empty_ocr_output_is_returned_as_valid_text(self) -> None:
        pytesseract = self._mock_pytesseract(Mock(return_value=""))

        with patch("snapseek.ocr.import_module", return_value=pytesseract):
            self.assertEqual(extract_text(self.image), "")

    def test_missing_pytesseract_package_has_actionable_error(self) -> None:
        missing_package = ModuleNotFoundError(
            "No module named 'pytesseract'", name="pytesseract"
        )

        with patch("snapseek.ocr.import_module", side_effect=missing_package):
            with self.assertRaisesRegex(OCRDependencyError, "pip install -e"):
                extract_text(self.image)

    def test_missing_tesseract_executable_has_distinct_error(self) -> None:
        image_to_string = Mock(side_effect=self.tesseract_not_found("not found"))
        pytesseract = self._mock_pytesseract(image_to_string)

        with patch("snapseek.ocr.import_module", return_value=pytesseract):
            with self.assertRaisesRegex(TesseractExecutableError, "available on PATH"):
                extract_text(self.image)

    def test_os_error_for_tesseract_process_is_an_executable_error(self) -> None:
        launch_error = PermissionError(13, "Permission denied", "tesseract.exe")
        image_to_string = Mock(side_effect=launch_error)
        pytesseract = self._mock_pytesseract(image_to_string)

        with patch("snapseek.ocr.import_module", return_value=pytesseract):
            with self.assertRaisesRegex(TesseractExecutableError, "could not be launched") as error:
                extract_text(self.image)

        self.assertIs(error.exception.__cause__, launch_error)

    def test_os_error_for_image_path_remains_image_specific(self) -> None:
        image_error = PermissionError(13, "Permission denied", str(self.path))
        image_to_string = Mock(side_effect=image_error)
        pytesseract = self._mock_pytesseract(image_to_string)

        with patch("snapseek.ocr.import_module", return_value=pytesseract):
            with self.assertRaisesRegex(ImageOCRError, "OCR failed for image") as error:
                extract_text(self.image)

        self.assertIs(error.exception.__cause__, image_error)

    def test_os_error_without_filename_remains_image_specific(self) -> None:
        image_error = PermissionError(13, "Permission denied")
        image_to_string = Mock(side_effect=image_error)
        pytesseract = self._mock_pytesseract(image_to_string)

        with patch("snapseek.ocr.import_module", return_value=pytesseract):
            with self.assertRaises(ImageOCRError) as error:
                extract_text(self.image)

        self.assertIs(error.exception.__cause__, image_error)

    def test_image_ocr_failure_names_the_image(self) -> None:
        image_to_string = Mock(side_effect=self.tesseract_error("invalid image"))
        pytesseract = self._mock_pytesseract(image_to_string)

        with patch("snapseek.ocr.import_module", return_value=pytesseract):
            with self.assertRaises(ImageOCRError) as error:
                extract_text(self.image)

        self.assertIn(str(self.path), str(error.exception))

    def test_ocr_failure_does_not_modify_source_file(self) -> None:
        image_to_string = Mock(side_effect=self.tesseract_error("invalid image"))
        pytesseract = self._mock_pytesseract(image_to_string)

        with patch("snapseek.ocr.import_module", return_value=pytesseract):
            with self.assertRaises(ImageOCRError):
                extract_text(self.image)

        self.assertEqual(self.path.read_bytes(), self.image_bytes)


if __name__ == "__main__":
    unittest.main()
