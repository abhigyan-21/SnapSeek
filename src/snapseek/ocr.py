"""Extract text from one image using the local Tesseract OCR engine."""

import os
from importlib import import_module
from pathlib import Path
import shutil
import sys

from snapseek.scanner import ImageMetadata


class OCRError(RuntimeError):
    """Base class for OCR setup and processing errors."""


class OCRDependencyError(OCRError):
    """Raised when the pytesseract Python package is unavailable."""


class TesseractExecutableError(OCRError):
    """Raised when the Tesseract executable cannot be found or started."""


class ImageOCRError(OCRError):
    """Raised when OCR fails while processing a particular image."""


def _discover_tesseract(pytesseract) -> None:
    """Keep custom Tesseract configuration or find the standard executable.

    pytesseract defaults to the command ``tesseract``, which it resolves via
    PATH. On Windows, desktop launches may not inherit a shell's temporary
    PATH changes, so use the standard installer location as a fallback.
    """
    pytesseract_module = pytesseract.pytesseract
    configured_command = pytesseract_module.tesseract_cmd
    if configured_command != "tesseract":
        # A non-default value is user configuration; leave it untouched.
        return

    if shutil.which(configured_command):
        # Keep pytesseract's normal PATH-based behavior.
        return

    if sys.platform == "win32":
        standard_install = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        if standard_install.is_file():
            pytesseract_module.tesseract_cmd = str(standard_install)


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

    _discover_tesseract(pytesseract)

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
