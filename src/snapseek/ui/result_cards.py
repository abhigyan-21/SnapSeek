"""Result-card widgets for screenshot and unified search results."""

from __future__ import annotations

import logging
import html
from pathlib import Path
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from snapseek.semantic import SemanticSearchResult
from snapseek.unified_index import IndexedItem, PDFSearchResult
from snapseek.unified_search import UnifiedSearchResult, launch_item


class ImageResultCard(QFrame):
    """A screenshot thumbnail, metadata, score, and OCR excerpt."""

    preview_requested = Signal(object)

    def __init__(self, result: SemanticSearchResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.result = result
        self.setObjectName("resultCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        image_label = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        image_label.setObjectName("thumbnail")
        image_label.setMinimumHeight(150)
        pixmap = QPixmap(str(result.record.image.path))
        if pixmap.isNull():
            image_label.setText("Preview unavailable")
        else:
            image_label.setPixmap(
                pixmap.scaled(
                    420,
                    210,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        layout.addWidget(image_label)

        name = QLabel(result.record.image.filename)
        name.setObjectName("resultFilename")
        name.setWordWrap(True)
        layout.addWidget(name)

        score = QLabel(f"Similarity  {result.score:.3f}")
        score.setObjectName("scorePill")
        layout.addWidget(score)

        path = QLabel(str(result.record.image.path))
        path.setObjectName("mutedText")
        path.setWordWrap(True)
        layout.addWidget(path)

        text = " ".join(result.record.ocr_text.split())
        excerpt = text[:220] + ("…" if len(text) > 220 else "")
        excerpt_label = QLabel(excerpt or "No text was recognized in this screenshot.")
        excerpt_label.setObjectName("ocrPreview")
        excerpt_label.setWordWrap(True)
        layout.addWidget(excerpt_label, 1)

        preview_button = QPushButton("Open preview")
        preview_button.setObjectName("secondaryButton")
        preview_button.clicked.connect(
            lambda _checked=False, image_path=result.record.image.path: self.preview_requested.emit(image_path)
        )
        layout.addWidget(preview_button)


class UnifiedResultCard(QFrame):
    """A file, folder, or application result with a native open action."""

    def __init__(self, result: UnifiedSearchResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("resultCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)
        type_label = QLabel(result.result_type.title())
        type_label.setObjectName("scorePill")
        layout.addWidget(type_label)
        name = QLabel(result.item.name)
        name.setObjectName("resultFilename")
        name.setWordWrap(True)
        layout.addWidget(name)
        score = QLabel(f"Match  {result.score:.3f}")
        score.setObjectName("scorePill")
        layout.addWidget(score)
        path = QLabel(str(result.item.path))
        path.setObjectName("mutedText")
        path.setWordWrap(True)
        layout.addWidget(path, 1)
        button = QPushButton("Open")
        button.setObjectName("secondaryButton")
        button.clicked.connect(lambda: self._open(result))
        layout.addWidget(button)

    @staticmethod
    def _open(result: UnifiedSearchResult) -> None:
        try:
            launch_item(result.item)
        except (OSError, RuntimeError) as error:
            logging.getLogger(__name__).warning("Could not open %s: %s", result.item.path, error)


class PDFResultCard(QFrame):
    """A page-level PDF match with safely highlighted plain-text context."""

    def __init__(self, result: PDFSearchResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("resultCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)
        type_label = QLabel("Document")
        type_label.setObjectName("scorePill")
        layout.addWidget(type_label)
        name = QLabel(result.title or result.name)
        name.setObjectName("resultFilename")
        name.setWordWrap(True)
        layout.addWidget(name)
        page = QLabel(f"Page {result.page_number}")
        page.setObjectName("scorePill")
        layout.addWidget(page)
        snippet = QLabel(self._highlight(result.snippet, result.matched_terms))
        snippet.setTextFormat(Qt.TextFormat.RichText)
        snippet.setObjectName("ocrPreview")
        snippet.setWordWrap(True)
        layout.addWidget(snippet)
        path = QLabel(str(result.path))
        path.setObjectName("mutedText")
        path.setWordWrap(True)
        layout.addWidget(path, 1)
        button = QPushButton("Open")
        button.setObjectName("secondaryButton")
        button.clicked.connect(lambda: self._open(result))
        layout.addWidget(button)

    @staticmethod
    def _highlight(text: str, terms: tuple[str, ...]) -> str:
        escaped = html.escape(text)
        if not terms:
            return escaped
        pattern = "|".join(re.escape(html.escape(term)) for term in terms)
        return re.sub(pattern, lambda match: f"<b>{match.group(0)}</b>", escaped, flags=re.IGNORECASE)

    @staticmethod
    def _open(result: PDFSearchResult) -> None:
        item = IndexedItem(
            item_type="file",
            name=result.name,
            path=result.path,
            extension=".pdf",
        )
        try:
            launch_item(item)
        except (OSError, RuntimeError) as error:
            logging.getLogger(__name__).warning(
                "Could not open %s: %s", result.path, error
            )
