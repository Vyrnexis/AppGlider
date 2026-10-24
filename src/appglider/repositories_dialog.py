"""Inspect and save custom GitHub repositories without blocking the desktop UI."""

from pathlib import Path
from threading import Event

from PySide6.QtCore import QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QStackedWidget,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from appglider import APP_ICON
from appglider.catalog import App, custom_app
from appglider.catalogfile import EXAMPLE_CATALOG, CatalogError, ParsedCatalog, read, render
from appglider.icons import (
    MAX_ICON_BYTES,
    IconCandidate,
    discover_icons,
    icon_pixmap,
    normalise_icon,
)
from appglider.jobs import Task, TaskResult
from appglider.library import ImportPlan, Library
from appglider.release_info import remember_release
from appglider.releases import (
    Progress,
    Release,
    architecture,
    latest_release,
    variant_label,
)
from appglider.storage import atomic_bytes
from appglider.version_label import VersionLabel


class CatalogPreviewDialog(QDialog):
    """Show what importing a catalog would change, as plain text, before anything is saved."""

    def __init__(self, catalog: ParsedCatalog, plan: ImportPlan, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import catalog")
        self.resize(640, 480)
        layout = QVBoxLayout(self)
        title = catalog.name or "Untitled catalog"
        summary = QLabel(
            f"{title}: {len(plan.new)} to add, {len(plan.existing)} already in your library"
            + (f", {len(catalog.skipped)} skipped." if catalog.skipped else ".")
        )
        summary.setTextFormat(Qt.TextFormat.PlainText)
        summary.setWordWrap(True)
        layout.addWidget(summary)
        lines: list[str] = []
        for entry in plan.new:
            channel = "stable releases only" if entry.stable_only else "includes previews"
            lines.append(f"ADD  {entry.name}  ({entry.repo}, {channel})")
            if entry.description:
                lines.append(f"       {entry.description}")
            if entry.build:
                lines.append(f"       build: {entry.build}")
        if plan.existing:
            lines += ["", "ALREADY IN YOUR LIBRARY (left unchanged)"]
            lines += [f"  {entry.name}  ({entry.repo})" for entry in plan.existing]
        if catalog.skipped:
            lines += ["", "SKIPPED (not valid)"] + [f"  {problem}" for problem in catalog.skipped]
        if catalog.ignored:
            lines += ["", "IGNORED KEYS (not used by this version)"]
            lines += [f"  {key}" for key in catalog.ignored]
        self.details = QPlainTextEdit("\n".join(lines))
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Catalog contents")
        layout.addWidget(self.details, stretch=1)
        note = QLabel("Nothing is installed. Apps are added to your library only.")
        note.setObjectName("muted")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.add_button = buttons.addButton(
            f"Add {len(plan.new)} {'app' if len(plan.new) == 1 else 'apps'}",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self.add_button.setObjectName("primary")
        self.add_button.setEnabled(bool(plan.new))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class ChannelChoice(QWidget):
    """Two radio buttons choosing stable-only or stable-and-preview releases."""

    currentIndexChanged = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.stable = QRadioButton("Stable releases only")
        self.preview = QRadioButton("Stable and prerelease versions")
        self.stable.setChecked(True)
        layout.addWidget(self.stable)
        layout.addWidget(self.preview)
        self.preview.toggled.connect(lambda checked: self.currentIndexChanged.emit(int(checked)))

    def currentIndex(self) -> int:
        return int(self.preview.isChecked())

    def setCurrentIndex(self, index: int) -> None:
        (self.preview if index else self.stable).setChecked(True)


class RepositoriesPanel(QWidget):
    """The Applications page of Settings: the list of apps and the add-an-application flow."""

    catalog_changed = Signal()
    stopped = Signal()
    app_requested = Signal(str)

    def __init__(self, library: Library, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.library = library
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
        self.setObjectName("repositoriesPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, stretch=1)

        # Page 0: the list of applications.
        list_page = QWidget()
        list_layout = QVBoxLayout(list_page)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(12)
        toolbar = QHBoxLayout()
        heading = QVBoxLayout()
        title = QLabel("Your applications")
        title.setObjectName("sectionTitle")
        heading.addWidget(title)
        self.list_hint = QLabel("Choose an app to open its page.")
        self.list_hint.setObjectName("muted")
        heading.addWidget(self.list_hint)
        toolbar.addLayout(heading, stretch=1)
        self.catalog_button = QToolButton()
        self.catalog_button.setText("Catalog ▾")
        self.catalog_button.setToolTip("Import or export a plain-text TOML list of apps.")
        self.catalog_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        catalog_menu = QMenu(self.catalog_button)
        self.import_button = catalog_menu.addAction("Import catalog…")
        self.import_button.setToolTip("Add apps from a plain-text TOML catalog file.")
        self.import_button.triggered.connect(self.import_catalog)
        self.example_button = catalog_menu.addAction("Example catalog…")
        self.example_button.setToolTip(
            "Add the ArtCraft apps and SolidCraft, a ready-made example list."
        )
        self.example_button.triggered.connect(self.import_example_catalog)
        self.export_button = catalog_menu.addAction("Export catalog…")
        self.export_button.setToolTip("Save your repositories as a plain-text TOML file.")
        self.export_button.triggered.connect(self.export_catalog)
        self.catalog_button.setMenu(catalog_menu)
        toolbar.addWidget(self.catalog_button)
        self.new_repository_button = QPushButton("+ Add repository")
        self.new_repository_button.setObjectName("primary")
        self.new_repository_button.clicked.connect(self.start_add_repository)
        toolbar.addWidget(self.new_repository_button)
        list_layout.addLayout(toolbar)
        self.repo_list = QListWidget()
        self.repo_list.setObjectName("repositories")
        self.repo_list.setAccessibleName("Repositories")
        self.repo_list.setIconSize(QSize(40, 40))
        self.repo_list.itemClicked.connect(self.open_item)
        self.repo_list.itemActivated.connect(self.open_item)
        list_layout.addWidget(self.repo_list, stretch=1)
        self.list_empty = QLabel(
            "No applications yet. Add a repository, or import the example catalog."
        )
        self.list_empty.setObjectName("emptyState")
        self.list_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.list_empty.setWordWrap(True)
        list_layout.addWidget(self.list_empty, stretch=1)
        self.pages.addWidget(list_page)

        # Page 1: the add-an-application flow.
        detail_page = QWidget()
        detail_layout = QVBoxLayout(detail_page)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(12)
        self.back_button = QPushButton("‹ Applications")
        self.back_button.setObjectName("backLink")
        self.back_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_button.setAutoDefault(False)
        self.back_button.clicked.connect(self.show_list)
        detail_layout.addWidget(self.back_button, alignment=Qt.AlignmentFlag.AlignLeft)
        editor_widget = QWidget()
        editor = QVBoxLayout(editor_widget)
        editor.setContentsMargins(0, 0, 8, 0)
        editor.setSpacing(12)
        editor.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.editor_scroll = QScrollArea()
        self.editor_scroll.setWidgetResizable(True)
        self.editor_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.editor_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.editor_scroll.setWidget(editor_widget)
        self.add_page = QWidget()
        add_layout = QVBoxLayout(self.add_page)
        add_layout.setContentsMargins(0, 0, 0, 0)
        add_layout.setSpacing(8)
        add_title = QLabel("Add an application")
        add_title.setObjectName("sectionTitle")
        add_layout.addWidget(add_title)
        add_note = QLabel("Paste a public GitHub repository to find its latest AppImage.")
        add_note.setObjectName("muted")
        add_note.setWordWrap(True)
        add_layout.addWidget(add_note)
        form = QFormLayout()
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://github.com/owner/application")
        self.url.setAccessibleName("GitHub repository")
        self.name = QLineEdit()
        self.name.setPlaceholderText("Optional display name")
        self.name.setAccessibleName("Application name")
        self.url.textChanged.connect(lambda _: self.invalidate_preview())
        self.name.textChanged.connect(lambda _: self.invalidate_preview())
        self.url.returnPressed.connect(self.inspect_repository)
        form.addRow("Repository", self.url)
        form.addRow("Name", self.name)
        add_layout.addLayout(form)
        detail_layout.addWidget(self.add_page)
        detail_layout.addWidget(self.editor_scroll, stretch=1)
        release_options = QGroupBox("Updates")
        options_layout = QVBoxLayout(release_options)
        self.release_channel = ChannelChoice()
        self.release_channel.setAccessibleName("Release preference")
        self.release_channel.currentIndexChanged.connect(self.change_release_preference)
        options_layout.addWidget(self.release_channel)
        release_note = QLabel(
            "Previews may be less reliable. Stable releases stay eligible either way."
        )
        release_note.setWordWrap(True)
        release_note.setObjectName("muted")
        options_layout.addWidget(release_note)
        editor.addWidget(release_options)
        self.inspect_button = QPushButton("Inspect repository")
        self.inspect_button.setObjectName("primary")
        self.inspect_button.clicked.connect(self.inspect_repository)
        self.inspect_button.setAutoDefault(False)
        editor.addWidget(self.inspect_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.build_group = QGroupBox("Build variant")
        build_layout = QVBoxLayout(self.build_group)
        self.build_combo = QComboBox()
        self.build_combo.setAccessibleName("AppImage build variant")
        self.build_combo.setItemDelegate(QStyledItemDelegate(self.build_combo))
        self.build_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.build_combo.setMinimumContentsLength(20)
        self.build_combo.currentIndexChanged.connect(self.change_build_variant)
        build_layout.addWidget(self.build_combo)
        build_note = QLabel(
            "Choose the build suitable for your system. This choice is remembered for updates."
        )
        build_note.setWordWrap(True)
        build_note.setObjectName("muted")
        build_layout.addWidget(build_note)
        editor.addWidget(self.build_group)
        self.build_group.hide()
        self.release_preview = QGroupBox("Available release")
        preview = QFormLayout(self.release_preview)
        self.version_label = VersionLabel()
        self.version = self.version_label.text
        self.filename = QLabel("—")
        self.filename.setTextFormat(Qt.TextFormat.PlainText)
        self.filename.setWordWrap(True)
        self.filename.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.checksum = QLabel("Not checked")
        self.checksum.setWordWrap(True)
        self.checksum.setObjectName("muted")
        preview.addRow("Version", self.version_label)
        preview.addRow("Download", self.filename)
        preview.addRow("Verification", self.checksum)
        editor.addWidget(self.release_preview)
        self.icon_group = QGroupBox("Application icon")
        icon_row = QHBoxLayout(self.icon_group)
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
        editor.addWidget(self.icon_group)
        editor.addStretch()
        actions = QHBoxLayout()
        actions.addStretch()
        self.add_button = QPushButton("Add to library")
        self.add_button.setObjectName("primary")
        self.add_button.clicked.connect(self.add_repository)
        actions.addWidget(self.add_button)
        detail_layout.addLayout(actions)
        self.pages.addWidget(detail_page)

        strip = QFrame()
        strip.setObjectName("statusStrip")
        strip_layout = QVBoxLayout(strip)
        strip_layout.setContentsMargins(12, 8, 12, 8)
        strip_layout.setSpacing(6)
        self.status = QLabel("Repository, release and icon changes save immediately.")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(self.status.fontMetrics().lineSpacing() * 2)
        self.status.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        strip_layout.addWidget(self.status)
        self.busy_bar = QProgressBar()
        retain = self.busy_bar.sizePolicy()
        retain.setRetainSizeWhenHidden(True)
        self.busy_bar.setSizePolicy(retain)
        self.busy_bar.setRange(0, 0)  # moving, because the time needed is not known
        self.busy_bar.setFixedHeight(6)
        self.busy_bar.setTextVisible(False)
        self.busy_bar.setAccessibleName("Search in progress")
        self.busy_bar.hide()
        strip_layout.addWidget(self.busy_bar)
        layout.addWidget(strip)
        self.refresh_list()
        self.set_busy(False)

    def refresh_list(self) -> None:
        apps = self.library.apps
        self.repo_list.blockSignals(True)
        self.repo_list.clear()
        for app in sorted(apps, key=lambda app: app.name.casefold()):
            details = [app.repo, "Previews" if app.include_prereleases else "Stable"]
            if app.build_variant:
                details.append(variant_label(app.build_variant))
            item = QListWidgetItem(f"{app.name}\n{'  ·  '.join(details)}")
            item.setToolTip(app.source_url)
            item.setIcon(QIcon(icon_pixmap(self.library.icon_path(app), 40)))
            item.setData(Qt.ItemDataRole.UserRole, app.id)
            self.repo_list.addItem(item)
        self.repo_list.blockSignals(False)
        self.repo_list.setVisible(bool(apps))
        self.list_empty.setVisible(not apps)

    def open_item(self, item: QListWidgetItem) -> None:
        if self.task is None:
            self.app_requested.emit(str(item.data(Qt.ItemDataRole.UserRole)))

    def show_list(self) -> None:
        if self.task is not None:
            return
        self.adding = False
        self.invalidate_preview()
        self.refresh_list()
        self.pages.setCurrentIndex(0)
        self.status.setText("Repository, release and icon changes save immediately.")
        self.set_busy(False)

    def start_add_repository(self) -> None:
        if self.task is not None:
            return
        self.adding = True
        self.release_channel.blockSignals(True)
        self.release_channel.setCurrentIndex(0)
        self.release_channel.blockSignals(False)
        self.pages.setCurrentIndex(1)
        self.url.clear()
        self.name.clear()
        self.invalidate_preview()
        self.url.setFocus()
        self.set_busy(False)

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
        self.status.setText("Inspect this repository before adding it.")
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
        QTimer.singleShot(0, self, lambda: self.editor_scroll.ensureWidgetVisible(self.build_group))

    def change_build_variant(self) -> None:
        if self.task is not None or not self.build_combo.currentData():
            return
        self.build_override = str(self.build_combo.currentData())
        self.inspect_repository()

    def inspect_repository(self) -> None:
        if self.task is not None or not self.adding:
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

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            release = latest_release(app, cancel, self.library.data_dir / "release-cache")
            result = TaskResult("Release inspected.", {app.id: release})
            try:
                if lookup_icons:
                    result.icons = discover_icons(app, cancel, lambda text: progress(text, 0, 0))
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
        self.busy_bar.setVisible(busy)
        self.build_combo.setEnabled(not busy)
        self.release_channel.setEnabled(not busy)
        self.repo_list.setEnabled(not busy)
        self.url.setEnabled(not busy)
        self.name.setEnabled(not busy)
        self.inspect_button.setVisible(self.adding)
        self.inspect_button.setEnabled(not busy)
        self.inspect_button.setText("Inspecting…" if busy else "Inspect repository")
        self.new_repository_button.setEnabled(not busy and not self.adding)
        self.catalog_button.setEnabled(not busy)
        self.back_button.setEnabled(not busy)
        self.import_button.setEnabled(not busy and not self.adding)
        self.example_button.setEnabled(not busy and not self.adding)
        self.export_button.setEnabled(not busy)
        self.add_button.setVisible(self.adding)
        self.add_button.setEnabled(not busy and self.release is not None)
        self.release_preview.setVisible(self.release is not None)
        self.icon_group.setVisible(
            self.icon_target is not None and (not self.adding or self.release is not None)
        )
        self.choose_icon_button.setEnabled(not busy and self.icon_target is not None)
        self.icon_choices.setEnabled(not busy and self.icon_target is not None)

    def inspection_finished(self, result: TaskResult) -> None:
        self.task = None
        self.set_busy(False)
        if self.closing:
            self.stopped.emit()
            return
        target = self.inspection_app
        if result.build_choices and target:
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
        )
        self.set_busy(False)

        QTimer.singleShot(
            0, self, lambda: self.editor_scroll.ensureWidgetVisible(self.release_preview)
        )

    def inspection_failed(self, error: str) -> None:
        self.inspection_finished(TaskResult(error))

    def add_repository(self) -> None:
        if self.task is not None or self.candidate is None or self.release is None:
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
            self.refresh_list()
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
            return
        self.add_button.setEnabled(False)
        self.release = None
        self.candidate = None
        self.status.setText(f"Added {app.name}. Close Settings to install it.{warning}")
        self.catalog_changed.emit()

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

    def import_catalog(self) -> None:
        if self.task is not None or self.adding:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Import catalog", "", "AppGlider catalogs (*.toml);;All files (*)"
        )
        if path:
            self.import_from(Path(path))

    def import_example_catalog(self) -> None:
        if self.task is None and not self.adding:
            self.import_from(EXAMPLE_CATALOG)

    def import_from(self, source: Path) -> None:
        try:
            catalog = read(source)
            plan = self.library.plan_import(catalog.entries)
        except (CatalogError, OSError, ValueError) as error:
            self.status.setText(f"Cannot import {source.name}: {error}")
            return
        preview = CatalogPreviewDialog(catalog, plan, self)
        accepted = preview.exec() == QDialog.DialogCode.Accepted
        preview.deleteLater()
        if not accepted:
            return
        try:
            added = self.library.import_catalog(plan.new)
        except (OSError, ValueError) as error:
            self.status.setText(f"Nothing was imported: {error}")
            return
        self.refresh_list()
        self.catalog_changed.emit()
        count = len(added.new)
        self.status.setText(
            f"Added {count} {'app' if count == 1 else 'apps'} from {source.name}. "
            "Open an app to choose its icon."
        )

    def export_catalog(self) -> None:
        if self.task is not None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export catalog", "appglider-catalog.toml", "AppGlider catalogs (*.toml)"
        )
        if not path:
            return
        try:
            entries = self.library.export_entries()
            atomic_bytes(Path(path), render(entries, "My AppGlider catalog").encode("utf-8"))
        except (OSError, ValueError) as error:
            self.status.setText(f"Cannot export the catalog: {error}")
            return
        self.status.setText(f"Exported {len(entries)} apps to {Path(path).name}.")

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

    def task_progress(self, message: str, received: object, total: object) -> None:
        if self.task is not None and message and not self.closing:
            self.status.setText(message)

    def stop(self) -> bool:
        """Cancel any running search; return True when nothing is left running."""
        if self.task is None:
            return True
        self.closing = True
        self.task.cancel.set()
        self.status.setText("Stopping… one moment.")
        return False
