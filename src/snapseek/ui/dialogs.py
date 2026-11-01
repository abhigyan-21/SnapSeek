"""Dialogs used by the desktop UI."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout, QWidget


class ImagePreviewDialog(QDialog):
    """Read-only, larger preview of a screenshot source file."""

    def __init__(self, image_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(image_path.name)
        self.setMinimumSize(640, 480)
        self.setObjectName("previewDialog")
        layout = QVBoxLayout(self)
        self.image_label = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(520, 360)
        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
            self.image_label.setText("This screenshot is no longer available or cannot be displayed.")
        else:
            self.image_label.setPixmap(
                pixmap.scaled(
                    1100,
                    780,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        layout.addWidget(self.image_label, 1)
        path_label = QLabel(str(image_path))
        path_label.setObjectName("mutedText")
        path_label.setWordWrap(True)
        layout.addWidget(path_label)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override name
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)
