import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtWidgets import QApplication

from snapseek.scanner import ImageMetadata
from snapseek.search import OCRTextRecord
from snapseek.semantic import SemanticSearchResult
from snapseek.desktop_workflow import OperationCancelled
from snapseek.ui.dialogs import ImagePreviewDialog
from snapseek.ui.result_cards import ImageResultCard
from snapseek.ui.result_grid import ResultsGrid
from snapseek.ui.workers import BackgroundWorker, WorkerOutcome


APPLICATION = QApplication.instance() or QApplication([])


class UIComponentTests(unittest.TestCase):
    def test_worker_emits_value_and_preserves_kind(self) -> None:
        outcomes: list[WorkerOutcome] = []
        worker = BackgroundWorker("scan", lambda _cancel: ["ok"])
        worker.signals.completed.connect(outcomes.append)

        worker.run()

        self.assertEqual(outcomes, [WorkerOutcome("scan", value=["ok"])])

    def test_worker_emits_cancellation_error(self) -> None:
        outcomes: list[WorkerOutcome] = []

        def task(cancel):
            if cancel.is_set():
                raise OperationCancelled("cancelled")
            return "done"

        worker = BackgroundWorker("search", task)
        worker.signals.completed.connect(outcomes.append)
        worker.cancel()

        worker.run()

        self.assertIsInstance(outcomes[0].error, Exception)
        self.assertTrue(outcomes[0].traceback_text)

    def test_worker_emits_unexpected_errors_with_traceback(self) -> None:
        outcomes: list[WorkerOutcome] = []
        worker = BackgroundWorker("broken", lambda _cancel: 1 / 0)
        worker.signals.completed.connect(outcomes.append)

        worker.run()

        self.assertIsInstance(outcomes[0].error, ZeroDivisionError)
        self.assertIn("ZeroDivisionError", outcomes[0].traceback_text or "")

    def test_result_card_and_grid_render_results(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "sample.png"
            path.write_bytes(b"not-a-real-image")
            metadata = ImageMetadata(path.name, path, ".png", path.stat().st_size)
            result = SemanticSearchResult(OCRTextRecord(metadata, "hello"), 0.8)
            card = ImageResultCard(result)
            grid = ResultsGrid()

            grid.set_results([result])
            QApplication.processEvents()

            self.assertEqual(card.result, result)
            self.assertEqual(len(grid._cards), 1)
            grid.deleteLater()
            card.deleteLater()

    def test_preview_dialog_handles_missing_image(self) -> None:
        dialog = ImagePreviewDialog(Path("missing-image.png"))
        self.assertIn("no longer available", dialog.image_label.text())
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
