"""Tests for screenshot configuration and inventory scanning."""

import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import main
from snapseek.config import ConfigurationError, get_screenshot_directory
from snapseek.scanner import scan_images


class ScanImagesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_supported_extensions_return_metadata(self) -> None:
        names = ["one.png", "two.jpg", "three.jpeg", "four.webp"]
        for name in names:
            (self.directory / name).write_bytes(b"image")

        images = scan_images(self.directory)

        self.assertEqual({image.filename for image in images}, set(names))
        self.assertTrue(all(image.path.parent == self.directory for image in images))
        self.assertTrue(all(image.extension.startswith(".") for image in images))
        self.assertTrue(all(image.size_bytes == 5 for image in images))

    def test_results_are_sorted_case_insensitively_by_filename(self) -> None:
        for name in ["zebra.png", "Alpha.jpg", "bravo.webp"]:
            (self.directory / name).write_bytes(b"image")

        images = scan_images(self.directory)

        self.assertEqual([image.filename for image in images], ["Alpha.jpg", "bravo.webp", "zebra.png"])

    def test_unsupported_files_are_ignored(self) -> None:
        (self.directory / "notes.txt").write_text("not an image", encoding="utf-8")

        self.assertEqual(scan_images(self.directory), [])

    def test_uppercase_extensions_are_supported_case_insensitively(self) -> None:
        (self.directory / "CAPTURE.PNG").write_bytes(b"png")
        (self.directory / "PHOTO.JpEg").write_bytes(b"jpeg")

        images = scan_images(self.directory)

        self.assertEqual([image.extension for image in images], [".png", ".jpeg"])

    def test_empty_directory_returns_no_images(self) -> None:
        self.assertEqual(scan_images(self.directory), [])

    def test_subfolders_are_ignored(self) -> None:
        nested = self.directory / "nested"
        nested.mkdir()
        (nested / "hidden.png").write_bytes(b"image")

        self.assertEqual(scan_images(self.directory), [])

    def test_missing_directory_raises_clear_error(self) -> None:
        missing = self.directory / "missing"

        with self.assertRaisesRegex(FileNotFoundError, "Image directory does not exist"):
            scan_images(missing)

    def test_disappearing_image_warns_skips_and_continues(self) -> None:
        disappearing = self.directory / "gone.png"
        surviving = self.directory / "stay.jpg"
        disappearing.write_bytes(b"gone")
        surviving.write_bytes(b"stay")
        original_stat = Path.stat

        def mocked_stat(path: Path, *args: object, **kwargs: object):
            if path == disappearing:
                raise FileNotFoundError(path)
            return original_stat(path, *args, **kwargs)

        warning = StringIO()
        with patch.object(Path, "stat", autospec=True, side_effect=mocked_stat), \
             redirect_stderr(warning):
            images = scan_images(self.directory)

        self.assertEqual([image.filename for image in images], ["stay.jpg"])
        self.assertIn(f"Warning: Image no longer exists: {disappearing}", warning.getvalue())


class ConfigurationTests(unittest.TestCase):
    def test_missing_environment_variable_raises_clear_error(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ConfigurationError, "SNAPSEEK_SCREENSHOT_DIR"):
                get_screenshot_directory()

    def test_nonexistent_directory_raises_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing"
            with patch.dict(os.environ, {"SNAPSEEK_SCREENSHOT_DIR": str(missing)}):
                with self.assertRaisesRegex(ConfigurationError, "does not exist"):
                    get_screenshot_directory()

    def test_existing_directory_is_returned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with patch.dict(os.environ, {"SNAPSEEK_SCREENSHOT_DIR": str(directory)}):
                self.assertEqual(get_screenshot_directory(), directory)

    def test_file_path_raises_clear_configuration_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            file_path = Path(temporary) / "not-a-directory.txt"
            file_path.write_text("file", encoding="utf-8")
            with patch.dict(os.environ, {"SNAPSEEK_SCREENSHOT_DIR": str(file_path)}):
                with self.assertRaisesRegex(ConfigurationError, "does not exist or is not a directory"):
                    get_screenshot_directory()


class MainTests(unittest.TestCase):
    def test_directory_listing_error_is_reported_with_nonzero_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            output = StringIO()
            with patch.dict(os.environ, {"SNAPSEEK_SCREENSHOT_DIR": str(directory)}), \
                 patch.object(Path, "iterdir", side_effect=PermissionError("permission denied")), \
                 redirect_stdout(output):
                status = main.main(["text"])

        self.assertEqual(status, 1)
        self.assertIn("Could not scan image directory", output.getvalue())
        self.assertIn("readable", output.getvalue())


if __name__ == "__main__":
    unittest.main()
