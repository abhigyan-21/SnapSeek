"""Native PySide6 desktop interface for local screenshot search."""

from __future__ import annotations

from dataclasses import replace
import logging
import os
from pathlib import Path
import sys
import threading
from typing import Callable

from PySide6.QtCore import (
    QEvent,
    QThreadPool,
    QTimer,
    QSize,
    Qt,
    Slot,
)
from PySide6.QtGui import QIcon, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
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
    saved_category,
    saved_directory,
    save_category,
    save_directory,
    save_state,
    state_path,
)
from snapseek.scanner import scan_images
from snapseek.semantic import (
    SemanticSearchError,
    SemanticSearchResult,
    load_semantic_encoder,
)
from snapseek.unified_index import LocalIndex
from snapseek.unified_search import (
    application_event_is_relevant,
    configured_application_directories,
    default_application_directories,
    search_index,
)
from snapseek.windows_hotkey import (
    register_global_hotkey,
    unregister_global_hotkey,
)
from snapseek.platform import windows_window
from snapseek.filesystem_monitor import FolderMonitor
from snapseek.ui.dialogs import ImagePreviewDialog
from snapseek.ui.navigation import WindowTitleBar
from snapseek.ui.pages import HomePage, SettingsPage, StatsPage
from snapseek.ui.operations import (
    WorkerController,
    build_application_refresh_task,
    build_scan_task,
    build_reconcile_task,
    build_search_task,
    build_watch_task,
    persist_snapshot,
    restore_cached_snapshot,
)
from snapseek.ui.workers import BackgroundWorker, WorkerOutcome

from snapseek.ui.theme import (
    _apply_soft_shadow,
    apply_style,
    load_application_icon,
    resolve_asset_path,
)

LOGGER = logging.getLogger(__name__)


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
        if windows_window.windows_supports_acrylic_blur():
            # Acrylic needs a per-pixel transparent client surface so the native
            # composition blur can show through Qt's otherwise opaque HWND.
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._worker_controller = WorkerController(self)
        self._closing = False
        self._snapshot: ScanSnapshot | None = None
        self._encoder = None
        self._embedding_cache: dict[str, tuple[float, ...]] = {}
        self._last_query = ""
        self._last_category = "all"
        self._last_results: list[SemanticSearchResult] | None = None
        self._scan_in_progress = False
        self._search_in_progress = False
        self._last_search_failed = False
        self._restored_scan = False
        self._restored_layout_pending = False
        self._category = saved_category()
        self._index = LocalIndex(state_path().with_name("index.sqlite3"))
        self._reduce_motion = os.environ.get("SNAPSEEK_REDUCE_MOTION") == "1"
        # Filesystem monitor — started after a successful scan completes.
        self._monitor = FolderMonitor(self)
        self._application_monitor = FolderMonitor(self)
        self._pending_monitor_events: frozenset | None = None
        self._application_refresh_pending = False
        self._application_recovery_attempt = 0
        self._application_refresh_attempt = 0
        self._reconciliation_pending = False
        self._reconcile_resume_monitor = False
        self._recovery_attempt = 0
        self._reconciliation_timer = QTimer(self)
        self._reconciliation_timer.setSingleShot(False)
        self._reconciliation_timer.setInterval(5 * 60 * 1000)
        self._reconciliation_timer.timeout.connect(self._request_reconciliation)
        self._recovery_timer = QTimer(self)
        self._recovery_timer.setSingleShot(True)
        self._recovery_timer.timeout.connect(self._attempt_monitor_recovery)
        self._application_recovery_timer = QTimer(self)
        self._application_recovery_timer.setSingleShot(True)
        self._application_recovery_timer.timeout.connect(
            self._attempt_application_monitor_recovery
        )
        self._application_refresh_timer = QTimer(self)
        self._application_refresh_timer.setSingleShot(True)
        self._application_refresh_timer.timeout.connect(
            self._request_application_refresh
        )
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
        self.home_page._set_category(self._category)
        self.stats_page = StatsPage()
        for page in (self.home_page, self.stats_page, self.settings_page):
            self.pages.addWidget(page)
        root_layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)

        if sys.platform == "win32":
            hwnd = int(self.winId())
            windows_window.enable_resize_frame(hwnd)
            if not register_global_hotkey(hwnd):
                LOGGER.info("Global hotkey is unavailable or already registered.")
            if windows_window.windows_supports_acrylic_blur():
                self._backdrop_enabled = windows_window.enable_acrylic_blur(hwnd)
                if self._backdrop_enabled:
                    windows_window.enable_rounded_corners(hwnd)
                else:
                    # Returning to Qt's ordinary opaque backing is essential when
                    # Acrylic cannot be enabled; otherwise the fallback may render
                    # as the platform's default gray window color.
                    self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
                    self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)

        self.home_page.search_requested.connect(self.submit_search)
        self.home_page.category_changed.connect(self._category_changed)
        self.home_page.preview_requested.connect(self.open_preview)
        self.settings_page.scan_requested.connect(self.scan_directory)
        self.settings_page.selection_changed.connect(self._directory_selection_changed)
        self.settings_page.reduce_motion_changed.connect(self._set_reduce_motion)
        self.settings_page.set_folder_message(initial_message, error=not bool(initial_directory))
        self.nav_buttons["Home"].setChecked(True)
        self._apply_style()
        self._update_stats()
        self._restore_cached_scan(initial_directory)
        self._worker_controller.completed.connect(self._worker_completed)
        self._monitor.changes_ready.connect(self._on_monitor_changes)
        self._monitor.monitor_error.connect(self._on_monitor_error)
        self._monitor.monitor_health.connect(self._on_monitor_health)
        self._application_monitor.changes_ready.connect(
            self._on_application_monitor_changes
        )
        self._application_monitor.monitor_error.connect(
            self._on_application_monitor_error
        )
        self._application_monitor.monitor_health.connect(
            self._on_application_monitor_health
        )
        QTimer.singleShot(0, self._start_application_monitor)
        if sys.platform == "win32":
            application = QApplication.instance()
            if application is not None:
                application.installEventFilter(self)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt override name
        super().showEvent(event)
        if self._restored_layout_pending:
            QTimer.singleShot(0, self._enforce_restored_home_layout)

    def _enforce_restored_home_layout(self) -> None:
        if self._closing or not self._restored_layout_pending:
            return
        self._restored_layout_pending = False
        self.pages.setCurrentWidget(self.home_page)
        self.home_page.show_landing()
        self.home_page.layout().invalidate()
        self.home_page.layout().activate()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override name
        if self._closing:
            return False
        if (
            event.type() == QEvent.Type.ApplicationStateChange
            and QApplication.applicationState()
            == Qt.ApplicationState.ApplicationActive
        ):
            self._schedule_monitor_recovery(0)
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
                windows_window.activate_window(int(self.winId()))
        if (
            watched is self.centralWidget()
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
            and not self.isMaximized()
        ):
            root = self.centralWidget()
            position = event.position()
            border = 8
            edges = windows_window.resize_hit_test(
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
        if sys.platform == "win32" and not getattr(self, "_native_hit_test_active", False):
            self._native_hit_test_active = True
            try:
                result = windows_window.native_event_result(self, message)
                if result is not None:
                    return result
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
        apply_style(self, self._backdrop_enabled)

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
        # Stop monitoring the old directory when the folder changes.
        self._monitor.stop()
        self.settings_page.set_monitor_status(False)
        self._pending_monitor_events = None
        if self._snapshot is None:
            return
        if normalize_directory_selection(value) == normalize_directory_selection(
            self._snapshot.directory
        ):
            return
        self._snapshot = None
        self._embedding_cache.clear()
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
        snapshot = restore_cached_snapshot(
            directory_text,
            image_scanner=scan_images,
            ocr_cache_loader=cached_ocr,
        )
        if snapshot is None:
            return
        self._snapshot = snapshot
        self._restored_scan = True
        self._restored_layout_pending = True
        self.settings_page.set_folder_message(
            f"Loaded previous scan: {len(snapshot.records)} OCR records. "
            "Choose Scan / Rescan folder to refresh."
        )
        self.home_page.show_landing()
        self.home_page.show_message("Previous scan restored. Enter a query to search.")
        self._update_stats()

    def scan_directory(self, directory_text: str) -> None:
        # Cancel any background watch worker so user-initiated scans are
        # never silently dropped by a monitor update occupying the slot.
        if self._active_worker is not None:
            if not self._worker_controller.cancel_watch():
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
        task = build_scan_task(
            directory,
            index=self._index,
            existing_encoder=self._encoder,
            image_scanner=scan_images,
            ocr_cache_loader=cached_ocr,
            scan_function=scan_and_extract,
            application_directories=default_application_directories,
        )
        self._start_worker("scan", task)

    def _category_changed(self, category: str) -> None:
        self._category = category
        save_category(category)

    def submit_search(self, query: str, category: str | None = None) -> None:
        # Cancel any background watch worker so user-initiated searches are
        # never silently dropped by a monitor update occupying the slot.
        if self._active_worker is not None:
            if not self._worker_controller.cancel_watch():
                return
        if not query.strip():
            self.home_page.show_message("Enter a search query first.", error=True)
            return
        # Direct callers retain the historical screenshot-search behavior;
        # UI submissions always provide the selected category explicitly.
        selected_category = category if category is not None else "screenshots"
        self._last_category = selected_category
        if selected_category == "screenshots" and self._snapshot is None:
            self.home_page.show_message(
                "Choose and scan a screenshot folder from Folder before searching.",
                error=True,
            )
            return
        if selected_category == "screenshots" and self._snapshot and self._snapshot.setup_error:
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
        self.home_page.set_busy(True, f"Searching {selected_category}…")
        self._update_stats()

        task = build_search_task(
            clean_query,
            selected_category,
            snapshot=snapshot,
            encoder=self._encoder,
            embedding_cache=self._embedding_cache,
            index=self._index,
            encoder_loader=load_semantic_encoder,
            screenshot_search=search_snapshot,
            indexed_search=search_index,
        )
        self._start_worker("search", task)

    def _start_worker(self, kind: str, task: Callable[[threading.Event], object]) -> bool:
        if self._closing:
            return False
        return self._worker_controller.start(kind, task)

    # ------------------------------------------------------------------
    # Filesystem monitor integration
    # ------------------------------------------------------------------

    @Slot(object)
    def _on_monitor_changes(self, events: frozenset) -> None:
        """Called on the GUI thread when the debounce timer fires."""
        if self._closing:
            return
        if self._active_worker is not None:
            # A scan or search is running — queue events for later.
            self._pending_monitor_events = events
            return
        self._start_monitor_update(events)

    @Slot(str)
    def _on_monitor_error(self, message: str) -> None:
        LOGGER.warning("Filesystem monitor error: %s", message)
        self.settings_page.set_monitor_status(False)

    def _start_application_monitor(self) -> None:
        if self._closing:
            return
        self._application_monitor.start_roots(
            configured_application_directories()
        )

    @Slot(object)
    def _on_application_monitor_changes(self, events: frozenset) -> None:
        if self._closing:
            return
        directories = configured_application_directories()
        if not any(
            application_event_is_relevant(
                event.src_path,
                event.dest_path,
                event.is_directory,
                directories,
            )
            for event in events
        ):
            return
        self._application_refresh_pending = True
        self._request_application_refresh()

    @Slot(str)
    def _on_application_monitor_error(self, message: str) -> None:
        LOGGER.warning("Application monitor error: %s", message)

    @Slot(str)
    def _on_application_monitor_health(self, state: str) -> None:
        if self._closing:
            return
        if state == "active":
            was_recovering = self._application_recovery_attempt > 0
            self._application_recovery_attempt = 0
            self._application_recovery_timer.stop()
            if was_recovering:
                self._application_refresh_pending = True
                self._request_application_refresh()
        elif state in {"failed", "root_missing"}:
            self._schedule_application_monitor_recovery()

    def _schedule_application_monitor_recovery(self) -> None:
        if self._closing or not self._application_monitor.configured_roots():
            return
        delay = min(
            30_000,
            1_000 * (2 ** self._application_recovery_attempt),
        )
        self._application_recovery_attempt += 1
        if not self._application_recovery_timer.isActive():
            self._application_recovery_timer.start(delay)

    def _attempt_application_monitor_recovery(self) -> None:
        if self._closing:
            return
        if self._application_monitor.recover():
            self._application_refresh_pending = True
            self._request_application_refresh()
            if not self._application_monitor.all_roots_active():
                self._schedule_application_monitor_recovery()
        else:
            self._schedule_application_monitor_recovery()

    def _request_application_refresh(self) -> None:
        if self._closing or not self._application_refresh_pending:
            return
        if self._active_worker is not None:
            return
        self._application_refresh_pending = False
        task = build_application_refresh_task(
            configured_application_directories,
            index=self._index,
        )
        if not self._start_worker("applications", task):
            self._application_refresh_pending = True

    @Slot(str)
    def _on_monitor_health(self, state: str) -> None:
        if self._closing:
            return
        if state == "active":
            self._recovery_attempt = 0
            self._recovery_timer.stop()
            self._reconciliation_timer.start()
            self.settings_page.set_monitor_status(True)
        elif state in {"failed", "root_missing"}:
            self._reconciliation_timer.stop()
            self.settings_page.set_monitor_status(False)
            self._schedule_monitor_recovery()

    def _schedule_monitor_recovery(self, delay_ms: int | None = None) -> None:
        if self._closing or self._monitor._root is None:
            return
        if delay_ms is None:
            delay_ms = min(30_000, 1_000 * (2 ** self._recovery_attempt))
            self._recovery_attempt += 1
        if not self._recovery_timer.isActive():
            self._recovery_timer.start(delay_ms)

    def _attempt_monitor_recovery(self) -> None:
        if self._closing:
            return
        root = self._monitor._root
        if root is None:
            return
        if not root.is_dir():
            self._schedule_monitor_recovery()
            return
        self._reconcile_resume_monitor = True
        self._request_reconciliation()

    def _request_reconciliation(self) -> None:
        if self._closing:
            return
        root = self._monitor._root
        if root is None:
            return
        if self._active_worker is not None:
            self._reconciliation_pending = True
            return
        self._reconciliation_pending = False
        task = build_reconcile_task(root, index=self._index)
        if not self._start_worker("reconcile", task):
            self._reconciliation_pending = True

    def _start_deferred_work(self) -> None:
        if self._closing or self._active_worker is not None:
            return
        pending = self._pending_monitor_events
        if pending is not None:
            self._pending_monitor_events = None
            self._start_monitor_update(pending)
            return
        if self._reconciliation_pending:
            self._request_reconciliation()
            return
        self._request_application_refresh()

    def _start_monitor_update(self, events: frozenset) -> None:
        """Start a targeted incremental index worker for *events*."""
        if not events or self._closing:
            return
        root = self._monitor._root
        if root is None:
            return
        task = build_watch_task(root, index=self._index, pending_events=events)
        self._start_worker("watch", task)

    @property
    def _active_worker(self) -> BackgroundWorker | None:
        """Compatibility view of the operation controller's active worker."""
        return self._worker_controller.active_worker

    @Slot(object)
    def _worker_completed(self, outcome: WorkerOutcome) -> None:
        if self._closing:
            # A deferred shutdown was waiting for this worker to finish.
            # No worker is running now, so the second close attempt will
            # proceed through the finalization path (event.accept()).
            self.close()
            return
        if outcome.kind == "scan":
            self._scan_in_progress = False
        elif outcome.kind == "search":
            self._search_in_progress = False
        self.settings_page.set_busy(False)
        self.home_page.set_busy(False)
        if outcome.error is not None:
            if outcome.kind == "applications":
                LOGGER.warning("Application shortcut refresh failed: %s", outcome.error)
                self._application_refresh_pending = True
                delay = min(
                    30_000,
                    1_000 * (2 ** self._application_refresh_attempt),
                )
                self._application_refresh_attempt += 1
                self._application_refresh_timer.start(delay)
                return
            if outcome.kind == "reconcile":
                LOGGER.warning("Filesystem reconciliation failed: %s", outcome.error)
                self._reconcile_resume_monitor = False
                self._schedule_monitor_recovery()
                self._start_deferred_work()
                return
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
            self._start_deferred_work()
            return

        if outcome.kind == "scan":
            self._snapshot = outcome.value  # type: ignore[assignment]
            self._last_results = None
            snapshot = self._snapshot
            assert snapshot is not None
            persist_snapshot(snapshot)
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
            # Start filesystem monitoring for the newly scanned folder.
            # Deferred via singleShot(0) so active_worker is cleared before
            # the observer thread starts — keeps wait_until tests reliable.
            if not snapshot.setup_error:
                monitored_dir = snapshot.directory

                def _start_monitor(d: Path = monitored_dir) -> None:
                    started = self._monitor.start(d)
                    self.settings_page.set_monitor_status(started)
                    if started:
                        self._reconciliation_timer.start()

                QTimer.singleShot(0, _start_monitor)
        elif outcome.kind == "search":
            results, encoder = outcome.value  # type: ignore[misc]
            if encoder is not None:
                self._encoder = encoder
            if self._snapshot is not None and encoder is not None:
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
                noun = "screenshot(s)" if self._last_category == "screenshots" else "result(s)"
                self.home_page.show_message(f"Found {len(results)} {noun}.")
            else:
                noun = "screenshots" if self._last_category == "screenshots" else "results"
                self.home_page.show_message(f"No matching {noun} found.")
            self._update_stats()

        # After any scan, search, or watch worker completes, dispatch any
        # filesystem events that arrived while the worker was busy.
        if outcome.kind == "reconcile" and not self._closing:
            if self._reconcile_resume_monitor:
                root = self._monitor._root
                self._reconcile_resume_monitor = False
                if root is not None and root.is_dir():
                    started = self._monitor.start(root)
                    self.settings_page.set_monitor_status(started)
                    if started:
                        self._recovery_attempt = 0
                        self._reconciliation_timer.start()
                    else:
                        self._schedule_monitor_recovery()
            self._start_deferred_work()
        elif outcome.kind in ("scan", "search", "watch") and not self._closing:
            self._start_deferred_work()
        elif outcome.kind == "applications" and not self._closing:
            self._application_refresh_attempt = 0
            self._start_deferred_work()

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

        # Stop the filesystem monitor before cancelling the worker so no new
        # change events can arrive after shutdown begins.
        self._monitor.stop()
        self._application_monitor.stop()
        self._reconciliation_timer.stop()
        self._recovery_timer.stop()
        self._application_recovery_timer.stop()
        self._application_refresh_timer.stop()
        self._pending_monitor_events = None
        self._reconciliation_pending = False
        self._reconcile_resume_monitor = False
        self._application_refresh_pending = False
        self._worker_controller.cancel()

        # If a worker is still executing, wait up to 2 seconds for it to finish.
        # waitForDone returns True when all pool runnables have exited, False on
        # timeout.  Never close the database or exit while a worker is running.
        # Note: active_worker is cleared by a *queued* signal that fires on the
        # next event-loop turn, so it may still be non-None even after
        # waitForDone returns True.  The return value of waitForDone is the
        # authoritative indicator of whether the thread pool is idle.
        if self._active_worker is not None:
            finished = QThreadPool.globalInstance().waitForDone(2000)
            if not finished:
                # Worker did not finish in time.  Keep the window alive so the
                # user can see it is still working.  _worker_completed will call
                # close() again once the worker finishes, and that second call
                # will take the finalization path below.
                event.ignore()
                return

        # No worker is running — safe to finalize.
        if sys.platform == "win32":
            unregister_global_hotkey(int(self.winId()))
        self._index.close()
        event.accept()


def _load_application_font(application: QApplication) -> None:
    application.setFont(application.font("QMenu"))


def main(argv: list[str] | None = None) -> int:
    """Start the native desktop application without loading the model eagerly."""
    windows_window.set_app_user_model_id()
    if not windows_window.acquire_single_instance():
        return 0
    application = QApplication(argv if argv is not None else sys.argv)
    application.setApplicationName("SnapSeek")
    application.setWindowIcon(load_application_icon())
    _load_application_font(application)
    window = MainWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
