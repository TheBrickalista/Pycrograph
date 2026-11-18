# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""Background SHA-256 hashing (QThreadPool). One file at a time by default:
the raw data usually sit on a single external drive, where parallel reads
only thrash."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from ..storage import HashCancelled, sha256_file


class HashSignals(QObject):
    progress = Signal(str, object, object)                    # image_id, done, total
    finished = Signal(str, str, str, object, object)          # image_id, path, sha256, size, mtime_ns
    failed = Signal(str, str)                           # image_id, message


class HashTask(QRunnable):
    def __init__(self, image_id: str, path: str | Path, cancel_event: threading.Event):
        super().__init__()
        self.image_id = image_id
        self.path = str(path)
        self.signals = HashSignals()
        self._cancel = cancel_event
        self.setAutoDelete(True)

    def run(self) -> None:  # runs in a worker thread
        try:
            st = os.stat(self.path)
            last = [-1]

            def progress(done: int, total: int) -> None:
                pct = int(100 * done / total) if total else 100
                if pct != last[0]:
                    last[0] = pct
                    self.signals.progress.emit(self.image_id, done, total)

            digest = sha256_file(self.path, progress=progress, cancelled=self._cancel.is_set)
            st2 = os.stat(self.path)
            if (st2.st_size, st2.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
                self.signals.failed.emit(self.image_id, "file changed while it was being hashed")
                return
            self.signals.finished.emit(self.image_id, self.path, digest, st.st_size, st.st_mtime_ns)
        except HashCancelled:
            pass
        except OSError as exc:
            self.signals.failed.emit(self.image_id, str(exc))


class HashQueue(QObject):
    """Owns the pool. Task signals are connected to bound slots of this
    QObject (main thread), so the re-emitted signals below always arrive in
    the GUI thread. cancel_all() on project switch / app exit."""

    progress = Signal(str, object, object)
    finished = Signal(str, str, str, object, object)
    failed = Signal(str, str)

    def __init__(self, parent: QObject | None = None, max_threads: int = 1):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max_threads)
        self._cancel = threading.Event()
        self.pending: set[str] = set()

    def submit(self, image_id: str, path: str | Path) -> None:
        if image_id in self.pending:
            return
        task = HashTask(image_id, path, self._cancel)
        task.signals.progress.connect(self._on_progress)
        task.signals.finished.connect(self._on_finished)
        task.signals.failed.connect(self._on_failed)
        self.pending.add(image_id)
        self.pool.start(task)

    @Slot(str, object, object)
    def _on_progress(self, image_id: str, done: int, total: int) -> None:
        self.progress.emit(image_id, done, total)

    @Slot(str, str, str, object, object)
    def _on_finished(self, image_id: str, path: str, digest: str, size: int, mtime_ns: int) -> None:
        self.pending.discard(image_id)
        self.finished.emit(image_id, path, digest, size, mtime_ns)

    @Slot(str, str)
    def _on_failed(self, image_id: str, message: str) -> None:
        self.pending.discard(image_id)
        self.failed.emit(image_id, message)

    def wait(self, ms: int = 30000) -> bool:
        return self.pool.waitForDone(ms)

    def cancel_all(self, wait_ms: int = 5000) -> None:
        self._cancel.set()
        self.pool.clear()
        self.pool.waitForDone(wait_ms)
        self.pending.clear()
        self._cancel = threading.Event()
