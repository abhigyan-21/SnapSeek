"""Filesystem monitoring for automatic incremental index updates.

``FolderMonitor`` watches a directory tree and emits a ``changes_ready``
signal carrying a ``frozenset[ChangeEvent]`` after a quiet period following
the last filesystem event.  All public methods must be called from the GUI
thread.  The watchdog observer runs on its own daemon thread; events are
marshalled to the GUI thread through a ``queue.SimpleQueue`` polled by a
``QTimer``.

Thread safety
-------------
- ``_event_queue`` is a ``queue.SimpleQueue`` which is safe for concurrent
  put/get from different threads.
- The debounce timer and ``_pending`` list are only touched on the GUI thread
  (inside ``_drain_queue`` and ``_flush``), so they need no additional lock.
- ``stop()`` drains the queue inside ``_drain_queue``'s timer stop, then
  clears ``_pending``.  Both happen on the GUI thread.
"""

from __future__ import annotations

import logging
import queue
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot

from snapseek.unified_index import DEFAULT_EXCLUDED_DIRECTORIES

LOGGER = logging.getLogger(__name__)

# Milliseconds of filesystem quiet before emitting a batch.
DEBOUNCE_MS = 250
# How often to drain the cross-thread event queue (milliseconds).
_POLL_MS = 50

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    _WATCHDOG_AVAILABLE = True
except ImportError:  # pragma: no cover
    _WATCHDOG_AVAILABLE = False
    Observer = None  # type: ignore[assignment,misc]
    FileSystemEventHandler = object  # type: ignore[assignment,misc]


class ChangeEvent:
    """A normalised filesystem change produced by the watcher.

    Attributes
    ----------
    kind:
        One of ``"created"``, ``"modified"``, ``"deleted"``, ``"moved"``.
    src_path:
        The affected path (source path for a move).
    dest_path:
        The destination path for a ``"moved"`` event; ``None`` otherwise.
    is_directory:
        ``True`` when the event concerns a directory rather than a file.
    """

    __slots__ = ("kind", "src_path", "dest_path", "is_directory")

    def __init__(
        self,
        kind: str,
        src_path: str,
        dest_path: str | None = None,
        *,
        is_directory: bool = False,
    ) -> None:
        self.kind = kind
        self.src_path = src_path
        self.dest_path = dest_path
        self.is_directory = is_directory

    def __repr__(self) -> str:
        dest = f", dest={self.dest_path!r}" if self.dest_path else ""
        d = " [dir]" if self.is_directory else ""
        return f"ChangeEvent({self.kind!r}, {self.src_path!r}{dest}{d})"

    def __hash__(self) -> int:
        return hash((self.kind, self.src_path, self.dest_path, self.is_directory))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ChangeEvent):
            return NotImplemented
        return (
            self.kind == other.kind
            and self.src_path == other.src_path
            and self.dest_path == other.dest_path
            and self.is_directory == other.is_directory
        )


class _SnapSeekEventHandler(FileSystemEventHandler):
    """Watchdog event handler that runs on the Observer's daemon thread.

    Never touches Qt objects.  Puts ``ChangeEvent`` instances into the
    ``FolderMonitor``'s cross-thread queue; the GUI thread drains the queue
    via a ``QTimer``.
    """

    def __init__(self, event_queue: "queue.SimpleQueue[ChangeEvent]") -> None:
        super().__init__()
        self._queue = event_queue

    def _put(
        self,
        kind: str,
        src: str,
        dest: str | None = None,
        *,
        is_dir: bool = False,
    ) -> None:
        self._queue.put(ChangeEvent(kind, src, dest, is_directory=is_dir))

    def on_created(self, event: object) -> None:  # type: ignore[override]
        self._put("created", event.src_path, is_dir=event.is_directory)  # type: ignore[attr-defined]

    def on_modified(self, event: object) -> None:  # type: ignore[override]
        if not event.is_directory:  # type: ignore[attr-defined]
            self._put("modified", event.src_path)

    def on_deleted(self, event: object) -> None:  # type: ignore[override]
        self._put("deleted", event.src_path, is_dir=event.is_directory)  # type: ignore[attr-defined]

    def on_moved(self, event: object) -> None:  # type: ignore[override]
        self._put(
            "moved",
            event.src_path,  # type: ignore[attr-defined]
            event.dest_path,  # type: ignore[attr-defined]
            is_dir=event.is_directory,  # type: ignore[attr-defined]
        )


class FolderMonitor(QObject):
    """Watch a directory tree and emit batched filesystem change events.

    Signals
    -------
    changes_ready(frozenset[ChangeEvent]):
        Emitted on the GUI thread after a ``DEBOUNCE_MS`` quiet period.
        The payload is a ``frozenset`` so callers can safely snapshot it.
    monitor_error(str):
        Emitted when the observer cannot be started or raises unexpectedly.
    """

    changes_ready = Signal(object)  # frozenset[ChangeEvent]
    monitor_error = Signal(str)
    monitor_health = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._observer: object | None = None
        self._root: Path | None = None
        self._roots: tuple[Path, ...] = ()
        self._active_roots: set[Path] = set()
        self._handler: _SnapSeekEventHandler | None = None
        self._health = "stopped"
        # Cross-thread queue: watchdog thread puts, GUI thread drains.
        self._event_queue: queue.SimpleQueue[ChangeEvent] = queue.SimpleQueue()
        # All fields below are GUI-thread-only.
        self._pending: list[ChangeEvent] = []
        self._excluded = {name.casefold() for name in DEFAULT_EXCLUDED_DIRECTORIES}

        # Poll timer: drains the queue every _POLL_MS on the GUI thread.
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_POLL_MS)
        self._poll_timer.timeout.connect(self._drain_queue)

        # Debounce timer: fires after DEBOUNCE_MS of silence, emits the batch.
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._flush)
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(1000)
        self._health_timer.timeout.connect(self._check_health)

    # ------------------------------------------------------------------
    # Public API (GUI thread only)
    # ------------------------------------------------------------------

    def start(self, path: Path) -> bool:
        """Start monitoring *path* recursively.

        Stops any existing observer first.  Returns ``True`` on success.
        """
        return self.start_roots((path,))

    def start_roots(self, paths: tuple[Path, ...] | list[Path]) -> bool:
        """Start one observer for all available roots in *paths*."""
        self.stop()
        if not _WATCHDOG_AVAILABLE:
            LOGGER.warning("watchdog is not installed; filesystem monitoring unavailable.")
            return False
        roots = tuple(path.expanduser().resolve(strict=False) for path in paths)
        if not roots:
            return False
        self._roots = roots
        self._root = roots[0]
        available = {root for root in roots if root.is_dir()}
        if not available:
            self._health = "root_missing"
            self.monitor_health.emit(self._health)
            return False
        try:
            observer = Observer()
            handler = _SnapSeekEventHandler(self._event_queue)
            for root in available:
                observer.schedule(handler, str(root), recursive=True)
            observer.start()
        except OSError as error:
            LOGGER.warning("Failed to start filesystem monitor: %s", error)
            self.monitor_error.emit(str(error))
            return False
        self._observer = observer
        self._handler = handler
        self._active_roots = available
        self._health = "active"
        self._poll_timer.start()
        self._health_timer.start()
        self.monitor_health.emit("active")
        LOGGER.info("Filesystem monitoring started for: %s", root)
        return True

    def stop(self) -> None:
        """Stop the observer and discard any pending events."""
        self._stop_observer(clear_root=True)

    def _stop_observer(self, *, clear_root: bool) -> None:
        self._poll_timer.stop()
        self._health_timer.stop()
        self._debounce.stop()
        if self._observer is not None:
            try:
                self._observer.stop()  # type: ignore[attr-defined]
                self._observer.join(timeout=1.5)  # type: ignore[attr-defined]
            except Exception as error:  # noqa: BLE001
                LOGGER.warning("Error stopping filesystem monitor: %s", error)
            self._observer = None
        self._handler = None
        self._active_roots.clear()
        if clear_root:
            self._root = None
            self._roots = ()
            self._health = "stopped"
        # Drain and discard any leftover items in the cross-thread queue.
        while not self._event_queue.empty():
            try:
                self._event_queue.get_nowait()
            except Exception:  # noqa: BLE001
                break
        self._pending.clear()

    def configured_root(self) -> Path | None:
        """Return the selected root, including while monitoring is paused."""
        return self._root

    def configured_roots(self) -> tuple[Path, ...]:
        """Return all configured roots, including unavailable roots."""
        return self._roots

    def recover(self) -> bool:
        """Add returned roots or restart a lost observer without duplication."""
        if not self._roots:
            return False
        if self._observer is None or not bool(
            getattr(self._observer, "is_alive", lambda: False)()
        ):
            return self.start_roots(self._roots)
        assert self._handler is not None
        added = False
        for root in self._roots:
            if root.is_dir() and root not in self._active_roots:
                self._observer.schedule(self._handler, str(root), recursive=True)
                self._active_roots.add(root)
                added = True
        if added:
            self._health = "active"
            self.monitor_health.emit("active")
        return bool(self._active_roots)

    def all_roots_active(self) -> bool:
        """Return whether every configured root currently has a schedule."""
        return bool(self._roots) and set(self._roots).issubset(self._active_roots)

    def is_active(self) -> bool:
        """Return ``True`` while an observer is running."""
        return self._observer is not None and bool(
            getattr(self._observer, "is_alive", lambda: False)()
        )

    @Slot()
    def _check_health(self) -> None:
        """Detect observer loss or a temporarily unavailable root."""
        roots = self._roots or ((self._root,) if self._root is not None else ())
        if self._observer is None or not roots:
            return
        if not self._active_roots:
            self._active_roots.update(roots)
        missing = {root for root in self._active_roots if not root.is_dir()}
        self._active_roots.difference_update(missing)
        returned = {
            root for root in roots if root.is_dir() and root not in self._active_roots
        }
        for root in returned:
            if self._handler is not None:
                self._observer.schedule(self._handler, str(root), recursive=True)
                self._active_roots.add(root)
        if missing and self._health != "root_missing":
            self._health = "root_missing"
            self.monitor_health.emit(self._health)
            self.monitor_error.emit("One or more monitored folders are unavailable.")
        if returned and self._active_roots and self._health != "active":
            self._health = "active"
            self.monitor_health.emit(self._health)
        if not bool(getattr(self._observer, "is_alive", lambda: False)()):
            self._stop_observer(clear_root=False)
            self._health = "failed"
            self.monitor_health.emit(self._health)
            self.monitor_error.emit("Filesystem monitor stopped unexpectedly.")

    # ------------------------------------------------------------------
    # Internal slots (GUI thread only)
    # ------------------------------------------------------------------

    @Slot()
    def _drain_queue(self) -> None:
        """Transfer items from the cross-thread queue to ``_pending``."""
        changed = False
        while True:
            try:
                event = self._event_queue.get_nowait()
            except queue.Empty:
                break
            if self._is_excluded(event.src_path):
                continue
            if event.dest_path and self._is_excluded(event.dest_path):
                continue
            self._pending.append(event)
            changed = True
        if changed:
            # Restart the debounce timer so we wait for DEBOUNCE_MS of quiet.
            self._debounce.start(DEBOUNCE_MS)

    @Slot()
    def _flush(self) -> None:
        """Emit the accumulated batch and reset pending state."""
        batch = list(self._pending)
        self._pending.clear()
        if batch:
            self.changes_ready.emit(frozenset(batch))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_excluded(self, path_str: str) -> bool:
        """Return ``True`` if any component of *path_str* is excluded."""
        try:
            parts = Path(path_str).parts
        except Exception:  # noqa: BLE001
            return False
        return any(part.casefold() in self._excluded for part in parts)
