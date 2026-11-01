"""Standalone local PDF text extraction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable, BinaryIO
import unicodedata

from pypdf import PdfReader
from pypdf.errors import PdfReadError


class PDFExtractionStatus(StrEnum):
    """Stable classifications for PDF extraction outcomes."""

    OK = "ok"
    EMPTY = "empty"
    OCR_REQUIRED = "ocr_required"
    ENCRYPTED = "encrypted"
    CORRUPT = "corrupt"
    UNSUPPORTED = "unsupported"
    INACCESSIBLE = "inaccessible"


class PDFExtractionCancelled(Exception):
    """Raised when extraction is cancelled before or during page processing."""


@dataclass(frozen=True)
class PDFPageText:
    """Text extracted from one PDF page."""

    page_number: int
    text: str
    has_text: bool
    text_source: str = "extracted"


@dataclass(frozen=True)
class PDFMetadata:
    """Source and document metadata available without modifying the PDF."""

    title: str | None
    author: str | None
    page_count: int
    size_bytes: int
    modified_ns: int


@dataclass(frozen=True)
class PDFExtractionResult:
    """Classified PDF extraction output with page boundaries preserved."""

    path: Path
    metadata: PDFMetadata
    pages: tuple[PDFPageText, ...]
    status: PDFExtractionStatus
    ocr_required: bool = False
    error: str | None = None


def _metadata(path: Path, *, page_count: int = 0, title: str | None = None,
              author: str | None = None) -> PDFMetadata:
    try:
        stat = path.stat()
    except OSError:
        return PDFMetadata(title, author, page_count, 0, 0)
    return PDFMetadata(
        title=title,
        author=author,
        page_count=page_count,
        size_bytes=stat.st_size,
        modified_ns=stat.st_mtime_ns,
    )


def _result(
    path: Path,
    status: PDFExtractionStatus,
    *,
    page_count: int = 0,
    title: str | None = None,
    author: str | None = None,
    pages: tuple[PDFPageText, ...] = (),
    ocr_required: bool = False,
    error: str | None = None,
) -> PDFExtractionResult:
    return PDFExtractionResult(
        path=path,
        metadata=_metadata(
            path,
            page_count=page_count,
            title=title,
            author=author,
        ),
        pages=pages,
        status=status,
        ocr_required=ocr_required,
        error=error,
    )


def _cancelled(cancelled: Callable[[], bool] | None) -> bool:
    return cancelled is not None and cancelled()


def has_meaningful_text(text: str) -> bool:
    """Return whether text contains more than whitespace/control characters."""
    return any(
        not (char.isspace() or unicodedata.category(char).startswith("C"))
        for char in text
    )


def _open_pdf(path: Path) -> BinaryIO:
    return path.open("rb")


def extract_pdf_text(
    path: str | Path,
    cancelled: Callable[[], bool] | None = None,
) -> PDFExtractionResult:
    """Extract PDF text page-by-page without changing the source file."""
    source = Path(path).expanduser()
    if _cancelled(cancelled):
        raise PDFExtractionCancelled("PDF extraction was cancelled.")
    if source.suffix.casefold() != ".pdf":
        return _result(
            source,
            PDFExtractionStatus.UNSUPPORTED,
            error="The file does not have a .pdf extension.",
        )
    try:
        source_stat = source.stat()
        if not source.is_file():
            return _result(
                source,
                PDFExtractionStatus.INACCESSIBLE,
                error="The PDF path is not a regular file.",
            )
    except OSError as error:
        return PDFExtractionResult(
            path=source,
            metadata=PDFMetadata(None, None, 0, 0, 0),
            pages=(),
            status=PDFExtractionStatus.INACCESSIBLE,
            error=str(error),
        )
    if source_stat.st_size < 5:
        return _result(
            source,
            PDFExtractionStatus.UNSUPPORTED,
            error="The file does not contain a PDF header.",
        )
    try:
        with _open_pdf(source) as stream:
            if _cancelled(cancelled):
                raise PDFExtractionCancelled("PDF extraction was cancelled.")
            if stream.read(5) != b"%PDF-":
                return _result(
                    source,
                    PDFExtractionStatus.UNSUPPORTED,
                    error="The file does not contain a PDF header.",
                )
            stream.seek(0)
            try:
                reader = PdfReader(stream, strict=False)
            except (PdfReadError, ValueError, TypeError) as error:
                return _result(
                    source,
                    PDFExtractionStatus.CORRUPT,
                    error=str(error),
                )
            if reader.is_encrypted:
                return _result(
                    source,
                    PDFExtractionStatus.ENCRYPTED,
                    error="The PDF is encrypted and requires a password.",
                )
            try:
                page_count = len(reader.pages)
                metadata = reader.metadata
                title = str(metadata.title) if metadata and metadata.title else None
                author = str(metadata.author) if metadata and metadata.author else None
            except (PdfReadError, ValueError, TypeError) as error:
                return _result(
                    source,
                    PDFExtractionStatus.CORRUPT,
                    error=str(error),
                )
            pages: list[PDFPageText] = []
            try:
                for page_number, page in enumerate(reader.pages, start=1):
                    if _cancelled(cancelled):
                        raise PDFExtractionCancelled("PDF extraction was cancelled.")
                    text = page.extract_text() or ""
                    meaningful = has_meaningful_text(text)
                    pages.append(
                        PDFPageText(
                            page_number=page_number,
                            text=text,
                            has_text=meaningful,
                        )
                    )
            except PDFExtractionCancelled:
                raise
            except (PdfReadError, ValueError, TypeError) as error:
                return _result(
                    source,
                    PDFExtractionStatus.CORRUPT,
                    page_count=page_count,
                    title=title,
                    author=author,
                    pages=tuple(pages),
                    error=str(error),
                )
    except PDFExtractionCancelled:
        raise
    except OSError as error:
        return _result(
            source,
            PDFExtractionStatus.INACCESSIBLE,
            error=str(error),
        )
    if page_count == 0:
        status = PDFExtractionStatus.EMPTY
        needs_ocr = False
    elif not any(page.has_text for page in pages):
        status = PDFExtractionStatus.OCR_REQUIRED
        needs_ocr = True
    else:
        status = PDFExtractionStatus.OK
        needs_ocr = False
    return _result(
        source,
        status,
        page_count=page_count,
        title=title,
        author=author,
        pages=tuple(pages),
        ocr_required=needs_ocr,
    )
