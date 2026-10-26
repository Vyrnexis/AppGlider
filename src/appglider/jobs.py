"""One cancellable background operation at a time; results return through Qt signals."""

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event

from PySide6.QtCore import QObject, QRunnable, Signal

from appglider.icons import IconCandidate
from appglider.release_info import ReleaseInfo
from appglider.releases import BuildChoiceRequired, Cancelled, Progress, Release
from appglider.self_update import Downloaded


@dataclass
class TaskResult:
    message: str
    releases: dict[str, Release] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    icons: tuple[IconCandidate, ...] = ()
    release_infos: dict[str, ReleaseInfo] = field(default_factory=dict)
    build_choices: dict[str, BuildChoiceRequired] = field(default_factory=dict)
    downloaded: Downloaded | None = None


class TaskSignals(QObject):
    progress = Signal(str, object, object)
    finished = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    def __init__(self, operation: Callable[[Event, Progress], TaskResult]) -> None:
        super().__init__()
        self.operation = operation
        self.cancel = Event()
        self.signals = TaskSignals()

    def run(self) -> None:
        try:
            result = self.operation(self.cancel, self.signals.progress.emit)
        except Cancelled as error:
            self.signals.finished.emit(TaskResult(str(error)))
        except BuildChoiceRequired as error:
            self.signals.finished.emit(TaskResult(str(error), build_choices={error.app_id: error}))
        except Exception as error:
            self.signals.failed.emit(str(error))
        else:
            self.signals.finished.emit(result)


class LookupSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class Lookup(QRunnable):
    """A small background question with one answer, kept apart from the window's single Task."""

    def __init__(self, operation: Callable[[Event], object]) -> None:
        super().__init__()
        self.operation = operation
        self.cancel = Event()
        self.signals = LookupSignals()

    def run(self) -> None:
        try:
            answer = self.operation(self.cancel)
        except Exception as error:
            self.signals.failed.emit(str(error))
        else:
            self.signals.finished.emit(answer)
