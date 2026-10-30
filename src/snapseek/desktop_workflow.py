"""Testable scan, OCR, and semantic-search coordination for the desktop app."""

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from typing import Callable

from snapseek.ocr import (
    ImageOCRError,
    OCRDependencyError,
    TesseractExecutableError,
    extract_text,
)
from snapseek.scanner import ImageMetadata, scan_images
from snapseek.search import OCRTextRecord
from snapseek.semantic import (
    SemanticDependencyError,
    SemanticModelError,
    SemanticSearchResult,
    TextEncoder,
    load_semantic_encoder,
    search_semantic,
)


class DirectorySelectionError(ValueError):
    """Raised when the selected screenshot directory is invalid."""


class OperationCancelled(RuntimeError):
    """Raised when a worker is asked to stop between blocking operations."""


DESKTOP_TOP_K = 10


@dataclass(frozen=True)
class ImageOCRFailure:
    """An image-specific OCR failure that the UI can show to the user."""

    image_path: Path
    message: str


@dataclass(frozen=True)
class ScanSnapshot:
    """In-memory scan and OCR data for one directory during this app run."""

    directory: Path
    images: tuple[ImageMetadata, ...]
    records: tuple[OCRTextRecord, ...]
    image_failures: tuple[ImageOCRFailure, ...]
    setup_error: str | None = None
    setup_error_kind: str | None = None
    encoder: TextEncoder | None = None

    @property
    def unprocessed_count(self) -> int:
        return len(self.images) - len(self.records) - len(self.image_failures)


def normalize_directory_selection(value: str | Path) -> str:
    """Create a stable comparison key for Windows or POSIX directory paths."""
    return str(Path(value).expanduser().resolve(strict=False)).casefold()


def scan_and_extract(
    selected_directory: str | Path,
    *,
    encoder: TextEncoder | None = None,
    encoder_loader: Callable[[], TextEncoder] | None = None,
    image_scanner: Callable[[Path], list[ImageMetadata]] | None = None,
    text_extractor: Callable[[ImageMetadata], str] | None = None,
    cancelled: Callable[[], bool] | None = None,
    cached_ocr: dict[Path, str] | None = None,
    max_ocr_workers: int | None = None,
) -> ScanSnapshot:
    """Validate, scan, then extract OCR while retaining image/text pairing.

    Expected setup and image failures are returned as data for display; scanner
    filesystem errors and unexpected programming errors propagate to the UI.
    Function injection keeps this workflow deterministic in unit tests.
    """
    directory = Path(selected_directory).expanduser()
    if not directory.is_dir():
        raise DirectorySelectionError(
            f"This path is not an accessible directory: {directory}"
        )

    scanner = image_scanner or scan_images
    extractor = text_extractor or extract_text
    is_cancelled = cancelled or (lambda: False)
    images = tuple(scanner(directory))
    if is_cancelled():
        raise OperationCancelled("The scan was cancelled.")
    if not images:
        return ScanSnapshot(directory, (), (), (), encoder=encoder)

    if encoder is None and encoder_loader is not None:
        try:
            encoder = encoder_loader()
        except (SemanticDependencyError, SemanticModelError) as error:
            return ScanSnapshot(
                directory=directory,
                images=images,
                records=(),
                image_failures=(),
                setup_error=str(error),
                setup_error_kind="semantic",
            )

    records: list[OCRTextRecord] = []
    failures: list[ImageOCRFailure] = []
    setup_error = None
    setup_error_kind = None
    cached = cached_ocr or {}
    pending = [image for image in images if image.path not in cached]
    extracted: dict[Path, str] = dict(cached)
    if pending:
        workers = max_ocr_workers or min(4, max(1, os.cpu_count() or 1))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {image.path: pool.submit(extractor, image) for image in pending}
            for image in pending:
                if is_cancelled():
                    for future in futures.values():
                        future.cancel()
                    raise OperationCancelled("The scan was cancelled.")
                try:
                    extracted[image.path] = futures[image.path].result()
                except (OCRDependencyError, TesseractExecutableError) as error:
                    setup_error = str(error)
                    setup_error_kind = "ocr"
                    for future in futures.values():
                        future.cancel()
                    break
                except ImageOCRError as error:
                    failures.append(ImageOCRFailure(image.path, str(error)))
    records = [
        OCRTextRecord(image=image, ocr_text=extracted[image.path])
        for image in images
        if image.path in extracted
    ]

    return ScanSnapshot(
        directory=directory,
        images=images,
        records=tuple(records),
        image_failures=tuple(failures),
        setup_error=setup_error,
        setup_error_kind=setup_error_kind,
        encoder=encoder,
    )


def search_snapshot(
    query: str, snapshot: ScanSnapshot, *, encoder: TextEncoder | None = None
) -> list[SemanticSearchResult]:
    """Return the ten highest-ranked results for the desktop UI."""
    results = search_semantic(
        query,
        snapshot.records,
        encoder=encoder if encoder is not None else snapshot.encoder,
    )
    return results[:DESKTOP_TOP_K]
