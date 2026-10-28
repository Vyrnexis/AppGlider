"""Desktop entry point."""

import json
import os
import ssl
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QComboBox, QSystemTrayIcon

from appglider import APP_NAME, APP_TASKBAR_ICON, APP_VERSION
from appglider.core.catalogfile import EXAMPLE_CATALOG
from appglider.core.catalogfile import read as read_catalog
from appglider.install.self_update import AFTER_UPDATE_FLAG, cleanup, running_image
from appglider.instance import SingleInstance
from appglider.ui.window import LauncherWindow


def launcher_command() -> list[str]:
    image = running_image()
    if image is not None:
        return [str(image)]
    return [sys.executable, "-O", "-m", "appglider"]


def show_initial(window: LauncherWindow, tray: bool) -> None:
    """Show the window, or keep it in the tray for a quiet login start.

    Hidden only when a tray icon is visible and closing to the tray is on, so the
    window can always be brought back; otherwise it opens minimised.
    """
    if not tray:
        window.show()
    elif (
        QSystemTrayIcon.isSystemTrayAvailable()
        and window.tray.isVisible()
        and window.settings.value("General/close-to-tray", True, type=bool)
    ):
        return
    else:
        window.showMinimized()


def acquire_instance(instance: SingleInstance, *, quiet: bool, after_update: bool) -> bool:
    """Take the single-instance lock; after an update's restart, wait for the old one to quit."""
    if after_update:
        return instance.acquire_after_update(wake=not quiet)
    return instance.acquire(wake=not quiet)


def remove_replaced_image(smoke: bool) -> None:
    """Delete the file the last update replaced. Never part of a smoke test."""
    if not smoke:
        cleanup(QSettings())


def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName(APP_NAME)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName("appglider")
    app.setWindowIcon(QIcon(str(APP_TASKBAR_ICON)))
    app.setStyle("Fusion")
    smoke = "--smoke-test" in sys.argv
    quiet = "--tray" in sys.argv and not smoke
    instance = SingleInstance(
        Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation))
        / "appglider"
    )
    after_update = AFTER_UPDATE_FLAG in sys.argv
    if not smoke and not acquire_instance(instance, quiet=quiet, after_update=after_update):
        return 0
    remove_replaced_image(smoke)
    if after_update:
        # The version that restarted us may still be closing; its file goes once it has.
        QTimer.singleShot(10_000, lambda: remove_replaced_image(smoke))
    window = LauncherWindow()
    instance.activated.connect(window.show_launcher)
    command = launcher_command()
    try:
        window.library.refresh_launcher_shortcut(command, packaged=len(command) == 1)
    except (OSError, ValueError) as error:
        window.footer.setText(f"Could not create the AppGlider menu shortcut: {error}")
    try:
        window.library.refresh_autostart(command, packaged=len(command) == 1)
    except (OSError, ValueError) as error:
        window.footer.setText(f"Could not update the AppGlider login entry: {error}")
    show_initial(window, quiet)
    if smoke:

        def report() -> None:
            print(
                json.dumps(
                    {
                        "app": APP_NAME,
                        "version": app.applicationVersion(),
                        "header_version": window.version_button.text(),
                        "refresh_action": window.check_button.accessibleName(),
                        "release_preferences": sum(
                            card.name_button.isEnabled() for card in window.cards.values()
                        ),
                        "card_dropdowns": sum(
                            len(card.findChildren(QComboBox)) for card in window.cards.values()
                        ),
                        "python": sys.version.split()[0],
                        "prefix": sys.prefix,
                        "appdir": os.environ.get("APPDIR"),
                        "installed": len(window.installed),
                        "catalog": len(window.apps),
                        "welcome": window.welcome.isVisible(),
                        "example_catalog": len(read_catalog(EXAMPLE_CATALOG).entries),
                        "icon": not window.windowIcon().isNull(),
                        "update_icon": not window.tray_icons[1].pixmap(32, 32).isNull(),
                        "update_checks_hours": window.update_timer.interval() // 3_600_000,
                        "https_certificates": ssl.create_default_context().cert_store_stats()[
                            "x509_ca"
                        ],
                        "command": command,
                        "data": str(window.library.data_dir),
                    }
                ),
                flush=True,
            )
            app.quit()

        QTimer.singleShot(200, report)
    else:
        QTimer.singleShot(0, window.startup_check)
        QTimer.singleShot(0, window.refresh_shortcuts)
    status = app.exec()
    instance.release()
    return status
