"""Search OCR text from supported images in the configured directory."""

import argparse
import sys

from snapseek.config import ConfigurationError, get_screenshot_directory
from snapseek.ocr import (
    ImageOCRError,
    OCRDependencyError,
    TesseractExecutableError,
    extract_text,
)
from snapseek.scanner import scan_images
from snapseek.search import (
    OCRTextRecord,
    SearchDependencyError,
    search_tfidf,
)


def _non_empty_query(value: str) -> str:
    """Reject empty CLI queries before scanning or running OCR."""
    if not value.strip():
        raise argparse.ArgumentTypeError(
            "Search query must not be empty or whitespace-only."
        )
    return value


def main(argv: list[str] | None = None) -> int:
    """Search screenshot OCR text, returning non-zero status on errors."""
    parser = argparse.ArgumentParser(description="Search screenshot OCR text.")
    parser.add_argument("query", type=_non_empty_query, help="word or phrase to search")
    args = parser.parse_args(argv)

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
    records: list[OCRTextRecord] = []
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

        records.append(OCRTextRecord(image=image, ocr_text=text))
        successes += 1

    print(f"Total images: {len(images)}")
    not_processed = len(images) - successes - failures
    print(
        f"OCR summary: {successes} succeeded, {failures} failed, "
        f"{not_processed} not processed."
    )
    if setup_error:
        return 1

    try:
        matches = search_tfidf(args.query, records)
    except SearchDependencyError as error:
        print(f"Error: Search setup failed: {error}", file=sys.stderr)
        return 1

    print(f"Search results for {args.query!r}:")
    if not matches:
        print("No matching images found.")
    else:
        for record in matches:
            print(
                f"{record.record.image.filename} | {record.record.image.path} | "
                f"Score: {record.score:.4f} | "
                f"OCR text: {record.record.ocr_text}"
            )
    return 1 if setup_error or failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
