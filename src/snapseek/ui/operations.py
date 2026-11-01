"""Desktop operation orchestration independent of page widgets."""

from __future__ import annotations

import logging
from pathlib import Path
import threading
import time
from typing import Callable

from PySide6.QtCore import QObject, QThreadPool, Signal

from snapseek.desktop_storage import cached_ocr, save_state
from snapseek.desktop_workflow import (
    OperationCancelled,
    ScanSnapshot,
    scan_and_extract,
    search_snapshot,
)
from snapseek.scanner import scan_images
from snapseek.search import OCRTextRecord
from snapseek.semantic import load_semantic_encoder
from snapseek.ui.workers import BackgroundWorker, WorkerOutcome
from snapseek.unified_index import FileIntegrityDeferred, LocalIndex
from snapseek.unified_search import (
    default_application_directories,
    recursive_application_directories,
    search_index,
    search_pdfs,
)

LOGGER = logging.getLogger(__name__)


Task = Callable[[threading.Event], object]


def restore_cached_snapshot(
    directory_text: str,
    *,
    image_scanner: Callable[[Path], list] = scan_images,
    ocr_cache_loader: Callable = cached_ocr,
) -> ScanSnapshot | None:
    """Restore a complete cached screenshot snapshot without starting a worker."""
    if not directory_text:
        return None
    directory = Path(directory_text).expanduser()
    try:
        images = tuple(image_scanner(directory))
    except OSError:
        return None
    cached = ocr_cache_loader(directory, images)
    if not cached and images:
        return None
    records = tuple(
        OCRTextRecord(image=image, ocr_text=cached[image.path])
        for image in images
        if image.path in cached
    )
    return ScanSnapshot(
        directory=directory,
        images=images,
        records=records,
        image_failures=(),
        encoder=None,
    )


def build_scan_task(
    directory: Path,
    *,
    index: LocalIndex,
    existing_encoder,
    image_scanner: Callable[[Path], list] = scan_images,
    ocr_cache_loader: Callable = cached_ocr,
    scan_function: Callable = scan_and_extract,
    application_directories: Callable = default_application_directories,
    recursive_application_directories: Callable = recursive_application_directories,
) -> Task:
    """Build the cancellable index, application-discovery, and OCR task."""

    def task(cancel_event: threading.Event) -> object:
        if not directory.is_dir():
            return scan_function(
                directory,
                encoder=existing_encoder,
                cancelled=cancel_event.is_set,
                cached_ocr={},
            )
        index.index_folder(directory, cancelled=cancel_event.is_set)
        directories = application_directories()
        index.index_applications(
            directories,
            recursive_directories=recursive_application_directories(directories),
        )
        images = tuple(image_scanner(directory))
        return scan_function(
            directory,
            encoder=existing_encoder,
            cancelled=cancel_event.is_set,
            image_scanner=lambda _directory: list(images),
            cached_ocr=ocr_cache_loader(directory, images),
        )

    return task


def build_search_task(
    query: str,
    category: str,
    *,
    snapshot: ScanSnapshot | None,
    encoder,
    embedding_cache: dict[str, tuple[float, ...]],
    index: LocalIndex,
    encoder_loader: Callable = load_semantic_encoder,
    screenshot_search: Callable = search_snapshot,
    indexed_search: Callable = search_index,
    pdf_search: Callable = search_pdfs,
) -> Task:
    """Build a cancellable category-aware search task."""

    def task(cancel_event: threading.Event) -> object:
        if cancel_event.is_set():
            raise OperationCancelled("The search was cancelled.")
        active_encoder = encoder
        if category == "screenshots":
            assert snapshot is not None
            active_encoder = snapshot.encoder or active_encoder
            if not snapshot.records:
                return [], active_encoder
            if active_encoder is None:
                active_encoder = encoder_loader()
            results = screenshot_search(
                query,
                snapshot,
                encoder=active_encoder,
                embedding_cache=embedding_cache,
            )
        elif category == "all":
            screenshot_results: list[object] = []
            if snapshot is not None and snapshot.records:
                if active_encoder is None:
                    active_encoder = encoder_loader()
                screenshot_results = screenshot_search(
                    query,
                    snapshot,
                    encoder=active_encoder,
                    embedding_cache=embedding_cache,
                )
            file_results = indexed_search(index, query, "files", 15)
            pdf_results = pdf_search(index, query, 15)
            pdf_paths = {str(result.path).casefold() for result in pdf_results}
            file_results = [
                result for result in file_results
                if str(result.item.path).casefold() not in pdf_paths
            ]
            results = (
                screenshot_results
                + file_results
                + indexed_search(index, query, "apps", 15)
                + pdf_results
            )
        elif category == "documents":
            active_encoder = None
            results = pdf_search(index, query, 30)
        else:
            active_encoder = None
            results = indexed_search(index, query, category)
        if cancel_event.is_set():
            raise OperationCancelled("The search was cancelled.")
        return results, active_encoder

    return task


def persist_snapshot(snapshot: ScanSnapshot) -> None:
    """Persist successful OCR metadata after a completed scan."""
    save_state(
        snapshot.directory,
        snapshot.images,
        records=tuple((record.image, record.ocr_text) for record in snapshot.records),
    )


def _apply_change_event(index: LocalIndex, event: object) -> None:
    """Apply a single filesystem ChangeEvent to the index.

    Imported lazily to avoid a circular import at module load time.
    """
    from snapseek.filesystem_monitor import ChangeEvent  # noqa: PLC0415

    if not isinstance(event, ChangeEvent):
        return
    kind = event.kind
    src = Path(event.src_path)

    if kind == "deleted":
        if event.is_directory:
            index.remove_items_under(src)
        else:
            index.remove_item(src)
    elif kind == "created":
        if event.is_directory:
            # Recursively index new directories; skip if it vanished already.
            if src.is_dir():
                try:
                    index.index_folder(src)
                except (FileNotFoundError, OSError) as error:
                    LOGGER.warning("Cannot index created directory %s: %s", src, error)
        else:
            # stability_check=False: the filesystem event itself is sufficient
            # evidence that the file exists and is ready.  A second stat read
            # adds latency that can cause watch workers to outlast test timeouts.
            index.index_single_file(src, stability_check=False)
    elif kind == "modified":
        # Only files emit "modified"; directories are ignored.
        if not event.is_directory:
            index.index_single_file(src, stability_check=False)
    elif kind == "moved":
        dest = Path(event.dest_path) if event.dest_path else None
        if event.is_directory:
            index.remove_items_under(src)
            if dest and dest.is_dir():
                try:
                    index.index_folder(dest)
                except (FileNotFoundError, OSError) as error:
                    LOGGER.warning("Cannot index moved directory %s: %s", dest, error)
        else:
            index.remove_item(src)
            if dest:
                index.index_single_file(dest, stability_check=False)


def build_watch_task(
    root: Path,
    *,
    index: LocalIndex,
    pending_events: frozenset,
) -> Task:
    """Build a targeted incremental index update task for filesystem events.

    Processes only the changed paths described by *pending_events*.
    Does NOT perform a full-root rescan.  Each event is applied
    independently; errors for one path are logged and do not stop
    processing of subsequent events.
    """

    def task(cancel_event: threading.Event) -> object:
        if cancel_event.is_set():
            return None
        processed = 0
        for event in pending_events:
            if cancel_event.is_set():
                break
            try:
                _apply_change_event(index, event)
                processed += 1
            except Exception as error:  # noqa: BLE001
                LOGGER.warning(
                    "Error applying filesystem event %r: %s", event, error
                )
        return processed

    return task


def build_reconcile_task(
    root: Path,
    *,
    index: LocalIndex,
) -> Task:
    """Build a cancellable full-root reconciliation task."""

    def task(cancel_event: threading.Event) -> object:
        if cancel_event.is_set():
            return None
        return index.index_folder(root, cancelled=cancel_event.is_set)

    return task


def build_application_refresh_task(
    directories: Callable[[], tuple[Path, ...]],
    *,
    index: LocalIndex,
    recursive_directories: Callable[
        [tuple[Path, ...]], tuple[Path, ...]
    ] = recursive_application_directories,
) -> Task:
    """Build a cancellable application shortcut refresh task."""

    def task(cancel_event: threading.Event) -> object:
        if cancel_event.is_set():
            return None
        roots = directories()
        return index.index_applications(
            roots,
            recursive_directories=recursive_directories(roots),
            cancelled=cancel_event.is_set,
        )

    return task


class WorkerController(QObject):
    """Own one active worker and its deferred completion lifecycle."""

    completed = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.active_worker: BackgroundWorker | None = None

    def start(self, kind: str, task: Task) -> bool:
        if self.active_worker is not None:
            return False
        completion_token = object()
        worker = BackgroundWorker(
            kind,
            task,
            completion_token=completion_token,
        )
        worker.signals.completed.connect(self._completed)
        self.active_worker = worker
        QThreadPool.globalInstance().start(worker)
        return True

    def cancel(self) -> None:
        if self.active_worker is not None:
            self.active_worker.cancel()

    def cancel_watch(self) -> bool:
        """Cancel the active worker only if it is a watch worker.

        Clears ``active_worker`` immediately so the slot is available for a
        user-initiated scan or search without waiting for the queued
        ``completed`` signal.  Returns True if a watch worker was cancelled.
        """
        if self.active_worker is not None and self.active_worker.kind == "watch":
            self.active_worker.cancel()
            self.active_worker = None
            return True
        return False

    def _completed(self, outcome: WorkerOutcome) -> None:
        worker = self.active_worker
        if worker is None or outcome.completion_token is not worker.completion_token:
            LOGGER.debug("Ignoring stale completion for %s worker.", outcome.kind)
            return
        self.active_worker = None
        self.completed.emit(outcome)


def build_watch_task(
    root: Path,
    *,
    index: LocalIndex,
    pending_events: frozenset,
    retry_delays: tuple[float, ...] = (0.1, 0.2, 0.4),
    wait: Callable[[float], None] = time.sleep,
) -> Task:
    """Build a targeted incremental index update task for filesystem changes.

    Processes only the affected paths from filesystem events.
    Does NOT perform a full rescan.
    """

    def task(cancel_event: threading.Event) -> object:
        if cancel_event.is_set():
            return None
        processed = 0
        for event in pending_events:
            if cancel_event.is_set():
                break
            try:
                if _process_change_event(
                    index,
                    event,
                    root,
                    cancel_event=cancel_event,
                    retry_delays=retry_delays,
                    wait=wait,
                ):
                    processed += 1
            except Exception as error:  # noqa: BLE001
                LOGGER.warning(
                    "Error processing filesystem event %s: %s", event.kind, error
                )
        return processed

    return task


def _process_change_event(
    index: LocalIndex,
    event: object,
    root: Path,
    *,
    cancel_event: threading.Event | None = None,
    retry_delays: tuple[float, ...] = (0.1, 0.2, 0.4),
    wait: Callable[[float], None] = time.sleep,
) -> bool:
    """Apply a single filesystem change to the index."""
    from snapseek.filesystem_monitor import ChangeEvent  # avoid circular import at module level
    assert isinstance(event, ChangeEvent)

    kind = event.kind
    src = Path(event.src_path)

    if kind == "deleted":
        if event.is_directory:
            index.remove_items_under(src)
        else:
            index.remove_item(src)
        return True
    elif kind == "created":
        if event.is_directory:
            if src.is_dir():
                index.index_folder(src)
                return True
            return False
        else:
            return _index_with_retry(index, src, cancel_event, retry_delays, wait)
    elif kind == "modified":
        if not event.is_directory:
            return _index_with_retry(index, src, cancel_event, retry_delays, wait)
        return False
    elif kind == "moved":
        dest = Path(event.dest_path) if event.dest_path else None
        if event.is_directory:
            index.remove_items_under(src)
            if dest and dest.is_dir():
                index.index_folder(dest)
                return True
            return False
        else:
            if dest:
                indexed = _index_with_retry(index, dest, cancel_event, retry_delays, wait)
                if indexed:
                    index.remove_item(src)
                return indexed
            return False
    return False


def _index_with_retry(
    index: LocalIndex,
    path: Path,
    cancel_event: threading.Event | None,
    retry_delays: tuple[float, ...],
    wait: Callable[[float], None],
) -> bool:
    """Index a file, retrying bounded transient integrity failures."""
    attempts = len(retry_delays) + 1
    for attempt in range(attempts):
        if cancel_event is not None and cancel_event.is_set():
            return False
        try:
            if path.suffix.casefold() == ".pdf":
                outcome = index.index_pdf(
                    path,
                    cancelled=cancel_event.is_set if cancel_event is not None else None,
                    force_integrity_check=True,
                )
                if outcome.action == "deferred":
                    if attempt == attempts - 1:
                        LOGGER.warning(
                            "Deferred indexing for %s: %s", path, outcome.error
                        )
                        return False
                    delay = retry_delays[attempt]
                    if cancel_event is not None:
                        if cancel_event.wait(delay):
                            return False
                    else:
                        wait(delay)
                    continue
                return outcome.action in {"indexed", "skipped", "status-only"}
            return index.index_single_file(
                path,
                stability_check=True,
                raise_on_deferred=True,
                wait=wait,
            )
        except FileIntegrityDeferred as error:
            if attempt == attempts - 1:
                LOGGER.warning("Deferred indexing for %s: %s", path, error)
                return False
            delay = retry_delays[attempt]
            if cancel_event is not None:
                if cancel_event.wait(delay):
                    return False
            else:
                wait(delay)
    return False
