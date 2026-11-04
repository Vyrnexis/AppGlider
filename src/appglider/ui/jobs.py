"""One cancellable background operation at a time; results return through Qt signals."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QRunnable, Signal

from appglider.core.catalog import App
from appglider.github.assets import BuildChoiceRequired, Release
from appglider.github.download import Progress
from appglider.github.icons import IconCandidate
from appglider.github.release_info import ReleaseInfo
from appglider.github.versions import Cancelled
from appglider.install.library import AppRunning
from appglider.install.self_update import Downloaded


@dataclass
class TaskResult:
    message: str
    releases: dict[str, Release] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    icons: tuple[IconCandidate, ...] = ()
    release_infos: dict[str, ReleaseInfo] = field(default_factory=dict)
    build_choices: dict[str, BuildChoiceRequired] = field(default_factory=dict)
    downloaded: Downloaded | None = None
    placed: Path | None = None  # AppGlider's copy in its new folder, ready to start
    moved: App | None = None  # the app under its new address, when Add app found a move
    running: str = ""  # the app that must be closed before the action can go ahead
    chosen: object = None  # a file picked on the Add page, with what was read from it
    added: App | None = None  # the app added from a file


class TaskSignals(QObject):
    progress = Signal(str, object, object)
    finished = Signal(object)
    failed = Signal(str)


class StepSignals(QObject):
    """One app of a longer task is done; reported before the whole task ends."""

    step = Signal(str, object)


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
        except AppRunning as error:
            self.signals.finished.emit(TaskResult(str(error), running=error.app_id))
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
