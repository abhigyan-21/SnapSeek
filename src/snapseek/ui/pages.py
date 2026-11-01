"""Page widgets for the SnapSeek desktop UI."""

from __future__ import annotations

import os

from PySide6.QtCore import QEasingCurve, QParallelAnimationGroup, QPropertyAnimation, QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
    QLineEdit, QProgressBar, QPushButton, QButtonGroup, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from snapseek.desktop_workflow import ScanSnapshot
from snapseek.ui.result_grid import ResultsGrid
from snapseek.ui.theme import (
    SearchLineEdit, _apply_soft_shadow, _scaled_pixmap, _wordmark_html,
    resolve_asset_path,
)


class HomePage(QWidget):
    """Search landing page and ranked results view."""

    search_requested = Signal(str, str)
    category_changed = Signal(str)
    preview_requested = Signal(object)
    COMPACT_SEARCH_HEIGHT = 64
    LANDING_SEARCH_WIDTH = 840

    EXAMPLES = ("error message", "Python list comprehension", "plot chart")
    CATEGORIES = (
        ("screenshots", "Screenshots"),
        ("files", "Files"),
        ("documents", "Documents"),
        ("apps", "Apps"),
        ("all", "All"),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.category = "all"
        self._category_buttons: list[QPushButton] = []
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
        landing_layout.addWidget(self._make_category_toggle(), alignment=Qt.AlignmentFlag.AlignHCenter)
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
        compact_layout.addWidget(self._make_category_toggle(), alignment=Qt.AlignmentFlag.AlignHCenter)
        self.compact.hide()
        root.addWidget(self.compact)
        self._set_category("all")

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
        self.search_input.setAccessibleName("Search SnapSeek")
        self.search_input.setAccessibleDescription(
            "Enter words from a screenshot or describe what you are looking for. Press Enter to search."
        )
        self.search_input.setPlaceholderText("Search everything…")
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

    def _make_category_toggle(self) -> QWidget:
        container = QFrame()
        container.setObjectName("categoryToggle")
        row = QHBoxLayout(container)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(2)
        group = QButtonGroup(container)
        group.setExclusive(True)
        for value, label in self.CATEGORIES:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty("category", value)
            button.setObjectName("categoryButton")
            button.clicked.connect(lambda _checked=False, category=value: self._set_category(category))
            group.addButton(button)
            row.addWidget(button)
            if not hasattr(self, "_category_buttons"):
                self._category_buttons = []
            self._category_buttons.append(button)
        return container

    def _set_category(self, category: str) -> None:
        self.category = category
        for button in getattr(self, "_category_buttons", []):
            button.setChecked(button.property("category") == category)
        placeholders = {
            "screenshots": "Search screenshot text…",
            "files": "Search files and folders…",
            "documents": "Search PDF documents…",
            "apps": "Search installed apps…",
            "all": "Search everything…",
        }
        for field in (self.landing_input, self.compact_input):
            field.setPlaceholderText(placeholders[category])
        self.category_changed.emit(category)

    def _submit_field(self, field: SearchLineEdit) -> None:
        query = field.text()
        self.set_query(query)
        self.search_requested.emit(query, self.category)

    def _submit_example(self, query: str) -> None:
        self.set_query(query)
        self.search_requested.emit(query, self.category)

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

    def show_results(self, results: list[object]) -> None:
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
        self.monitor_status = QLabel()
        self.monitor_status.setObjectName("mutedText")
        self.monitor_status.setAccessibleName("Monitoring status")
        self.monitor_status.setWordWrap(True)
        self.monitor_status.hide()
        folder_layout.addWidget(self.monitor_status)
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

    def set_monitor_status(self, active: bool) -> None:
        """Show or hide the monitoring-active indicator."""
        if active:
            self.monitor_status.setText("Monitoring active — index updates automatically")
            self.monitor_status.show()
        else:
            self.monitor_status.hide()
