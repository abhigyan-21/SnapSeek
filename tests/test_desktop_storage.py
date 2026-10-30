"""Tests for the local desktop folder and OCR state."""

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from snapseek.desktop_storage import (
    cached_ocr,
    load_state,
    saved_directory,
    save_state,
    state_path,
)
from snapseek.scanner import ImageMetadata


class DesktopStorageTests(unittest.TestCase):
    def test_cached_ocr_requires_matching_file_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            image_path = directory / "capture.png"
            image_path.write_bytes(b"image")
            image = ImageMetadata("capture.png", image_path, ".png", image_path.stat().st_size)
            with patch("snapseek.desktop_storage.state_path", return_value=directory / "state.json"):
                save_state(directory, (image,), records=((image, "recognized"),))
                self.assertEqual(cached_ocr(directory, (image,)), {image_path: "recognized"})
                image_path.write_bytes(b"changed")
                changed = ImageMetadata(
                    image.filename,
                    image.path,
                    image.extension,
                    image_path.stat().st_size,
                )
                self.assertEqual(cached_ocr(directory, (changed,)), {})

    def test_malformed_state_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            path.write_text("{not json", encoding="utf-8")
            with patch("snapseek.desktop_storage.state_path", return_value=path):
                self.assertEqual(load_state()["folders"], {})

    def test_saved_directory_skips_missing_latest_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            valid = root / "valid"
            valid.mkdir()
            missing = root / "missing"
            state = root / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "folders": {
                            "valid": {"directory": str(valid), "images": {}},
                            "missing": {"directory": str(missing), "images": {}},
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch("snapseek.desktop_storage.state_path", return_value=state):
                self.assertEqual(saved_directory(), valid)
