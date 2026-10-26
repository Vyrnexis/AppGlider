"""The Settings page: appearance, updates, catalogs, storage, GitHub status and About.

Every control saves as it changes; there is no Save or Cancel.
"""

import shutil
import stat
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSettings, QSignalBlocker, QStandardPaths, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QShowEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from appglider import APP_NAME, APP_VERSION, self_update
from appglider.app_page import SettingRow, add_setting_rows, format_size, panel
from appglider.catalog_preview import CatalogPreviewDialog
from appglider.catalogfile import EXAMPLE_CATALOG, CatalogError, read, render
from appglider.controls import Segmented, SpaceBar, SpaceSwatch, Switch
from appglider.library import previous_copy
from appglider.preferences import (
    ACCENTS,
    CHECK_INTERVALS,
    THEME_CHOICES,
    appearance_choice,
    check_interval_hours,
)
from appglider.releases import github_request_status
from appglider.rows import ElidedLabel
from appglider.storage import atomic_bytes

if TYPE_CHECKING:
    from appglider.window import LauncherWindow

SECTIONS = ("General", "Updates", "Catalogs", "Storage", "GitHub", "About")
LINKS = (
    ("View AppGlider on GitHub", "https://github.com/Vyrnexis/AppGlider"),
    ("Report an issue", "https://github.com/Vyrnexis/AppGlider/issues"),
)


def default_folder() -> str:
    data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)
    return str(Path(data) / "appglider" / "apps")


def checked_folder(text: str) -> tuple[Path | None, str]:
    """Validate a folder entry: the expanded path, or None with the reason it cannot be used."""
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        return None, "The path contains control characters."
    folder = Path(text.strip()).expanduser()
    if not text.strip() or not folder.is_absolute():
        return None, "Enter an absolute path or use Browse."
    if folder.exists() and not folder.is_dir():
        return None, "The selected path is a file."
    return folder, ""


def format_space(size: int) -> str:
    """Format byte counts: 0 MB for zero, MB under 1 GB, decimal GB for 1 GB and over."""
    if size <= 0:
        return "0 MB"
    if size >= 1_000_000_000:
        return f"{size / 1_000_000_000:.1f} GB"
    return format_size(size)


def drive_free_space(folder: Path) -> int | None:
    """Free bytes on the folder's drive, or the nearest existing parent."""
    current = folder.resolve()
    while True:
        try:
            if current.exists():
                return shutil.disk_usage(current).free
        except OSError:
            pass
        if current.parent == current:
            return None
        current = current.parent


class SettingsPage(QWidget):
    """Reads and writes the window's QSettings and calls the window's handlers."""

    def __init__(self, launcher: "LauncherWindow") -> None:
        super().__init__()
        self.launcher = launcher
        self.settings: QSettings = launcher.settings
        self.setObjectName("settingsPage")
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 12)
        root.setSpacing(14)
        header = QHBoxLayout()
        title = QLabel("Settings")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        self.saved_note = QLabel("✓ Changes save automatically")
        self.saved_note.setObjectName("savedNote")
        header.addWidget(self.saved_note)
        root.addLayout(header)
        body = QHBoxLayout()
        body.setSpacing(16)
        root.addLayout(body, stretch=1)
        nav = QWidget()
        nav.setFixedWidth(140)
        nav.setAccessibleName("Settings sections")
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(2)
        self.section_group = QButtonGroup(self)
        self.section_group.setExclusive(True)
        self.sections: list[QPushButton] = []
        for index, name in enumerate(SECTIONS):
            button = QPushButton(name)
            button.setObjectName("sectionNav")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.section_group.addButton(button, index)
            nav_layout.addWidget(button)
            self.sections.append(button)
        nav_layout.addStretch()
        body.addWidget(nav)
        self.stack = QStackedWidget()
        self.body_scroll = QScrollArea()
        self.body_scroll.setWidgetResizable(True)
        self.body_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body_scroll.setWidget(self.stack)
        body.addWidget(self.body_scroll, stretch=1)
        builders = (
            self.build_general,
            self.build_updates,
            self.build_catalogs,
            self.build_storage,
            self.build_github,
            self.build_about,
        )
        for build in builders:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 0, 8, 0)
            layout.setSpacing(16)
            build(layout)
            layout.addStretch()
            self.stack.addWidget(page)
        self.sections[0].setChecked(True)
        self.section_group.idToggled.connect(self.section_toggled)
        self.update_state()

    # Construction

    def build_general(self, column: QVBoxLayout) -> None:
        theme, accent = appearance_choice(self.settings)
        frame, layout, _ = panel("Appearance")
        self.theme = Segmented(tuple(THEME_CHOICES))
        self.theme.setAccessibleName("Theme")
        self.theme.setCurrentIndex(THEME_CHOICES.index(theme))
        self.theme.currentIndexChanged.connect(self.appearance_changed)
        swatches = QWidget()
        swatch_layout = QHBoxLayout(swatches)
        swatch_layout.setContentsMargins(0, 0, 0, 0)
        swatch_layout.setSpacing(8)
        swatches.setAccessibleName("Accent colour")
        self.accent_group = QButtonGroup(self)
        self.accent_group.setExclusive(True)
        self.accents: dict[str, QPushButton] = {}
        for name in ACCENTS:
            swatch = QPushButton()
            swatch.setObjectName("swatch")
            swatch.setProperty("accent", name)
            swatch.setCheckable(True)
            swatch.setFixedSize(28, 28)
            swatch.setCursor(Qt.CursorShape.PointingHandCursor)
            swatch.setAccessibleName(f"{name} accent")
            swatch.setToolTip(name)
            swatch.setChecked(name == accent)
            self.accent_group.addButton(swatch)
            swatch_layout.addWidget(swatch)
            self.accents[name] = swatch
        self.accent_group.buttonToggled.connect(self.accent_toggled)
        add_setting_rows(
            layout,
            (
                SettingRow("Theme", "System follows your desktop", self.theme),
                SettingRow("Accent", "Used for buttons and highlights", swatches),
            ),
        )
        column.addWidget(frame)
        frame, layout, _ = panel("Window")
        self.close_to_tray = Switch()
        self.close_to_tray.setAccessibleName("Keep running in the tray")
        self.close_to_tray.setChecked(
            bool(self.settings.value("General/close-to-tray", True, type=bool))
        )
        self.close_to_tray.toggled.connect(
            lambda checked: self.settings.setValue("General/close-to-tray", checked)
        )
        row = SettingRow(
            "Keep running in the tray",
            "Closing the window leaves AppGlider checking in the background",
            self.close_to_tray,
        )
        row.setToolTip("Applies when a system tray is available. Use Quit to exit.")
        layout.addWidget(row)
        self.autostart = Switch()
        self.autostart.setAccessibleName("Start with my desktop")
        self.autostart.setChecked(self.launcher.library.autostart_enabled())
        self.autostart.toggled.connect(self.autostart_toggled)
        layout.addWidget(
            SettingRow("Start with my desktop", "Opens in the tray when you log in", self.autostart)
        )
        self.autostart_error = QLabel()
        self.autostart_error.setObjectName("settingError")
        self.autostart_error.setTextFormat(Qt.TextFormat.PlainText)
        self.autostart_error.setWordWrap(True)
        self.autostart_error.setAccessibleName("Start with my desktop problem")
        self.autostart_error.hide()
        layout.addWidget(self.autostart_error)
        column.addWidget(frame)

    def autostart_toggled(self, checked: bool) -> None:
        from appglider.app import launcher_command

        library = self.launcher.library
        try:
            if checked:
                library.enable_autostart(launcher_command())
            else:
                library.disable_autostart()
        except (OSError, ValueError) as error:
            self.autostart_error.setText(str(error))
            self.autostart_error.show()
            with QSignalBlocker(self.autostart):
                self.autostart.setChecked(not checked)
            return
        self.autostart_error.hide()

    def build_updates(self, column: QVBoxLayout) -> None:
        frame, layout, _ = panel("Updates")
        self.check_interval = QComboBox()
        self.check_interval.setItemDelegate(QStyledItemDelegate(self.check_interval))
        self.check_interval.setAccessibleName("Check for updates")
        for label, hours in CHECK_INTERVALS:
            self.check_interval.addItem(label, hours)
        self.check_interval.setCurrentIndex(
            max(0, self.check_interval.findData(check_interval_hours(self.settings)))
        )
        self.check_interval.currentIndexChanged.connect(self.interval_changed)
        self.notify_updates = Switch()
        self.notify_updates.setAccessibleName("Notify me about new updates")
        self.notify_updates.setChecked(
            bool(self.settings.value("notifications/updates", False, type=bool))
        )
        self.notify_updates.toggled.connect(
            lambda checked: self.settings.setValue("notifications/updates", checked)
        )
        notify_row = SettingRow(
            "Notify me about new updates", "Once per new version", self.notify_updates
        )
        notify_row.setToolTip(
            "Only for the scheduled checks, while AppGlider is in the background, and once "
            "for each new version."
        )
        add_setting_rows(
            layout,
            (
                SettingRow(
                    "Check for updates",
                    "Unchanged apps use none of GitHub's allowance",
                    self.check_interval,
                ),
                notify_row,
            ),
        )
        column.addWidget(frame)

    def build_catalogs(self, column: QVBoxLayout) -> None:
        frame, layout, _ = panel("Catalogs")
        self.import_button = QPushButton("Import catalog…")
        self.import_button.setToolTip("Add apps from a plain-text TOML catalog file.")
        self.import_button.clicked.connect(self.import_catalog)
        self.example_button = QPushButton("Example catalog")
        self.example_button.setToolTip(
            "Add the ArtCraft apps and SolidCraft, a ready-made example list."
        )
        self.example_button.clicked.connect(self.import_example_catalog)
        self.export_button = QPushButton("Export catalog…")
        self.export_button.setToolTip("Save your repositories as a plain-text TOML file.")
        self.export_button.clicked.connect(self.export_catalog)
        add_setting_rows(
            layout,
            (
                SettingRow(
                    "Import a catalog",
                    "A plain-text list of repositories; you see it before it is added",
                    self.import_button,
                ),
                SettingRow(
                    "Example catalog",
                    "The ArtCraft apps and SolidCraft",
                    self.example_button,
                ),
                SettingRow(
                    "Export your catalog",
                    "Save your repositories as a TOML file",
                    self.export_button,
                ),
            ),
        )
        column.addWidget(frame)

    def build_storage(self, column: QVBoxLayout) -> None:
        frame, layout, _ = panel("Storage")
        self.applied_folder = str(self.settings.value("installation/folder", default_folder()))
        self.folder = QLineEdit(self.applied_folder)
        self.folder.setAccessibleName("AppImage folder")
        self.folder.setCursorPosition(0)
        self.folder.editingFinished.connect(self.apply_folder)
        self.browse_button = QPushButton("Browse…")
        self.browse_button.clicked.connect(self.browse)
        field = QWidget()
        field_layout = QHBoxLayout(field)
        field_layout.setContentsMargins(0, 0, 0, 0)
        field_layout.setSpacing(8)
        field_layout.addWidget(self.folder, stretch=1)
        field_layout.addWidget(self.browse_button)
        field.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        row = SettingRow(
            "Install new applications in",
            "Existing applications stay in their current folders.",
            field,
            below=True,
        )
        layout.addWidget(row)
        self.folder_error = QLabel()
        self.folder_error.setObjectName("settingError")
        self.folder_error.setTextFormat(Qt.TextFormat.PlainText)
        self.folder_error.setWordWrap(True)
        self.folder_error.setAccessibleName("Folder problem")
        self.folder_error.hide()
        layout.addWidget(self.folder_error)
        column.addWidget(frame)

        space_frame, space_layout, _ = panel("Space used")
        self.space_panel = space_frame

        total_line = QWidget()
        total_layout = QHBoxLayout(total_line)
        total_layout.setContentsMargins(0, 0, 0, 0)
        total_layout.setSpacing(8)
        total_layout.setAlignment(Qt.AlignmentFlag.AlignBaseline)

        self.space_total_label = QLabel()
        self.space_total_label.setObjectName("spaceTotal")

        self.space_free_label = QLabel()
        self.space_free_label.setObjectName("muted")

        total_layout.addWidget(self.space_total_label)
        total_layout.addWidget(self.space_free_label)
        total_layout.addStretch()
        space_layout.addWidget(total_line)

        self.space_summary_bar = SpaceBar()
        self.space_summary_bar.setFixedHeight(12)
        space_layout.addWidget(self.space_summary_bar)

        self.space_legend = QWidget()
        legend_layout = QHBoxLayout(self.space_legend)
        legend_layout.setContentsMargins(0, 0, 0, 0)
        legend_layout.setSpacing(6)
        legend_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        swatch_in_use = SpaceSwatch(kind="in_use")
        in_use_label = QLabel("In use")
        self.space_legend_in_use_size = QLabel()
        font_in_use = self.space_legend_in_use_size.font()
        font_in_use.setBold(True)
        self.space_legend_in_use_size.setFont(font_in_use)

        swatch_kept = SpaceSwatch(kind="kept")
        kept_label = QLabel("Kept for roll back")
        self.space_legend_kept_size = QLabel()
        font_kept = self.space_legend_kept_size.font()
        font_kept.setBold(True)
        self.space_legend_kept_size.setFont(font_kept)

        legend_layout.addWidget(swatch_in_use)
        legend_layout.addWidget(in_use_label)
        legend_layout.addWidget(self.space_legend_in_use_size)
        legend_layout.addSpacing(16)
        legend_layout.addWidget(swatch_kept)
        legend_layout.addWidget(kept_label)
        legend_layout.addWidget(self.space_legend_kept_size)
        legend_layout.addStretch()
        space_layout.addWidget(self.space_legend)

        self.space_empty_label = QLabel("No apps installed yet")
        self.space_empty_label.setObjectName("muted")
        space_layout.addWidget(self.space_empty_label)

        self.space_list = QWidget()
        self.space_list_layout = QVBoxLayout(self.space_list)
        self.space_list_layout.setContentsMargins(0, 4, 0, 4)
        self.space_list_layout.setSpacing(8)
        space_layout.addWidget(self.space_list)

        hint = QLabel("Each app keeps one earlier version for roll back; updating replaces it.")
        hint.setObjectName("settingHint")
        hint.setWordWrap(True)
        space_layout.addWidget(hint)

        column.addWidget(space_frame)
        self.update_space_used()

    def update_space_used(self) -> None:
        """Refresh the space used panel with current stats from disk."""
        folder = Path(self.applied_folder)
        free_bytes = drive_free_space(folder)
        free_part = (
            f" · {format_space(free_bytes)} free on this drive" if free_bytes is not None else ""
        )

        app_names = {app.id: app.name for app in self.launcher.apps}
        apps_data: list[tuple[str, str, int, int, int]] = []
        for app_id, installed in self.launcher.installed.items():
            try:
                stat_result = installed.path.stat()
                if not stat.S_ISREG(stat_result.st_mode):
                    continue
                in_use = stat_result.st_size
            except OSError:
                continue
            prev = previous_copy(installed)
            kept = prev.size if prev is not None else 0
            total = in_use + kept
            name = app_names.get(app_id, app_id)
            apps_data.append((app_id, name, in_use, kept, total))

        if not apps_data:
            self.space_total_label.setText(format_space(0))
            self.space_free_label.setText(f"by 0 apps{free_part}")
            self.space_summary_bar.hide()
            self.space_legend.hide()
            self.space_list.hide()
            self.space_empty_label.show()
            self._clear_space_list()
            return

        self.space_empty_label.hide()
        self.space_summary_bar.show()
        self.space_legend.show()
        self.space_list.show()

        total_in_use = sum(item[2] for item in apps_data)
        total_kept = sum(item[3] for item in apps_data)
        total_size = total_in_use + total_kept
        count = len(apps_data)
        app_word = "app" if count == 1 else "apps"

        self.space_total_label.setText(format_space(total_size))
        self.space_free_label.setText(f"by {count} {app_word}{free_part}")

        self.space_summary_bar.set_values(total_in_use, total_kept, total_size)
        self.space_summary_bar.setAccessibleName(
            f"In use {format_space(total_in_use)}, kept for roll back {format_space(total_kept)}"
        )

        self.space_legend_in_use_size.setText(format_space(total_in_use))
        self.space_legend_kept_size.setText(format_space(total_kept))

        apps_data.sort(key=lambda item: (-item[4], item[1].lower()))
        max_total = apps_data[0][4]

        self._clear_space_list()
        for _app_id, name, in_use, kept, total in apps_data:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(12)

            name_label = ElidedLabel()
            name_label.set_full_text(name)
            name_label.setFixedWidth(160)
            # ElidedLabel ignores its width hint; without this the bar paints over the name.
            name_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

            bar = SpaceBar(in_use=in_use, kept=kept, scale=max_total)
            bar.setFixedHeight(8)
            if kept > 0:
                bar.setAccessibleName(
                    f"{name}: {format_space(in_use)}, {format_space(kept)} kept for roll back"
                )
            else:
                bar.setAccessibleName(f"{name}: {format_space(in_use)}")

            size_label = QLabel(format_space(total))
            size_label.setObjectName("muted")
            font = size_label.font()
            font.setFeature(QFont.Tag("tnum"), 1)
            size_label.setFont(font)
            size_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            size_label.setFixedWidth(72)

            row_layout.addWidget(name_label)
            row_layout.addWidget(bar, stretch=1)
            row_layout.addWidget(size_label)

            self.space_list_layout.addWidget(row)

    def _clear_space_list(self) -> None:
        while self.space_list_layout.count():
            item = self.space_list_layout.takeAt(0)
            if item is not None and (widget := item.widget()) is not None:
                widget.deleteLater()

    def build_github(self, column: QVBoxLayout) -> None:
        frame, layout, _ = panel("GitHub")
        self.github_details = QLabel()
        self.github_details.setTextFormat(Qt.TextFormat.PlainText)
        self.github_details.setWordWrap(True)
        self.github_details.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.github_details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.github_details.setAccessibleName("GitHub request status")
        layout.addWidget(self.github_details)
        note = QLabel(
            "Status updates from existing requests; opening Settings makes no requests. "
            "Other applications on your network may share the request allowance."
        )
        note.setObjectName("settingHint")
        note.setWordWrap(True)
        layout.addWidget(note)
        column.addWidget(frame)
        self.update_github_status()

    def build_about(self, column: QVBoxLayout) -> None:
        frame, layout, _ = panel("About")
        self.self_check_button = QPushButton("Check now")
        self.self_check_button.setAccessibleName("Check for a new AppGlider version")
        self.self_check_button.clicked.connect(
            lambda: self.launcher.start_self_check(explicit=True)
        )
        self.self_row = SettingRow(
            f"{APP_NAME} {APP_VERSION}", "Not checked yet", self.self_check_button
        )
        layout.addWidget(self.self_row)
        text = QLabel("A home for your Linux AppImages.\nWritten in Python with Qt.")
        text.setWordWrap(True)
        layout.addWidget(text)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        for caption, url in LINKS:
            button = QPushButton(caption)
            button.clicked.connect(
                lambda checked=False, url=url: QDesktopServices.openUrl(QUrl(url))
            )
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        column.addWidget(frame)
        self.update_self_update()

    def update_self_update(self) -> None:
        """Show what the last check of AppGlider itself found, and whether one is running."""
        checking = self.launcher.self_task is not None
        ready = self.launcher.self_ready
        self.self_row.hint.setText(
            self.launcher.self_state.hint(
                self_update.running_image(), ready.version if ready else ""
            )
        )
        self.self_check_button.setText("Checking…" if checking else "Check now")
        self.self_check_button.setEnabled(not checking)

    # Sections and state

    def show_section(self, name: str) -> None:
        if name not in SECTIONS:
            raise ValueError(f"Unknown settings section: {name}")
        self.sections[SECTIONS.index(name)].setChecked(True)
        if name == "Storage":
            self.update_space_used()

    def focus_section(self) -> None:
        """Put the keyboard on the chosen section, so focus does not sit on another one."""
        self.sections[self.stack.currentIndex()].setFocus()

    def section_toggled(self, index: int, checked: bool) -> None:
        if checked:
            self.stack.setCurrentIndex(index)
            self.body_scroll.verticalScrollBar().setValue(0)
            if SECTIONS[index] == "Storage":
                self.update_space_used()

    def current_section(self) -> str:
        return SECTIONS[self.stack.currentIndex()]

    def update_state(self) -> None:
        """Catalog changes and the folder wait for a running task; the rest stays usable."""
        idle = self.launcher.task is None
        for widget in (
            self.import_button,
            self.example_button,
            self.export_button,
            self.folder,
            self.browse_button,
        ):
            widget.setEnabled(idle)
        if self.isVisible() and self.current_section() == "Storage":
            self.update_space_used()

    def update_github_status(self) -> None:
        self.github_details.setText(github_request_status()[1])

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.update_github_status()
        enabled = self.launcher.library.autostart_enabled()  # the file is the setting
        if enabled != self.autostart.isChecked():
            with QSignalBlocker(self.autostart):
                self.autostart.setChecked(enabled)
        if self.current_section() == "Storage":
            self.update_space_used()

    # Appearance, window and updates

    def appearance_changed(self) -> None:
        accent = next((name for name, button in self.accents.items() if button.isChecked()), None)
        self.settings.setValue("appearance/theme", THEME_CHOICES[self.theme.currentIndex()])
        self.settings.setValue("appearance/accent", accent or next(iter(ACCENTS)))
        self.launcher.apply_theme()

    def accent_toggled(self, button: object, checked: bool) -> None:
        if checked:
            self.appearance_changed()

    def interval_changed(self) -> None:
        self.settings.setValue("General/check-interval-hours", self.check_interval.currentData())
        self.launcher.schedule_update_checks()

    # Catalogs

    def say(self, message: str) -> None:
        self.launcher.footer.setText(message)

    def import_catalog(self) -> None:
        if self.launcher.task is not None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Import catalog", "", "AppGlider catalogs (*.toml);;All files (*)"
        )
        if path:
            self.import_from(Path(path))

    def import_example_catalog(self) -> None:
        if self.launcher.task is None:
            self.import_from(EXAMPLE_CATALOG)

    def import_from(self, source: Path) -> None:
        library = self.launcher.library
        try:
            catalog = read(source)
            plan = library.plan_import(catalog.entries)
        except (CatalogError, OSError, ValueError) as error:
            self.say(f"Cannot import {source.name}: {error}")
            return
        preview = CatalogPreviewDialog(catalog, plan, self)
        accepted = preview.exec() == QDialog.DialogCode.Accepted
        preview.deleteLater()
        if not accepted:
            return
        try:
            added = library.import_catalog(plan.new)
        except (OSError, ValueError) as error:
            self.say(f"Nothing was imported: {error}")
            return
        self.launcher.catalog_changed()
        count = len(added.new)
        self.say(
            f"Added {count} {'app' if count == 1 else 'apps'} from {source.name}. "
            "Open an app to choose its icon."
        )

    def export_catalog(self) -> None:
        if self.launcher.task is not None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export catalog", "appglider-catalog.toml", "AppGlider catalogs (*.toml)"
        )
        if not path:
            return
        try:
            entries = self.launcher.library.export_entries()
            atomic_bytes(Path(path), render(entries, "My AppGlider catalog").encode("utf-8"))
        except (OSError, ValueError) as error:
            self.say(f"Cannot export the catalog: {error}")
            return
        self.say(f"Exported {len(entries)} apps to {Path(path).name}.")

    # Storage

    def browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose AppImage folder", self.folder.text()
        )
        if folder:
            self.folder.setText(folder)
            self.apply_folder()

    def apply_folder(self) -> bool:
        """Use the entered folder if it is valid; otherwise explain and keep the previous one."""
        if self.launcher.task is not None:
            return False
        folder, problem = checked_folder(self.folder.text())
        self.folder_error.setText(problem)
        self.folder_error.setVisible(bool(problem))
        if folder is None:
            return False
        text = str(folder)
        self.folder.setText(text)
        self.folder.setCursorPosition(0)
        if text != self.applied_folder:
            self.settings.setValue("installation/folder", text)
            self.launcher.library.apps_dir = folder
            self.applied_folder = text
            self.launcher.scan_installed()
            self.update_space_used()
        return True
