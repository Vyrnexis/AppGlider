"""Queue background release checks and run several at once when GitHub quota allows."""

from collections.abc import Callable
from threading import Event

from PySide6.QtCore import QObject, QThreadPool, Signal

from appglider.core.catalog import App
from appglider.github.download import Progress
from appglider.ui.jobs import Task, TaskResult

Operation = Callable[[Event, Progress], TaskResult]


class ReleaseChecks(QObject):
    started = Signal(str)
    completed = Signal(str, str, object, str)
    drained = Signal()

    def __init__(
        self,
        resolve: Callable[[str], App | None],
        operation: Callable[[App, bool], Operation],
        limit: Callable[[], int],
    ) -> None:
        super().__init__()
        self.resolve = resolve
        self.operation = operation
        self.limit = limit
        self.queue: list[str] = []
        self.tasks: dict[str, Task] = {}
        self.variants: dict[str, str] = {}
        self.force = False
        self.closing = False

    @property
    def busy(self) -> bool:
        return bool(self.tasks)

    def is_checking(self, app_id: str) -> bool:
        return app_id in self.tasks

    def add(self, app_ids: list[str]) -> None:
        for app_id in app_ids:
            if self.resolve(app_id) and app_id not in self.queue and app_id not in self.tasks:
                self.queue.append(app_id)
        self.pump()

    def requeue_active(self, app_id: str) -> None:
        if app_id in self.tasks and app_id not in self.queue:
            self.queue.append(app_id)

    def stop(self) -> None:
        self.closing = True
        self.queue.clear()
        for task in self.tasks.values():
            task.cancel.set()

    def pump(self) -> None:
        if not self.closing:
            while self.queue and len(self.tasks) < max(1, self.limit()):
                app = self.resolve(self.queue.pop(0))
                if app is not None:
                    self._start(app)
        if not self.tasks:
            self.drained.emit()

    def _start(self, app: App) -> None:
        task = Task(self.operation(app, self.force))
        self.tasks[app.id] = task
        self.variants[app.id] = app.build_variant
        task.signals.finished.connect(lambda result, app_id=app.id: self._done(app_id, result, ""))
        task.signals.failed.connect(lambda error, app_id=app.id: self._done(app_id, None, error))
        self.started.emit(app.id)
        QThreadPool.globalInstance().start(task)

    def _done(self, app_id: str, result: TaskResult | None, error: str) -> None:
        self.tasks.pop(app_id, None)
        variant = self.variants.pop(app_id, "")
        self.completed.emit(app_id, variant, result, error)
        self.pump()
