"""The system tray icon, its menu and update notifications."""

import json
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMainWindow, QMenu, QSystemTrayIcon

from appglider import APP_ICON, APP_UPDATE_ICON
from appglider.core.catalog import App
from appglider.core.preferences import notified_versions
from appglider.github.assets import Release
from appglider.github.http import file_stamp
from appglider.github.icons import icon_pixmap
from appglider.install import self_update
from appglider.ui.release_checks import ReleaseChecks

if TYPE_CHECKING:
    from collections.abc import Iterable

    from PySide6.QtCore import QSettings

    from appglider.install.library import Installed, Library


class TrayMixin(QMainWindow):
    """Tray behaviour of `LauncherWindow`, which supplies the members declared below."""

    if TYPE_CHECKING:
        settings: QSettings
        library: Library
        apps: tuple[App, ...]
        installed: dict[str, Installed]
        releases: dict[str, Release]
        self_state: self_update.SelfUpdate
        checks: ReleaseChecks
        tray_key: list[tuple[str, str, Path, tuple[int, int, int] | None]] | None

        def show_launcher(self) -> None: ...

        def quit_launcher(self) -> None: ...

        def check_for_updates(self) -> None: ...

        def scan_installed(self) -> None: ...

        def launch_app(self, app_id: str) -> None: ...

        def has_update(self, app_id: str) -> bool: ...

        def sorted_apps(self, apps: Iterable[App]) -> list[App]: ...

    def build_tray(self) -> None:
        """System tray icon and its menu."""
        self.tray_icons = (QIcon(str(APP_ICON)), QIcon(str(APP_UPDATE_ICON)))
        self.tray = QSystemTrayIcon(self.tray_icons[0], self)
        self.tray_has_updates = False
        self.tray_menu = QMenu(self)
        self.tray_menu.addAction("Open AppGlider").triggered.connect(self.show_launcher)
        self.tray_check_action = self.tray_menu.addAction("Check for updates")
        self.tray_check_action.triggered.connect(self.check_for_updates)
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

    def update_tray(self, update_count: int, own: str) -> None:
        """Show the waiting updates in the tray tooltip, icon and installed-apps menu."""
        self.tray.setToolTip(
            f"AppGlider · Updates: {update_count}"
            + (f" · AppGlider {own} available" if own else "")
        )
        if (update_count > 0 or bool(own)) != self.tray_has_updates:
            self.tray_has_updates = update_count > 0 or bool(own)
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

    def tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_launcher()

    def notify_updates(self) -> None:
        """Tell the user, once per new version, that updates are waiting (opt-in)."""
        waiting = {
            app.id: release.version
            for app in self.apps
            if app.id in self.installed
            and self.has_update(app.id)
            and (release := self.releases.get(app.id)) is not None
        }
        names = [
            f"{app.name} {waiting[app.id]}"
            for app in self.apps
            if waiting.get(app.id) not in (None, self.notified.get(app.id))
        ]
        own = self.self_state.version
        if own and own != self.notified.get(self_update.NOTIFY_KEY):
            names.insert(0, f"AppGlider {own}")
        if own:
            waiting[self_update.NOTIFY_KEY] = own
        elif not self.self_state.checked_at and self_update.NOTIFY_KEY in self.notified:
            # Not checked this session: keep what was announced rather than announce it twice.
            waiting[self_update.NOTIFY_KEY] = self.notified[self_update.NOTIFY_KEY]
        self.notified = dict(waiting)
        self.settings.setValue("notifications/seen", json.dumps(self.notified))
        if (
            not names
            or not self.settings.value("notifications/updates", False, type=bool)
            or not QSystemTrayIcon.isSystemTrayAvailable()
            or not self.tray.isVisible()
            or (self.isVisible() and self.isActiveWindow())
        ):
            return
        if len(names) == 1:
            text = f"{names[0]} is available."
        else:
            more = f", and {len(names) - 3} more" if len(names) > 3 else ""
            text = f"{len(names)} updates are waiting: {', '.join(names[:3])}{more}."
        self.tray.showMessage("AppGlider: updates available", text, self.tray_icons[1], 15000)
