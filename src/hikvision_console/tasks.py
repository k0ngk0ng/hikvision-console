from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class Signals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class Task(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = Signals()

    @Slot()
    def run(self):
        try:
            result = self.function()
            self.signals.result.emit(result)
        except Exception as exc:
            # API errors are already sanitized. Never include arbitrary URLs in an exception.
            from .api import DeviceError
            self.signals.error.emit(str(exc) if isinstance(exc, (DeviceError, ValueError)) else type(exc).__name__)
        finally:
            self.signals.finished.emit()


class TaskPool(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(6)
        self.active = set()

    def submit(self, function, result=None, error=None):
        task = Task(function)
        self.active.add(task)
        if result:
            task.signals.result.connect(result)
        if error:
            task.signals.error.connect(error)
        task.signals.finished.connect(lambda: self.active.discard(task))
        self.pool.start(task)
        return task
