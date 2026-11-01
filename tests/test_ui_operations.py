import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from snapseek.desktop_workflow import ScanSnapshot
from snapseek.ui.operations import (
    WorkerController,
    build_search_task,
    persist_snapshot,
    restore_cached_snapshot,
)
from snapseek.ui.workers import BackgroundWorker, WorkerOutcome


class UIOperationTests(unittest.TestCase):
    def test_restore_cached_snapshot_reuses_ocr_records(self) -> None:
        directory = Path("screenshots")
        image = Mock(path=directory / "one.png")
        image.path = directory / "one.png"
        image_scanner = Mock(return_value=[image])
        cache_loader = Mock(return_value={image.path: "cached text"})

        snapshot = restore_cached_snapshot(
            str(directory),
            image_scanner=image_scanner,
            ocr_cache_loader=cache_loader,
        )

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.records[0].ocr_text, "cached text")
        cache_loader.assert_called_once()

    def test_search_task_cancels_before_touching_engines(self) -> None:
        indexed_search = Mock()
        task = build_search_task(
            "query",
            "files",
            snapshot=None,
            encoder=None,
            embedding_cache={},
            index=Mock(),
            indexed_search=indexed_search,
        )
        cancelled = threading.Event()
        cancelled.set()

        with self.assertRaises(RuntimeError):
            task(cancelled)
        indexed_search.assert_not_called()

    def test_empty_screenshot_search_does_not_load_model(self) -> None:
        encoder_loader = Mock(side_effect=AssertionError("model should not load"))
        screenshot_search = Mock(side_effect=AssertionError("search should not run"))
        task = build_search_task(
            "query",
            "screenshots",
            snapshot=ScanSnapshot(Path("screenshots"), (), (), ()),
            encoder=None,
            embedding_cache={},
            index=Mock(),
            encoder_loader=encoder_loader,
            screenshot_search=screenshot_search,
        )

        self.assertEqual(task(threading.Event()), ([], None))
        encoder_loader.assert_not_called()
        screenshot_search.assert_not_called()

    def test_persist_snapshot_delegates_successful_records(self) -> None:
        snapshot = ScanSnapshot(Path("screenshots"), (), (), ())
        with patch("snapseek.ui.operations.save_state") as save:
            persist_snapshot(snapshot)
        save.assert_called_once()

    def test_worker_controller_rejects_duplicate_and_clears_after_completion(self) -> None:
        controller = WorkerController()
        controller.active_worker = Mock()
        self.assertFalse(controller.start("duplicate", lambda _event: None))

    def test_worker_controller_ignores_stale_completion(self) -> None:
        controller = WorkerController()
        old_worker = BackgroundWorker("watch", lambda _event: None)
        old_worker.completion_token = object()
        controller.active_worker = old_worker
        self.assertTrue(controller.cancel_watch())

        current_worker = BackgroundWorker("search", lambda _event: None)
        current_worker.completion_token = object()
        controller.active_worker = current_worker
        completions: list[WorkerOutcome] = []
        controller.completed.connect(completions.append)

        controller._completed(
            WorkerOutcome("watch", completion_token=old_worker.completion_token)
        )

        self.assertIs(controller.active_worker, current_worker)
        self.assertEqual(completions, [])

    def test_worker_controller_clears_before_publishing_completion(self) -> None:
        controller = WorkerController()
        worker = BackgroundWorker("scan", lambda _event: None)
        worker.completion_token = object()
        controller.active_worker = worker
        observed_active_workers: list[object | None] = []
        controller.completed.connect(
            lambda _outcome: observed_active_workers.append(controller.active_worker)
        )

        controller._completed(
            WorkerOutcome("scan", value="done", completion_token=worker.completion_token)
        )

        self.assertEqual(observed_active_workers, [None])
