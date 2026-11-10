"""The App page: one application's status, release notes, versions and preferences."""

from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPaintEvent,
    QPalette,
    QResizeEvent,
    QShowEvent,
    QWheelEvent,
)
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
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from appglider.core.catalog import MANUAL_DESCRIPTION, App
from appglider.github.assets import BuildChoiceRequired, Release, variant_label
from appglider.github.download import Progress, latest_release
from appglider.github.http import UNREACHABLE, file_stamp
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
from appglider.ui.app_status import version_text as version_text
from appglider.ui.controls import MessageBox, Segmented, Switch, repolish
from appglider.ui.errors import describe_error
from appglider.ui.jobs import TaskResult
from appglider.ui.pages.icon_picker import choose_icon_candidate

if TYPE_CHECKING:
    from appglider.ui.window import LauncherWindow

NOTES_PREVIEW_LINES = 5
WEB_SCHEMES = ("http", "https")
RUNNING_CHECK_MS = 3000
WAIT_TIP = "Wait for the current task to finish, or cancel it."
CHECK_TIP = "Look up stable and preview releases again without downloading anything."
SAVED_SHOWN_MS = 3000
UNINSTALL_HINT = "Deletes the file; your data is kept"
UNINSTALL_MANUAL_HINT = "Also leaves your library"
FORGET_HINT = "No files are deleted"
FORGET_MANUAL_HINT = "Also leaves your library"
REMOVE_HINT = "No files are deleted"
HOLD_HINT = "New releases are shown, not installed"
PIN_HINT = "Show first in the library"
OFFLINE_NOTE = "Offline: could not reach GitHub. Showing what was known before."


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

    fitted = Signal()  # the height, and whether the preview hides anything, may have changed

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
        self.expanded = False
        self.overflows = False
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def loadResource(self, type: int, name: QUrl | str) -> Any:  # noqa: A002, N802
        """Notes come from the network: never fetch images or read local files for them.

        Only an image stops Qt from opening the path itself; None or bytes do not.
        """
        return QImage()

    def open_link(self, url: QUrl) -> None:
        if url.scheme().lower() in WEB_SCHEMES and url.host():
            QDesktopServices.openUrl(url)

    def set_notes(self, text: str) -> None:
        self.setMarkdown(text)
        self.fit()

    def preview_height(self) -> int:
        return self.fontMetrics().lineSpacing() * NOTES_PREVIEW_LINES + 6

    def fit(self) -> None:
        """Show the first lines, or all of them once expanded; the page scrolls, never this."""
        if self.fitting:
            return
        self.fitting = True
        try:
            document = self.document()
            document.setTextWidth(max(1, self.width()))
            full = int(document.size().height()) + 6
            # Hiding less than a line behind a button would only be in the way.
            self.overflows = full > self.preview_height() + self.fontMetrics().lineSpacing()
            height = max(24, full if self.expanded or not self.overflows else self.preview_height())
            if self.height() != height:
                self.setFixedHeight(height)
            self.verticalScrollBar().setValue(0)
        finally:
            self.fitting = False
        self.fitted.emit()

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = expanded
        self.fit()
        self.viewport().update()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        event.ignore()  # the page scrolls; the notes never hold the wheel

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        if self.expanded or not self.overflows:
            return
        # Fade the last lines into the panel, a hint that there is more.
        viewport = self.viewport()
        fade = min(viewport.height(), self.fontMetrics().lineSpacing() * 2)
        surface = QApplication.palette().color(QPalette.ColorRole.Base)
        clear = QColor(surface)
        clear.setAlpha(0)
        gradient = QLinearGradient(0, viewport.height() - fade, 0, viewport.height())
        gradient.setColorAt(0, clear)
        gradient.setColorAt(1, surface)
        painter = QPainter(viewport)
        painter.fillRect(0, viewport.height() - fade, viewport.width(), fade, gradient)
        painter.end()

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
        self.row = layout = QHBoxLayout(self)
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

    def add_link(self, button: QPushButton) -> None:
        """A link at the end of the line, such as Roll back beside the previous copy."""
        self.row.addWidget(button, alignment=Qt.AlignmentFlag.AlignTop)

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


def add_setting_rows(layout: QVBoxLayout, rows: Iterable[QWidget]) -> dict[QWidget, QFrame]:
    """Stack setting rows in a panel with a divider between neighbours.

    Returns each row's divider (the one above it), to hide along with the row.
    """
    dividers: dict[QWidget, QFrame] = {}
    for index, row in enumerate(rows):
        if index:
            divider = QFrame()
            divider.setObjectName("rowDivider")
            divider.setFixedHeight(1)
            layout.addWidget(divider)
            dividers[row] = divider
        layout.addWidget(row)
    return dividers


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
        self.message_failed = False
        self.release_url = ""
        self.shown_notes: str | None = None
        self.options_app: str | None = None
        self.running = False
        # An app can be started or closed anywhere, so the page looks again every few seconds.
        self.running_timer = QTimer(self)
        self.running_timer.setInterval(RUNNING_CHECK_MS)
        self.running_timer.timeout.connect(self.update_running)
        self.running_timer.start()
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
        self.left_empty = False
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
            self.apply_column_stretch()

    def apply_column_stretch(self) -> None:
        # Side by side the left column takes 13 parts, unless it is empty (a manual app).
        side_by_side = self.columns.direction() == QBoxLayout.Direction.LeftToRight
        self.columns.setStretch(0, 13 if side_by_side and not self.left_empty else 0)
        self.columns.setStretch(1, 10 if side_by_side else 0)

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
        self.repo_link = link_button("")
        self.repo_link.setToolTip("Open the repository on GitHub")
        self.repo_link.clicked.connect(self.open_repository)
        self.source = QLabel()
        self.source.setObjectName("muted")
        self.source.setTextFormat(Qt.TextFormat.PlainText)
        self.source.setWordWrap(True)
        source_line = QHBoxLayout()
        source_line.setSpacing(0)
        source_line.setContentsMargins(-5, 0, 0, 0)  # the link's own padding lines up with the name
        source_line.addWidget(self.repo_link, alignment=Qt.AlignmentFlag.AlignTop)
        source_line.addWidget(self.source, stretch=1)
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
        identity.addLayout(source_line)
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
        self.notes_frame = frame
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
        links = QHBoxLayout()
        links.setContentsMargins(0, 0, 0, 0)
        self.notes_toggle = link_button("Show all notes ▾")
        self.notes_toggle.clicked.connect(self.toggle_notes)
        self.notes_view.fitted.connect(self.update_notes_toggle)
        self.notes_link = link_button("On GitHub ↗")
        self.notes_link.setAccessibleName("Full release notes on GitHub")
        self.notes_link.clicked.connect(self.open_release_page)
        links.addWidget(self.notes_toggle)
        links.addStretch()
        links.addWidget(self.notes_link)
        layout.addLayout(links)
        # The notes view sizes itself; spare height belongs below the panels, not inside.
        frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        column.addWidget(frame)

    def build_versions(self, column: QVBoxLayout) -> None:
        frame, layout, _ = panel("Versions")
        self.versions_frame = frame
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
        self.rollback_link = link_button("Roll back")
        self.rollback_link.clicked.connect(self.rollback)
        self.versions["previous"].add_link(self.rollback_link)
        checked_label = QLabel("Last checked")
        checked_label.setObjectName("captionLabel")
        checked = QHBoxLayout()
        checked.setSpacing(8)
        self.checked_value = QLabel()
        self.checked_value.setObjectName("versionValue")
        self.check_button = link_button("Check now")
        self.check_button.setToolTip(CHECK_TIP)
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
        self.missing_note = QLabel()
        self.missing_note.setObjectName("muted")
        self.missing_note.setWordWrap(True)
        self.missing_note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.missing_note)
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
        self.replace_button = QPushButton("Replace with a newer file…")
        self.replace_button.clicked.connect(self.replace_file)
        self.build_button = QPushButton("Automatic…")
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
        # Whether the menu entry exists is not cheap to know, so the button covers both.
        self.shortcut_button = QPushButton("Create or repair")
        self.shortcut_button.setAccessibleName("Create or repair menu shortcut")
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
            "Release channel", "Previews can be less reliable", self.channel, below=True
        )
        self.hold_row = SettingRow("Hold at this version", HOLD_HINT, self.hold_switch)
        self.pin_row = SettingRow("Pin to top", PIN_HINT, self.pin_switch)
        self.build_row = SettingRow("Build", "Which download to use", self.build_button)
        self.replace_row = SettingRow(
            "Newer file", "The old file is kept for Roll back", self.replace_button
        )
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
        self.preference_dividers = add_setting_rows(
            layout,
            (
                self.channel_row,
                self.hold_row,
                self.pin_row,
                self.build_row,
                self.replace_row,
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
        self.options_saved = QLabel("Saved")
        self.options_saved.setObjectName("muted")
        self.options_saved.setAccessibleName("Launch options saved")
        self.options_saved.hide()
        layout.addWidget(self.options_saved)
        self.hold_hint = self.hold_row.hint
        self.build_hint = self.build_row.hint
        self.pin_hint = self.pin_row.hint
        self.icon_hint = self.icon_row.hint
        column.addWidget(frame)

    def build_manage(self, column: QVBoxLayout) -> None:
        frame, layout, _ = panel("Manage", "dangerPanel")
        self.force_button = QPushButton("Force close…")
        self.force_button.clicked.connect(self.force_close)
        self.rollback_button = QPushButton("Roll back")
        self.rollback_button.clicked.connect(self.rollback)
        self.uninstall_button = QPushButton("Uninstall…")
        self.uninstall_button.clicked.connect(self.uninstall)
        self.forget_button = QPushButton("Forget…")
        self.forget_button.setAccessibleName("Forget missing copy")
        self.forget_button.clicked.connect(self.forget_missing)
        self.remove_button = QPushButton("Remove…")
        self.remove_button.setAccessibleName("Remove from library")
        self.remove_button.clicked.connect(self.remove_from_library)
        for button in (
            self.force_button,
            self.uninstall_button,
            self.forget_button,
            self.remove_button,
        ):
            button.setObjectName("danger")
        self.force_row = SettingRow("Force close", "Unsaved work is lost", self.force_button)
        self.rollback_row = SettingRow("Roll back", "Restore the kept copy", self.rollback_button)
        self.uninstall_row = SettingRow("Uninstall", UNINSTALL_HINT, self.uninstall_button)
        self.forget_row = SettingRow("Forget missing copy", FORGET_HINT, self.forget_button)
        self.remove_row = SettingRow("Remove from library", REMOVE_HINT, self.remove_button)
        # Each divider sits above its row and shows only when a shown row comes before it.
        self.manage_dividers: dict[SettingRow, QFrame] = {}
        rows = (
            self.force_row,
            self.rollback_row,
            self.uninstall_row,
            self.forget_row,
            self.remove_row,
        )
        for index, row in enumerate(rows):
            if index:
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
        if self.pending_app != app_id:
            self.pending = ""
        self.icon_key = None
        self.message = ""
        self.message_failed = False
        self.shown_notes = None
        self.options_app = None
        self.launch_error.hide()
        self.options_saved.hide()
        self.back_button.setText(f"‹ {back_label}")
        self.back_button.setAccessibleName(f"Back to {back_label}")
        self.verticalScrollBar().setValue(0)
        self.running = self.launcher.app_running(app_id)
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
        busy = launcher.busy
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
            launcher.missing.get(app.id),
            self.running,
        )
        self.refresh_header(app, status.mode, status.state, status.text, status.tooltip, busy)
        self.refresh_notes(app, release, info, installed is not None)
        previous = previous_copy(installed)
        self.refresh_versions(app, installed, release, info, previous, busy)
        self.refresh_preferences(app, installed, busy)
        # Nothing to look up for an app added from a file: no releases, no versions column.
        self.notes_frame.setVisible(not app.manual)
        self.versions_frame.setVisible(not app.manual)
        self.left_empty = app.manual
        self.apply_column_stretch()
        missing = launcher.missing.get(app.id) if installed is None else None
        close_first = f"Close {app.name} first, or use Force close."
        running = self.running
        self.uninstall_row.hint.setText(
            close_first
            if running and installed is not None
            else UNINSTALL_MANUAL_HINT
            if app.manual
            else UNINSTALL_HINT
        )
        self.forget_row.label.setText("Forget" if app.manual else "Forget missing copy")
        self.forget_row.hint.setText(FORGET_MANUAL_HINT if app.manual else FORGET_HINT)
        self.rollback_row.label.setText(
            f"Roll back to {version_text(previous.version)}" if previous else "Roll back"
        )
        self.rollback_row.hint.setText(close_first if running else "Restore the kept copy")
        # Roll back sits on the Previous copy line; an app added from a file has no Versions.
        can_roll_back = managed and previous is not None
        self.rollback_link.setVisible(can_roll_back and not app.manual)
        self.rollback_link.setEnabled(not busy)
        self.rollback_link.setToolTip(
            WAIT_TIP if busy else close_first if running else "Restore the kept copy"
        )
        if previous:
            self.rollback_link.setAccessibleName(
                f"Roll back {app.name} to {version_text(previous.version)}"
            )
        self.force_button.setAccessibleName(f"Force close {app.name}")
        rows = (
            (self.force_row, self.force_button, managed and running),
            (self.rollback_row, self.rollback_button, can_roll_back and app.manual),
            (self.uninstall_row, self.uninstall_button, installed is not None),
            (self.forget_row, self.forget_button, missing is not None),
            (self.remove_row, self.remove_button, installed is None and missing is None),
        )
        earlier = False
        for row, button, visible in rows:
            row.setVisible(visible)
            waiting = busy and button is not self.force_button
            button.setEnabled(not waiting)
            if waiting:
                button.setToolTip(WAIT_TIP)
            else:
                button.setToolTip("")
            if row in self.manage_dividers:
                self.manage_dividers[row].setVisible(visible and earlier)
            earlier = earlier or visible
        if focused is not None and self.isAncestorOf(focused) and not focused.isVisibleTo(self):
            # Keep keyboard focus in the Manage panel when its button disappears.
            shown = (
                self.uninstall_button
                if installed is not None
                else self.forget_button
                if missing is not None
                else self.remove_button
            )
            if shown.isEnabled():
                shown.setFocus()

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
        self.repo_link.setVisible(not app.manual)
        if app.manual:
            self.source.setText(f"{MANUAL_DESCRIPTION} · {app.file}")
        else:
            self.repo_link.setText(app.repo)
            self.repo_link.setAccessibleName(f"Open {app.repo} on GitHub")
            self.source.setText(f"· {app.description}" if app.description else "")
        self.source.setVisible(bool(self.source.text()))
        show_chip(self.chip, state, text, tooltip)
        # Stable is the default and needs no chip; only a preview choice is worth saying.
        preview = app.include_prereleases
        self.channel_chip.setVisible(preview and not app.manual)
        show_chip(
            self.channel_chip,
            "none",
            "Previews on",
            "Stable and preview releases are offered.",
        )
        installed = launcher.installed.get(app.id)
        release = launcher.releases.get(app.id)
        self.mode = mode
        self.launch_button.setVisible(installed is not None)
        working = app.id in launcher.working
        self.launch_button.setEnabled(installed is not None and not working)
        self.launch_button.setToolTip(
            WAIT_TIP
            if working
            else "Already running"
            if self.running and installed
            else f"Launch {installed.path}"
            if installed
            else ""
        )
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
            # The target version is in the chip, so the button keeps one width.
            "update": (
                "Update",
                f"Update {app.name} to {version_text(release.version)}"
                if release
                else f"Update {app.name}",
                f"Update to {version_text(release.version)}; keep the previous version"
                if release
                else "Update; keep the previous version",
            ),
            "launch": ("", "", ""),
            "install": (
                "Install",
                f"Install {app.name}",
                "Check the eligible release, then download and verify its AppImage",
            ),
            "reinstall": (
                "Reinstall",
                f"Reinstall {app.name}",
                "The AppImage was moved or deleted; download and verify it again",
            ),
            "replace": (
                "Replace…",
                f"Choose a file for {app.name}",
                "The AppImage was moved or deleted; choose a file to use instead",
            ),
        }[mode]
        self.action_button.setText(text)
        self.action_button.setAccessibleName(accessible)
        waiting = busy and mode != "cancel"
        self.action_button.setToolTip(WAIT_TIP if waiting else tooltip)
        self.action_button.setEnabled(not waiting)

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
            self.notes_toggle.hide()
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
        parts.append(f"{format_size(release.size)} download")
        parts.append(
            "checked with the published checksum"
            if release.sha256
            else "no published checksum to check it against"
        )
        self.release_meta.setText(" · ".join(parts))
        notes = release.notes.strip()
        if notes != self.shown_notes:
            self.shown_notes = notes
            self.notes_view.expanded = False  # each release starts as a preview
            self.notes_view.set_notes(notes)
        self.notes_view.setVisible(bool(notes))
        self.notes_empty.setVisible(not notes)
        self.notes_link.setText("On GitHub ↗")
        self.update_notes_toggle()
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
            missing = self.launcher.missing.get(app.id)
            if missing is not None:
                installed_value.set_version(f"{version_text(missing.version)} · file missing")
                installed_value.setToolTip(f"No longer at\n{missing.path}")
            else:
                installed_value.set_version("Not installed")
                installed_value.setToolTip("")
        installed_value.text.setToolTip(installed_value.toolTip())
        # "Installed: Not installed" says nothing the header chip does not.
        missing_copy = None if installed else self.launcher.missing.get(app.id)
        self.set_row_visible("installed", bool(installed) or missing_copy is not None)
        self.set_row_visible("previous", previous is not None)
        if previous:
            self.versions["previous"].set_version(
                f"{version_text(previous.version)} · {format_size(previous.size)}",
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
        checking = launcher.checks.is_checking(app.id) or (
            self.pending == "check" and self.pending_app == app.id and busy
        )
        self.check_button.setText("Checking…" if checking else "Check now")
        self.check_button.setEnabled(not busy)
        self.check_button.setToolTip(CHECK_TIP if not busy else WAIT_TIP)
        error = launcher.release_errors.get(app.id) or launcher.check_errors.get(app.id, "")
        text, failed = error or self.message, bool(error) or self.message_failed
        if text == UNREACHABLE:
            text, failed = OFFLINE_NOTE, False  # being offline is not a failed check
        if not text and info is None and release is None and not installed and missing_copy is None:
            text = "Press Check now or Install to see the latest release."
        self.note.setText(text)
        self.note.setVisible(bool(text))
        set_object_name(self.note, "settingError" if failed else "muted")
        self.missing_note.setText(
            f"The file used to be at {missing_copy.path}" if missing_copy else ""
        )
        self.missing_note.setVisible(missing_copy is not None)

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
        managed = bool(installed and installed.managed)
        manual = app.manual
        # Holding and the menu shortcut need an installed copy; a stale hold can still go.
        # An app added from a file has no releases, so no channel, hold or build.
        earlier = False
        for row, visible in (
            (self.channel_row, not manual),
            (self.hold_row, (managed or held) and not manual),
            (self.pin_row, True),
            (self.build_row, not manual),
            (self.replace_row, manual),
            (self.icon_row, True),
            (self.shortcut_row, managed),
            (self.arguments_row, True),
            (self.environment_row, True),
        ):
            row.setVisible(visible)
            if row in self.preference_dividers:
                self.preference_dividers[row].setVisible(visible and earlier)
            earlier = earlier or visible
        self.find_icons_action.setVisible(not manual)
        self.replace_button.setEnabled(not busy)
        self.hold_switch.setEnabled(not busy)
        self.hold_hint.setText(self.hold_text(app, installed))
        self.pin_switch.setChecked(app.id in launcher.pinned)
        self.pin_hint.setText(PIN_HINT)
        build = variant_label(app.build_variant) if app.build_variant else "Automatic"
        self.build_button.setText(f"{build}…")
        self.build_button.setAccessibleName(f"Change build, now {build}")
        self.build_button.setToolTip("Change which download is used")
        self.build_button.setEnabled(not busy)
        self.icon_button.setEnabled(not busy)
        self.shortcut_button.setEnabled(not busy)
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
            self.launch_error.setText(describe_error(error))
            self.launch_error.show()
        self.options_app = app.id
        self.arguments_edit.setText(format_arguments(options))
        self.environment_edit.setText(format_environment(options))

    def hold_text(self, app: App, installed: Any) -> str:
        """Say what is held, or what holding does."""
        if not (installed and app.hold_updates):
            return HOLD_HINT
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
        if self.mode == "replace":
            self.replace_file()
        elif self.mode in ("install", "reinstall", "update"):
            self.launcher.install_app(self.app_id)
        elif self.mode == "build":
            self.choose_build()
        elif self.mode == "cancel":
            self.launcher.cancel_task()

    def toggle_notes(self) -> None:
        self.notes_view.set_expanded(not self.notes_view.expanded)
        self.update_notes_toggle()

    def update_notes_toggle(self) -> None:
        """Offer the toggle only when the preview hides part of the notes."""
        view = self.notes_view
        self.notes_toggle.setVisible(not view.isHidden() and view.overflows)
        self.notes_toggle.setText("Show less ▴" if view.expanded else "Show all notes ▾")
        self.notes_toggle.setAccessibleName(
            "Show fewer release notes" if view.expanded else "Show all release notes"
        )

    def open_repository(self) -> None:
        app = self.current_app()
        if app is not None and not app.manual:
            QDesktopServices.openUrl(QUrl(app.source_url))

    def open_release_page(self) -> None:
        QDesktopServices.openUrl(QUrl(self.release_url))

    def toggle_hold(self) -> None:
        if self.app_id:
            self.launcher.toggle_hold(self.app_id)
        self.refresh()

    def toggle_pin(self) -> None:
        if self.app_id:
            wanted = self.pin_switch.isChecked()
            self.launcher.toggle_pin(self.app_id)
            pinned = self.app_id in self.launcher.pinned
            if pinned != wanted:
                # The limit refused it: show the switch as it really is, and say why here too.
                self.pin_switch.setChecked(pinned)
                self.pin_hint.setText(
                    "The limit of pinned apps is reached. Unpin another app first."
                )

    def create_shortcut(self) -> None:
        if self.app_id:
            self.launcher.create_shortcut(self.app_id)

    def save_launch_options(self) -> None:
        app = self.current_app()
        if app is None or self.options_app != app.id:
            return
        library = self.launcher.library
        saved = False
        self.options_saved.hide()
        try:
            options = LaunchOptions(
                parse_arguments(self.arguments_edit.text()),
                parse_environment(self.environment_edit.text()),
            )
            if options != library.launch_options(app.id):
                library.set_launch_options(app.id, options, refresh_database=False)
                self.launcher.refresh_desktop_database()
                self.launcher.footer.setText(f"Saved launch options for {app.name}.")
                saved = True
        except (OSError, ValueError) as error:
            self.launch_error.setText(describe_error(error))
            self.launch_error.show()
            return
        self.launch_error.hide()
        if saved:
            self.options_saved.show()
            QTimer.singleShot(SAVED_SHOWN_MS, self.options_saved.hide)

    def update_running(self) -> None:
        """Show Force close only while the app runs; looked up only while the page is seen."""
        if self.app_id is None or not self.isVisible():
            return
        running = self.launcher.app_running(self.app_id)
        if running != self.running:
            self.running = running
            self.refresh()

    def force_close(self) -> None:
        if self.app_id:
            self.launcher.force_close_app(self.app_id)

    def replace_file(self) -> None:
        if self.app_id:
            self.launcher.replace_file(self.app_id)

    def rollback(self) -> None:
        if self.app_id:
            self.launcher.rollback_app(self.app_id)

    def uninstall(self) -> None:
        if self.app_id:
            self.launcher.remove_app(self.app_id)

    def change_channel(self, index: int) -> None:
        app = self.current_app()
        if app is None or self.launcher.busy:
            self.refresh()
            return
        try:
            self.launcher.library.set_release_preference(app.id, index == 1)
        except (OSError, ValueError) as error:
            self.launcher.footer.setText(describe_error(error))
            self.refresh()
            return
        self.launcher.catalog_changed()
        # The old results belong to the old channel: look again right away.
        self.check_now()
        self.message = "Release preference saved. Checking…"
        self.message_failed = False
        self.launcher.footer.setText(self.message)
        self.refresh()

    def remove_from_library(self) -> None:
        if self.app_id:
            self.launcher.remove_from_library(self.app_id)

    def forget_missing(self) -> None:
        if self.app_id:
            self.launcher.forget_missing(self.app_id)

    # Background work

    def start(
        self, kind: str, message: str, operation: Callable[[Event, Progress], TaskResult]
    ) -> None:
        self.pending = kind
        self.pending_app = self.app_id
        self.message = ""
        self.message_failed = False
        self.launcher.start_task(message, operation, page=self)

    def check_now(self) -> None:
        if not self.launcher.busy and self.app_id:
            self.pending = "check"
            self.pending_app = self.app_id
            self.message = ""
            self.message_failed = False
            self.launcher.check_releases(self.app_id, self)

    def choose_build(self) -> None:
        """Look up the builds a release offers and let the person choose one."""
        app = self.current_app()
        if app is not None and not self.launcher.busy:
            self.inspect_build(app, "")

    def inspect_build(self, app: App, variant: str) -> None:
        target = replace(app, build_variant=variant)
        outcome: dict[str, Any] = {}
        cache_dir = self.launcher.library.data_dir / "release-cache"

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            try:
                outcome["release"] = self.launcher.library.following_moves(
                    target, lambda app: latest_release(app, cancel, cache_dir)
                )
            except BuildChoiceRequired as error:
                outcome["choices"] = error
                return TaskResult("Choose a build.")
            return TaskResult("Release inspected.")

        self.build_result = outcome
        self.build_target = target
        self.start("build", "Checking eligible releases and the published checksum…", operation)

    def find_existing_icons(self) -> None:
        app = self.current_app()
        if app is None or self.launcher.busy:
            return

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            icons = discover_icons(app, cancel, lambda text: progress(text, 0, 0))
            message = (
                f"Found {len(icons)} icon{'' if len(icons) == 1 else 's'}."
                if icons
                else "No icon found in the repository. Choose an image file instead."
            )
            return TaskResult(message, icons=icons)

        self.start("icons", f"Looking for icons for {app.name}…", operation)

    def task_done(self, result: TaskResult) -> None:
        """Receive the result of work this page started (the window has already handled it)."""
        kind, self.pending = self.pending, ""
        if self.launcher.close_after_task or self.app_id is None or self.pending_app != self.app_id:
            return
        if kind == "check":
            # A saved-preference note ends with the check it announced.
            self.message = "" if self.app_id in result.release_infos else result.message
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
            self.message_failed = True
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
                reason = describe_error(failure)
                launcher.footer.setText(f"Build preference could not be saved: {reason}")
                MessageBox.warning(self, "Cannot save the build", reason)
                return
        remember_release(target, release, launcher.library.data_dir / "release-cache")
        launcher.catalog_changed()
        installed = launcher.installed.get(app.id)
        if changed and installed and not release.newer_than(installed.version):
            self.message = (
                "Saved. Your current copy is unchanged; the new build is used from the next update."
            )
            launcher.footer.setText(self.message)
        else:
            launcher.footer.setText(
                "Build preference saved for future installs and updates. "
                "The installed copy was kept."
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
            chosen = choose_icon_candidate(self, [(icon.name, icon.png) for icon in icons])
            if chosen is None:
                return
            index = chosen
        self.save_icon(icons[index].png)

    def choose_icon(self) -> None:
        if self.current_app() is None or self.launcher.busy:
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
            MessageBox.warning(self, "Cannot use this icon", describe_error(error))
            return
        self.save_icon(png)

    def use_default_icon(self) -> None:
        if self.current_app() is not None and not self.launcher.busy:
            self.save_icon(None)

    def save_icon(self, png: bytes | None) -> None:
        app = self.current_app()
        if app is None:
            return
        try:
            self.launcher.library.set_icon(app, png)
        except (OSError, ValueError) as error:
            self.launcher.footer.setText(f"Icon could not be saved: {describe_error(error)}")
            MessageBox.warning(self, "Cannot save the icon", describe_error(error))
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
