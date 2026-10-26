"""Desktop entry point."""

import json
import os
import ssl
import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QComboBox, QSystemTrayIcon

from appglider import APP_NAME, APP_TASKBAR_ICON, APP_VERSION
from appglider.catalogfile import EXAMPLE_CATALOG
from appglider.catalogfile import read as read_catalog
from appglider.instance import SingleInstance
from appglider.self_update import running_image
from appglider.window import LauncherWindow


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
    if not smoke and not instance.acquire(wake=not quiet):
        return 0
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
    status = app.exec()
    instance.release()
    return status
