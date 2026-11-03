"""Workers Qt pequenos; os serviços permanecem independentes de Qt."""

from __future__ import annotations

import threading
import time

try:
    from PySide6.QtCore import QObject, QRunnable, Signal, Slot
except ImportError:  # pragma: no cover - somente permite import em ambiente sem Qt
    QObject = QRunnable = object  # type: ignore[misc,assignment]

    # Handle Slot.
    def Slot(*_args, **_kwargs):
        return lambda func: func

    class Signal:  # type: ignore[no-redef]
        # Initialize the object.
        def __init__(self, *_args, **_kwargs): pass


class WorkerSignals(QObject):
    finished = Signal(object)
    error = Signal(str)
    progress = Signal(object)


class ServiceWorker(QRunnable):
    # Initialize the object.
    def __init__(self, function, *args, cancellable: bool = False, with_progress: bool = True, **kwargs):
        super().__init__()
        self.function, self.args, self.kwargs = function, args, kwargs
        self.signals = WorkerSignals()
        self.cancellable = cancellable
        self.with_progress = with_progress
        self.cancel_event = threading.Event()

    # Cancel.
    def cancel(self) -> None:
        if self.cancellable:
            self.cancel_event.set()

    @Slot()
    # Run the worker.
    def run(self):
        try:
            kwargs = dict(self.kwargs)
            last_progress = 0.0

            # Report progress.
            def report_progress(item):
                nonlocal last_progress
                now = time.monotonic()
                if last_progress == 0.0 or now - last_progress >= 0.1:
                    last_progress = now
                    self.signals.progress.emit(item)

            if self.cancellable:
                kwargs["cancel"] = self.cancel_event.is_set
            if self.with_progress:
                kwargs["progress"] = report_progress
            result = self.function(*self.args, **kwargs)
        except Exception as exc:  # a UI boundary must turn errors into a signal
            self.signals.error.emit(str(exc))
        else:
            self.signals.finished.emit(result)
