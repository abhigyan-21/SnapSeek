"""Responsive result-grid presentation for the desktop UI."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import QGridLayout, QWidget

from snapseek.semantic import SemanticSearchResult
from snapseek.unified_index import PDFSearchResult
from snapseek.ui.result_cards import ImageResultCard, PDFResultCard, UnifiedResultCard


class ResultsGrid(QWidget):
    """Responsive grid that rearranges result cards as the window resizes."""

    preview_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 4, 0, 12)
        self._layout.setHorizontalSpacing(16)
        self._layout.setVerticalSpacing(16)
        self._cards: list[QWidget] = []
        self._pending_results: list[object] = []
        self._render_index = 0

    def set_results(self, results: list[object]) -> None:
        for card in self._cards:
            self._layout.removeWidget(card)
            card.deleteLater()
        self._cards = []
        self._pending_results = list(results)
        self._render_index = 0
        self._render_next_batch()

    def _render_next_batch(self) -> None:
        batch = self._pending_results[self._render_index : self._render_index + 4]
        for result in batch:
            if isinstance(result, SemanticSearchResult):
                card = ImageResultCard(result, self)
                card.preview_requested.connect(self.preview_requested)
            elif isinstance(result, PDFSearchResult):
                card = PDFResultCard(result, self)
            else:
                card = UnifiedResultCard(result, self)
            self._cards.append(card)
        self._render_index += len(batch)
        self._relayout()
        if self._render_index < len(self._pending_results):
            QTimer.singleShot(0, self._render_next_batch)

    def _relayout(self) -> None:
        columns = 1 if self.width() < 560 else 2 if self.width() < 900 else 3
        for index, card in enumerate(self._cards):
            self._layout.addWidget(card, index // columns, index % columns)
            self._layout.setColumnStretch(index % columns, 1)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override name
        super().resizeEvent(event)
        self._relayout()
