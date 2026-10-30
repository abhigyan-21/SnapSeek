"""Native PySide6 desktop interface for local screenshot search."""

from __future__ import annotations

from dataclasses import dataclass, replace
import ctypes
import logging
import os
from pathlib import Path
import sys
import threading
import traceback
from typing import Callable

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QRunnable,
    QTimer,
    QSize,
    QThreadPool,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QFocusEvent, QIcon, QPixmap, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QProgressBar,
    QSizePolicy,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from snapseek.config import ConfigurationError, get_screenshot_directory
from snapseek.desktop_workflow import (
    DirectorySelectionError,
    OperationCancelled,
    ScanSnapshot,
    normalize_directory_selection,
    scan_and_extract,
    search_snapshot,
)
from snapseek.desktop_storage import (
    cached_ocr,
    saved_directory,
    save_directory,
    save_state,
)
from snapseek.scanner import scan_images
from snapseek.search import OCRTextRecord
from snapseek.semantic import (
    SemanticSearchError,
    SemanticSearchResult,
    load_semantic_encoder,
)


LOGGER = logging.getLogger(__name__)

PALETTE = {
    "background": "#e8f2fc",
    "surface": "#ffffff",
    "navy": "#172b4d",
    "blue": "#2878c8",
    "blue_hover": "#2169b2",
    "border": "#dce6f0",
    "muted": "#5d6f84",
}

DWM_WINDOW_CORNER_PREFERENCE_ATTRIBUTE = 33
DWM_WINDOW_CORNER_ROUND = 2
WCA_ACCENT_POLICY = 19
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
ACCENT_FLAGS_DRAW_ALL_BORDERS = 2
# AccentPolicy packs tint alpha in the high byte. 0x26 is about 15% tint,
# preserving the icy-blue hue while letting more of the blurred desktop show.
ACRYLIC_TINT_COLOR_ABGR = 0x30F0E4C8
WM_NCHITTEST = 0x0084
WM_MOUSEACTIVATE = 0x0021
MA_ACTIVATE = 1
GWL_STYLE = -16
WS_THICKFRAME = 0x00040000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
HT_LEFT = 10
HT_RIGHT = 11
HT_TOP = 12
HT_TOPLEFT = 13
HT_TOPRIGHT = 14
HT_BOTTOM = 15
HT_BOTTOMLEFT = 16
HT_BOTTOMRIGHT = 17


class _NativeMessage(ctypes.Structure):
    """Pointer-sized Windows MSG layout used only for native hit testing."""

    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wparam", ctypes.c_size_t),
        ("lparam", ctypes.c_ssize_t),
        ("time", ctypes.c_uint),
        ("point_x", ctypes.c_long),
        ("point_y", ctypes.c_long),
        ("private", ctypes.c_uint),
    ]


class _NativeRect(ctypes.Structure):
    """Win32 RECT stored in physical screen coordinates for hit testing."""

    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _AccentPolicy(ctypes.Structure):
    """Windows composition settings for the optional Acrylic material."""

    _fields_ = [
        ("accent_state", ctypes.c_int),
        ("accent_flags", ctypes.c_int),
        ("gradient_color", ctypes.c_uint),
        ("animation_id", ctypes.c_int),
    ]


class _WindowCompositionAttributeData(ctypes.Structure):
    """ctypes-backed attribute payload for SetWindowCompositionAttribute."""

    _fields_ = [
        ("attribute", ctypes.c_int),
        ("data", ctypes.c_void_p),
        ("size_of_data", ctypes.c_size_t),
    ]


def resolve_asset_path(relative_path: str) -> Path:
    """Resolve a UI asset from the source tree or PyInstaller bundle root."""
    bundle_root = getattr(sys, "_MEIPASS", None)
    root = Path(bundle_root) if bundle_root else Path(__file__).resolve().parents[2]
    return root / relative_path


WINDOWS_APP_USER_MODEL_ID = "SnapSeek.Desktop"


def _set_windows_app_user_model_id(setter=None) -> bool:
    """Set a stable Windows taskbar identity before QApplication is created."""
    if sys.platform != "win32":
        return False
    if setter is None:
        import ctypes

        try:
            setter = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
            setter.argtypes = [ctypes.c_wchar_p]
            setter.restype = ctypes.c_long
        except (AttributeError, OSError) as error:
            LOGGER.warning("Could not configure the Windows application identity: %s", error)
            return False
    try:
        result = setter(WINDOWS_APP_USER_MODEL_ID)
    except OSError as error:
        LOGGER.warning("Could not configure the Windows application identity: %s", error)
        return False
    if result != 0:
        LOGGER.warning(
            "Windows rejected AppUserModelID %s (HRESULT 0x%08X)",
            WINDOWS_APP_USER_MODEL_ID,
            result & 0xFFFFFFFF,
        )
        return False
    return True


def _windows_supports_acrylic_blur() -> bool:
    """Return whether this Windows release is new enough for Acrylic blur."""
    if sys.platform != "win32":
        return False
    application = QApplication.instance()
    if application is not None and application.platformName().lower() != "windows":
        return False
    try:
        return sys.getwindowsversion().build >= 17763
    except (AttributeError, OSError):
        return False


def _windows_transparency_effects_enabled() -> bool:
    """Respect Windows' Reduce transparency and High Contrast settings."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            enabled, _kind = winreg.QueryValueEx(key, "EnableTransparency")
        if not bool(enabled):
            return False
    except OSError:
        # Missing value means Windows' default; an inaccessible value should
        # not prevent app startup, so continue to the native capability check.
        pass
    try:
        class HighContrast(ctypes.Structure):
            _fields_ = [
                ("cb_size", ctypes.c_uint),
                ("flags", ctypes.c_uint),
                ("default_scheme", ctypes.c_wchar_p),
            ]

        preference = HighContrast(ctypes.sizeof(HighContrast), 0, None)
        query = ctypes.windll.user32.SystemParametersInfoW
        query.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        query.restype = ctypes.c_bool
        if query(0x0042, ctypes.sizeof(preference), ctypes.byref(preference), 0):
            return not bool(preference.flags & 0x00000001)
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    return True


def _enable_windows_acrylic_blur(hwnd: int, setter=None) -> bool:
    """Request Windows Acrylic blur; fail closed if the optional API is absent."""
    if not _windows_supports_acrylic_blur() or not _windows_transparency_effects_enabled():
        return False
    if setter is None:
        try:
            setter = ctypes.windll.user32.SetWindowCompositionAttribute
            setter.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            setter.restype = ctypes.c_bool
        except (AttributeError, OSError):
            return False

    policy = _AccentPolicy(
        ACCENT_ENABLE_ACRYLICBLURBEHIND,
        ACCENT_FLAGS_DRAW_ALL_BORDERS,
        ACRYLIC_TINT_COLOR_ABGR,
        0,
    )
    data = _WindowCompositionAttributeData(
        WCA_ACCENT_POLICY,
        ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p),
        ctypes.sizeof(policy),
    )
    try:
        result = setter(hwnd, ctypes.byref(data))
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return bool(result)


def _enable_windows_rounded_corners(hwnd: int, setter=None) -> bool:
    """Ask DWM for its native rounded-window treatment when available."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        if setter is None:
            setter = ctypes.windll.dwmapi.DwmSetWindowAttribute
            setter.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
            setter.restype = ctypes.c_long
        corner = ctypes.c_int(DWM_WINDOW_CORNER_ROUND)
        result = setter(
            hwnd,
            DWM_WINDOW_CORNER_PREFERENCE_ATTRIBUTE,
            ctypes.byref(corner),
            ctypes.sizeof(corner),
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return result == 0


def _enable_windows_resize_frame(
    hwnd: int,
    get_style=None,
    set_style=None,
    refresh_frame=None,
) -> bool:
    """Enable the native sizing frame while retaining Qt's frameless client."""
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.windll.user32 if any(
            value is None for value in (get_style, set_style, refresh_frame)
        ) else None
        if get_style is None:
            assert user32 is not None
            get_style = user32.GetWindowLongPtrW
            get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]
            get_style.restype = ctypes.c_ssize_t
        if set_style is None:
            assert user32 is not None
            set_style = user32.SetWindowLongPtrW
            set_style.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            set_style.restype = ctypes.c_ssize_t
        if refresh_frame is None:
            assert user32 is not None
            set_window_pos = user32.SetWindowPos
            set_window_pos.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint,
            ]
            set_window_pos.restype = ctypes.c_bool

            def refresh_frame(window_handle):
                flags = (
                    SWP_NOMOVE
                    | SWP_NOSIZE
                    | SWP_NOZORDER
                    | SWP_NOACTIVATE
                    | SWP_FRAMECHANGED
                )
                return set_window_pos(
                    window_handle, None, 0, 0, 0, 0, flags
                )

        window_handle = ctypes.c_void_p(hwnd)
        old_style = int(get_style(window_handle, GWL_STYLE))
        if old_style & WS_THICKFRAME:
            return True
        previous_style = int(
            set_style(window_handle, GWL_STYLE, old_style | WS_THICKFRAME)
        )
        if old_style and not previous_style:
            return False
        if refresh_frame(window_handle):
            return True
        set_style(window_handle, GWL_STYLE, old_style)
        refresh_frame(window_handle)
    except (AttributeError, OSError, TypeError, ValueError, ctypes.ArgumentError):
        return False
    return False


def _activate_windows_window(hwnd: int) -> bool:
    """Ask Windows to foreground a clicked layered window."""
    if sys.platform != "win32":
        return False
    try:
        foreground = ctypes.windll.user32.SetForegroundWindow
        foreground.argtypes = [ctypes.c_void_p]
        foreground.restype = ctypes.c_bool
        return bool(foreground(ctypes.c_void_p(hwnd)))
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _resize_hit_test(
    rect: tuple[int, int, int, int],
    screen_x: int,
    screen_y: int,
    border: int,
) -> Qt.Edges | None:
    """Map a pointer near a frameless window edge to Qt's native resize edges."""
    left, top, right, bottom = rect
    if not (left <= screen_x < right and top <= screen_y < bottom):
        return None
    on_left = screen_x < left + border
    on_right = screen_x >= right - border
    on_top = screen_y < top + border
    on_bottom = screen_y >= bottom - border
    if on_top and on_left:
        return Qt.Edge.TopEdge | Qt.Edge.LeftEdge
    if on_top and on_right:
        return Qt.Edge.TopEdge | Qt.Edge.RightEdge
    if on_bottom and on_left:
        return Qt.Edge.BottomEdge | Qt.Edge.LeftEdge
    if on_bottom and on_right:
        return Qt.Edge.BottomEdge | Qt.Edge.RightEdge
    if on_left:
        return Qt.Edge.LeftEdge
    if on_right:
        return Qt.Edge.RightEdge
    if on_top:
        return Qt.Edge.TopEdge
    if on_bottom:
        return Qt.Edge.BottomEdge
    return None


def _windows_resize_hit_test(
    rect: tuple[int, int, int, int],
    screen_x: int,
    screen_y: int,
    border: int,
) -> int | None:
    """Return a Win32 non-client resize code for an in-window screen point."""
    edges = _resize_hit_test(rect, screen_x, screen_y, border)
    return {
        Qt.Edge.LeftEdge: HT_LEFT,
        Qt.Edge.RightEdge: HT_RIGHT,
        Qt.Edge.TopEdge: HT_TOP,
        Qt.Edge.BottomEdge: HT_BOTTOM,
        Qt.Edge.TopEdge | Qt.Edge.LeftEdge: HT_TOPLEFT,
        Qt.Edge.TopEdge | Qt.Edge.RightEdge: HT_TOPRIGHT,
        Qt.Edge.BottomEdge | Qt.Edge.LeftEdge: HT_BOTTOMLEFT,
        Qt.Edge.BottomEdge | Qt.Edge.RightEdge: HT_BOTTOMRIGHT,
    }.get(edges)


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


@dataclass(frozen=True)
class WorkerOutcome:
    """Value or exception returned by a background operation."""

    kind: str
    value: object | None = None
    error: Exception | None = None
    traceback_text: str | None = None


class WorkerSignals(QObject):
    """Signals emitted by a QRunnable; slots update widgets on the GUI thread."""

    completed = Signal(object)


class BackgroundWorker(QRunnable):
    """Run a widget-independent callable on Qt's worker thread pool."""

    def __init__(self, kind: str, task: Callable[[threading.Event], object]) -> None:
        super().__init__()
        self.kind = kind
        self.task = task
        self.cancel_event = threading.Event()
        self.signals = WorkerSignals()

    def cancel(self) -> None:
        """Request cancellation at the next cooperative checkpoint."""
        self.cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            value = self.task(self.cancel_event)
        except Exception as error:
            detail = traceback.format_exc()
            if isinstance(
                error,
                (OperationCancelled, DirectorySelectionError, OSError, SemanticSearchError),
            ):
                LOGGER.warning("Background %s task failed: %s", self.kind, error)
            else:
                LOGGER.error("Background %s task failed:\n%s", self.kind, detail)
            self.signals.completed.emit(
                WorkerOutcome(self.kind, error=error, traceback_text=detail)
            )
        else:
            self.signals.completed.emit(WorkerOutcome(self.kind, value=value))


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


class ResultsGrid(QWidget):
    """Responsive grid that rearranges screenshot cards as the window resizes."""

    preview_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 4, 0, 12)
        self._layout.setHorizontalSpacing(16)
        self._layout.setVerticalSpacing(16)
        self._cards: list[ImageResultCard] = []
        self._pending_results: list[SemanticSearchResult] = []
        self._render_index = 0

    def set_results(self, results: list[SemanticSearchResult]) -> None:
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
            card = ImageResultCard(result, self)
            card.preview_requested.connect(self.preview_requested)
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


class HomePage(QWidget):
    """Search landing page and ranked results view."""

    search_requested = Signal(str)
    preview_requested = Signal(object)
    COMPACT_SEARCH_HEIGHT = 64
    LANDING_SEARCH_WIDTH = 840

    EXAMPLES = ("error message", "Python list comprehension", "plot chart")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 24, 36, 24)
        root.setSpacing(12)

        self.landing = QWidget()
        landing_layout = QVBoxLayout(self.landing)
        landing_layout.setContentsMargins(0, 32, 0, 0)
        landing_layout.addStretch(2)
        brand_row = QHBoxLayout()
        brand_row.addStretch(1)
        self.logo = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.logo.setAccessibleName("SnapSeek logo")
        self.logo.setFixedSize(78, 78)
        self.logo.setPixmap(
            _scaled_pixmap(resolve_asset_path("assets/logo/snapseek-icon.png"), 70, self.devicePixelRatioF())
        )
        brand_row.addWidget(self.logo)
        self.wordmark = QLabel(
            _wordmark_html(),
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        self.wordmark.setTextFormat(Qt.TextFormat.RichText)
        self.wordmark.setAccessibleName("SnapSeek")
        self.wordmark.setObjectName("wordmark")
        brand_row.addWidget(self.wordmark)
        brand_row.addStretch(1)
        landing_layout.addLayout(brand_row)
        subtitle = QLabel(
            "Search your screenshots with natural language",
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        subtitle.setObjectName("subtitle")
        landing_layout.addWidget(subtitle)
        landing_layout.addSpacing(24)
        landing_search = self._make_search_row(compact=False)
        landing_search.setMinimumWidth(self.LANDING_SEARCH_WIDTH)
        landing_search.setMaximumWidth(self.LANDING_SEARCH_WIDTH)
        landing_search.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        landing_search_row = QHBoxLayout()
        landing_search_row.setContentsMargins(0, 0, 0, 0)
        landing_search_row.addStretch(1)
        landing_search_row.addWidget(landing_search)
        landing_search_row.addStretch(1)
        landing_layout.addLayout(landing_search_row)
        examples = QHBoxLayout()
        examples.addStretch(1)
        self.example_buttons: list[QPushButton] = []
        for index, query in enumerate(self.EXAMPLES):
            button = QPushButton(query)
            button.setObjectName("exampleButton")
            button.clicked.connect(lambda _checked=False, value=query: self._submit_example(value))
            self.example_buttons.append(button)
            examples.addWidget(button)
        examples.addStretch(1)
        landing_layout.addLayout(examples)
        landing_layout.addStretch(3)
        self.landing.setObjectName("landingView")
        root.addWidget(self.landing, 1)

        self.compact = QWidget()
        compact_layout = QVBoxLayout(self.compact)
        compact_layout.setContentsMargins(0, 4, 0, 0)
        compact_layout.addWidget(self._make_search_row(compact=True))
        self.compact.hide()
        root.addWidget(self.compact)

        self.progress = QProgressBar()
        self.progress.setObjectName("activityBar")
        self.progress.setAccessibleName("Search and scan activity")
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        root.addWidget(self.progress)

        self.status = QLabel("Choose a screenshot folder to get started.")
        self.status.setObjectName("statusMessage")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.ocr_failures = QLabel()
        self.ocr_failures.setObjectName("warningMessage")
        self.ocr_failures.setTextFormat(Qt.TextFormat.PlainText)
        self.ocr_failures.setWordWrap(True)
        self.ocr_failures.hide()
        root.addWidget(self.ocr_failures)

        self.results_scroll = QScrollArea()
        self.results_scroll.setWidgetResizable(True)
        self.results_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.results_grid = ResultsGrid()
        self.results_grid.preview_requested.connect(self.preview_requested)
        self.results_scroll.setWidget(self.results_grid)
        self.results_scroll.hide()
        root.addWidget(self.results_scroll, 3)
        self._animation: QParallelAnimationGroup | None = None
        self._compact_opacity = QGraphicsOpacityEffect(self.compact)
        self._compact_opacity.setOpacity(1.0)
        self.compact.setGraphicsEffect(self._compact_opacity)

    def _make_search_row(self, *, compact: bool) -> QWidget:
        box = QFrame()
        box.setObjectName("searchBox")
        box.setMinimumHeight(self.COMPACT_SEARCH_HEIGHT if compact else 68)
        _apply_soft_shadow(box)
        row = QHBoxLayout(box)
        row.setContentsMargins(18, 8, 8, 8)
        search_glyph = QLabel()
        search_glyph.setObjectName("searchGlyph")
        search_glyph.setAccessibleName("Search")
        search_glyph.setPixmap(
            QIcon(str(resolve_asset_path("assets/icons/search.svg"))).pixmap(QSize(22, 22))
        )
        row.addWidget(search_glyph)
        self.search_input = SearchLineEdit()
        self.search_input.setObjectName("searchInputCompact" if compact else "searchInputLanding")
        self.search_input.setAccessibleName("Search screenshot text")
        self.search_input.setAccessibleDescription(
            "Enter words from a screenshot or describe what you are looking for. Press Enter to search."
        )
        self.search_input.setPlaceholderText("Search screenshots…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.returnPressed.connect(
            lambda field=self.search_input: self._submit_field(field)
        )
        row.addWidget(self.search_input, 1)
        button = QPushButton("Search")
        button.setObjectName("primaryButton")
        button.clicked.connect(
            lambda _checked=False, field=self.search_input: self._submit_field(field)
        )
        row.addWidget(button)
        if compact:
            self.compact_input = self.search_input
            self.compact_button = button
        else:
            self.landing_input = self.search_input
            self.landing_button = button
        return box

    def _submit_field(self, field: SearchLineEdit) -> None:
        query = field.text()
        self.set_query(query)
        self.search_requested.emit(query)

    def _submit_example(self, query: str) -> None:
        self.set_query(query)
        self.search_requested.emit(query)

    def set_query(self, query: str) -> None:
        self.landing_input.setText(query)
        self.compact_input.setText(query)

    def show_compact_search(self, reduce_motion: bool) -> None:
        if not self.compact.isHidden():
            if reduce_motion:
                self.finish_search_transition()
            self.compact_input.setFocus(Qt.FocusReason.OtherFocusReason)
            self.compact_input.setCursorPosition(len(self.compact_input.text()))
            return
        self.landing.setMaximumHeight(max(self.landing.height(), 1))
        self.compact.setMaximumHeight(0)
        self.compact.show()
        self.compact.raise_()
        if reduce_motion:
            self.landing.hide()
            self.compact.setMaximumHeight(16777215)
            self.compact_input.setFocus(Qt.FocusReason.OtherFocusReason)
            self.compact_input.setCursorPosition(len(self.compact_input.text()))
            return
        if self._animation is not None:
            self.finish_search_transition()
        end_height = self.compact.sizeHint().height()
        group = QParallelAnimationGroup(self)
        landing_animation = QPropertyAnimation(self.landing, b"maximumHeight", group)
        landing_animation.setDuration(240)
        landing_animation.setStartValue(max(self.landing.height(), 1))
        landing_animation.setEndValue(0)
        landing_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        height_animation = QPropertyAnimation(self.compact, b"maximumHeight", group)
        height_animation.setDuration(240)
        height_animation.setStartValue(0)
        height_animation.setEndValue(end_height)
        height_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        group.addAnimation(landing_animation)
        group.addAnimation(height_animation)
        group.finished.connect(self._search_transition_finished)
        self._animation = group
        group.start()

    def keep_search_bar_visible(self) -> None:
        """Keep the compact search control available above search results."""
        self.finish_search_transition()
        self.landing.hide()
        self.compact.setMinimumHeight(self.COMPACT_SEARCH_HEIGHT)
        self.compact.setMaximumHeight(16777215)
        self.compact.show()
        # A nested opacity effect can stop painting on translucent Windows
        # surfaces after results replace the landing view. The final state does
        # not need the effect, so let Qt paint the bar directly.
        self.compact.setGraphicsEffect(None)
        self._compact_opacity = QGraphicsOpacityEffect()
        self._compact_opacity.setOpacity(1.0)
        self.landing.hide()
        self.layout().invalidate()
        self.layout().activate()

    def _search_transition_finished(self) -> None:
        self.landing.hide()
        self.compact.setMaximumHeight(16777215)
        self.compact.setGraphicsEffect(None)
        self._compact_opacity = QGraphicsOpacityEffect()
        self._compact_opacity.setOpacity(1.0)
        self._animation = None

    def finish_search_transition(self) -> None:
        """Snap to a consistent final layout before hiding or changing motion settings."""
        if self._animation is not None:
            self._animation.stop()
            self._animation = None
        if not self.compact.isHidden():
            self.compact.setMaximumHeight(16777215)
            self.landing.hide()

    def show_landing(self) -> None:
        self.finish_search_transition()
        self.compact.hide()
        self.compact.setMinimumHeight(0)
        self.compact.setMaximumHeight(16777215)
        self.landing.show()

    def set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        self.status.setText(message if message else self.status.text())
        self.landing_input.setEnabled(not busy)
        self.compact_input.setEnabled(not busy)
        self.landing_button.setEnabled(not busy)
        self.compact_button.setEnabled(not busy)
        for button in self.example_buttons:
            button.setEnabled(not busy)

    def show_message(self, text: str, *, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setProperty("error", error)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def show_ocr_failures(self, snapshot: ScanSnapshot) -> None:
        if not snapshot.image_failures:
            self.ocr_failures.clear()
            self.ocr_failures.hide()
            return
        messages = [
            f"OCR failed for {failure.image_path}: {failure.message}"
            for failure in snapshot.image_failures
        ]
        self.ocr_failures.setText("\n".join(messages))
        self.ocr_failures.show()

    def clear_ocr_failures(self) -> None:
        self.ocr_failures.clear()
        self.ocr_failures.hide()

    def show_results(self, results: list[SemanticSearchResult]) -> None:
        self.results_grid.set_results(results)
        self.results_scroll.setVisible(bool(results))


class FolderPage(QWidget):
    """Scan status and explicit scan/rescan action."""

    scan_requested = Signal(str)
    selection_changed = Signal(str)

    def __init__(self, initial_path: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 36, 40, 36)
        layout.setSpacing(14)
        title = QLabel("Screenshot folder")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        intro = QLabel("Manage the screenshot folder in Settings, then scan it here.")
        intro.setObjectName("mutedText")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.path_input = QLineEdit(initial_path)
        self.path_input.setVisible(False)

        self.scan_button = QPushButton("Scan / Rescan folder")
        self.scan_button.setObjectName("primaryButton")
        self.scan_button.clicked.connect(lambda: self.scan_requested.emit(self.path_input.text()))
        layout.addWidget(self.scan_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.progress = QProgressBar()
        self.progress.setObjectName("folderActivityBar")
        self.progress.setAccessibleName("Scanning screenshot folder")
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.message = QLabel("Choose a folder in Settings.")
        self.message.setObjectName("statusMessage")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        layout.addStretch(1)

    def set_busy(self, busy: bool) -> None:
        self.scan_button.setEnabled(not busy)
        self.progress.setVisible(busy)

    def set_message(self, text: str, *, error: bool = False) -> None:
        self.message.setText(text)
        self.message.setProperty("error", error)
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)


class StatsPage(QWidget):
    """Statistics derived from the current in-memory scan and search."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(40, 36, 40, 36)
        self.title = QLabel("Library statistics")
        self.title.setObjectName("pageTitle")
        self.layout.addWidget(self.title)
        self.folder = QLabel("No directory scanned yet.")
        self.folder.setObjectName("mutedText")
        self.folder.setWordWrap(True)
        self.layout.addWidget(self.folder)
        self.metrics = [QLabel("—") for _ in range(4)]
        self.metric_icons: list[QLabel] = []
        metric_layout = QHBoxLayout()
        for (label, icon_name), widget in zip(
            (
                ("Images", "image"),
                ("OCR successes", "file-text"),
                ("Image failures", "triangle-alert"),
                ("Not processed", "clock"),
            ),
            self.metrics,
        ):
            card = QFrame()
            card.setObjectName("statCard")
            _apply_soft_shadow(card, alpha=12)
            card_layout = QVBoxLayout(card)
            card_layout.setSpacing(6)
            icon = QLabel()
            icon.setObjectName("statIcon")
            icon.setAccessibleName(f"{label} icon")
            icon.setToolTip(label)
            icon.setPixmap(
                QIcon(str(resolve_asset_path(f"assets/icons/{icon_name}.svg"))).pixmap(
                    QSize(24, 24)
                )
            )
            self.metric_icons.append(icon)
            widget.setObjectName("statValue")
            widget.setAccessibleName(f"{label} count")
            name = QLabel(label)
            name.setObjectName("mutedText")
            name.setAccessibleName(label)
            card_layout.addWidget(icon, alignment=Qt.AlignmentFlag.AlignLeft)
            card_layout.addWidget(widget)
            card_layout.addWidget(name)
            metric_layout.addWidget(card, 1)
        self.layout.addLayout(metric_layout)
        self.progress = QProgressBar()
        self.progress.setObjectName("statsActivityBar")
        self.progress.setAccessibleName("Statistics scan activity")
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.layout.addWidget(self.progress)
        self.scan_status = QLabel("Scan status · Not scanned")
        self.search_status = QLabel("Search status · No query submitted")
        self.layout.addWidget(self.scan_status)
        self.layout.addWidget(self.search_status)
        self.layout.addStretch(1)

    def update_snapshot(
        self,
        snapshot: ScanSnapshot | None,
        query: str = "",
        result_count: int | None = None,
        *,
        scanning: bool = False,
        selected_directory: str = "",
        searching: bool = False,
        search_failed: bool = False,
    ) -> None:
        if scanning:
            self.folder.setText(f"Scanning folder · {selected_directory}")
            self.progress.show()
            for metric in self.metrics:
                metric.setText("—")
            self.scan_status.setText("Scan status · Scanning images and reading text…")
            self.search_status.setText("Search status · Available after the scan completes")
            return
        if snapshot is None:
            self.progress.hide()
            if selected_directory:
                self.folder.setText(f"Selected folder · {selected_directory}")
            else:
                self.folder.setText("No folder selected.")
            for metric in self.metrics:
                metric.setText("—")
            self.scan_status.setText("Scan status · Not scanned")
            self.search_status.setText("Search status · Scan a folder before searching")
            return
        self.progress.hide()
        self.folder.setText(f"Scanned folder · {snapshot.directory}")
        for metric, value in zip(
            self.metrics,
            (len(snapshot.images), len(snapshot.records), len(snapshot.image_failures), snapshot.unprocessed_count),
        ):
            metric.setText(str(value))
        if snapshot.setup_error:
            scan_text = f"Setup failed ({snapshot.setup_error_kind})"
        elif not snapshot.images:
            scan_text = "Complete · no supported images"
        elif snapshot.image_failures:
            scan_text = f"Complete · {len(snapshot.image_failures)} OCR failure(s)"
        else:
            scan_text = "Complete"
        self.scan_status.setText(f"Scan status · {scan_text}")
        if query and searching:
            self.search_status.setText(f"Searching · {query}")
        elif query and search_failed:
            self.search_status.setText(f"Last search failed · {query}")
        elif query:
            self.search_status.setText(f"Last search · {query} · {result_count or 0} results")
        else:
            self.search_status.setText("Search status · No query submitted")


class SettingsPage(QWidget):
    """Screenshot folder, motion preference, and application information."""

    reduce_motion_changed = Signal(bool)
    selection_changed = Signal(str)
    scan_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 36, 40, 36)
        title = QLabel("Settings")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        folder_card = QFrame()
        folder_card.setObjectName("settingsCard")
        _apply_soft_shadow(folder_card, alpha=12)
        folder_layout = QVBoxLayout(folder_card)
        folder_layout.setContentsMargins(18, 16, 18, 16)
        folder_layout.setSpacing(10)
        folder_title = QLabel("Screenshot folder")
        folder_title.setObjectName("settingsCardTitle")
        folder_layout.addWidget(folder_title)
        folder_description = QLabel(
            "Choose the local folder containing screenshots. Subfolders are not included."
        )
        folder_description.setObjectName("mutedText")
        folder_description.setWordWrap(True)
        folder_layout.addWidget(folder_description)
        folder_row = QHBoxLayout()
        self.path_input = QLineEdit()
        self.path_input.setObjectName("directoryPath")
        self.path_input.setAccessibleName("Screenshot folder path")
        self.path_input.setPlaceholderText("Choose or enter a folder path")
        self.path_input.setClearButtonEnabled(True)
        self.path_input.textChanged.connect(self.selection_changed)
        folder_row.addWidget(self.path_input, 1)
        self.browse_button = QPushButton("Browse…")
        self.browse_button.setAccessibleName("Browse for screenshot folder")
        self.browse_button.clicked.connect(self._browse)
        folder_row.addWidget(self.browse_button)
        folder_layout.addLayout(folder_row)
        self.folder_message = QLabel("No folder selected.")
        self.folder_message.setObjectName("statusMessage")
        self.folder_message.setWordWrap(True)
        folder_layout.addWidget(self.folder_message)
        self.scan_button = QPushButton("Scan / Rescan folder")
        self.scan_button.setObjectName("primaryButton")
        self.scan_button.clicked.connect(
            lambda: self.scan_requested.emit(self.path_input.text())
        )
        folder_layout.addWidget(self.scan_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.progress = QProgressBar()
        self.progress.setObjectName("folderActivityBar")
        self.progress.setAccessibleName("Scanning screenshot folder")
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        folder_layout.addWidget(self.progress)
        layout.addWidget(folder_card)
        motion_card = QFrame()
        motion_card.setObjectName("settingsCard")
        _apply_soft_shadow(motion_card, alpha=12)
        motion_layout = QHBoxLayout(motion_card)
        motion_layout.setContentsMargins(18, 16, 18, 16)
        motion_layout.setSpacing(14)
        motion_icon = QLabel()
        motion_icon.setAccessibleName("Reduce motion setting")
        motion_icon.setPixmap(
            QIcon(str(resolve_asset_path("assets/icons/reduce-motion.svg"))).pixmap(
                QSize(26, 26)
            )
        )
        motion_layout.addWidget(motion_icon, alignment=Qt.AlignmentFlag.AlignTop)
        motion_copy = QVBoxLayout()
        motion_label = QLabel("Reduce motion")
        motion_label.setObjectName("settingsCardTitle")
        self.reduce_motion = QCheckBox()
        self.reduce_motion.setAccessibleName("Reduce motion")
        self.reduce_motion.setAccessibleDescription(
            "Skip the search bar layout transition while this app is running."
        )
        self.reduce_motion.setChecked(os.environ.get("SNAPSEEK_REDUCE_MOTION") == "1")
        self.reduce_motion.toggled.connect(self.reduce_motion_changed)
        motion_label.setBuddy(self.reduce_motion)
        motion_copy.addWidget(motion_label)
        self.motion_description = QLabel(
            "Skip the search field transition animation. This preference applies for this session only."
        )
        self.motion_description.setObjectName("mutedText")
        self.motion_description.setWordWrap(True)
        motion_copy.addWidget(self.motion_description)
        motion_layout.addLayout(motion_copy, 1)
        motion_layout.addWidget(self.reduce_motion, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(motion_card)

        about_card = QFrame()
        about_card.setObjectName("settingsCard")
        _apply_soft_shadow(about_card, alpha=12)
        about_layout = QHBoxLayout(about_card)
        about_layout.setContentsMargins(18, 16, 18, 16)
        about_layout.setSpacing(14)
        about_icon = QLabel()
        about_icon.setAccessibleName("About SnapSeek")
        about_icon.setPixmap(
            QIcon(str(resolve_asset_path("assets/icons/info.svg"))).pixmap(QSize(26, 26))
        )
        about_layout.addWidget(about_icon, alignment=Qt.AlignmentFlag.AlignTop)
        about_copy = QVBoxLayout()
        about_title = QLabel("About")
        about_title.setObjectName("settingsCardTitle")
        about_copy.addWidget(about_title)
        details = QLabel(
            "SnapSeek searches screenshot text locally. Scan counts and OCR records stay in memory for this app session; each launch requires a new scan."
        )
        details.setObjectName("mutedText")
        details.setWordWrap(True)
        about_copy.addWidget(details)
        about_layout.addLayout(about_copy, 1)
        layout.addWidget(about_card)
        layout.addStretch(1)

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "Choose screenshot folder",
            self.path_input.text() or str(Path.home()),
            QFileDialog.Option.ShowDirsOnly,
        )
        if path:
            self.path_input.setText(path)

    def set_busy(self, busy: bool) -> None:
        self.path_input.setEnabled(not busy)
        self.browse_button.setEnabled(not busy)
        self.scan_button.setEnabled(not busy)
        self.progress.setVisible(busy)

    def set_folder_message(self, text: str, *, error: bool = False) -> None:
        self.folder_message.setText(text)
        self.folder_message.setProperty("error", error)
        self.folder_message.style().unpolish(self.folder_message)
        self.folder_message.style().polish(self.folder_message)


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


class MainWindow(QMainWindow):
    """Coordinate native pages while running all expensive work off the UI thread."""

    NAV_ITEMS = (
        ("Home", "home"),
        ("Stats", "chart-no-axes-column"),
        ("Settings", "settings"),
    )

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SnapSeek")
        self.setWindowIcon(load_application_icon())
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setMinimumSize(850, 620)
        self.resize(1120, 780)
        self._backdrop_enabled = False
        if _windows_supports_acrylic_blur():
            # Acrylic needs a per-pixel transparent client surface so the native
            # composition blur can show through Qt's otherwise opaque HWND.
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._active_worker: BackgroundWorker | None = None
        self._closing = False
        self._snapshot: ScanSnapshot | None = None
        self._encoder = None
        self._last_query = ""
        self._last_results: list[SemanticSearchResult] | None = None
        self._scan_in_progress = False
        self._search_in_progress = False
        self._last_search_failed = False
        self._restored_scan = False
        self._reduce_motion = os.environ.get("SNAPSEEK_REDUCE_MOTION") == "1"
        try:
            initial_directory = str(saved_directory() or get_screenshot_directory())
            initial_message = "Choose Scan / Rescan to inspect this folder."
        except ConfigurationError as error:
            initial_directory = ""
            initial_message = str(error)

        root = QWidget()
        root.setObjectName("windowRoot")
        root.installEventFilter(self)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(18, 14, 18, 18)
        root_layout.setSpacing(12)

        self.title_bar = WindowTitleBar(self)
        _apply_soft_shadow(self.title_bar)
        self.nav_buttons: dict[str, QToolButton] = {}
        for name, icon_name in self.NAV_ITEMS:
            button = QToolButton()
            button.setText(name)
            button.setIcon(QIcon(str(resolve_asset_path(f"assets/icons/{icon_name}.svg"))))
            button.setIconSize(QSize(18, 18))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setAccessibleName(name)
            button.clicked.connect(lambda _checked=False, page=name: self.navigate(page))
            self.title_bar.navigation.addWidget(button)
            self.nav_buttons[name] = button
        root_layout.addWidget(self.title_bar)

        self.pages = QStackedWidget()
        self.settings_page = SettingsPage()
        self.settings_page.path_input.setText(initial_directory)
        self.home_page = HomePage()
        self.stats_page = StatsPage()
        for page in (self.home_page, self.stats_page, self.settings_page):
            self.pages.addWidget(page)
        root_layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)

        if sys.platform == "win32":
            hwnd = int(self.winId())
            _enable_windows_resize_frame(hwnd)
            if _windows_supports_acrylic_blur():
                self._backdrop_enabled = _enable_windows_acrylic_blur(hwnd)
                if self._backdrop_enabled:
                    _enable_windows_rounded_corners(hwnd)
                else:
                    # Returning to Qt's ordinary opaque backing is essential when
                    # Acrylic cannot be enabled; otherwise the fallback may render
                    # as the platform's default gray window color.
                    self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
                    self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)

        self.home_page.search_requested.connect(self.submit_search)
        self.home_page.preview_requested.connect(self.open_preview)
        self.settings_page.scan_requested.connect(self.scan_directory)
        self.settings_page.selection_changed.connect(self._directory_selection_changed)
        self.settings_page.reduce_motion_changed.connect(self._set_reduce_motion)
        self.settings_page.set_folder_message(initial_message, error=not bool(initial_directory))
        self.nav_buttons["Home"].setChecked(True)
        self._apply_style()
        self._update_stats()
        self._restore_cached_scan(initial_directory)
        if sys.platform == "win32":
            application = QApplication.instance()
            if application is not None:
                application.installEventFilter(self)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt override name
        super().showEvent(event)
        if self._restored_scan:
            QTimer.singleShot(0, self._enforce_restored_home_layout)

    def _enforce_restored_home_layout(self) -> None:
        if self._closing or not self._restored_scan:
            return
        self.pages.setCurrentWidget(self.home_page)
        self.home_page.show_landing()
        self.home_page.layout().invalidate()
        self.home_page.layout().activate()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override name
        if self._closing:
            return False
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and isinstance(watched, QWidget)
            and (watched is self or self.isAncestorOf(watched))
            and not self.isActiveWindow()
        ):
            self.raise_()
            self.activateWindow()
            handle = self.windowHandle()
            if handle is not None:
                handle.requestActivate()
            if sys.platform == "win32":
                _activate_windows_window(int(self.winId()))
        if (
            watched is self.centralWidget()
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
            and not self.isMaximized()
        ):
            root = self.centralWidget()
            position = event.position()
            border = 8
            edges = _resize_hit_test(
                (0, 0, root.width(), root.height()),
                round(position.x()),
                round(position.y()),
                border,
            )
            handle = self.windowHandle()
            if edges is not None and handle is not None and handle.startSystemResize(edges):
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def nativeEvent(self, event_type, message):  # noqa: N802 - Qt override name
        """Let Windows perform native non-client resizing for this frameless HWND."""
        if sys.platform == "win32":
            if getattr(self, "_native_hit_test_active", False):
                return super().nativeEvent(event_type, message)
            self._native_hit_test_active = True
            try:
                if self.isMaximized() or self.isFullScreen():
                    return super().nativeEvent(event_type, message)
                native_message = ctypes.cast(
                    ctypes.c_void_p(int(message)), ctypes.POINTER(_NativeMessage)
                ).contents
                if native_message.message == WM_MOUSEACTIVATE:
                    return True, MA_ACTIVATE
                if native_message.message == WM_NCHITTEST:
                    if self.isMaximized() or self.isFullScreen():
                        return super().nativeEvent(event_type, message)
                    user32 = ctypes.windll.user32
                    hwnd = ctypes.c_void_p(int(self.winId()))
                    rect = _NativeRect()
                    user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
                    user32.GetWindowRect.restype = ctypes.c_bool
                    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                        dpi = 96
                        try:
                            user32.GetDpiForWindow.argtypes = [ctypes.c_void_p]
                            user32.GetDpiForWindow.restype = ctypes.c_uint
                            dpi = user32.GetDpiForWindow(hwnd) or dpi
                        except (AttributeError, OSError):
                            pass
                        border = max(6, round(8 * dpi / 96))
                        packed = native_message.lparam
                        screen_x = packed & 0xFFFF
                        screen_y = (packed >> 16) & 0xFFFF
                        if screen_x & 0x8000:
                            screen_x -= 0x10000
                        if screen_y & 0x8000:
                            screen_y -= 0x10000
                        hit = _windows_resize_hit_test(
                            (rect.left, rect.top, rect.right, rect.bottom),
                            screen_x,
                            screen_y,
                            border,
                        )
                        if hit is not None:
                            return True, hit
            except (AttributeError, OSError, TypeError, ValueError, ctypes.ArgumentError):
                # Keep Qt's ordinary event path available if native hit testing
                # is missing or cannot read the current window rectangle.
                pass
            finally:
                self._native_hit_test_active = False
        return super().nativeEvent(event_type, message)

    def toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self.title_bar.update_maximize_control(self.isMaximized())

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt override name
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "title_bar"):
            self.title_bar.update_maximize_control(self.isMaximized())

    def _apply_style(self) -> None:
        root_background = (
            "rgba(214, 233, 255, 18)"
            if self._backdrop_enabled
            else PALETTE["background"]
        )
        # Layered Windows surfaces pass mouse input through alpha-zero pixels.
        # Keep the native resize strip barely visible but hit-testable.
        root_border = (
            "8px solid rgba(224, 239, 255, 8)"
            if self._backdrop_enabled
            else "8px solid transparent"
        )
        glass_surface = "rgba(224, 239, 255, 110)" if self._backdrop_enabled else "#ffffff"
        title_surface = "rgba(214, 233, 255, 100)" if self._backdrop_enabled else "#f7fbff"
        nav_hover = "rgba(231, 241, 251, 96)" if self._backdrop_enabled else "#e7f1fb"
        nav_pressed = "rgba(215, 232, 248, 120)" if self._backdrop_enabled else "#d7e8f8"
        nav_checked = "rgba(225, 239, 251, 112)" if self._backdrop_enabled else "#e1effb"
        raised_surface = "rgba(224, 239, 255, 110)" if self._backdrop_enabled else "#ffffff"
        search_surface = "rgba(232, 243, 255, 178)" if self._backdrop_enabled else "#ffffff"
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
        self.setStyleSheet(stylesheet)

    def navigate(self, page: str) -> None:
        index = {"Home": 0, "Stats": 1, "Settings": 2}[page]
        if page != "Home":
            self.home_page.finish_search_transition()
        self.pages.setCurrentIndex(index)
        for name, button in self.nav_buttons.items():
            button.setChecked(name == page)
        if page == "Stats":
            self._update_stats()
        if page == "Home":
            self.home_page.set_query(self._last_query)

    def _set_reduce_motion(self, value: bool) -> None:
        self._reduce_motion = value
        if value:
            self.home_page.finish_search_transition()

    def _directory_selection_changed(self, value: str) -> None:
        selected_path = Path(value).expanduser() if value.strip() else None
        if selected_path is not None and selected_path.is_dir():
            save_directory(selected_path)
        if self._snapshot is None:
            return
        if normalize_directory_selection(value) == normalize_directory_selection(
            self._snapshot.directory
        ):
            return
        self._snapshot = None
        self._last_results = None
        self._last_query = ""
        self._last_search_failed = False
        self.home_page.set_query("")
        self.home_page.show_landing()
        self.home_page.show_results([])
        self.home_page.show_message("Folder changed. Scan it to refresh screenshot text.")
        self._update_stats()

    def _restore_cached_scan(self, directory_text: str) -> None:
        """Restore the last successful OCR snapshot without starting work."""
        if not directory_text:
            return
        directory = Path(directory_text).expanduser()
        try:
            images = tuple(scan_images(directory))
        except OSError:
            return
        cached = cached_ocr(directory, images)
        if not cached and images:
            return
        records = tuple(
            OCRTextRecord(image=image, ocr_text=cached[image.path])
            for image in images
            if image.path in cached
        )
        self._snapshot = ScanSnapshot(
            directory=directory,
            images=images,
            records=records,
            image_failures=(),
            encoder=None,
        )
        self._restored_scan = True
        self.settings_page.set_folder_message(
            f"Loaded previous scan: {len(records)} OCR records. "
            "Choose Scan / Rescan folder to refresh."
        )
        self.home_page.show_landing()
        self.home_page.show_message("Previous scan restored. Enter a query to search.")
        self._update_stats()

    def scan_directory(self, directory_text: str) -> None:
        if self._active_worker is not None:
            return
        directory = Path(directory_text).expanduser()
        self.settings_page.path_input.setText(str(directory))
        self._snapshot = None
        self._restored_scan = False
        self._last_results = None
        self._last_query = ""
        self._last_search_failed = False
        self._scan_in_progress = True
        self.home_page.set_query("")
        self.home_page.show_landing()
        self.home_page.show_results([])
        self.home_page.clear_ocr_failures()
        self.settings_page.set_busy(True)
        self.settings_page.set_folder_message("Scanning images and extracting text…")
        self.home_page.set_busy(True, "Scanning images and extracting text…")
        self.navigate("Home")
        self.home_page.show_message("Scanning images and extracting text…")
        self._update_stats()
        existing_encoder = self._encoder

        def task(cancel_event: threading.Event) -> object:
            if not directory.is_dir():
                return scan_and_extract(
                    directory,
                    encoder=existing_encoder,
                    cancelled=cancel_event.is_set,
                    cached_ocr={},
                )
            images = tuple(scan_images(directory))
            return scan_and_extract(
                directory,
                encoder=existing_encoder,
                cancelled=cancel_event.is_set,
                image_scanner=lambda _directory: list(images),
                cached_ocr=cached_ocr(directory, images),
            )

        self._start_worker("scan", task)

    def submit_search(self, query: str) -> None:
        if self._active_worker is not None:
            return
        if not query.strip():
            self.home_page.show_message("Enter a search query first.", error=True)
            return
        if self._snapshot is None:
            self.home_page.show_message(
                "Choose and scan a screenshot folder from Folder before searching.",
                error=True,
            )
            return
        if self._snapshot.setup_error:
            self.home_page.show_message(self._snapshot.setup_error, error=True)
            return
        clean_query = query.strip()
        snapshot = self._snapshot
        self._last_query = clean_query
        self._last_results = None
        self._last_search_failed = False
        self._search_in_progress = True
        self.home_page.set_query(clean_query)
        self.home_page.show_results([])
        self.home_page.set_busy(True, "Searching OCR text…")
        self._update_stats()

        def task(cancel_event: threading.Event) -> object:
            if cancel_event.is_set():
                raise OperationCancelled("The search was cancelled.")
            encoder = snapshot.encoder or self._encoder
            if encoder is None:
                encoder = load_semantic_encoder()
            results = search_snapshot(clean_query, snapshot, encoder=encoder)
            if cancel_event.is_set():
                raise OperationCancelled("The search was cancelled.")
            return results, encoder

        self._start_worker("search", task)

    def _start_worker(self, kind: str, task: Callable[[threading.Event], object]) -> bool:
        if self._active_worker is not None:
            return False
        worker = BackgroundWorker(kind, task)
        worker.signals.completed.connect(self._worker_completed)
        self._active_worker = worker
        QThreadPool.globalInstance().start(worker)
        return True

    @Slot(object)
    def _worker_completed(self, outcome: WorkerOutcome) -> None:
        if self._closing:
            return
        self._active_worker = None
        if outcome.kind == "scan":
            self._scan_in_progress = False
        elif outcome.kind == "search":
            self._search_in_progress = False
        self.settings_page.set_busy(False)
        self.home_page.set_busy(False)

        if outcome.error is not None:
            if isinstance(outcome.error, OperationCancelled):
                self.home_page.show_message("Operation cancelled.")
                if outcome.kind == "scan":
                    self.settings_page.set_folder_message("Scan cancelled.")
            elif isinstance(outcome.error, DirectorySelectionError):
                self.settings_page.set_folder_message(str(outcome.error), error=True)
                self.home_page.show_message(str(outcome.error), error=True)
            elif isinstance(outcome.error, OSError):
                message = (
                    f"Could not read the selected screenshot folder: {outcome.error}. "
                    "Check that it exists and is accessible."
                )
                self.settings_page.set_folder_message(message, error=True)
                self.home_page.show_message(message, error=True)
            elif isinstance(outcome.error, SemanticSearchError):
                message = f"Semantic search failed: {outcome.error}"
                self.home_page.show_message(message, error=True)
            else:
                message = f"The {outcome.kind} operation failed: {outcome.error}"
                self.home_page.show_message(message, error=True)
                if outcome.kind == "scan":
                    self.settings_page.set_folder_message(message, error=True)
            if outcome.kind == "search":
                self._last_search_failed = True
            self._update_stats()
            return

        if outcome.kind == "scan":
            self._snapshot = outcome.value  # type: ignore[assignment]
            self._last_results = None
            snapshot = self._snapshot
            assert snapshot is not None
            save_state(
                snapshot.directory,
                snapshot.images,
                records=tuple((record.image, record.ocr_text) for record in snapshot.records),
            )
            self.home_page.show_ocr_failures(snapshot)
            if snapshot.encoder is not None:
                self._encoder = snapshot.encoder
            if snapshot.setup_error:
                heading = "Semantic model setup failed" if snapshot.setup_error_kind == "semantic" else "OCR setup failed"
                message = f"{heading}: {snapshot.setup_error}"
                self.settings_page.set_folder_message(message, error=True)
                self.home_page.show_message(message, error=True)
            else:
                self.settings_page.set_folder_message(
                    f"Scan complete: {len(snapshot.images)} images, "
                    f"{len(snapshot.records)} OCR successes, "
                    f"{len(snapshot.image_failures)} image failures, "
                    f"{snapshot.unprocessed_count} not processed."
                )
                if not snapshot.images:
                    self.home_page.show_message("This folder contains no supported screenshots.")
                elif snapshot.image_failures:
                    self.home_page.show_message(
                        f"Scan complete with {len(snapshot.image_failures)} image-specific OCR failure(s).",
                        error=True,
                    )
                else:
                    self.home_page.show_message("Scan complete. Enter a query to search.")
            self._update_stats()
        elif outcome.kind == "search":
            results, encoder = outcome.value  # type: ignore[misc]
            self._encoder = encoder
            if self._snapshot is not None:
                self._snapshot = replace(self._snapshot, encoder=encoder)
            self._last_results = results
            self._last_search_failed = False
            results = self._last_results or []
            self.home_page.show_results(results)
            self.home_page.show_compact_search(self._reduce_motion)
            if self.pages.currentWidget() is self.home_page and not self.home_page.compact.isHidden():
                self.home_page.compact_input.setFocus(Qt.FocusReason.OtherFocusReason)
                self.home_page.compact_input.setCursorPosition(len(self.home_page.compact_input.text()))
            if results:
                self.home_page.show_message(f"Found {len(results)} screenshot(s).")
            else:
                self.home_page.show_message("No matching screenshots found.")
            self._update_stats()

    def _update_stats(self) -> None:
        self.stats_page.update_snapshot(
            self._snapshot,
            self._last_query,
            len(self._last_results) if self._last_results is not None else None,
            scanning=self._scan_in_progress,
            selected_directory=self.settings_page.path_input.text(),
            searching=self._search_in_progress,
            search_failed=self._last_search_failed,
        )

    @Slot(object)
    def open_preview(self, image_path: Path) -> None:
        dialog = ImagePreviewDialog(Path(image_path), self)
        dialog.exec()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override name
        self._closing = True
        application = QApplication.instance()
        if application is not None:
            application.removeEventFilter(self)
        self.home_page.finish_search_transition()
        self.home_page.compact.setGraphicsEffect(None)
        for widget in (self.title_bar, self.home_page):
            widget.setGraphicsEffect(None)
        if self._active_worker is not None:
            self._active_worker.cancel()
        # A running OCR/model call may not be interruptible. It owns no widgets;
        # closing only requests a stop at its next checkpoint and disconnects UI slots.
        event.accept()


def _load_application_font(application: QApplication) -> None:
    application.setFont(application.font("QMenu"))


def main(argv: list[str] | None = None) -> int:
    """Start the native desktop application without loading the model eagerly."""
    _set_windows_app_user_model_id()
    application = QApplication(argv if argv is not None else sys.argv)
    application.setApplicationName("SnapSeek")
    application.setWindowIcon(load_application_icon())
    _load_application_font(application)
    window = MainWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
