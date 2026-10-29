"""Installed applications and background installation controls."""

import html
import json
import os
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import datetime
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
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
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
from appglider.core.preferences import (
    ACCENTS,
    DEFAULT_ACCENT,
    MAX_PINNED,
    appearance_choice,
    check_interval_hours,
    custom_order,
    pinned_apps,
)
from appglider.github import status as github_status
from appglider.github.assets import BuildChoiceRequired, Release
from appglider.github.download import Progress, cached_release, latest_release, release_cache_path
from appglider.github.http import file_stamp, github_request_status, restore_rate_limits
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
from appglider.install.appimage import check_appimage, clean_environment
from appglider.install.library import Installed, Library, Orphan
from appglider.ui.jobs import Lookup, Task, TaskResult
from appglider.ui.pages.add_page import AddPage
from appglider.ui.pages.app_page import AppPage, link_button
from appglider.ui.pages.settings_page import SettingsPage
from appglider.ui.release_checks import ReleaseChecks
from appglider.ui.rows import AppList, AppRow, DragHandle, StatusMessage
from appglider.ui.theme import NAV_ICON_SIZE, THEMES, nav_icon, palette, refresh_icon, stylesheet
from appglider.ui.tray import TrayMixin

SIDEBAR_WIDTH = 210
LEGACY_SECTIONS = {0: "General", 1: "Catalogs", 2: "Storage", 3: "GitHub", 4: "About"}
CHECK_TOOLTIP = "Check for updates"


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
        self.close_after_task = False
        self.quitting = False
        self.self_state = self_update.SelfUpdate()
        self.self_task: Lookup | None = None
        self.shortcut_task: Lookup | None = None
        self.menu_task: Lookup | None = None
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
        self.status_block.clicked.connect(lambda: self.open_settings("GitHub"))
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
        self.add_page = AddPage(self)
        self.add_page.added.connect(self.app_added)
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
        self.options_button = QToolButton()
        self.options_button.setObjectName("libraryOptions")
        self.options_button.setText("⋯")
        self.options_button.setAccessibleName("Library options")
        self.options_button.setToolTip("Library options")
        self.options_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        options_menu = QMenu(self.options_button)
        self.sort_action = options_menu.addAction("Sort A–Z")
        self.sort_action.triggered.connect(self.sort_alphabetically)
        self.options_button.setMenu(options_menu)
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
        self.update_all_button.clicked.connect(self.update_all)
        self.update_all_button.setObjectName("primary")
        banner_layout.addWidget(self.update_all_button)
        self.review_button = QPushButton("Review")
        self.review_button.setToolTip("Show only the apps that have updates")
        self.review_button.clicked.connect(lambda: self.show_view("updates"))
        banner_layout.addWidget(self.review_button)
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
        self.empty = QLabel()
        self.empty.setObjectName("emptyState")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty, stretch=1)

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
        self.welcome_add_button.clicked.connect(self.open_add_app)
        welcome_buttons.addWidget(self.welcome_add_button)
        self.welcome_import_button = QPushButton("Import catalog…")
        self.welcome_import_button.clicked.connect(self.settings_page.import_catalog)
        welcome_buttons.addWidget(self.welcome_import_button)
        self.welcome_example_button = QPushButton("Example catalog")
        self.welcome_example_button.setToolTip(
            "Add the ArtCraft apps and SolidCraft, a ready-made example list."
        )
        self.welcome_example_button.clicked.connect(self.settings_page.import_example_catalog)
        welcome_buttons.addWidget(self.welcome_example_button)
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
        body_layout.addWidget(self.status_line)
        self.update_status_line()

    def setup_keyboard(self) -> None:
        """Shortcuts, focus behaviour and the order Tab walks through the controls."""
        for sequence, handler in (("Ctrl+F", self.focus_search), ("Esc", self.go_back)):
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
        """Escape leaves the App or Add page for the list."""
        if self.pages.currentWidget() in (self.app_page, self.add_page):
            self.show_view(self.view)

    def update_status_line(self) -> None:
        """Show the bottom row only while it has a message, progress, Cancel or orphans."""
        self.footer.setVisible(bool(self.footer.text()))
        self.status_line.setVisible(
            any(
                not widget.isHidden()
                for widget in (
                    self.footer,
                    self.progress_bar,
                    self.cancel_button,
                    self.orphans_button,
                )
            )
        )

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
        self.automatic_self_check(notify=True)
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

    def update_github_status(self) -> None:
        label, details, limited = github_request_status()
        allowance = github_status.allowance()
        self.github_label.setText(
            "GitHub: limited"
            if limited
            else f"GitHub {allowance[0]} of {allowance[1]} left"
            if allowance
            else label
        )
        self.github_meter.setVisible(allowance is not None)
        if allowance:
            self.github_meter.setRange(0, max(1, allowance[1]))
            self.github_meter.setValue(allowance[0])
        self.status_label.setText(self.check_summary())
        if self.settings_page.isVisible():
            self.settings_page.update_github_status()
        self.status_block.setToolTip(details + "\nClick for details in Settings → GitHub.")
        if self.status_block.property("limited") != limited:
            self.status_block.setProperty("limited", limited)
            for widget in (self.status_block, self.github_label, self.github_meter):
                widget.style().unpolish(widget)
                widget.style().polish(widget)

    def check_summary(self) -> str:
        """Describe the last release check and, when scheduled, the next one."""
        last = max((info.checked_at for info in self.release_infos.values()), default=0)
        scheduled = self.update_timer.isActive()
        if not last:
            return "Not checked yet" if scheduled else "Automatic checks off"
        text = f"Checked {datetime.fromtimestamp(last):%H:%M}"
        if scheduled:
            upcoming = datetime.now().timestamp() + self.update_timer.remainingTime() / 1000
            text += f" · next {datetime.fromtimestamp(upcoming):%H:%M}"
        return text

    def create_row(self, app: App) -> AppRow:
        card = AppRow(app, self.library.icon_path(app))
        card.setParent(self.app_list)
        card.details_requested.connect(self.open_app)
        card.launch_requested.connect(self.launch_app)
        card.install_requested.connect(self.install_app)
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
        answer = QMessageBox.question(
            self,
            "Sort the library A–Z?",
            "This replaces your own order. Pinned apps stay on top.",
        )
        if answer != QMessageBox.StandardButton.Yes:
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
            card.set_state(
                self.installed.get(app_id),
                self.releases.get(app_id),
                self.task is not None,
                self.check_errors.get(app_id, ""),
                self.release_infos.get(app_id),
                self.checks.is_checking(app_id),
                self.release_errors.get(app_id, ""),
                self.working.get(app_id, ""),
                self.missing.get(app_id),
            )
            card.setVisible(app_id in ids)
            card.draggable = False
            for action in (card.move_up_action, card.move_down_action):
                action.setVisible(False)
                action.setEnabled(False)
        shown = [self.cards[app.id] for app in visible]
        reorder = self.reorder_enabled()
        self.app_list.reorderable = reorder
        for index, row in enumerate(shown):
            row.draggable = reorder and len(shown) > 1
            row.move_up_action.setVisible(row.draggable)
            row.move_down_action.setVisible(row.draggable)
            row.move_up_action.setEnabled(
                reorder and index > 0 and shown[index - 1].pinned == row.pinned
            )
            row.move_down_action.setEnabled(
                reorder and index + 1 < len(shown) and shown[index + 1].pinned == row.pinned
            )
        self.nav_add.setEnabled(self.task is None)
        self.settings_page.update_state()
        self.add_page.update_state()
        self.search.setPlaceholderText(
            f"Search {update_count if updates_view else len(self.apps)} apps"
        )
        self.check_button.setToolTip("Checking for updates…" if self.checks.busy else CHECK_TOOLTIP)
        self.check_button.setEnabled(not self.checks.busy and bool(self.installed))
        self.options_button.setEnabled(len(self.apps) > 1)
        self.update_all_button.setVisible(
            any(
                item.managed and app_id in self.releases and self.has_update(app_id)
                for app_id, item in self.installed.items()
            )
        )
        self.update_all_button.setEnabled(self.task is None)
        self.review_button.setVisible(not updates_view)
        self.show_self_banner()
        self.banner.setVisible(update_count > 0)
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
            self.update_all_button,
            self.review_button,
        ]
        for row in shown:
            order += [row.name_button, row.action_button, row.more_button]
        order += [self.cancel_button, self.orphans_button]
        for before, after in zip(order, order[1:], strict=False):
            QWidget.setTabOrder(before, after)
        self.update_status_line()
        self.scroll_area.setVisible(bool(visible))
        self.empty.setVisible(not visible and bool(self.apps))
        self.welcome.setVisible(not self.apps)
        self.welcome_add_button.setEnabled(self.task is None)
        self.welcome_import_button.setEnabled(self.task is None)
        self.welcome_example_button.setEnabled(self.task is None)
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
                "No known updates.\nUse Check for updates to look for new releases."
                if self.installed
                else "No installed apps to check.\nInstall an app from Library to get started."
            )
        elif self.installed_only:
            self.empty.setText("No apps are installed.\nChoose Install from All to get started.")
        else:
            self.empty.setText("No apps match your search.\nTry another name or clear the search.")

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
        self.filters.setVisible(view == "library")
        self.refresh()

    def banner_text(self) -> str:
        """Summarise the waiting updates, shortened the way the tray notification is."""
        names = []
        for app in self.apps:
            if self.has_update(app.id):
                release = self.releases.get(app.id)
                version = (
                    f" {'v' if release.version[:1].isdigit() else ''}{release.version}"
                    if release
                    else ""
                )
                names.append(html.escape(f"{app.name}{version}"))
        count = len(names)
        lead = f"<b>{count} update{'s' if count != 1 else ''} ready.</b>"
        if count > 3:
            tail = f"{', '.join(names[:3])}, and {count - 3} more"
        elif count > 1:
            tail = f"{', '.join(names[:-1])} and {names[-1]}"
        else:
            tail = names[0]
        return f"{lead} {tail}."

    def open_app(self, app_id: str) -> None:
        """Show the App page; the sidebar keeps the view it was opened from."""
        if not any(app.id == app_id for app in self.apps):
            return
        if self.pages.currentWidget() is not self.app_page:
            self.list_scroll = self.scroll_area.verticalScrollBar().value()
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
        self.app_page.app_id = None
        self.show_page(self.list_page)
        QTimer.singleShot(
            0, self, lambda: self.scroll_area.verticalScrollBar().setValue(self.list_scroll)
        )
        self.scroll_area.verticalScrollBar().setValue(self.list_scroll)

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
        if self.task is not None:
            return
        self.add_page.start()
        self.show_page(self.add_page)
        self.add_page.url.setFocus()
        self.nav_add.setChecked(True)

    def app_added(self, app_id: str, warning: str) -> None:
        self.catalog_changed()
        app = next((app for app in self.apps if app.id == app_id), None)
        self.open_app(app_id)
        if app is not None:
            self.footer.setText(f"Added {app.name}. Install it from its page.{warning}")

    def choose_build(self, app_id: str) -> None:
        """Open the App page and ask which build of the app to use."""
        self.open_app(app_id)
        self.app_page.choose_build()

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
                        self.cards[app.id] = self.create_row(app)
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
            self.missing = missing
            self.migrate_legacy_sort()
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
        page: AppPage | None = None,
        working: dict[str, str] | None = None,
    ) -> None:
        if self.task is not None:
            return
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
        self.cancel_button.show()
        self.refresh()
        QThreadPool.globalInstance().start(self.task)

    def check_releases(self, app_id: str, page: AppPage | None = None) -> None:
        """A full release check of one app, even an uninstalled one (never downloads the app)."""
        app = next((app for app in self.apps if app.id == app_id), None)
        if app is None or self.task is not None:
            return

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            info = self.library.following_moves(
                app,
                lambda app: discover_releases(
                    app, cancel, self.library.data_dir / "release-cache", force=True
                ),
            )
            return TaskResult("Release checked.", release_infos={app.id: info})

        self.start_task(f"Checking releases for {app.name}…", operation, page=page)

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
            self.footer.setText(f"Created menu shortcut: {self.menu_app.name} (AppGlider).")

    def shortcut_failed(self, error: str) -> None:
        self.menu_task = None
        if not self.quitting:
            QMessageBox.warning(self, "Cannot create shortcut", error)

    def refresh_desktop_database(self) -> None:
        """Let the desktop notice changed menu entries; the tool can take seconds."""
        library = self.library
        QThreadPool.globalInstance().start(Lookup(lambda cancel: library.update_desktop_database()))

    def task_progress(self, message: str, received: int, total: int) -> None:
        self.footer.setText(message)
        self.progress_bar.setRange(0, 100 if total > 0 else 0)
        if total > 0:
            self.progress_bar.setValue(min(100, int(received * 100 / total)))

    def reset_task(self) -> None:
        self.task = None
        self.working = {}
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

    def task_finished(self, result: TaskResult) -> None:
        self.show_moves()
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
        self.footer.setText(self.with_move_notice(result.message))
        if result.downloaded is not None:
            self.self_update_downloaded(result.downloaded)
        if result.build_choices and not self.close_after_task:
            self.choose_build(next(iter(result.build_choices)))
        if result.errors and not self.close_after_task:
            errors = "\n\n".join(f"{key}: {value}" for key, value in result.errors.items())
            QMessageBox.warning(self, "Some operations could not finish", errors)
        if self.close_after_task:
            self.close()

    def task_failed(self, error: str) -> None:
        self.show_moves()
        self.reset_task()
        self.footer.setText(self.with_move_notice(error))
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

    def startup_check(self) -> None:
        """The first check after the window opens; both parts follow the update schedule choice."""
        self.refresh_releases()
        self.automatic_self_check()

    def check_for_updates(self) -> None:
        """The toolbar and tray command: a full check of the apps and of AppGlider itself."""
        self.start_self_check(explicit=True)
        self.refresh_releases(force=True)

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

    def self_check_done(self, tag: object) -> None:
        self.self_task = None
        if self.quitting:
            return
        self.self_explicit = False
        self.self_state = self_update.SelfUpdate(time.time(), tag if isinstance(tag, str) else "")
        self.finish_self_check()

    def self_check_failed(self, error: str) -> None:
        self.self_task = None
        if self.quitting:
            return
        explicit, self.self_explicit = self.self_explicit, False
        if explicit:
            # Background checks stay silent; only a check the user asked for reports failure.
            self.self_state = replace(self.self_state, checked_at=time.time(), failed=True)
            if self.task is None and not self.checks.busy:
                self.footer.setText("Couldn't check for a new AppGlider version.")
        self.finish_self_check()

    def finish_self_check(self) -> None:
        self.refresh()
        self.settings_page.update_self_update()
        self.notify_when_idle()

    def notify_when_idle(self) -> None:
        """Announce waiting updates once the checks a scheduled run started have all finished."""
        if (self.notify_pending or self.self_notify) and self.self_task is None:
            if not self.checks.busy:
                self.notify_pending = self.self_notify = False
                self.notify_updates()

    def show_self_banner(self) -> None:
        ready = self.self_ready
        version = ready.version if ready else self.self_state.version
        self.self_banner.setVisible(bool(version))
        if not version:
            return
        shown = html.escape(version)
        if ready:
            self.self_banner_label.setText(
                f"<b>AppGlider {shown}</b> is ready. It starts the next time you open AppGlider."
            )
            self.self_notes_button.hide()
            self.self_update_button.setText("Restart now")
            self.self_update_button.setAccessibleName(f"Restart now to use AppGlider {version}")
            self.self_update_button.setEnabled(self.task is None and not self.checks.busy)
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
        self.self_update_button.setEnabled(self.task is None or not replaceable)

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
        if self.task is not None or not version:
            return
        cache_dir = self.library.data_dir / "release-cache"

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            try:
                done = self_update.download_update(image, tag, cache_dir, cancel, progress)
            except Cancelled:
                raise
            except Exception as error:
                return TaskResult(f"Couldn't update AppGlider: {error}")
            return TaskResult(f"AppGlider {done.version} is ready.", downloaded=done)

        self.start_task(f"Downloading AppGlider {version}…", operation)

    def self_update_downloaded(self, done: self_update.Downloaded) -> None:
        """Point the menu and login entries at the new file and offer the restart."""
        self_update.record(self.settings, done)
        self.self_ready = self_update.Ready(done.new, done.version)
        problems = []
        try:
            self.library.create_launcher_shortcut([str(done.new)])
        except (OSError, ValueError) as error:
            problems.append(f"The menu shortcut was not updated: {error}")
        try:
            if self.library.autostart_enabled():
                self.library.enable_autostart([str(done.new)])
        except (OSError, ValueError) as error:
            problems.append(f"The login entry was not updated: {error}")
        self.footer.setText(" ".join([self.footer.text(), *problems]))
        self.refresh()
        self.settings_page.update_self_update()

    def restart_self(self) -> None:
        """Start the downloaded AppGlider, which waits for this one to quit, then quit."""
        ready = self.self_ready
        if ready is None or self.task is not None or self.checks.busy:
            return
        try:
            check_appimage(ready.path)
            if not os.access(ready.path, os.X_OK):
                raise ValueError("The new file is not executable.")
        except (OSError, ValueError) as error:
            self.footer.setText(f"Couldn't start AppGlider {ready.version}: {error}")
            return
        try:
            # Like app launches: a new session with no inherited descriptors. An inherited
            # handle on this image's mount would keep the old file busy, so it could not be
            # removed once the new version runs.
            subprocess.Popen(
                [str(ready.path), self_update.AFTER_UPDATE_FLAG],
                cwd=ready.path.parent,
                env=clean_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as error:
            self.footer.setText(f"Couldn't start AppGlider {ready.version}: {error}")
            return
        self.quit_launcher()

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
        return next((app for app in self.apps if app.id == app_id), None)

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
        self.refresh()
        self.notify_when_idle()
        if self.task is None:
            failed = any(app_id in self.installed for app_id in self.release_errors)
            self.footer.setText(
                self.with_move_notice(
                    "Update check finished."
                    + (
                        " Some checks failed; rows keep previously known information."
                        if failed
                        else ""
                    )
                )
            )

    def install_app(self, app_id: str) -> None:
        app = next(app for app in self.apps if app.id == app_id)
        installed = self.installed.get(app_id)

        def install(app: App, cancel: Event, progress: Progress) -> TaskResult:
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

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            return self.library.following_moves(app, lambda app: install(app, cancel, progress))

        self.start_task(
            f"Preparing {app.name}…",
            operation,
            working={app_id: "Updating" if installed else "Installing"},
        )

    def update_all(self) -> None:
        apps = [
            app
            for app in self.apps
            if self.has_update(app.id)
            and app.id in self.releases
            and self.installed[app.id].managed
        ]

        versions = {app.id: self.installed[app.id].version for app in apps}

        def update(app: App, cancel: Event, progress: Progress) -> Release:
            release = latest_release(app, cancel, self.library.data_dir / "release-cache")
            if release.newer_than(versions[app.id]):
                self.library.install(
                    app,
                    release,
                    cancel,
                    lambda message, done, total: progress(f"{app.name} · {message}", done, total),
                )
            return release

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            result = TaskResult("Updates finished.")
            updated: list[str] = []
            for app in apps:
                check_cancel(cancel)
                progress(f"Checking {app.name}…", 0, 0)
                try:
                    release = self.library.following_moves(
                        app, lambda app: update(app, cancel, progress)
                    )
                    result.releases[app.id] = release
                    if release.newer_than(versions[app.id]):
                        updated.append(app.id)
                except BuildChoiceRequired as error:
                    result.build_choices[app.id] = error
                except (OSError, ValueError) as error:
                    result.errors[app.id] = str(error)
            result.message = f"Updated {len(updated)} apps."
            return result

        if apps:
            self.start_task(
                "Updating installed apps…",
                operation,
                working={app.id: "Updating" for app in apps},
            )

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

    def forget_missing(self, app_id: str) -> None:
        app = next(app for app in self.apps if app.id == app_id)
        missing = self.missing.get(app_id)
        if self.task is not None or missing is None:
            return
        answer = QMessageBox.question(
            self,
            f"Forget the missing {app.name} copy?",
            f"AppGlider will stop tracking the AppImage it installed at\n{missing.path}\n"
            "No files are deleted. You can install the app again afterwards.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.library.forget_missing(app_id)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Cannot forget the copy", str(error))
            return
        self.scan_installed()
        self.footer.setText(f"Forgot the missing {app.name} copy. No files were deleted.")

    def remove_from_library(self, app_id: str) -> None:
        """Remove a repository whose app is not installed, after a confirmation."""
        app = next((app for app in self.apps if app.id == app_id), None)
        if app is None or self.task is not None or app_id in self.installed:
            return
        answer = QMessageBox.question(
            self,
            f"Remove {app.name} from the library?",
            f"{app.name} will no longer be listed. No application files are deleted.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.library.remove_repository(app_id)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Cannot remove the app", str(error))
            return
        self.catalog_changed()
        self.footer.setText(
            f"Removed {app.name} from the library. No application files were deleted."
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
            self.add_page.stop()
            self.tray.hide()
            self.hide()
            event.ignore()
            return
        if self.self_task is not None:
            self.self_task.cancel.set()
        if self.shortcut_task is not None:
            self.shortcut_task.cancel.set()
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
