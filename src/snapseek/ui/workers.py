"""Qt worker infrastructure used by the desktop UI."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import threading
import traceback
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from snapseek.desktop_workflow import (
    DirectorySelectionError,
    OperationCancelled,
)
from snapseek.semantic import SemanticSearchError


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerOutcome:
    """Value or exception returned by a background operation."""

    kind: str
    value: object | None = None
    error: Exception | None = None
    traceback_text: str | None = None
    completion_token: object | None = field(
        default=None, compare=False, repr=False
    )


class WorkerSignals(QObject):
    """Signals emitted by a QRunnable; slots update widgets on the GUI thread."""

    completed = Signal(object)


class BackgroundWorker(QRunnable):
    """Run a widget-independent callable on Qt's worker thread pool."""

    def __init__(
        self,
        kind: str,
        task: Callable[[threading.Event], object],
        *,
        completion_token: object | None = None,
    ) -> None:
        super().__init__()
        self.kind = kind
        self.task = task
        self.completion_token = completion_token
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
                WorkerOutcome(
                    self.kind,
                    error=error,
                    traceback_text=detail,
                    completion_token=self.completion_token,
                )
            )
        else:
            self.signals.completed.emit(
                WorkerOutcome(
                    self.kind,
                    value=value,
                    completion_token=self.completion_token,
                )
            )
