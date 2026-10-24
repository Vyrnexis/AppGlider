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
from PySide6.QtCore import QCoreApplication, QEvent, QRect, QSettings, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QImage, QPalette, QPixmap
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

from appglider import APP_ICON, APP_UPDATE_ICON, APP_VERSION, github_status, releases
from appglider.catalog import custom_app
from appglider.jobs import TaskResult
from appglider.library import Installed, Library
from appglider.release_info import ReleaseInfo, ReleaseOption, info_path
from appglider.releases import Release
from appglider.repositories_dialog import RepositoriesPanel
from appglider.storage import atomic_json
from appglider.window import (
    DetailsDialog,
    LauncherWindow,
    OrphansDialog,
    SettingsDialog,
    appearance_choice,
    check_interval_hours,
    fit_size,
    notified_versions,
    pinned_apps,
    sort_mode,
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
        library = stock(
            Library(
                Path(self.temp.name) / "data",
                Path(self.temp.name) / "apps",
                desktop_dir=Path(self.temp.name) / "desktop",
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

        def close_dialog(observed: list[bool]) -> None:
            active = self.qt.activeModalWidget()
            if isinstance(active, SettingsDialog):
                observed.append(active.repositories.adding)
                active.reject()

        observed: list[bool] = []
        QTimer.singleShot(0, lambda: close_dialog(observed))
        window.welcome_add_button.click()
        self.assertEqual(observed, [True])

        opened: list[bool] = []
        with patch.object(RepositoriesPanel, "import_catalog", autospec=True) as importer:
            QTimer.singleShot(50, lambda: close_dialog(opened))
            window.welcome_import_button.click()
        importer.assert_called_once()
        self.assertEqual(opened, [False])

        with patch.object(RepositoriesPanel, "import_example_catalog", autospec=True) as example:
            QTimer.singleShot(50, lambda: close_dialog([]))
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
        self.assertEqual(self.window.findChild(QLabel, "pageTitle").text(), "Library")
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
            timer.assert_called_once_with(0, window.return_value.refresh_releases)

    def test_close_hides_to_tray_and_activation_restores_window(self) -> None:
        with patch("appglider.window.QSystemTrayIcon.isSystemTrayAvailable", return_value=True):
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

    def test_no_tray_host_keeps_normal_close_behaviour(self) -> None:
        with patch("appglider.window.QSystemTrayIcon.isSystemTrayAvailable", return_value=False):
            self.window.tray.show()
            event = QCloseEvent()
            self.window.closeEvent(event)
            self.assertTrue(event.isAccepted())
            self.assertFalse(self.window.tray.isVisible())

    def test_tray_menu_launches_only_managed_apps_and_shows_update_count(self) -> None:
        self.assertFalse(self.window.tray_apps_menu.isEnabled())
        self.assertFalse(self.window.tray_check_action.isEnabled())
        payload = appimage_bytes()
        with patch("appglider.releases.request", return_value=io.BytesIO(payload)):
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
        with patch("appglider.releases.request", return_value=io.BytesIO(payload)):
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
        with patch("appglider.window.discover_releases", return_value=object()) as discover:
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

        def click_check() -> None:
            dialog = self.qt.activeModalWidget()
            if isinstance(dialog, DetailsDialog):
                dialog.check_button.click()
                dialog.reject()

        with patch("appglider.window.discover_releases", side_effect=discover):
            QTimer.singleShot(0, click_check)
            self.window.open_details(APP.id)
            QTest.qWait(10)
            self.wait_for_task()
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["force"])
        self.assertFalse(calls[0].get("cheap", False))

    def shown(self) -> list[str]:
        return [card.app.id for card in self.window.grid.cards]

    def choose_sort(self, mode: str) -> None:
        self.window.sort_combo.setCurrentIndex(self.window.sort_combo.findData(mode))

    def test_apps_are_sorted_by_name_by_default_and_the_choice_is_remembered(self) -> None:
        names = [app.name.casefold() for app in sorted(APPS, key=lambda a: a.name.casefold())]
        self.assertEqual(
            [self.window.cards[app_id].app.name.casefold() for app_id in self.shown()], names
        )
        self.assertEqual(
            [self.window.sort_combo.itemText(i) for i in range(self.window.sort_combo.count())],
            [
                "Sort: Name (A–Z)",
                "Sort: Installed first",
                "Sort: Updates first",
                "Sort: Order added",
            ],
        )
        self.choose_sort("added")
        self.assertEqual(self.shown(), [app.id for app in APPS])  # the order they were added in
        self.assertEqual(self.settings.value("library/sort"), "added")
        self.settings.sync()
        reopened = QSettings(self.settings.fileName(), QSettings.Format.IniFormat)
        self.assertEqual(sort_mode(reopened), "added")
        for garbage in ("sideways", "", "Name"):
            reopened.setValue("library/sort", garbage)
            self.assertEqual(sort_mode(reopened), "name")

    def test_installed_and_updates_first_orders(self) -> None:
        window = self.window
        payload = appimage_bytes()
        other = APPS[3]  # LightCraft, not at the front in any order
        for app in (APP, other):
            with patch("appglider.releases.request", return_value=io.BytesIO(payload)):
                window.library.install(
                    app,
                    replace(release_for("1.0.0", payload), name=f"{app.id}-1.0.0.AppImage"),
                    Event(),
                    lambda *args: None,
                )
        window.releases[other.id] = Release("1.1.0", "x", "x", 1, "0" * 64)
        window.scan_installed()
        self.choose_sort("installed")
        installed = self.shown()[:2]
        self.assertEqual(sorted(installed), sorted([APP.id, other.id]))
        self.assertEqual(
            installed, sorted(installed, key=lambda i: window.cards[i].app.name.casefold())
        )
        self.choose_sort("updates")
        self.assertEqual(self.shown()[:2], [other.id, APP.id])  # the update, then the rest

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
        for mode in ("name", "installed", "updates", "added"):
            with self.subTest(mode=mode):
                self.choose_sort(mode)
                self.assertEqual(self.shown()[0], target.id)
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
            with patch("appglider.releases.request", return_value=io.BytesIO(payload)):
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

    def waiting_update(self, version: str = "1.1.0") -> None:
        window = self.window
        if APP.id not in window.installed:
            payload = appimage_bytes()
            with patch("appglider.releases.request", return_value=io.BytesIO(payload)):
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
            patch("appglider.window.QSystemTrayIcon.isSystemTrayAvailable", return_value=True)
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
        self.assertTrue(card.update_button.isHidden())
        self.assertTrue(window.update_all_button.isHidden())
        self.assertEqual(card.installed_version.caption.text(), "Held")
        self.assertEqual(card.checked.text(), "Held · v1.1.0 available")
        self.assertEqual(card.hold_action.text(), "Release hold")
        with patch("appglider.window.discover_releases") as discover:
            window.update_all()
            self.assertIsNone(window.task)
            window.notify_updates()
            shown.assert_not_called()
            card.hold_action.trigger()
            discover.assert_not_called()
        card = window.cards[APP.id]
        self.assertEqual(window.updates_count.text(), "1")
        self.assertFalse(card.update_button.isHidden())
        self.assertEqual(card.update_button.text(), "Update")
        self.assertEqual(card.hold_action.text(), "Hold at current version")

    def test_hold_is_disabled_while_busy_and_details_show_it(self) -> None:
        self.waiting_update()
        card = self.window.cards[APP.id]
        installed = self.window.installed[APP.id]
        card.set_state(installed, self.window.releases[APP.id], True)
        self.assertFalse(card.hold_action.isEnabled())
        app = replace(APP, hold_updates=True)
        release = self.window.releases[APP.id]
        dialog = DetailsDialog(app, self.window, installed, release)
        self.addCleanup(dialog.deleteLater)
        self.assertTrue(dialog.hold_label.isVisibleTo(dialog))
        self.assertEqual(dialog.hold_label.text(), "Held at v1.0.0 · v1.1.0 available")
        plain = DetailsDialog(APP, self.window, installed, release)
        self.addCleanup(plain.deleteLater)
        self.assertFalse(plain.hold_label.isVisibleTo(plain))

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
        with patch("appglider.window.QSystemTrayIcon.isSystemTrayAvailable", return_value=False):
            window.notify_updates()
        shown.assert_not_called()

    def test_several_updates_are_combined_into_one_message(self) -> None:
        window = self.window
        shown = self.notifier()
        self.settings.setValue("notifications/updates", True)
        apps = APPS[:5]
        payload = appimage_bytes()
        for app in apps:
            with patch("appglider.releases.request", return_value=io.BytesIO(payload)):
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

    def test_notification_setting_defaults_off_and_saves(self) -> None:
        seen: list[bool] = []

        def choose() -> None:
            dialog = self.qt.activeModalWidget()
            if isinstance(dialog, SettingsDialog):
                seen.append(dialog.notify_updates.isChecked())
                dialog.notify_updates.setChecked(True)
                dialog.accept()

        QTimer.singleShot(0, choose)
        self.window.open_settings()
        self.assertEqual(seen, [False])
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
        seen: list[tuple[list[str], int]] = []

        def choose(hours: int) -> None:
            dialog = self.qt.activeModalWidget()
            if isinstance(dialog, SettingsDialog):
                seen.append(
                    (
                        [
                            dialog.check_interval.itemText(i)
                            for i in range(dialog.check_interval.count())
                        ],
                        dialog.check_interval.currentData(),
                    )
                )
                dialog.check_interval.setCurrentIndex(dialog.check_interval.findData(hours))
                dialog.accept()

        QTimer.singleShot(0, lambda: choose(12))
        self.window.open_settings()
        self.assertEqual(
            seen[0],
            (
                ["Every hour", "Every 3 hours", "Every 6 hours", "Every 12 hours", "Every 24 hours",
                 "Only when I choose"],
                6,
            ),
        )  # fmt: skip
        self.assertEqual(self.settings.value("General/check-interval-hours", type=int), 12)
        self.assertEqual(self.window.update_timer.interval(), 12 * 3600 * 1000)
        QTimer.singleShot(0, lambda: choose(0))
        self.window.open_settings()
        self.assertFalse(self.window.update_timer.isActive())
        self.assertEqual(seen[1][1], 12)
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
            with patch("appglider.releases.request", return_value=io.BytesIO(payload)):
                window.library.install(
                    APP, release_for("1.0.0", payload), Event(), lambda *args: None
                )
            window.scan_installed()
            window.scheduled_check()
            refresh.assert_called_once_with()
            refresh.reset_mock()
            with patch("appglider.window.github_status.quota_low", return_value=True):
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
                "appglider.window.QSystemTrayIcon.isSystemTrayAvailable",
                return_value=True,
            ),
            patch("appglider.window.QThreadPool.globalInstance"),
            patch("appglider.window.QMessageBox.question") as question,
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
                self.assertTrue(self.window.cards[app.id].preferences_action.isEnabled())
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

    def test_sidebar_status_block_opens_github_settings(self) -> None:
        observed = []

        def inspect() -> None:
            dialog = self.qt.activeModalWidget()
            if isinstance(dialog, SettingsDialog):
                observed.append(dialog.sections.currentItem().text())
                dialog.reject()

        self.assertIn(self.window.status_label.text().split()[0], ("Not", "Automatic", "Checked"))
        QTimer.singleShot(0, inspect)
        QTest.mouseClick(self.window.status_block, Qt.MouseButton.LeftButton)
        self.assertEqual(observed, ["GitHub"])
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
        self.assertIn("v0.16.1", card.status.text())
        self.assertIn("v0.16.2", card.available_version.text.text())
        self.assertEqual(card.action_button.text(), "Launch")
        self.window.show_view("updates")
        self.assertEqual(self.visible_ids(), {"solidcraft"})
        self.assertIn("v0.16.1", card.status.text())
        self.assertIn("v0.16.2", card.available_version.text.text())

        self.window.search.setText("photocraft")
        self.assertFalse(self.visible_ids())
        self.assertEqual(self.window.library_count.text(), "14")
        self.assertEqual(self.window.filter_installed.text(), "Installed (1)")
        self.assertEqual(self.window.updates_count.text(), "1")
        self.window.installed["solidcraft"] = Installed(path, "0.16.2", True)
        self.window.refresh()
        self.assertEqual(self.window.updates_count.text(), "0")

    def test_grid_reflows_and_preserves_cards_when_filtering(self) -> None:
        self.window.resize(1400, 800)
        self.qt.processEvents()
        wide_columns = self.window.grid.columns
        first_card = self.window.cards["photocraft"]
        self.window.resize(860, 600)
        self.qt.processEvents()
        self.assertLess(self.window.grid.columns, wide_columns)
        self.assertEqual(self.window.grid.layout_grid.count(), 14)
        viewport = self.window.scroll_area.viewport()
        self.assertLessEqual(self.window.grid.width(), viewport.width())
        cards = self.window.grid.cards
        for index, card in enumerate(cards):
            self.assertGreaterEqual(card.x(), 0)
            self.assertLessEqual(card.geometry().right(), self.window.grid.width())
            for other in cards[index + 1 :]:
                self.assertFalse(card.geometry().intersects(other.geometry()))
        self.window.search.setText("PhotoCraft")
        self.assertIs(self.window.grid.cards[0], first_card)

    def test_compact_cards_keep_versions_and_actions_inline_at_narrow_widths(self) -> None:
        info = self.release_info()
        for width in (760, 1120):
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
                card = self.window.cards[APP.id]
                offered = info.stable.release if installed else info.preview.release
                card.set_state(installed, offered, False, info=info)
                self.qt.processEvents()
                version = card.installed_version if installed else card.available_version
                label = version.caption if installed else version.text
                caption = label.mapTo(card, label.rect().center())
                button = card.action_button.geometry()
                self.assertLessEqual(button.top(), caption.y())
                self.assertGreaterEqual(button.bottom(), caption.y())
                text = version.text.mapTo(card, version.text.rect().topRight())
                badge = version.badge.mapTo(card, version.badge.rect().bottomRight())
                self.assertLess(text.x(), button.left())
                if version.badge.isVisibleTo(card):
                    self.assertLess(badge.x(), button.left())
                self.assertLess(card.height(), 280)
                self.assertEqual(card.action_button.text(), "Launch" if installed else "Install")
            self.window.refresh()
            self.qt.processEvents()
            self.assertLess(max(card.height() for card in self.window.grid.cards), 280)

    def test_clicking_name_opens_real_details_dialog(self) -> None:
        observed = []
        info = self.release_info()
        self.window.release_infos["solidcraft"] = info
        self.window.apply_release_info(self.window.cards["solidcraft"].app)

        def close_details() -> None:
            dialog = self.qt.activeModalWidget()
            if isinstance(dialog, DetailsDialog):
                observed.append(dialog.windowTitle())
                observed.extend(label.text() for label in dialog.findChildren(QLabel))
                dialog.reject()

        QTimer.singleShot(0, close_details)
        name = self.window.cards["solidcraft"].findChild(QPushButton, "appName")
        self.assertIsNotNone(name)
        QTest.mouseClick(name, Qt.MouseButton.LeftButton)
        self.assertIn("About SolidCraft", observed)
        self.assertIn("tangency-labs/solidcraft", observed)
        self.assertIn(info.stable.version, observed)
        self.assertIn(info.preview.version, observed)

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
                "appglider.window.QTimer.singleShot",
                side_effect=lambda delay, *args: single_shot(0, *args),
            ),
            patch("appglider.window.QMessageBox.warning") as warning,
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

    def test_settings_save_cancel_and_theme_persistence(self) -> None:
        folder = str(Path(self.temp.name) / "future-apps")

        def save_settings() -> None:
            dialog = self.qt.activeModalWidget()
            if isinstance(dialog, SettingsDialog):
                dialog.theme.setCurrentText("Dark")
                dialog.folder.setText(folder)
                dialog.accept()

        QTimer.singleShot(0, save_settings)
        self.window.open_settings()
        self.assertEqual(self.settings.value("appearance/theme"), "Dark")
        self.assertEqual(self.settings.value("installation/folder"), folder)
        self.assertFalse(Path(folder).exists())
        self.assertEqual(self.qt.palette().color(QPalette.ColorRole.Window).name(), "#15181e")
        self.settings.sync()
        reopened = QSettings(self.settings.fileName(), QSettings.Format.IniFormat)
        self.assertEqual(reopened.value("appearance/theme"), "Dark")

        def cancel_settings() -> None:
            dialog = self.qt.activeModalWidget()
            if isinstance(dialog, SettingsDialog):
                dialog.theme.setCurrentText("Light")
                self.assertEqual(
                    self.qt.palette().color(QPalette.ColorRole.Window).name(), "#eef0f4"
                )
                self.assertEqual(self.settings.value("appearance/theme"), "Dark")
                dialog.reject()

        QTimer.singleShot(0, cancel_settings)
        self.window.open_settings()
        self.assertEqual(self.settings.value("appearance/theme"), "Dark")
        self.assertEqual(self.qt.palette().color(QPalette.ColorRole.Window).name(), "#15181e")

    def test_appearance_choices_switch_and_persist(self) -> None:
        def window_colour() -> str:
            return self.qt.palette().color(QPalette.ColorRole.Window).name()

        def choose(theme: str) -> None:
            def change() -> None:
                dialog = self.qt.activeModalWidget()
                if isinstance(dialog, SettingsDialog):
                    self.assertEqual(
                        [dialog.theme.itemText(i) for i in range(dialog.theme.count())],
                        ["System", "Light", "Dark"],
                    )
                    dialog.theme.setCurrentText(theme)
                    dialog.accept()

            QTimer.singleShot(0, change)
            self.window.open_settings()

        self.settings.remove("appearance/theme")

        def default_choice() -> None:
            dialog = self.qt.activeModalWidget()
            if isinstance(dialog, SettingsDialog):
                self.assertEqual(dialog.theme.currentText(), "System")
                dialog.reject()

        QTimer.singleShot(0, default_choice)
        self.window.open_settings()
        for theme, colour in (
            ("Light", "#eef0f4"),
            ("Dark", "#15181e"),
        ):
            choose(theme)
            self.assertEqual(self.settings.value("appearance/theme"), theme)
            self.assertEqual(window_colour(), colour)

    def test_retired_themes_and_unknown_values_are_migrated_on_read(self) -> None:
        for stored, expected in (
            ("Forest", ("Dark", "Green")),
            ("Earthy", ("Dark", "Clay")),
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

    def test_retired_theme_applies_and_saves_migrated_values(self) -> None:
        self.settings.setValue("appearance/theme", "Forest")
        self.window.apply_theme()
        palette = self.qt.palette()
        self.assertEqual(palette.color(QPalette.ColorRole.Window).name(), "#15181e")
        self.assertEqual(palette.color(QPalette.ColorRole.Highlight).name(), "#6fcf97")

        def save() -> None:
            dialog = self.qt.activeModalWidget()
            if isinstance(dialog, SettingsDialog):
                self.assertEqual(dialog.theme.currentText(), "Dark")
                self.assertEqual(dialog.accent.currentText(), "Green")
                dialog.accept()

        QTimer.singleShot(0, save)
        self.window.open_settings()
        self.assertEqual(self.settings.value("appearance/theme"), "Dark")
        self.assertEqual(self.settings.value("appearance/accent"), "Green")

    def test_accent_previews_reverts_and_persists(self) -> None:
        def highlight() -> str:
            return self.qt.palette().color(QPalette.ColorRole.Highlight).name()

        self.settings.setValue("appearance/theme", "Light")
        self.window.apply_theme()
        self.assertEqual(highlight(), "#3563d8")

        def cancel() -> None:
            dialog = self.qt.activeModalWidget()
            if isinstance(dialog, SettingsDialog):
                self.assertEqual(dialog.accent.accessibleName(), "Accent colour")
                self.assertEqual(
                    [dialog.accent.itemText(i) for i in range(dialog.accent.count())],
                    ["Blue", "Green", "Amber", "Clay"],
                )
                dialog.accent.setCurrentText("Clay")
                self.assertEqual(highlight(), "#9c5434")
                dialog.reject()

        QTimer.singleShot(0, cancel)
        self.window.open_settings()
        self.assertEqual(highlight(), "#3563d8")
        self.assertIsNone(self.settings.value("appearance/accent"))

        def save() -> None:
            dialog = self.qt.activeModalWidget()
            if isinstance(dialog, SettingsDialog):
                dialog.theme.setCurrentText("Dark")
                dialog.accent.setCurrentText("Amber")
                dialog.accept()

        QTimer.singleShot(0, save)
        self.window.open_settings()
        self.assertEqual(self.settings.value("appearance/accent"), "Amber")
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
                dialog = SettingsDialog(self.settings, self.window, self.window.library)
                self.addCleanup(dialog.close)
                dialog.folder.setText(self.temp.name)
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
        with patch("appglider.window.discover_releases") as discover:
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
            self.assertEqual(card.available_version.text.text(), "Available")
            self.assertTrue(card.available_version.badge.isHidden())
            self.assertTrue(card.checked.isHidden())
            self.assertTrue(card.action_button.isEnabled())

    def test_details_explicitly_checks_an_uninstalled_app_without_downloading(self) -> None:
        info = self.release_info()
        observed = []

        def inspect() -> None:
            dialog = self.qt.activeModalWidget()
            if not isinstance(dialog, DetailsDialog):
                return
            try:
                observed.append(dialog.available_label.text.text())
                dialog.check_button.click()
                self.wait_for_task()
                observed.append(dialog.available_label.text.text())
                observed.append(dialog.preview_label.text.text())
                observed.append(dialog.check_button.isEnabled())
            finally:
                dialog.reject()

        with (
            patch("appglider.window.discover_releases", return_value=info) as discover,
            patch("appglider.library.download") as download,
        ):
            QTimer.singleShot(0, inspect)
            self.window.open_details(APP.id)
            discover.assert_called_once()
            self.assertEqual(discover.call_args.args[0].id, APP.id)
            self.assertTrue(discover.call_args.kwargs["force"])
            download.assert_not_called()
        self.assertEqual(observed, ["Not checked", info.stable.version, info.preview.version, True])
        self.assertEqual(self.window.installed, {})
        self.assertEqual(self.window.cards[APP.id].available_version.text.text(), "Available")

    def test_details_can_close_while_its_release_check_finishes(self) -> None:
        info = self.release_info()
        finish = Event()

        def discover(app, cancel, cache_dir, *, force):
            if not finish.wait(5):
                raise TimeoutError("Test release check was not released")
            return info

        def close_details() -> None:
            dialog = self.qt.activeModalWidget()
            if isinstance(dialog, DetailsDialog):
                dialog.check_button.click()
                dialog.reject()

        with patch("appglider.window.discover_releases", side_effect=discover):
            try:
                QTimer.singleShot(0, close_details)
                self.window.open_details(APP.id)
                QTest.qWait(10)
            finally:
                finish.set()
            self.wait_for_task()
        self.assertEqual(self.window.release_infos[APP.id], info)

    def test_failed_details_check_keeps_cached_versions_and_allows_retry(self) -> None:
        info = self.release_info()
        atomic_json(info_path(APP, self.window.library.data_dir / "release-cache"), asdict(info))
        self.window.scan_installed()
        observed = []

        def inspect() -> None:
            dialog = self.qt.activeModalWidget()
            if not isinstance(dialog, DetailsDialog):
                return
            try:
                dialog.check_button.click()
                self.wait_for_task()
                observed.extend(
                    [
                        dialog.available_label.text.text(),
                        dialog.note.text(),
                        dialog.check_button.isEnabled(),
                    ]
                )
            finally:
                dialog.reject()

        with (
            patch("appglider.window.discover_releases", side_effect=OSError("Offline")),
            patch("appglider.window.QMessageBox.warning"),
            patch("appglider.window.QThreadPool.globalInstance") as pool,
        ):
            pool.return_value.start.side_effect = lambda task: task.run()
            QTimer.singleShot(0, inspect)
            self.window.open_details(APP.id)
        self.assertEqual(observed, [info.stable.version, "Offline", True])
        self.assertEqual(self.window.release_infos[APP.id], info)

    def test_queued_background_check_skips_an_app_that_was_uninstalled(self) -> None:
        other = APPS[1]
        record = Installed(Path(self.temp.name) / "example.AppImage", "0.9.0", True)
        self.window.installed = {APP.id: record, other.id: record}
        with patch("appglider.window.QThreadPool.globalInstance") as pool:
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
                patch("appglider.window.QThreadPool.globalInstance") as pool,
                patch("appglider.window.github_status.quota_plentiful", return_value=plentiful),
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
        with patch("appglider.releases.request", return_value=io.BytesIO(payload)):
            self.window.library.install(
                APP, release_for("0.9.0", payload), Event(), lambda *args: None
            )
        with patch("appglider.window.discover_releases", return_value=info) as discover:
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
        self.assertEqual(card.available_version.badge.text(), "Latest")
        self.assertNotIn(
            info.preview.version,
            " ".join(
                label.text() for label in card.findChildren(QLabel) if label.isVisibleTo(card)
            ),
        )
        self.assertEqual(card.action_button.text(), "Launch")
        self.assertEqual(self.window.updates_count.text(), "1")
        self.change_release_preference(1)
        self.assertEqual(self.window.cards[APP.id].available_version.badge.text(), "Pre-release")
        self.assertEqual(self.window.check_button.toolTip(), "Check for updates")

    def change_release_preference(self, index: int) -> None:
        selected = []

        def choose() -> None:
            dialog = self.qt.activeModalWidget()
            if isinstance(dialog, SettingsDialog):
                try:
                    panel = dialog.repositories
                    selected.append(panel.selected_app.id)
                    panel.release_channel.setCurrentIndex(index)
                finally:
                    dialog.reject()

        QTimer.singleShot(0, choose)
        self.window.cards[APP.id].preferences_action.trigger()
        self.assertEqual(selected, [APP.id])

    def test_card_preference_changes_offers_and_count_without_installing(self) -> None:
        info = self.release_info()
        payload = appimage_bytes()
        with patch("appglider.releases.request", return_value=io.BytesIO(payload)):
            self.window.library.install(APP, info.stable.release, Event(), lambda *args: None)
        atomic_json(info_path(APP, self.window.library.data_dir / "release-cache"), asdict(info))
        self.window.scan_installed()
        self.assertEqual(self.window.updates_count.text(), "0")
        with patch.object(self.window.library, "install") as install:
            self.change_release_preference(1)
            install.assert_not_called()
        card = self.window.cards[APP.id]
        self.assertEqual(self.window.updates_count.text(), "1")
        self.assertEqual(card.available_version.badge.text(), "Pre-release")
        self.assertTrue(card.installed_version.badge.isHidden())
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
        self.assertTrue(card.available_version.isHidden())
        self.assertEqual(card.installed_version.badge.text(), "Latest")

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
        with patch("appglider.releases.request", return_value=io.BytesIO(payload)):
            self.window.library.install(
                APP, release_for("0.9.0", payload), Event(), lambda *args: None
            )
        atomic_json(info_path(APP, self.window.library.data_dir / "release-cache"), asdict(info))
        self.window.scan_installed()
        self.assertIn("refresh needed", self.window.cards[APP.id].checked.text())
        with (
            patch("appglider.window.discover_releases", side_effect=OSError("Offline")),
            patch("appglider.window.QMessageBox.warning") as warning,
        ):
            self.window.refresh_releases()
            self.wait_for_release_checks()
            warning.assert_not_called()
        card = self.window.cards[APP.id]
        self.assertEqual(card.available_version.badge.text(), "Latest")
        self.assertEqual(self.window.release_infos[APP.id].preview, info.preview)
        self.assertIn("Refresh failed · checked", card.checked.text())
        self.assertIn("Offline", card.checked.toolTip())

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
        self.assertEqual(card.available_version.text.text(), "Available")
        self.assertEqual(self.window.updates_count.text(), "0")
        payload = appimage_bytes()
        release = replace(release_for("1.1.0-beta.1", payload), prerelease=True, is_latest=False)
        with (
            patch("appglider.window.latest_release", return_value=release) as latest,
            patch("appglider.releases.request", return_value=io.BytesIO(payload)),
        ):
            card.action_button.click()
            self.wait_for_task()
        latest.assert_called_once()
        self.assertTrue(latest.call_args.args[0].include_prereleases)
        self.assertEqual(self.window.installed[APP.id].version, release.version)
        self.assertTrue(self.window.installed[APP.id].prerelease)

    def test_closing_cancels_background_refresh_without_installation_prompt(self) -> None:
        payload = appimage_bytes()
        with patch("appglider.releases.request", return_value=io.BytesIO(payload)):
            self.window.library.install(
                APP, release_for("1.0.0", payload), Event(), lambda *args: None
            )
        with (
            patch("appglider.window.QThreadPool.globalInstance"),
            patch("appglider.window.QMessageBox.question") as question,
            patch("appglider.window.QSystemTrayIcon.isSystemTrayAvailable", return_value=False),
        ):
            self.window.refresh_releases()
            task = self.window.checks.tasks[APP.id]
            self.assertTrue(self.window.cards[APP.id].preferences_action.isEnabled())
            self.assertFalse(self.window.close())
            self.assertTrue(task.cancel.is_set())
            self.assertFalse(self.window.checks.queue)
            task.signals.finished.emit(TaskResult("Cancelled"))
            question.assert_not_called()
            self.assertFalse(self.window.checks.busy)
            self.assertFalse(self.window.isVisible())

    def test_managed_appimage_gets_launch_action_and_installed_filter(self) -> None:
        payload = appimage_bytes()
        with patch("appglider.releases.request", return_value=io.BytesIO(payload)):
            self.window.library.install(
                APP, release_for("1.0.0", payload), Event(), lambda *args: None
            )
        self.window.scan_installed()
        path = self.window.installed[APP.id].path
        card = self.window.cards[APP.id]
        self.assertEqual(card.action_button.text(), "Launch")
        self.window.filter_installed.click()
        self.assertEqual(self.visible_ids(), {APP.id})
        with patch("appglider.library.subprocess.Popen") as spawn:
            spawn.return_value.poll.return_value = None
            QTest.mouseClick(card.action_button, Qt.MouseButton.LeftButton)
            self.assertEqual(spawn.call_args.args[0], [str(path)])
        self.assertTrue(card.remove_action.isEnabled())
        self.assertTrue(card.shortcut_action.isEnabled())
        shortcut = self.window.library.desktop_dir / f"appglider-{APP.id}.desktop"
        shortcut.unlink()
        with patch("appglider.window.QMessageBox.warning") as warning:
            card.shortcut_requested.emit(APP.id)
            warning.assert_not_called()
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
        with patch("appglider.window.QMessageBox.warning") as warning:
            for version in ("1.0.0", "1.1.0"):
                payload = appimage_bytes(version.encode())
                release = release_for(version, payload)
                self.window.releases[APP.id] = release
                self.window.refresh()
                with (
                    patch("appglider.window.latest_release", return_value=release),
                    patch("appglider.releases.request", return_value=io.BytesIO(payload)),
                ):
                    button = card.update_button if version == "1.1.0" else card.action_button
                    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                    self.wait_for_task()
                self.assertEqual(self.window.installed[APP.id].version, version)
                self.assertEqual(card.action_button.text(), "Launch")
                self.assertTrue(self.window.installed[APP.id].managed)
            self.assertTrue(card.rollback_action.isEnabled())
            card.rollback_requested.emit(APP.id)
            self.wait_for_task()
            self.assertEqual(self.window.installed[APP.id].version, "1.0.0")
            self.assertTrue(card.update_button.isVisible())
            warning.assert_not_called()

    def test_update_all_counts_only_apps_that_were_really_updated(self) -> None:
        card = self.window.cards[APP.id]
        first_payload = appimage_bytes(b"1.0.0")
        first = release_for("1.0.0", first_payload)
        with patch("appglider.releases.request", return_value=io.BytesIO(first_payload)):
            self.window.library.install(APP, first, Event(), lambda *args: None)
        self.window.scan_installed()
        newer_payload = appimage_bytes(b"1.1.0")
        newer = release_for("1.1.0", newer_payload)
        with patch("appglider.window.QMessageBox.warning") as warning:
            for latest, expected, version in ((first, "Updated 0 apps.", "1.0.0"),):
                self.window.releases[APP.id] = newer
                self.window.refresh()
                with patch("appglider.window.latest_release", return_value=latest):
                    self.window.update_all()
                    self.wait_for_task()
                self.assertEqual(self.window.footer.text(), expected)
                self.assertEqual(self.window.installed[APP.id].version, version)
            self.window.releases[APP.id] = newer
            self.window.refresh()
            with (
                patch("appglider.window.latest_release", return_value=newer),
                patch("appglider.releases.request", return_value=io.BytesIO(newer_payload)),
            ):
                self.window.update_all()
                self.wait_for_task()
            self.assertEqual(self.window.footer.text(), "Updated 1 apps.")
            self.assertEqual(self.window.installed[APP.id].version, "1.1.0")
            self.assertTrue(card.rollback_action.isEnabled())
            warning.assert_not_called()

    def test_busy_state_cancellation_and_launch_stays_available(self) -> None:
        payload = appimage_bytes()
        with patch("appglider.releases.request", return_value=io.BytesIO(payload)):
            self.window.library.install(
                APP, release_for("1.0.0", payload), Event(), lambda *args: None
            )
        self.window.scan_installed()
        with patch("appglider.window.QThreadPool.globalInstance"):
            self.window.start_task("Test operation", lambda cancel, progress: TaskResult("Done"))
        task = self.window.task
        self.assertIsNotNone(task)
        self.assertTrue(self.window.cards[APP.id].action_button.isEnabled())
        self.assertFalse(self.window.cards["solidcraft"].action_button.isEnabled())
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

    def test_settings_reject_invalid_destination(self) -> None:
        dialog = SettingsDialog(self.settings, self.window, self.window.library)
        self.addCleanup(dialog.close)
        for path in ("", "relative/path", "/bad\npath", self.settings.fileName()):
            self.settings.sync()
            dialog.folder.setText(path)
            with (
                self.subTest(path=path),
                patch("appglider.window.QMessageBox.warning") as warning,
            ):
                dialog.accept()
                warning.assert_called_once()
                self.assertEqual(dialog.result(), 0)

    def test_footer_version_opens_about_and_settings_sections_are_accessible(self) -> None:
        observations = []

        def inspect_about() -> None:
            dialog = self.qt.activeModalWidget()
            if isinstance(dialog, SettingsDialog):
                observations.append(dialog.sections.currentItem().text())
                for section in range(4):
                    dialog.sections.setCurrentRow(section)
                    self.assertEqual(dialog.pages.currentIndex(), section)
                dialog.reject()

        self.assertEqual(self.window.version_button.text(), f"v{APP_VERSION}")
        self.assertEqual(
            self.window.version_button.cursor().shape(), Qt.CursorShape.PointingHandCursor
        )
        self.assertEqual(self.window.version_button.toolTip(), "About AppGlider")
        self.assertEqual(self.window.findChild(QWidget, "sidebar").width(), 210)
        self.assertTrue(
            self.window.nav_settings.geometry().bottom() < self.window.version_button.y()
        )
        QTimer.singleShot(0, inspect_about)
        QTest.mouseClick(self.window.version_button, Qt.MouseButton.LeftButton)
        self.assertEqual(observations, ["About"])

    def test_github_indicator_opens_live_status_without_network_requests(self) -> None:
        observed = []

        def inspect_status() -> None:
            dialog = self.qt.activeModalWidget()
            if not isinstance(dialog, SettingsDialog):
                return
            try:
                observed.append(dialog.sections.currentItem().text())
                observed.append(dialog.github_details.text())
                github_status.observe_headers(
                    {
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Limit": "60",
                        "X-RateLimit-Reset": "1600",
                    }
                )
                github_status.release_result("Public GitHub pages", APP.repo, 990)
                releases._RETRY_AFTER["github.com"] = 1120
                dialog.github_timer.timeout.emit()
                self.window.github_timer.timeout.emit()
                self.qt.processEvents()
                observed.append(dialog.github_details.text())
                self.assertEqual(self.window.github_label.text(), "GitHub: limited")
                self.assertTrue(self.window.status_block.property("limited"))
                label = dialog.github_details
                self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
            finally:
                dialog.reject()
                self.assertFalse(dialog.github_timer.isActive())

        with (
            patch.object(github_status, "_status", github_status.GitHubStatus()),
            patch.object(github_status.time, "time", return_value=1000),
            patch.dict(releases._RETRY_AFTER, {}, clear=True),
            patch.object(releases, "request") as request,
        ):
            self.window.update_github_status()
            self.assertEqual(self.window.github_label.text(), "GitHub status")
            self.assertEqual(
                self.window.status_block.cursor().shape(), Qt.CursorShape.PointingHandCursor
            )
            QTimer.singleShot(0, inspect_status)
            QTest.mouseClick(self.window.status_block, Qt.MouseButton.LeftButton)
            request.assert_not_called()
        self.assertEqual(observed[0], "GitHub")
        self.assertIn("unknown", observed[1])
        self.assertIn("0 of 60", observed[2])
        self.assertIn("retry in 10 min", observed[2])

    def test_release_badges_describe_the_version_and_beta_waits_for_stable(self) -> None:
        card = self.window.cards["solidcraft"]
        beta = Installed(
            Path(self.temp.name) / "beta.AppImage", "1.2.0-beta.1", True, prerelease=True
        )
        stable = Release("1.2.0", "solid.AppImage", "unused", 1, "0" * 64)
        card.set_state(beta, stable, False)
        self.assertTrue(card.installed_version.badge.isHidden())
        self.assertEqual(card.available_version.badge.text(), "Latest")
        self.assertTrue(card.update_button.isVisible())
        previous = Release("1.1.0", "solid.AppImage", "unused", 1, "0" * 64)
        card.set_state(beta, previous, False)
        self.assertFalse(card.update_button.isVisible())
        self.assertTrue(card.available_version.isHidden())
        self.assertEqual(card.installed_version.badge.text(), "Pre-release")
        self.assertIn("Waiting for a newer stable", card.checked.text())
        self.assertEqual(card.action_button.text(), "Launch")
        current = Installed(beta.path, "1.1.0", True)
        card.set_state(current, previous, False)
        self.assertEqual(card.installed_version.badge.text(), "Latest")
        card.set_state(current, stable, False)
        self.assertEqual(card.installed_version.badge.text(), "")
        self.assertEqual(card.available_version.badge.text(), "Latest")

    def test_preview_update_card_only_shows_the_update_and_installed_version(self) -> None:
        info = self.release_info()
        installed = Installed(
            Path(self.temp.name) / "cura.AppImage", "1.1.0-beta.0", True, prerelease=True
        )
        card = self.window.cards[APP.id]
        card.set_state(installed, info.preview.release, False, info=info)
        self.assertEqual(card.available_version.text.text(), f"v{info.preview.version}")
        self.assertEqual(card.installed_version.text.text(), f"v{installed.version}")
        self.assertEqual(card.available_version.badge.text(), "Pre-release")
        self.assertTrue(card.installed_version.badge.isHidden())
        self.assertEqual(card.action_button.text(), "Launch")
        self.assertTrue(card.update_button.isVisible())
        self.assertNotIn(
            f"v{info.stable.version}",
            " ".join(
                label.text() for label in card.findChildren(QLabel) if label.isVisibleTo(card)
            ),
        )
        self.assertEqual(
            sum(badge.isVisibleTo(card) for badge in card.findChildren(QLabel, "releaseBadge")), 1
        )

        card.set_state(installed, None, False, info=info)
        self.assertTrue(card.available_version.isHidden())
        self.assertEqual(card.installed_version.badge.text(), "Pre-release")

    def test_preview_only_installation_waits_after_disabling_previews(self) -> None:
        info = self.release_info()
        info = replace(info, stable=None)
        installed = Installed(
            Path(self.temp.name) / "preview.AppImage", info.preview.version, True, prerelease=True
        )
        card = self.window.cards[APP.id]
        card.set_state(installed, None, False, info=info)
        self.assertIn("Waiting for a newer stable", card.checked.text())
        self.assertEqual(card.installed_version.badge.text(), "Pre-release")
        self.assertFalse(card.update_button.isVisible())
        self.assertEqual(card.action_button.text(), "Launch")

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
        def save() -> None:
            dialog = self.qt.activeModalWidget()
            if isinstance(dialog, SettingsDialog):
                dialog.close_to_tray.setChecked(False)
                dialog.accept()

        QTimer.singleShot(0, save)
        self.window.open_settings()
        self.assertFalse(self.settings.value("General/close-to-tray", True, type=bool))
        with patch("appglider.window.QSystemTrayIcon.isSystemTrayAvailable", return_value=True):
            self.window.tray.show()
            event = QCloseEvent()
            self.window.closeEvent(event)
            self.assertTrue(event.isAccepted())


if __name__ == "__main__":
    unittest.main()
