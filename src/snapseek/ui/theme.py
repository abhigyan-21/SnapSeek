"""Shared visual helpers for the SnapSeek desktop UI."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QLineEdit, QWidget


PALETTE = {
    "background": "#e8f2fc",
    "surface": "#ffffff",
    "navy": "#172b4d",
    "blue": "#2878c8",
    "blue_hover": "#2169b2",
    "border": "#dce6f0",
    "muted": "#5d6f84",
}


def resolve_asset_path(relative_path: str) -> Path:
    """Resolve a UI asset from the source tree or PyInstaller bundle root."""
    bundle_root = getattr(sys, "_MEIPASS", None)
    root = Path(bundle_root) if bundle_root else Path(__file__).resolve().parents[3]
    return root / relative_path


def load_application_icon() -> QIcon:
    """Load the approved multi-size icon, with its PNG as a safe fallback."""
    icon = QIcon(str(resolve_asset_path("assets/logo/snapseek-icon.ico")))
    if not icon.isNull():
        return icon
    return QIcon(str(resolve_asset_path("assets/logo/snapseek-icon.png")))


def _wordmark_html() -> str:
    return (
        f'<span style="color: {PALETTE["navy"]}">Snap</span>'
        f'<span style="color: {PALETTE["blue"]}">Seek</span>'
    )


def _scaled_pixmap(image_path: Path, logical_size: int, device_pixel_ratio: float) -> QPixmap:
    """Scale a raster asset for crisp display on high-DPI screens."""
    pixmap = QPixmap(str(image_path))
    if pixmap.isNull():
        return pixmap
    pixel_size = max(1, round(logical_size * device_pixel_ratio))
    pixmap = pixmap.scaled(
        pixel_size,
        pixel_size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    pixmap.setDevicePixelRatio(device_pixel_ratio)
    return pixmap


def _apply_soft_shadow(widget: QWidget, *, alpha: int = 22) -> None:
    """Add the restrained elevation used for the floating title/search surfaces."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(18)
    effect.setOffset(0, 3)
    effect.setColor(QColor(36, 68, 105, alpha))
    widget.setGraphicsEffect(effect)


class SearchLineEdit(QLineEdit):
    """Search input with an Escape shortcut to clear its current query."""

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override name
        if event.key() == Qt.Key.Key_Escape:
            self.clear()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt override name
        super().focusInEvent(event)
        box = self.parentWidget()
        if box is not None:
            box.setProperty("focused", True)
            box.style().unpolish(box)
            box.style().polish(box)
            effect = box.graphicsEffect()
            if effect is None:
                from PySide6.QtWidgets import QGraphicsDropShadowEffect

                effect = QGraphicsDropShadowEffect(box)
                effect.setBlurRadius(22)
                effect.setOffset(0, 3)
                box.setGraphicsEffect(effect)
            effect.setColor(QColor(82, 145, 231, 65))

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt override name
        super().focusOutEvent(event)
        box = self.parentWidget()
        if box is not None:
            box.setProperty("focused", False)
            box.style().unpolish(box)
            box.style().polish(box)
            effect = box.graphicsEffect()
            if effect is not None:
                effect.setColor(QColor(36, 68, 105, 22))



def apply_style(widget, backdrop_enabled: bool) -> None:
    root_background = (
        "rgba(214, 233, 255, 18)"
        if backdrop_enabled
        else PALETTE["background"]
    )
    # Layered Windows surfaces pass mouse input through alpha-zero pixels.
    # Keep the native resize strip barely visible but hit-testable.
    root_border = (
        "8px solid rgba(224, 239, 255, 8)"
        if backdrop_enabled
        else "8px solid transparent"
    )
    glass_surface = "rgba(224, 239, 255, 110)" if backdrop_enabled else "#ffffff"
    title_surface = "rgba(214, 233, 255, 100)" if backdrop_enabled else "#f7fbff"
    nav_hover = "rgba(231, 241, 251, 96)" if backdrop_enabled else "#e7f1fb"
    nav_pressed = "rgba(215, 232, 248, 120)" if backdrop_enabled else "#d7e8f8"
    nav_checked = "rgba(225, 239, 251, 112)" if backdrop_enabled else "#e1effb"
    raised_surface = "rgba(224, 239, 255, 110)" if backdrop_enabled else "#ffffff"
    search_surface = "rgba(232, 243, 255, 178)" if backdrop_enabled else "#ffffff"
    stylesheet = """
        QMainWindow { background: %(root_background)s; color: %(navy)s; font-family: 'Segoe UI'; font-size: 10pt; }
        QWidget { color: %(navy)s; font-family: 'Segoe UI'; font-size: 10pt; background: transparent; }
        QWidget#windowRoot { background: %(root_background)s; border: %(root_border)s; border-radius: 18px; }
        QFrame#titleBar { background: %(title_surface)s; border: 1px solid %(border)s; border-radius: 15px; }
        QLabel#titleBrand { font-size: 11pt; font-weight: 700; padding-right: 5px; }
        QToolButton#navButton { background: transparent; border: 1px solid transparent; border-radius: 10px; padding: 8px 9px; color: %(muted)s; }
        QToolButton#navButton:hover { background: %(nav_hover)s; color: %(blue)s; }
        QToolButton#navButton:pressed { background: %(nav_pressed)s; }
        QToolButton#navButton:checked { background: %(nav_checked)s; color: %(blue)s; font-weight: 600; }
        QToolButton#navButton:focus { border: 1px solid #78a8ee; }
        QToolButton#windowControlButton { background: transparent; border: 1px solid transparent; border-radius: 8px; }
        QToolButton#windowControlButton:hover { background: #e9f2fb; border-color: #d5e3f2; }
        QToolButton#windowControlButton:pressed { background: #dbeafa; border-color: #c3d8ef; }
        QToolButton#windowControlButton[keyboardFocus="true"] { background: #f0f6ff; border: 2px solid #78a8ee; }
        QToolButton#closeWindowButton:hover { background: #fcebed; border-color: #efc9cc; }
        QLabel#wordmark { font-family: 'Segoe UI'; font-size: 38pt; font-weight: 700; letter-spacing: -1px; }
        QLabel#subtitle, QLabel#mutedText { color: %(muted)s; }
        QLabel#subtitle { font-size: 12pt; }
        QLabel#pageTitle { font-size: 22pt; font-weight: 650; color: #18243a; }
        QFrame#searchBox { background: %(search_surface)s; border: 1px solid rgba(193, 221, 255, 225); border-radius: 18px; }
        QFrame#searchBox[focused="true"] { border: 1px solid #78a8ee; }
        QFrame#categoryToggle { background: rgba(224, 239, 255, 120); border: 1px solid #d5e5f4; border-radius: 18px; }
        QPushButton#categoryButton { color: #536982; background: transparent; border: 0; border-radius: 13px; padding: 8px 18px; }
        QPushButton#categoryButton:hover { background: rgba(255, 255, 255, 150); }
        QPushButton#categoryButton:checked { color: #185b9d; background: #ffffff; font-weight: 600; }
        QLineEdit { background: transparent; border: 0; padding: 10px 8px; selection-background-color: #b5d1fa; }
        QLabel#warningMessage { color: #9b5c00; background: #fff7e6; border-radius: 9px; padding: 8px; }
        QLabel#statusMessage[error="true"] { color: #a72c32; }
        QPushButton#primaryButton { color: white; background: #2169a8; border: 0; border-radius: 12px; padding: 11px 20px; font-weight: 600; }
        QPushButton#primaryButton:hover { background: #19598f; }
        QPushButton#primaryButton:pressed { background: #164d7e; }
        QPushButton#primaryButton:disabled { background: #a8bfdf; }
        QPushButton#exampleButton, QPushButton#secondaryButton { color: #465772; background: %(glass_surface)s; border: 1px solid #dfe6f0; border-radius: 12px; padding: 9px 14px; }
        QPushButton#exampleButton:hover, QPushButton#secondaryButton:hover { border-color: #9bbbe8; background: #f8fbff; }
        QPushButton#exampleButton:focus, QPushButton#secondaryButton:focus { border: 2px solid #78a8ee; }
        QFrame#resultCard, QFrame#statCard, QFrame#settingsCard { background: %(raised_surface)s; border: 1px solid #d8e4f0; border-radius: 16px; }
        QLabel#settingsCardTitle { color: #18243a; font-weight: 650; }
        QLabel#thumbnail { background: #eef2f8; border-radius: 10px; color: #6c7890; }
        QLabel#resultFilename { font-size: 11pt; font-weight: 650; }
        QLabel#scorePill { color: #285eaa; background: #eaf2ff; border-radius: 8px; padding: 4px 8px; }
        QLabel#ocrPreview { color: #4e5d73; }
        QLabel#statValue { color: #285eaa; font-size: 23pt; font-weight: 650; }
        QLabel#statusMessage[error="true"] { color: #a72c32; }
        QProgressBar#activityBar, QProgressBar#folderActivityBar, QProgressBar#statsActivityBar { border: 0; border-radius: 2px; background: #dfeaf5; max-height: 4px; }
        QProgressBar#activityBar::chunk, QProgressBar#folderActivityBar::chunk, QProgressBar#statsActivityBar::chunk { background: #4e8fce; border-radius: 2px; }
        QCheckBox { spacing: 8px; }
        QCheckBox::indicator { width: 44px; height: 24px; }
        QCheckBox::indicator:unchecked { image: url("%(motion_off)s"); }
        QCheckBox::indicator:checked { image: url("%(motion_on)s"); }
        QCheckBox:focus { border: 1px solid #78a8ee; border-radius: 5px; }
        QScrollArea { border: 0; background: transparent; }
        QDialog#previewDialog { background: #f5f7fb; }
        """ % {
            **PALETTE,
            "root_background": root_background,
            "root_border": root_border,
            "glass_surface": glass_surface,
            "title_surface": title_surface,
            "raised_surface": raised_surface,
            "search_surface": search_surface,
            "nav_hover": nav_hover,
            "nav_pressed": nav_pressed,
            "nav_checked": nav_checked,
            "motion_off": resolve_asset_path("assets/icons/motion-toggle-off.svg").as_posix(),
            "motion_on": resolve_asset_path("assets/icons/motion-toggle-on.svg").as_posix(),
        }
    widget.setStyleSheet(stylesheet)

