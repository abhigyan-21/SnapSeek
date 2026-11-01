"""Navigation and custom title-bar presentation for the desktop UI."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFocusEvent, QIcon
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from snapseek.ui.theme import (
    _apply_soft_shadow,
    _scaled_pixmap,
    _wordmark_html,
    resolve_asset_path,
)


class _WindowControlButton(QToolButton):
    """Title control that distinguishes keyboard focus from mouse focus."""

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - Qt override name
        keyboard_reasons = {
            Qt.FocusReason.TabFocusReason,
            Qt.FocusReason.BacktabFocusReason,
            Qt.FocusReason.ShortcutFocusReason,
        }
        self.setProperty("keyboardFocus", event.reason() in keyboard_reasons)
        self.style().unpolish(self)
        self.style().polish(self)
        super().focusInEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - Qt override name
        self.setProperty("keyboardFocus", False)
        self.style().unpolish(self)
        self.style().polish(self)
        super().focusOutEvent(event)


class WindowTitleBar(QFrame):
    """Branded title area with native system move and standard window actions."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self.window = window
        self.setObjectName("titleBar")
        self.setAccessibleName("SnapSeek window title bar")
        self.setFixedHeight(48)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 6, 4)
        layout.setSpacing(8)

        logo = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        logo.setAccessibleName("SnapSeek logo")
        logo.setPixmap(_scaled_pixmap(resolve_asset_path("assets/logo/snapseek-icon.png"), 26, self.devicePixelRatioF()))
        layout.addWidget(logo)
        brand = QLabel(_wordmark_html())
        brand.setTextFormat(Qt.TextFormat.RichText)
        brand.setAccessibleName("SnapSeek")
        brand.setObjectName("titleBrand")
        layout.addWidget(brand)
        layout.addSpacing(8)

        self.navigation = QHBoxLayout()
        self.navigation.setSpacing(3)
        layout.addLayout(self.navigation)
        layout.addStretch(1)

        self.minimize_button = self._control("minimize", "Minimize", window.showMinimized)
        self.maximize_button = self._control("maximize", "Maximize", window.toggle_maximized)
        self.close_button = self._control("close", "Close", window.close)
        self.close_button.setObjectName("closeWindowButton")
        layout.addWidget(self.minimize_button)
        layout.addWidget(self.maximize_button)
        layout.addWidget(self.close_button)

    def _control(self, icon_name: str, name: str, action: Callable[[], object]) -> QToolButton:
        button = _WindowControlButton(self)
        button.setIcon(QIcon(str(resolve_asset_path(f"assets/icons/{icon_name}.svg"))))
        button.setIconSize(QSize(16, 16))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setToolTip(name)
        button.setAccessibleName(name)
        button.setFixedSize(34, 32)
        button.setObjectName("windowControlButton")
        button.clicked.connect(action)
        return button

    def update_maximize_control(self, maximized: bool) -> None:
        if maximized:
            self.maximize_button.setIcon(
                QIcon(str(resolve_asset_path("assets/icons/restore.svg")))
            )
            self.maximize_button.setToolTip("Restore")
            self.maximize_button.setAccessibleName("Restore")
        else:
            self.maximize_button.setIcon(
                QIcon(str(resolve_asset_path("assets/icons/maximize.svg")))
            )
            self.maximize_button.setToolTip("Maximize")
            self.maximize_button.setAccessibleName("Maximize")

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override name
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window.windowHandle()
            if handle is not None:
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt override name
        if event.button() == Qt.MouseButton.LeftButton:
            self.window.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
