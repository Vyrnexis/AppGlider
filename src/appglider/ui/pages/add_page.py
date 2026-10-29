"""The Add app page: inspect a public GitHub repository, then add it to the library."""

from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QThreadPool, QTimer, Signal
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
from appglider.core.catalog import App, custom_app
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
from appglider.ui.controls import Segmented
from appglider.ui.jobs import Task, TaskResult
from appglider.ui.pages.app_page import SettingRow, VersionValue, add_setting_rows, panel

if TYPE_CHECKING:
    from appglider.ui.window import LauncherWindow

PROMPT = "Inspect this repository before adding it."
MAX_WIDTH = 720


class AddPage(QScrollArea):
    """Add-an-application flow. Leaving the page cancels a running search and drops its result."""

    added = Signal(str, str)  # app id, a warning to append to the footer message

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
        self.adding = False
        self.build_override: str | None = None
        self.inspection_app: App | None = None
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

        frame, layout, _ = panel("Repository")
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://github.com/owner/application")
        self.url.setAccessibleName("GitHub repository")
        self.url.setMinimumWidth(300)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Optional display name")
        self.name.setAccessibleName("Application name")
        self.name.setMinimumWidth(300)
        self.url.textChanged.connect(lambda _: self.invalidate_preview())
        self.name.textChanged.connect(lambda _: self.invalidate_preview())
        self.url.returnPressed.connect(self.inspect_repository)
        self.release_channel = Segmented(("Stable", "Preview"))
        self.release_channel.setAccessibleName("Release preference")
        self.release_channel.buttons[0].setAccessibleName("Stable releases only")
        self.release_channel.buttons[1].setAccessibleName("Stable and prerelease versions")
        self.release_channel.currentIndexChanged.connect(self.change_release_preference)
        add_setting_rows(
            layout,
            (
                SettingRow("Repository", "A GitHub address or owner/name", self.url),
                SettingRow("Name", "Shown in your library", self.name),
                SettingRow(
                    "Release channel",
                    "Previews may be less reliable. Stable releases stay eligible either way.",
                    self.release_channel,
                ),
            ),
        )
        layout.addSpacing(6)
        self.inspect_button = QPushButton("Inspect repository")
        self.inspect_button.setObjectName("primary")
        self.inspect_button.setAutoDefault(False)
        self.inspect_button.clicked.connect(self.inspect_repository)
        layout.addWidget(self.inspect_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.status = QLabel(PROMPT)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(self.status.fontMetrics().lineSpacing() * 2)
        self.status.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.status)
        self.busy_bar = QProgressBar()
        retain = self.busy_bar.sizePolicy()
        retain.setRetainSizeWhenHidden(True)
        self.busy_bar.setSizePolicy(retain)
        self.busy_bar.setRange(0, 0)  # moving, because the time needed is not known
        self.busy_bar.setFixedHeight(6)
        self.busy_bar.setTextVisible(False)
        self.busy_bar.setAccessibleName("Search in progress")
        self.busy_bar.hide()
        layout.addWidget(self.busy_bar)
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

        self.release_preview, layout, _ = panel("Available release")
        self.version_label = VersionValue()
        self.version = self.version_label.text
        self.filename = QLabel("—")
        self.filename.setTextFormat(Qt.TextFormat.PlainText)
        self.filename.setWordWrap(True)
        self.filename.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.checksum = QLabel("Not checked")
        self.checksum.setWordWrap(True)
        self.checksum.setObjectName("muted")
        for caption, value in (
            ("Version", self.version_label),
            ("Download", self.filename),
            ("Verification", self.checksum),
        ):
            line = QHBoxLayout()
            line.setSpacing(10)
            label = QLabel(caption)
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

        actions = QHBoxLayout()
        actions.addStretch()
        self.add_button = QPushButton("Add to library")
        self.add_button.setObjectName("primary")
        self.add_button.clicked.connect(self.add_repository)
        actions.addWidget(self.add_button)
        column.addLayout(actions)
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
            self.status.setText("Stopping… one moment.")

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
        self.task.cancel.set()
        self.status.setText("Stopping… one moment.")
        return False

    def update_state(self) -> None:
        """Called by the window when a window task starts or ends."""
        self.set_busy(self.task is not None)

    # Form

    def change_release_preference(self) -> None:
        if self.task is None and self.adding:
            self.invalidate_preview(clear_icons=False)

    def invalidate_preview(self, *, clear_build: bool = True, clear_icons: bool = True) -> None:
        self.candidate = None
        self.release = None
        self.icon_target = None
        if clear_icons:
            self.set_icon_options(())
        self.choose_icon_button.setEnabled(False)
        self.icon_choices.setEnabled(False)
        self.add_button.setEnabled(False)
        self.version.setText("Not inspected")
        self.filename.setText("—")
        self.checksum.setText("Not checked")
        self.status.setText(PROMPT)
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

    def inspect_repository(self) -> None:
        if self.task is not None or not self.adding or self.launcher.task is not None:
            return
        self.invalidate_preview(clear_build=False, clear_icons=False)
        try:
            app = custom_app(
                self.url.text(),
                self.name.text() or None,
                self.release_channel.currentIndex() == 1,
                self.build_override or "",
            )
            if any(known.repo.casefold() == app.repo.casefold() for known in self.library.apps):
                raise ValueError("This repository is already in the catalog.")
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
            return
        self.name.blockSignals(True)
        self.name.setText(app.name)
        self.name.blockSignals(False)
        self.candidate = app
        self.icon_target = app
        lookup_icons = not self.icon_options
        self.inspection_app = app

        known = [known.repo.casefold() for known in self.library.apps]

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            target = app
            try:
                release = latest_release(target, cancel, self.library.data_dir / "release-cache")
            except RepositoryMoved as moved:
                # Add it under its new address, so the old name is never used again.
                target = custom_app(moved.new, app.name, app.include_prereleases, app.build_variant)
                if target.repo.casefold() in known:
                    raise ValueError(
                        f"{moved} It is already in your library under its new address."
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
                result.errors[app.id] = f"Icon lookup unavailable: {error}"
            return result

        self.task = Task(operation)
        self.task.signals.progress.connect(self.task_progress)
        self.task.signals.finished.connect(self.inspection_finished)
        self.task.signals.failed.connect(self.inspection_failed)
        self.set_busy(True)
        self.status.setText("Checking eligible releases and the published checksum…")
        QThreadPool.globalInstance().start(self.task)

    def set_busy(self, busy: bool) -> None:
        blocked = busy or self.launcher.task is not None
        self.busy_bar.setVisible(busy)
        self.build_combo.setEnabled(not blocked)
        self.release_channel.setEnabled(not busy)
        self.url.setEnabled(not busy)
        self.name.setEnabled(not busy)
        self.inspect_button.setEnabled(not blocked)
        self.inspect_button.setText("Inspecting…" if busy else "Inspect repository")
        self.add_button.setEnabled(not blocked and self.release is not None)
        self.release_preview.setVisible(self.release is not None)
        self.icon_group.setVisible(self.icon_target is not None and self.release is not None)
        self.choose_icon_button.setEnabled(not blocked and self.icon_target is not None)
        self.icon_choices.setEnabled(not blocked and self.icon_target is not None)

    def inspection_finished(self, result: TaskResult) -> None:
        self.task = None
        if self.closing:
            self.closing = False
            self.invalidate_preview()
            self.set_busy(False)
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
            self.status.setText(
                "Your saved build is unavailable. Choose a replacement to continue."
                if error.missing
                else "Multiple builds are available. Choose a build variant to continue."
            )
            return
        self.release = result.releases.get(target.id) if target else None
        if self.release is None:
            self.set_busy(False)
            self.status.setText(result.message)
            return
        if target is not None:
            remember_release(target, self.release, self.library.data_dir / "release-cache")
        self.version_label.set_version(
            f"v{self.release.version} · Linux {architecture()}",
            self.release.prerelease,
            self.release.is_latest,
        )
        self.filename.setText(self.release.name)
        self.checksum.setText("Published SHA-256 available; verified when downloaded")
        if not self.icon_options:
            self.set_icon_options(result.icons)
        self.status.setText(
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
        if self.task is not None and message and not self.closing:
            self.status.setText(message)

    def add_repository(self) -> None:
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
                    warning = f" Icon could not be saved: {error}"
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
            return
        self.add_button.setEnabled(False)
        self.release = None
        self.candidate = None
        self.added.emit(app.id, warning)

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

    def set_icon_options(self, icons: tuple[IconCandidate, ...]) -> None:
        self.icon_options = icons
        self.icon_choices.blockSignals(True)
        self.icon_choices.clear()
        for icon in icons:
            self.icon_choices.addItem(Path(icon.name).name)
            self.icon_choices.setItemData(
                self.icon_choices.count() - 1, icon.name, Qt.ItemDataRole.ToolTipRole
            )
        self.icon_choices.addItem("AppGlider default")
        self.icon_choices.blockSignals(False)
        self.selected_icon = icons[0].png if icons else None
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
            self, "Choose application icon", "", "Application icons (*.png *.svg)"
        )
        if not path:
            return
        try:
            with Path(path).open("rb") as source:
                png = normalise_icon(source.read(MAX_ICON_BYTES + 1))
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
            return
        self.set_icon_options((IconCandidate(Path(path).name, png),))
        self.select_icon(0)
