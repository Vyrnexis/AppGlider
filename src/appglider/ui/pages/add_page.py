"""The Add app page: inspect a public GitHub repository, then add it to the library."""

from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

from PySide6.QtCore import QStandardPaths, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from appglider import APP_ICON
from appglider.core.catalog import OWN_REPO_NOTE, App, custom_app, is_own_repo, manual_app
from appglider.github.assets import Release, variant_label
from appglider.github.download import Progress, latest_release
from appglider.github.http import RepositoryMoved
from appglider.github.icons import (
    MAX_ICON_BYTES,
    IconCandidate,
    discover_icons,
    normalise_icon,
)
from appglider.github.release_info import remember_release
from appglider.github.versions import architecture
from appglider.install.place import FileDetails, describe_appimage
from appglider.ui.controls import MessageBox, Segmented
from appglider.ui.errors import describe_error
from appglider.ui.jobs import Task, TaskResult
from appglider.ui.pages.app_page import (
    SettingRow,
    VersionValue,
    add_setting_rows,
    format_size,
    panel,
    set_object_name,
)

if TYPE_CHECKING:
    from appglider.ui.window import LauncherWindow

PROMPT = "Look up an app, or choose a file, before adding it."
MAX_WIDTH = 720
FILE_NOTE = "AppGlider cannot check apps added from a file for updates."


def start_folder() -> str:
    """Where file pickers open: the Downloads folder, else the home folder."""
    downloads = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
    if downloads and Path(downloads).is_dir():
        return downloads
    return str(Path.home())


@dataclass(frozen=True)
class ChosenFile:
    """An AppImage picked on this computer, with what was read from inside it."""

    source: Path
    details: FileDetails


class AddPage(QScrollArea):
    """Add-an-application flow. Leaving the page cancels a running search and drops its result."""

    added = Signal(str, str)  # app id, a warning to append to the footer message
    add_and_install = Signal(str, str)  # same, when "Add and install" was used
    copy_changed = Signal()  # a file copy started or ended
    copy_failed = Signal(str)  # a copy failed after the page was left: the error to show

    def __init__(self, launcher: "LauncherWindow") -> None:
        super().__init__()
        self.launcher = launcher
        self.library = launcher.library
        self.task: Task | None = None
        self.candidate: App | None = None
        self.release: Release | None = None
        self.icon_target: App | None = None
        self.icon_options: tuple[IconCandidate, ...] = ()
        self.selected_icon: bytes | None = None
        self.closing = False
        self.inspecting = False  # the running task is a repository inspection
        self.cancelling = False  # the user pressed Cancel on it
        self.copying = False  # a file is being copied into the library
        self.adding = False
        self.build_override: str | None = None
        self.inspection_app: App | None = None
        self.manual: ChosenFile | None = None
        self.reading_file = False  # a chosen file is being read
        self.setObjectName("addPage")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self.setWidget(content)
        outer = QVBoxLayout(content)
        outer.setContentsMargins(22, 20, 22, 20)
        column_widget = QWidget()
        column_widget.setMaximumWidth(MAX_WIDTH)
        outer.addWidget(column_widget, alignment=Qt.AlignmentFlag.AlignTop)
        outer.addStretch()
        column = QVBoxLayout(column_widget)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(14)
        title = QLabel("Add app")
        title.setObjectName("pageTitle")
        column.addWidget(title)
        note = QLabel("Paste a public GitHub repository to find its latest AppImage.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        column.addWidget(note)

        frame, layout, self.repository_heading = panel("Repository")
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://github.com/owner/application")
        self.url.setAccessibleName("GitHub repository")
        self.url.setMinimumWidth(300)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Optional display name")
        self.name.setAccessibleName("Application name")
        self.name.setMinimumWidth(300)
        self.url.textChanged.connect(lambda _: self.invalidate_preview())
        self.name.textChanged.connect(lambda _: self.name_edited())
        self.url.returnPressed.connect(self.inspect_repository)
        self.name.returnPressed.connect(self.enter_in_name)
        self.release_channel = Segmented(("Stable", "Preview"))
        self.release_channel.setAccessibleName("Release preference")
        self.release_channel.buttons[0].setAccessibleName("Stable releases only")
        self.release_channel.buttons[1].setAccessibleName("Stable and preview versions")
        self.release_channel.currentIndexChanged.connect(self.change_release_preference)
        self.repository_row = SettingRow("Repository", "A GitHub address or owner/name", self.url)
        self.name_row = SettingRow("Name", "Shown in your library", self.name)
        self.channel_row = SettingRow(
            "Release channel",
            "Previews may be less reliable. Stable releases stay eligible either way.",
            self.release_channel,
        )
        dividers = add_setting_rows(layout, (self.repository_row, self.name_row, self.channel_row))
        self.github_only: tuple[QWidget, ...] = (
            self.repository_row,
            dividers[self.name_row],
            self.channel_row,
            dividers[self.channel_row],
        )
        layout.addSpacing(6)
        self.inspect_button = QPushButton("Look up app")
        self.inspect_button.setObjectName("primary")
        self.inspect_button.setAutoDefault(False)
        self.inspect_button.clicked.connect(self.inspect_or_cancel)
        layout.addWidget(self.inspect_button, alignment=Qt.AlignmentFlag.AlignLeft)
        column.addWidget(frame)

        self.build_group, layout, _ = panel("Build")
        self.build_combo = QComboBox()
        self.build_combo.setAccessibleName("AppImage build variant")
        self.build_combo.setItemDelegate(QStyledItemDelegate(self.build_combo))
        self.build_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.build_combo.setMinimumContentsLength(20)
        self.build_combo.currentIndexChanged.connect(self.change_build_variant)
        layout.addWidget(
            SettingRow(
                "Build variant",
                "Choose the build for your system. It is remembered for updates.",
                self.build_combo,
            )
        )
        column.addWidget(self.build_group)
        self.build_group.hide()

        self.release_preview, layout, self.release_heading = panel("Available release")
        self.version_label = VersionValue()
        self.version = self.version_label.text
        self.filename = QLabel("—")
        self.filename.setTextFormat(Qt.TextFormat.PlainText)
        self.filename.setWordWrap(True)
        self.filename.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.checksum = QLabel("Not checked")
        self.checksum.setWordWrap(True)
        self.checksum.setObjectName("muted")
        self.captions: dict[str, QLabel] = {}
        for caption, value in (
            ("Version", self.version_label),
            ("Download", self.filename),
            ("Verification", self.checksum),
        ):
            line = QHBoxLayout()
            line.setSpacing(10)
            label = QLabel(caption)
            self.captions[caption] = label
            label.setObjectName("captionLabel")
            label.setFixedWidth(84)
            line.addWidget(label, alignment=Qt.AlignmentFlag.AlignTop)
            line.addWidget(value, stretch=1)
            layout.addLayout(line)
        column.addWidget(self.release_preview)

        self.icon_group, layout, _ = panel("Icon")
        icon_row = QHBoxLayout()
        icon_row.setSpacing(14)
        self.icon_preview = QLabel()
        self.icon_preview.setFixedSize(64, 64)
        self.icon_preview.setAccessibleName("Application icon preview")
        icon_row.addWidget(self.icon_preview)
        icon_controls = QVBoxLayout()
        self.icon_choices = QComboBox()
        self.icon_choices.setItemDelegate(QStyledItemDelegate(self.icon_choices))
        self.icon_choices.setAccessibleName("Application icon")
        self.icon_choices.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.icon_choices.setMinimumContentsLength(12)
        self.icon_choices.currentIndexChanged.connect(self.select_icon)
        icon_controls.addWidget(self.icon_choices)
        self.choose_icon_button = QPushButton("Choose file…")
        self.choose_icon_button.clicked.connect(self.choose_icon)
        icon_controls.addWidget(self.choose_icon_button, alignment=Qt.AlignmentFlag.AlignLeft)
        icon_row.addLayout(icon_controls, stretch=1)
        layout.addLayout(icon_row)
        column.addWidget(self.icon_group)

        # Status and progress sit next to the buttons they belong to.
        actions = QHBoxLayout()
        actions.setSpacing(12)
        feedback = QVBoxLayout()
        feedback.setSpacing(6)
        self.status = QLabel(PROMPT)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setObjectName("muted")
        self.status.setAccessibleName("Add app status")
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(self.status.fontMetrics().lineSpacing() * 2)
        self.status.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        feedback.addWidget(self.status)
        self.busy_bar = QProgressBar()
        retain = self.busy_bar.sizePolicy()
        retain.setRetainSizeWhenHidden(True)
        self.busy_bar.setSizePolicy(retain)
        self.busy_bar.setRange(0, 0)  # moving until a size is known
        self.busy_bar.setFixedHeight(6)
        self.busy_bar.setTextVisible(False)
        self.busy_bar.setAccessibleName("Progress")
        self.busy_bar.hide()
        feedback.addWidget(self.busy_bar)
        actions.addLayout(feedback, stretch=1)
        self.add_button = QPushButton("Add to library")
        self.add_button.clicked.connect(lambda: self.add_repository())
        actions.addWidget(self.add_button, alignment=Qt.AlignmentFlag.AlignTop)
        self.install_button = QPushButton("Add and install")
        self.install_button.setAccessibleName("Add to library and install")
        self.install_button.clicked.connect(lambda: self.add_repository(install=True))
        actions.addWidget(self.install_button, alignment=Qt.AlignmentFlag.AlignTop)
        column.addLayout(actions)

        frame, layout, _ = panel("Add a file from this computer")
        self.file_frame = frame
        file_note = QLabel(FILE_NOTE)
        file_note.setObjectName("muted")
        file_note.setWordWrap(True)
        layout.addWidget(file_note)
        self.file_chosen = QLabel()
        self.file_chosen.setTextFormat(Qt.TextFormat.PlainText)
        self.file_chosen.setAccessibleName("Chosen file")
        self.file_chosen.setWordWrap(True)
        self.file_chosen.hide()
        layout.addWidget(self.file_chosen)
        file_buttons = QHBoxLayout()
        file_buttons.setSpacing(8)
        self.file_button = QPushButton("Choose AppImage…")
        self.file_button.setAutoDefault(False)
        self.file_button.clicked.connect(self.choose_file)
        file_buttons.addWidget(self.file_button)
        self.file_clear_button = QPushButton("Clear")
        self.file_clear_button.setAccessibleName("Clear the chosen file")
        self.file_clear_button.setAutoDefault(False)
        self.file_clear_button.clicked.connect(self.clear_file)
        file_buttons.addWidget(self.file_clear_button)
        file_buttons.addStretch()
        layout.addLayout(file_buttons)
        column.addWidget(frame)
        self.invalidate_preview()
        self.set_busy(False)

    # Lifecycle

    def start(self) -> None:
        """Show an empty form. A search that is still stopping keeps the form disabled."""
        self.adding = True
        self.release_channel.setCurrentIndex(0)
        self.url.clear()
        self.name.clear()
        self.invalidate_preview()
        self.url.setFocus()
        self.set_busy(self.task is not None)
        if self.task is not None:
            self.say("Stopping… one moment.")

    def leave(self) -> None:
        """The page was left: cancel a running search; its result is dropped when it arrives."""
        if not self.adding:
            return
        self.adding = False
        self.stop()
        if self.task is None:
            self.invalidate_preview()

    def stop(self) -> bool:
        """Cancel any running search; return True when nothing is left running."""
        if self.task is None:
            return True
        self.closing = True
        if not self.copying:  # a copy in progress finishes; the app is then reported
            self.task.cancel.set()
        self.say("Stopping… one moment.")
        return False

    def update_state(self) -> None:
        """Called by the window when a window task starts or ends."""
        self.set_busy(self.task is not None)

    # Form

    def say(self, text: str, *, error: bool = False) -> None:
        """Show a status line: muted for progress and results, red for problems."""
        self.status.setText(text)
        set_object_name(self.status, "settingError" if error else "muted")

    def change_release_preference(self) -> None:
        if self.task is None and self.adding:
            inspected = self.release is not None
            self.invalidate_preview(clear_icons=False)
            if inspected:
                self.say("Release channel changed. Inspect again.")

    @property
    def ready(self) -> bool:
        """Whether there is something to add: an inspected release or a chosen file."""
        return self.release is not None or self.manual is not None

    def enter_in_name(self) -> None:
        """Enter in the Name field adds the app to the library when ready; it never downloads."""
        if self.task is not None:
            return
        button = self.add_button
        if button.isEnabled():
            button.click()

    def name_edited(self) -> None:
        if self.manual is None:
            self.invalidate_preview()
        elif self.task is None:
            self.set_busy(False)  # a file needs a name before it can be added

    def show_captions(self, release_heading: str, download: str, verification: str) -> None:
        self.release_heading.setText(release_heading.upper())
        self.release_heading.setAccessibleName(release_heading)
        self.captions["Download"].setText(download)
        self.captions["Verification"].setText(verification)

    def invalidate_preview(self, *, clear_build: bool = True, clear_icons: bool = True) -> None:
        self.candidate = None
        self.release = None
        self.manual = None
        self.show_captions("Available release", "Download", "Verification")
        self.icon_target = None
        if clear_icons:
            self.set_icon_options(())
        self.choose_icon_button.setEnabled(False)
        self.icon_choices.setEnabled(False)
        self.add_button.setEnabled(False)
        self.install_button.setEnabled(False)
        self.version.setText("Not looked up")
        self.filename.setText("—")
        self.checksum.setText("Not checked")
        self.say(PROMPT)
        self.release_preview.hide()
        self.icon_group.hide()
        if clear_build:
            self.build_override = None
            self.build_combo.blockSignals(True)
            self.build_combo.clear()
            self.build_combo.blockSignals(False)
            self.build_group.hide()

    def show_build_choices(self, choices: tuple[tuple[str, str], ...], selected: str = "") -> None:
        self.build_combo.blockSignals(True)
        self.build_combo.clear()
        self.build_combo.addItem("Choose a build…", "")
        for pattern, filename in choices:
            self.build_combo.addItem(variant_label(pattern), pattern)
            self.build_combo.setItemData(
                self.build_combo.count() - 1, filename, Qt.ItemDataRole.ToolTipRole
            )
        self.build_combo.setCurrentIndex(max(0, self.build_combo.findData(selected)))
        self.build_combo.blockSignals(False)
        self.build_group.show()
        QTimer.singleShot(0, self, lambda: self.ensureWidgetVisible(self.build_group))

    def change_build_variant(self) -> None:
        if self.task is not None or not self.build_combo.currentData():
            return
        self.build_override = str(self.build_combo.currentData())
        self.inspect_repository()

    def inspect_or_cancel(self) -> None:
        """The button inspects, and while an inspection runs it cancels it."""
        if self.task is not None and self.inspecting:
            if not (self.cancelling or self.closing):
                self.cancelling = True
                self.task.cancel.set()
                self.say("Cancelling…")
                self.set_busy(True)
            return
        self.inspect_repository()

    def inspect_repository(self) -> None:
        if self.task is not None or not self.adding or self.launcher.task is not None:
            return
        if self.manual is not None:  # never throw a chosen file away without being asked
            self.say("A file is chosen. Clear it first to look up a repository.", error=True)
            return
        self.invalidate_preview(clear_build=False, clear_icons=False)
        try:
            app = custom_app(
                self.url.text(),
                self.name.text() or None,
                self.release_channel.currentIndex() == 1,
                self.build_override or "",
            )
            if is_own_repo(app.repo):
                raise ValueError(OWN_REPO_NOTE)
            owned = next(
                (
                    known
                    for known in self.library.apps
                    if known.repo.casefold() == app.repo.casefold()
                ),
                None,
            )
            if owned is not None:
                raise ValueError(f"Already in your library as {owned.name}.")
        except (OSError, ValueError) as error:
            self.say(describe_error(error), error=True)
            return
        self.name.blockSignals(True)
        self.name.setText(app.name)
        self.name.blockSignals(False)
        self.candidate = app
        self.icon_target = app
        lookup_icons = not self.icon_options
        self.inspection_app = app

        known = {known.repo.casefold(): known.name for known in self.library.apps}

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            target = app
            try:
                release = latest_release(target, cancel, self.library.data_dir / "release-cache")
            except RepositoryMoved as moved:
                # Add it under its new address, so the old name is never used again.
                target = custom_app(moved.new, app.name, app.include_prereleases, app.build_variant)
                if is_own_repo(target.repo):
                    raise ValueError(f"{moved} {OWN_REPO_NOTE}") from moved
                if target.repo.casefold() in known:
                    raise ValueError(
                        f"{moved} Already in your library as {known[target.repo.casefold()]}."
                    ) from moved
                release = latest_release(target, cancel, self.library.data_dir / "release-cache")
            result = TaskResult(
                "Release inspected.",
                {target.id: release},
                moved=target if target is not app else None,
            )
            try:
                if lookup_icons:
                    result.icons = discover_icons(target, cancel, lambda text: progress(text, 0, 0))
            except (OSError, ValueError) as error:
                result.errors[app.id] = f"Icon lookup unavailable: {describe_error(error)}"
            return result

        self.task = Task(operation)
        self.inspecting = True
        self.cancelling = False
        self.task.signals.progress.connect(self.task_progress)
        self.task.signals.finished.connect(self.inspection_finished)
        self.task.signals.failed.connect(self.inspection_failed)
        self.set_busy(True)
        self.say("Checking the latest release…")
        QThreadPool.globalInstance().start(self.task)

    def set_busy(self, busy: bool) -> None:
        blocked = busy or self.launcher.task is not None
        file_mode = self.manual is not None or self.reading_file
        self.busy_bar.setVisible(busy)
        if not busy:
            self.busy_bar.setRange(0, 0)
        self.repository_heading.setText("CHOSEN FILE" if file_mode else "REPOSITORY")
        self.repository_heading.setAccessibleName("Chosen file" if file_mode else "Repository")
        for widget in self.github_only:
            widget.setVisible(not file_mode)
        self.inspect_button.setVisible(not file_mode)
        self.build_combo.setEnabled(not blocked)
        self.release_channel.setEnabled(not busy)
        self.url.setEnabled(not busy)
        self.name.setEnabled(not busy)
        if busy and self.inspecting:
            stopping = self.cancelling or self.closing
            self.inspect_button.setEnabled(not stopping)
            self.inspect_button.setText("Cancelling…" if stopping else "Cancel")
        else:
            self.inspect_button.setEnabled(not blocked)
            self.inspect_button.setText("Looking up…" if busy else "Look up app")
        github_ready = self.release is not None
        # One primary button at a time: the next step people most likely want.
        set_object_name(self.inspect_button, "" if self.ready else "primary")
        set_object_name(self.install_button, "primary" if github_ready else "")
        set_object_name(self.add_button, "primary" if file_mode else "")
        self.install_button.setVisible(not file_mode)
        self.file_button.setEnabled(not blocked)
        self.file_button.setText(
            "Choose another…" if self.manual is not None else "Choose AppImage…"
        )
        self.file_clear_button.setVisible(self.manual is not None)
        self.file_clear_button.setEnabled(not blocked)
        self.file_chosen.setVisible(self.manual is not None)
        if self.manual is not None:
            self.file_chosen.setText(
                f"{self.manual.source.name} · {format_size(self.manual.details.size)}"
            )
        named = self.manual is None or bool(self.name.text().strip())
        if busy and self.copying:
            stopping = self.cancelling or self.closing
            self.add_button.setEnabled(not stopping)
            self.add_button.setText("Cancelling…" if stopping else "Cancel")
            self.add_button.setToolTip("Stop copying the file.")
        else:
            self.add_button.setText("Add to library")
            self.add_button.setEnabled(not blocked and self.ready and named)
            self.add_button.setToolTip(
                self.add_tip(
                    blocked,
                    named,
                    "Copy the file into your library."
                    if file_mode
                    else "Add to your library without installing it.",
                )
            )
        self.install_button.setEnabled(not blocked and github_ready)
        self.install_button.setToolTip(
            self.add_tip(blocked, True, "Add to your library and install it now.")
        )
        self.release_preview.setVisible(self.ready)
        self.icon_group.setVisible(self.icon_target is not None and self.ready)
        self.choose_icon_button.setEnabled(not blocked and self.icon_target is not None)
        self.icon_choices.setEnabled(not blocked and self.icon_target is not None)

    def add_tip(self, blocked: bool, named: bool, ready_tip: str) -> str:
        """The tooltip for an Add button: what it does, or what to do first."""
        if self.copying:
            return ready_tip
        if self.launcher.task is not None:
            return "Wait for the current task to finish, or cancel it."
        if blocked:
            return "Wait for this to finish."
        if not self.ready:
            return "Look up an app or choose a file first."
        if not named:
            return "Type a name for the app first."
        return ready_tip

    def inspection_finished(self, result: TaskResult) -> None:
        self.task = None
        self.inspecting = False
        cancelled, self.cancelling = self.cancelling, False
        if self.closing:
            self.closing = False
            self.invalidate_preview()
            self.set_busy(False)
            return
        if cancelled and not result.releases and not result.build_choices:
            self.set_busy(False)
            self.say("Lookup cancelled.")
            return
        moved, old = result.moved, self.inspection_app.repo if self.inspection_app else ""
        if moved is not None:
            self.candidate = self.icon_target = self.inspection_app = moved
            self.url.blockSignals(True)  # a new address would otherwise discard this result
            self.url.setText(moved.source_url)
            self.url.blockSignals(False)
        target = self.inspection_app
        if result.build_choices and target:
            self.set_busy(False)
            error = result.build_choices[target.id]
            self.show_build_choices(error.choices)
            self.say(
                "Your saved build is unavailable. Choose a replacement to continue."
                if error.missing
                else "Multiple builds are available. Choose a build variant to continue."
            )
            return
        self.release = result.releases.get(target.id) if target else None
        if self.release is None:
            self.set_busy(False)
            self.say(result.message, error=True)
            return
        if target is not None:
            remember_release(target, self.release, self.library.data_dir / "release-cache")
        self.version_label.set_version(
            f"v{self.release.version} · Linux {architecture()}",
            self.release.prerelease,
            self.release.is_latest,
        )
        self.filename.setText(self.release.name)
        self.checksum.setText("Checksum found; checked after download")
        if not self.icon_options:
            self.set_icon_options(result.icons)
        self.say(
            (
                "Ready to add."
                if self.selected_icon is not None
                else "Ready to add with the default icon."
            )
            + (" " + result.errors[target.id] if target and target.id in result.errors else "")
            + (
                f" {old} moved to {moved.repo} on GitHub, so it is added under the new address."
                if moved is not None
                else ""
            )
        )
        self.set_busy(False)
        QTimer.singleShot(0, self, lambda: self.ensureWidgetVisible(self.add_button))

    def inspection_failed(self, error: str) -> None:
        self.inspection_finished(TaskResult(error))

    def task_progress(self, message: str, received: object, total: object) -> None:
        if self.task is None or self.closing:
            return
        if message:
            self.say(message)
        if isinstance(received, int) and isinstance(total, int) and total > 0:
            self.busy_bar.setRange(0, 1000)
            self.busy_bar.setValue(min(1000, received * 1000 // total))
        else:
            self.busy_bar.setRange(0, 0)

    def choose_file(self) -> None:
        if self.task is not None or not self.adding or self.launcher.task is not None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose an AppImage",
            start_folder(),
            "AppImage files (*.AppImage *.appimage);;All files (*)",
        )
        if path:
            self.inspect_file(Path(path))

    def inspect_file(self, source: Path) -> None:
        """Read the name, version and icon from inside the file, off the Qt thread."""
        self.invalidate_preview()
        self.reading_file = True

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            progress(f"Reading {source.name}…", 0, 0)
            return TaskResult("", chosen=ChosenFile(source, describe_appimage(source)))

        self.task = Task(operation)
        self.task.signals.progress.connect(self.task_progress)
        self.task.signals.finished.connect(self.file_inspected)
        self.task.signals.failed.connect(self.file_inspection_failed)
        self.set_busy(True)
        self.say(f"Reading {source.name}…")
        QThreadPool.globalInstance().start(self.task)

    def file_inspected(self, result: TaskResult) -> None:
        self.task = None
        self.reading_file = False
        if self.closing:
            self.closing = False
            self.invalidate_preview()
            self.set_busy(False)
            return
        chosen = result.chosen
        if not isinstance(chosen, ChosenFile):
            self.file_inspection_failed(result.message)
            return
        details = chosen.details
        try:
            target = manual_app(details.name, chosen.source.name)
        except ValueError as error:
            self.file_inspection_failed(str(error))
            return
        self.manual = chosen
        self.release = self.candidate = None
        self.url.blockSignals(True)
        self.url.clear()
        self.url.blockSignals(False)
        self.name.blockSignals(True)
        self.name.setText(details.name)
        self.name.blockSignals(False)
        self.icon_target = target
        self.show_captions("Selected file", "File", "Check")
        self.version_label.set_version(f"v{details.version}" if details.version else "Unknown")
        self.filename.setText(f"{chosen.source.name} · {format_size(details.size)}")
        self.checksum.setText(
            "A type 2 AppImage for this computer." + (f"\n{details.note}" if details.note else "")
        )
        self.set_icon_options(
            (IconCandidate("Icon from the AppImage", details.icon),) if details.icon else ()
        )
        self.say(
            "Ready to add. The file is copied; the original stays where it is."
            + ("" if details.icon else " No icon was found in it, so the default icon is used.")
        )
        self.set_busy(False)
        QTimer.singleShot(0, self, lambda: self.ensureWidgetVisible(self.add_button))

    def file_inspection_failed(self, error: str) -> None:
        self.task = None
        self.reading_file = False
        if self.closing:
            self.closing = False
            self.invalidate_preview()
        else:
            self.invalidate_preview()
            self.say(error, error=True)
        self.set_busy(False)

    def clear_file(self) -> None:
        """Forget the chosen file and show the repository form again."""
        if self.task is not None:
            return
        self.invalidate_preview()
        self.set_busy(False)
        self.url.setFocus()

    def add_file(self) -> None:
        chosen = self.manual
        if chosen is None or self.task is not None or self.launcher.task is not None:
            return
        try:
            app = manual_app(self.name.text(), chosen.source.name)
        except ValueError as error:
            self.say(str(error), error=True)
            return
        png, version = self.selected_icon, chosen.details.version

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            progress(f"Copying {chosen.source.name}…", 0, 0)
            found: list[str] = []
            warning = self.library.add_file(
                app,
                chosen.source,
                version,
                png,
                cancel,
                identify=bool(chosen.details.note),
                progress=progress,
                warnings=found,
            )
            if found:
                warning = f"{warning} {found[0]}"
            return TaskResult(warning, added=app, warnings={app.id: found[0]} if found else {})

        self.copying = True
        self.task = Task(operation)
        self.task.signals.progress.connect(self.task_progress)
        self.task.signals.finished.connect(self.file_added)
        self.task.signals.failed.connect(self.file_add_failed)
        self.set_busy(True)
        self.say(f"Copying {chosen.source.name}…")
        self.copy_changed.emit()
        QThreadPool.globalInstance().start(self.task)

    def cancel_copy(self) -> None:
        """The Add button cancels a file copy that is running."""
        if self.task is None or self.cancelling or self.closing:
            return
        self.cancelling = True
        self.task.cancel.set()
        self.say("Cancelling…")
        self.set_busy(True)

    def file_added(self, result: TaskResult) -> None:
        self.task = None
        self.copying = False
        self.copy_changed.emit()
        cancelled, self.cancelling = self.cancelling, False
        app = result.added
        if result.warnings:
            MessageBox.warning(
                self, "An install command needs a look", "\n\n".join(result.warnings.values())
            )
        if self.closing:
            # The page was left meanwhile, but the app is in the library: tell the window.
            self.closing = False
            self.invalidate_preview()
            self.set_busy(False)
            if app is not None:
                self.added.emit(app.id, result.message)
            return
        self.set_busy(False)
        if app is None:  # cancelled: the file stays chosen
            name = self.manual.source.name if self.manual else "The file"
            if cancelled:
                self.say(f"Cancelled. {name} was not added.")
            else:
                self.say(result.message, error=True)
            return
        self.manual = None
        self.added.emit(app.id, result.message)

    def file_add_failed(self, error: str) -> None:
        self.task = None
        self.copying = False
        self.cancelling = False
        left = self.closing or self.launcher.pages.currentWidget() is not self
        self.closing = False
        if left:
            self.invalidate_preview()
        self.set_busy(False)
        self.say(error, error=True)
        self.copy_changed.emit()
        if left:
            self.copy_failed.emit(error)

    def add_repository(self, install: bool = False) -> None:
        """Add the looked-up repository; with `install`, the window then installs it."""
        if self.copying:
            self.cancel_copy()
            return
        if self.manual is not None:
            self.add_file()
            return
        if (
            self.task is not None
            or self.candidate is None
            or self.release is None
            or self.launcher.task is not None
        ):
            return
        app, png = self.candidate, self.selected_icon
        warning = ""
        try:
            self.library.add_repository(app)
            if png is not None:
                try:
                    self.library.set_icon(app, png)
                except (OSError, ValueError) as error:
                    warning = f" Icon could not be saved: {describe_error(error)}"
        except (OSError, ValueError) as error:
            self.say(describe_error(error), error=True)
            return
        self.add_button.setEnabled(False)
        self.install_button.setEnabled(False)
        self.release = None
        self.candidate = None
        (self.add_and_install if install else self.added).emit(app.id, warning)

    # Icons

    def show_icon(self, path: Path | bytes) -> None:
        image = QPixmap()
        if isinstance(path, bytes):
            image.loadFromData(path, "PNG")
        else:
            image.load(str(path))
        if image.isNull():
            image.load(str(APP_ICON))
        self.icon_preview.setPixmap(
            image.scaled(
                64,
                64,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def set_icon_options(self, icons: tuple[IconCandidate, ...], select: int = 0) -> None:
        self.icon_options = icons
        self.icon_choices.blockSignals(True)
        self.icon_choices.clear()
        for icon in icons:
            self.icon_choices.addItem(Path(icon.name).name)
            self.icon_choices.setItemData(
                self.icon_choices.count() - 1, icon.name, Qt.ItemDataRole.ToolTipRole
            )
        self.icon_choices.addItem("AppGlider default")
        select = select if 0 <= select < len(icons) else 0
        self.icon_choices.setCurrentIndex(select)
        self.icon_choices.blockSignals(False)
        self.selected_icon = icons[select].png if icons else None
        self.show_icon(self.selected_icon if self.selected_icon is not None else APP_ICON)

    def select_icon(self, index: int) -> None:
        if self.task is not None or index < 0:
            return
        self.selected_icon = (
            self.icon_options[index].png if index < len(self.icon_options) else None
        )
        self.show_icon(self.selected_icon if self.selected_icon is not None else APP_ICON)

    def choose_icon(self) -> None:
        if self.task is not None or self.icon_target is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose application icon", start_folder(), "Application icons (*.png *.svg)"
        )
        if not path:
            return
        try:
            with Path(path).open("rb") as source:
                png = normalise_icon(source.read(MAX_ICON_BYTES + 1))
        except (OSError, ValueError) as error:
            self.say(describe_error(error), error=True)
            return
        options = (*self.icon_options, IconCandidate(Path(path).name, png))
        self.set_icon_options(options, select=len(options) - 1)
