"""Print an inventory of supported images in the configured directory."""

import sys

from snapseek.config import ConfigurationError, get_screenshot_directory
from snapseek.ocr import (
    ImageOCRError,
    OCRDependencyError,
    TesseractExecutableError,
    extract_text,
)
from snapseek.scanner import scan_images


def main() -> int:
    """Print inventory and OCR text, returning a non-zero status on errors."""
    try:
        directory = get_screenshot_directory()
    except ConfigurationError as error:
        print(f"Error: {error}")
        return 1

    try:
        images = scan_images(directory)
    except OSError as error:
        print(
            f"Error: Could not scan image directory {directory}: {error}. "
            "Check that the directory exists and is readable."
        )
        return 1
    successes = 0
    failures = 0
    setup_error = False
    for image in images:
        try:
            text = extract_text(image)
        except (OCRDependencyError, TesseractExecutableError) as error:
            print(f"Error: OCR setup failed: {error}", file=sys.stderr)
            setup_error = True
            break
        except ImageOCRError as error:
            print(
                f"Warning: OCR failed for image {image.path}: {error}",
                file=sys.stderr,
            )
            failures += 1
            continue

        text_summary = text if text else "(no text detected)"
        print(
            f"{image.filename} | {image.path} | {image.extension} | "
            f"{image.size_bytes} bytes | OCR text: {text_summary}"
        )
        successes += 1

    print(f"Total images: {len(images)}")
    not_processed = len(images) - successes - failures
    print(
        f"OCR summary: {successes} succeeded, {failures} failed, "
        f"{not_processed} not processed."
    )
    return 1 if setup_error or failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
