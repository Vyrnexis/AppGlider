"""Installed applications and background installation controls."""

import html
import json
import os
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Event

from PySide6.QtCore import (
    QByteArray,
    QRect,
    QSettings,
    QSize,
    QStandardPaths,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
)
from PySide6.QtGui import (
    QCloseEvent,
    QDesktopServices,
    QIcon,
    QKeySequence,
    QPalette,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from appglider import (
    APP_ICON,
    APP_NAME,
    APP_TASKBAR_ICON,
    APP_VERSION,
)
from appglider.core.catalog import App
from appglider.core.catalogfile import EXAMPLE_CATALOG
from appglider.core.catalogfile import read as read_catalog
from appglider.core.preferences import (
    ACCENTS,
    DEFAULT_ACCENT,
    MAX_PINNED,
    appearance_choice,
    check_interval_hours,
    custom_order,
    dismissed_versions,
    pinned_apps,
)
from appglider.github import status as github_status
from appglider.github.assets import BuildChoiceRequired, Release
from appglider.github.download import Progress, cached_release, latest_release, release_cache_path
from appglider.github.http import (
    UNREACHABLE,
    RateLimited,
    Unreachable,
    file_stamp,
    github_request_status,
    restore_rate_limits,
)
from appglider.github.icons import icon_pixmap
from appglider.github.release_info import (
    ReleaseInfo,
    cached_info,
    discover_releases,
    info_path,
    remember_release,
)
from appglider.github.versions import Cancelled, check_cancel, version_key
from appglider.install import self_update
from appglider.install.appimage import (
    check_appimage,
    clean_environment,
    fuse_explanation,
    log_tail,
)
from appglider.install.library import (
    AppRunning,
    Installed,
    Library,
    Orphan,
    previous_copy,
    running,
)
from appglider.install.place import describe_appimage, place_appimage, replaceable
from appglider.ui.app_status import WAITING, AppStatus, app_status, version_text
from appglider.ui.controls import WAIT_TIP, MessageBox
from appglider.ui.errors import describe_error
from appglider.ui.jobs import Lookup, StepSignals, Task, TaskResult
from appglider.ui.pages.add_page import AddPage
from appglider.ui.pages.app_page import AppPage, link_button
from appglider.ui.pages.settings_page import SettingsPage
from appglider.ui.release_checks import ReleaseChecks
from appglider.ui.rows import AppList, AppRow, DragHandle, StatusMessage
from appglider.ui.theme import NAV_ICON_SIZE, THEMES, nav_icon, palette, refresh_icon, stylesheet
from appglider.ui.tray import TrayMixin

SIDEBAR_WIDTH = 210
LEGACY_SECTIONS = {0: "General", 1: "Catalogs", 2: "Storage", 3: "Updates", 4: "About"}
CHECK_TOOLTIP = "Check for updates"
WAIT_FOR_CHECK = "Wait for the update check to finish, then try again."
SORT_TIP = "Sort A–Z. To set your own order, drag rows by their handle."
FIRST_HIDE_KEY = "tray/hint-seen"
# Waits before automatic checks retry when GitHub was unreachable, e.g. at login.
OFFLINE_RETRY_SECONDS = (15, 30, 60, 120, 300)


class OrphansDialog(QDialog):
    """Offer to forget library entries for unknown apps; keeping them changes nothing."""

    def __init__(self, orphans: list[Orphan], parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Unrecognised library entries")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "These entries are in AppGlider's records but match no app in your library, for "
            "example a repository that was removed. They are kept untouched unless you tick "
            "them. Forgetting an entry never deletes files."
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


def nav_entry(text: str, *, badge: bool = False) -> tuple[QPushButton, QLabel]:
    """Create a sidebar entry with a trailing count label (hidden until it has text)."""
    button = QPushButton(text)
    button.setObjectName("nav")
    button.setIconSize(QSize(NAV_ICON_SIZE, NAV_ICON_SIZE))
    button.setFixedHeight(38)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    layout = QHBoxLayout(button)
    layout.setContentsMargins(12, 0, 10, 0)
    layout.addStretch()
    count = QLabel()
    count.setObjectName("navBadge" if badge else "navCount")
    count.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    count.setAlignment(Qt.AlignmentFlag.AlignCenter)
    if badge:
        count.setFixedHeight(18)
        count.setMinimumWidth(18)
    layout.addWidget(count, alignment=Qt.AlignmentFlag.AlignVCenter)
    return button, count


def banner_close(tooltip: str) -> QToolButton:
    """The × that closes a banner until there is something new to show."""
    button = QToolButton()
    button.setText("×")
    button.setObjectName("bannerClose")
    button.setFixedSize(32, 32)
    button.setToolTip(tooltip)
    button.setAccessibleName("Close banner")
    button.setAccessibleDescription(tooltip)
    return button


def update_all_message(
    updated: int,
    failed: int,
    stopped: str = "",
    skipped: Iterable[str] = (),
    choices: Iterable[str] = (),
    cancelled: bool = False,
) -> str:
    """The footer line after Update all: what was updated, what was left, and why."""
    if updated:
        text = f"Updated {updated} app{'' if updated == 1 else 's'}."
    else:
        text = "No apps were updated."
    if cancelled:
        text = f"Cancelled. {text}"
    if failed:
        text += f" {failed} could not be updated."
    skipped = list(skipped)
    if skipped:
        text += (
            f" Skipped because running: {', '.join(skipped)}. "
            f"Close {'it' if len(skipped) == 1 else 'them'} and press Update all again."
        )
    choices = list(choices)
    if choices:
        text += f" Needs a build choice: {', '.join(choices)}."
    if stopped:
        text = f"{stopped} {text}"
    return text


def when_text(stamp: float) -> str:
    """A time of day, with "yesterday" or the date when it was not today."""
    when = datetime.fromtimestamp(stamp)
    today = date.today()
    if when.date() == today:
        return f"{when:%H:%M}"
    if when.date() == today - timedelta(days=1):
        return f"yesterday {when:%H:%M}"
    return f"{when:%d %b} {when:%H:%M}"


def shortened(text: str, limit: int = 70) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def lower_first(text: str) -> str:
    """ "Preparing X" as "preparing X", but leave names like "AppGlider" alone."""
    return text[:1].lower() + text[1:] if text[1:2].islower() else text


class LauncherWindow(TrayMixin):
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
        self.missing: dict[str, Installed] = {}
        self.move_notice = ""
        self.apps: tuple[App, ...] = ()
        self.pinned = pinned_apps(self.settings)
        self.releases: dict[str, Release] = {}
        self.check_errors: dict[str, str] = {}
        self.release_infos: dict[str, ReleaseInfo] = {}
        self.cache_stamps: dict[str, object] = {}
        self.tray_key = None
        self.tray_check_pending = False
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
        self.working: dict[str, str] = {}
        self.failure = ""  # names the running task in the dialog that reports its failure
        self.task_name = ""  # the running task in words, for "Stop … and quit?"
        self.work_progress: int | None = None  # percent of the download an app's chip shows
        self.welcome_shown = False
        self.return_to: str | None = None  # the row whose App page is open, to refocus on return
        self.close_after_task = False
        self.quitting = False
        self.self_state = self_update.SelfUpdate()
        self.self_task: Lookup | None = None
        self.shortcut_task: Lookup | None = None
        self.names_task: Lookup | None = None
        self.menu_task: Lookup | None = None
        self.force_task: Lookup | None = None
        self.force_app: App | None = None
        self.update_steps = StepSignals(self)
        self.update_steps.step.connect(self.update_step)
        self.menu_app: App | None = None
        self.self_explicit = False
        self.self_notify = False
        self.self_ready = self_update.ready_update(self.settings)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(str(APP_TASKBAR_ICON)))
        self.resize(fit_size(1040, 816, screen_area(None)))
        saved = self.settings.value("window/geometry")
        if isinstance(saved, QByteArray | bytes):
            self.restoreGeometry(QByteArray(saved))
        self.setMinimumSize(860, 560)
        self.view = "library"
        shell = QWidget()
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        self.setCentralWidget(shell)
        self.build_sidebar(shell_layout)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        shell_layout.addWidget(body, stretch=1)
        self.pages = QStackedWidget()
        body_layout.addWidget(self.pages, stretch=1)
        layout = self.build_pages(body_layout)
        self.build_toolbar(layout)
        self.build_banner(layout)
        self.build_list(layout)
        self.build_welcome(layout)
        self.build_footer(body_layout)
        self.cards: dict[str, AppRow] = {}
        self.search.textChanged.connect(self.refresh)
        self.filter_installed.toggled.connect(self.refresh)
        QApplication.styleHints().colorSchemeChanged.connect(self.system_theme_changed)
        self.build_tray()
        self.apply_theme()
        restore_rate_limits(self.library.data_dir / "release-cache")
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.scheduled_check)
        # Automatic checks that found no network (at login, after a resume) try again soon.
        self.offline_timer = QTimer(self)
        self.offline_timer.setSingleShot(True)
        self.offline_timer.timeout.connect(self.offline_retry)
        self.offline_attempts = 0
        self.self_retry = False
        self.github_timer = QTimer(self)
        self.github_timer.timeout.connect(self.update_github_status)
        self.github_timer.start(1000)
        self.update_github_status()
        self.scan_installed()
        self.schedule_update_checks()
        self.setup_keyboard()

    def build_sidebar(self, shell_layout: QHBoxLayout) -> None:
        """Navigation, GitHub status block and version button."""
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 18, 12, 12)
        side.setSpacing(4)
        brand = QHBoxLayout()
        brand.setContentsMargins(6, 0, 0, 14)
        brand.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(QIcon(str(APP_ICON)).pixmap(32, 32))
        logo.setFixedSize(32, 32)
        logo.setAccessibleName(APP_NAME)
        brand.addWidget(logo)
        brand_name = QLabel(APP_NAME)
        brand_name.setObjectName("brand")
        brand.addWidget(brand_name)
        brand.addStretch()
        side.addLayout(brand)
        self.nav_library, self.library_count = nav_entry("Library")
        self.nav_updates, self.updates_count = nav_entry("Updates", badge=True)
        self.nav_add, _ = nav_entry("Add app")
        self.nav_settings, _ = nav_entry("Settings")
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for button in (self.nav_library, self.nav_updates, self.nav_add, self.nav_settings):
            button.setCheckable(True)
            self.nav_group.addButton(button)
        self.nav_library.setChecked(True)
        self.nav_library.clicked.connect(lambda: self.show_view("library"))
        self.nav_updates.clicked.connect(lambda: self.show_view("updates"))
        self.nav_add.setAccessibleName("Add app")
        self.nav_add.clicked.connect(self.open_add_app)
        self.nav_settings.setAccessibleName("Settings")
        self.nav_settings.clicked.connect(lambda: self.open_settings("General"))
        for button in (self.nav_library, self.nav_updates, self.nav_add):
            side.addWidget(button)
        side.addStretch()
        side.addWidget(self.nav_settings)
        side.addSpacing(6)
        self.status_block = QPushButton()
        self.status_block.setObjectName("statusBlock")
        self.status_block.setAccessibleName("GitHub request status")
        self.status_block.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status_block.setFixedHeight(66)
        self.status_block.clicked.connect(lambda: self.open_settings("Updates"))
        status_layout = QVBoxLayout(self.status_block)
        status_layout.setContentsMargins(8, 8, 8, 6)
        status_layout.setSpacing(4)
        self.status_label = QLabel()
        self.github_label = QLabel()
        self.github_meter = QProgressBar()
        self.github_meter.setObjectName("meter")
        self.github_meter.setTextVisible(False)
        self.github_meter.setFixedHeight(4)
        for status_text in (self.status_label, self.github_label):
            status_text.setObjectName("sideStatus")
        for widget in (self.status_label, self.github_label, self.github_meter):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            status_layout.addWidget(widget)
        side.addWidget(self.status_block)
        self.version_button = QPushButton(f"v{APP_VERSION}")
        self.version_button.setObjectName("versionButton")
        self.version_button.setAccessibleName(f"About AppGlider {APP_VERSION}")
        self.version_button.setToolTip("About AppGlider")
        self.version_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.version_button.clicked.connect(lambda: self.open_settings("About"))
        side.addWidget(self.version_button, alignment=Qt.AlignmentFlag.AlignLeft)
        shell_layout.addWidget(sidebar)

    def build_pages(self, body_layout: QVBoxLayout) -> QVBoxLayout:
        """Page stack with the library page, returning its layout."""
        list_widget = self.list_page = QWidget()
        layout = QVBoxLayout(list_widget)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(14)
        self.pages.addWidget(list_widget)
        self.app_page = AppPage(self)
        self.app_page.back_requested.connect(self.close_app)
        self.pages.addWidget(self.app_page)
        self.settings_page = SettingsPage(self)
        self.pages.addWidget(self.settings_page)
        self.settings_page.import_changed.connect(self.copy_changed)
        self.add_page = AddPage(self)
        self.add_page.added.connect(self.app_added)
        self.add_page.add_and_install.connect(self.app_added_install)
        self.add_page.copy_changed.connect(self.copy_changed)
        self.add_page.copy_failed.connect(self.copy_failed)
        self.pages.addWidget(self.add_page)
        self.pages.currentChanged.connect(self.page_changed)
        self.list_scroll = 0
        return layout

    def build_toolbar(self, layout: QVBoxLayout) -> None:
        """Title, search, filters and the library buttons."""
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        self.page_title = QLabel("Library")
        self.page_title.setObjectName("pageTitle")
        toolbar.addWidget(self.page_title)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search apps")
        self.search.setAccessibleName("Search apps")
        self.search.setClearButtonEnabled(True)
        toolbar.addWidget(self.search, stretch=1)
        self.filters = QWidget()
        filters = QHBoxLayout(self.filters)
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setSpacing(0)
        self.filter_all = QPushButton("All")
        self.filter_installed = QPushButton("Installed")
        filter_group = QButtonGroup(self)
        filter_group.setExclusive(True)
        for position, button in (("first", self.filter_all), ("last", self.filter_installed)):
            button.setCheckable(True)
            button.setObjectName("segment")
            button.setProperty("segment", position)
            filter_group.addButton(button)
            filters.addWidget(button)
        self.filter_all.setChecked(True)
        self.filters.setAccessibleName("Library filter")
        toolbar.addWidget(self.filters)
        self.options_button = QPushButton("Sort A–Z")
        self.options_button.setAccessibleName("Sort the library A–Z")
        self.options_button.setToolTip(SORT_TIP)
        self.options_button.clicked.connect(self.sort_alphabetically)
        toolbar.addWidget(self.options_button)
        self.check_button = QToolButton()
        self.check_button.setObjectName("checkNow")
        self.check_button.setAccessibleName("Check for updates")
        self.check_button.setToolTip(CHECK_TOOLTIP)
        self.check_button.setIconSize(QSize(18, 18))
        self.check_button.clicked.connect(self.check_for_updates)
        toolbar.addWidget(self.check_button)
        layout.addLayout(toolbar)

    def build_banner(self, layout: QVBoxLayout) -> None:
        """AppGlider's own update banner, then the updates banner, above the list."""
        self.self_banner = QFrame()
        self.self_banner.setObjectName("selfUpdateBanner")
        self_layout = QHBoxLayout(self.self_banner)
        self_layout.setContentsMargins(14, 10, 10, 10)
        self_layout.setSpacing(8)
        self.self_banner_label = QLabel()
        self.self_banner_label.setTextFormat(Qt.TextFormat.RichText)
        self.self_banner_label.setWordWrap(True)
        self_layout.addWidget(self.self_banner_label, stretch=1)
        self.self_notes_button = link_button("What's new ↗")
        self.self_notes_button.clicked.connect(self.open_self_release)
        self_layout.addWidget(self.self_notes_button)
        # Not accent-filled: Update all just below stays the one primary action on the page.
        self.self_update_button = QPushButton("Releases on GitHub ↗")
        self.self_update_button.clicked.connect(self.self_button_clicked)
        self_layout.addWidget(self.self_update_button)
        self.self_close_button = banner_close("Hide until a newer AppGlider is out")
        self.self_close_button.clicked.connect(self.dismiss_self_banner)
        self_layout.addWidget(self.self_close_button)
        self.self_banner.hide()
        layout.addWidget(self.self_banner)
        self.banner = QFrame()
        self.banner.setObjectName("updatesBanner")
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(14, 10, 10, 10)
        banner_layout.setSpacing(8)
        self.banner_label = QLabel()
        self.banner_label.setTextFormat(Qt.TextFormat.RichText)
        self.banner_label.setWordWrap(True)
        banner_layout.addWidget(self.banner_label, stretch=1)
        self.update_all_button = QPushButton("Update all")
        self.update_all_button.clicked.connect(self.banner_action)
        self.update_all_button.setObjectName("primary")
        banner_layout.addWidget(self.update_all_button)
        self.review_button = QPushButton("Review")
        self.review_button.setToolTip("Show only the apps that have updates")
        self.review_button.clicked.connect(lambda: self.show_view("updates"))
        banner_layout.addWidget(self.review_button)
        self.banner_close_button = banner_close("Hide until another update is out")
        self.banner_close_button.clicked.connect(self.dismiss_banner)
        banner_layout.addWidget(self.banner_close_button)
        self.banner.hide()
        layout.addWidget(self.banner)
        self.search.setFocus()

    def build_list(self, layout: QVBoxLayout) -> None:
        """Scrolling app list and the empty-state label."""
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        list_page = QWidget()
        list_layout = QVBoxLayout(list_page)
        list_layout.setContentsMargins(0, 0, 8, 0)
        self.app_list = AppList()
        self.app_list.scroll_area = self.scroll_area
        self.app_list.row_dropped.connect(self.drop_app)
        list_layout.addWidget(self.app_list)
        list_layout.addStretch()
        self.scroll_area.setWidget(list_page)
        layout.addWidget(self.scroll_area, stretch=1)
        self.empty_panel = QWidget()
        empty_layout = QVBoxLayout(self.empty_panel)
        empty_layout.setContentsMargins(0, 0, 0, 0)
        empty_layout.setSpacing(10)
        empty_layout.addStretch()
        self.empty = QLabel()
        self.empty.setObjectName("emptyState")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        empty_layout.addWidget(self.empty)
        self.empty_checked = QLabel()
        self.empty_checked.setObjectName("emptyState")
        self.empty_checked.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_checked)
        self.empty_check_button = QPushButton("Check for updates")
        self.empty_check_button.clicked.connect(self.check_for_updates)
        empty_layout.addWidget(self.empty_check_button, alignment=Qt.AlignmentFlag.AlignCenter)
        empty_layout.addStretch()
        layout.addWidget(self.empty_panel, stretch=1)

    def build_welcome(self, layout: QVBoxLayout) -> None:
        """First-run panel shown while the library is empty."""
        self.welcome = QWidget()
        welcome_layout = QVBoxLayout(self.welcome)
        welcome_layout.setSpacing(16)
        welcome_layout.setContentsMargins(24, 0, 24, 0)
        welcome_layout.addStretch()
        welcome_title = QLabel("Welcome to AppGlider")
        welcome_title.setObjectName("welcomeTitle")
        welcome_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_layout.addWidget(welcome_title)
        welcome_text = QLabel("Install and keep AppImage apps from GitHub up to date in one place.")
        welcome_text.setObjectName("emptyState")
        welcome_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_text.setWordWrap(True)
        welcome_layout.addWidget(welcome_text)
        self.welcome_add_button = QPushButton("Add your first app")
        self.welcome_add_button.setObjectName("primary")
        self.welcome_add_button.clicked.connect(self.open_add_app)
        welcome_layout.addWidget(self.welcome_add_button, alignment=Qt.AlignmentFlag.AlignCenter)
        welcome_buttons = QHBoxLayout()
        welcome_buttons.addStretch()
        self.welcome_import_button = QPushButton("Import a catalog file…")
        self.welcome_import_button.setToolTip(
            "A catalog is a plain-text list of repositories you can read before adding it."
        )
        self.welcome_import_button.clicked.connect(self.settings_page.import_catalog)
        welcome_buttons.addWidget(self.welcome_import_button)
        try:
            count = len(read_catalog(EXAMPLE_CATALOG).entries)
        except (OSError, ValueError):
            count = 0
        self.welcome_example_button = QPushButton(
            f"Try an example list (adds {count} creative apps: painting, 3D, CAD)"
            if count
            else "Try an example list"
        )
        self.welcome_example_button.setToolTip(
            "Add the ArtCraft apps and SolidCraft, a ready-made example list."
        )
        self.welcome_example_button.clicked.connect(self.settings_page.import_example_catalog)
        welcome_buttons.addWidget(self.welcome_example_button)
        self.welcome_tips: dict[QAbstractButton, str] = {
            button: button.toolTip()
            for button in (
                self.welcome_add_button,
                self.welcome_import_button,
                self.welcome_example_button,
            )
        }
        welcome_buttons.addStretch()
        welcome_layout.addLayout(welcome_buttons)
        welcome_layout.addStretch()
        layout.addWidget(self.welcome, stretch=1)

    def build_footer(self, body_layout: QVBoxLayout) -> None:
        """Status line with progress, cancel and orphans buttons."""
        self.footer = StatusMessage()
        self.footer.changed.connect(self.update_status_line)
        self.footer.expired.connect(self.expire_footer)
        self.status_line = QWidget()
        progress_row = QHBoxLayout(self.status_line)
        progress_row.setContentsMargins(22, 0, 22, 12)
        progress_row.addWidget(self.footer, stretch=1)
        self.status_line.setMinimumHeight(self.footer.fontMetrics().lineSpacing() + 16)
        self.progress_bar = QProgressBar()
        self.progress_bar.setAccessibleName("Task progress")
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
        body_layout.addWidget(self.status_line)
        self.update_status_line()

    def setup_keyboard(self) -> None:
        """Shortcuts, focus behaviour and the order Tab walks through the controls."""
        for sequence, handler in (
            ("Ctrl+F", self.focus_search),
            ("Esc", self.go_back),
            ("F5", self.check_shortcut),
            ("Ctrl+R", self.check_shortcut),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(handler)
        for button in self.findChildren(QAbstractButton):
            self.keyboard_button(button)
        QWidget.setTabOrder(self.version_button, self.search)

    @staticmethod
    def keyboard_button(button: QAbstractButton) -> None:
        """Focus a button with Tab only, so a mouse click leaves no ring; Enter presses it."""
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        if isinstance(button, QPushButton):
            button.setAutoDefault(True)

    def focus_search(self) -> None:
        if self.pages.currentIndex() != 0:
            self.show_view(self.view)
        self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search.selectAll()

    def go_back(self) -> None:
        """Escape clears a search first, then leaves the App, Add or Settings page."""
        page = self.pages.currentWidget()
        if page is self.list_page:
            if self.search.text() and self.search.hasFocus():
                self.search.clear()
            return
        if page in (self.app_page, self.add_page, self.settings_page):
            self.show_view(self.view)

    def check_shortcut(self) -> None:
        """F5 and Ctrl+R press Check for updates when it is available."""
        if self.check_button.isEnabled():
            self.check_for_updates()

    def update_status_line(self) -> None:
        """Keep the bottom row's height, so the list above does not jump as messages come."""
        self.footer.setVisible(bool(self.footer.text()))
        self.status_line.setVisible(True)

    def expire_footer(self) -> None:
        if self.task is None and not self.checks.busy:
            self.footer.setText("")
        else:
            self.footer.timer.start(self.footer.LIFETIME_MS)

    def schedule_update_checks(self) -> None:
        hours = check_interval_hours(self.settings)
        if hours:
            self.update_timer.start(hours * 60 * 60 * 1000)
        else:
            self.update_timer.stop()

    def scheduled_check(self) -> None:
        """Check installed apps on the chosen schedule, without disturbing a running task."""
        self.offline_attempts = 0
        self.refresh_menu_names()
        self.automatic_self_check(notify=True)
        if (
            not self.busy
            and not self.checks.busy
            and not self.close_after_release
            and not self.quitting
            and self.installed
            and not github_status.quota_low()
        ):
            self.notify_pending = True
            self.refresh_releases()
            self.notify_pending = self.notify_pending and self.checks.busy

    def update_github_status(self) -> None:
        label, details, limited = github_request_status()
        allowance = github_status.allowance()
        offline = not limited and bool(self.offline_apps())
        if offline:
            details = "Couldn't reach GitHub. Rows show what was known before.\n" + details
        self.github_label.setText(
            "GitHub: limited"
            if limited
            else "GitHub: offline"
            if offline
            else f"GitHub {allowance[0]} of {allowance[1]} left"
            if allowance
            else "GitHub: not used yet"
            if label == "GitHub status"
            else label
        )
        self.github_meter.setVisible(allowance is not None and not offline)
        if allowance:
            self.github_meter.setRange(0, max(1, allowance[1]))
            self.github_meter.setValue(allowance[0])
        self.status_label.setText(self.check_summary())
        tooltip = details + "\nClick to choose how often to check."
        self.status_block.setToolTip(tooltip)
        self.status_block.setAccessibleDescription(
            f"{self.status_label.text()}. {self.github_label.text()}.\n{tooltip}"
        )
        self.update_retry_countdown()
        if self.status_block.property("limited") != limited:
            self.status_block.setProperty("limited", limited)
            for widget in (self.status_block, self.github_label, self.github_meter):
                widget.style().unpolish(widget)
                widget.style().polish(widget)

    def retry_message(self) -> str:
        """The footer line while automatic checks wait to try GitHub again."""
        seconds = self.offline_timer.remainingTime() // 1000 + 1
        wait = f"in {seconds} seconds" if seconds > 1 else "shortly"
        return f"Couldn't reach GitHub. Trying again {wait}…"

    def update_retry_countdown(self) -> None:
        """Count the wait down live while that message is the one showing."""
        if (
            self.offline_timer.isActive()
            and self.task is None
            and self.footer.text().startswith("Couldn't reach GitHub. Trying again")
        ):
            text = self.retry_message()
            if text != self.footer.text():
                self.footer.setText(text)

    def check_summary(self) -> str:
        """Describe the last release check and, when scheduled, the next one."""
        last = max((info.checked_at for info in self.release_infos.values()), default=0)
        scheduled = self.update_timer.isActive()
        if not last:
            return "Not checked yet" if scheduled else "Automatic checks off"
        text = f"Checked {when_text(last)}"
        if scheduled:
            upcoming = datetime.now().timestamp() + self.update_timer.remainingTime() / 1000
            text += f" · next {datetime.fromtimestamp(upcoming):%H:%M}"
        return text

    def create_row(self, app: App) -> AppRow:
        card = AppRow(app, self.library.icon_path(app))
        card.setParent(self.app_list)
        card.details_requested.connect(self.open_app)
        card.launch_requested.connect(self.launch_app)
        card.force_close_requested.connect(self.force_close_app)
        card.running_check = self.app_running
        card.install_requested.connect(self.install_app)
        card.replace_requested.connect(self.replace_file)
        card.shortcut_requested.connect(self.create_shortcut)
        card.rollback_requested.connect(self.rollback_app)
        card.remove_requested.connect(self.remove_app)
        card.forget_requested.connect(self.forget_missing)
        card.unlist_requested.connect(self.remove_from_library)
        card.pin_requested.connect(self.toggle_pin)
        card.hold_requested.connect(self.toggle_hold)
        card.build_requested.connect(self.choose_build)
        card.cancel_requested.connect(self.cancel_task)
        card.move_requested.connect(self.move_app)
        for button in card.findChildren(QAbstractButton):
            self.keyboard_button(button)
        return card

    def sorted_apps(self, apps: Iterable[App]) -> list[App]:
        """Pinned apps first, then the user's order; apps without a place follow, A-Z."""
        custom = {app_id: index for index, app_id in enumerate(custom_order(self.settings))}
        catalog = {app.id: index for index, app in enumerate(self.apps)}

        def key(app: App) -> tuple[object, ...]:
            return (
                app.id not in self.pinned,
                custom.get(app.id, len(custom)),
                app.name.casefold(),
                catalog.get(app.id, 0),
            )

        return sorted(apps, key=key)

    def legacy_order(self, mode: str) -> list[str]:
        """The order the retired Sort box showed for `mode`, for one-time migration."""
        catalog = {app.id: index for index, app in enumerate(self.apps)}

        def key(app: App) -> tuple[object, ...]:
            installed = app.id in self.installed
            name = app.name.casefold()
            if mode == "installed":
                return (not installed, name)
            if mode == "updates":
                return (not self.has_update(app.id), not installed, name)
            if mode == "added":
                return ()
            return (name,)

        return [app.id for app in sorted(self.apps, key=lambda app: (key(app), catalog[app.id]))]

    def migrate_legacy_sort(self) -> None:
        """Turn a saved Sort choice into the equivalent custom order, then forget the choice."""
        if not self.apps or not self.settings.contains("library/sort"):
            return
        mode = str(self.settings.value("library/sort", "name"))
        if not custom_order(self.settings):
            self.settings.setValue("library/order", json.dumps(self.legacy_order(mode)))
        self.settings.remove("library/sort")

    def sort_alphabetically(self) -> None:
        if len(self.apps) < 2:
            return
        accepted, _ = MessageBox.confirm(
            self,
            "Sort the library A–Z?",
            "This replaces your own order. Pinned apps stay on top.",
            "Replace my order",
        )
        if not accepted:
            return
        ids = [app.id for app in sorted(self.apps, key=lambda app: app.name.casefold())]
        self.settings.setValue("library/order", json.dumps(ids))
        self.footer.setText("Library sorted A–Z.")
        self.refresh()

    def reorder_enabled(self) -> bool:
        """Dragging works in the Library, with All or Installed, but not while searching."""
        return self.view == "library" and not self.search.text().strip()

    def move_app(self, app_id: str, delta: int) -> None:
        ids = [row.app.id for row in self.app_list.rows]
        if app_id in ids:
            source = ids.index(app_id)
            if delta > 0:
                self.reorder_app(app_id, source + delta, after=True)
            else:
                self.reorder_app(app_id, source + delta)

    def drop_app(self, app_id: str, before: int) -> None:
        ids = [row.app.id for row in self.app_list.rows]
        if app_id in ids:
            source = ids.index(app_id)
            self.reorder_app(app_id, before if before <= source else before - 1)

    def reorder_app(self, app_id: str, index: int, after: bool = False) -> None:
        """Move an app within the shown apps, leaving the hidden ones where they are.

        `index` is a position among the shown apps without the moved one. The app goes
        directly before the app there (or directly after the one before it when `after`
        is set, or after the last shown app at the end), within its pinned group.
        """
        if not self.reorder_enabled():
            return
        shown = [row.app.id for row in self.app_list.rows]
        if app_id not in shown:
            return
        source = shown.index(app_id)
        ids = [item for item in shown if item != app_id]
        pinned = sum(item in self.pinned for item in ids)
        if app_id in self.pinned:
            index = min(max(index, 0), pinned)
        else:
            index = min(max(index, pinned), len(ids))
        if index == source or not ids:
            return
        full = [app.id for app in self.sorted_apps(self.apps)]
        full.remove(app_id)
        if after or index >= len(ids):
            full.insert(full.index(ids[index - 1]) + 1, app_id)
        else:
            full.insert(full.index(ids[index]), app_id)
        if full == [app.id for app in self.sorted_apps(self.apps)]:
            return
        self.settings.setValue("library/order", json.dumps(full))
        self.footer.setText("Custom order saved.")
        self.refresh()

    def toggle_pin(self, app_id: str) -> None:
        if app_id in self.pinned:
            self.pinned.discard(app_id)
        elif len(self.pinned) < MAX_PINNED:
            self.pinned.add(app_id)
        else:
            self.footer.setText(f"You can pin up to {MAX_PINNED} apps.")
        self.settings.setValue("library/pinned", json.dumps(sorted(self.pinned)))
        self.refresh()

    def toggle_hold(self, app_id: str) -> None:
        app = next((app for app in self.apps if app.id == app_id), None)
        if app is None or self.busy:
            return
        held = not app.hold_updates
        try:
            self.library.set_update_hold(app_id, held)
        except (OSError, ValueError) as error:
            MessageBox.warning(
                self, f"Couldn't change the update hold for {app.name}", describe_error(error)
            )
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

    def quit_launcher(self) -> None:
        self.show_launcher()
        self.quitting = True
        if not self.close() and not self.close_after_task and not self.close_after_release:
            self.quitting = False

    def refresh(self) -> None:
        update_count = sum(self.has_update(app.id) for app in self.apps)
        self.library_count.setText(str(len(self.apps)))
        self.updates_count.setText(str(update_count))
        self.updates_count.setVisible(update_count > 0)
        self.nav_updates.setAccessibleName(
            f"Updates, {update_count} waiting" if update_count else "Updates"
        )
        self.filter_installed.setText(f"Installed ({len(self.installed)})")
        updates_view = self.view == "updates"
        own = self.self_state.version
        self.update_tray(update_count, own)
        query = self.search.text().strip().casefold()
        visible = [
            app
            for app in self.apps
            if (
                self.has_update(app.id)
                if updates_view
                else app.id in self.installed or not self.installed_only
            )
            and query in f"{app.name} {app.description} {app.repo}".casefold()
        ]
        visible = self.sorted_apps(visible)
        ids = {app.id for app in visible}
        for app_id, card in self.cards.items():
            card.icon.setPixmap(icon_pixmap(self.library.icon_path(card.app), 40))
            card.set_pinned(app_id in self.pinned)
            self.apply_row(card)
            card.setVisible(app_id in ids)
            card.draggable = False
            for action in (card.move_up_action, card.move_down_action):
                action.setVisible(False)
                action.setEnabled(False)
        shown = [self.cards[app.id] for app in visible]
        reorder = self.reorder_enabled()
        self.app_list.reorderable = reorder
        if reorder:
            hint = ""
        elif updates_view:
            hint = "Reordering is available in the Library"
        else:
            hint = "Clear the search to reorder"
        for index, row in enumerate(shown):
            row.set_reorder(reorder and len(shown) > 1, hint if len(shown) > 1 else "")
            row.move_up_action.setVisible(row.draggable)
            row.move_down_action.setVisible(row.draggable)
            row.move_up_action.setEnabled(
                reorder and index > 0 and shown[index - 1].pinned == row.pinned
            )
            row.move_down_action.setEnabled(
                reorder and index + 1 < len(shown) and shown[index + 1].pinned == row.pinned
            )
        self.nav_add.setEnabled(not self.busy)
        self.nav_add.setToolTip(WAIT_TIP if self.busy else "")
        self.settings_page.update_state()
        self.add_page.update_state()
        count = len(self.apps)
        self.search.setPlaceholderText(
            "Search updates" if updates_view else f"Search {count} app{'' if count == 1 else 's'}"
        )
        self.update_check_button()
        self.options_button.setEnabled(len(self.apps) > 1 and not self.busy)
        self.options_button.setToolTip(WAIT_TIP if self.busy else SORT_TIP)
        ready, choose = self.update_groups()
        actionable = any(
            app.id in self.releases and self.installed[app.id].managed for app in ready
        )
        self.update_all_button.setVisible(actionable or bool(choose))
        self.update_all_button.setText("Update all" if actionable or not choose else "Choose build")
        self.update_all_button.setEnabled(not self.busy)
        self.update_all_button.setToolTip(WAIT_TIP if self.busy else "")
        self.review_button.setVisible(not updates_view)
        self.banner_close_button.setVisible(not updates_view)
        self.show_self_banner()
        # Updates always keeps it, with Update all; a closed one returns for something new.
        self.banner.setVisible(update_count > 0 and (updates_view or self.new_updates()))
        if update_count > 0:
            self.banner_label.setText(self.banner_text())
        self.app_list.set_rows(shown)
        order: list[QWidget] = [
            self.search,
            self.filter_all,
            self.filter_installed,
            self.options_button,
            self.check_button,
            self.self_notes_button,
            self.self_update_button,
            self.self_close_button,
            self.update_all_button,
            self.review_button,
            self.banner_close_button,
        ]
        for row in shown:
            order += [row.name_button, row.action_button, row.more_button]
        order += [self.cancel_button, self.orphans_button]
        for before, after in zip(order, order[1:], strict=False):
            QWidget.setTabOrder(before, after)
        self.update_status_line()
        self.scroll_area.setVisible(bool(visible))
        show_empty = not visible and bool(self.apps)
        self.empty.setVisible(show_empty)
        self.empty_panel.setVisible(show_empty)
        updates_empty = show_empty and updates_view and not query
        self.empty_checked.setVisible(updates_empty)
        self.empty_check_button.setVisible(updates_empty)
        self.empty_check_button.setEnabled(not self.checks.busy)
        self.empty_check_button.setToolTip(self.check_button.toolTip())
        last = max((info.checked_at for info in self.release_infos.values()), default=0)
        self.empty_checked.setText(f"Last checked {when_text(last)}" if last else "Not checked yet")
        # Nothing to search, filter or sort in an empty library or an empty Updates view.
        welcome = not self.apps
        self.welcome.setVisible(welcome)
        self.search.setVisible(not welcome)
        self.search.setEnabled(bool(visible) or bool(query))
        self.filters.setVisible(not welcome and not updates_view)
        self.options_button.setVisible(not welcome and not updates_view)
        self.check_button.setVisible(not welcome)
        for button in (
            self.welcome_add_button,
            self.welcome_import_button,
            self.welcome_example_button,
        ):
            button.setEnabled(not self.busy)
            button.setToolTip(WAIT_TIP if self.busy else self.welcome_tips[button])
        if welcome and not self.welcome_shown and self.pages.currentWidget() is self.list_page:
            self.welcome_add_button.setFocus()
        self.welcome_shown = welcome
        if self.app_page.app_id is not None:
            if any(app.id == self.app_page.app_id for app in self.apps):
                self.app_page.refresh()
            else:
                self.close_app()
        if query:
            self.empty.setText(
                "No apps match your search in this view.\nTry another name or clear the search."
            )
        elif updates_view:
            self.empty.setText(
                "No updates are waiting."
                if self.installed
                else "No apps are installed yet, so there is nothing to update.\n"
                "Install an app from the Library to get started."
            )
        elif self.installed_only:
            self.empty.setText(
                "No apps are installed yet. Choose All, then press Install on an app."
            )
        else:
            self.empty.setText("No apps match your search.\nTry another name or clear the search.")

    def status_for(self, app_id: str) -> AppStatus:
        app = next(app for app in self.apps if app.id == app_id)
        return app_status(
            app,
            self.installed.get(app_id),
            self.releases.get(app_id),
            self.check_errors.get(app_id, ""),
            self.release_infos.get(app_id),
            self.checks.is_checking(app_id),
            self.release_errors.get(app_id, ""),
            self.working.get(app_id, ""),
            self.missing.get(app_id),
        )

    def apply_row(self, card: AppRow) -> None:
        app_id = card.app.id
        working = self.working.get(app_id, "")
        process = self.library.processes.get(app_id)
        card.set_state(
            self.installed.get(app_id),
            self.releases.get(app_id),
            self.busy,
            self.check_errors.get(app_id, ""),
            self.release_infos.get(app_id),
            self.checks.is_checking(app_id),
            self.release_errors.get(app_id, ""),
            working,
            self.missing.get(app_id),
            process is not None and process.poll() is None,
            self.work_progress if working and working != WAITING else None,
        )

    def update_groups(self) -> tuple[list[App], list[App]]:
        """Apps with a waiting update: those Update all can install, and those needing a build."""
        ready: list[App] = []
        choose: list[App] = []
        for app in self.apps:
            if self.has_update(app.id):
                (choose if self.status_for(app.id).mode == "build" else ready).append(app)
        return ready, choose

    def update_check_button(self) -> None:
        """Check for updates stays available with no apps: it then checks AppGlider itself."""
        self.check_button.setEnabled(not self.checks.busy)
        if self.checks.busy:
            tip = "Checking for updates…"
        elif not self.installed:
            tip = "No apps are installed yet, so this checks only AppGlider itself (F5)"
        else:
            tip = f"{CHECK_TOOLTIP} (F5)"
        self.check_button.setToolTip(tip)

    def banner_action(self) -> None:
        """The banner's main button: Update all, or Choose build when only those are waiting."""
        ready, choose = self.update_groups()
        actionable = any(
            app.id in self.releases and self.installed[app.id].managed for app in ready
        )
        if actionable or not choose:
            self.update_all()
        else:
            self.choose_build(choose[0].id)

    @property
    def busy(self) -> bool:
        """A window task, file copy or catalog download is running; file changes wait."""
        # The pages are built after the first reads of this, so the Add page may not exist yet.
        add_page = getattr(self, "add_page", None)
        settings_page = getattr(self, "settings_page", None)
        return (
            self.task is not None
            or (add_page is not None and add_page.copying)
            or (settings_page is not None and settings_page.importing)
        )

    @property
    def installed_only(self) -> bool:
        return self.filter_installed.isChecked()

    def show_view(self, view: str) -> None:
        """Show the whole library or only the apps with updates."""
        if view not in ("library", "updates"):
            raise ValueError(f"Unknown view: {view}")
        self.view = view
        self.show_list()
        (self.nav_updates if view == "updates" else self.nav_library).setChecked(True)
        self.page_title.setText("Updates" if view == "updates" else "Library")
        self.refresh()

    def waiting_versions(self) -> dict[str, str]:
        """The newest version waiting for each app, including those that need a build choice."""
        waiting: dict[str, str] = {}
        for app in self.apps:
            if not self.has_update(app.id):
                continue
            info = self.release_infos.get(app.id)
            choice = info.choice(app.include_prereleases) if info else None
            if app.id in self.releases:
                waiting[app.id] = self.releases[app.id].version
            elif choice:
                waiting[app.id] = choice.version
        return waiting

    def new_updates(self) -> bool:
        """Whether an update is waiting that was not on the banner when it was closed."""
        dismissed = dismissed_versions(self.settings)
        return any(dismissed.get(key) != value for key, value in self.waiting_versions().items())

    def dismiss_banner(self) -> None:
        dismissed = dismissed_versions(self.settings)
        own = dismissed.get(self_update.NOTIFY_KEY)
        dismissed = self.waiting_versions() | ({self_update.NOTIFY_KEY: own} if own else {})
        self.settings.setValue("banners/dismissed", json.dumps(dismissed))
        self.refresh()
        self.search.setFocus()

    def dismiss_self_banner(self) -> None:
        version = self.self_banner_version()
        if version:
            dismissed = dismissed_versions(self.settings)
            dismissed[self_update.NOTIFY_KEY] = version
            self.settings.setValue("banners/dismissed", json.dumps(dismissed))
        self.refresh()
        self.search.setFocus()

    def banner_text(self) -> str:
        """Summarise the waiting updates, shortened the way the tray notification is."""
        ready, choose = self.update_groups()

        def named(apps: list[App]) -> str:
            names = []
            for app in apps:
                release = self.releases.get(app.id)
                version = (
                    f" {'v' if release.version[:1].isdigit() else ''}{release.version}"
                    if release and app in ready
                    else ""
                )
                names.append(html.escape(f"{app.name}{version}"))
            if len(names) > 3:
                return f"{', '.join(names[:3])}, and {len(names) - 3} more"
            if len(names) > 1:
                return f"{', '.join(names[:-1])} and {names[-1]}"
            return names[0] if names else ""

        if not ready:
            count = len(choose)
            lead = f"<b>{count} update{' needs' if count == 1 else 's need'} a choice.</b>"
            return f"{lead} {named(choose)}: choose which build to install."
        count = len(ready)
        text = f"<b>{count} update{'s' if count != 1 else ''} ready.</b> {named(ready)}."
        if choose:
            text += (
                f" {len(choose)} more {'needs' if len(choose) == 1 else 'need'} a build choice: "
                f"{named(choose)}."
            )
        return text

    def open_app(self, app_id: str) -> None:
        """Show the App page; the sidebar keeps the view it was opened from."""
        if not any(app.id == app_id for app in self.apps):
            return
        if self.pages.currentWidget() is not self.app_page:
            self.list_scroll = self.scroll_area.verticalScrollBar().value()
            self.return_to = app_id
        self.app_page.show_app(app_id, "Updates" if self.view == "updates" else "Library")
        self.show_page(self.app_page)
        (self.nav_updates if self.view == "updates" else self.nav_library).setChecked(True)
        if isinstance(QApplication.focusWidget(), QAbstractButton):
            # A keyboard user stays oriented; a mouse click leaves no focus ring behind.
            self.app_page.back_button.setFocus()

    def close_app(self) -> None:
        """Return to the list with the view, filter, search and scroll position as they were."""
        if self.pages.currentWidget() is not self.app_page:
            return
        keyboard = isinstance(QApplication.focusWidget(), QAbstractButton)
        self.app_page.app_id = None
        self.show_page(self.list_page)
        QTimer.singleShot(
            0, self, lambda: self.scroll_area.verticalScrollBar().setValue(self.list_scroll)
        )
        self.scroll_area.verticalScrollBar().setValue(self.list_scroll)
        card = self.cards.get(self.return_to or "")
        self.return_to = None
        if keyboard and card is not None and card.isVisibleTo(self.list_page):
            card.name_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def show_list(self) -> None:
        """Return to the Library or Updates list from whichever page is showing."""
        if self.pages.currentWidget() is self.app_page:
            self.close_app()
        else:
            self.show_page(self.list_page)

    def show_page(self, page: QWidget) -> None:
        """Switch pages; a mouse user is not left with a focus ring on the new page."""
        keyboard = isinstance(QApplication.focusWidget(), QAbstractButton)
        self.pages.setCurrentWidget(page)
        if not keyboard:
            # Parks focus on the page itself: Tab still enters it, and no button shows a ring.
            page.setFocus(Qt.FocusReason.OtherFocusReason)

    def page_changed(self) -> None:
        if self.pages.currentWidget() is not self.add_page:
            self.add_page.leave()

    def open_settings(self, section: int | str = "General") -> None:
        """Show the Settings page at a section; the old dialog's numbers still work."""
        name = LEGACY_SECTIONS[section] if isinstance(section, int) else section
        self.settings_page.show_section(name)
        self.show_page(self.settings_page)
        self.nav_settings.setChecked(True)
        if isinstance(QApplication.focusWidget(), QAbstractButton):
            self.settings_page.focus_section()

    def open_add_app(self) -> None:
        if self.busy:
            return
        self.add_page.start()
        self.show_page(self.add_page)
        self.add_page.url.setFocus()
        self.nav_add.setChecked(True)

    def copy_changed(self) -> None:
        """A file copy on the Add page started or ended: other actions wait, or may go on."""
        self.refresh()
        self.notify_when_idle()

    def copy_failed(self, error: str) -> None:
        """The Add page was left before its file copy failed, so its status line is not seen."""
        self.footer.setText(f"Couldn't add the file: {error}")
        MessageBox.warning(self, "Couldn't add the file", error)

    def app_added(self, app_id: str, warning: str) -> None:
        self.catalog_changed()
        app = next((app for app in self.apps if app.id == app_id), None)
        if self.pages.currentWidget() is self.add_page:
            self.open_app(app_id)  # not when the user left the page while the file was copied
        if app is not None:
            tail = "" if app.manual else " Install it from its page."
            self.footer.setText(f"Added {app.name}.{tail}{warning}")

    def app_added_install(self, app_id: str, warning: str) -> None:
        """Add the app, then install it straight away ("Add and install")."""
        self.catalog_changed()
        if not any(app.id == app_id for app in self.apps):
            return
        if self.pages.currentWidget() is self.add_page:
            self.open_app(app_id)
        self.install_app(app_id)
        if warning:
            self.footer.setText(f"{self.footer.text()}{warning}")

    def choose_build(self, app_id: str) -> None:
        """Open the App page and ask which build of the app to use."""
        self.open_app(app_id)
        self.app_page.choose_build()

    def review_orphans(self) -> None:
        if self.busy:
            return
        dialog = OrphansDialog(self.orphans, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected():
            try:
                self.library.discard_orphans(dialog.selected())
            except (OSError, ValueError) as error:
                MessageBox.warning(self, "Couldn't update the library", describe_error(error))
            self.scan_installed()
        dialog.deleteLater()

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
            missing = self.library.missing()
            orphans = self.library.orphans()
            self.library.reap_processes()
        except (OSError, ValueError) as error:
            self.footer.setText(describe_error(error))
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
                        self.cards[app.id] = self.create_row(app)
                self.apps = apps
            cache_dir = self.library.data_dir / "release-cache"
            for app in apps:
                if app.manual:
                    continue  # no repository, so no release information
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
            self.missing = missing
            self.migrate_legacy_sort()
        self.refresh()

    def launch_app(self, app_id: str) -> None:
        app = next(app for app in self.apps if app.id == app_id)
        installed = self.installed.get(app_id)
        if installed is None:
            self.scan_installed()
            return
        if app_id in self.working:
            self.footer.setText(f"Wait for {app.name} to finish, then launch it.")
            return
        self.library.reap_processes()
        if self.library.processes.get(app_id) is not None:
            self.footer.setText(f"{app.name} is already running.")
            return
        try:
            log = self.library.launch(app, installed)
        except (OSError, ValueError) as error:
            MessageBox.warning(self, f"Couldn't launch {app.name}", describe_error(error))
            return
        process = self.library.processes[app_id]
        self.footer.setText(f"Launched {app.name}.")
        self.footer.setToolTip(f"Log: {log}")
        page, page_app = self.pages.currentWidget(), self.app_page.app_id

        def check_startup() -> None:
            code = process.poll()
            if code is not None and code != 0:
                self.footer.setText(f"{app.name} exited with code {code}. See {log}")
                if self.pages.currentWidget() is not page or self.app_page.app_id != page_app:
                    return  # the user has moved on; the footer says it
                tail = log_tail(log)
                parts = [fuse_explanation(tail), f"The application exited with code {code}."]
                if tail:
                    parts.append(f"The end of its diagnostic log:\n{tail}")
                parts.append(f"The full log is:\n{log}")
                MessageBox.warning(
                    self,
                    f"{app.name} could not start",
                    "\n\n".join(part for part in parts if part),
                )

        QTimer.singleShot(1800, self, check_startup)

    def start_task(
        self,
        message: str,
        operation: Callable[[Event, Progress], TaskResult],
        *,
        page: AppPage | None = None,
        working: dict[str, str] | None = None,
        failure: str = "",
        cancellable: bool = True,
    ) -> None:
        if self.busy:
            return
        self.failure = failure or self.page_failure(page)
        self.task_name = lower_first(message.rstrip("…. "))
        self.work_progress = None
        self.working = working or {}
        self.task = Task(operation)
        self.task.signals.progress.connect(self.task_progress)
        self.task.signals.finished.connect(self.task_finished)
        self.task.signals.failed.connect(self.task_failed)
        if page is not None:
            # After the window's own handlers, so the page sees the settled state.
            self.task.signals.finished.connect(page.task_done)
            self.task.signals.failed.connect(page.task_error)
        self.footer.setText(message)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()
        self.cancel_button.setText("Cancel")
        self.cancel_button.setEnabled(True)
        self.cancel_button.setVisible(cancellable)
        self.refresh()
        QThreadPool.globalInstance().start(self.task)

    def page_failure(self, page: AppPage | None) -> str:
        """The dialog title for a task an App page started without naming its failure."""
        app = next((app for app in self.apps if page is not None and app.id == page.app_id), None)
        name = app.name if app is not None else "the app"
        kinds = {
            "build": f"Couldn't check the builds for {name}",
            "icons": f"Couldn't look for icons for {name}",
            "check": f"Couldn't check releases for {name}",
        }
        return kinds.get(page.pending if page is not None else "", f"Couldn't finish for {name}")

    def check_releases(self, app_id: str, page: AppPage | None = None) -> None:
        """A full release check of one app, even an uninstalled one (never downloads the app)."""
        app = next((app for app in self.apps if app.id == app_id), None)
        if app is None or self.busy:
            return

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            info = self.library.following_moves(
                app,
                lambda app: discover_releases(
                    app, cancel, self.library.data_dir / "release-cache", force=True
                ),
            )
            return TaskResult("Release checked.", release_infos={app.id: info})

        self.start_task(
            f"Checking releases for {app.name}…",
            operation,
            page=page,
            failure=f"Couldn't check releases for {app.name}",
        )

    def create_shortcut(self, app_id: str) -> None:
        """Write the menu entry off the Qt thread: it may start the app's runtime to read it."""
        app = next(app for app in self.apps if app.id == app_id)
        if self.menu_task is not None:
            return
        self.menu_app = app
        self.footer.setText(f"Creating menu shortcut for {app.name}…")
        library = self.library
        task = self.menu_task = Lookup(lambda cancel: library.create_shortcut(app))
        task.signals.finished.connect(self.shortcut_created)
        task.signals.failed.connect(self.shortcut_failed)
        QThreadPool.globalInstance().start(task)

    def shortcut_created(self, result: object) -> None:
        self.menu_task = None
        if not self.quitting and self.menu_app is not None:
            name = result if isinstance(result, str) else self.menu_app.name
            self.footer.setText(f"Created menu shortcut: {name}.")

    def shortcut_failed(self, error: str) -> None:
        self.menu_task = None
        if not self.quitting:
            name = self.menu_app.name if self.menu_app is not None else "the app"
            MessageBox.warning(self, f"Couldn't create the menu shortcut for {name}", error)

    def app_running(self, app_id: str) -> bool:
        """Whether an app AppGlider installed is running now, however it was started."""
        app = next((app for app in self.apps if app.id == app_id), None)
        installed = self.installed.get(app_id)
        if app is None or installed is None or not installed.managed:
            return False
        try:
            return self.library.app_running(app)
        except OSError:
            return False

    def offer_force_close(self, app_id: str, message: str) -> None:
        """An action stopped because the app is running: say so, and offer to force close it."""
        app = next((app for app in self.apps if app.id == app_id), None)
        if app is None:
            return
        box = MessageBox(
            QMessageBox.Icon.Warning,
            f"{app.name} is running",
            f"{app.name} is running.",
            parent=self,
        )
        box.setInformativeText(message)
        box.addButton(QMessageBox.StandardButton.Ok)
        force = box.addButton("Force close…", QMessageBox.ButtonRole.ActionRole)
        box.exec()
        if box.clickedButton() is force:
            self.force_close_app(app_id)

    def offer_force_close_many(self, app_ids: list[str]) -> None:
        """Update all skipped running apps: list them, with a Force close… button for each."""
        apps = [app for app in self.apps if app.id in app_ids]
        if not apps:
            return
        names = ", ".join(app.name for app in apps)
        box = MessageBox(
            QMessageBox.Icon.Warning,
            "Some apps are running",
            f"Update all skipped {names} because "
            f"{'it is' if len(apps) == 1 else 'they are'} running.",
            parent=self,
        )
        box.setInformativeText(
            "Close them and press Update all again, or force close them here. "
            "Unsaved work in a force closed app is lost."
        )
        box.addButton(QMessageBox.StandardButton.Ok)
        buttons = {
            box.addButton(f"Force close {app.name}…", QMessageBox.ButtonRole.ActionRole): app.id
            for app in apps[:3]
        }
        box.exec()
        clicked = box.clickedButton()
        if isinstance(clicked, QPushButton) and clicked in buttons:
            self.force_close_app(buttons[clicked])

    def force_close_app(self, app_id: str) -> None:
        """Ask, then close every process of the app off the Qt thread (it may wait seconds)."""
        app = next((app for app in self.apps if app.id == app_id), None)
        if app is None or self.force_task is not None:
            return
        box = MessageBox(
            QMessageBox.Icon.Warning,
            f"Force close {app.name}?",
            f"Force close {app.name}?",
            parent=self,
        )
        box.setInformativeText("Any unsaved work in it will be lost.")
        box.addButton(QMessageBox.StandardButton.Cancel)
        force = box.addButton("Force close", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not force:
            return
        self.footer.setText(f"Closing {app.name}…")
        library = self.library
        task = self.force_task = Lookup(lambda cancel: library.force_close(app))
        self.force_app = app
        task.signals.finished.connect(self.force_closed)
        task.signals.failed.connect(self.force_close_failed)
        QThreadPool.globalInstance().start(task)

    def force_closed(self, closed: object) -> None:
        self.force_task = None
        app = self.force_app
        if self.quitting or app is None:
            return
        self.footer.setText(f"Closed {app.name}." if closed else f"{app.name} is still running.")
        self.app_page.update_running()

    def force_close_failed(self, error: str) -> None:
        self.force_task = None
        if not self.quitting:
            name = self.force_app.name if self.force_app is not None else "the app"
            MessageBox.warning(self, f"Couldn't force close {name}", error)

    def refresh_desktop_database(self) -> None:
        """Let the desktop notice changed menu entries; the tool can take seconds."""
        library = self.library
        QThreadPool.globalInstance().start(Lookup(lambda cancel: library.update_desktop_database()))

    def task_progress(self, message: str, received: int, total: int) -> None:
        self.footer.setText(message)
        self.progress_bar.setRange(0, 100 if total > 0 else 0)
        percent = min(100, int(received * 100 / total)) if total > 0 else None
        if percent is not None:
            self.progress_bar.setValue(percent)
        if percent != self.work_progress:
            self.work_progress = percent
            # The chip of the app being worked on shows the same percentage.
            for app_id, state in self.working.items():
                card = self.cards.get(app_id)
                if card is not None and state != WAITING:
                    self.apply_row(card)

    def reset_task(self) -> None:
        self.task = None
        self.working = {}
        self.work_progress = None
        self.progress_bar.hide()
        self.cancel_button.hide()
        self.scan_installed()

    def show_moves(self) -> None:
        """Reload the catalog after a repository moved during a check, and note it."""
        moves, self.library.moves = self.library.moves, []
        if not moves:
            return
        self.scan_installed()
        self.move_notice = " ".join(
            [self.move_notice]
            + [
                f"{name} moved to {new} on GitHub; AppGlider now uses the new address."
                for name, _, new in moves
            ]
        ).strip()
        self.footer.setText(self.move_notice)

    def with_move_notice(self, message: str) -> str:
        """`message`, followed by any repository move not yet reported with a result."""
        notice, self.move_notice = self.move_notice, ""
        return f"{message} {notice}" if notice else message

    @staticmethod
    def with_warnings(result: TaskResult) -> str:
        """The result's message, followed by what its after-install commands reported."""
        if not result.warnings:
            return result.message
        found = list(result.warnings.values())
        extra = found[0] if len(found) == 1 else f"{len(found)} after-install commands failed."
        return f"{result.message} {extra}".strip()

    def task_finished(self, result: TaskResult) -> None:
        self.show_moves()
        self.apply_results(result)
        self.reset_task()
        self.footer.setText(self.with_move_notice(self.with_warnings(result)))
        if result.message and not self.close_after_task and not self.quitting:
            self.tray_message(result.title or "AppGlider", result.message)
        if result.downloaded is not None:
            self.self_update_downloaded(result.downloaded)
        if result.placed is not None:
            self.self_moved(result.placed)
        if result.stopped and not self.close_after_task:
            MessageBox.warning(self, "Update all stopped", result.message)
        if (
            len(result.build_choices) == 1
            and len(result.choice_apps) <= 1
            and not self.close_after_task
        ):
            self.choose_build(next(iter(result.build_choices)))
        if result.running and not self.close_after_task:
            self.offer_force_close(result.running, result.message)
        if result.skipped_running and not self.close_after_task:
            self.offer_force_close_many(result.skipped_running)
        if result.warnings and not self.close_after_task:
            MessageBox.warning(
                self, "An install command needs a look", "\n\n".join(result.warnings.values())
            )
        if result.errors and not self.close_after_task:
            names = {app.id: app.name for app in self.apps}
            errors = "\n\n".join(
                f"{names.get(key, key)}: {value}" for key, value in result.errors.items()
            )
            MessageBox.warning(self, self.failure, errors)
        if self.close_after_task:
            self.close()
        else:
            self.notify_when_idle()

    def apply_results(self, result: TaskResult) -> None:
        """Take in what a task (or one app of Update all) found and installed."""
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

    def update_step(self, app_id: str, part: TaskResult) -> None:
        """One app of Update all is done: show it now, and start the next one's chip."""
        if self.task is None or app_id not in self.working:
            return
        del self.working[app_id]
        self.work_progress = None
        following = next((key for key, state in self.working.items() if state == WAITING), None)
        if following is not None:
            self.working[following] = "Updating"
        self.apply_results(part)
        self.scan_installed()

    def task_failed(self, error: str) -> None:
        self.show_moves()
        self.reset_task()
        self.footer.setText(
            self.with_move_notice(f"{self.failure}: {error}" if self.failure else error)
        )
        if self.close_after_task:
            self.close()
        else:
            MessageBox.warning(self, self.failure, error)
            self.notify_when_idle()

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

    def startup_check(self) -> None:
        """The first check after the window opens; both parts follow the update schedule choice."""
        self.refresh_menu_names()
        self.refresh_releases()
        self.automatic_self_check()

    def check_for_updates(self) -> None:
        """The toolbar and tray command: a full check of the apps and of AppGlider itself."""
        self.offline_attempts = 0
        self.refresh_menu_names()
        self.start_self_check(explicit=True)
        self.refresh_releases(force=True)

    def tray_check_for_updates(self) -> None:
        """The tray command: as the toolbar's, and a hidden window gets the result as a message."""
        self.tray_check_pending = True
        self.check_for_updates()
        if not self.checks.busy:
            self.tray_check_pending = False

    def tray_check_result(self) -> None:
        """Tell a user who started a check from the tray, with the window hidden, how it went."""
        count = sum(self.has_update(app.id) for app in self.apps)
        if any(app_id in self.installed for app_id in self.release_errors):
            text = "Couldn't check for updates. Try again later."
        elif count:
            text = f"{count} update{'s' if count != 1 else ''} available."
        else:
            text = "Everything is up to date."
        self.tray_message("Update check", text)

    def automatic_self_check(self, *, notify: bool = False) -> None:
        """Startup and scheduled checks of AppGlider itself; none when updates are manual."""
        if check_interval_hours(self.settings):
            self.start_self_check(notify=notify)

    def start_self_check(self, *, explicit: bool = False, notify: bool = False) -> None:
        """Ask GitHub's release page about AppGlider, apart from the window's task slot.

        The answer needs no API allowance, so it also runs with no apps installed. A check
        that is already running is not repeated, but a request for one stays on record.
        """
        if self.quitting or self.close_after_release:
            return
        self.self_explicit = self.self_explicit or explicit
        if explicit:
            dismissed = dismissed_versions(self.settings)
            if dismissed.pop(self_update.NOTIFY_KEY, None) is not None:
                self.settings.setValue("banners/dismissed", json.dumps(dismissed))
        self.self_notify = self.self_notify or notify
        if self.self_task is None:
            task = self.self_task = Lookup(self_update.check)
            task.signals.finished.connect(self.self_check_done)
            task.signals.failed.connect(self.self_check_failed)
            QThreadPool.globalInstance().start(task)
        self.settings_page.update_self_update()

    def refresh_shortcuts(self) -> None:
        """Once at startup, give older menu entries their app's own details, off the Qt thread."""
        if self.quitting or self.shortcut_task is not None:
            return
        library = self.library
        task = self.shortcut_task = Lookup(library.refresh_shortcuts)
        task.signals.finished.connect(self.shortcuts_refreshed)
        task.signals.failed.connect(self.shortcuts_refreshed)
        QThreadPool.globalInstance().start(task)

    def shortcuts_refreshed(self, result: object) -> None:
        self.shortcut_task = None

    def refresh_menu_names(self) -> None:
        """Add or drop the "(AppGlider)" name suffix as other menu entries come and go."""
        if self.quitting or self.names_task is not None:
            return
        library = self.library
        task = self.names_task = Lookup(library.refresh_shortcut_names)
        task.signals.finished.connect(self.menu_names_refreshed)
        task.signals.failed.connect(self.menu_names_refreshed)
        QThreadPool.globalInstance().start(task)

    def menu_names_refreshed(self, result: object) -> None:
        self.names_task = None

    def self_check_done(self, tag: object) -> None:
        self.self_task = None
        if self.quitting:
            return
        explicit, self.self_explicit = self.self_explicit, False
        self.self_retry = False
        self.self_state = self_update.SelfUpdate(time.time(), tag if isinstance(tag, str) else "")
        if explicit and not self.installed and self.task is None and not self.checks.busy:
            version = self.self_state.version
            self.footer.setText(
                f"AppGlider {version} is available." if version else "AppGlider is up to date."
            )
        self.finish_self_check()

    def self_check_failed(self, error: str) -> None:
        self.self_task = None
        if self.quitting:
            return
        explicit, self.self_explicit = self.self_explicit, False
        # Offline, this reads "did not name a latest release", so any background failure retries.
        self.self_retry = not explicit
        if explicit:
            # Background checks stay silent; only a check the user asked for reports failure.
            self.self_state = replace(self.self_state, checked_at=time.time(), failed=True)
            if self.task is None and not self.checks.busy:
                self.footer.setText("Couldn't check for a new AppGlider version.")
        self.finish_self_check()

    def finish_self_check(self) -> None:
        self.retry_when_offline()
        self.refresh()
        self.settings_page.update_self_update()
        self.notify_when_idle()

    def notify_when_idle(self) -> None:
        """Announce waiting updates once the checks a scheduled run started have all finished."""
        if self.offline_timer.isActive():
            return
        if (self.notify_pending or self.self_notify) and self.self_task is None:
            if not self.checks.busy and not self.busy:
                self.notify_pending = self.self_notify = False
                self.notify_updates()

    def self_banner_version(self) -> str:
        ready = self.self_ready
        return ready.version if ready else self.self_state.version

    def show_self_banner(self) -> None:
        ready = self.self_ready
        version = self.self_banner_version()
        closed = dismissed_versions(self.settings).get(self_update.NOTIFY_KEY) == version
        self.self_banner.setVisible(bool(version) and not closed)
        if not version or closed:
            return
        shown = html.escape(version)
        if ready:
            self.self_banner_label.setText(
                f"<b>AppGlider {shown}</b> is ready. It starts the next time you open AppGlider."
            )
            self.self_notes_button.hide()
            self.self_update_button.setText("Restart now")
            self.self_update_button.setAccessibleName(f"Restart now to use AppGlider {version}")
            self.self_update_button.setEnabled(not self.busy and not self.checks.busy)
            self.self_update_button.setToolTip(
                WAIT_TIP
                if self.busy
                else "Waits for the update check to finish."
                if self.checks.busy
                else ""
            )
            return
        image = self_update.running_image()
        replaceable = image is not None and self_update.can_replace(image)
        self.self_banner_label.setText(f"<b>AppGlider {shown}</b> is available")
        self.self_notes_button.show()
        self.self_notes_button.setAccessibleName(f"What's new in AppGlider {version}")
        self.self_update_button.setText(
            "Update AppGlider" if replaceable else "Releases on GitHub ↗"
        )
        self.self_update_button.setAccessibleName(
            "Update AppGlider" if replaceable else "AppGlider releases on GitHub"
        )
        self.self_update_button.setToolTip(WAIT_TIP if self.busy and replaceable else "")
        self.self_update_button.setEnabled(not self.busy or not replaceable)

    def open_self_release(self) -> None:
        QDesktopServices.openUrl(QUrl(self.self_state.url))

    def self_button_clicked(self) -> None:
        if self.self_ready:
            self.restart_self()
        else:
            self.update_self()

    def update_self(self) -> None:
        """Download the new AppImage next to the running one; else open the release page."""
        image = self_update.running_image()
        if image is None or not self_update.can_replace(image):
            self.open_self_release()
            return
        tag, version = self.self_state.tag, self.self_state.version
        if self.busy or not version:
            return
        cache_dir = self.library.data_dir / "release-cache"

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            done = self_update.download_update(image, tag, cache_dir, cancel, progress)
            return TaskResult(f"AppGlider {done.version} is ready.", downloaded=done)

        self.start_task(
            f"Downloading AppGlider {version}…", operation, failure="Couldn't update AppGlider"
        )

    def self_update_downloaded(self, done: self_update.Downloaded) -> None:
        """Point the menu and login entries at the new file and offer the restart."""
        self_update.record(self.settings, done)
        self.self_ready = self_update.Ready(done.new, done.version)
        self.footer.setText(" ".join([self.footer.text(), *self.point_entries_at(done.new)]))
        self.refresh()
        self.settings_page.update_self_update()

    def point_entries_at(self, image: Path) -> list[str]:
        """Repoint the menu and login entries; returns what could not be updated."""
        problems = []
        try:
            self.library.create_launcher_shortcut([str(image)])
        except (OSError, ValueError) as error:
            problems.append(f"The menu shortcut was not updated: {describe_error(error)}")
        try:
            if self.library.autostart_enabled():
                self.library.enable_autostart([str(image)])
        except (OSError, ValueError) as error:
            problems.append(f"The login entry was not updated: {describe_error(error)}")
        return problems

    def move_self(self, folder: Path) -> None:
        """Copy the running AppImage into `folder`, then start the copy, which removes this one."""
        image = self_update.running_image()
        if image is None or self.busy:
            return
        if self.checks.busy:
            self.footer.setText(WAIT_FOR_CHECK)
            return

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            target = folder / image.name
            older = target.exists() and not self_update.same_file(target, image)
            if older and replaceable(target) and running(target):
                raise ValueError(f"The copy of AppGlider in {folder} is running. Close it first.")
            # A copy of the same name already there is an older AppGlider: the new one replaces it.
            placed = place_appimage(image, folder, cancel, replace=True)
            return TaskResult(f"AppGlider is now in {folder}.", placed=placed)

        self.start_task("Moving AppGlider…", operation, failure="Couldn't move AppGlider")

    def self_moved(self, placed: Path) -> None:
        """Remember the old file for removal, repoint the entries and start the copy."""
        image = self_update.running_image()
        if image is None:
            return
        self_update.record_move(self.settings, image, placed)
        problems = self.point_entries_at(placed)
        self.footer.setText(" ".join([self.footer.text(), *problems]))
        self.start_image(placed, "AppGlider in its new folder")

    def restart_self(self) -> None:
        """Start the downloaded AppGlider, which waits for this one to quit, then quit."""
        ready = self.self_ready
        if ready is None or self.busy:
            return
        if self.checks.busy:
            self.footer.setText(WAIT_FOR_CHECK)
            return
        self.start_image(ready.path, f"AppGlider {ready.version}")

    def start_image(self, path: Path, name: str) -> None:
        """Start another AppGlider file, which waits for this one to quit, then quit."""
        try:
            check_appimage(path)
            if not os.access(path, os.X_OK):
                raise ValueError("The new file is not executable.")
        except (OSError, ValueError) as error:
            self.start_failed(name, error)
            return
        try:
            # Like app launches: a new session with no inherited descriptors. An inherited
            # handle on this image's mount would keep the old file busy, so it could not be
            # removed once the new version runs.
            subprocess.Popen(
                [str(path), self_update.AFTER_UPDATE_FLAG],
                cwd=path.parent,
                env=clean_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as error:
            self.start_failed(name, error)
            return
        self.quit_launcher()

    def start_failed(self, name: str, error: Exception) -> None:
        """AppGlider's new file did not start: a dialog, since the user must act on it."""
        text = describe_error(error)
        self.footer.setText(f"Couldn't start {name}: {text}")
        MessageBox.warning(self, f"Couldn't start {name}", text)

    def refresh_releases(self, *, force: bool = False) -> None:
        if self.checks.busy or self.close_after_release:
            return
        self.background_enabled = True
        self.scan_installed()
        if not self.installed:
            if force and self.apps:
                self.footer.setText(
                    "No apps are installed yet, so only AppGlider itself is being checked."
                )
            return
        self.checks.force = force
        self.checks.add([app.id for app in self.apps if app.id in self.installed])

    def release_operation(self, app: App, force: bool) -> Callable[[Event, Progress], TaskResult]:
        def operation(cancel: Event, progress: Progress) -> TaskResult:
            info = self.library.following_moves(
                app,
                lambda app: discover_releases(
                    app, cancel, self.library.data_dir / "release-cache", force=force, cheap=True
                ),
            )
            return TaskResult("Release checked.", release_infos={app.id: info})

        return operation

    def checkable_app(self, app_id: str) -> App | None:
        if app_id not in self.installed:
            return None
        app = next((app for app in self.apps if app.id == app_id), None)
        # An app added from a file has no repository: it is never checked.
        return None if app is None or app.manual else app

    def release_check_started(self, app_id: str) -> None:
        if self.task is None:
            app = next(app for app in self.apps if app.id == app_id)
            self.footer.setText(f"Checking releases for {app.name}…")
        self.refresh()

    def release_check_done(
        self, app_id: str, variant: str, result: TaskResult | None, error: str
    ) -> None:
        self.show_moves()
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
        self.retry_when_offline()
        self.refresh()
        self.notify_when_idle()
        if self.tray_check_pending:
            self.tray_check_pending = False
            self.tray_check_result()
        offline = bool(self.offline_apps())
        if self.task is None and self.offline_timer.isActive():
            self.footer.setText(self.retry_message())
        elif self.task is None and offline:
            self.footer.setText("Couldn't reach GitHub. Rows show what was known before.")
        elif self.task is None:
            self.footer.setText(self.with_move_notice(self.check_outcome()))

    def check_outcome(self) -> str:
        """The footer line after a check: it names the app and the reason when checks failed."""
        names = {app.id: app.name for app in self.apps}
        failed = [
            (names.get(app_id, app_id), reason)
            for app_id, reason in self.release_errors.items()
            if app_id in self.installed and reason != UNREACHABLE
        ]
        if not failed:
            return "Update check finished."
        if len(failed) == 1:
            name, reason = failed[0]
            return f"Check failed for {name}: {shortened(reason, 120)}"
        listed = ", ".join(f"{name} ({shortened(reason, 40)})" for name, reason in failed[:2])
        more = f", and {len(failed) - 2} more" if len(failed) > 2 else ""
        return f"{len(failed)} checks failed: {listed}{more}. Rows keep what was known before."

    def offline_apps(self) -> list[str]:
        errors = self.release_errors
        return [
            app_id
            for app_id in self.installed
            if errors.get(app_id) == UNREACHABLE and not self.checks.is_checking(app_id)
        ]

    def retry_when_offline(self) -> None:
        """Try again shortly when only the network was missing, a few times with longer waits."""
        if self.offline_timer.isActive() or self.quitting or self.close_after_release:
            return
        if not self.self_retry and not self.offline_apps():
            if not self.checks.busy and self.self_task is None:
                self.offline_attempts = 0
            return
        if self.offline_attempts < len(OFFLINE_RETRY_SECONDS):
            self.offline_timer.start(OFFLINE_RETRY_SECONDS[self.offline_attempts] * 1000)
            self.offline_attempts += 1

    def offline_retry(self) -> None:
        if self.quitting or self.close_after_release:
            return
        if self.self_retry:
            self.self_retry = False
            self.start_self_check()
        offline = self.offline_apps()
        if offline:
            self.checks.add(offline)
        else:
            self.notify_when_idle()

    def install_app(self, app_id: str) -> None:
        app = next(app for app in self.apps if app.id == app_id)
        if app.manual:
            return
        installed = self.installed.get(app_id)

        def install(app: App, cancel: Event, progress: Progress) -> TaskResult:
            progress(f"Checking {app.name} release…", 0, 0)
            release = latest_release(app, cancel, self.library.data_dir / "release-cache")
            if installed and not release.newer_than(installed.version):
                return TaskResult(
                    f"{app.name} is already up to date.",
                    {app_id: release},
                    title=f"{app.name} is up to date",
                )
            found: list[str] = []
            self.library.install(
                app,
                release,
                cancel,
                lambda message, done, total: progress(f"{app.name} · {message}", done, total),
                found,
            )
            return TaskResult(
                f"{app.name} {version_text(release.version)} is ready to launch.",
                {app_id: release},
                warnings={app_id: found[0]} if found else {},
                title=f"{app.name} {'updated' if installed else 'installed'}",
            )

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            return self.library.following_moves(app, lambda app: install(app, cancel, progress))

        self.start_task(
            f"Preparing {app.name}…",
            operation,
            working={app_id: "Updating" if installed else "Installing"},
            failure=f"Couldn't {'update' if installed else 'install'} {app.name}",
        )

    def update_all(self) -> None:
        # In the order the list shows, so rows leave it from the top.
        apps = [
            app
            for app in self.sorted_apps(self.apps)
            if self.has_update(app.id)
            and app.id in self.releases
            and self.installed[app.id].managed
        ]

        if not apps or self.busy:
            return
        versions = {app.id: self.installed[app.id].version for app in apps}
        # Each app is reported as it finishes, so its row changes without waiting for the rest.
        steps = self.update_steps.step

        def update(app: App, cancel: Event, progress: Progress, found: list[str]) -> Release:
            release = latest_release(app, cancel, self.library.data_dir / "release-cache")
            if release.newer_than(versions[app.id]):
                self.library.install(
                    app,
                    release,
                    cancel,
                    lambda message, done, total: progress(f"{app.name} · {message}", done, total),
                    found,
                )
            return release

        names = {app.id: app.name for app in self.apps}
        choose_first = [app.id for app in self.apps if self.has_update(app.id) and app not in apps]

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            result = TaskResult("")
            updated: list[str] = []
            skipped: list[str] = []
            cancelled = False
            stopped = ""
            for app in apps:
                if cancel.is_set():
                    cancelled = True
                    break
                progress(f"Checking {app.name}…", 0, 0)
                part = TaskResult("")
                found: list[str] = []
                try:
                    release = self.library.following_moves(
                        app, lambda app: update(app, cancel, progress, found)
                    )
                    part.releases[app.id] = release
                    if release.newer_than(versions[app.id]):
                        updated.append(app.id)
                except Cancelled:
                    cancelled = True
                    steps.emit(app.id, part)
                    break
                except BuildChoiceRequired as error:
                    result.build_choices[app.id] = error
                except AppRunning:
                    skipped.append(app.id)
                except (RateLimited, Unreachable) as error:
                    # The next apps would fail the same way: stop, and say so once.
                    stopped = str(error)
                    steps.emit(app.id, part)
                    break
                except Exception as error:
                    part.errors[app.id] = describe_error(error)
                result.releases.update(part.releases)
                result.errors.update(part.errors)
                if found:
                    result.warnings[app.id] = found[0]
                steps.emit(app.id, part)
            result.skipped_running = skipped
            result.stopped = stopped
            result.updated = len(updated)
            result.choice_apps = list(dict.fromkeys([*choose_first, *result.build_choices]))
            result.message = update_all_message(
                len(updated),
                len(result.errors),
                stopped,
                [names[app_id] for app_id in skipped],
                [names[app_id] for app_id in result.choice_apps],
                cancelled,
            )
            count = len(updated)
            plural = "" if count == 1 else "s"
            result.title = f"Updated {count} app{plural}" if count else "Update all finished"
            return result

        working = {app.id: WAITING for app in apps}
        working[apps[0].id] = "Updating"
        self.start_task(
            "Updating installed apps…",
            operation,
            working=working,
            failure="Some apps couldn't be updated",
        )

    def replace_file(self, app_id: str) -> None:
        """Ask for a newer AppImage and make it the app's file; the old one stays for Roll back."""
        app = next((app for app in self.apps if app.id == app_id), None)
        if app is None or not app.manual or self.busy:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Choose a newer file for {app.name}",
            "",
            "AppImage files (*.AppImage *.appimage);;All files (*)",
        )
        if not path:
            return
        source = Path(path)

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            progress(f"Reading {source.name}…", 0, 0)
            details = describe_appimage(source)
            check_cancel(cancel)
            progress(f"Copying {source.name}…", 0, 0)
            found: list[str] = []
            self.library.replace_file(
                app,
                source,
                details.version,
                cancel,
                identify=bool(details.note),
                progress=progress,
                warnings=found,
            )
            return TaskResult(
                f"Replaced {app.name} with {source.name}. The old file is kept.",
                warnings={app.id: found[0]} if found else {},
            )

        self.start_task(
            f"Replacing {app.name}…",
            operation,
            working={app_id: "Replacing"},
            failure=f"Couldn't replace {app.name}",
        )

    def rollback_app(self, app_id: str) -> None:
        app = next(app for app in self.apps if app.id == app_id)
        if self.busy:
            return
        if self.app_running(app_id):
            self.offer_force_close(
                app_id, f"Close {app.name} before rolling it back, or force close it."
            )
            return
        previous = previous_copy(self.installed.get(app_id))
        target = version_text(previous.version) if previous else "the previous version"
        can_hold = not app.manual and not app.hold_updates
        accepted, hold = MessageBox.confirm(
            self,
            f"Roll back {app.name}?",
            f"Go back to {target} of {app.name}? The current version is kept, so you can "
            "switch back to it.",
            "Roll back",
            f"Hold at {target} so the newer version isn't offered again" if can_hold else None,
            checked=True,
        )
        if not accepted:
            return
        hold = hold and can_hold

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            check_cancel(cancel)
            self.library.rollback(app)
            message = f"Restored the previous {app.name} version."
            if hold:
                try:
                    self.library.set_update_hold(app.id, True)
                    message += f" It is held at {target}, so the newer version isn't offered."
                except (OSError, ValueError) as error:
                    message += f" It could not be held: {describe_error(error)}"
            return TaskResult(message, title=f"{app.name} rolled back")

        self.start_task(
            f"Restoring {app.name}…",
            operation,
            failure=f"Couldn't roll back {app.name}",
            cancellable=False,
        )

    def remove_app(self, app_id: str) -> None:
        """Uninstall the app's file. A GitHub app stays in the library unless asked otherwise."""
        app = next(app for app in self.apps if app.id == app_id)
        if self.busy:
            return
        if self.app_running(app_id):
            self.offer_force_close(
                app_id, f"Close {app.name} before uninstalling it, or force close it."
            )
            return
        if app.manual:
            accepted, _ = MessageBox.confirm(
                self,
                f"Uninstall {app.name}?",
                f"{app.name} was added from a file, so it also leaves your library. "
                "The file you added is not touched. Your documents and the app's settings "
                "are kept.",
                "Uninstall",
            )
            also = True
        else:
            accepted, also = MessageBox.confirm(
                self,
                f"Uninstall {app.name}?",
                "This deletes the app file and the kept previous copy, and releases any hold "
                "on updates. Your documents and the app's settings are kept.",
                "Uninstall",
                "Also remove from my library",
            )
        if not accepted:
            return

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            check_cancel(cancel)
            if app.manual:
                self.library.remove_file_app(app)  # nothing is left to install, so it goes too
            else:
                self.library.remove(app)
                if also:
                    self.library.remove_repository(app.id)
            if also:
                message = f"Uninstalled {app.name} and removed it from your library."
            else:
                message = (
                    f"Uninstalled {app.name}. It stays in your library, "
                    "so you can install it again."
                )
            return TaskResult(message, title=f"{app.name} uninstalled")

        self.start_task(
            f"Uninstalling {app.name}…",
            operation,
            failure=f"Couldn't uninstall {app.name}",
            cancellable=False,
        )

    def forget_missing(self, app_id: str) -> None:
        app = next(app for app in self.apps if app.id == app_id)
        missing = self.missing.get(app_id)
        if self.busy or missing is None:
            return
        if app.manual:
            accepted, _ = MessageBox.confirm(
                self,
                f"Forget {app.name}?",
                f"The file AppGlider kept at\n{missing.path}\nis gone. {app.name} is removed "
                "from your library, and its kept previous copy in AppGlider's apps folder is "
                "deleted. The file you added is not touched.",
                "Forget",
            )
            also = True
        else:
            accepted, also = MessageBox.confirm(
                self,
                f"Forget the missing {app.name} copy?",
                f"AppGlider will stop tracking the AppImage it installed at\n{missing.path}\n"
                "No files are deleted. You can install the app again afterwards.",
                "Forget",
                "Also remove from my library",
            )
        if not accepted:
            return
        try:
            self.library.forget_missing(app_id)
            if also:
                self.library.remove_repository(app_id)
        except (OSError, ValueError) as error:
            MessageBox.warning(self, f"Couldn't forget {app.name}", describe_error(error))
            self.scan_installed()
            return
        self.scan_installed()
        if app.manual:
            self.footer.setText(f"Forgot {app.name} and removed it from your library.")
        elif also:
            self.footer.setText(
                f"Forgot the missing {app.name} copy and removed it from your library. "
                "No files were deleted."
            )
        else:
            self.footer.setText(f"Forgot the missing {app.name} copy. No files were deleted.")

    def remove_from_library(self, app_id: str) -> None:
        """Remove a repository whose app is neither installed nor missing, after asking."""
        app = next((app for app in self.apps if app.id == app_id), None)
        if app is None or self.busy or app_id in self.installed or app_id in self.missing:
            return
        accepted, _ = MessageBox.confirm(
            self,
            f"Remove {app.name} from the library?",
            f"{app.name} will no longer be listed. No application files are deleted.",
            "Remove",
        )
        if not accepted:
            return
        try:
            self.library.remove_repository(app_id)
        except (OSError, ValueError) as error:
            MessageBox.warning(self, f"Couldn't remove {app.name}", describe_error(error))
            return
        self.catalog_changed()
        self.footer.setText(
            f"Removed {app.name} from the library. No application files were deleted."
        )

    def first_hide_hint(self) -> None:
        """The first time closing only hides the window, say where AppGlider went."""
        if self.settings.value(FIRST_HIDE_KEY, False, type=bool):
            return
        self.settings.setValue(FIRST_HIDE_KEY, True)
        self.tray.showMessage(
            "AppGlider is still running",
            "Use the tray icon to open it again, or choose Quit AppGlider there to exit.",
            self.tray_icons[0],
            15000,
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        self.settings.setValue("window/geometry", self.saveGeometry())
        if (
            not self.quitting
            and self.settings.value("General/close-to-tray", True, type=bool)
            and self.tray.isVisible()
            and QSystemTrayIcon.isSystemTrayAvailable()
        ):
            self.hide()
            self.first_hide_hint()
            event.ignore()
            return
        if self.task is not None:
            accepted, _ = MessageBox.confirm(
                self,
                f"Stop {self.task_name} and quit?",
                "AppGlider will cancel it, wait for it to stop, then quit. A version you "
                "already have installed is kept.",
                "Stop and quit",
            )
            if accepted:
                self.quitting = True
                self.close_after_task = True
                self.cancel_task()
            event.ignore()
            return
        if self.checks.busy:
            self.quitting = True
            self.close_after_release = True
            self.checks.stop()
            self.add_page.stop()
            self.tray.hide()
            self.hide()
            event.ignore()
            return
        if self.self_task is not None:
            self.self_task.cancel.set()
        if self.shortcut_task is not None:
            self.shortcut_task.cancel.set()
        if self.names_task is not None:
            self.names_task.cancel.set()
        self.add_page.stop()
        self.tray.hide()
        super().closeEvent(event)

    def system_theme_changed(self, scheme: object = None) -> None:
        self.apply_theme()

    def apply_theme(self) -> None:
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        theme, accent_name = appearance_choice(self.settings)
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
        foreground, muted, faint = tokens["text"], tokens["muted"], tokens["faint"]
        DragHandle.colours = (faint, muted)
        for row in self.cards.values():
            row.handle.update()
        app.setPalette(palette(tokens, accent, dark))
        self.check_button.setIcon(refresh_icon(foreground))
        for button, kind in (
            (self.nav_library, "library"),
            (self.nav_updates, "updates"),
            (self.nav_add, "add"),
            (self.nav_settings, "settings"),
        ):
            button.setIcon(nav_icon(kind, muted, foreground, faint))
        app.setStyleSheet(stylesheet(tokens, accent, dark))
