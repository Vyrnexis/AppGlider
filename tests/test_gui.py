"""Exercise the actual Qt widgets without network or installation side effects."""

import io
import json
import os
import tempfile
import time
import unittest
from contextlib import ExitStack
from dataclasses import asdict, replace
from pathlib import Path
from threading import Event
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from catalog_fixture import APPS, STARTER_ENTRIES, stock
from gui_helpers import close_window
from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QMimeData,
    QPoint,
    QPointF,
    QRect,
    QSettings,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QCloseEvent,
    QDragMoveEvent,
    QDropEvent,
    QImage,
    QMouseEvent,
    QPalette,
    QPixmap,
)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyleOptionFrame,
    QSystemTrayIcon,
    QToolButton,
    QWidget,
)
from test_library import APP, appimage_bytes, release_for

from appglider import APP_ICON, APP_UPDATE_ICON, APP_VERSION
from appglider.core.catalog import App, custom_app
from appglider.core.preferences import (
    appearance_choice,
    check_interval_hours,
    custom_order,
    dismissed_versions,
    notified_versions,
    pinned_apps,
)
from appglider.core.storage import atomic_json
from appglider.github import http
from appglider.github import status as github_status
from appglider.github.assets import Release
from appglider.github.release_info import ReleaseInfo, ReleaseOption, info_path
from appglider.install.library import Installed, Library, previous_copy
from appglider.ui.controls import SpaceBar
from appglider.ui.jobs import TaskResult
from appglider.ui.pages.settings_page import SettingsPage, format_space
from appglider.ui.rows import ORDER_MIME
from appglider.ui.window import (
    LauncherWindow,
    OrphansDialog,
    fit_size,
)


class LibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setApplicationName("AppGlider tests")
        cls.qt.setStyle("Fusion")

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = QSettings(
            str(Path(self.temp.name) / "settings.ini"), QSettings.Format.IniFormat
        )
        self.settings.setValue("appearance/theme", "Light")
        check = patch("appglider.install.self_update.check", return_value=None)  # no network
        check.start()
        self.addCleanup(check.stop)
        library = stock(
            Library(
                Path(self.temp.name) / "data",
                Path(self.temp.name) / "apps",
                desktop_dir=Path(self.temp.name) / "desktop",
                autostart_dir=Path(self.temp.name) / "autostart",
            )
        )
        self.window = LauncherWindow(self.settings, library)
        self.window.show()
        self.qt.processEvents()
        self.addCleanup(close_window, self.window)

    def visible_ids(self) -> set[str]:
        return {app_id for app_id, card in self.window.cards.items() if card.isVisible()}

    def test_empty_library_shows_a_welcome_with_add_and_import(self) -> None:
        root = Path(self.temp.name) / "empty"
        library = Library(root / "data", root / "apps", desktop_dir=root / "desktop")
        window = LauncherWindow(self.settings, library)
        self.addCleanup(close_window, window)
        window.show()
        window.scan_installed()
        self.qt.processEvents()
        self.assertEqual(window.apps, ())
        self.assertTrue(window.welcome.isVisibleTo(window))
        self.assertTrue(window.scroll_area.isHidden())
        self.assertTrue(window.empty.isHidden())
        self.assertEqual(window.library_count.text(), "0")

        window.welcome_add_button.click()
        self.assertIs(window.pages.currentWidget(), window.add_page)
        self.assertTrue(window.add_page.adding)

        with patch.object(SettingsPage, "import_catalog", autospec=True) as importer:
            window.welcome_import_button.click()
        importer.assert_called_once()
        self.assertIs(window.pages.currentWidget(), window.add_page)

        with patch.object(SettingsPage, "import_example_catalog", autospec=True) as example:
            window.welcome_example_button.click()
        example.assert_called_once()

        library.import_catalog(STARTER_ENTRIES[:2])
        window.scan_installed()
        self.qt.processEvents()
        self.assertTrue(window.welcome.isHidden())
        self.assertFalse(window.scroll_area.isHidden())
        self.assertEqual(window.library_count.text(), "2")
        self.assertEqual(window.search.placeholderText(), "Search 2 apps")

    def test_appglider_identity_and_icon(self) -> None:
        self.assertEqual(self.window.windowTitle(), "AppGlider")
        self.assertFalse(self.window.windowIcon().isNull())
        self.assertFalse(self.window.tray.icon().isNull())
        self.assertEqual(self.window.page_title.text(), "Library")
        image = QImage(str(APP_ICON))
        self.assertTrue(image.hasAlphaChannel())
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        for size in (16, 24, 32, 64):
            self.assertFalse(self.window.windowIcon().pixmap(size, size).isNull())

    def test_entry_point_uses_appglider_identity(self) -> None:
        from appglider.app import main

        with (
            patch("appglider.app.QApplication") as application,
            patch("appglider.app.LauncherWindow") as window,
            patch("appglider.app.QTimer.singleShot") as timer,
            patch("appglider.app.SingleInstance") as instance,
        ):
            instance.return_value.acquire.return_value = True
            application.return_value.exec.return_value = 0
            self.assertEqual(main(), 0)
            application.return_value.setOrganizationName.assert_called_once_with("AppGlider")
            application.return_value.setApplicationName.assert_called_once_with("AppGlider")
            application.return_value.setApplicationDisplayName.assert_called_once_with("AppGlider")
            application.return_value.setDesktopFileName.assert_called_once_with("appglider")
            window.return_value.library.refresh_launcher_shortcut.assert_called_once()
            self.assertEqual(
                [call.args for call in timer.call_args_list],
                [
                    (0, window.return_value.startup_check),
                    (0, window.return_value.refresh_shortcuts),
                ],
            )

    def test_close_hides_to_tray_and_activation_restores_window(self) -> None:
        with patch("appglider.ui.window.QSystemTrayIcon.isSystemTrayAvailable", return_value=True):
            self.window.tray.show()
            self.assertFalse(self.window.close())
            self.assertFalse(self.window.isVisible())
            self.assertTrue(self.window.tray.isVisible())
            self.window.tray.activated.emit(QSystemTrayIcon.ActivationReason.Context)
            self.assertFalse(self.window.isVisible())
            self.window.tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
            self.assertTrue(self.window.isVisible())
            self.window.showMinimized()
            self.window.tray_menu.actions()[0].trigger()
            self.assertFalse(self.window.isMinimized())
            self.window.quit_launcher()
            self.assertFalse(self.window.isVisible())
            self.assertFalse(self.window.tray.isVisible())

    def test_tray_start_hides_with_a_tray_and_minimises_without_one(self) -> None:
        from appglider.app import show_initial

        with patch("appglider.app.QSystemTrayIcon.isSystemTrayAvailable", return_value=True):
            self.window.hide()
            self.window.tray.show()
            show_initial(self.window, tray=True)
            self.assertTrue(self.window.isHidden())
            self.settings.setValue("General/close-to-tray", False)
            show_initial(self.window, tray=True)
            self.assertTrue(self.window.isMinimized())
            self.settings.setValue("General/close-to-tray", True)
        with patch("appglider.app.QSystemTrayIcon.isSystemTrayAvailable", return_value=False):
            self.window.hide()
            self.window.setWindowState(Qt.WindowState.WindowNoState)
            show_initial(self.window, tray=True)
            self.assertTrue(self.window.isMinimized())
        self.window.hide()
        show_initial(self.window, tray=False)
        self.assertTrue(self.window.isVisible())

    def test_start_with_my_desktop_switch_reflects_and_changes_the_entry(self) -> None:
        page = self.window.settings_page
        library = self.window.library
        self.assertFalse(page.autostart.isChecked())
        self.assertEqual(page.autostart.accessibleName(), "Start with my desktop")
        page.autostart.setChecked(True)
        self.assertTrue(library.autostart_enabled())
        self.assertIn("--tray", library.autostart_file.read_text())
        page.autostart.setChecked(False)
        self.assertFalse(library.autostart_file.exists())
        library.enable_autostart(["/x"])  # changed outside the window
        page.hide()
        page.show()
        self.assertTrue(page.autostart.isChecked())
        library.autostart_file.unlink()
        page.hide()
        page.show()
        self.assertFalse(page.autostart.isChecked())

    def test_start_with_my_desktop_reverts_and_explains_an_error(self) -> None:
        page = self.window.settings_page
        library = self.window.library
        library.autostart_dir.mkdir(parents=True)
        library.autostart_file.write_text("[Desktop Entry]\nExec=/bin/true\n")
        page.autostart.setChecked(True)
        self.assertFalse(page.autostart.isChecked())
        self.assertIn("another application", page.autostart_error.text())
        self.assertFalse(page.autostart_error.isHidden())
        self.assertEqual(library.autostart_file.read_text(), "[Desktop Entry]\nExec=/bin/true\n")

    def test_no_tray_host_keeps_normal_close_behaviour(self) -> None:
        with patch("appglider.ui.window.QSystemTrayIcon.isSystemTrayAvailable", return_value=False):
            self.window.tray.show()
            event = QCloseEvent()
            self.window.closeEvent(event)
            self.assertTrue(event.isAccepted())
            self.assertFalse(self.window.tray.isVisible())

    def test_tray_menu_launches_only_managed_apps_and_shows_update_count(self) -> None:
        self.assertFalse(self.window.tray_apps_menu.isEnabled())
        self.assertFalse(self.window.tray_check_action.isEnabled())
        payload = appimage_bytes()
        with patch("appglider.github.http.request", return_value=io.BytesIO(payload)):
            self.window.library.install(
                APP, release_for("1.0.0", payload), Event(), lambda *args: None
            )
        self.window.releases[APP.id] = release_for("1.1.0", payload)
        self.window.scan_installed()
        self.assertEqual(self.window.tray.toolTip(), "AppGlider · Updates: 1")
        actions = self.window.tray_apps_menu.actions()
        self.assertEqual([action.text() for action in actions], [APP.name])
        with patch.object(self.window, "launch_app") as launch:
            actions[0].trigger()
            launch.assert_called_once_with(APP.id)
        self.assertTrue(self.window.tray_check_action.isEnabled())

    def test_tray_icon_gets_an_update_badge_only_while_updates_wait(self) -> None:
        window = self.window
        self.assertFalse(window.tray_has_updates)
        self.assertEqual(window.tray.icon().cacheKey(), window.tray_icons[0].cacheKey())
        payload = appimage_bytes()
        with patch("appglider.github.http.request", return_value=io.BytesIO(payload)):
            window.library.install(APP, release_for("1.0.0", payload), Event(), lambda *args: None)
        window.releases[APP.id] = release_for("1.1.0", payload)
        window.scan_installed()
        self.assertTrue(window.tray_has_updates)
        self.assertEqual(window.tray.icon().cacheKey(), window.tray_icons[1].cacheKey())
        self.assertIn("Updates: 1", window.tray.toolTip())
        self.assertNotEqual(window.tray_icons[0].cacheKey(), window.tray_icons[1].cacheKey())
        window.releases[APP.id] = release_for("1.0.0", payload)
        window.scan_installed()
        self.assertFalse(window.tray_has_updates)
        self.assertEqual(window.tray.icon().cacheKey(), window.tray_icons[0].cacheKey())
        for icon in (APP_ICON, APP_UPDATE_ICON):
            self.assertTrue(QImage(str(icon)).hasAlphaChannel())
            self.assertEqual(QImage(str(icon)).pixelColor(0, 0).alpha(), 0)

    def test_background_checks_are_cheap_and_explicit_details_checks_are_full(self) -> None:
        with patch("appglider.ui.window.discover_releases", return_value=object()) as discover:
            for force in (False, True):
                operation = self.window.release_operation(APP, force)
                operation(Event(), lambda *args: None)
                self.assertEqual(discover.call_args.kwargs["cheap"], True)
                self.assertEqual(discover.call_args.kwargs["force"], force)

    def test_the_details_check_is_a_full_check(self) -> None:
        calls: list[dict[str, object]] = []

        def discover(*args: object, **kwargs: object) -> object:
            calls.append(kwargs)
            return self.release_info()

        with patch("appglider.ui.window.discover_releases", side_effect=discover):
            self.window.open_app(APP.id)
            self.window.app_page.check_button.click()
            self.wait_for_task()
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["force"])
        self.assertFalse(calls[0].get("cheap", False))

    def shown(self) -> list[str]:
        return [card.app.id for card in self.window.app_list.rows]

    def install_apps(self, *apps: App) -> None:
        payload = appimage_bytes()
        for app in apps:
            with patch("appglider.github.http.request", return_value=io.BytesIO(payload)):
                self.window.library.install(
                    app,
                    replace(release_for("1.0.0", payload), name=f"{app.id}-1.0.0.AppImage"),
                    Event(),
                    lambda *args: None,
                )
        self.window.scan_installed()

    def by_name(self) -> list[str]:
        return [app.id for app in sorted(APPS, key=lambda a: a.name.casefold())]

    def reopen(self) -> LauncherWindow:
        self.settings.sync()
        reopened = LauncherWindow(self.settings, self.window.library)
        self.addCleanup(close_window, reopened)
        return reopened

    def test_apps_are_in_name_order_without_a_saved_order_and_there_is_no_sort_box(self) -> None:
        self.assertEqual(self.shown(), self.by_name())
        self.assertFalse(hasattr(self.window, "sort_combo"))
        self.assertIsNone(self.settings.value("library/order"))
        self.assertFalse(self.settings.contains("library/sort"))
        self.assertEqual(self.window.options_button.accessibleName(), "Library options")
        self.assertEqual(self.window.options_button.toolTip(), "Library options")
        self.assertEqual(
            [action.text() for action in self.window.options_button.menu().actions()],
            ["Sort A–Z"],
        )

    def test_apps_without_a_place_in_the_saved_order_come_last(self) -> None:
        names = self.by_name()
        self.settings.setValue("library/order", json.dumps([names[5], "gone", names[1]]))
        self.window.refresh()
        rest = [app_id for app_id in names if app_id not in (names[5], names[1])]
        self.assertEqual(self.shown(), [names[5], names[1], *rest])
        # A newly added app lands at the end, whatever its name.
        extra = custom_app("example/aaa-first", "Aaa first")
        self.settings.setValue("library/order", json.dumps(names))
        self.window.apps = (*self.window.apps, extra)
        self.assertEqual(self.window.sorted_apps(self.window.apps)[-1].id, extra.id)

    def test_sort_alphabetically_asks_first_and_keeps_pinned_apps_on_top(self) -> None:
        window = self.window
        names = self.by_name()
        window.reorder_app(names[-1], 0)
        window.toggle_pin(names[3])
        custom = self.shown()
        self.assertNotEqual(custom[1:], [n for n in names if n != names[3]])
        with patch("appglider.ui.window.QMessageBox.question") as ask:
            ask.return_value = QMessageBox.StandardButton.No
            window.sort_action.trigger()
            self.assertEqual(
                ask.call_args.args[1:],
                (
                    "Sort the library A–Z?",
                    "This replaces your own order. Pinned apps stay on top.",
                ),
            )
            self.assertEqual(self.shown(), custom)
            ask.return_value = QMessageBox.StandardButton.Yes
            window.sort_action.trigger()
        self.assertEqual(self.shown(), [names[3], *[n for n in names if n != names[3]]])
        self.assertEqual(json.loads(self.settings.value("library/order")), names)

    def test_library_options_are_disabled_with_fewer_than_two_apps(self) -> None:
        window = self.window
        self.assertTrue(window.options_button.isEnabled())
        window.apps = window.apps[:1]
        window.refresh()
        self.assertFalse(window.options_button.isEnabled())

    def migrated(self, mode: str, order: list[str] | None = None) -> LauncherWindow:
        self.settings.setValue("library/sort", mode)
        if order is not None:
            self.settings.setValue("library/order", json.dumps(order))
        return self.reopen()

    def test_a_legacy_sort_choice_becomes_the_order_it_showed(self) -> None:
        other = APPS[3]
        self.install_apps(APP, other)
        names = self.by_name()
        installed = [i for i in names if i in (APP.id, other.id)]
        rest = [i for i in names if i not in installed]
        expected = {
            "name": names,
            "installed": [*installed, *rest],
            "updates": [other.id, APP.id, *rest],
            "added": [app.id for app in APPS],
            "custom": names,
        }
        for mode, order in expected.items():
            with self.subTest(mode=mode):
                self.settings.remove("library/order")
                with patch.object(LauncherWindow, "has_update", lambda s, i: i == other.id):
                    reopened = self.migrated(mode)
                self.assertFalse(self.settings.contains("library/sort"))
                self.assertEqual(json.loads(self.settings.value("library/order")), order)
                self.assertEqual([row.app.id for row in reopened.app_list.rows], order)

    def test_a_legacy_sort_choice_keeps_an_existing_order_and_is_removed(self) -> None:
        names = self.by_name()
        wanted = [names[4], names[0]]
        self.migrated("installed", wanted)
        self.assertFalse(self.settings.contains("library/sort"))
        self.assertEqual(json.loads(self.settings.value("library/order")), wanted)

    def test_nothing_is_written_when_there_is_no_legacy_sort(self) -> None:
        self.reopen()
        self.assertFalse(self.settings.contains("library/sort"))
        self.assertFalse(self.settings.contains("library/order"))

    def test_pinned_apps_come_first_in_every_order_and_persist(self) -> None:
        window = self.window
        target = APPS[5]
        card = window.cards[target.id]
        self.assertEqual(card.pin_action.text(), "Pin to top")
        card.pin_action.trigger()
        self.assertEqual(self.shown()[0], target.id)
        self.assertEqual(card.pin_action.text(), "Unpin")
        self.assertTrue(card.pin_star.isVisibleTo(card))  # a separate, gold ★ beside the name
        self.assertEqual(card.pin_star.text(), "★")
        self.assertEqual(card.name_button.text(), target.name)  # the name itself is unchanged
        self.assertIn("pinned", card.name_button.accessibleName())
        self.assertIn("QLabel#pinStar { color: #e6a817", self.qt.styleSheet())
        self.assertEqual(json.loads(self.settings.value("library/pinned")), [target.id])
        self.settings.sync()
        reopened = QSettings(self.settings.fileName(), QSettings.Format.IniFormat)
        self.assertEqual(pinned_apps(reopened), {target.id})
        card.pin_action.trigger()  # unpin
        self.assertTrue(card.pin_star.isHidden())
        self.assertEqual(card.name_button.text(), target.name)
        self.assertEqual(card.pin_action.text(), "Pin to top")
        self.assertEqual(window.pinned, set())

    def test_pinning_still_works_with_search_and_the_tray_menu_follows_the_order(self) -> None:
        window = self.window
        payload = appimage_bytes()
        for app in (APP, APPS[3]):
            with patch("appglider.github.http.request", return_value=io.BytesIO(payload)):
                window.library.install(
                    app,
                    replace(release_for("1.0.0", payload), name=f"{app.id}-1.0.0.AppImage"),
                    Event(),
                    lambda *args: None,
                )
        window.scan_installed()
        window.toggle_pin(APPS[3].id)
        self.assertEqual(
            [action.text() for action in window.tray_apps_menu.actions()],
            [APPS[3].name, APP.name],
        )
        window.search.setText("craft")
        self.assertEqual(self.shown()[0], APPS[3].id)
        window.search.setText(APP.name)
        self.assertEqual(self.shown(), [APP.id])

    def test_unreadable_pinned_settings_mean_nothing_is_pinned(self) -> None:
        for garbage in ("not json", "{}", "[1, 2]", '["a", 3]', "", "null"):
            with self.subTest(garbage=garbage):
                self.settings.setValue("library/pinned", garbage)
                self.assertEqual(pinned_apps(self.settings), set())
        self.settings.setValue("library/pinned", json.dumps(["a", "b", "a"]))
        self.assertEqual(pinned_apps(self.settings), {"a", "b"})

    def assert_chip(self, card, state: str, text: str, button: str) -> None:
        self.assertEqual(card.chip.property("state"), state)
        self.assertEqual(card.chip.text(), text)
        self.assertEqual(card.action_button.text(), button)

    def test_row_chip_states_follow_the_status_table(self) -> None:
        card = self.window.cards[APP.id]
        payload = appimage_bytes()
        current = release_for("1.0.0", payload)
        newer = release_for("1.1.0", payload)
        path = Path(self.temp.name) / "photo.AppImage"
        installed = Installed(path, "1.0.0", True)
        self.assert_chip(card, "none", "Not installed", "Install")
        self.assertEqual(card.installed_version.text(), "—")
        self.assertEqual(card.action_button.objectName(), "")
        card.set_state(installed, None, False)
        self.assert_chip(card, "none", "Not checked", "Launch")
        self.assertEqual(card.installed_version.text(), "v1.0.0")
        card.set_state(installed, None, False, refreshing=True)
        self.assert_chip(card, "none", "Checking…", "Launch")
        card.set_state(installed, current, False)
        self.assert_chip(card, "ok", "✓ Up to date", "Launch")
        card.set_state(installed, current, False, error="Offline", refresh_error="Offline")
        self.assert_chip(card, "failed", "Check failed", "Launch")
        self.assertIn("Offline", card.chip.toolTip())
        # No network is not a failed check: the row keeps what was known before.
        card.set_state(installed, current, False, refresh_error=http.UNREACHABLE)
        self.assert_chip(card, "ok", "✓ Up to date", "Launch")
        self.assertIn("Offline: could not reach GitHub", card.chip.toolTip())
        card.set_state(installed, newer, False, refresh_error=http.UNREACHABLE)
        self.assert_chip(card, "update", "↑ 1.1.0", "Update")
        card.set_state(installed, None, False, refresh_error=http.UNREACHABLE)
        self.assert_chip(card, "none", "Not checked", "Launch")
        card.set_state(installed, newer, False)
        self.assert_chip(card, "update", "↑ 1.1.0", "Update")
        self.assertEqual(card.action_button.objectName(), "primary")
        self.assertEqual(card.action_button.accessibleName(), f"Update {APP.name}")
        card.set_state(installed, newer, True)
        self.assertFalse(card.action_button.isEnabled())
        preview = Installed(path, "1.0.0", True, prerelease=True)
        card.app = replace(APP, include_prereleases=True)
        card.set_state(preview, current, False)
        self.assert_chip(card, "preview", "Preview channel", "Launch")
        card.app = replace(APP, hold_updates=True)
        card.set_state(installed, newer, False)
        self.assert_chip(card, "held", "‖ Held · 1.1.0 out", "Launch")
        self.assertIn("Held at v1.0.0 · v1.1.0 available", card.chip.toolTip())
        card.set_state(installed, current, False)
        self.assert_chip(card, "held", "‖ Held", "Launch")
        self.assertEqual(card.hold_action.text(), "Release hold")
        card.app = APP
        card.set_state(installed, newer, False, working="Updating")
        self.assert_chip(card, "busy", "Updating…", "Cancel")
        self.assertTrue(card.action_button.isEnabled())
        cancelled: list[str] = []
        card.cancel_requested.connect(cancelled.append)
        card.action_button.click()
        self.assertEqual(cancelled, [APP.id])

    def test_install_shows_a_busy_chip_on_that_row_only(self) -> None:
        payload = appimage_bytes()
        release = release_for("1.0.0", payload)
        seen: list[str] = []
        with (
            patch("appglider.ui.window.QThreadPool.globalInstance"),
            patch("appglider.ui.window.latest_release", return_value=release),
        ):
            self.window.install_app(APP.id)
            seen.append(self.window.cards[APP.id].chip.text())
            self.assertEqual(self.window.cards[APP.id].action_button.text(), "Cancel")
            self.assertEqual(self.window.cards[APPS[3].id].chip.text(), "Not installed")
        self.assertEqual(seen, ["Installing…"])
        self.window.task_finished(TaskResult("Cancelled"))
        self.assertEqual(self.window.cards[APP.id].chip.text(), "Not installed")
        self.assertEqual(self.window.cards[APP.id].action_button.text(), "Install")

    def test_unreadable_custom_order_settings_mean_no_custom_order(self) -> None:
        for garbage in ("not json", "{}", "[1, 2]", '["a", 3]', "", "null"):
            with self.subTest(garbage=garbage):
                self.settings.setValue("library/order", garbage)
                self.assertEqual(custom_order(self.settings), [])
        self.settings.setValue("library/order", json.dumps(["b", "a", "b", "x" * 300]))
        self.assertEqual(custom_order(self.settings), ["b", "a"])

    def test_moving_a_row_saves_a_custom_order(self) -> None:
        window = self.window
        names = self.shown()
        window.cards[names[2]].move_up_action.trigger()
        expected = [names[0], names[2], names[1], *names[3:]]
        self.assertEqual(self.shown(), expected)
        self.assertEqual(json.loads(self.settings.value("library/order")), expected)
        window.cards[names[0]].move_down_action.trigger()
        expected = [names[2], names[0], names[1], *names[3:]]
        self.assertEqual(self.shown(), expected)
        self.settings.sync()
        reopened = LauncherWindow(self.settings, window.library)
        self.addCleanup(close_window, reopened)
        self.assertEqual([row.app.id for row in reopened.app_list.rows], expected)
        self.assertFalse(window.cards[names[2]].move_up_action.isEnabled())
        self.assertFalse(window.cards[names[-1]].move_down_action.isEnabled())

    def test_custom_order_keeps_pinned_apps_above_unpinned_ones(self) -> None:
        window = self.window
        names = self.shown()
        window.toggle_pin(names[4])
        window.toggle_pin(names[7])
        pinned = self.shown()[:2]
        self.assertEqual(set(pinned), {names[4], names[7]})
        first_unpinned = window.cards[self.shown()[2]]
        self.assertFalse(first_unpinned.move_up_action.isEnabled())
        self.assertTrue(window.cards[pinned[0]].move_down_action.isEnabled())
        self.assertFalse(window.cards[pinned[1]].move_down_action.isEnabled())
        before = self.shown()
        window.reorder_app(first_unpinned.app.id, 0)  # clamped below the pinned apps
        self.assertEqual(self.shown()[:2], pinned)
        self.assertEqual(self.shown(), before)
        window.reorder_app(pinned[0], len(before) - 1)  # clamped inside the pinned group
        self.assertEqual(self.shown()[:2], [pinned[1], pinned[0]])
        self.assertEqual(self.shown()[2:], before[2:])
        window.reorder_app(before[-1], 2)
        self.assertEqual(self.shown()[2], before[-1])

    def installed_view(self, *apps: App):
        """Install some apps, show only them, and return (visible ids, full effective order)."""
        self.install_apps(*apps)
        self.window.filter_installed.click()
        self.qt.processEvents()
        return self.shown(), [a.id for a in self.window.sorted_apps(self.window.apps)]

    def test_installed_filter_reorders_and_keeps_hidden_apps_in_place(self) -> None:
        window = self.window
        names = self.by_name()
        a, b, c = names[1], names[4], names[7]
        visible, full = self.installed_view(*[x for x in APPS if x.id in (a, b, c)])
        self.assertEqual(visible, [a, b, c])
        self.assertTrue(window.reorder_enabled())
        self.assertTrue(all(row.draggable for row in window.app_list.rows))
        hidden = [i for i in full if i not in visible]
        # Move down: directly after the next visible row, hidden apps between stay put.
        window.cards[a].move_down_action.trigger()
        full = [x.id for x in window.sorted_apps(window.apps)]
        self.assertEqual(self.shown(), [b, a, c])
        self.assertEqual([i for i in full if i not in visible], hidden)
        self.assertEqual(full.index(a), full.index(b) + 1)
        # Move up: directly before the previous visible row.
        window.cards[c].move_up_action.trigger()
        full = [x.id for x in window.sorted_apps(window.apps)]
        self.assertEqual(self.shown(), [b, c, a])
        self.assertEqual(full.index(c), full.index(a) - 1)
        self.assertEqual([i for i in full if i not in visible], hidden)
        self.assertEqual(json.loads(self.settings.value("library/order")), full)

    def test_installed_filter_drop_before_a_row_and_at_the_end(self) -> None:
        window = self.window
        names = self.by_name()
        a, b, c = names[1], names[4], names[7]
        visible, full = self.installed_view(*[x for x in APPS if x.id in (a, b, c)])
        hidden = [i for i in full if i not in visible]
        window.drop_app(c, 1)  # before b
        full = [x.id for x in window.sorted_apps(window.apps)]
        self.assertEqual(self.shown(), [a, c, b])
        self.assertEqual(full.index(c), full.index(b) - 1)  # directly before b
        self.assertEqual([i for i in full if i not in visible], hidden)
        window.drop_app(a, 3)  # after the last visible row
        full = [x.id for x in window.sorted_apps(window.apps)]
        self.assertEqual(self.shown(), [c, b, a])
        self.assertEqual(full.index(a), full.index(b) + 1)  # directly after b
        self.assertEqual([i for i in full if i not in visible], hidden)
        saved = self.settings.value("library/order")
        window.drop_app(a, 3)  # where it already is
        window.drop_app(a, 2)
        self.assertEqual(self.settings.value("library/order"), saved)

    def test_installed_filter_reordering_stays_inside_the_pinned_group(self) -> None:
        window = self.window
        names = self.by_name()
        a, b, c = names[1], names[4], names[7]
        self.install_apps(*[x for x in APPS if x.id in (a, b, c)])
        window.toggle_pin(b)
        window.filter_installed.click()
        self.qt.processEvents()
        self.assertEqual(self.shown()[0], b)
        self.assertFalse(window.cards[b].move_down_action.isEnabled())
        self.assertFalse(window.cards[self.shown()[1]].move_up_action.isEnabled())
        order = self.settings.value("library/order")
        window.reorder_app(b, 2)  # the pinned app cannot go below the unpinned ones
        window.reorder_app(self.shown()[1], 0)  # nor an unpinned one above it
        self.assertEqual(self.shown()[0], b)
        self.assertEqual(self.settings.value("library/order"), order)

    def drop_event(self, row_id: str, position: QPointF, cls=QDropEvent):
        mime = QMimeData()
        mime.setData(ORDER_MIME, row_id.encode())
        event = cls(
            position,
            Qt.DropAction.MoveAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        return event, mime

    def test_dropping_a_row_reorders_the_list_and_shows_a_drop_line(self) -> None:
        window = self.window
        names = self.shown()
        self.qt.processEvents()
        app_list = window.app_list
        self.assertTrue(app_list.reorderable)
        self.assertTrue(all(row.draggable for row in app_list.rows))
        target = app_list.rows[1].geometry()
        position = QPointF(40, target.top() + 5)
        move, keep = self.drop_event(names[5], position, QDragMoveEvent)
        app_list.dragMoveEvent(move)
        self.assertTrue(app_list.drop_line.isVisibleTo(app_list))
        self.assertEqual(app_list.drop_line.y(), target.top() - 1)
        drop, keep2 = self.drop_event(names[5], position)
        app_list.dropEvent(drop)
        self.assertTrue(app_list.drop_line.isHidden())
        self.assertEqual(self.shown(), [names[0], names[5], *names[1:5], *names[6:]])
        self.assertEqual(json.loads(self.settings.value("library/order")), self.shown())
        self.assertEqual((keep.hasFormat(ORDER_MIME), keep2.hasFormat(ORDER_MIME)), (True, True))
        # Dropping a row where it already is changes nothing.
        self.qt.processEvents()
        saved = self.settings.value("library/order")
        same, keep3 = self.drop_event(
            self.shown()[3], QPointF(40, app_list.rows[3].geometry().top() + 5)
        )
        app_list.dropEvent(same)
        self.assertEqual(self.settings.value("library/order"), saved)
        self.assertTrue(keep3.hasFormat(ORDER_MIME))

    def test_dropping_below_the_last_row_and_across_the_pin_boundary_is_clamped(self) -> None:
        window = self.window
        names = self.shown()
        window.toggle_pin(names[0])
        self.qt.processEvents()
        app_list = window.app_list
        end = QPointF(40, app_list.rows[-1].geometry().bottom() - 1)
        drop, keep = self.drop_event(names[0], end)
        app_list.dropEvent(drop)
        self.assertEqual(self.shown()[0], names[0])  # the pinned app cannot leave the top
        top, keep2 = self.drop_event(names[3], QPointF(40, 2))
        app_list.dropEvent(top)
        self.assertEqual(self.shown()[:2], [names[0], names[3]])
        self.assertTrue(keep.hasFormat(ORDER_MIME) and keep2.hasFormat(ORDER_MIME))

    def test_drag_handle_shows_when_reordering_works_and_only_it_starts_a_drag(self) -> None:
        window = self.window
        self.qt.processEvents()
        row = window.app_list.rows[1]
        handle = row.handle
        self.assertTrue(handle.isVisibleTo(row))
        self.assertEqual(handle.toolTip(), "Drag to reorder")
        icon_x = row.icon.x()
        window.search.setText(row.app.name)
        self.qt.processEvents()
        self.assertTrue(handle.isHidden())
        self.assertEqual(row.icon.x(), icon_x)  # the hidden handle keeps its space
        window.search.clear()
        self.qt.processEvents()
        self.assertTrue(handle.isVisibleTo(row))
        start = QPoint(7, 14)
        with (
            patch.object(row, "start_drag") as drag,
            patch.object(window, "open_app") as opened,
        ):
            QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=start)
            opened.assert_not_called()  # a click on the handle does not open the app
            QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=start)
            moved = QMouseEvent(
                QEvent.Type.MouseMove,
                QPointF(start + QPoint(0, QApplication.startDragDistance() + 2)),
                QPointF(handle.mapToGlobal(start)),
                Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            handle.mouseMoveEvent(moved)
            drag.assert_called_once_with(handle.mapTo(row, start))
            # Dragging on the rest of the row no longer starts a drag.
            drag.reset_mock()
            body = QPoint(row.width() // 2, row.height() // 2)
            QTest.mousePress(row, Qt.MouseButton.LeftButton, pos=body)
            QTest.mouseMove(row, body + QPoint(0, 30))
            drag.assert_not_called()

    def test_reordering_is_disabled_while_searching_and_on_updates(self) -> None:
        window = self.window
        names = self.shown()

        def assert_disabled() -> None:
            self.assertFalse(window.reorder_enabled())
            self.assertFalse(window.app_list.reorderable)
            for row in window.app_list.rows:
                self.assertFalse(row.draggable)
                self.assertFalse(row.move_up_action.isEnabled())
                self.assertFalse(row.move_down_action.isEnabled())
            order = self.settings.value("library/order")
            window.reorder_app(names[-1], 0)
            drop, keep = self.drop_event(names[-1], QPointF(40, 2))
            window.app_list.dropEvent(drop)
            self.assertEqual(self.settings.value("library/order"), order)
            self.assertTrue(keep.hasFormat(ORDER_MIME))

        window.search.setText("craft")
        assert_disabled()
        window.search.clear()
        self.assertTrue(window.reorder_enabled())
        self.assertTrue(window.cards[names[1]].move_up_action.isEnabled())
        window.filter_installed.click()
        self.assertTrue(window.reorder_enabled())
        window.filter_all.click()
        window.show_view("updates")
        assert_disabled()

    def test_tray_installed_apps_follow_the_custom_order(self) -> None:
        window = self.window
        payload = appimage_bytes()
        chosen = (APP, APPS[3], APPS[6])
        for app in chosen:
            with patch("appglider.github.http.request", return_value=io.BytesIO(payload)):
                window.library.install(
                    app,
                    replace(release_for("1.0.0", payload), name=f"{app.id}-1.0.0.AppImage"),
                    Event(),
                    lambda *args: None,
                )
        window.scan_installed()
        by_name = sorted(chosen, key=lambda app: app.name.casefold())
        self.assertEqual(
            [action.text() for action in window.tray_apps_menu.actions()],
            [app.name for app in by_name],
        )
        window.reorder_app(by_name[-1].id, 0)  # last installed app to the very top
        expected = [by_name[-1], *by_name[:-1]]
        self.assertEqual(
            [action.text() for action in window.tray_apps_menu.actions()],
            [app.name for app in expected],
        )
        window.toggle_pin(by_name[0].id)  # pinned still leads the menu
        self.assertEqual(window.tray_apps_menu.actions()[0].text(), by_name[0].name)

    def waiting_update(self, version: str = "1.1.0") -> None:
        window = self.window
        if APP.id not in window.installed:
            payload = appimage_bytes()
            with patch("appglider.github.http.request", return_value=io.BytesIO(payload)):
                window.library.install(
                    APP, release_for("1.0.0", payload), Event(), lambda *args: None
                )
        window.releases[APP.id] = release_for(version, appimage_bytes())
        window.scan_installed()

    def notifier(self):
        """Patch the desktop so a tray exists and the window is in the background."""
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(
            patch("appglider.ui.window.QSystemTrayIcon.isSystemTrayAvailable", return_value=True)
        )
        stack.enter_context(patch.object(self.window.tray, "isVisible", return_value=True))
        stack.enter_context(patch.object(self.window, "isActiveWindow", return_value=False))
        return stack.enter_context(patch.object(self.window.tray, "showMessage"))

    def test_held_update_is_hidden_until_released(self) -> None:
        window = self.window
        shown = self.notifier()
        self.settings.setValue("notifications/updates", True)
        self.waiting_update()
        card = window.cards[APP.id]
        self.assertEqual(window.updates_count.text(), "1")
        card.hold_action.trigger()
        self.assertEqual(window.updates_count.text(), "0")
        self.assertFalse(window.tray_has_updates)
        self.assertEqual(window.tray.icon().cacheKey(), window.tray_icons[0].cacheKey())
        card = window.cards[APP.id]
        self.assertEqual(card.action_button.text(), "Launch")
        self.assertTrue(window.update_all_button.isHidden())
        self.assertEqual(card.chip.property("state"), "held")
        self.assertEqual(card.chip.text(), "‖ Held · 1.1.0 out")
        self.assertIn("Held at v1.0.0 · v1.1.0 available", card.chip.toolTip())
        self.assertEqual(card.hold_action.text(), "Release hold")
        with patch("appglider.ui.window.discover_releases") as discover:
            window.update_all()
            self.assertIsNone(window.task)
            window.notify_updates()
            shown.assert_not_called()
            card.hold_action.trigger()
            discover.assert_not_called()
        card = window.cards[APP.id]
        self.assertEqual(window.updates_count.text(), "1")
        self.assertEqual(card.action_button.text(), "Update")
        self.assertEqual(card.chip.property("state"), "update")
        self.assertEqual(card.hold_action.text(), "Hold at current version")

    def test_hold_is_disabled_while_busy_and_details_show_it(self) -> None:
        self.waiting_update()
        card = self.window.cards[APP.id]
        installed = self.window.installed[APP.id]
        card.set_state(installed, self.window.releases[APP.id], True)
        self.assertFalse(card.hold_action.isEnabled())
        page = self.window.app_page
        self.window.open_app(APP.id)
        self.assertEqual(page.hold_hint.text(), "Keep checking, never offer updates")
        self.assertFalse(page.hold_switch.isChecked())
        self.window.library.set_update_hold(APP.id, True)
        self.window.scan_installed()
        self.assertEqual(page.hold_hint.text(), "Held at v1.0.0 · v1.1.0 available")
        self.assertTrue(page.hold_switch.isChecked())
        self.assertTrue(page.hold_switch.isEnabled())
        self.window.cards[APP.id].set_state(installed, self.window.releases[APP.id], True)
        self.window.task = object()  # type: ignore[assignment]
        page.refresh()
        self.window.task = None
        self.assertFalse(page.hold_switch.isEnabled())

    def test_update_notifications_are_opt_in_and_sent_once_per_new_version(self) -> None:
        window = self.window
        shown = self.notifier()
        self.waiting_update()
        window.notify_updates()
        shown.assert_not_called()  # off by default
        self.settings.setValue("notifications/updates", True)
        window.notified = {}
        window.notify_updates()
        shown.assert_called_once()
        title, text = shown.call_args.args[:2]
        self.assertEqual(title, "AppGlider: updates available")
        self.assertEqual(text, f"{APP.name} 1.1.0 is available.")
        window.notify_updates()
        shown.assert_called_once()  # already told about 1.1.0
        self.waiting_update("1.2.0")
        window.notify_updates()
        self.assertEqual(shown.call_count, 2)
        self.assertIn("1.2.0", shown.call_args.args[1])
        self.assertEqual(json.loads(self.settings.value("notifications/seen")), {APP.id: "1.2.0"})
        window.releases[APP.id] = release_for("1.0.0", appimage_bytes())  # no longer waiting
        window.notify_updates()
        self.assertEqual(window.notified, {})
        self.waiting_update("1.2.0")  # the same version waits again later: tell again
        window.notify_updates()
        self.assertEqual(shown.call_count, 3)

    def test_notifications_stay_quiet_when_the_user_can_already_see_the_window_or_no_tray(
        self,
    ) -> None:
        window = self.window
        shown = self.notifier()
        self.settings.setValue("notifications/updates", True)
        self.waiting_update()
        with (
            patch.object(window, "isVisible", return_value=True),
            patch.object(window, "isActiveWindow", return_value=True),
        ):
            window.notify_updates()
        shown.assert_not_called()
        self.waiting_update("1.3.0")
        with patch("appglider.ui.window.QSystemTrayIcon.isSystemTrayAvailable", return_value=False):
            window.notify_updates()
        shown.assert_not_called()

    def test_several_updates_are_combined_into_one_message(self) -> None:
        window = self.window
        shown = self.notifier()
        self.settings.setValue("notifications/updates", True)
        apps = APPS[:5]
        payload = appimage_bytes()
        for app in apps:
            with patch("appglider.github.http.request", return_value=io.BytesIO(payload)):
                window.library.install(
                    app,
                    replace(release_for("1.0.0", payload), name=f"{app.id}-1.0.0.AppImage"),
                    Event(),
                    lambda *args: None,
                )
            window.releases[app.id] = Release("2.0.0", "x", "x", 1, "0" * 64)
        window.scan_installed()
        window.notify_updates()
        shown.assert_called_once()
        text = shown.call_args.args[1]
        self.assertTrue(text.startswith("5 updates are waiting: "))
        self.assertTrue(text.endswith(", and 2 more."))

    def test_only_scheduled_checks_notify_and_a_notification_click_opens_the_window(self) -> None:
        window = self.window
        self.settings.setValue("notifications/updates", True)
        self.waiting_update()
        with patch.object(window, "notify_updates") as notify:
            window.release_checks_drained()  # a manual or startup refresh
            notify.assert_not_called()
            window.notify_pending = True  # what a scheduled check sets before it starts
            window.release_checks_drained()
            notify.assert_called_once()
            self.assertFalse(window.notify_pending)
        with (
            patch.object(window, "refresh_releases") as refresh,
            patch.object(window.checks, "tasks", {"app": object()}),
        ):
            window.scheduled_check()
            refresh.assert_not_called()  # busy: nothing starts, nothing is left pending
        self.assertFalse(window.notify_pending)
        with patch.object(window, "show_launcher") as show:
            window.tray.messageClicked.emit()
        show.assert_called_once()

    def test_checks_that_could_not_reach_github_retry_with_longer_waits(self) -> None:
        window = self.window
        self.addCleanup(window.offline_timer.stop)
        self.waiting_update()
        waits = []
        for _ in range(6):
            window.offline_timer.stop()
            window.release_errors[APP.id] = http.UNREACHABLE
            window.release_checks_drained()
            if window.offline_timer.isActive():
                waits.append(window.offline_timer.interval())
                self.assertIn("Trying again in", window.footer.text())
        self.assertEqual(waits, [15_000, 30_000, 60_000, 120_000, 300_000])
        self.assertEqual(
            window.footer.text(), "Could not reach GitHub. Rows show what was known before."
        )
        window.update_github_status()
        self.assertEqual(window.github_label.text(), "GitHub: offline")
        self.assertIn("Could not reach GitHub", window.status_block.toolTip())
        with patch.object(window.checks, "add") as add:
            window.offline_retry()
        add.assert_called_once_with([APP.id])
        window.release_errors.pop(APP.id)
        window.release_checks_drained()  # a check got through: the next outage starts over
        self.assertEqual(window.offline_attempts, 0)
        window.update_github_status()
        self.assertNotEqual(window.github_label.text(), "GitHub: offline")
        window.release_errors[APP.id] = "GitHub answered HTTP 404."
        window.release_checks_drained()
        self.assertFalse(window.offline_timer.isActive())
        self.assertIn("Some checks failed", window.footer.text())

    def test_a_background_self_check_failure_retries_and_notifications_wait(self) -> None:
        window = self.window
        self.addCleanup(window.offline_timer.stop)
        window.self_check_failed("Couldn't check")
        self.assertTrue(window.offline_timer.isActive())
        with patch.object(window, "start_self_check") as start:
            window.offline_retry()
        start.assert_called_once_with()
        self.assertFalse(window.self_retry)
        window.offline_timer.stop()
        window.self_explicit = True
        window.self_check_failed("Couldn't check")  # the user asked and sees the failure
        self.assertFalse(window.offline_timer.isActive())
        self.waiting_update()
        window.notify_pending = True
        window.release_errors[APP.id] = http.UNREACHABLE
        with patch.object(window, "notify_updates") as notify:
            window.release_checks_drained()
            notify.assert_not_called()
            self.assertTrue(window.notify_pending)
            window.offline_timer.stop()
            window.release_errors.pop(APP.id)
            window.release_checks_drained()
            notify.assert_called_once()

    def test_notification_setting_defaults_off_and_saves(self) -> None:
        page = self.window.settings_page
        self.assertFalse(page.notify_updates.isChecked())
        self.assertEqual(page.notify_updates.accessibleName(), "Notify me about new updates")
        page.notify_updates.click()
        self.assertTrue(self.settings.value("notifications/updates", type=bool))
        page.notify_updates.click()
        self.assertFalse(self.settings.value("notifications/updates", type=bool))
        page.notify_updates.click()
        self.assertTrue(self.settings.value("notifications/updates", type=bool))
        self.assertEqual(notified_versions(self.settings), {})
        for garbage in ("not json", "[]", '{"a": 1}'):
            self.settings.setValue("notifications/seen", garbage)
            self.assertEqual(notified_versions(self.settings), {})
        self.settings.setValue("notifications/seen", '{"a": "1.0", "b": 2}')
        self.assertEqual(notified_versions(self.settings), {"a": "1.0"})

    def test_update_check_interval_defaults_to_six_hours_and_saves(self) -> None:
        self.assertEqual(self.window.update_timer.interval(), 6 * 3600 * 1000)
        self.assertTrue(self.window.update_timer.isActive())
        combo = self.window.settings_page.check_interval
        self.assertEqual(
            [combo.itemText(i) for i in range(combo.count())],
            ["Every hour", "Every 3 hours", "Every 6 hours", "Every 12 hours", "Every 24 hours",
             "Only when I choose"],
        )  # fmt: skip
        self.assertEqual(combo.currentData(), 6)
        combo.setCurrentIndex(combo.findData(12))
        self.assertEqual(self.settings.value("General/check-interval-hours", type=int), 12)
        self.assertEqual(self.window.update_timer.interval(), 12 * 3600 * 1000)
        combo.setCurrentIndex(combo.findData(0))
        self.assertEqual(self.settings.value("General/check-interval-hours", type=int), 0)
        self.assertFalse(self.window.update_timer.isActive())
        for stored in ("garbage", "5", "-1"):
            self.settings.setValue("General/check-interval-hours", stored)
            self.window.schedule_update_checks()
            self.assertEqual(self.window.update_timer.interval(), 6 * 3600 * 1000)

    def test_general_settings_persist_in_one_section_across_restarts(self) -> None:
        self.settings.setValue("General/check-interval-hours", 12)
        self.settings.sync()
        for hours in (12, 3):  # each restart reads the saved value, then saves another
            reopened = QSettings(self.settings.fileName(), QSettings.Format.IniFormat)
            self.assertEqual(check_interval_hours(reopened), hours)
            reopened.setValue("General/check-interval-hours", 3 if hours == 12 else 1)
            reopened.sync()
        text = Path(self.settings.fileName()).read_text()
        self.assertEqual(text.lower().count("[%general]") + text.count("[General]"), 1)
        self.assertEqual(
            check_interval_hours(QSettings(self.settings.fileName(), QSettings.Format.IniFormat)), 1
        )

    def test_scheduled_checks_run_only_when_idle_with_apps_and_enough_quota(self) -> None:
        window = self.window
        with patch.object(window, "refresh_releases") as refresh:
            window.scheduled_check()
            refresh.assert_not_called()  # nothing installed
            payload = appimage_bytes()
            with patch("appglider.github.http.request", return_value=io.BytesIO(payload)):
                window.library.install(
                    APP, release_for("1.0.0", payload), Event(), lambda *args: None
                )
            window.scan_installed()
            window.scheduled_check()
            refresh.assert_called_once_with()
            refresh.reset_mock()
            with patch("appglider.ui.window.github_status.quota_low", return_value=True):
                window.scheduled_check()
            window.task = object()  # a download or other task is running
            window.scheduled_check()
            window.task = None
            window.quitting = True
            window.scheduled_check()
            window.quitting = False
            refresh.assert_not_called()

    def test_quota_low_needs_a_known_low_unexpired_report(self) -> None:
        original = github_status._status
        self.addCleanup(setattr, github_status, "_status", original)
        reset = time.time() + 600
        github_status._status = github_status.GitHubStatus(remaining=None)
        self.assertFalse(github_status.quota_low())
        github_status._status = github_status.GitHubStatus(remaining=5, limit=60, reset_at=reset)
        self.assertTrue(github_status.quota_low())
        github_status._status = github_status.GitHubStatus(remaining=45, limit=60, reset_at=reset)
        self.assertFalse(github_status.quota_low())
        github_status._status = github_status.GitHubStatus(remaining=5, limit=60, reset_at=1)
        self.assertFalse(github_status.quota_low())  # the window has reset

    def test_hiding_keeps_download_running_and_quit_can_cancel_safely(self) -> None:
        with (
            patch(
                "appglider.ui.window.QSystemTrayIcon.isSystemTrayAvailable",
                return_value=True,
            ),
            patch("appglider.ui.window.QThreadPool.globalInstance"),
            patch("appglider.ui.window.QMessageBox.question") as question,
        ):
            self.window.tray.show()
            self.window.start_task("Downloading", lambda cancel, progress: TaskResult("Done"))
            task = self.window.task
            self.assertFalse(self.window.tray_check_action.isEnabled())
            self.window.close()
            self.assertFalse(task.cancel.is_set())
            question.assert_not_called()
            question.return_value = QMessageBox.StandardButton.No
            self.window.quit_launcher()
            self.assertTrue(self.window.isVisible())
            self.assertFalse(self.window.quitting)
            self.assertFalse(task.cancel.is_set())
            question.return_value = QMessageBox.StandardButton.Yes
            self.window.quit_launcher()
            self.assertTrue(task.cancel.is_set())
            self.assertTrue(self.window.close_after_task)
            self.assertTrue(self.window.quitting)
            self.window.task_finished(TaskResult("Cancelled"))
            self.assertIsNone(self.window.task)
            self.assertFalse(self.window.tray.isVisible())

    def test_catalog_icons_and_install_actions(self) -> None:
        self.assertEqual(len(APPS), 14)
        self.assertEqual(len({app.id for app in APPS}), 14)
        self.assertEqual(len(self.visible_ids()), 14)
        for app in APPS:
            with self.subTest(app=app.id):
                self.assertEqual(self.window.cards[app.id].findChildren(QComboBox), [])
                self.assertTrue(self.window.cards[app.id].name_button.isEnabled())
                self.assertFalse(QPixmap(str(app.icon)).isNull())
                install = next(
                    button
                    for button in self.window.cards[app.id].findChildren(QPushButton)
                    if button.text() == "Install"
                )
                self.assertTrue(install.isEnabled())
                self.assertIn("verify", install.toolTip())
                self.assertFalse(self.window.cards[app.id].shortcut_action.isEnabled())

    def test_search_by_name_description_repository_and_clear(self) -> None:
        self.window.search.setText("  SOLIDcraft  ")
        self.assertEqual(self.visible_ids(), {"solidcraft"})
        self.window.search.setText("CAD")
        self.assertEqual(self.visible_ids(), {"cadcraft", "solidcraft"})
        self.window.search.setText("tangency-labs")
        self.assertEqual(self.visible_ids(), {"solidcraft"})
        self.window.search.setText("no matching app")
        self.assertFalse(self.visible_ids())
        self.assertTrue(self.window.empty.isVisible())
        self.assertIn("No apps match", self.window.empty.text())
        self.window.search.clear()
        self.assertEqual(len(self.visible_ids()), 14)
        self.assertFalse(self.window.empty.isVisible())

    def test_empty_filters_and_return_to_library(self) -> None:
        self.assertEqual(self.window.filter_all.text(), "All")
        self.assertEqual(self.window.filter_installed.text(), "Installed (0)")
        self.window.filter_installed.click()
        self.assertTrue(self.window.installed_only)
        self.assertFalse(self.visible_ids())
        self.assertTrue(self.window.empty.isVisible())
        self.assertFalse(self.window.scroll_area.isVisible())
        self.assertIn("No apps are installed", self.window.empty.text())
        self.window.show_view("updates")
        self.assertFalse(self.visible_ids())
        self.assertTrue(self.window.empty.isVisible())
        self.assertIn("No installed apps to check", self.window.empty.text())
        self.window.show_view("library")
        self.window.filter_all.click()
        self.assertEqual(len(self.visible_ids()), 14)

    def test_a_closed_updates_banner_returns_only_for_something_new(self) -> None:
        window = self.window
        path = Path(self.temp.name) / "app.AppImage"
        for app_id, version in (("solidcraft", "0.16.2"), ("photocraft", "1.4.0")):
            window.installed[app_id] = Installed(path, "0.1.0", True)
            window.releases[app_id] = Release(version, "a", "u", 1, "0" * 64)
        window.refresh()
        self.assertTrue(window.banner_close_button.isVisibleTo(window))
        self.assertEqual(window.banner_close_button.accessibleName(), "Close banner")
        QTest.mouseClick(window.banner_close_button, Qt.MouseButton.LeftButton)
        self.assertTrue(window.banner.isHidden())
        self.assertEqual(window.updates_count.text(), "2")  # the rows and count still say so
        window.show_view("updates")  # where Update all lives, the banner always stays
        self.assertTrue(window.banner.isVisibleTo(window))
        self.assertTrue(window.banner_close_button.isHidden())
        window.show_view("library")
        self.assertTrue(window.banner.isHidden())
        del window.installed["photocraft"]  # one fewer update is nothing new
        window.refresh()
        self.assertTrue(window.banner.isHidden())
        window.releases["solidcraft"] = Release("0.16.3", "a", "u", 1, "0" * 64)
        window.refresh()
        self.assertTrue(window.banner.isVisibleTo(window))
        window.dismiss_banner()
        window.installed["gridcraft"] = Installed(path, "0.1.0", True)
        window.releases["gridcraft"] = Release("1.0.0", "a", "u", 1, "0" * 64)
        window.refresh()
        self.assertTrue(window.banner.isVisibleTo(window))
        self.assertEqual(dismissed_versions(self.settings), {"solidcraft": "0.16.3"})

    def test_updates_banner_badge_and_review_switch_views(self) -> None:
        window = self.window
        self.assertTrue(window.banner.isHidden())
        self.assertTrue(window.updates_count.isHidden())
        self.assertEqual(window.minimumSize().width(), 860)
        self.assertEqual(window.minimumSize().height(), 560)
        path = Path(self.temp.name) / "solidcraft-0.16.1-linux-x86_64.AppImage"
        window.installed["solidcraft"] = Installed(path, "0.16.1", True)
        window.releases["solidcraft"] = Release("0.16.2", "s.AppImage", "unused", 1, "0" * 64)
        window.refresh()
        self.assertTrue(window.banner.isVisibleTo(window))
        self.assertFalse(window.updates_count.isHidden())
        self.assertEqual(window.updates_count.text(), "1")
        self.assertIn("1 update ready.", window.banner_label.text())
        self.assertIn("SolidCraft v0.16.2", window.banner_label.text())
        self.assertEqual(window.update_all_button.objectName(), "primary")
        self.assertTrue(window.filters.isVisibleTo(window))
        QTest.mouseClick(window.review_button, Qt.MouseButton.LeftButton)
        self.assertEqual(window.view, "updates")
        self.assertTrue(window.nav_updates.isChecked())
        self.assertFalse(window.nav_library.isChecked())
        self.assertEqual(window.page_title.text(), "Updates")
        self.assertTrue(window.filters.isHidden())
        self.assertTrue(window.review_button.isHidden())
        self.assertEqual(self.visible_ids(), {"solidcraft"})
        window.nav_library.click()
        self.assertEqual(window.view, "library")
        self.assertEqual(len(self.visible_ids()), 14)
        for number, app in enumerate(window.apps[:5], start=2):
            window.installed[app.id] = Installed(path, "1.0.0", True)
            window.releases[app.id] = Release(f"{number}.0.0", "a", "u", 1, "0" * 64)
        window.refresh()
        self.assertIn("6 updates ready.", window.banner_label.text())
        self.assertIn("and 3 more.", window.banner_label.text())
        window.installed["solidcraft"] = Installed(path, "0.16.2", True)
        window.refresh()

    def test_installed_filter_counts_and_hides_other_apps(self) -> None:
        window = self.window
        path = Path(self.temp.name) / "solidcraft-0.16.1-linux-x86_64.AppImage"
        window.installed["solidcraft"] = Installed(path, "0.16.1", True)
        window.refresh()
        self.assertTrue(window.filter_all.isChecked())
        self.assertFalse(window.installed_only)
        self.assertEqual(window.filter_installed.text(), "Installed (1)")
        window.filter_installed.click()
        self.assertTrue(window.installed_only)
        self.assertEqual(self.visible_ids(), {"solidcraft"})
        window.filter_all.click()
        self.assertFalse(window.installed_only)
        self.assertEqual(len(self.visible_ids()), 14)
        with self.assertRaises(ValueError):
            window.show_view("missing")

    def test_sidebar_status_block_opens_the_update_settings(self) -> None:
        self.assertIn(self.window.status_label.text().split()[0], ("Not", "Automatic", "Checked"))
        QTest.mouseClick(self.window.status_block, Qt.MouseButton.LeftButton)
        self.assertIs(self.window.pages.currentWidget(), self.window.settings_page)
        self.assertEqual(self.window.settings_page.current_section(), "Updates")
        self.assertTrue(self.window.nav_settings.isChecked())
        self.assertEqual(self.window.nav_add.accessibleName(), "Add app")
        self.assertEqual(self.window.check_button.accessibleName(), "Check for updates")

    def test_update_count_and_versions_are_visible_before_selecting_updates(self) -> None:
        path = Path(self.temp.name) / "solidcraft-0.16.1-linux-x86_64.AppImage"
        self.window.installed["solidcraft"] = Installed(path, "0.16.1", True)
        release = Release("0.16.2", "solidcraft.AppImage", "unused", 1, "0" * 64)
        self.window.releases["solidcraft"] = release
        self.window.refresh()

        self.assertEqual(self.window.view, "library")
        self.assertEqual(self.window.updates_count.text(), "1")
        card = self.window.cards["solidcraft"]
        self.assertEqual(card.installed_version.text(), "v0.16.1")
        self.assertEqual(card.chip.text(), "↑ 0.16.2")
        self.assertEqual(card.action_button.text(), "Update")
        self.window.show_view("updates")
        self.assertEqual(self.visible_ids(), {"solidcraft"})
        self.assertEqual(card.installed_version.text(), "v0.16.1")
        self.assertEqual(card.chip.text(), "↑ 0.16.2")

        self.window.search.setText("photocraft")
        self.assertFalse(self.visible_ids())
        self.assertEqual(self.window.library_count.text(), "14")
        self.assertEqual(self.window.filter_installed.text(), "Installed (1)")
        self.assertEqual(self.window.updates_count.text(), "1")
        self.window.installed["solidcraft"] = Installed(path, "0.16.2", True)
        self.window.refresh()
        self.assertEqual(self.window.updates_count.text(), "0")

    def test_rows_fill_the_list_without_overlap_and_survive_filtering(self) -> None:
        first_row = self.window.cards["photocraft"]
        for width, height in ((1400, 800), (860, 600)):
            self.window.resize(width, height)
            self.qt.processEvents()
            rows = self.window.app_list.rows
            self.assertEqual(len(rows), 14)
            viewport = self.window.scroll_area.viewport()
            self.assertLessEqual(self.window.app_list.width(), viewport.width())
            for index, row in enumerate(rows):
                self.assertEqual(row.height(), 60)
                self.assertGreaterEqual(row.x(), 0)
                self.assertLessEqual(row.geometry().right(), self.window.app_list.width())
                if index:
                    self.assertGreaterEqual(row.y(), rows[index - 1].geometry().bottom())
        self.window.search.setText("PhotoCraft")
        self.assertIs(self.window.app_list.rows[0], first_row)

    def test_row_controls_stay_in_one_line_at_narrow_and_wide_widths(self) -> None:
        info = self.release_info()
        for width in (860, 1400):
            self.window.resize(width, 1000)
            self.qt.processEvents()
            for installed in (
                None,
                Installed(Path(self.temp.name) / "old.AppImage", "0.9.0", True),
                Installed(
                    Path(self.temp.name) / "preview.AppImage",
                    info.preview.version,
                    True,
                    prerelease=True,
                ),
            ):
                row = self.window.cards[APP.id]
                offered = info.stable.release if installed else info.preview.release
                row.set_state(installed, offered, False, info=info)
                self.qt.processEvents()
                self.assertEqual(row.height(), 60)
                parts = [
                    row.icon,
                    row.name_button,
                    row.installed_version,
                    row.chip,
                    row.action_button,
                ]
                boxes = [part.mapTo(row, part.rect().topLeft()).x() for part in parts]
                self.assertEqual(boxes, sorted(boxes))
                for part in parts:
                    rect = part.rect().translated(part.mapTo(row, part.rect().topLeft()))
                    self.assertTrue(row.rect().contains(rect), part.objectName())
                chip = row.chip.mapTo(row, row.chip.rect().topRight())
                self.assertLess(chip.x(), row.action_button.x())
                self.assertEqual(row.action_button.width(), 84)
                if not installed:
                    self.assertEqual(row.action_button.text(), "Install")
                    self.assertEqual(row.installed_version.text(), "—")
            self.window.refresh()
            self.qt.processEvents()
            self.assertEqual({row.height() for row in self.window.app_list.rows}, {60})

    def test_clicking_name_opens_the_app_page(self) -> None:
        info = self.release_info()
        self.window.release_infos["solidcraft"] = info
        self.window.apply_release_info(self.window.cards["solidcraft"].app)
        name = self.window.cards["solidcraft"].findChild(QPushButton, "appName")
        self.assertIsNotNone(name)
        QTest.mouseClick(name, Qt.MouseButton.LeftButton)
        page = self.window.app_page
        self.assertIs(self.window.pages.currentWidget(), page)
        self.assertEqual(page.title.text(), "SolidCraft")
        self.assertIn("tangency-labs/solidcraft", page.source.text())
        self.assertEqual(page.back_button.text(), "‹ Library")
        self.assertIn(info.stable.version, page.versions["stable"].text.text())
        self.assertIn(info.preview.version, page.versions["preview"].text.text())

    def test_destroyed_windows_stop_receiving_system_theme_changes(self) -> None:
        calls = []

        class ThemeWindow(LauncherWindow):
            def apply_theme(self):
                calls.append(True)

        window = ThemeWindow(self.settings, self.window.library)
        try:
            calls.clear()
            self.qt.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Dark)
            self.assertEqual(calls, [True])
        finally:
            close_window(window)
        calls.clear()
        self.qt.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Light)
        self.assertEqual(calls, [])

    def test_destroyed_window_cancels_pending_launch_status_check(self) -> None:
        window = LauncherWindow(self.settings, self.window.library)
        window.installed[APP.id] = Installed(Path(self.temp.name) / "app.AppImage", "1.0.0", True)
        single_shot = QTimer.singleShot
        with (
            patch.object(window.library, "launch") as launch,
            patch(
                "appglider.ui.window.QTimer.singleShot",
                side_effect=lambda delay, *args: single_shot(0, *args),
            ),
            patch("appglider.ui.window.QMessageBox.warning") as warning,
        ):
            process = launch.return_value
            process.poll.return_value = 1
            window.library.processes[APP.id] = process
            try:
                window.launch_app(APP.id)
            finally:
                window.quitting = True
                window.close()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.qt.processEvents()
            process.poll.assert_not_called()
            warning.assert_not_called()

    def test_settings_apply_immediately_and_persist(self) -> None:
        page = self.window.settings_page
        folder = str(Path(self.temp.name) / "future-apps")
        self.assertTrue(self.window.nav_settings.isEnabled())
        QTest.mouseClick(self.window.nav_settings, Qt.MouseButton.LeftButton)
        self.assertIs(self.window.pages.currentWidget(), page)
        page.theme.buttons[2].click()
        self.assertEqual(self.settings.value("appearance/theme"), "Dark")
        self.assertEqual(self.qt.palette().color(QPalette.ColorRole.Window).name(), "#15181e")
        page.folder.setText(folder)
        page.folder.editingFinished.emit()
        self.assertEqual(self.settings.value("installation/folder"), folder)
        self.assertEqual(self.window.library.apps_dir, Path(folder))
        self.assertFalse(Path(folder).exists())
        self.settings.sync()
        reopened = QSettings(self.settings.fileName(), QSettings.Format.IniFormat)
        self.assertEqual(reopened.value("appearance/theme"), "Dark")
        self.assertEqual(reopened.value("installation/folder"), folder)
        page.theme.buttons[1].click()
        self.assertEqual(self.settings.value("appearance/theme"), "Light")
        self.assertEqual(self.qt.palette().color(QPalette.ColorRole.Window).name(), "#eef0f4")

    def test_appearance_choices_switch_and_persist(self) -> None:
        def window_colour() -> str:
            return self.qt.palette().color(QPalette.ColorRole.Window).name()

        self.settings.remove("appearance/theme")
        page = SettingsPage(self.window)
        self.addCleanup(page.deleteLater)
        self.assertEqual(
            [button.text() for button in page.theme.buttons], ["System", "Light", "Dark"]
        )
        self.assertEqual(page.theme.currentIndex(), 0)
        self.assertEqual(page.theme.accessibleName(), "Theme")
        for index, theme, colour in ((2, "Dark", "#15181e"), (1, "Light", "#eef0f4")):
            self.window.settings_page.theme.buttons[index].click()
            self.assertEqual(self.settings.value("appearance/theme"), theme)
            self.assertEqual(window_colour(), colour)

    def test_retired_themes_and_unknown_values_are_migrated_on_read(self) -> None:
        for stored, expected in (
            ("Forest", ("Dark", "Green")),
            ("Earthy", ("Dark", "Amber")),
            ("Sandstone", ("Dark", "Amber")),
            ("Unknown", ("System", "Blue")),
            ("Light", ("Light", "Blue")),
        ):
            self.settings.setValue("appearance/theme", stored)
            self.settings.remove("appearance/accent")
            self.assertEqual(appearance_choice(self.settings), expected)
            self.assertEqual(self.settings.value("appearance/theme"), stored)
            self.assertIsNone(self.settings.value("appearance/accent"))
        self.settings.setValue("appearance/theme", "Dark")
        self.settings.setValue("appearance/accent", "Mauve")
        self.assertEqual(appearance_choice(self.settings), ("Dark", "Blue"))
        self.settings.setValue("appearance/accent", "Amber")
        self.assertEqual(appearance_choice(self.settings), ("Dark", "Amber"))
        self.settings.setValue("appearance/accent", "Clay")  # retired accent
        self.assertEqual(appearance_choice(self.settings), ("Dark", "Amber"))
        self.assertEqual(self.settings.value("appearance/accent"), "Clay")  # read never writes

    def test_retired_theme_applies_and_shows_migrated_values(self) -> None:
        self.settings.setValue("appearance/theme", "Forest")
        self.window.apply_theme()
        palette = self.qt.palette()
        self.assertEqual(palette.color(QPalette.ColorRole.Window).name(), "#15181e")
        self.assertEqual(palette.color(QPalette.ColorRole.Highlight).name(), "#6fcf97")
        page = SettingsPage(self.window)
        self.addCleanup(page.deleteLater)
        self.assertEqual(page.theme.buttons[page.theme.currentIndex()].text(), "Dark")
        self.assertTrue(page.accents["Green"].isChecked())
        self.assertEqual(self.settings.value("appearance/theme"), "Forest")  # nothing written yet
        page.accents["Amber"].click()
        page.accents["Green"].click()
        self.assertEqual(self.settings.value("appearance/theme"), "Dark")
        self.assertEqual(self.settings.value("appearance/accent"), "Green")

    def test_accent_swatches_apply_immediately_and_persist(self) -> None:
        def highlight() -> str:
            return self.qt.palette().color(QPalette.ColorRole.Highlight).name()

        self.settings.setValue("appearance/theme", "Light")
        self.window.apply_theme()
        self.assertEqual(highlight(), "#3563d8")
        page = self.window.settings_page
        self.assertEqual(list(page.accents), ["Blue", "Green", "Amber", "Teal"])
        for name, button in page.accents.items():
            self.assertTrue(button.isCheckable())
            self.assertEqual(button.accessibleName(), f"{name} accent")
        self.assertTrue(page.accents["Blue"].isChecked())
        page.accents["Teal"].click()
        self.assertTrue(page.accents["Teal"].isChecked())
        self.assertFalse(page.accents["Blue"].isChecked())
        self.assertEqual(highlight(), "#0f7c80")
        self.assertEqual(self.settings.value("appearance/accent"), "Teal")
        page.theme.buttons[2].click()
        page.accents["Amber"].click()
        self.assertEqual(self.settings.value("appearance/accent"), "Amber")
        self.assertEqual(self.settings.value("appearance/theme"), "Dark")
        self.assertEqual(highlight(), "#d9a35b")
        self.assertIn("#d9a35b", self.qt.styleSheet())

    def test_system_appearance_follows_the_desktop_scheme(self) -> None:
        self.settings.setValue("appearance/theme", "System")
        hints = self.qt.styleHints()
        for scheme, colour in (
            (Qt.ColorScheme.Dark, "#15181e"),
            (Qt.ColorScheme.Light, "#eef0f4"),
        ):
            with patch.object(type(hints), "colorScheme", return_value=scheme):
                self.window.apply_theme()
            self.assertEqual(self.qt.palette().color(QPalette.ColorRole.Window).name(), colour)
        self.settings.setValue("appearance/theme", "Unknown")
        with patch.object(type(hints), "colorScheme", return_value=Qt.ColorScheme.Dark):
            self.window.apply_theme()
        self.assertEqual(self.qt.palette().color(QPalette.ColorRole.Window).name(), "#15181e")

    def wait_for_task(self) -> None:
        deadline = time.monotonic() + 5
        while self.window.task is not None and time.monotonic() < deadline:
            QTest.qWait(10)
            time.sleep(0.001)  # Let Python worker threads run between Qt waits.
        self.assertIsNone(self.window.task, "Background operation did not finish")

    def test_new_folder_name_is_readable_in_light_and_dark_themes(self) -> None:
        for theme in ("Light", "Dark"):
            with self.subTest(theme=theme):
                self.settings.setValue("appearance/theme", theme)
                self.window.apply_theme()
                dialog = self.window.settings_page
                self.window.open_settings("Storage")
                dialog.folder.setText(self.temp.name)
                dialog.apply_folder()
                observations = []

                def type_folder_name() -> None:
                    picker = self.qt.activeModalWidget()
                    if not isinstance(picker, QFileDialog):
                        if picker is not None:
                            picker.close()
                        return
                    try:
                        create = picker.findChild(QToolButton, "newFolderButton")
                        if create is None:
                            return
                        deadline = time.monotonic() + 5
                        while not create.isEnabled() and time.monotonic() < deadline:
                            QTest.qWait(10)
                        create.click()
                        editor = None
                        while time.monotonic() < deadline:
                            editor = next(
                                (
                                    edit
                                    for edit in picker.findChildren(QLineEdit)
                                    if edit.isVisible() and not edit.objectName()
                                ),
                                None,
                            )
                            if editor is not None:
                                break
                            QTest.qWait(10)
                        if editor is None:
                            return
                        editor.selectAll()
                        QTest.keyClicks(editor, "My AppImages")
                        option = QStyleOptionFrame()
                        editor.initStyleOption(option)
                        text_rect = editor.style().subElementRect(
                            QStyle.SubElement.SE_LineEditContents, option, editor
                        )
                        observations.append(
                            (editor.text(), text_rect.height(), editor.fontMetrics().height())
                        )
                    finally:
                        picker.reject()

                QTimer.singleShot(100, type_folder_name)
                dialog.browse()
                self.assertEqual(len(observations), 1, "New-folder editor did not appear")
                text, available_height, font_height = observations[0]
                self.assertEqual(text, "My AppImages")
                self.assertGreaterEqual(available_height, font_height)
                self.assertEqual(dialog.folder.text(), self.temp.name)

    def wait_for_release_checks(self) -> None:
        deadline = time.monotonic() + 10
        while (self.window.checks.busy or self.window.checks.queue) and time.monotonic() < deadline:
            QTest.qWait(10)
            time.sleep(0.001)
        self.assertFalse(self.window.checks.busy, "Background release check did not finish")
        self.assertFalse(self.window.checks.queue)

    def release_info(self) -> ReleaseInfo:
        payload = appimage_bytes()
        stable = release_for("1.0.0", payload)
        preview = replace(release_for("1.1.0-beta.1", payload), prerelease=True, is_latest=False)
        return ReleaseInfo(
            ReleaseOption(stable.version, stable),
            ReleaseOption(preview.version, preview),
            time.time(),
        )

    def test_empty_library_and_added_repositories_make_no_background_requests(self) -> None:
        info = self.release_info()
        atomic_json(info_path(APP, self.window.library.data_dir / "release-cache"), asdict(info))
        with patch("appglider.ui.window.discover_releases") as discover:
            self.window.refresh_releases()
            self.window.refresh_releases(force=True)
            self.window.library.add_repository(custom_app("example/another-app"))
            self.window.catalog_changed()
            discover.assert_not_called()
        self.assertFalse(self.window.checks.busy)
        self.assertFalse(self.window.checks.queue)
        self.assertFalse(self.window.check_button.isEnabled())
        self.assertFalse(self.window.tray_check_action.isEnabled())
        for card in self.window.cards.values():
            self.assertEqual(card.chip.text(), "Not installed")
            self.assertEqual(card.chip.property("state"), "none")
            self.assertEqual(card.installed_version.text(), "—")
            self.assertTrue(card.action_button.isEnabled())

    def test_the_page_explicitly_checks_an_uninstalled_app_without_downloading(self) -> None:
        info = self.release_info()
        page = self.window.app_page
        with (
            patch("appglider.ui.window.discover_releases", return_value=info) as discover,
            patch("appglider.install.library.download") as download,
        ):
            self.window.open_app(APP.id)
            before = page.versions["stable"].text.text()
            page.check_button.click()
            self.wait_for_task()
            after = (
                page.versions["stable"].text.text(),
                page.versions["preview"].text.text(),
                page.check_button.isEnabled(),
            )
            discover.assert_called_once()
            self.assertEqual(discover.call_args.args[0].id, APP.id)
            self.assertTrue(discover.call_args.kwargs["force"])
            download.assert_not_called()
        self.assertEqual(before, "Not checked")
        self.assertEqual(after, (f"v{info.stable.version}", f"v{info.preview.version}", True))
        self.assertEqual(self.window.installed, {})
        self.assertEqual(self.window.cards[APP.id].chip.text(), "Not installed")

    def test_the_page_can_change_or_close_while_its_release_check_finishes(self) -> None:
        info = self.release_info()
        page = self.window.app_page
        for leave in ("back", "other app", "removed"):
            finish = Event()

            def discover(app, cancel, cache_dir, *, force):
                if not finish.wait(5):
                    raise TimeoutError("Test release check was not released")
                return info

            with (
                self.subTest(leave=leave),
                patch("appglider.ui.window.discover_releases", side_effect=discover),
            ):
                self.window.release_infos.pop(APP.id, None)
                self.window.open_app(APP.id)
                page.check_button.click()
                self.assertEqual(page.check_button.text(), "Checking…")
                try:
                    if leave == "back":
                        page.back_button.click()
                    elif leave == "other app":
                        self.window.open_app(APPS[1].id)
                    else:
                        self.window.library.remove_repository(APP.id)
                        self.window.scan_installed()
                        self.assertEqual(self.window.pages.currentIndex(), 0)
                finally:
                    finish.set()
                self.wait_for_task()
                self.assertEqual(self.window.pages.currentIndex() == 1, leave == "other app")
                if leave != "removed":
                    self.assertEqual(self.window.release_infos[APP.id], info)
                self.assertEqual(page.note.text(), page.message)
                if leave == "removed":
                    self.window.library.import_catalog(STARTER_ENTRIES)
                    self.window.scan_installed()
                if self.window.pages.currentIndex() == 1:
                    page.back_button.click()

    def test_a_failed_page_check_keeps_cached_versions_and_allows_retry(self) -> None:
        info = self.release_info()
        atomic_json(info_path(APP, self.window.library.data_dir / "release-cache"), asdict(info))
        self.window.scan_installed()
        page = self.window.app_page
        with (
            patch("appglider.ui.window.discover_releases", side_effect=OSError("Offline")),
            patch("appglider.ui.window.QMessageBox.warning"),
            patch("appglider.ui.window.QThreadPool.globalInstance") as pool,
        ):
            pool.return_value.start.side_effect = lambda task: task.run()
            self.window.open_app(APP.id)
            page.check_button.click()
        self.assertEqual(page.versions["stable"].text.text(), f"v{info.stable.version}")
        self.assertEqual(page.note.text(), "Offline")
        self.assertTrue(page.check_button.isEnabled())
        self.assertEqual(self.window.release_infos[APP.id], info)

    def test_queued_background_check_skips_an_app_that_was_uninstalled(self) -> None:
        other = APPS[1]
        record = Installed(Path(self.temp.name) / "example.AppImage", "0.9.0", True)
        self.window.installed = {APP.id: record, other.id: record}
        with patch("appglider.ui.window.QThreadPool.globalInstance") as pool:
            self.window.checks.add([APP.id, other.id])
            self.assertEqual(self.window.checks.queue, [other.id])
            self.window.installed.pop(other.id)
            self.window.checks.tasks[APP.id].signals.finished.emit(TaskResult("Done"))
            self.assertEqual(pool.return_value.start.call_count, 1)
        self.assertFalse(self.window.checks.busy)
        self.assertFalse(self.window.checks.queue)

    def test_unrecognised_entries_can_be_reviewed_kept_or_forgotten(self) -> None:
        state = self.window.library.data_dir / "library.json"
        atomic_json(state, {"old-app": {"path": "/gone/Old.AppImage", "version": "2.0"}})
        self.window.scan_installed()
        self.assertFalse(self.window.orphans_button.isHidden())
        self.assertIn("1 unrecognised entry", self.window.orphans_button.text())
        for accepted, ticked, remaining in ((False, True, 1), (True, False, 1), (True, True, 0)):

            def choose(dialog: OrphansDialog) -> QDialog.DialogCode:
                state = Qt.CheckState.Checked if ticked else Qt.CheckState.Unchecked
                dialog.entries.item(0).setCheckState(state)
                return QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected

            with (
                self.subTest(accepted=accepted, ticked=ticked),
                patch.object(OrphansDialog, "exec", choose),
            ):
                self.window.review_orphans()
            self.assertEqual(len(self.window.library.orphans()), remaining)
        self.assertTrue(self.window.orphans_button.isHidden())

    def test_release_checks_overlap_only_when_quota_is_plentiful(self) -> None:
        other = APPS[1]
        record = Installed(Path(self.temp.name) / "example.AppImage", "0.9.0", True)
        for plentiful, started in ((False, 1), (True, 2)):
            with (
                self.subTest(plentiful=plentiful),
                patch("appglider.ui.window.QThreadPool.globalInstance") as pool,
                patch("appglider.ui.window.github_status.quota_plentiful", return_value=plentiful),
            ):
                self.window.installed = {APP.id: record, other.id: record}
                self.window.checks.add([APP.id, other.id])
                self.assertEqual(pool.return_value.start.call_count, started)
                self.assertEqual(len(self.window.checks.tasks), started)
                while self.window.checks.tasks:
                    task = next(iter(self.window.checks.tasks.values()))
                    task.signals.finished.emit(TaskResult("Done"))
                self.assertFalse(self.window.checks.busy)
                self.assertFalse(self.window.checks.queue)

    def test_refresh_only_checks_installed_apps_and_respects_release_preferences(self) -> None:
        info = self.release_info()
        payload = appimage_bytes()
        with patch("appglider.github.http.request", return_value=io.BytesIO(payload)):
            self.window.library.install(
                APP, release_for("0.9.0", payload), Event(), lambda *args: None
            )
        with patch("appglider.ui.window.discover_releases", return_value=info) as discover:
            self.window.refresh_releases()
            self.assertTrue(self.window.cards[APP.id].action_button.isEnabled())
            self.wait_for_release_checks()
            discover.assert_called_once()
            self.assertEqual(discover.call_args.args[0].id, APP.id)
            self.assertFalse(discover.call_args.kwargs["force"])
            self.window.check_button.click()
            self.wait_for_release_checks()
            self.assertEqual(discover.call_count, 2)
            self.assertTrue(discover.call_args.kwargs["force"])
        card = self.window.cards[APP.id]
        self.assertEqual(card.chip.text(), f"↑ {info.stable.version}")
        self.assertIn("latest release", card.chip.toolTip())
        self.assertNotIn(
            info.preview.version,
            " ".join(
                label.text() for label in card.findChildren(QLabel) if label.isVisibleTo(card)
            ),
        )
        self.assertEqual(card.action_button.text(), "Update")
        self.assertEqual(self.window.updates_count.text(), "1")
        self.change_release_preference(1)
        preview_chip = self.window.cards[APP.id].chip
        self.assertEqual(preview_chip.text(), f"↑ {info.preview.version}")
        self.assertIn("pre-release", preview_chip.toolTip())
        self.assertEqual(self.window.check_button.toolTip(), "Check for updates")

    def change_release_preference(self, index: int) -> None:
        self.window.open_app(APP.id)
        self.assertEqual(self.window.app_page.app_id, APP.id)
        self.window.app_page.channel.buttons[index].click()
        self.window.app_page.back_button.click()

    def test_card_preference_changes_offers_and_count_without_installing(self) -> None:
        info = self.release_info()
        payload = appimage_bytes()
        with patch("appglider.github.http.request", return_value=io.BytesIO(payload)):
            self.window.library.install(APP, info.stable.release, Event(), lambda *args: None)
        atomic_json(info_path(APP, self.window.library.data_dir / "release-cache"), asdict(info))
        self.window.scan_installed()
        self.assertEqual(self.window.updates_count.text(), "0")
        with patch.object(self.window.library, "install") as install:
            self.change_release_preference(1)
            install.assert_not_called()
        card = self.window.cards[APP.id]
        self.assertEqual(self.window.updates_count.text(), "1")
        self.assertEqual(card.chip.text(), f"↑ {info.preview.version}")
        self.assertIn("pre-release", card.chip.toolTip())
        self.assertEqual(card.action_button.text(), "Update")
        self.assertTrue(card.app.include_prereleases)
        reopened = LauncherWindow(self.settings, self.window.library)
        self.addCleanup(reopened.deleteLater)
        self.addCleanup(reopened.close)
        self.assertTrue(reopened.cards[APP.id].app.include_prereleases)
        self.assertEqual(reopened.cards[APP.id].findChildren(QComboBox), [])
        self.assertEqual(reopened.updates_count.text(), "1")
        self.change_release_preference(0)
        self.assertEqual(self.window.updates_count.text(), "0")
        card = self.window.cards[APP.id]
        self.assertEqual(card.chip.property("state"), "ok")
        self.assertEqual(card.action_button.text(), "Launch")
        self.assertIn("latest release", card.chip.toolTip())

    def test_preferred_window_size_is_limited_to_the_screen(self) -> None:
        laptop = fit_size(1040, 816, QRect(0, 0, 1366, 728))
        self.assertEqual((laptop.width(), laptop.height()), (1040, 655))
        small = fit_size(1040, 816, QRect(0, 0, 1024, 700))
        self.assertEqual((small.width(), small.height()), (921, 630))
        large = fit_size(1040, 816, QRect(0, 0, 2560, 1400))
        self.assertEqual((large.width(), large.height()), (1040, 816))

    def test_window_size_is_remembered_between_launches(self) -> None:
        self.window.resize(860, 600)
        self.window.quitting = True
        self.window.close()
        reopened = LauncherWindow(self.settings, self.window.library)
        self.addCleanup(reopened.deleteLater)
        self.addCleanup(reopened.close)
        self.assertEqual((reopened.width(), reopened.height()), (860, 600))

    def test_failed_refresh_retains_badges_and_shows_last_checked_without_dialog(self) -> None:
        info = replace(self.release_info(), checked_at=time.time() - 3600)
        payload = appimage_bytes()
        with patch("appglider.github.http.request", return_value=io.BytesIO(payload)):
            self.window.library.install(
                APP, release_for("0.9.0", payload), Event(), lambda *args: None
            )
        atomic_json(info_path(APP, self.window.library.data_dir / "release-cache"), asdict(info))
        self.window.scan_installed()
        self.assertIn("check needed", self.window.cards[APP.id].chip.toolTip())
        with (
            patch("appglider.ui.window.discover_releases", side_effect=OSError("Offline")),
            patch("appglider.ui.window.QMessageBox.warning") as warning,
        ):
            self.window.refresh_releases()
            self.wait_for_release_checks()
            warning.assert_not_called()
        card = self.window.cards[APP.id]
        self.assertEqual(card.chip.property("state"), "update")  # the saved release is kept
        self.assertEqual(card.action_button.text(), "Update")
        self.assertEqual(self.window.release_infos[APP.id].preview, info.preview)
        self.assertIn("Last checked", card.chip.toolTip())
        self.assertIn("Offline", card.chip.toolTip())

    def test_install_rechecks_an_uninstalled_app_after_a_cached_preview_failure(self) -> None:
        info = replace(
            self.release_info(),
            preview=ReleaseOption("1.1.0-beta.1", None, "No supported AppImage was found."),
        )
        atomic_json(info_path(APP, self.window.library.data_dir / "release-cache"), asdict(info))
        self.window.scan_installed()
        self.assertTrue(self.window.cards[APP.id].action_button.isEnabled())
        self.change_release_preference(1)
        card = self.window.cards[APP.id]
        self.assertTrue(card.action_button.isEnabled())
        self.assertEqual(card.chip.text(), "Not installed")
        self.assertEqual(self.window.updates_count.text(), "0")
        payload = appimage_bytes()
        release = replace(release_for("1.1.0-beta.1", payload), prerelease=True, is_latest=False)
        with (
            patch("appglider.ui.window.latest_release", return_value=release) as latest,
            patch("appglider.github.http.request", return_value=io.BytesIO(payload)),
        ):
            card.action_button.click()
            self.wait_for_task()
        latest.assert_called_once()
        self.assertTrue(latest.call_args.args[0].include_prereleases)
        self.assertEqual(self.window.installed[APP.id].version, release.version)
        self.assertTrue(self.window.installed[APP.id].prerelease)

    def test_closing_cancels_background_refresh_without_installation_prompt(self) -> None:
        payload = appimage_bytes()
        with patch("appglider.github.http.request", return_value=io.BytesIO(payload)):
            self.window.library.install(
                APP, release_for("1.0.0", payload), Event(), lambda *args: None
            )
        with (
            patch("appglider.ui.window.QThreadPool.globalInstance"),
            patch("appglider.ui.window.QMessageBox.question") as question,
            patch("appglider.ui.window.QSystemTrayIcon.isSystemTrayAvailable", return_value=False),
        ):
            self.window.refresh_releases()
            task = self.window.checks.tasks[APP.id]
            self.assertTrue(self.window.cards[APP.id].name_button.isEnabled())
            self.assertFalse(self.window.close())
            self.assertTrue(task.cancel.is_set())
            self.assertFalse(self.window.checks.queue)
            task.signals.finished.emit(TaskResult("Cancelled"))
            question.assert_not_called()
            self.assertFalse(self.window.checks.busy)
            self.assertFalse(self.window.isVisible())

    def test_managed_appimage_gets_launch_action_and_installed_filter(self) -> None:
        payload = appimage_bytes()
        with patch("appglider.github.http.request", return_value=io.BytesIO(payload)):
            self.window.library.install(
                APP, release_for("1.0.0", payload), Event(), lambda *args: None
            )
        self.window.scan_installed()
        path = self.window.installed[APP.id].path
        card = self.window.cards[APP.id]
        self.assertEqual(card.action_button.text(), "Launch")
        self.window.filter_installed.click()
        self.assertEqual(self.visible_ids(), {APP.id})
        with patch("appglider.install.library.subprocess.Popen") as spawn:
            spawn.return_value.poll.return_value = None
            QTest.mouseClick(card.action_button, Qt.MouseButton.LeftButton)
            self.assertEqual(spawn.call_args.args[0], [str(path)])
        self.assertTrue(card.remove_action.isEnabled())
        self.assertTrue(card.shortcut_action.isEnabled())
        shortcut = self.window.library.desktop_dir / f"appglider-{APP.id}.desktop"
        shortcut.unlink()
        with patch("appglider.ui.window.QMessageBox.warning") as warning:
            card.shortcut_requested.emit(APP.id)
            deadline = time.monotonic() + 5
            while self.window.menu_task is not None and time.monotonic() < deadline:
                QTest.qWait(10)
            self.assertIsNone(self.window.menu_task)
            warning.assert_not_called()
        self.assertIn("Created menu shortcut", self.window.footer.text())
        self.assertTrue(shortcut.is_file())
        self.assertIn("Name=PhotoCraft (AppGlider)", shortcut.read_text())

    def test_legacy_external_link_does_not_appear_as_installed_or_updatable(self) -> None:
        path = Path(self.temp.name) / "solidcraft.AppImage"
        path.write_bytes(appimage_bytes())
        self.window.library._save({"solidcraft": Installed(path, "0.16.1", False)})
        self.window.releases["solidcraft"] = Release("0.16.2", "unused", "unused", 1, "0" * 64)
        self.window.scan_installed()
        self.assertNotIn("solidcraft", self.window.installed)
        self.assertEqual(self.window.cards["solidcraft"].action_button.text(), "Install")
        self.assertEqual(self.window.updates_count.text(), "0")
        self.window.filter_installed.click()
        self.assertFalse(self.visible_ids())
        self.assertEqual(path.read_bytes(), appimage_bytes())

    def test_install_update_and_rollback_through_background_ui(self) -> None:
        card = self.window.cards[APP.id]
        with patch("appglider.ui.window.QMessageBox.warning") as warning:
            for version in ("1.0.0", "1.1.0"):
                payload = appimage_bytes(version.encode())
                release = release_for(version, payload)
                self.window.releases[APP.id] = release
                self.window.refresh()
                with (
                    patch("appglider.ui.window.latest_release", return_value=release),
                    patch("appglider.github.http.request", return_value=io.BytesIO(payload)),
                ):
                    self.assertEqual(
                        card.action_button.text(), "Update" if version == "1.1.0" else "Install"
                    )
                    QTest.mouseClick(card.action_button, Qt.MouseButton.LeftButton)
                    self.wait_for_task()
                self.assertEqual(self.window.installed[APP.id].version, version)
                self.assertEqual(card.action_button.text(), "Launch")
                self.assertTrue(self.window.installed[APP.id].managed)
            self.assertTrue(card.rollback_action.isEnabled())
            card.rollback_requested.emit(APP.id)
            self.wait_for_task()
            self.assertEqual(self.window.installed[APP.id].version, "1.0.0")
            self.assertEqual(card.action_button.text(), "Update")
            self.assertEqual(card.chip.property("state"), "update")
            warning.assert_not_called()

    def test_update_all_counts_only_apps_that_were_really_updated(self) -> None:
        card = self.window.cards[APP.id]
        first_payload = appimage_bytes(b"1.0.0")
        first = release_for("1.0.0", first_payload)
        with patch("appglider.github.http.request", return_value=io.BytesIO(first_payload)):
            self.window.library.install(APP, first, Event(), lambda *args: None)
        self.window.scan_installed()
        newer_payload = appimage_bytes(b"1.1.0")
        newer = release_for("1.1.0", newer_payload)
        with patch("appglider.ui.window.QMessageBox.warning") as warning:
            for latest, expected, version in ((first, "Updated 0 apps.", "1.0.0"),):
                self.window.releases[APP.id] = newer
                self.window.refresh()
                with patch("appglider.ui.window.latest_release", return_value=latest):
                    self.window.update_all()
                    self.wait_for_task()
                self.assertEqual(self.window.footer.text(), expected)
                self.assertEqual(self.window.installed[APP.id].version, version)
            self.window.releases[APP.id] = newer
            self.window.refresh()
            with (
                patch("appglider.ui.window.latest_release", return_value=newer),
                patch("appglider.github.http.request", return_value=io.BytesIO(newer_payload)),
            ):
                self.window.update_all()
                self.wait_for_task()
            self.assertEqual(self.window.footer.text(), "Updated 1 apps.")
            self.assertEqual(self.window.installed[APP.id].version, "1.1.0")
            self.assertTrue(card.rollback_action.isEnabled())
            warning.assert_not_called()

    def test_busy_state_cancellation_and_launch_stays_available(self) -> None:
        payload = appimage_bytes()
        with patch("appglider.github.http.request", return_value=io.BytesIO(payload)):
            self.window.library.install(
                APP, release_for("1.0.0", payload), Event(), lambda *args: None
            )
        self.window.scan_installed()
        with patch("appglider.ui.window.QThreadPool.globalInstance"):
            self.window.start_task("Test operation", lambda cancel, progress: TaskResult("Done"))
        task = self.window.task
        self.assertIsNotNone(task)
        self.assertTrue(self.window.cards[APP.id].action_button.isEnabled())
        self.assertFalse(self.window.cards["solidcraft"].action_button.isEnabled())
        self.assertFalse(self.window.cards["solidcraft"].chip.text().endswith("…"))
        self.window.task_progress("Large download", 3 * 1024**3, 4 * 1024**3)
        self.assertEqual(self.window.progress_bar.value(), 75)
        QTest.mouseClick(self.window.cancel_button, Qt.MouseButton.LeftButton)
        self.assertTrue(task.cancel.is_set())
        self.window.task_finished(TaskResult("Cancelled"))
        self.assertTrue(self.window.cards["solidcraft"].action_button.isEnabled())

    def test_failed_assertion_cleans_up_without_an_interactive_dialog(self) -> None:
        failed = LibraryTests("test_busy_state_cancellation_and_launch_stays_available")
        dialogs = []
        timer = QTimer()

        def dismiss_unexpected_dialog() -> None:
            dialog = self.qt.activeModalWidget()
            if isinstance(dialog, QMessageBox):
                dialogs.append(dialog.windowTitle())
                dialog.done(QMessageBox.StandardButton.No)

        timer.timeout.connect(dismiss_unexpected_dialog)
        timer.start(10)
        result = unittest.TestResult()
        try:
            with patch.object(failed, "assertTrue", side_effect=AssertionError("Injected failure")):
                failed.run(result)
        finally:
            timer.stop()
        self.assertEqual(len(result.failures), 1)
        self.assertIn("Injected failure", result.failures[0][1])
        self.assertEqual(result.errors, [])
        self.assertEqual(dialogs, [])
        self.assertNotIn(failed.window, self.qt.topLevelWidgets())

    def test_a_folder_inside_appimagelaunchers_own_shows_a_warning(self) -> None:
        page = self.window.settings_page
        self.window.open_settings("Storage")
        theirs = Path(self.temp.name) / "Applications"
        with patch("appglider.ui.pages.settings_page.appimagelauncher_folder", return_value=theirs):
            page.folder.setText(str(theirs / "appglider"))
            page.folder.editingFinished.emit()
            self.assertTrue(page.folder_warning.isVisibleTo(page))
            self.assertIn("AppImageLauncher", page.folder_warning.text())
            page.folder.setText(str(Path(self.temp.name) / "elsewhere"))
            page.folder.editingFinished.emit()
            self.assertTrue(page.folder_warning.isHidden())

    def test_invalid_folder_shows_an_inline_error_and_keeps_the_previous_one(self) -> None:
        page = self.window.settings_page
        library = self.window.library
        self.window.open_settings("Storage")
        good = str(Path(self.temp.name) / "good-apps")
        page.folder.setText(good)
        page.folder.editingFinished.emit()
        self.assertEqual(self.settings.value("installation/folder"), good)
        self.assertTrue(page.folder_error.isHidden())
        for path, message in (
            ("", "Enter an absolute path"),
            ("relative/path", "Enter an absolute path"),
            ("/bad\npath", "control characters"),
            (self.settings.fileName(), "is a file"),
        ):
            with self.subTest(path=path):
                page.folder.setText(path)
                page.folder.editingFinished.emit()
                self.assertTrue(page.folder_error.isVisibleTo(page))
                self.assertIn(message, page.folder_error.text())
                self.assertEqual(self.settings.value("installation/folder"), good)
                self.assertEqual(library.apps_dir, Path(good))
        page.folder.setText(good)
        page.folder.editingFinished.emit()
        self.assertTrue(page.folder_error.isHidden())

    def test_folder_and_catalog_changes_wait_for_a_running_task(self) -> None:
        page = self.window.settings_page
        theme = page.theme.buttons[2]
        self.window.task = object()  # type: ignore[assignment]
        try:
            self.window.refresh()
            for widget in (page.folder, page.browse_button, page.import_button):
                self.assertFalse(widget.isEnabled())
            for widget in (theme, page.close_to_tray, page.notify_updates, page.check_interval):
                self.assertTrue(widget.isEnabled())
            before = self.settings.value("installation/folder")
            page.folder.setText(str(Path(self.temp.name) / "blocked"))
            self.assertFalse(page.apply_folder())
            self.assertEqual(self.settings.value("installation/folder"), before)
            theme.click()
            self.assertEqual(self.settings.value("appearance/theme"), "Dark")
        finally:
            self.window.task = None
            self.window.refresh()
        self.assertTrue(page.folder.isEnabled())

    def test_browse_applies_a_valid_choice_at_once(self) -> None:
        page = self.window.settings_page
        chosen = str(Path(self.temp.name) / "browsed")
        with patch(
            "appglider.ui.pages.settings_page.QFileDialog.getExistingDirectory", return_value=chosen
        ):
            page.browse_button.click()
        self.assertEqual(page.folder.text(), chosen)
        self.assertEqual(self.settings.value("installation/folder"), chosen)
        with patch(
            "appglider.ui.pages.settings_page.QFileDialog.getExistingDirectory", return_value=""
        ):
            page.browse_button.click()
        self.assertEqual(self.settings.value("installation/folder"), chosen)

    def test_storage_space_used_empty_library(self) -> None:
        page = self.window.settings_page
        self.window.open_settings("Storage")
        self.assertTrue(page.space_empty_label.isVisible())
        self.assertEqual(page.space_empty_label.text(), "No apps installed yet")
        self.assertTrue(page.space_summary_bar.isHidden())
        self.assertTrue(page.space_legend.isHidden())
        self.assertTrue(page.space_list.isHidden())
        self.assertEqual(page.space_total_label.text(), "0 MB")
        self.assertIn("by 0 apps", page.space_free_label.text())
        self.assertEqual(page.space_panel.findChildren(QPushButton), [])

    def test_storage_space_used_order_and_sizes_with_rollback(self) -> None:
        page = self.window.settings_page
        library = self.window.library
        self.window.open_settings("Storage")

        app_rolled = self.window.apps[0]
        app_single = self.window.apps[1]

        payload_rolled_old = appimage_bytes(b"v1" + b"x" * 200_000)
        with patch("appglider.github.http.request", return_value=io.BytesIO(payload_rolled_old)):
            library.install(
                app_rolled, release_for("1.0.0", payload_rolled_old), Event(), lambda *a: None
            )

        payload_rolled_new = appimage_bytes(b"v2" + b"y" * 100_000)
        with patch("appglider.github.http.request", return_value=io.BytesIO(payload_rolled_new)):
            library.install(
                app_rolled, release_for("1.1.0", payload_rolled_new), Event(), lambda *a: None
            )

        # Roll back: active version is 1.0.0, kept copy is 1.1.0 (newer than in use)
        library.rollback(app_rolled)
        installed_rolled = library.scan()[app_rolled.id]
        self.assertEqual(installed_rolled.version, "1.0.0")
        prev = previous_copy(installed_rolled)
        self.assertIsNotNone(prev)
        assert prev is not None
        self.assertEqual(prev.version, "1.1.0")

        # Single app with no kept copy
        payload_single = appimage_bytes(b"v3" + b"z" * 500_000)
        with patch("appglider.github.http.request", return_value=io.BytesIO(payload_single)):
            library.install(
                app_single, release_for("1.0.0", payload_single), Event(), lambda *a: None
            )

        self.window.scan_installed()

        self.assertTrue(page.space_empty_label.isHidden())
        self.assertTrue(page.space_summary_bar.isVisible())
        self.assertTrue(page.space_legend.isVisible())
        self.assertTrue(page.space_list.isVisible())

        # No QPushButton inside Space used panel
        self.assertEqual(page.space_panel.findChildren(QPushButton), [])

        # Total line
        expected_in_use = len(payload_rolled_old) + len(payload_single)
        expected_kept = len(payload_rolled_new)
        expected_total = expected_in_use + expected_kept
        self.assertEqual(page.space_total_label.text(), format_space(expected_total))
        self.assertIn("by 2 apps", page.space_free_label.text())

        # Summary bar
        self.assertEqual(page.space_summary_bar.in_use, expected_in_use)
        self.assertEqual(page.space_summary_bar.kept, expected_kept)
        self.assertEqual(
            page.space_summary_bar.accessibleName(),
            f"In use {format_space(expected_in_use)}, "
            f"kept for roll back {format_space(expected_kept)}",
        )

        # An unchanged measurement keeps the rows; a changed one rebuilds them.
        same = page.space_list.findChildren(SpaceBar)
        page.update_space_used()
        self.assertEqual(page.space_list.findChildren(SpaceBar), same)

        # Per-app list order: largest total first
        # app_single (500 KB) > app_rolled (200 KB in use + 100 KB kept = 300 KB)
        list_bars = page.space_list.findChildren(SpaceBar)
        self.assertEqual(len(list_bars), 2)
        self.qt.processEvents()
        for bar in list_bars:
            name = bar.parentWidget().layout().itemAt(0).widget()
            self.assertGreater(bar.geometry().left(), name.geometry().right())

        # First app: app_single (no kept copy)
        bar_single = list_bars[0]
        self.assertEqual(bar_single.in_use, len(payload_single))
        self.assertEqual(bar_single.kept, 0)
        self.assertEqual(
            bar_single.accessibleName(), f"{app_single.name}: {format_space(len(payload_single))}"
        )
        self.assertNotIn("kept for roll back", bar_single.accessibleName())

        # Second app: app_rolled (kept copy whose version is newer than in use)
        bar_rolled = list_bars[1]
        self.assertEqual(bar_rolled.in_use, len(payload_rolled_old))
        self.assertEqual(bar_rolled.kept, len(payload_rolled_new))
        self.assertEqual(
            bar_rolled.accessibleName(),
            f"{app_rolled.name}: {format_space(len(payload_rolled_old))}, "
            f"{format_space(len(payload_rolled_new))} kept for roll back",
        )

    def test_about_links_open_the_project_pages(self) -> None:
        page = self.window.settings_page
        self.window.open_settings("About")
        names = ("Visit appglider.org", "View AppGlider on GitHub", "Report an issue")
        captions = {
            button.text(): button
            for button in page.findChildren(QPushButton)
            if button.text() in names
        }
        self.assertEqual(set(captions), set(names))
        opened: list[str] = []
        with patch(
            "appglider.ui.pages.settings_page.QDesktopServices.openUrl",
            side_effect=lambda url: opened.append(url.toString()),
        ):
            for button in captions.values():
                button.click()
        self.assertEqual(
            opened,
            [
                "https://appglider.org/",
                "https://github.com/Vyrnexis/AppGlider",
                "https://github.com/Vyrnexis/AppGlider/issues",
            ],
        )
        self.assertIn(
            f"AppGlider {APP_VERSION}", [label.text() for label in page.findChildren(QLabel)]
        )

    def test_about_links_to_the_notes_of_the_running_version(self) -> None:
        page = self.window.settings_page
        self.window.open_settings("About")
        self.assertEqual(page.notes_link.text(), f"What's new in {APP_VERSION} ↗")
        with patch("appglider.ui.pages.settings_page.QDesktopServices.openUrl") as opened:
            page.notes_link.click()
        self.assertEqual(
            opened.call_args.args[0].toString(),
            f"https://github.com/Vyrnexis/AppGlider/releases/tag/v{APP_VERSION}",
        )

    def test_about_states_the_licence_and_links_to_it(self) -> None:
        page = self.window.settings_page
        self.window.open_settings("About")
        self.assertEqual(
            page.licence_note.text(), "Free software under GPL-3.0-or-later, with no warranty."
        )
        opened: list[str] = []
        with patch(
            "appglider.ui.pages.settings_page.QDesktopServices.openUrl",
            side_effect=lambda url: opened.append(url.toString()),
        ):
            page.licence_link.click()
        self.assertEqual(opened, ["https://github.com/Vyrnexis/AppGlider/blob/main/LICENSE"])

    def test_legacy_section_numbers_still_open_the_matching_section(self) -> None:
        for number, name in ((0, "General"), (3, "Updates"), (4, "About")):
            self.window.open_settings(number)
            self.assertEqual(self.window.settings_page.current_section(), name)
        self.window.open_settings("Updates")
        self.assertEqual(self.window.settings_page.current_section(), "Updates")
        with self.assertRaises(ValueError):
            self.window.open_settings("Missing")

    def test_footer_version_opens_about_and_settings_sections_are_accessible(self) -> None:
        self.assertEqual(self.window.version_button.text(), f"v{APP_VERSION}")
        self.assertEqual(
            self.window.version_button.cursor().shape(), Qt.CursorShape.PointingHandCursor
        )
        self.assertEqual(self.window.version_button.toolTip(), "About AppGlider")
        self.assertEqual(self.window.findChild(QWidget, "sidebar").width(), 210)
        self.assertTrue(
            self.window.nav_settings.geometry().bottom() < self.window.version_button.y()
        )
        QTest.mouseClick(self.window.version_button, Qt.MouseButton.LeftButton)
        page = self.window.settings_page
        self.assertIs(self.window.pages.currentWidget(), page)
        self.assertEqual(page.current_section(), "About")
        self.assertEqual(
            [button.text() for button in page.sections],
            ["General", "Updates", "Catalogs", "Storage", "About"],
        )
        for section in range(5):
            page.sections[section].click()
            self.assertEqual(page.stack.currentIndex(), section)

    def test_github_status_shows_in_the_sidebar_without_network_requests(self) -> None:
        with (
            patch.object(github_status, "_status", github_status.GitHubStatus()),
            patch.object(github_status.time, "time", return_value=1000),
            patch.dict(http._RETRY_AFTER, {}, clear=True),
            patch.object(http, "request") as request,
        ):
            self.window.update_github_status()
            self.assertEqual(self.window.github_label.text(), "GitHub status")
            self.assertEqual(
                self.window.status_block.cursor().shape(), Qt.CursorShape.PointingHandCursor
            )
            self.assertIn("unknown", self.window.status_block.toolTip())
            github_status.observe_headers(
                {
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Limit": "60",
                    "X-RateLimit-Reset": "1600",
                }
            )
            github_status.release_result("Public GitHub pages", APP.repo, 990)
            http._RETRY_AFTER["github.com"] = 1120
            self.window.github_timer.timeout.emit()
            self.qt.processEvents()
            self.assertEqual(self.window.github_label.text(), "GitHub: limited")
            self.assertTrue(self.window.status_block.property("limited"))
            tooltip = self.window.status_block.toolTip()
            self.assertIn("0 of 60", tooltip)
            self.assertIn("retry in 10 min", tooltip)
            self.assertIn("how often to check", tooltip)
            request.assert_not_called()

    def test_row_chip_describes_the_release_and_beta_waits_for_stable(self) -> None:
        card = self.window.cards["solidcraft"]
        beta = Installed(
            Path(self.temp.name) / "beta.AppImage", "1.2.0-beta.1", True, prerelease=True
        )
        stable = Release("1.2.0", "solid.AppImage", "unused", 1, "0" * 64)
        card.set_state(beta, stable, False)
        self.assertEqual((card.chip.property("state"), card.chip.text()), ("update", "↑ 1.2.0"))
        self.assertIn("latest release", card.chip.toolTip())
        self.assertEqual(card.action_button.text(), "Update")
        previous = Release("1.1.0", "solid.AppImage", "unused", 1, "0" * 64)
        card.set_state(beta, previous, False)
        self.assertEqual(card.chip.property("state"), "preview")
        self.assertEqual(card.chip.text(), "Waiting for stable")
        self.assertIn("Waiting for a newer stable", card.chip.toolTip())
        self.assertIn("Pre-release", card.installed_version.toolTip())
        self.assertEqual(card.action_button.text(), "Launch")
        current = Installed(beta.path, "1.1.0", True)
        card.set_state(current, previous, False)
        self.assertEqual((card.chip.property("state"), card.chip.text()), ("ok", "✓ Up to date"))
        self.assertIn("latest release", card.chip.toolTip())
        card.set_state(current, stable, False)
        self.assertEqual(card.chip.property("state"), "update")
        self.assertIn("latest release", card.chip.toolTip())

    def test_preview_update_row_shows_the_update_and_installed_version(self) -> None:
        info = self.release_info()
        installed = Installed(
            Path(self.temp.name) / "cura.AppImage", "1.1.0-beta.0", True, prerelease=True
        )
        card = self.window.cards[APP.id]
        card.set_state(installed, info.preview.release, False, info=info)
        self.assertEqual(card.chip.text(), f"↑ {info.preview.version}")
        self.assertIn("pre-release", card.chip.toolTip())
        self.assertEqual(card.installed_version.text(), f"v{installed.version}")
        self.assertEqual(card.action_button.text(), "Update")
        self.assertEqual(card.action_button.objectName(), "primary")
        self.assertTrue(card.launch_action.isEnabled())
        self.assertNotIn(
            f"v{info.stable.version}",
            " ".join(
                label.text() for label in card.findChildren(QLabel) if label.isVisibleTo(card)
            ),
        )

        card.set_state(installed, None, False, info=info)
        self.assertEqual(card.chip.text(), "Waiting for stable")
        self.assertIn("Pre-release", card.installed_version.toolTip())

    def test_preview_only_installation_waits_after_disabling_previews(self) -> None:
        info = self.release_info()
        info = replace(info, stable=None)
        installed = Installed(
            Path(self.temp.name) / "preview.AppImage", info.preview.version, True, prerelease=True
        )
        card = self.window.cards[APP.id]
        card.set_state(installed, None, False, info=info)
        self.assertEqual(card.chip.text(), "Waiting for stable")
        self.assertIn("Waiting for a newer stable", card.chip.toolTip())
        self.assertIn("Pre-release", card.installed_version.toolTip())
        self.assertEqual(card.action_button.text(), "Launch")
        self.assertEqual(card.action_button.objectName(), "")

    def test_scan_keeps_newer_channel_information_when_catalog_timestamp_is_equal(self) -> None:
        info = self.release_info()
        atomic_json(info_path(APP, self.window.library.data_dir / "release-cache"), asdict(info))
        new_release = release_for("1.2.0", appimage_bytes())
        self.window.release_infos[APP.id] = replace(
            info, stable=ReleaseOption(new_release.version, new_release)
        )
        self.window.scan_installed()
        self.assertEqual(self.window.releases[APP.id], new_release)

    def test_close_to_tray_setting_can_be_disabled(self) -> None:
        switch = self.window.settings_page.close_to_tray
        self.assertTrue(switch.isChecked())
        switch.click()
        self.assertFalse(self.settings.value("General/close-to-tray", True, type=bool))
        with patch("appglider.ui.window.QSystemTrayIcon.isSystemTrayAvailable", return_value=True):
            self.window.tray.show()
            event = QCloseEvent()
            self.window.closeEvent(event)
            self.assertTrue(event.isAccepted())


if __name__ == "__main__":
    unittest.main()
