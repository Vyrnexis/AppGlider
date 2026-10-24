"""The Settings page: appearance, updates, catalogs, storage, GitHub status and About.

Every control saves as it changes; there is no Save or Cancel.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSettings, QStandardPaths, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QShowEvent
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

from appglider import APP_NAME, APP_VERSION
from appglider.app_page import SettingRow, add_setting_rows, panel
from appglider.catalog_preview import CatalogPreviewDialog
from appglider.catalogfile import EXAMPLE_CATALOG, CatalogError, read, render
from appglider.controls import Segmented, Switch
from appglider.preferences import (
    ACCENTS,
    CHECK_INTERVALS,
    THEME_CHOICES,
    appearance_choice,
    check_interval_hours,
)
from appglider.releases import github_request_status
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
            layout.setSpacing(14)
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
        column.addWidget(frame)

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
        title = QLabel(f"{APP_NAME} {APP_VERSION}")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
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

    # Sections and state

    def show_section(self, name: str) -> None:
        if name not in SECTIONS:
            raise ValueError(f"Unknown settings section: {name}")
        self.sections[SECTIONS.index(name)].setChecked(True)

    def focus_section(self) -> None:
        """Put the keyboard on the chosen section, so focus does not sit on another one."""
        self.sections[self.stack.currentIndex()].setFocus()

    def section_toggled(self, index: int, checked: bool) -> None:
        if checked:
            self.stack.setCurrentIndex(index)
            self.body_scroll.verticalScrollBar().setValue(0)

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

    def update_github_status(self) -> None:
        self.github_details.setText(github_request_status()[1])

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.update_github_status()

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
        return True
