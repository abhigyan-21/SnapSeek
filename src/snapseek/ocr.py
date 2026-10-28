"""Extract text from one image using the local Tesseract OCR engine."""

import os
from importlib import import_module
from pathlib import Path

from snapseek.scanner import ImageMetadata


class OCRError(RuntimeError):
    """Base class for OCR setup and processing errors."""


class OCRDependencyError(OCRError):
    """Raised when the pytesseract Python package is unavailable."""


class TesseractExecutableError(OCRError):
    """Raised when the Tesseract executable cannot be found or started."""


class ImageOCRError(OCRError):
    """Raised when OCR fails while processing a particular image."""


def _is_tesseract_launch_error(error: OSError, image_path: Path) -> bool:
    """Identify OS errors naming a path other than the input image."""
    if error.filename is None:
        return False

    error_path = os.path.normcase(os.path.abspath(os.fsdecode(error.filename)))
    source_path = os.path.normcase(os.path.abspath(os.fspath(image_path)))
    return error_path != source_path


def extract_text(image: ImageMetadata) -> str:
    """Return OCR text for one image, which may be an empty string.

    ``pytesseract`` is imported here rather than at module load time so code
    can import this module even when the optional runtime dependency is absent.
    """
    try:
        pytesseract = import_module("pytesseract")
    except ModuleNotFoundError as error:
        if error.name != "pytesseract":
            raise
        raise OCRDependencyError(
            "The pytesseract package is unavailable. Install SnapSeek's Python "
            "dependencies with `pip install -e .`."
        ) from error

    try:
        return pytesseract.image_to_string(str(image.path))
    except pytesseract.TesseractNotFoundError as error:
        raise TesseractExecutableError(
            "The Tesseract executable was not found. Install Tesseract OCR and "
            "make `tesseract` available on PATH, or configure "
            "pytesseract.pytesseract.tesseract_cmd."
        ) from error
    except OSError as error:
        if _is_tesseract_launch_error(error, image.path):
            raise TesseractExecutableError(
                f"The Tesseract executable could not be launched: {error}. "
                "Check the executable path and permissions."
            ) from error
        raise ImageOCRError(
            f"OCR failed for image {image.path}: {error}"
        ) from error
    except pytesseract.TesseractError as error:
        raise ImageOCRError(
            f"OCR failed for image {image.path}: {error}"
        ) from error
