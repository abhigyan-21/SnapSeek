"""Bounded, page-at-a-time PDF rendering for local OCR."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Callable


class PDFRenderError(RuntimeError):
    """Raised when a PDF page cannot be rendered."""


class PDFRenderLimitError(PDFRenderError):
    """Raised when a rendered page exceeds configured resource limits."""


class PDFRenderCancelled(PDFRenderError):
    """Raised when rendering is cancelled."""


@dataclass(frozen=True)
class PDFRenderLimits:
    dpi: int = 150
    max_width: int = 4096
    max_height: int = 4096
    max_pixels: int = 16_000_000


class PDFRenderer:
    """Render one PDF page into an owning PIL image at a time."""

    def __init__(
        self,
        path: str | Path,
        *,
        limits: PDFRenderLimits = PDFRenderLimits(),
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self.path = Path(path)
        self.limits = limits
        self.cancelled = cancelled or (lambda: False)
        self._pdfium = None
        self._document = None

    def __enter__(self) -> "PDFRenderer":
        if self.cancelled():
            raise PDFRenderCancelled("PDF rendering was cancelled.")
        try:
            self._pdfium = import_module("pypdfium2")
            self._document = self._pdfium.PdfDocument(str(self.path))
        except PDFRenderCancelled:
            raise
        except Exception as error:
            raise PDFRenderError(f"Could not open PDF for rendering: {error}") from error
        return self

    def __exit__(self, *_: object) -> None:
        document = self._document
        self._document = None
        if document is not None:
            close = getattr(document, "close", None)
            if close is not None:
                close()

    def render_page(self, page_number: int):
        if self.cancelled():
            raise PDFRenderCancelled("PDF rendering was cancelled.")
        if self._document is None:
            raise PDFRenderError("The PDF renderer is not open.")
        try:
            page = self._document[page_number - 1]
            scale = self.limits.dpi / 72
            width = int(page.get_width() * scale)
            height = int(page.get_height() * scale)
            if (
                width <= 0
                or height <= 0
                or width > self.limits.max_width
                or height > self.limits.max_height
                or width * height > self.limits.max_pixels
            ):
                raise PDFRenderLimitError(
                    f"Rendered page {page_number} exceeds configured limits."
                )
            bitmap = page.render(scale=scale, rev_byteorder=True)
            try:
                image = bitmap.to_pil().copy()
            finally:
                bitmap.close()
                page.close()
            if self.cancelled():
                image.close()
                raise PDFRenderCancelled("PDF rendering was cancelled.")
            return image
        except PDFRenderError:
            raise
        except Exception as error:
            raise PDFRenderError(
                f"Could not render PDF page {page_number}: {error}"
            ) from error
