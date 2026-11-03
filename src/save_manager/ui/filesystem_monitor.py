"""Notificações nativas com debounce e varredura de reserva fora da UI."""

import logging
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QThreadPool, QTimer, Signal, Slot

from .workers import ServiceWorker


class FilesystemMonitor(QObject):
    synchronized = Signal(object)
    idle = Signal()

    # Initialize the object.
    def __init__(self, service, busy, parent=None):
        super().__init__(parent)
        self.service, self.busy = service, busy
        self.running = False
        self.pending = False
        self.stopped = False
        self.worker = None
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(self.request)
        self.watcher.fileChanged.connect(self.request)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(150)
        self.debounce.timeout.connect(self._scan)
        self.fallback = QTimer(self)
        self.fallback.setInterval(1500)
        self.fallback.timeout.connect(self.request)
        self.fallback.start()
        self.request()

    # Request synchronization.
    def request(self, *_args):
        if self.stopped:
            return
        self.pending = True
        if not self.debounce.isActive():
            self.debounce.start()

    # Scan.
    def _scan(self):
        if self.stopped or self.running or self.busy():
            return
        self.pending = False
        self.running = True
        self.worker = ServiceWorker(self.service.scan, with_progress=False)
        self.worker.signals.finished.connect(self._finished)
        self.worker.signals.error.connect(self._error)
        QThreadPool.globalInstance().start(self.worker)

    @Slot(object)
    # Handle.
    def _finished(self, result):
        if result is not None and not self.stopped:
            watching = set(self.watcher.directories() + self.watcher.files())
            removed = watching - result.watch_paths
            added = result.watch_paths - watching
            if removed:
                self.watcher.removePaths(sorted(removed))
            if added:
                self.watcher.addPaths(sorted(added))
            self.synchronized.emit(result)
        self.running = False
        self.worker = None
        self.idle.emit()
        if self.pending:
            self.request()

    @Slot(str)
    # Handle.
    def _error(self, message):
        logging.getLogger("save_manager").warning("Sincronização externa adiada: %s", message)
        self._finished(None)

    # Remove watched paths below root.
    def remove_paths_under(self, root: str | Path) -> None:
        root_path = Path(root).resolve(strict=False)
        watched = self.watcher.directories() + self.watcher.files()
        removed: list[str] = []
        for raw_path in watched:
            try:
                Path(raw_path).resolve(strict=False).relative_to(root_path)
            except (OSError, ValueError):
                continue
            removed.append(raw_path)
        if removed:
            self.watcher.removePaths(removed)

    # Stop synchronization.
    def stop(self):
        self.stopped = True
        self.debounce.stop()
        self.fallback.stop()
        paths = self.watcher.directories() + self.watcher.files()
        if paths:
            self.watcher.removePaths(paths)
