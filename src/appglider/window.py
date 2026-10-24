"""Card library, installed applications and background installation controls."""

import json
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import Event

from PySide6.QtCore import (
    QByteArray,
    QEvent,
    QObject,
    QPointF,
    QRect,
    QRectF,
    QSettings,
    QSize,
    QStandardPaths,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QDesktopServices,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QResizeEvent,
    QTransform,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStyledItemDelegate,
    QSystemTrayIcon,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from appglider import (
    APP_ICON,
    APP_NAME,
    APP_TASKBAR_ICON,
    APP_UPDATE_ICON,
    APP_VERSION,
    github_status,
)
from appglider.catalog import App
from appglider.icons import icon_pixmap
from appglider.jobs import Task, TaskResult
from appglider.library import Installed, Library, Orphan
from appglider.release_checks import ReleaseChecks
from appglider.release_info import (
    ReleaseInfo,
    cached_info,
    discover_releases,
    info_path,
    remember_release,
)
from appglider.releases import (
    BuildChoiceRequired,
    Progress,
    Release,
    cached_release,
    check_cancel,
    file_stamp,
    github_request_status,
    latest_release,
    release_cache_path,
    restore_rate_limits,
    variant_label,
    version_key,
)
from appglider.repositories_dialog import RepositoriesPanel
from appglider.version_label import VersionLabel

CARD_MIN_WIDTH = 300
SCROLLBAR_GAP = 14


def icon_label(app: App, size: int, path: Path | None = None) -> QLabel:
    label = QLabel()
    label.setFixedSize(size, size)
    label.setPixmap(icon_pixmap(path or app.icon, size))
    label.setAccessibleName(f"{app.name} icon")
    return label


# Colour tokens per theme (see docs/design.md). "System" picks Light or Dark from the desktop.
THEMES: dict[str, dict[str, str]] = {
    "Dark": {
        "bg": "#15181e",
        "surface": "#1c2028",
        "surface2": "#232833",
        "raised": "#2a303c",
        "line": "#2f3542",
        "text": "#e8eaef",
        "muted": "#9aa1ae",
        "faint": "#6c7381",
        "ok": "#6fcf97",
        "warn": "#f0b860",
        "held": "#c7a6ff",
        "danger": "#ff8a8a",
    },
    "Light": {
        "bg": "#eef0f4",
        "surface": "#ffffff",
        "surface2": "#f5f6f9",
        "raised": "#eceef3",
        "line": "#dde0e7",
        "text": "#1b1f27",
        "muted": "#5d6573",
        "faint": "#8b92a0",
        "ok": "#1f8a4c",
        "warn": "#a8670a",
        "held": "#7044c4",
        "danger": "#c53030",
    },
}
# Accent -> (dark value, light value).
ACCENTS: dict[str, tuple[str, str]] = {
    "Blue": ("#8fb3ff", "#3563d8"),
    "Green": ("#6fcf97", "#1f8a4c"),
    "Amber": ("#d9a35b", "#9a6512"),
    "Clay": ("#c58b6a", "#9c5434"),
}
DEFAULT_ACCENT = "Blue"
# Retired themes -> (theme, accent) they are read as.
RETIRED_THEMES = {
    "Forest": ("Dark", "Green"),
    "Earthy": ("Dark", "Clay"),
    "Sandstone": ("Dark", "Amber"),
}
CHECK_INTERVALS = (
    ("Every hour", 1),
    ("Every 3 hours", 3),
    ("Every 6 hours", 6),
    ("Every 12 hours", 12),
    ("Every 24 hours", 24),
    ("Only when I choose", 0),
)
DEFAULT_CHECK_HOURS = 6
SORT_CHOICES = (
    ("Name (A–Z)", "name"),
    ("Installed first", "installed"),
    ("Updates first", "updates"),
    ("Order added", "added"),
)
DEFAULT_SORT = "name"
PIN_COLOUR = "#e6a817"  # gold, readable on both light and dark themes
MAX_PINNED = 100
SYSTEM_THEME = "System"
THEME_CHOICES = [SYSTEM_THEME, "Light", "Dark"]


def appearance_choice(settings: QSettings) -> tuple[str, str]:
    """Return the saved (theme, accent), mapping retired themes. Never writes settings."""
    theme = str(settings.value("appearance/theme", SYSTEM_THEME))
    accent = str(settings.value("appearance/accent", ""))
    if theme in RETIRED_THEMES:
        theme, retired_accent = RETIRED_THEMES[theme]
        accent = accent if accent in ACCENTS else retired_accent
    if theme not in THEME_CHOICES:
        theme = SYSTEM_THEME
    return theme, accent if accent in ACCENTS else DEFAULT_ACCENT


def check_interval_hours(settings: QSettings) -> int:
    """Hours between scheduled update checks; 0 means only when asked."""
    try:
        hours = int(str(settings.value("General/check-interval-hours", DEFAULT_CHECK_HOURS)))
    except ValueError:
        return DEFAULT_CHECK_HOURS
    return hours if hours in {value for _, value in CHECK_INTERVALS} else DEFAULT_CHECK_HOURS


def sort_mode(settings: QSettings) -> str:
    value = str(settings.value("library/sort", DEFAULT_SORT))
    return value if value in {mode for _, mode in SORT_CHOICES} else DEFAULT_SORT


def pinned_apps(settings: QSettings) -> set[str]:
    """Ids of the apps the user pinned to the top; anything unreadable counts as none."""
    try:
        data = json.loads(str(settings.value("library/pinned", "[]")))
    except ValueError:
        return set()
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        return set()
    return {item for item in data if len(item) <= 256}


def notified_versions(settings: QSettings) -> dict[str, str]:
    """Update versions the user was already told about, keyed by app id."""
    try:
        data = json.loads(str(settings.value("notifications/seen", "{}")))
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key: value
        for key, value in data.items()
        if isinstance(key, str) and isinstance(value, str) and len(key) <= 256 and len(value) <= 128
    }


class AppCard(QFrame):
    details_requested = Signal(str)
    launch_requested = Signal(str)
    install_requested = Signal(str)
    shortcut_requested = Signal(str)
    rollback_requested = Signal(str)
    hold_requested = Signal(str)
    remove_requested = Signal(str)
    preferences_requested = Signal(str)
    pin_requested = Signal(str)
    build_requested = Signal(str)

    def __init__(self, app: App, icon_path: Path | None = None) -> None:
        super().__init__()
        self.app = app
        self.installed: Installed | None = None
        self.build_required = False
        self.setObjectName("appCard")
        self.setMinimumWidth(CARD_MIN_WIDTH)
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(6)
        top = QHBoxLayout()
        self.icon = icon_label(app, 40, icon_path)
        top.addWidget(self.icon, alignment=Qt.AlignmentFlag.AlignTop)
        identity = QVBoxLayout()
        identity.setSpacing(3)
        name = QPushButton(app.name)
        self.name_button = name
        self.pinned = False
        name.setObjectName("appName")
        name.setAccessibleName(f"View {app.name} details")
        name.setToolTip(app.name)
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        name.setCursor(Qt.CursorShape.PointingHandCursor)
        name.clicked.connect(lambda: self.details_requested.emit(app.id))
        name_row = QHBoxLayout()
        name_row.setSpacing(4)
        self.pin_star = QLabel("★")
        self.pin_star.setObjectName("pinStar")
        self.pin_star.setToolTip("Pinned to the top")
        self.pin_star.setAccessibleName("Pinned to the top")
        self.pin_star.hide()
        name_row.addWidget(self.pin_star)
        name_row.addWidget(name, stretch=1)
        identity.addLayout(name_row)
        description = QLabel(app.description)
        description.setTextFormat(Qt.TextFormat.PlainText)
        description.setObjectName("cardDescription")
        description.setWordWrap(True)
        description.setMinimumWidth(0)
        identity.addWidget(description)
        top.addLayout(identity, stretch=1)
        self.update_button = QPushButton("Update")
        self.update_button.setObjectName("cardAction")
        self.update_button.setAccessibleName(f"Update {app.name}")
        self.update_button.clicked.connect(self.request_update)
        self.update_button.hide()
        more = QToolButton()
        more.setText("⋯")
        more.setObjectName("cardMenu")
        more.setAccessibleName(f"More actions for {app.name}")
        more.setToolTip(f"More actions for {app.name}")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        menu.addAction("View details").triggered.connect(
            lambda: self.details_requested.emit(app.id)
        )
        menu.addAction("Open repository").triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl(app.source_url))
        )
        self.pin_action = menu.addAction("Pin to top")
        self.pin_action.triggered.connect(lambda: self.pin_requested.emit(app.id))
        self.preferences_action = menu.addAction("Application preferences…")
        self.preferences_action.triggered.connect(lambda: self.preferences_requested.emit(app.id))
        self.hold_action = menu.addAction("Hold at current version")
        self.hold_action.triggered.connect(lambda: self.hold_requested.emit(app.id))
        menu.addSeparator()
        self.shortcut_action = menu.addAction("Create/repair menu shortcut")
        self.shortcut_action.triggered.connect(lambda: self.shortcut_requested.emit(app.id))
        self.rollback_action = menu.addAction("Roll back to previous version")
        self.rollback_action.triggered.connect(lambda: self.rollback_requested.emit(app.id))
        self.remove_action = menu.addAction("Uninstall…")
        self.remove_action.triggered.connect(lambda: self.remove_requested.emit(app.id))
        more.setMenu(menu)
        top.addWidget(more, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)
        layout.addStretch()
        divider = QFrame()
        divider.setObjectName("cardDivider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        self.available_version = VersionLabel()
        self.available_row = QHBoxLayout()
        self.available_row.setSpacing(8)
        self.available_row.addWidget(self.available_version, stretch=1)
        self.available_row.addWidget(self.update_button)
        layout.addLayout(self.available_row)
        self.installed_version = VersionLabel()
        self.status = self.installed_version.text
        self.installed_row = QHBoxLayout()
        self.installed_row.setSpacing(8)
        self.installed_row.addWidget(self.installed_version, stretch=1)
        layout.addLayout(self.installed_row)
        self.action_button = QPushButton()
        self.action_button.setObjectName("cardAction")
        self.action_button.clicked.connect(self.activate)
        self.available_row.addWidget(self.action_button)
        self.checked = QLabel("Release information not checked")
        self.checked.setObjectName("cardChecked")
        self.checked.setWordWrap(True)
        layout.addWidget(self.checked)

    def activate(self) -> None:
        if self.installed is not None:
            self.launch_requested.emit(self.app.id)
        else:
            self.install_requested.emit(self.app.id)

    def request_update(self) -> None:
        if self.build_required:
            self.build_requested.emit(self.app.id)
        else:
            self.install_requested.emit(self.app.id)

    def set_pinned(self, pinned: bool) -> None:
        if pinned == self.pinned:
            return
        self.pinned = pinned
        self.pin_star.setVisible(pinned)
        self.name_button.setAccessibleName(
            f"View {self.app.name} details" + (", pinned to the top" if pinned else "")
        )
        self.pin_action.setText("Unpin" if pinned else "Pin to top")

    def set_state(
        self,
        installed: Installed | None,
        release: Release | None,
        busy: bool,
        error: str = "",
        info: ReleaseInfo | None = None,
        refreshing: bool = False,
        refresh_error: str = "",
    ) -> None:
        self.installed = installed
        action_row = self.installed_row if installed else self.available_row
        if action_row.indexOf(self.action_button) < 0:
            action_row.addWidget(self.action_button)
        held = bool(installed and self.app.hold_updates)
        newer_release = bool(installed and release and release.newer_than(installed.version))
        newer = newer_release and not held
        choice = info.choice(self.app.include_prereleases) if info else None
        self.build_required = bool(choice and choice.build_required) and not held
        pending = ""
        if held and installed:
            if newer_release and release:
                pending = release.version
            elif (
                choice
                and choice.build_required
                and version_key(choice.version) > version_key(installed.version)
            ):
                pending = choice.version
        self.action_button.setText("Launch" if installed else "Install")
        self.action_button.setAccessibleName(
            f"{'Launch' if installed else 'Install'} {self.app.name}"
        )
        self.action_button.setEnabled(installed is not None or not busy)
        self.rollback_action.setEnabled(
            bool(
                not busy
                and installed
                and installed.managed
                and installed.previous
                and installed.previous.is_file()
            )
        )
        self.remove_action.setEnabled(bool(not busy and installed and installed.managed))
        self.hold_action.setText(
            "Release hold" if self.app.hold_updates else "Hold at current version"
        )
        self.hold_action.setEnabled(
            bool(not busy and (self.app.hold_updates or (installed and installed.managed)))
        )
        self.shortcut_action.setEnabled(bool(not busy and installed and installed.managed))
        self.update_button.setText("Choose build" if self.build_required else "Update")
        self.update_button.setVisible(bool(installed and self.build_required) or newer)
        self.update_button.setEnabled(not busy)
        self.preferences_action.setEnabled(not busy)
        waiting = bool(
            installed
            and installed.prerelease
            and not self.app.include_prereleases
            and (info is not None or release is not None)
            and not newer
            and (release is None or release.version != installed.version)
        )
        self.available_version.setVisible(bool(newer or not installed or self.build_required))
        if not installed:
            self.available_version.set_version("Available")
        elif release:
            self.available_version.set_version(
                f"v{release.version}",
                release.prerelease,
                release.is_latest,
                caption="Update" if newer else "Available",
            )
        else:
            choice = info.choice(self.app.include_prereleases) if info else None
            self.available_version.set_version(
                f"v{choice.version} unavailable"
                if choice
                else "Only previews available"
                if info and info.stable is None
                else "Release unavailable"
                if error or refresh_error
                else "Checking releases…"
                if refreshing
                else "Release not checked"
            )
        self.available_version.setToolTip(error if installed else "Release checked when installing")
        self.installed_version.setVisible(installed is not None)
        if installed:
            latest = info.stable.release if info and info.stable else release
            self.installed_version.set_version(
                f"v{installed.version}" if installed.version else "Unknown",
                installed.prerelease and not newer,
                bool(
                    not newer
                    and latest
                    and latest.version == installed.version
                    and latest.is_latest
                ),
                caption="Held" if held else "Installed",
            )
            self.status.setToolTip(f"Installed with AppGlider\n{installed.path}")
        stamp = datetime.fromtimestamp(info.checked_at).strftime("%d %b, %H:%M") if info else ""
        if refreshing:
            checked = "Refreshing releases…"
        elif refresh_error:
            checked = f"Refresh failed · checked {stamp}" if stamp else "Release check failed"
        elif held and pending:
            checked = f"Held · v{pending} available"
        elif self.build_required:
            checked = "Choose a build before updating"
        elif waiting:
            checked = "Waiting for a newer stable release"
        elif info:
            checked = f"Checked {stamp}" + (" · refresh needed" if not info.fresh() else "")
        else:
            checked = "Release information not checked"
        self.checked.setText(checked)
        self.checked.setVisible(installed is not None)
        self.checked.setToolTip(
            refresh_error or error or (f"Last checked {stamp}" if stamp else "")
        )
        self.action_button.setToolTip(
            f"Launch {installed.path}"
            if installed
            else "Check the eligible release, then download and verify its AppImage"
        )
        if release:
            self.update_button.setToolTip(
                f"Update to v{release.version}; keep the previous version"
            )


class CardGrid(QWidget):
    """Reflow existing cards without losing keyboard focus."""

    def __init__(self) -> None:
        super().__init__()
        self.cards: list[AppCard] = []
        self.columns = 0
        self.layout_grid = QGridLayout(self)
        self.layout_grid.setContentsMargins(0, 0, SCROLLBAR_GAP, 0)
        self.layout_grid.setSpacing(16)
        self.layout_grid.setAlignment(Qt.AlignmentFlag.AlignTop)

    def set_cards(self, cards: list[AppCard], width: int) -> None:
        self.cards = cards
        self.reflow(width)

    def reflow(self, width: int) -> None:
        while self.layout_grid.count():
            self.layout_grid.takeAt(0)
        for column in range(self.columns):
            self.layout_grid.setColumnStretch(column, 0)
        for row in range(self.layout_grid.rowCount()):
            self.layout_grid.setRowStretch(row, 0)
        self.columns = max(1, (width - SCROLLBAR_GAP + 16) // (CARD_MIN_WIDTH + 16))
        for index, card in enumerate(self.cards):
            self.layout_grid.addWidget(card, index // self.columns, index % self.columns)
        for column in range(self.columns):
            self.layout_grid.setColumnStretch(column, 1)
        rows = (len(self.cards) + self.columns - 1) // self.columns
        self.layout_grid.setRowStretch(rows, 1)


class DetailsDialog(QDialog):
    check_requested = Signal()

    def __init__(
        self,
        app: App,
        parent: QWidget,
        installed: Installed | None = None,
        release: Release | None = None,
        error: str = "",
        icon_path: Path | None = None,
        release_info: ReleaseInfo | None = None,
    ) -> None:
        super().__init__(parent)
        self.app = app
        self.installed = installed
        self.setWindowTitle(f"About {app.name}")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 24)
        layout.setSpacing(18)
        heading = QHBoxLayout()
        heading.addWidget(icon_label(app, 64, icon_path))
        title = QLabel(app.name)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setObjectName("pageTitle")
        heading.addWidget(title)
        heading.addStretch()
        layout.addLayout(heading)
        description = QLabel(app.description)
        description.setTextFormat(Qt.TextFormat.PlainText)
        description.setWordWrap(True)
        layout.addWidget(description)
        info = QFormLayout()
        repo = QLabel(app.repo)
        repo.setTextFormat(Qt.TextFormat.PlainText)
        repo.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info.addRow("Repository", repo)
        info.addRow("Format", QLabel("Linux AppImage"))
        if app.build_variant:
            build = QLabel(variant_label(app.build_variant))
            build.setTextFormat(Qt.TextFormat.PlainText)
            build.setWordWrap(True)
            info.addRow("Selected build", build)
        self.installed_label = VersionLabel()
        info.addRow("Installed version", self.installed_label)
        self.available_label = VersionLabel()
        info.addRow("Available release", self.available_label)
        info.addRow(
            "Release preference",
            QLabel(
                "Stable and prerelease versions"
                if app.include_prereleases
                else "Stable releases only"
            ),
        )
        self.hold_label = QLabel()
        self.hold_label.setTextFormat(Qt.TextFormat.PlainText)
        info.addRow("Updates", self.hold_label)
        self.stable_label = VersionLabel()
        self.preview_label = VersionLabel()
        info.addRow("Stable release", self.stable_label)
        info.addRow("Preview release", self.preview_label)
        self.release_form = info
        if installed:
            location = QLabel(str(installed.path))
            location.setWordWrap(True)
            location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            info.addRow("Location", location)
            filename = QLabel(installed.path.name)
            filename.setTextFormat(Qt.TextFormat.PlainText)
            filename.setWordWrap(True)
            info.addRow("Installed file", filename)
        layout.addLayout(info)
        self.note = QLabel()
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.check_button = buttons.addButton(
            "Check releases", QDialogButtonBox.ButtonRole.ActionRole
        )
        self.check_button.clicked.connect(self.check_requested.emit)
        source = buttons.addButton("Open repository", QDialogButtonBox.ButtonRole.ActionRole)
        source.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(app.source_url)))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.set_release_info(release, release_info, error)

    def set_release_info(
        self, release: Release | None, info: ReleaseInfo | None, error: str = ""
    ) -> None:
        installed = self.installed
        latest = info.stable.release if info and info.stable else release
        self.installed_label.set_version(
            installed.version or "Unknown" if installed else "Not installed",
            bool(installed and installed.prerelease),
            bool(installed and latest and installed.version == latest.version and latest.is_latest),
        )
        self.available_label.set_version(
            release.version if release else "Not checked" if info is None else "Unavailable",
            bool(release and release.prerelease),
            bool(release and release.is_latest),
        )
        self.available_label.setToolTip(release.name if release else "")
        held = bool(installed and self.app.hold_updates)
        self.release_form.setRowVisible(self.hold_label, held)
        if held and installed:
            text = f"Held at v{installed.version or 'unknown'}"
            choice = info.choice(self.app.include_prereleases) if info else None
            if release and release.newer_than(installed.version):
                text += f" · v{release.version} available"
            elif (
                choice
                and choice.build_required
                and version_key(choice.version) > version_key(installed.version)
            ):
                text += f" · v{choice.version} available"
            self.hold_label.setText(text)
        for label, option in (
            (self.stable_label, info.stable if info else None),
            (self.preview_label, info.preview if info else None),
        ):
            self.release_form.setRowVisible(
                label, option is not None and (release is None or option.version != release.version)
            )
            if option:
                label.set_version(
                    option.version + (" · unavailable" if option.release is None else ""),
                    bool(option.release and option.release.prerelease),
                    bool(option.release and option.release.is_latest),
                )
                label.setToolTip(option.error)
        self.note.setText(
            error
            or "Check releases to inspect stable and preview versions without downloading an app."
        )

    def release_check_finished(self, result: TaskResult) -> None:
        info = result.release_infos.get(self.app.id)
        if info:
            choice = info.choice(self.app.include_prereleases)
            self.set_release_info(
                choice.release if choice else None, info, info.error(self.app.include_prereleases)
            )
        else:
            self.note.setText(result.message)
        self.check_button.setEnabled(True)
        self.check_button.setText("Check releases")

    def release_check_failed(self, error: str) -> None:
        self.note.setText(error)
        self.check_button.setEnabled(True)
        self.check_button.setText("Check releases")


class OrphansDialog(QDialog):
    """Offer to forget library entries for unknown apps; keeping them changes nothing."""

    def __init__(self, orphans: list[Orphan], parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Unrecognised library entries")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "These entries are in your AppGlider library but match no application in the "
            "current catalog, for example a repository that was removed. They are kept "
            "untouched unless you tick them. Forgetting an entry never deletes files."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.entries = QListWidget()
        self.entries.setAccessibleName("Unrecognised entries")
        for orphan in orphans:
            version = f" v{orphan.version}" if orphan.version else ""
            item = QListWidgetItem(f"{orphan.app_id}{version} · {orphan.path}")
            item.setData(Qt.ItemDataRole.UserRole, orphan.app_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.entries.addItem(item)
        layout.addWidget(self.entries)
        buttons = QDialogButtonBox()
        self.forget_button = buttons.addButton(
            "Forget ticked entries", QDialogButtonBox.ButtonRole.AcceptRole
        )
        buttons.addButton("Keep all", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected(self) -> set[str]:
        return {
            str(item.data(Qt.ItemDataRole.UserRole))
            for row in range(self.entries.count())
            if (item := self.entries.item(row)).checkState() == Qt.CheckState.Checked
        }


def fit_size(width: int, height: int, available: QRect) -> QSize:
    """Limit a preferred window size to 90% of the screen's usable area."""
    return QSize(min(width, available.width() * 9 // 10), min(height, available.height() * 9 // 10))


def screen_area(widget: QWidget | None) -> QRect:
    screen = widget.screen() if widget is not None else QApplication.primaryScreen()
    return screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1080)


def glyph_path(name: str, colour: str, points: list[QPointF]) -> str:
    """Return a cached stroked-line glyph in the given colour, for stylesheet images."""
    folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation))
    path = folder / f"{name}-{colour.lstrip('#')}.png"
    if not path.exists():
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(colour), 2.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline(points)
        painter.end()
        folder.mkdir(parents=True, exist_ok=True)
        pixmap.save(str(path))
    return path.as_posix()


def chevron_path(colour: str) -> str:
    return glyph_path("chevron", colour, [QPointF(7, 9.5), QPointF(12, 14.5), QPointF(17, 9.5)])


def tick_path(colour: str) -> str:
    return glyph_path("tick", colour, [QPointF(6, 12.5), QPointF(10.5, 17), QPointF(18, 7.5)])


def cog_icon(colour: str) -> QIcon:
    """Draw a settings cog in the given colour."""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    cog = QPainterPath()
    cog.setFillRule(Qt.FillRule.WindingFill)
    centre = QPointF(size / 2, size / 2)
    cog.addEllipse(centre, 21, 21)
    for step in range(8):
        tooth = QPainterPath()
        tooth.addRoundedRect(QRectF(size / 2 - 6, 4, 12, 18), 3, 3)
        transform = QTransform()
        transform.translate(centre.x(), centre.y())
        transform.rotate(step * 45)
        transform.translate(-centre.x(), -centre.y())
        cog.addPath(transform.map(tooth))
    hole = QPainterPath()
    hole.addEllipse(centre, 9, 9)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.fillPath(cog.simplified().subtracted(hole), QColor(colour))
    painter.end()
    return QIcon(pixmap)


class SettingsDialog(QDialog):
    def __init__(self, settings: QSettings, parent: QWidget, library: Library) -> None:
        super().__init__(parent)
        self.pending: Callable[[], None] | None = None
        self.setWindowTitle("Settings")
        self.setMinimumSize(860, 620)
        self.resize(fit_size(1040, 800, screen_area(parent)))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)
        title = QLabel("Settings")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        content = QHBoxLayout()
        self.sections = QListWidget()
        self.sections.setObjectName("settingsSections")
        self.sections.setAccessibleName("Settings sections")
        self.sections.addItems(["General", "Applications", "Storage", "GitHub", "About"])
        self.sections.setFixedWidth(150)
        self.pages = QStackedWidget()
        self.sections.currentRowChanged.connect(self.pages.setCurrentIndex)
        content.addWidget(self.sections)
        content.addWidget(self.pages, stretch=1)
        layout.addLayout(content, stretch=1)
        page_layouts = []
        for _ in range(5):
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(12, 0, 0, 0)
            page_layout.setSpacing(16)
            self.pages.addWidget(page)
            page_layouts.append(page_layout)
        general, applications_page, storage, github_page, about = page_layouts
        applications_page.setContentsMargins(12, 0, 0, 0)
        appearance = QGroupBox("Appearance")
        appearance_form = QFormLayout(appearance)
        self.theme = QComboBox()
        self.theme.setItemDelegate(QStyledItemDelegate(self.theme))
        self.theme.setAccessibleName("Appearance")
        self.theme.addItems(THEME_CHOICES)
        current_theme, current_accent = appearance_choice(settings)
        self.theme.setCurrentText(current_theme)
        appearance_form.addRow("Theme", self.theme)
        self.accent = QComboBox()
        self.accent.setItemDelegate(QStyledItemDelegate(self.accent))
        self.accent.setAccessibleName("Accent colour")
        self.accent.addItems(list(ACCENTS))
        self.accent.setCurrentText(current_accent)
        appearance_form.addRow("Accent colour", self.accent)
        general.addWidget(appearance)
        window_group = QGroupBox("Window")
        window_layout = QVBoxLayout(window_group)
        self.close_to_tray = QCheckBox("Keep AppGlider in the tray when closing its window")
        self.close_to_tray.setChecked(
            bool(settings.value("General/close-to-tray", True, type=bool))
        )
        self.close_to_tray.setToolTip("Applies when a system tray is available. Use Quit to exit.")
        window_layout.addWidget(self.close_to_tray)
        general.addWidget(window_group)
        updates = QGroupBox("Updates")
        updates_layout = QVBoxLayout(updates)
        updates_form = QFormLayout()
        self.check_interval = QComboBox()
        self.check_interval.setItemDelegate(QStyledItemDelegate(self.check_interval))
        self.check_interval.setAccessibleName("Check for updates")
        for label, hours in CHECK_INTERVALS:
            self.check_interval.addItem(label, hours)
        self.check_interval.setCurrentIndex(
            max(0, self.check_interval.findData(check_interval_hours(settings)))
        )
        updates_form.addRow("Check for updates", self.check_interval)
        updates_layout.addLayout(updates_form)
        self.notify_updates = QCheckBox("Show a desktop notification when new updates are found")
        self.notify_updates.setChecked(
            bool(settings.value("notifications/updates", False, type=bool))
        )
        self.notify_updates.setToolTip(
            "Only for the scheduled checks, while AppGlider is in the background, and once "
            "for each new version."
        )
        updates_layout.addWidget(self.notify_updates)
        updates_note = QLabel(
            "Checks the apps you have installed while AppGlider is running, including in the "
            "tray. Checking an app that has not changed does not use GitHub's request allowance "
            "(apps with previews turned on still use some), and checks are skipped when few "
            "requests remain."
        )
        updates_note.setObjectName("muted")
        updates_note.setWordWrap(True)
        updates_layout.addWidget(updates_note)
        general.addWidget(updates)
        downloads = QGroupBox("Downloads")
        download_layout = QVBoxLayout(downloads)
        download_layout.addWidget(QLabel("Install new applications in"))
        default_folder = str(
            Path(
                QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)
            )
            / "appglider"
            / "apps"
        )
        self.folder = QLineEdit(str(settings.value("installation/folder", default_folder)))
        self.folder.setAccessibleName("AppImage folder")
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse)
        folder_row.addWidget(browse)
        download_layout.addLayout(folder_row)
        note = QLabel("Existing applications stay in their current folders.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        download_layout.addWidget(note)
        storage.addWidget(downloads)
        self.repositories = RepositoriesPanel(library)
        self.repositories.stopped.connect(self.finish_pending)
        applications_page.addWidget(self.repositories, stretch=1)
        github = QGroupBox("GitHub requests")
        github_layout = QVBoxLayout(github)
        self.github_details = QLabel()
        self.github_details.setTextFormat(Qt.TextFormat.PlainText)
        self.github_details.setWordWrap(True)
        self.github_details.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.github_details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.github_details.setAccessibleName("GitHub request status")
        github_layout.addWidget(self.github_details)
        github_note = QLabel(
            "Status updates from existing requests; opening Settings makes no requests. "
            "Other applications on your network may share the request allowance."
        )
        github_note.setObjectName("muted")
        github_note.setWordWrap(True)
        github_layout.addWidget(github_note)
        github_page.addWidget(github)
        self.github_timer = QTimer(self)
        self.github_timer.timeout.connect(self.update_github_status)
        self.finished.connect(self.github_timer.stop)
        self.github_timer.start(1000)
        self.update_github_status()
        app_title = QLabel(f"{APP_NAME} {APP_VERSION}")
        app_title.setObjectName("sectionTitle")
        about.addWidget(app_title)
        about_note = QLabel("A home for your Linux AppImages.\nWritten in Python with Qt.")
        about_note.setWordWrap(True)
        about.addWidget(about_note)
        for caption, url in (
            ("View AppGlider on GitHub", "https://github.com/Vyrnexis/AppGlider"),
            ("Report an issue", "https://github.com/Vyrnexis/AppGlider/issues"),
        ):
            button = QPushButton(caption)
            button.clicked.connect(
                lambda checked=False, url=url: QDesktopServices.openUrl(QUrl(url))
            )
            about.addWidget(button)
        for page_layout in page_layouts:
            if page_layout is not applications_page:
                page_layout.addStretch()
        self.sections.setCurrentRow(0)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.save_button.setObjectName("primary")
        self.close_button = buttons.addButton("Close", QDialogButtonBox.ButtonRole.AcceptRole)
        self.close_button.setObjectName("primary")
        layout.addWidget(buttons)
        self.sections.currentRowChanged.connect(self.update_buttons)
        self.update_buttons(self.sections.currentRow())

    def update_github_status(self) -> None:
        self.github_details.setText(github_request_status()[1])

    def browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose AppImage folder", self.folder.text()
        )
        if folder:
            self.folder.setText(folder)

    def update_buttons(self, section: int) -> None:
        """Save and Cancel apply to General and Storage; the other pages save as you go."""
        editing = section in (0, 2)
        self.save_button.setVisible(editing)
        self.cancel_button.setVisible(editing)
        self.close_button.setVisible(not editing)

    def reject(self) -> None:
        if self.repositories.stop():
            super().reject()
        else:
            self.pending = super().reject
            self.stopping()

    def stopping(self) -> None:
        self.setEnabled(False)

    def finish_pending(self) -> None:
        self.setEnabled(True)
        pending, self.pending = self.pending, None
        if pending is not None:
            pending()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.repositories.task is not None:
            self.reject()
            event.ignore()
            return
        super().closeEvent(event)

    def accept(self) -> None:
        if not self.repositories.stop():
            self.pending = self.accept
            self.stopping()
            return
        if any(ord(c) < 32 or ord(c) == 127 for c in self.folder.text()):
            QMessageBox.warning(self, "Choose a folder", "The path contains control characters.")
            return
        folder = Path(self.folder.text().strip()).expanduser()
        if not self.folder.text().strip() or not folder.is_absolute():
            QMessageBox.warning(self, "Choose a folder", "Enter an absolute path or use Browse.")
            return
        if folder.exists() and not folder.is_dir():
            QMessageBox.warning(self, "Choose a folder", "The selected path is a file.")
            return
        self.folder.setText(str(folder))
        super().accept()


class LauncherWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None, library: Library | None = None) -> None:
        super().__init__()
        self.settings = settings if settings is not None else QSettings()
        data_dir = (
            Path(
                QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)
            )
            / "appglider"
        )
        self.library = (
            library
            if library is not None
            else Library(
                data_dir,
                Path(str(self.settings.value("installation/folder", str(data_dir / "apps")))),
            )
        )
        self.installed: dict[str, Installed] = {}
        self.apps: tuple[App, ...] = ()
        self.pinned = pinned_apps(self.settings)
        self.releases: dict[str, Release] = {}
        self.check_errors: dict[str, str] = {}
        self.release_infos: dict[str, ReleaseInfo] = {}
        self.cache_stamps: dict[str, object] = {}
        self.tray_key: list[tuple[str, str, Path, tuple[int, int, int] | None]] | None = None
        self.release_errors: dict[str, str] = {}
        self.checks = ReleaseChecks(
            self.checkable_app,
            self.release_operation,
            lambda: 3 if github_status.quota_plentiful() else 1,
        )
        self.checks.started.connect(self.release_check_started)
        self.checks.completed.connect(self.release_check_done)
        self.checks.drained.connect(self.release_checks_drained)
        self.background_enabled = False
        self.close_after_release = False
        self.task: Task | None = None
        self.close_after_task = False
        self.quitting = False
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(str(APP_TASKBAR_ICON)))
        self.resize(fit_size(1040, 816, screen_area(None)))
        saved = self.settings.value("window/geometry")
        if isinstance(saved, QByteArray | bytes):
            self.restoreGeometry(QByteArray(saved))
        self.setMinimumSize(720, 520)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(32, 28, 32, 20)
        layout.setSpacing(20)
        self.setCentralWidget(body)
        header = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(QIcon(str(APP_ICON)).pixmap(96, 96))
        logo.setFixedSize(96, 96)
        logo.setAccessibleName(APP_NAME)
        header.addWidget(logo)
        titles = QVBoxLayout()
        title = QLabel(APP_NAME)
        title.setObjectName("pageTitle")
        titles.addWidget(title)
        subtitle = QLabel("One place for your Linux AppImages.")
        subtitle.setObjectName("muted")
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch()
        self.version_button = QPushButton(f"v{APP_VERSION}")
        self.version_button.setObjectName("versionButton")
        self.version_button.setAccessibleName(f"About AppGlider {APP_VERSION}")
        self.version_button.setToolTip("About AppGlider")
        self.version_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.version_button.clicked.connect(lambda: self.open_settings(4))
        self.settings_button = QToolButton()
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setAccessibleName("Settings")
        self.settings_button.setToolTip("Settings")
        self.settings_button.setIconSize(QSize(22, 22))
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_button.clicked.connect(lambda: self.open_settings())
        header.addWidget(self.settings_button, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search apps…")
        self.search.setAccessibleName("Search apps")
        self.search.setClearButtonEnabled(True)
        search_row.addWidget(self.search, stretch=1)
        self.add_app_button = QPushButton("+ Add app")
        self.add_app_button.setObjectName("primary")
        self.add_app_button.clicked.connect(lambda: self.open_repositories(adding=True))
        search_row.addWidget(self.add_app_button)
        layout.addLayout(search_row)
        self.search.setFocus()
        navigation = QHBoxLayout()
        self.tabs = QTabBar()
        self.tabs.setAccessibleName("Library filters")
        self.tabs.setDrawBase(False)
        for name in ("All apps", "Installed", "Updates"):
            self.tabs.addTab(name)
        navigation.addWidget(self.tabs)
        navigation.addStretch()
        layout.addLayout(navigation)
        list_row = QHBoxLayout()
        self.results = QLabel()
        self.results.setObjectName("muted")
        list_row.addWidget(self.results, stretch=1)
        self.sort_combo = QComboBox()
        self.sort_combo.setItemDelegate(QStyledItemDelegate(self.sort_combo))
        self.sort_combo.setAccessibleName("Sort apps")
        self.sort_combo.setToolTip(
            "Choose how the apps are ordered. Pinned apps always come first."
        )
        for label, mode in SORT_CHOICES:
            self.sort_combo.addItem(f"Sort: {label}", mode)
        self.sort_combo.setCurrentIndex(max(0, self.sort_combo.findData(sort_mode(self.settings))))
        self.sort_combo.currentIndexChanged.connect(self.sort_changed)
        self.sort_combo.setObjectName("compact")
        list_row.addWidget(self.sort_combo)
        self.update_all_button = QPushButton("Update all")
        self.update_all_button.clicked.connect(self.update_all)
        self.update_all_button.setObjectName("compact")
        list_row.addWidget(self.update_all_button)
        self.check_button = QPushButton("Refresh releases")
        self.check_button.setToolTip(
            "Check installed applications for eligible stable and preview updates"
        )
        self.check_button.clicked.connect(lambda: self.refresh_releases(force=True))
        self.check_button.setObjectName("compact")
        list_row.addWidget(self.check_button)
        layout.addLayout(list_row)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid = CardGrid()
        self.scroll_area.setWidget(self.grid)
        layout.addWidget(self.scroll_area, stretch=1)
        self.empty = QLabel()
        self.empty.setObjectName("emptyState")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty, stretch=1)
        self.welcome = QWidget()
        welcome_layout = QVBoxLayout(self.welcome)
        welcome_layout.setSpacing(16)
        welcome_layout.setContentsMargins(120, 0, 120, 0)
        welcome_layout.addStretch()
        welcome_title = QLabel("Welcome to AppGlider")
        welcome_title.setObjectName("pageTitle")
        welcome_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_layout.addWidget(welcome_title)
        welcome_text = QLabel(
            "Your library is empty. Add an app from a public GitHub repository, import a "
            "catalog (a plain-text list of repositories you can read before adding it), or "
            "start from the ready-made example list of ArtCraft apps and SolidCraft."
        )
        welcome_text.setObjectName("emptyState")
        welcome_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_text.setWordWrap(True)
        welcome_layout.addWidget(welcome_text)
        welcome_buttons = QHBoxLayout()
        welcome_buttons.addStretch()
        self.welcome_add_button = QPushButton("+ Add app")
        self.welcome_add_button.setObjectName("primary")
        self.welcome_add_button.clicked.connect(lambda: self.open_repositories(adding=True))
        welcome_buttons.addWidget(self.welcome_add_button)
        self.welcome_import_button = QPushButton("Import catalog…")
        self.welcome_import_button.clicked.connect(
            lambda: self.open_repositories(import_catalog=True)
        )
        welcome_buttons.addWidget(self.welcome_import_button)
        self.welcome_example_button = QPushButton("Example catalog")
        self.welcome_example_button.setToolTip(
            "Add the ArtCraft apps and SolidCraft, a ready-made example list."
        )
        self.welcome_example_button.clicked.connect(
            lambda: self.open_repositories(import_example=True)
        )
        welcome_buttons.addWidget(self.welcome_example_button)
        welcome_buttons.addStretch()
        welcome_layout.addLayout(welcome_buttons)
        welcome_layout.addStretch()
        layout.addWidget(self.welcome, stretch=1)
        self.footer = QLabel("Loading your library…")
        self.footer.setObjectName("footer")
        self.footer.setTextFormat(Qt.TextFormat.PlainText)
        self.footer.setWordWrap(True)
        progress_row = QHBoxLayout()
        progress_row.addWidget(self.footer, stretch=1)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(170)
        self.progress_bar.hide()
        progress_row.addWidget(self.progress_bar)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_task)
        self.cancel_button.hide()
        progress_row.addWidget(self.cancel_button)
        self.orphans: list[Orphan] = []
        self.orphans_button = QPushButton()
        self.orphans_button.clicked.connect(self.review_orphans)
        self.orphans_button.hide()
        progress_row.addWidget(self.orphans_button)
        self.github_button = QPushButton()
        self.github_button.setObjectName("githubStatus")
        self.github_button.setAccessibleName("GitHub request status")
        self.github_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.github_button.clicked.connect(lambda: self.open_settings(3))
        progress_row.addWidget(self.github_button)
        progress_row.addWidget(self.version_button)
        layout.addLayout(progress_row)
        self.cards: dict[str, AppCard] = {}
        self.search.textChanged.connect(self.refresh)
        self.tabs.currentChanged.connect(self.refresh)
        QApplication.styleHints().colorSchemeChanged.connect(self.apply_theme)
        self.scroll_area.viewport().installEventFilter(self)
        self.tray_icons = (QIcon(str(APP_ICON)), QIcon(str(APP_UPDATE_ICON)))
        self.tray = QSystemTrayIcon(self.tray_icons[0], self)
        self.tray_has_updates = False
        self.tray_menu = QMenu(self)
        self.tray_menu.addAction("Open AppGlider").triggered.connect(self.show_launcher)
        self.tray_check_action = self.tray_menu.addAction("Refresh releases")
        self.tray_check_action.triggered.connect(lambda: self.refresh_releases(force=True))
        self.tray_apps_menu = self.tray_menu.addMenu("Installed apps")
        self.tray_menu.addSeparator()
        self.tray_menu.addAction("Quit AppGlider").triggered.connect(self.quit_launcher)
        self.tray_menu.aboutToShow.connect(self.scan_installed)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(self.tray_activated)
        self.tray.messageClicked.connect(self.show_launcher)
        self.notify_pending = False
        self.notified = notified_versions(self.settings)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.apply_theme()
        restore_rate_limits(self.library.data_dir / "release-cache")
        self.github_timer = QTimer(self)
        self.github_timer.timeout.connect(self.update_github_status)
        self.github_timer.start(1000)
        self.update_github_status()
        self.scan_installed()
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.scheduled_check)
        self.schedule_update_checks()

    def schedule_update_checks(self) -> None:
        hours = check_interval_hours(self.settings)
        if hours:
            self.update_timer.start(hours * 60 * 60 * 1000)
        else:
            self.update_timer.stop()

    def scheduled_check(self) -> None:
        """Check installed apps on the chosen schedule, without disturbing a running task."""
        if (
            self.task is None
            and not self.checks.busy
            and not self.close_after_release
            and not self.quitting
            and self.installed
            and not github_status.quota_low()
        ):
            self.notify_pending = True
            self.refresh_releases()
            self.notify_pending = self.notify_pending and self.checks.busy

    def notify_updates(self) -> None:
        """Tell the user, once per new version, that updates are waiting (opt-in)."""
        waiting = {
            app.id: release.version
            for app in self.apps
            if app.id in self.installed
            and self.has_update(app.id)
            and (release := self.releases.get(app.id)) is not None
        }
        new = [
            app for app in self.apps if waiting.get(app.id) not in (None, self.notified.get(app.id))
        ]
        self.notified = dict(waiting)
        self.settings.setValue("notifications/seen", json.dumps(self.notified))
        if (
            not new
            or not self.settings.value("notifications/updates", False, type=bool)
            or not QSystemTrayIcon.isSystemTrayAvailable()
            or not self.tray.isVisible()
            or (self.isVisible() and self.isActiveWindow())
        ):
            return
        names = [f"{app.name} {waiting[app.id]}" for app in new]
        if len(names) == 1:
            text = f"{names[0]} is available."
        else:
            more = f", and {len(names) - 3} more" if len(names) > 3 else ""
            text = f"{len(names)} updates are waiting: {', '.join(names[:3])}{more}."
        self.tray.showMessage("AppGlider: updates available", text, self.tray_icons[1], 15000)

    def update_github_status(self) -> None:
        label, details, limited = github_request_status()
        self.github_button.setText(label)
        self.github_button.setToolTip(details + "\nClick for details in Settings → GitHub.")
        if self.github_button.property("limited") != limited:
            self.github_button.setProperty("limited", limited)
            self.github_button.style().unpolish(self.github_button)
            self.github_button.style().polish(self.github_button)

    def create_card(self, app: App) -> AppCard:
        card = AppCard(app, self.library.icon_path(app))
        card.setParent(self.grid)
        card.details_requested.connect(self.open_details)
        card.launch_requested.connect(self.launch_app)
        card.install_requested.connect(self.install_app)
        card.shortcut_requested.connect(self.create_shortcut)
        card.rollback_requested.connect(self.rollback_app)
        card.remove_requested.connect(self.remove_app)
        card.preferences_requested.connect(lambda app_id: self.open_repositories(app_id=app_id))
        card.pin_requested.connect(self.toggle_pin)
        card.hold_requested.connect(self.toggle_hold)
        card.build_requested.connect(self.choose_build)
        return card

    def sorted_apps(self, apps: Iterable[App]) -> list[App]:
        """Pinned apps first, then the chosen order; ties keep the order they were added in."""
        mode = sort_mode(self.settings)
        order = {app.id: index for index, app in enumerate(self.apps)}

        def key(app: App) -> tuple[object, ...]:
            installed = app.id in self.installed
            tail: tuple[object, ...] = {
                "name": (app.name.casefold(),),
                "installed": (not installed, app.name.casefold()),
                "updates": (not self.has_update(app.id), not installed, app.name.casefold()),
                "added": (),
            }[mode]
            return (app.id not in self.pinned, *tail, order.get(app.id, 0))

        return sorted(apps, key=key)

    def sort_changed(self) -> None:
        self.settings.setValue("library/sort", self.sort_combo.currentData())
        self.refresh()

    def toggle_pin(self, app_id: str) -> None:
        if app_id in self.pinned:
            self.pinned.discard(app_id)
        elif len(self.pinned) < MAX_PINNED:
            self.pinned.add(app_id)
        self.settings.setValue("library/pinned", json.dumps(sorted(self.pinned)))
        self.refresh()

    def toggle_hold(self, app_id: str) -> None:
        app = next((app for app in self.apps if app.id == app_id), None)
        if app is None or self.task is not None:
            return
        held = not app.hold_updates
        try:
            self.library.set_update_hold(app_id, held)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Cannot change update hold", str(error))
            return
        self.scan_installed()
        self.footer.setText(
            f"{app.name} is held at its current version."
            if held
            else f"{app.name} is no longer held."
        )

    def show_launcher(self) -> None:
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_launcher()

    def quit_launcher(self) -> None:
        self.show_launcher()
        self.quitting = True
        if not self.close() and not self.close_after_task and not self.close_after_release:
            self.quitting = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.scroll_area.viewport() and isinstance(event, QResizeEvent):
            width = event.size().width()
            if max(1, (width + 16) // (CARD_MIN_WIDTH + 16)) != self.grid.columns:
                self.grid.reflow(width)
        return super().eventFilter(watched, event)

    def refresh(self) -> None:
        update_count = sum(self.has_update(app.id) for app in self.apps)
        self.tabs.setTabText(1, f"Installed ({len(self.installed)})")
        self.tabs.setTabText(2, f"Updates ({update_count})")
        self.tray.setToolTip(f"AppGlider · Updates: {update_count}")
        if (update_count > 0) != self.tray_has_updates:
            self.tray_has_updates = update_count > 0
            self.tray.setIcon(self.tray_icons[int(self.tray_has_updates)])
        self.tray_check_action.setEnabled(not self.checks.busy and bool(self.installed))
        tray_apps = [
            (app, self.library.icon_path(app))
            for app in self.sorted_apps(app for app in self.apps if app.id in self.installed)
        ]
        tray_key = [(app.id, app.name, path, file_stamp(path)) for app, path in tray_apps]
        if tray_key != self.tray_key:
            self.tray_key = tray_key
            self.tray_apps_menu.clear()
            self.tray_apps_menu.setEnabled(bool(tray_apps))
            for app, path in tray_apps:
                action = self.tray_apps_menu.addAction(QIcon(icon_pixmap(path, 64)), app.name)
                action.triggered.connect(
                    lambda checked=False, app_id=app.id: self.launch_app(app_id)
                )
        query = self.search.text().strip().casefold()
        visible = [
            app
            for app in self.apps
            if (
                self.tabs.currentIndex() == 0
                or (self.tabs.currentIndex() == 1 and app.id in self.installed)
                or (self.tabs.currentIndex() == 2 and self.has_update(app.id))
            )
            and query in f"{app.name} {app.description} {app.repo}".casefold()
        ]
        visible = self.sorted_apps(visible)
        ids = {app.id for app in visible}
        for app_id, card in self.cards.items():
            card.icon.setPixmap(icon_pixmap(self.library.icon_path(card.app), 40))
            card.set_pinned(app_id in self.pinned)
            card.set_state(
                self.installed.get(app_id),
                self.releases.get(app_id),
                self.task is not None,
                self.check_errors.get(app_id, ""),
                self.release_infos.get(app_id),
                self.checks.is_checking(app_id),
                self.release_errors.get(app_id, ""),
            )
            card.setVisible(app_id in ids)
        self.add_app_button.setEnabled(self.task is None)
        self.check_button.setText("Refreshing…" if self.checks.busy else "Refresh releases")
        self.check_button.setEnabled(not self.checks.busy and bool(self.installed))
        self.update_all_button.setVisible(
            any(
                item.managed and app_id in self.releases and self.has_update(app_id)
                for app_id, item in self.installed.items()
            )
        )
        self.update_all_button.setEnabled(self.task is None)
        self.grid.set_cards(
            [self.cards[app.id] for app in visible], self.scroll_area.viewport().width()
        )
        self.scroll_area.setVisible(bool(visible))
        self.empty.setVisible(not visible and bool(self.apps))
        self.welcome.setVisible(not self.apps)
        self.welcome_add_button.setEnabled(self.task is None)
        self.welcome_import_button.setEnabled(self.task is None)
        self.welcome_example_button.setEnabled(self.task is None)
        self.results.setText(
            "No apps yet"
            if not self.apps
            else f"{len(visible)} of {len(self.apps)} apps"
            if query
            else f"{len(visible)} apps · AppImage catalog"
        )
        if query:
            self.empty.setText(
                "No apps match your search in this view.\nTry another name or clear the search."
            )
        elif self.tabs.currentIndex() == 1:
            self.empty.setText(
                "No apps are installed.\nChoose Install from All apps to get started."
            )
        elif self.tabs.currentIndex() == 2:
            self.empty.setText(
                "No known updates.\nUse Refresh releases to look for new releases."
                if self.installed
                else "No installed apps to check.\nInstall an app from All apps to get started."
            )
        else:
            self.empty.setText("No apps match your search.\nTry another name or clear the search.")

    def open_details(self, app_id: str) -> None:
        app = next(app for app in self.apps if app.id == app_id)
        dialog = DetailsDialog(
            app,
            self,
            self.installed.get(app_id),
            self.releases.get(app_id),
            self.check_errors.get(app_id, ""),
            icon_path=self.library.icon_path(app),
            release_info=self.release_infos.get(app_id),
        )
        dialog.check_button.setEnabled(self.task is None)

        def check_releases() -> None:
            if self.task is not None:
                return

            def operation(cancel: Event, progress: Progress) -> TaskResult:
                info = discover_releases(
                    app, cancel, self.library.data_dir / "release-cache", force=True
                )
                return TaskResult("Release checked.", release_infos={app.id: info})

            dialog.check_button.setEnabled(False)
            dialog.check_button.setText("Checking…")
            self.start_task(f"Checking releases for {app.name}…", operation, details=dialog)

        dialog.check_requested.connect(check_releases)
        dialog.exec()
        dialog.deleteLater()

    def open_settings(
        self,
        section: int = 0,
        *,
        adding: bool = False,
        app_id: str | None = None,
        choose_build: bool = False,
        import_catalog: bool = False,
        import_example: bool = False,
    ) -> None:
        try:
            dialog = SettingsDialog(self.settings, self, self.library)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Cannot open settings", str(error))
            return
        panel = dialog.repositories
        panel.setEnabled(self.task is None)
        panel.catalog_changed.connect(self.catalog_changed)
        dialog.sections.setCurrentRow(section)
        dialog.theme.currentTextChanged.connect(
            lambda theme: self.apply_theme(theme, dialog.accent.currentText())
        )
        dialog.accent.currentTextChanged.connect(
            lambda accent: self.apply_theme(dialog.theme.currentText(), accent)
        )
        if adding:
            panel.start_add_repository()
        elif app_id:
            panel.open_app(app_id)
            if choose_build:
                panel.choose_build()
        if import_catalog:
            QTimer.singleShot(0, panel.import_catalog)
        elif import_example:
            QTimer.singleShot(0, panel.import_example_catalog)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        if not accepted:
            self.apply_theme()
        if accepted:
            self.settings.setValue("General/close-to-tray", dialog.close_to_tray.isChecked())
            self.settings.setValue(
                "General/check-interval-hours", dialog.check_interval.currentData()
            )
            self.settings.setValue("notifications/updates", dialog.notify_updates.isChecked())
            self.schedule_update_checks()
            self.settings.setValue("appearance/theme", dialog.theme.currentText())
            self.settings.setValue("appearance/accent", dialog.accent.currentText())
            self.settings.setValue("installation/folder", dialog.folder.text())
            self.library.apps_dir = Path(dialog.folder.text())
            self.apply_theme()
            self.scan_installed()

    def choose_build(self, app_id: str) -> None:
        self.open_repositories(app_id=app_id, choose_build=True)

    def review_orphans(self) -> None:
        if self.task is not None:
            return
        dialog = OrphansDialog(self.orphans, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected():
            try:
                self.library.discard_orphans(dialog.selected())
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "Cannot update the library", str(error))
            self.scan_installed()
        dialog.deleteLater()

    def open_repositories(
        self,
        adding: bool = False,
        app_id: str | None = None,
        *,
        choose_build: bool = False,
        import_catalog: bool = False,
        import_example: bool = False,
    ) -> None:
        if self.task is None:
            self.open_settings(
                1,
                adding=adding,
                app_id=app_id,
                choose_build=choose_build,
                import_catalog=import_catalog,
                import_example=import_example,
            )

    def has_update(self, app_id: str) -> bool:
        app = next((app for app in self.apps if app.id == app_id), None)
        if app is not None and app.hold_updates:
            return False
        installed, release = self.installed.get(app_id), self.releases.get(app_id)
        if installed and release:
            return release.newer_than(installed.version)
        info = self.release_infos.get(app_id)
        choice = info.choice(app.include_prereleases) if info and app else None
        return bool(
            installed
            and choice
            and choice.build_required
            and version_key(choice.version) > version_key(installed.version)
        )

    def scan_installed(self) -> None:
        try:
            apps = self.library.apps
            installed = self.library.scan()
            orphans = self.library.orphans()
            self.library.reap_processes()
        except (OSError, ValueError) as error:
            self.footer.setText(str(error))
        else:
            self.orphans = orphans
            self.orphans_button.setVisible(bool(orphans))
            self.orphans_button.setText(
                f"Review {len(orphans)} unrecognised {'entry' if len(orphans) == 1 else 'entries'}…"
            )
            if apps != self.apps:
                for app_id in set(self.cards) - {app.id for app in apps}:
                    self.cards.pop(app_id).deleteLater()
                    self.releases.pop(app_id, None)
                    self.check_errors.pop(app_id, None)
                    self.release_infos.pop(app_id, None)
                    self.release_errors.pop(app_id, None)
                for app in apps:
                    kept = self.cards.get(app.id)
                    if (
                        kept is not None
                        and kept.app != app
                        and replace(kept.app, hold_updates=app.hold_updates) == app
                    ):
                        # Only the hold changed: keep the card and the release it knows.
                        kept.app = app
                        continue
                    if app.id not in self.cards or self.cards[app.id].app != app:
                        old = self.cards.pop(app.id, None)
                        if old is not None:
                            if old.app.build_variant != app.build_variant:
                                self.release_infos.pop(app.id, None)
                                self.release_errors.pop(app.id, None)
                                self.checks.requeue_active(app.id)
                            old.deleteLater()
                        self.releases.pop(app.id, None)
                        self.check_errors.pop(app.id, None)
                        self.cards[app.id] = self.create_card(app)
                self.apps = apps
            cache_dir = self.library.data_dir / "release-cache"
            for app in apps:
                paths = (info_path(app, cache_dir), release_cache_path(app, cache_dir))
                stamps = tuple(file_stamp(path) for path in paths)
                fresh = self.cache_stamps.get(app.id) != (paths, stamps)
                self.cache_stamps[app.id] = (paths, stamps)
                info = cached_info(app, cache_dir) if fresh else None
                if info is not None and (
                    app.id not in self.release_infos
                    or info.checked_at > self.release_infos[app.id].checked_at
                ):
                    self.release_infos[app.id] = info
                    github_status.release_result("Cached information", app.repo, info.checked_at)
                if app.id in self.release_infos:
                    self.apply_release_info(app)
                elif fresh:
                    release = cached_release(app, cache_dir)
                    if release is not None:
                        self.releases[app.id] = release
            self.installed = installed
            if self.task is None:
                self.footer.setText(f"{len(self.installed)} apps installed with AppGlider.")
        self.refresh()

    def launch_app(self, app_id: str) -> None:
        app = next(app for app in self.apps if app.id == app_id)
        installed = self.installed.get(app_id)
        if installed is None:
            self.scan_installed()
            return
        try:
            log = self.library.launch(app, installed)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, f"Cannot launch {app.name}", str(error))
            return
        process = self.library.processes[app_id]
        self.footer.setText(f"Launched {app.name} · Log: {log}")

        def check_startup() -> None:
            code = process.poll()
            if code is not None and code != 0:
                self.footer.setText(f"{app.name} exited with code {code}. See {log}")
                QMessageBox.warning(
                    self,
                    f"{app.name} could not start",
                    f"The application exited with code {code}.\nIts diagnostic log is:\n{log}",
                )

        QTimer.singleShot(1800, self, check_startup)

    def start_task(
        self,
        message: str,
        operation: Callable[[Event, Progress], TaskResult],
        *,
        details: DetailsDialog | None = None,
    ) -> None:
        if self.task is not None:
            return
        self.task = Task(operation)
        if details is not None:
            self.task.signals.finished.connect(details.release_check_finished)
            self.task.signals.failed.connect(details.release_check_failed)
        self.task.signals.progress.connect(self.task_progress)
        self.task.signals.finished.connect(self.task_finished)
        self.task.signals.failed.connect(self.task_failed)
        self.footer.setText(message)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()
        self.cancel_button.setText("Cancel")
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()
        self.refresh()
        QThreadPool.globalInstance().start(self.task)

    def create_shortcut(self, app_id: str) -> None:
        app = next(app for app in self.apps if app.id == app_id)
        try:
            self.library.create_shortcut(app)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Cannot create shortcut", str(error))
        else:
            self.footer.setText(f"Created menu shortcut: {app.name} (AppGlider).")

    def task_progress(self, message: str, received: int, total: int) -> None:
        self.footer.setText(message)
        self.progress_bar.setRange(0, 100 if total > 0 else 0)
        if total > 0:
            self.progress_bar.setValue(min(100, int(received * 100 / total)))

    def reset_task(self) -> None:
        self.task = None
        self.progress_bar.hide()
        self.cancel_button.hide()
        self.scan_installed()

    def task_finished(self, result: TaskResult) -> None:
        self.releases.update(result.releases)
        for app in self.apps:
            checked_info = result.release_infos.get(app.id)
            if checked_info is not None and checked_info.build_variant == app.build_variant:
                self.release_infos[app.id] = checked_info
                self.release_errors.pop(app.id, None)
                self.apply_release_info(app)
            release = result.releases.get(app.id)
            if release is not None:
                info = remember_release(app, release, self.library.data_dir / "release-cache")
                if info is not None:
                    self.release_infos[app.id] = info
        self.check_errors.update(result.errors)
        for app_id in result.releases:
            self.check_errors.pop(app_id, None)
        self.reset_task()
        self.footer.setText(result.message)
        if result.build_choices and not self.close_after_task:
            self.choose_build(next(iter(result.build_choices)))
        if result.errors and not self.close_after_task:
            errors = "\n\n".join(f"{key}: {value}" for key, value in result.errors.items())
            QMessageBox.warning(self, "Some operations could not finish", errors)
        if self.close_after_task:
            self.close()

    def task_failed(self, error: str) -> None:
        self.reset_task()
        self.footer.setText(error)
        if self.close_after_task:
            self.close()
        else:
            QMessageBox.warning(self, "Operation could not finish", error)

    def cancel_task(self) -> None:
        if self.task is not None:
            self.task.cancel.set()
            self.cancel_button.setText("Cancelling…")
            self.cancel_button.setEnabled(False)

    def apply_release_info(self, app: App) -> None:
        info = self.release_infos[app.id]
        choice = info.choice(app.include_prereleases)
        if choice and choice.release:
            self.releases[app.id] = choice.release
        else:
            self.releases.pop(app.id, None)
        error = info.error(app.include_prereleases)
        if error:
            self.check_errors[app.id] = error
        else:
            self.check_errors.pop(app.id, None)

    def catalog_changed(self) -> None:
        self.scan_installed()
        if self.background_enabled:
            self.checks.add(
                [
                    app.id
                    for app in self.apps
                    if app.id in self.installed and app.id not in self.release_infos
                ]
            )

    def refresh_releases(self, *, force: bool = False) -> None:
        if self.checks.busy or self.close_after_release:
            return
        self.background_enabled = True
        self.scan_installed()
        if not self.installed:
            self.footer.setText("Install an application to enable release checks.")
            return
        self.checks.force = force
        self.checks.add([app.id for app in self.apps if app.id in self.installed])

    def release_operation(self, app: App, force: bool) -> Callable[[Event, Progress], TaskResult]:
        def operation(cancel: Event, progress: Progress) -> TaskResult:
            info = discover_releases(
                app, cancel, self.library.data_dir / "release-cache", force=force, cheap=True
            )
            return TaskResult("Release checked.", release_infos={app.id: info})

        return operation

    def checkable_app(self, app_id: str) -> App | None:
        if app_id not in self.installed:
            return None
        return next((app for app in self.apps if app.id == app_id), None)

    def release_check_started(self, app_id: str) -> None:
        if self.task is None:
            app = next(app for app in self.apps if app.id == app_id)
            self.footer.setText(f"Checking releases for {app.name}…")
        self.refresh()

    def release_check_done(
        self, app_id: str, variant: str, result: TaskResult | None, error: str
    ) -> None:
        if result is None:
            app = next((app for app in self.apps if app.id == app_id), None)
            if app and app.build_variant == variant:
                self.release_errors[app.id] = error
            return
        for app in self.apps:
            info = result.release_infos.get(app.id)
            if info is not None:
                if info.build_variant != app.build_variant:
                    self.checks.add([app.id])
                    continue
                self.release_infos[app.id] = info
                self.release_errors.pop(app.id, None)
                self.apply_release_info(app)

    def release_checks_drained(self) -> None:
        if self.close_after_release:
            self.close()
            return
        self.refresh()
        if self.notify_pending:
            self.notify_pending = False
            self.notify_updates()
        if self.task is None:
            self.footer.setText(
                "Release refresh finished."
                + (
                    " Some checks failed; cards retain previously known information."
                    if any(app_id in self.installed for app_id in self.release_errors)
                    else ""
                )
            )

    def install_app(self, app_id: str) -> None:
        app = next(app for app in self.apps if app.id == app_id)
        installed = self.installed.get(app_id)

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            progress(f"Checking {app.name} release…", 0, 0)
            release = latest_release(app, cancel, self.library.data_dir / "release-cache")
            if installed and not release.newer_than(installed.version):
                return TaskResult(f"{app.name} is already up to date.", {app_id: release})
            self.library.install(
                app,
                release,
                cancel,
                lambda message, done, total: progress(f"{app.name} · {message}", done, total),
            )
            return TaskResult(
                f"{app.name} v{release.version} is ready to launch.", {app_id: release}
            )

        self.start_task(f"Preparing {app.name}…", operation)

    def update_all(self) -> None:
        apps = [
            app
            for app in self.apps
            if self.has_update(app.id)
            and app.id in self.releases
            and self.installed[app.id].managed
        ]

        versions = {app.id: self.installed[app.id].version for app in apps}

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            result = TaskResult("Updates finished.")
            updated: list[str] = []
            for app in apps:
                check_cancel(cancel)
                progress(f"Checking {app.name}…", 0, 0)
                try:
                    release = latest_release(app, cancel, self.library.data_dir / "release-cache")
                    if not release.newer_than(versions[app.id]):
                        result.releases[app.id] = release
                        continue
                    self.library.install(
                        app,
                        release,
                        cancel,
                        lambda message, done, total: progress(
                            f"{app.name} · {message}", done, total
                        ),
                    )
                    result.releases[app.id] = release
                    updated.append(app.id)
                except BuildChoiceRequired as error:
                    result.build_choices[app.id] = error
                except (OSError, ValueError) as error:
                    result.errors[app.id] = str(error)
            result.message = f"Updated {len(updated)} apps."
            return result

        if apps:
            self.start_task("Updating installed apps…", operation)

    def rollback_app(self, app_id: str) -> None:
        app = next(app for app in self.apps if app.id == app_id)

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            check_cancel(cancel)
            self.library.rollback(app)
            return TaskResult(f"Restored the previous {app.name} version.")

        self.start_task(f"Restoring {app.name}…", operation)

    def remove_app(self, app_id: str) -> None:
        app = next(app for app in self.apps if app.id == app_id)
        answer = QMessageBox.question(
            self,
            f"Remove {app.name}?",
            "Remove this app and its previous version from AppGlider?\n"
            "Your documents and application settings will be kept.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            check_cancel(cancel)
            self.library.remove(app)
            return TaskResult(f"Removed the managed {app.name} installation.")

        self.start_task(f"Removing {app.name}…", operation)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.settings.setValue("window/geometry", self.saveGeometry())
        if (
            not self.quitting
            and self.settings.value("General/close-to-tray", True, type=bool)
            and self.tray.isVisible()
            and QSystemTrayIcon.isSystemTrayAvailable()
        ):
            self.hide()
            event.ignore()
            return
        if self.task is not None:
            answer = QMessageBox.question(
                self,
                "Operation in progress",
                "Cancel the current operation and close when it has stopped?",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.quitting = True
                self.close_after_task = True
                self.cancel_task()
            event.ignore()
            return
        if self.checks.busy:
            self.quitting = True
            self.close_after_release = True
            self.checks.stop()
            self.tray.hide()
            self.hide()
            event.ignore()
            return
        self.tray.hide()
        super().closeEvent(event)

    def apply_theme(self, preview: str | None = None, accent_preview: str | None = None) -> None:
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        saved_theme, saved_accent = appearance_choice(self.settings)
        theme = preview or saved_theme
        accent_name = accent_preview or saved_accent
        if theme not in THEMES:
            scheme = app.styleHints().colorScheme()
            if scheme == Qt.ColorScheme.Unknown:
                # Our own palette has replaced the desktop's, so ask the style for its default.
                window = app.style().standardPalette().color(QPalette.ColorRole.Window)
                scheme = Qt.ColorScheme.Dark if window.lightness() < 128 else Qt.ColorScheme.Light
            theme = "Dark" if scheme == Qt.ColorScheme.Dark else "Light"
        tokens = THEMES[theme]
        dark = theme == "Dark"
        accents = ACCENTS.get(accent_name, ACCENTS[DEFAULT_ACCENT])
        accent = accents[0] if dark else accents[1]
        background, surface = tokens["bg"], tokens["surface"]
        foreground, muted, border = tokens["text"], tokens["muted"], tokens["line"]
        ok, warn, danger = tokens["ok"], tokens["warn"], tokens["danger"]
        on_accent = background if dark else "#ffffff"
        palette = QPalette()
        for role, color in (
            (QPalette.ColorRole.Window, background),
            (QPalette.ColorRole.WindowText, foreground),
            (QPalette.ColorRole.Base, surface),
            (QPalette.ColorRole.AlternateBase, background),
            (QPalette.ColorRole.Text, foreground),
            (QPalette.ColorRole.Button, surface),
            (QPalette.ColorRole.ButtonText, foreground),
            (QPalette.ColorRole.ToolTipBase, surface),
            (QPalette.ColorRole.ToolTipText, foreground),
            (QPalette.ColorRole.Highlight, accent),
            (QPalette.ColorRole.HighlightedText, on_accent),
            (QPalette.ColorRole.PlaceholderText, muted),
        ):
            palette.setColor(role, QColor(color))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(muted))
        app.setPalette(palette)
        self.settings_button.setIcon(cog_icon(foreground))
        app.setStyleSheet(f"""
            QWidget {{ font-size: 14px; }}
            QMainWindow, QDialog {{ background: {background}; }}
            QLabel {{ background: transparent; }}
            QLabel#pageTitle {{ font-size: 27px; font-weight: 650; }}
            QLabel#dialogTitle {{ font-size: 22px; font-weight: 650; }}
            QLabel#sectionTitle {{ font-size: 18px; font-weight: 650; }}
            QGroupBox {{ background: {surface}; border: 1px solid {border};
                border-radius: 10px; margin-top: 12px; padding: 18px 14px 14px; }}
            QWidget#repositoriesPanel QGroupBox {{ padding: 16px 14px 10px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 14px;
                padding: 0 6px; color: {foreground}; font-weight: 600; }}
            QPushButton#compact {{ padding: 6px 12px; }}
            QComboBox#compact {{ padding: 6px 10px; }}
            QToolButton#settingsButton {{ padding: 8px; }}
            QFrame#statusStrip {{ background: {surface}; border: 1px solid {border};
                border-radius: 8px; }}
            QProgressBar {{ background: {background}; border: none; border-radius: 3px; }}
            QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}
            QListWidget#repositories {{ background: {surface}; border: 1px solid {border};
                border-radius: 10px; outline: 0; }}
            QListWidget#repositories:focus {{ border: 2px solid {accent}; }}
            QListWidget#repositories::item {{ padding: 10px 8px; border-radius: 6px; }}
            QPushButton#backLink {{ background: transparent; border: none; color: {accent};
                padding: 4px 2px; font-weight: 600; }}
            QPushButton#backLink:hover {{ text-decoration: underline; }}
            QPushButton#backLink:disabled {{ color: {muted}; }}
            QListWidget#repositories::item:selected {{ background: {background}; color: {accent}; }}
            QLabel#muted, QLabel#footer {{ color: {muted}; }}
            QLabel#footer {{ font-size: 12px; padding-top: 8px; }}
            QLabel#emptyState {{ font-size: 17px; color: {muted}; }}
            QFrame#appCard {{ background: {surface}; border: 1px solid {border};
                border-radius: 12px; }}
            QFrame#appCard:hover {{ border-color: {accent}; }}
            QFrame#cardDivider {{ border: none; background: {border}; }}
            QLabel#cardDescription, QLabel#versionCaption {{ color: {muted}; font-size: 12px; }}
            QLabel#cardChecked {{ color: {muted}; font-size: 11px; }}
            QPushButton#cardAction {{ padding: 7px 12px; font-size: 13px; }}
            QPushButton, QToolButton {{ background: {surface}; color: {foreground};
                border: 1px solid {border}; border-radius: 7px; padding: 9px 14px; }}
            QPushButton:hover, QToolButton:hover {{ border-color: {accent}; color: {accent}; }}
            QPushButton:focus, QToolButton:focus, QLineEdit:focus, QComboBox:focus {{
                border: 2px solid {accent}; }}
            QPushButton:disabled {{ color: {muted}; background: {background}; }}
            QPushButton#primary {{ background: {accent}; color: {on_accent};
                border-color: {accent}; font-weight: 600; }}
            QPushButton#primary:hover {{ border-color: {foreground}; }}
            QPushButton#primary:disabled {{ background: {surface}; color: {muted};
                border-color: {border}; }}
            QPushButton#danger {{ color: {danger}; }}
            QLabel#pinStar {{ color: {PIN_COLOUR}; font-size: 15px; }}
            QPushButton#appName {{ border: none; padding: 0; text-align: left;
                font-size: 17px; font-weight: 650; background: transparent; }}
            QPushButton#appName:focus {{ outline: 2px solid {accent}; }}
            QToolButton#cardMenu {{ border: none; background: transparent;
                font-size: 24px; padding: 0px 6px; }}
            QToolButton::menu-indicator {{ image: none; }}
            QLabel#status {{ color: {muted}; font-size: 12px; }}
            QLabel#releaseBadge {{ border: 1px solid; border-radius: 9px;
                padding: 2px 7px; font-size: 11px; font-weight: 600; }}
            QLabel#releaseBadge[release="latest"] {{ color: {ok};
                border-color: {ok}; }}
            QLabel#releaseBadge[release="prerelease"] {{ color: {warn};
                border-color: {warn}; }}
            QPushButton#versionButton, QPushButton#githubStatus {{
                background: transparent; border: 1px solid transparent;
                color: {muted}; font-size: 12px; padding: 5px 10px; }}
            QPushButton#versionButton:hover, QPushButton#versionButton:focus,
            QPushButton#githubStatus:hover, QPushButton#githubStatus:focus {{
                background: {surface}; border-color: {accent}; color: {accent}; }}
            QPushButton#githubStatus[limited="true"] {{
                color: {warn}; }}
            QListWidget#settingsSections {{ border: none; background: transparent; outline: 0; }}
            QListWidget#settingsSections::item {{ padding: 12px; border-radius: 7px; }}
            QListWidget#settingsSections::item:selected {{
                background: {surface}; color: {accent}; }}
            QLineEdit, QComboBox {{ background: {surface}; color: {foreground};
                border: 1px solid {border}; border-radius: 7px; padding: 9px; }}
            QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px;
                border: 2px solid {muted}; background: {surface}; }}
            QCheckBox::indicator {{ border-radius: 5px; }}
            QRadioButton::indicator {{ border-radius: 9px; }}
            QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {accent}; }}
            QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent};
                image: url({tick_path(on_accent)}); }}
            QRadioButton::indicator:checked {{ border-color: {accent};
                background: qradialgradient(cx: 0.5, cy: 0.5, radius: 0.5, fx: 0.5, fy: 0.5,
                    stop: 0 {accent}, stop: 0.45 {accent},
                    stop: 0.55 {surface}, stop: 1 {surface}); }}
            QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
                border-color: {border}; }}
            QComboBox {{ min-height: 20px; }}
            QComboBox::drop-down {{ border: none; width: 28px; }}
            QComboBox::down-arrow {{ image: url({chevron_path(foreground)});
                width: 20px; height: 20px; }}
            QComboBox QAbstractItemView {{ background: {surface}; color: {foreground};
                border: 1px solid {border}; padding: 4px; outline: 0;
                selection-background-color: {accent}; selection-color: {on_accent}; }}
            QComboBox QAbstractItemView::item {{ min-height: 32px; padding: 4px 10px; }}
            QComboBox QAbstractItemView::item:selected {{ background: {accent};
                color: {on_accent}; }}
            QAbstractItemView QLineEdit, QAbstractItemView QLineEdit:focus {{
                padding: 0; border: 1px solid {accent}; border-radius: 2px; }}
            QScrollArea, QScrollArea > QWidget > QWidget {{ background: {background}; }}
            QTabBar::tab {{ color: {muted}; padding: 12px 16px;
                border-bottom: 2px solid transparent; }}
            QTabBar::tab:selected {{ color: {accent}; border-bottom-color: {accent}; }}
            QTabBar::tab:focus {{ border: 1px solid {accent}; }}
            QMenu {{ background: {surface}; color: {foreground};
                border: 1px solid {border}; padding: 6px; }}
            QMenu::item {{ padding: 8px 16px; }}
            QMenu::item:selected {{ background: {background}; color: {accent}; }}
        """)
