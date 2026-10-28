"""The App page: one application's status, release notes, versions and preferences."""

from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from appglider.core.catalog import App
from appglider.github.assets import BuildChoiceRequired, Release, variant_label
from appglider.github.download import Progress, latest_release
from appglider.github.http import file_stamp
from appglider.github.icons import (
    MAX_ICON_BYTES,
    IconCandidate,
    discover_icons,
    icon_pixmap,
    normalise_icon,
)
from appglider.github.release_info import ReleaseInfo, ReleaseOption, remember_release
from appglider.github.versions import version_key
from appglider.install.launch_options import (
    LaunchOptions,
    format_arguments,
    format_environment,
    parse_arguments,
    parse_environment,
)
from appglider.install.library import PreviousCopy, previous_copy
from appglider.ui.app_status import app_status
from appglider.ui.controls import Segmented, Switch, repolish
from appglider.ui.jobs import TaskResult

if TYPE_CHECKING:
    from appglider.ui.window import LauncherWindow

NOTES_MAX_HEIGHT = 260
WEB_SCHEMES = ("http", "https")


def version_text(version: str | None) -> str:
    """Show a version with a leading v when it starts with a digit."""
    if not version:
        return "unknown"
    return f"v{version}" if version[:1].isdigit() else version


def format_size(size: int) -> str:
    if size >= 1_000_000_000:
        return f"{size / 1_000_000_000:.1f} GB"
    return f"{max(1, round(size / 1_000_000))} MB"


def format_date(day: str) -> str:
    """Turn an ISO date into 19 Oct 2026; an unreadable date is shown as it is."""
    try:
        value = date.fromisoformat(day)
    except ValueError:
        return day
    return f"{value.day:02d} {value:%b} {value.year}"


def release_page_url(release: Release) -> str:
    """The release's page on GitHub, keeping the tag exactly as it appears in the download URL."""
    base, _, rest = release.url.partition("/releases/download/")
    return f"{base}/releases/tag/{rest.split('/', 1)[0]}"


class NotesBrowser(QTextBrowser):
    """Release notes as Markdown: read-only, never loading anything and opening web links only."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("notes")
        self.setAccessibleName("Release notes")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setSearchPaths([])
        self.anchorClicked.connect(self.open_link)
        self.fitting = False
        self.setFixedHeight(NOTES_MAX_HEIGHT)

    def loadResource(self, type: int, name: QUrl | str) -> Any:  # noqa: A002, N802
        """Notes come from the network: never fetch images or read local files for them."""
        return None

    def open_link(self, url: QUrl) -> None:
        if url.scheme().lower() in WEB_SCHEMES and url.host():
            QDesktopServices.openUrl(url)

    def set_notes(self, text: str) -> None:
        self.setMarkdown(text)
        self.fit()

    def fit(self) -> None:
        """Be as tall as the notes, up to a limit, then scroll."""
        if self.fitting:
            return
        self.fitting = True
        try:
            document = self.document()
            # The whole width: short notes need no scroll bar, which would take some of it.
            document.setTextWidth(max(1, self.width()))
            height = int(document.size().height()) + 6
            policy = (
                Qt.ScrollBarPolicy.ScrollBarAsNeeded
                if height > NOTES_MAX_HEIGHT
                else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            if self.verticalScrollBarPolicy() != policy:
                self.setVerticalScrollBarPolicy(policy)
            height = max(24, min(height, NOTES_MAX_HEIGHT))
            if self.height() != height:
                self.setFixedHeight(height)
        finally:
            self.fitting = False

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.fit()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.fit()


def panel(title: str, name: str = "panel") -> tuple[QFrame, QVBoxLayout, QLabel]:
    frame = QFrame()
    frame.setObjectName(name)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 14, 14, 14)
    layout.setSpacing(8)
    heading = QLabel(title.upper())
    heading.setObjectName("dangerTitle" if name == "dangerPanel" else "panelTitle")
    font = heading.font()
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
    heading.setFont(font)
    heading.setAccessibleName(title)
    layout.addWidget(heading)
    return frame, layout, heading


def link_button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("linkButton")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFlat(True)
    return button


class VersionValue(QWidget):
    """A version (or a short sentence about one) with its release badge, wrapping when needed."""

    def __init__(self) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.text = QLabel()
        self.text.setObjectName("versionValue")
        self.text.setTextFormat(Qt.TextFormat.PlainText)
        self.text.setWordWrap(True)
        self.text.setMinimumWidth(0)
        self.badge = QLabel()
        self.badge.setObjectName("releaseBadge")
        self.badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.text, stretch=1)
        layout.addWidget(self.badge, alignment=Qt.AlignmentFlag.AlignTop)

    def set_version(self, text: str, prerelease: bool = False, is_latest: bool = False) -> None:
        self.text.setText(text)
        label = "Pre-release" if prerelease else "Latest" if is_latest else ""
        self.badge.setText(label)
        self.badge.setVisible(bool(label))
        state = "prerelease" if prerelease else "latest"
        if self.badge.property("release") != state:
            self.badge.setProperty("release", state)
            repolish(self.badge)
        self.badge.setToolTip(
            "A preview version, which may be less reliable."
            if prerelease
            else "GitHub's designated latest stable release."
        )


class SettingRow(QWidget):
    """A bold label, a one-line hint and the control on the right."""

    def __init__(self, label: str, hint: str, control: QWidget, *, below: bool = False) -> None:
        super().__init__()
        layout = QVBoxLayout(self) if below else QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(8 if below else 12)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.label = QLabel(label)
        self.label.setObjectName("settingLabel")
        self.hint = QLabel(hint)
        self.hint.setObjectName("settingHint")
        self.hint.setWordWrap(True)
        self.hint.setTextFormat(Qt.TextFormat.PlainText)
        text.addWidget(self.label)
        text.addWidget(self.hint)
        layout.addLayout(text, stretch=0 if below else 1)
        self.control = control
        if below:
            layout.addWidget(control)
        else:
            layout.addWidget(control, alignment=Qt.AlignmentFlag.AlignVCenter)


def add_setting_rows(layout: QVBoxLayout, rows: Iterable[QWidget]) -> None:
    """Stack setting rows in a panel with a divider between neighbours."""
    for index, row in enumerate(rows):
        if index:
            divider = QFrame()
            divider.setObjectName("rowDivider")
            divider.setFixedHeight(1)
            layout.addWidget(divider)
        layout.addWidget(row)


class AppPage(QScrollArea):
    """Everything about one app. It reads the window's state and calls the window's handlers."""

    back_requested = Signal()

    def __init__(self, launcher: "LauncherWindow") -> None:
        super().__init__()
        self.launcher = launcher
        self.app_id: str | None = None
        self.pending = ""
        self.pending_app: str | None = None
        self.icon_key: tuple[Path, object] | None = None
        self.build_result: dict[str, Any] = {}
        self.build_target: App | None = None
        self.mode = "install"
        self.message = ""
        self.release_url = ""
        self.shown_notes: str | None = None
        self.options_app: str | None = None
        self.setObjectName("appPage")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self.setWidget(content)
        content.installEventFilter(self)
        self.root_layout = root = QVBoxLayout(content)
        root.setContentsMargins(22, 16, 22, 20)
        root.setSpacing(16)
        self.back_button = QPushButton("‹ Library")
        self.back_button.setObjectName("backLink")
        self.back_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_button.setAutoDefault(False)
        self.back_button.clicked.connect(self.back_requested)
        root.addWidget(self.back_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.build_header(root)
        # Two columns, stacked into one when a wide font leaves too little room for both.
        self.columns = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.columns.setSpacing(14)
        self.column_layouts = left, right = QVBoxLayout(), QVBoxLayout()
        left.setSpacing(16)
        right.setSpacing(16)
        self.columns.addLayout(left, stretch=13)
        self.columns.addLayout(right, stretch=10)
        root.addLayout(self.columns)
        root.addStretch()
        self.build_notes(left)
        self.build_versions(left)
        left.addStretch()
        self.build_preferences(right)
        self.build_manage(right)
        right.addStretch()

    def fit_columns(self) -> None:
        left, right = self.column_layouts
        margins = self.root_layout.contentsMargins()
        needed = (
            left.minimumSize().width()
            + right.minimumSize().width()
            + self.columns.spacing()
            + margins.left()
            + margins.right()
        )
        wide = self.viewport().width() >= needed
        direction = QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom
        if self.columns.direction() != direction:
            self.columns.setDirection(direction)
            self.columns.setStretch(0, 13 if wide else 0)
            self.columns.setStretch(1, 10 if wide else 0)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.fit_columns()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        # Minimum widths settle only after styling and new text, so check again then.
        if watched is self.widget() and event.type() == QEvent.Type.LayoutRequest:
            self.fit_columns()
        return super().eventFilter(watched, event)

    # Construction

    def build_header(self, root: QVBoxLayout) -> None:
        header = QHBoxLayout()
        header.setSpacing(16)
        self.icon = QLabel()
        self.icon.setFixedSize(72, 72)
        header.addWidget(self.icon, alignment=Qt.AlignmentFlag.AlignTop)
        identity = QVBoxLayout()
        identity.setSpacing(4)
        self.title = QLabel()
        self.title.setObjectName("appTitle")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setWordWrap(True)
        self.source = QLabel()
        self.source.setObjectName("muted")
        self.source.setTextFormat(Qt.TextFormat.PlainText)
        self.source.setWordWrap(True)
        chips = QHBoxLayout()
        chips.setSpacing(8)
        self.chip = QLabel()
        self.channel_chip = QLabel()
        for chip in (self.chip, self.channel_chip):
            chip.setObjectName("chip")
            chip.setProperty("state", "none")
            chip.setFixedHeight(22)
            chips.addWidget(chip)
        chips.addStretch()
        identity.addWidget(self.title)
        identity.addWidget(self.source)
        identity.addSpacing(2)
        identity.addLayout(chips)
        header.addLayout(identity, stretch=1)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.launch_button = QPushButton("Launch")
        self.launch_button.clicked.connect(self.launch)
        self.action_button = QPushButton()
        self.action_button.setObjectName("primary")
        self.action_button.clicked.connect(self.activate)
        for button in (self.launch_button, self.action_button):
            button.setMinimumHeight(36)
            buttons.addWidget(button)
        header.addLayout(buttons)
        root.addLayout(header)

    def build_notes(self, column: QVBoxLayout) -> None:
        frame, layout, self.notes_heading = panel("What's new")
        self.notes_panel = frame
        release_line = QHBoxLayout()
        release_line.setSpacing(8)
        self.release_title = QLabel()
        self.release_title.setObjectName("releaseTitle")
        self.release_title.setTextFormat(Qt.TextFormat.PlainText)
        self.release_badge = QLabel()
        self.release_badge.setObjectName("releaseBadge")
        self.release_badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        release_line.addWidget(self.release_title)
        release_line.addWidget(self.release_badge)
        release_line.addStretch()
        layout.addLayout(release_line)
        self.release_meta = QLabel()
        self.release_meta.setObjectName("muted")
        self.release_meta.setTextFormat(Qt.TextFormat.PlainText)
        self.release_meta.setWordWrap(True)
        layout.addWidget(self.release_meta)
        self.notes_view = NotesBrowser()
        layout.addWidget(self.notes_view)
        self.notes_empty = QLabel("No release notes were published.")
        self.notes_empty.setObjectName("muted")
        self.notes_empty.setWordWrap(True)
        layout.addWidget(self.notes_empty)
        self.notes_link = link_button("Full release notes on GitHub ↗")
        self.notes_link.clicked.connect(self.open_release_page)
        layout.addWidget(self.notes_link, alignment=Qt.AlignmentFlag.AlignLeft)
        # The notes view sizes itself; spare height belongs below the panels, not inside.
        frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        column.addWidget(frame)

    def build_versions(self, column: QVBoxLayout) -> None:
        frame, layout, _ = panel("Versions")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setColumnMinimumWidth(0, 84)
        grid.setColumnStretch(1, 1)
        self.version_rows: dict[str, tuple[QLabel, QWidget]] = {}
        self.versions: dict[str, VersionValue] = {}
        for row, (key, caption) in enumerate(
            (
                ("installed", "Installed"),
                ("previous", "Previous copy"),
                ("stable", "Latest stable"),
                ("preview", "Latest preview"),
            )
        ):
            label = QLabel(caption)
            label.setObjectName("captionLabel")
            value = VersionValue()
            grid.addWidget(label, row, 0, alignment=Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value, row, 1)
            self.version_rows[key] = (label, value)
            self.versions[key] = value
        checked_label = QLabel("Last checked")
        checked_label.setObjectName("captionLabel")
        checked = QHBoxLayout()
        checked.setSpacing(8)
        self.checked_value = QLabel()
        self.checked_value.setObjectName("versionValue")
        self.check_button = link_button("Check now")
        self.check_button.setToolTip(
            "Look up stable and preview releases again without downloading anything."
        )
        self.check_button.clicked.connect(self.check_now)
        checked.addWidget(self.checked_value)
        checked.addWidget(self.check_button)
        checked.addStretch()
        grid.addWidget(checked_label, 4, 0)
        grid.addLayout(checked, 4, 1)
        layout.addLayout(grid)
        self.note = QLabel()
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.note)
        column.addWidget(frame)

    def build_preferences(self, column: QVBoxLayout) -> None:
        frame, layout, _ = panel("Preferences")
        self.channel = Segmented(("Stable", "Preview"))
        self.channel.setAccessibleName("Release channel")
        self.channel.buttons[0].setAccessibleName("Stable releases")
        self.channel.buttons[1].setAccessibleName("Stable and preview releases")
        self.channel.currentIndexChanged.connect(self.change_channel)
        self.hold_switch = Switch()
        self.hold_switch.setAccessibleName("Hold at this version")
        self.hold_switch.clicked.connect(self.toggle_hold)
        self.pin_switch = Switch()
        self.pin_switch.setAccessibleName("Pin to top")
        self.pin_switch.clicked.connect(self.toggle_pin)
        self.build_button = QPushButton("Change…")
        self.build_button.setAccessibleName("Change build")
        self.build_button.clicked.connect(self.choose_build)
        icon_control = QWidget()
        icon_layout = QHBoxLayout(icon_control)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_layout.setSpacing(8)
        self.icon_preview = QLabel()
        self.icon_preview.setFixedSize(32, 32)
        self.icon_preview.setAccessibleName("Application icon preview")
        self.icon_button = QPushButton("Change…")
        self.icon_button.setAccessibleName("Change icon")
        menu = QMenu(self.icon_button)
        self.find_icons_action = menu.addAction("Find online")
        self.find_icons_action.setToolTip("Look for icons in the application's repository.")
        self.find_icons_action.triggered.connect(self.find_existing_icons)
        self.choose_icon_action = menu.addAction("Choose file…")
        self.choose_icon_action.triggered.connect(self.choose_icon)
        self.default_icon_action = menu.addAction("Use the default icon")
        self.default_icon_action.triggered.connect(self.use_default_icon)
        self.icon_button.setMenu(menu)
        icon_layout.addWidget(self.icon_preview)
        icon_layout.addWidget(self.icon_button)
        self.shortcut_button = QPushButton("Repair")
        self.shortcut_button.setAccessibleName("Repair menu shortcut")
        self.shortcut_button.clicked.connect(self.create_shortcut)
        self.arguments_edit = QLineEdit()
        self.arguments_edit.setAccessibleName("Launch arguments")
        self.arguments_edit.setPlaceholderText("--flag value")
        self.arguments_edit.editingFinished.connect(self.save_launch_options)
        self.environment_edit = QLineEdit()
        self.environment_edit.setAccessibleName("Launch environment")
        self.environment_edit.setPlaceholderText("NAME=value OTHER=value")
        self.environment_edit.editingFinished.connect(self.save_launch_options)
        self.channel_row = SettingRow(
            "Release channel", "Previews may be less reliable", self.channel
        )
        self.hold_row = SettingRow(
            "Hold at this version", "Keep checking, never offer updates", self.hold_switch
        )
        self.pin_row = SettingRow("Pin to top", "Show first in the library", self.pin_switch)
        self.build_row = SettingRow("Build", "Automatic", self.build_button)
        self.icon_row = SettingRow("Icon", "", icon_control)
        self.shortcut_row = SettingRow(
            "Menu shortcut", "In your applications menu", self.shortcut_button
        )
        self.arguments_row = SettingRow(
            "Arguments", "Used by Launch and the menu shortcut", self.arguments_edit, below=True
        )
        self.environment_row = SettingRow(
            "Environment",
            "Variables for the app, separated by spaces",
            self.environment_edit,
            below=True,
        )
        add_setting_rows(
            layout,
            (
                self.channel_row,
                self.hold_row,
                self.pin_row,
                self.build_row,
                self.icon_row,
                self.shortcut_row,
                self.arguments_row,
                self.environment_row,
            ),
        )
        self.launch_error = QLabel()
        self.launch_error.setObjectName("settingError")
        self.launch_error.setTextFormat(Qt.TextFormat.PlainText)
        self.launch_error.setWordWrap(True)
        self.launch_error.setAccessibleName("Launch options problem")
        self.launch_error.hide()
        layout.addWidget(self.launch_error)
        self.hold_hint = self.hold_row.hint
        self.build_hint = self.build_row.hint
        self.icon_hint = self.icon_row.hint
        column.addWidget(frame)

    def build_manage(self, column: QVBoxLayout) -> None:
        frame, layout, _ = panel("Manage", "dangerPanel")
        self.rollback_button = QPushButton("Roll back")
        self.rollback_button.clicked.connect(self.rollback)
        self.uninstall_button = QPushButton("Uninstall…")
        self.uninstall_button.clicked.connect(self.uninstall)
        self.remove_button = QPushButton("Remove…")
        self.remove_button.setAccessibleName("Remove from library")
        self.remove_button.clicked.connect(self.remove_from_library)
        for button in (self.uninstall_button, self.remove_button):
            button.setObjectName("danger")
        self.rollback_row = SettingRow("Roll back", "Restore the kept copy", self.rollback_button)
        self.uninstall_row = SettingRow("Uninstall", "Your data is kept", self.uninstall_button)
        self.remove_row = SettingRow(
            "Remove from library", "No application files are deleted", self.remove_button
        )
        layout.addWidget(self.rollback_row)
        # Each divider belongs to the row below it and is shown with that row.
        self.manage_dividers: dict[SettingRow, QFrame] = {}
        for row in (self.uninstall_row, self.remove_row):
            divider = QFrame()
            divider.setObjectName("rowDivider")
            divider.setFixedHeight(1)
            self.manage_dividers[row] = divider
            layout.addWidget(divider)
            layout.addWidget(row)
        column.addWidget(frame)

    # State

    def current_app(self) -> App | None:
        return next((app for app in self.launcher.apps if app.id == self.app_id), None)

    def show_app(self, app_id: str, back_label: str) -> None:
        self.app_id = app_id
        self.pending = ""
        self.icon_key = None
        self.message = ""
        self.shown_notes = None
        self.options_app = None
        self.launch_error.hide()
        self.back_button.setText(f"‹ {back_label}")
        self.back_button.setAccessibleName(f"Back to {back_label}")
        self.verticalScrollBar().setValue(0)
        self.refresh()

    def refresh(self) -> None:
        launcher = self.launcher
        app = self.current_app()
        if app is None:
            return
        # Read focus first: disabling or hiding the focused button moves it elsewhere.
        focused = QApplication.focusWidget()
        installed = launcher.installed.get(app.id)
        release = launcher.releases.get(app.id)
        info = launcher.release_infos.get(app.id)
        busy = launcher.task is not None
        managed = bool(installed and installed.managed)
        status = app_status(
            app,
            installed,
            release,
            launcher.check_errors.get(app.id, ""),
            info,
            launcher.checks.is_checking(app.id),
            launcher.release_errors.get(app.id, ""),
            launcher.working.get(app.id, ""),
        )
        self.refresh_header(app, status.mode, status.state, status.text, status.tooltip, busy)
        self.refresh_notes(app, release, info, installed is not None)
        previous = previous_copy(installed)
        self.refresh_versions(app, installed, release, info, previous, busy)
        self.refresh_preferences(app, installed, busy)
        self.rollback_row.label.setText(
            f"Roll back to {version_text(previous.version)}" if previous else "Roll back"
        )
        self.rollback_button.setEnabled(bool(not busy and managed and previous))
        self.uninstall_button.setEnabled(bool(not busy and managed))
        self.remove_button.setEnabled(not busy and installed is None)
        for row, visible in (
            (self.uninstall_row, installed is not None),
            (self.remove_row, installed is None),
        ):
            row.setVisible(visible)
            self.manage_dividers[row].setVisible(visible)
        if focused is not None and self.isAncestorOf(focused) and not focused.isVisibleTo(self):
            # Keep keyboard focus in the Manage panel when its button disappears.
            shown = self.uninstall_button if installed is not None else self.remove_button
            (shown if shown.isEnabled() else self.rollback_button).setFocus()

    def refresh_header(
        self, app: App, mode: str, state: str, text: str, tooltip: str, busy: bool
    ) -> None:
        launcher = self.launcher
        path = launcher.library.icon_path(app)
        stamp = file_stamp(path)
        if self.icon_key != (path, stamp):
            self.icon_key = (path, stamp)
            self.icon.setPixmap(icon_pixmap(path, 72))
            self.icon.setAccessibleName(f"{app.name} icon")
            self.icon_preview.setPixmap(icon_pixmap(path, 32))
            self.icon_hint.setText("Saved icon" if path != app.icon else "Default icon")
        self.title.setText(app.name)
        self.source.setText(f"{app.repo} · {app.description}" if app.description else app.repo)
        show_chip(self.chip, state, text, tooltip)
        preview = app.include_prereleases
        show_chip(
            self.channel_chip,
            "none",
            "Preview channel" if preview else "Stable channel",
            "Stable and preview releases are offered."
            if preview
            else "Only stable releases are offered.",
        )
        installed = launcher.installed.get(app.id)
        release = launcher.releases.get(app.id)
        self.mode = mode
        self.launch_button.setVisible(installed is not None)
        self.launch_button.setEnabled(installed is not None)
        self.launch_button.setToolTip(f"Launch {installed.path}" if installed else "")
        self.launch_button.setAccessibleName(f"Launch {app.name}")
        launch_only = mode == "launch"
        set_object_name(self.launch_button, "primary" if launch_only else "")
        self.action_button.setVisible(not launch_only)
        text, accessible, tooltip = {
            "cancel": ("Cancel", f"Cancel {app.name}", "Cancel the running operation"),
            "build": (
                "Choose build",
                f"Choose build for {app.name}",
                "Choose which build to install",
            ),
            "update": (
                f"Update to {version_text(release.version)}" if release else "Update",
                f"Update {app.name}",
                "Update; keep the previous version",
            ),
            "launch": ("", "", ""),
            "install": (
                "Install",
                f"Install {app.name}",
                "Check the eligible release, then download and verify its AppImage",
            ),
        }[mode]
        self.action_button.setText(text)
        self.action_button.setAccessibleName(accessible)
        self.action_button.setToolTip(tooltip)
        self.action_button.setEnabled(mode == "cancel" or not busy)

    def refresh_notes(
        self, app: App, release: Release | None, info: ReleaseInfo | None, installed: bool
    ) -> None:
        if release is None and info is not None:
            choice = info.choice(app.include_prereleases)
            release = choice.release if choice else None
        if release is None:
            self.notes_heading.setText("WHAT'S NEW")
            self.release_title.setText("")
            self.release_badge.hide()
            self.release_meta.setText("Release notes appear after the releases have been checked.")
            self.notes_view.hide()
            self.notes_empty.hide()
            self.notes_link.setText("Releases on GitHub ↗")
            self.release_url = f"{app.source_url}/releases"
            return
        self.notes_heading.setText(f"WHAT'S NEW IN {release.version.upper()}")
        self.release_title.setText(f"{app.name} {release.version}")
        label = "Pre-release" if release.prerelease else "Latest" if release.is_latest else ""
        self.release_badge.setText(label)
        self.release_badge.setVisible(bool(label))
        state = "prerelease" if release.prerelease else "latest"
        if self.release_badge.property("release") != state:
            self.release_badge.setProperty("release", state)
            repolish(self.release_badge)
        parts = []
        if release.published:
            parts.append(f"Released {format_date(release.published)}")
        parts.append(format_size(release.size))
        if release.sha256:
            parts.append("checksum published")
        self.release_meta.setText(" · ".join(parts))
        notes = release.notes.strip()
        if notes != self.shown_notes:
            self.shown_notes = notes
            self.notes_view.set_notes(notes)
        self.notes_view.setVisible(bool(notes))
        self.notes_empty.setVisible(not notes)
        self.notes_link.setText("Full release notes on GitHub ↗")
        self.release_url = release_page_url(release)

    def refresh_versions(
        self,
        app: App,
        installed: Any,
        release: Release | None,
        info: ReleaseInfo | None,
        previous: PreviousCopy | None,
        busy: bool,
    ) -> None:
        launcher = self.launcher
        latest = info.stable.release if info and info.stable else release
        installed_value = self.versions["installed"]
        if installed:
            installed_value.set_version(
                version_text(installed.version),
                installed.prerelease,
                bool(
                    installed.version
                    and latest
                    and installed.version == latest.version
                    and latest.is_latest
                ),
            )
            installed_value.setToolTip(f"Installed with AppGlider\n{installed.path}")
        else:
            installed_value.set_version("Not installed")
            installed_value.setToolTip("")
        installed_value.text.setToolTip(installed_value.toolTip())
        self.set_row_visible("previous", previous is not None)
        if previous:
            self.versions["previous"].set_version(
                f"{version_text(previous.version)} · kept for roll back · "
                f"{format_size(previous.size)}",
                previous.prerelease,
            )
            self.versions["previous"].setToolTip(str(previous.path))
        stable: ReleaseOption | None = info.stable if info else None
        preview: ReleaseOption | None = info.preview if info else None
        if info is None and release is not None:
            stable, preview = (
                (None, ReleaseOption(release.version, release))
                if release.prerelease
                else (ReleaseOption(release.version, release), None)
            )
        self.show_option("stable", stable, info is None and release is None)
        self.set_row_visible("preview", preview is not None)
        if preview:
            self.show_option("preview", preview, False)
        checked = info.checked_at if info else None
        self.checked_value.setText(checked_text(checked))
        checking = self.pending == "check" and busy
        self.check_button.setText("Checking…" if checking else "Check now")
        self.check_button.setEnabled(not busy)
        error = launcher.release_errors.get(app.id) or launcher.check_errors.get(app.id, "")
        self.note.setText(error or self.message)
        self.note.setVisible(bool(self.note.text()))

    def show_option(self, key: str, option: ReleaseOption | None, unchecked: bool) -> None:
        value = self.versions[key]
        if option is None:
            value.set_version("Not checked" if unchecked else "Unavailable")
            value.setToolTip("")
            return
        value.set_version(
            version_text(option.version) + (" · unavailable" if option.release is None else ""),
            bool(option.release and option.release.prerelease),
            bool(option.release and option.release.is_latest),
        )
        value.setToolTip(option.error)

    def set_row_visible(self, key: str, visible: bool) -> None:
        for widget in self.version_rows[key]:
            widget.setVisible(visible)

    def refresh_preferences(self, app: App, installed: Any, busy: bool) -> None:
        launcher = self.launcher
        self.channel.setCurrentIndex(int(app.include_prereleases))
        self.channel.setEnabled(not busy)
        held = app.hold_updates
        self.hold_switch.setChecked(held)
        self.hold_switch.setEnabled(bool(not busy and (held or (installed and installed.managed))))
        self.hold_hint.setText(self.hold_text(app, installed))
        self.pin_switch.setChecked(app.id in launcher.pinned)
        self.build_hint.setText(
            variant_label(app.build_variant) if app.build_variant else "Automatic"
        )
        self.build_button.setEnabled(not busy)
        self.icon_button.setEnabled(not busy)
        self.shortcut_button.setEnabled(bool(not busy and installed and installed.managed))
        self.refresh_launch_options(app)

    def refresh_launch_options(self, app: App) -> None:
        """Show the saved options, leaving text alone while it is being edited or refused."""
        editing = self.arguments_edit.hasFocus() or self.environment_edit.hasFocus()
        if self.options_app == app.id and (editing or self.launch_error.isVisible()):
            return
        try:
            options = self.launcher.library.launch_options(app.id)
        except ValueError as error:
            options = LaunchOptions()
            self.launch_error.setText(str(error))
            self.launch_error.show()
        self.options_app = app.id
        self.arguments_edit.setText(format_arguments(options))
        self.environment_edit.setText(format_environment(options))

    def hold_text(self, app: App, installed: Any) -> str:
        """Say what is held, or what holding does."""
        if not (installed and app.hold_updates):
            return "Keep checking, never offer updates"
        launcher = self.launcher
        text = f"Held at {version_text(installed.version)}"
        release = launcher.releases.get(app.id)
        info = launcher.release_infos.get(app.id)
        choice = info.choice(app.include_prereleases) if info else None
        if release and release.newer_than(installed.version):
            text += f" · {version_text(release.version)} available"
        elif (
            choice
            and choice.build_required
            and version_key(choice.version) > version_key(installed.version)
        ):
            text += f" · {version_text(choice.version)} available"
        return text

    # Actions

    def launch(self) -> None:
        if self.app_id:
            self.launcher.launch_app(self.app_id)

    def activate(self) -> None:
        if self.app_id is None:
            return
        if self.mode in ("install", "update"):
            self.launcher.install_app(self.app_id)
        elif self.mode == "build":
            self.choose_build()
        elif self.mode == "cancel":
            self.launcher.cancel_task()

    def open_release_page(self) -> None:
        QDesktopServices.openUrl(QUrl(self.release_url))

    def toggle_hold(self) -> None:
        if self.app_id:
            self.launcher.toggle_hold(self.app_id)
        self.refresh()

    def toggle_pin(self) -> None:
        if self.app_id:
            self.launcher.toggle_pin(self.app_id)

    def create_shortcut(self) -> None:
        if self.app_id:
            self.launcher.create_shortcut(self.app_id)

    def save_launch_options(self) -> None:
        app = self.current_app()
        if app is None or self.options_app != app.id:
            return
        library = self.launcher.library
        try:
            options = LaunchOptions(
                parse_arguments(self.arguments_edit.text()),
                parse_environment(self.environment_edit.text()),
            )
            if options != library.launch_options(app.id):
                library.set_launch_options(app.id, options, refresh_database=False)
                self.launcher.refresh_desktop_database()
                self.launcher.footer.setText(f"Saved launch options for {app.name}.")
        except (OSError, ValueError) as error:
            self.launch_error.setText(str(error))
            self.launch_error.show()
            return
        self.launch_error.hide()

    def rollback(self) -> None:
        if self.app_id:
            self.launcher.rollback_app(self.app_id)

    def uninstall(self) -> None:
        if self.app_id:
            self.launcher.remove_app(self.app_id)

    def change_channel(self, index: int) -> None:
        app = self.current_app()
        if app is None or self.launcher.task is not None:
            self.refresh()
            return
        try:
            self.launcher.library.set_release_preference(app.id, index == 1)
        except (OSError, ValueError) as error:
            self.launcher.footer.setText(str(error))
            self.refresh()
            return
        self.message = "Release preference saved. Check the available release."
        self.launcher.catalog_changed()
        self.launcher.footer.setText(self.message)

    def remove_from_library(self) -> None:
        app = self.current_app()
        if app is None or self.launcher.task is not None or app.id in self.launcher.installed:
            return
        answer = QMessageBox.question(
            self,
            f"Remove {app.name} from the library?",
            f"{app.name} will no longer be listed. No application files are deleted.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.launcher.library.remove_repository(app.id)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Cannot remove the app", str(error))
            return
        self.launcher.catalog_changed()
        self.launcher.footer.setText(
            f"Removed {app.name} from the library. No application files were deleted."
        )

    # Background work

    def start(
        self, kind: str, message: str, operation: Callable[[Event, Progress], TaskResult]
    ) -> None:
        self.pending = kind
        self.pending_app = self.app_id
        self.message = ""
        self.launcher.start_task(message, operation, page=self)

    def check_now(self) -> None:
        if self.launcher.task is None and self.app_id:
            self.pending = "check"
            self.pending_app = self.app_id
            self.message = ""
            self.launcher.check_releases(self.app_id, self)

    def choose_build(self) -> None:
        """Look up the builds a release offers and let the person choose one."""
        app = self.current_app()
        if app is not None and self.launcher.task is None:
            self.inspect_build(app, "")

    def inspect_build(self, app: App, variant: str) -> None:
        target = replace(app, build_variant=variant)
        outcome: dict[str, Any] = {}
        cache_dir = self.launcher.library.data_dir / "release-cache"

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            try:
                outcome["release"] = latest_release(target, cancel, cache_dir)
            except BuildChoiceRequired as error:
                outcome["choices"] = error
                return TaskResult("Choose a build.")
            return TaskResult("Release inspected.")

        self.build_result = outcome
        self.build_target = target
        self.start("build", "Checking eligible releases and the published checksum…", operation)

    def find_existing_icons(self) -> None:
        app = self.current_app()
        if app is None or self.launcher.task is not None:
            return

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            return TaskResult(
                "No icon found. Choose an image to replace the current icon.",
                icons=discover_icons(app, cancel, lambda text: progress(text, 0, 0)),
            )

        self.start("icons", f"Looking for icons for {app.name}…", operation)

    def task_done(self, result: TaskResult) -> None:
        """Receive the result of work this page started (the window has already handled it)."""
        kind, self.pending = self.pending, ""
        if self.launcher.close_after_task or self.app_id is None or self.pending_app != self.app_id:
            return
        if kind == "check":
            if result.release_infos.get(self.app_id) is None:
                self.message = result.message
        elif kind == "build":
            self.build_finished()
        elif kind == "icons" and result.icons:
            self.pick_icon(result.icons)
        self.refresh()

    def task_error(self, error: str) -> None:
        kind, self.pending = self.pending, ""
        if self.app_id is None or self.pending_app != self.app_id:
            return
        if kind == "check":
            self.message = error
        self.refresh()

    def build_finished(self) -> None:
        target, outcome = self.build_target, self.build_result
        app = self.current_app()
        if target is None or app is None or target.id != app.id:
            return
        error = outcome.get("choices")
        if isinstance(error, BuildChoiceRequired):
            chosen = self.pick_variant(app, error)
            if chosen:
                self.inspect_build(app, chosen)
            return
        release = outcome.get("release")
        if release is None:
            return
        launcher = self.launcher
        changed = target.build_variant != app.build_variant
        if changed:
            try:
                launcher.library.set_build_variant(app.id, target.build_variant)
            except (OSError, ValueError) as failure:
                launcher.footer.setText(f"Build preference could not be saved: {failure}")
                QMessageBox.warning(self, "Cannot save the build", str(failure))
                return
        remember_release(target, release, launcher.library.data_dir / "release-cache")
        launcher.catalog_changed()
        launcher.footer.setText(
            "Build preference saved for future installs and updates. The installed copy was kept."
            if changed
            else "Release checked."
        )

    def pick_variant(self, app: App, error: BuildChoiceRequired) -> str:
        """Ask which build to use; return its pattern, or an empty string if none was chosen."""
        labels = [f"{variant_label(pattern)} ({name})" for pattern, name in error.choices]
        current = next(
            (i for i, (pattern, _) in enumerate(error.choices) if pattern == app.build_variant), 0
        )
        text, accepted = QInputDialog.getItem(
            self,
            "Choose a build",
            "Your saved build is unavailable. Choose a replacement to continue."
            if error.missing
            else "Multiple builds are available. Choose the one for your system.",
            labels,
            current,
            False,
        )
        if not accepted or text not in labels:
            return ""
        return error.choices[labels.index(text)][0]

    def pick_icon(self, icons: tuple[IconCandidate, ...]) -> None:
        index = 0
        if len(icons) > 1:
            names = [Path(icon.name).name for icon in icons]
            text, accepted = QInputDialog.getItem(
                self, "Choose an icon", "Icons found in the repository:", names, 0, False
            )
            if not accepted:
                return
            index = names.index(text)
        self.save_icon(icons[index].png)

    def choose_icon(self) -> None:
        if self.current_app() is None or self.launcher.task is not None:
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
            QMessageBox.warning(self, "Cannot use this icon", str(error))
            return
        self.save_icon(png)

    def use_default_icon(self) -> None:
        if self.current_app() is not None and self.launcher.task is None:
            self.save_icon(None)

    def save_icon(self, png: bytes | None) -> None:
        app = self.current_app()
        if app is None:
            return
        try:
            self.launcher.library.set_icon(app, png)
        except (OSError, ValueError) as error:
            self.launcher.footer.setText(f"Icon could not be saved: {error}")
            QMessageBox.warning(self, "Cannot save the icon", str(error))
            return
        self.launcher.catalog_changed()
        self.launcher.footer.setText(f"Saved icon for {app.name}.")
        self.refresh()


def checked_text(checked: float | None) -> str:
    if checked is None:
        return "Not checked"
    moment = datetime.fromtimestamp(checked)
    if moment.date() == date.today():
        return f"Today {moment:%H:%M}"
    return f"{moment.day:02d} {moment:%b %H:%M}"


def set_object_name(widget: QWidget, name: str) -> None:
    if widget.objectName() != name:
        widget.setObjectName(name)
        repolish(widget)


def show_chip(chip: QLabel, state: str, text: str, tooltip: str) -> None:
    chip.setText(text)
    chip.setToolTip(tooltip)
    if chip.property("state") != state:
        chip.setProperty("state", state)
        repolish(chip)
