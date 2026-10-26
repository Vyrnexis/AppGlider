"""The App page, its controls and the release notes it shows."""

import io
import os
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from catalog_fixture import APPS, stock
from gui_helpers import close_window
from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QBoxLayout, QMessageBox
from test_builds import BAMBU, bambu_metadata, chosen_bambu
from test_library import APP, appimage_bytes, release_for

from appglider import APP_ICON, releases
from appglider.app_page import (
    NotesBrowser,
    format_date,
    format_size,
    release_page_url,
    version_text,
)
from appglider.controls import Segmented, Switch
from appglider.icons import IconCandidate, normalise_icon
from appglider.library import Library
from appglider.release_info import ReleaseInfo, ReleaseOption, info_path
from appglider.releases import Release
from appglider.window import LauncherWindow

BLUE = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
    b'<path d="M0 0H32V32H0Z" fill="blue"/></svg>'
)
RED = BLUE.replace(b"blue", b"red")


class AppPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setStyle("Fusion")

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.library = stock(Library(root / "data", root / "apps", root / "desktop"))
        self.settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
        self.settings.setValue("appearance/theme", "Light")
        self.window = LauncherWindow(self.settings, self.library)
        self.window.show()
        self.addCleanup(close_window, self.window)
        self.page = self.window.app_page
        self.qt.processEvents()

    def wait_for_task(self) -> None:
        # The window and the page get the result as two separate events; on a slow machine
        # the task can be cleared before the page has seen it, so wait for both.
        deadline = time.monotonic() + 5
        while (self.window.task is not None or self.page.pending) and time.monotonic() < deadline:
            QTest.qWait(10)
            time.sleep(0.001)
        self.assertIsNone(self.window.task, "Background operation did not finish")
        self.assertEqual(self.page.pending, "", "The page did not receive the result")

    def install(self, *versions: str) -> None:
        for version in versions:
            payload = appimage_bytes(version.encode())
            with patch("appglider.releases.request", return_value=io.BytesIO(payload)):
                self.library.install(
                    APP, release_for(version, payload), Event(), lambda *args: None
                )
        self.window.scan_installed()

    def release_info(self) -> ReleaseInfo:
        payload = appimage_bytes()
        stable = release_for("1.0.0", payload)
        preview = replace(release_for("1.1.0-beta.1", payload), prerelease=True, is_latest=False)
        return ReleaseInfo(
            ReleaseOption(stable.version, stable),
            ReleaseOption(preview.version, preview),
            time.time(),
        )

    # Opening, leaving and following the window

    def test_rows_open_the_page_and_back_restores_view_filter_and_search(self) -> None:
        window = self.window
        self.install("1.0.0")
        window.filter_installed.setChecked(True)
        window.search.setText("photo")
        self.qt.processEvents()
        self.assertEqual([row.app.id for row in window.app_list.rows], [APP.id])
        window.cards[APP.id].details_requested.emit(APP.id)
        self.assertIs(window.pages.currentWidget(), self.page)
        self.assertEqual(self.page.app_id, APP.id)
        self.assertEqual(self.page.back_button.text(), "‹ Library")
        self.assertTrue(window.nav_library.isChecked())
        self.page.back_button.click()
        self.assertEqual(window.pages.currentIndex(), 0)
        self.assertEqual(window.view, "library")
        self.assertTrue(window.filter_installed.isChecked())
        self.assertEqual(window.search.text(), "photo")
        self.assertEqual([row.app.id for row in window.app_list.rows], [APP.id])
        self.assertIsNone(self.page.app_id)

    def test_the_back_link_names_the_updates_view_and_keeps_it_selected(self) -> None:
        window = self.window
        self.install("1.0.0")
        window.releases[APP.id] = release_for("1.1.0", appimage_bytes())
        window.scan_installed()
        window.show_view("updates")
        window.open_app(APP.id)
        self.assertEqual(self.page.back_button.text(), "‹ Updates")
        self.assertTrue(window.nav_updates.isChecked())
        self.assertFalse(window.nav_library.isChecked())
        self.page.back_button.click()
        self.assertEqual(window.view, "updates")
        self.assertEqual(window.page_title.text(), "Updates")
        self.assertEqual([row.app.id for row in window.app_list.rows], [APP.id])

    def test_the_scroll_position_of_the_list_comes_back(self) -> None:
        window = self.window
        window.resize(1000, 400)
        self.qt.processEvents()
        bar = window.scroll_area.verticalScrollBar()
        self.assertGreater(bar.maximum(), 100)
        bar.setValue(120)
        window.open_app(APPS[5].id)
        self.page.back_button.click()
        self.qt.processEvents()
        self.assertEqual(bar.value(), 120)

    def test_choosing_a_sidebar_entry_leaves_the_page(self) -> None:
        for entry, view in (
            (self.window.nav_library, "library"),
            (self.window.nav_updates, "updates"),
        ):
            self.window.open_app(APP.id)
            self.assertIs(self.window.pages.currentWidget(), self.page)
            entry.click()
            self.assertEqual(self.window.pages.currentIndex(), 0)
            self.assertEqual(self.window.view, view)

    def test_the_page_follows_window_state_and_closes_when_its_app_is_removed(self) -> None:
        window = self.window
        window.open_app(APP.id)
        self.assertEqual(self.page.chip.text(), "Not installed")
        self.install("1.0.0")
        self.assertEqual(self.page.chip.text(), "Not checked")
        self.assertTrue(self.page.launch_button.isVisibleTo(self.page))
        window.releases[APP.id] = release_for("1.1.0", appimage_bytes())
        window.refresh()
        self.assertEqual(self.page.chip.text(), "↑ 1.1.0")
        self.assertEqual(self.page.action_button.text(), "Update to v1.1.0")
        self.assertEqual(self.page.chip.property("state"), "update")
        self.assertEqual(self.page.channel_chip.text(), "Stable channel")
        window.pinned.add(APP.id)
        window.refresh()
        self.assertTrue(self.page.pin_switch.isChecked())
        self.library.remove(APP)
        window.scan_installed()
        self.assertIs(window.pages.currentWidget(), self.page)
        self.assertEqual(self.page.chip.text(), "Not installed")
        self.library.remove_repository(APP.id)
        window.scan_installed()
        self.assertEqual(window.pages.currentIndex(), 0)
        self.assertIsNone(self.page.app_id)
        self.assertNotIn(APP.id, window.cards)

    def test_the_primary_action_follows_the_same_rules_as_the_row(self) -> None:
        window = self.window
        window.open_app(APP.id)
        self.assertEqual(self.page.action_button.text(), "Install")
        self.assertFalse(self.page.launch_button.isVisibleTo(self.page))
        with patch.object(window, "install_app") as install:
            self.page.action_button.click()
            install.assert_called_once_with(APP.id)
        self.install("1.0.0")
        self.assertTrue(self.page.action_button.isHidden())
        self.assertEqual(self.page.launch_button.objectName(), "primary")
        with patch.object(window, "launch_app") as launch:
            self.page.launch_button.click()
            launch.assert_called_once_with(APP.id)
        window.releases[APP.id] = release_for("1.1.0", appimage_bytes())
        window.refresh()
        self.assertEqual(self.page.launch_button.objectName(), "")
        window.task = object()  # type: ignore[assignment]
        window.refresh()
        self.assertFalse(self.page.action_button.isEnabled())
        self.assertFalse(self.page.channel.isEnabled())
        window.task = None
        window.refresh()
        self.assertTrue(self.page.action_button.isEnabled())

    # Preferences

    def test_release_channel_saves_clears_old_results_and_updates_the_row(self) -> None:
        window = self.window
        window.open_app(APP.id)
        window.releases[APP.id] = release_for("1.0.0", appimage_bytes())
        window.check_errors[APP.id] = "Old failure"
        self.page.channel.buttons[1].click()
        self.assertTrue(window.cards[APP.id].app.include_prereleases)
        self.assertTrue(next(a for a in self.library.apps if a.id == APP.id).include_prereleases)
        self.assertNotIn(APP.id, window.releases)
        self.assertNotIn(APP.id, window.check_errors)
        self.assertEqual(self.page.channel.currentIndex(), 1)
        self.assertEqual(self.page.channel_chip.text(), "Preview channel")
        self.assertIn("Release preference saved", window.footer.text())
        self.page.channel.buttons[0].click()
        self.assertFalse(window.cards[APP.id].app.include_prereleases)
        self.assertEqual(self.page.channel_chip.text(), "Stable channel")

    def test_a_failed_channel_save_keeps_the_old_choice(self) -> None:
        self.window.open_app(APP.id)
        with patch.object(self.library, "set_release_preference", side_effect=OSError("Disk full")):
            self.page.channel.buttons[1].click()
        self.assertEqual(self.page.channel.currentIndex(), 0)
        self.assertEqual(self.window.footer.text(), "Disk full")

    def test_hold_and_pin_switches_use_the_window_actions(self) -> None:
        window = self.window
        window.open_app(APP.id)
        self.assertFalse(self.page.hold_switch.isEnabled())  # not a managed install
        self.install("1.0.0")
        self.assertTrue(self.page.hold_switch.isEnabled())
        self.page.hold_switch.click()
        self.assertTrue(next(a for a in self.library.apps if a.id == APP.id).hold_updates)
        self.assertTrue(self.page.hold_switch.isChecked())
        self.assertEqual(self.page.hold_hint.text(), "Held at v1.0.0")
        self.page.hold_switch.click()
        self.assertFalse(self.page.hold_switch.isChecked())
        self.page.pin_switch.click()
        self.assertIn(APP.id, window.pinned)
        self.assertTrue(window.cards[APP.id].pinned)
        self.page.pin_switch.click()
        self.assertNotIn(APP.id, window.pinned)

    def test_build_choice_saves_through_the_library_and_keeps_caches_apart(self) -> None:
        self.library.add_repository(replace(chosen_bambu(), include_prereleases=True))
        self.window.scan_installed()
        self.window.open_app(BAMBU.id)
        self.assertIn("Ubuntu 24.04", self.page.build_hint.text())
        old = next(app for app in self.library.apps if app.id == BAMBU.id)
        entries = [bambu_metadata(), bambu_metadata("02.09.00.12", preview=True)]
        with (
            patch.object(releases, "api_entries", return_value=entries),
            patch("appglider.library.download") as download,
            patch(
                "appglider.app_page.QInputDialog.getItem",
                side_effect=lambda parent, title, label, items, current, editable: (
                    next(item for item in items if "22.04" in item),
                    True,
                ),
            ) as picker,
        ):
            self.page.build_button.click()
            self.wait_for_task()
            picker.assert_called_once()
            self.assertIn("Multiple builds", picker.call_args.args[2])
            self.assertEqual(picker.call_args.args[4], 1)  # the saved build is preselected
            self.wait_for_task()
            download.assert_not_called()
        new = next(app for app in self.library.apps if app.id == BAMBU.id)
        self.assertIn("ubuntu22.04", new.build_variant)
        self.assertTrue(new.include_prereleases)
        self.assertIn("Ubuntu 22.04", self.page.build_hint.text())
        cache = self.library.data_dir / "release-cache"
        self.assertNotEqual(info_path(old, cache), info_path(new, cache))
        self.assertIn("Build preference saved", self.window.footer.text())
        self.assertEqual(self.library.scan(), {})

    def test_cancelling_the_build_choice_changes_nothing(self) -> None:
        self.library.add_repository(chosen_bambu())
        self.window.scan_installed()
        self.window.open_app(BAMBU.id)
        with (
            patch.object(releases, "api_entries", return_value=[bambu_metadata()]),
            patch("appglider.app_page.QInputDialog.getItem", return_value=("", False)),
            patch.object(self.library, "set_build_variant") as save,
        ):
            self.page.build_button.click()
            self.wait_for_task()
        save.assert_not_called()
        self.assertEqual(next(a for a in self.library.apps if a.id == BAMBU.id), chosen_bambu())

    def test_failed_build_save_keeps_the_old_choice_and_repository(self) -> None:
        selected = chosen_bambu()
        self.library.add_repository(selected)
        self.window.scan_installed()
        self.window.open_app(BAMBU.id)
        with (
            patch.object(releases, "api_entries", return_value=[bambu_metadata()]),
            patch(
                "appglider.app_page.QInputDialog.getItem",
                side_effect=lambda parent, title, label, items, *args: (items[0], True),
            ),
            patch("appglider.app_page.QMessageBox.warning") as warning,
            patch.object(self.library, "set_build_variant", side_effect=OSError("disk full")),
        ):
            self.page.choose_build()
            self.wait_for_task()
        warning.assert_called_once()
        self.assertIn("could not be saved", self.window.footer.text())
        self.assertEqual(next(a for a in self.library.apps if a.id == selected.id), selected)

    def test_a_build_choice_raised_by_an_install_opens_the_page_and_asks(self) -> None:
        self.library.add_repository(BAMBU)
        self.window.scan_installed()
        with (
            patch.object(releases, "api_entries", return_value=[bambu_metadata()]),
            patch.object(self.page, "choose_build") as choose,
            patch("appglider.library.download") as download,
        ):
            self.window.install_app(BAMBU.id)
            self.wait_for_task()
            choose.assert_called_once_with()
            download.assert_not_called()
        self.assertIs(self.window.pages.currentWidget(), self.page)
        self.assertEqual(self.page.app_id, BAMBU.id)

    def test_icon_search_saves_the_found_icon_or_asks_between_several(self) -> None:
        window = self.window
        window.open_app(APP.id)
        red, blue = normalise_icon(RED), normalise_icon(BLUE)
        found = (IconCandidate("assets/red.svg", red), IconCandidate("assets/blue.svg", blue))
        cached = self.library.icon_path(APP)
        self.assertEqual(self.page.icon_hint.text(), "Default icon")
        with (
            patch("appglider.app_page.discover_icons", return_value=found[:1]),
            patch("appglider.app_page.QInputDialog.getItem") as picker,
        ):
            self.page.find_icons_action.trigger()
            self.wait_for_task()
            picker.assert_not_called()
        cached = self.library.icon_path(APP)
        self.assertEqual(cached.read_bytes(), red)
        self.assertEqual(self.page.icon_hint.text(), "Saved icon")
        self.assertEqual(
            window.cards[APP.id].icon.pixmap().toImage().pixelColor(0, 0).name(), "#ff0000"
        )
        self.assertEqual(self.page.icon.pixmap().toImage().pixelColor(0, 0).name(), "#ff0000")
        with (
            patch("appglider.app_page.discover_icons", return_value=found),
            patch(
                "appglider.app_page.QInputDialog.getItem", return_value=("blue.svg", True)
            ) as picker,
        ):
            self.page.find_icons_action.trigger()
            self.wait_for_task()
            self.assertEqual(picker.call_args.args[3], ["red.svg", "blue.svg"])
        self.assertEqual(cached.read_bytes(), blue)
        self.assertEqual(
            Library(self.library.data_dir, self.library.apps_dir).icon_path(APP), cached
        )
        self.page.default_icon_action.trigger()
        self.assertEqual(cached.read_bytes(), normalise_icon(APP_ICON.read_bytes()))

    def test_icon_search_failures_and_empty_results_leave_the_icon_alone(self) -> None:
        self.window.open_app(APP.id)
        saved = normalise_icon(RED)
        self.library.set_icon(APP, saved)
        cached = self.library.icon_path(APP)
        for outcome, message in (
            (ValueError("GitHub request limit reached"), "request limit"),
            ((), "No icon found"),
        ):
            with (
                self.subTest(outcome=outcome),
                patch("appglider.window.QMessageBox.warning"),
                patch(
                    "appglider.app_page.discover_icons",
                    side_effect=outcome if isinstance(outcome, Exception) else None,
                    return_value=outcome,
                ),
            ):
                self.page.find_icons_action.trigger()
                self.wait_for_task()
                self.assertIn(message, self.window.footer.text())
                self.assertEqual(cached.read_bytes(), saved)
                self.assertTrue(self.page.icon_button.isEnabled())

    def test_a_local_icon_is_saved_and_a_broken_one_is_refused(self) -> None:
        self.window.open_app(APP.id)
        image = Path(self.temp.name) / "a local icon.png"
        image.write_bytes(normalise_icon(BLUE))
        with patch("appglider.app_page.QFileDialog.getOpenFileName", return_value=(str(image), "")):
            self.page.choose_icon_action.trigger()
        cached = self.library.icon_path(APP)
        saved = cached.read_bytes()
        self.assertEqual(saved, normalise_icon(BLUE))
        image.write_bytes(b"broken PNG")
        with (
            patch("appglider.app_page.QFileDialog.getOpenFileName", return_value=(str(image), "")),
            patch("appglider.app_page.QMessageBox.warning") as warning,
        ):
            self.page.choose_icon_action.trigger()
        warning.assert_called_once()
        self.assertIn("valid PNG or SVG", warning.call_args.args[2])
        self.assertEqual(cached.read_bytes(), saved)

    def test_the_icon_search_can_be_cancelled_without_changing_the_icon(self) -> None:
        self.window.open_app(APP.id)
        started = Event()

        def lookup(app, cancel, progress=None):
            progress("Looking through folders… 7 pages so far")
            started.set()
            cancel.wait(timeout=2)
            releases.check_cancel(cancel)
            return ()

        with patch("appglider.app_page.discover_icons", side_effect=lookup):
            self.page.find_icons_action.trigger()
            deadline = time.monotonic() + 3
            while not started.is_set() and time.monotonic() < deadline:
                QTest.qWait(10)
            QTest.qWait(30)
            self.assertIn("7 pages so far", self.window.footer.text())
            self.assertTrue(self.window.progress_bar.isVisibleTo(self.window))
            self.assertFalse(self.page.icon_button.isEnabled())
            self.window.cancel_task()
            self.wait_for_task()
        self.assertEqual(self.library.icon_path(APP), APP.icon)

    def test_shortcut_rollback_and_uninstall_use_the_window_actions(self) -> None:
        window = self.window
        window.open_app(APP.id)
        self.assertFalse(self.page.shortcut_button.isEnabled())
        self.assertFalse(self.page.rollback_button.isEnabled())
        self.assertTrue(self.page.uninstall_row.isHidden())
        self.install("1.0.0")
        self.assertFalse(self.page.rollback_button.isEnabled())
        self.assertEqual(self.page.rollback_row.label.text(), "Roll back")
        self.install("1.1.0")
        self.assertEqual(self.page.rollback_row.label.text(), "Roll back to v1.0.0")
        for button, handler in (
            (self.page.shortcut_button, "create_shortcut"),
            (self.page.rollback_button, "rollback_app"),
            (self.page.uninstall_button, "remove_app"),
        ):
            with self.subTest(handler=handler), patch.object(window, handler) as called:
                self.assertTrue(button.isEnabled())
                button.click()
                called.assert_called_once_with(APP.id)

    def test_removing_from_the_library_asks_first_and_returns_to_the_list(self) -> None:
        window = self.window
        window.open_app(APP.id)
        self.assertTrue(self.page.remove_button.isEnabled())
        with patch(
            "appglider.app_page.QMessageBox.question", return_value=QMessageBox.StandardButton.No
        ) as ask:
            self.page.remove_button.click()
        ask.assert_called_once()
        self.assertEqual(ask.call_args.args[1], f"Remove {APP.name} from the library?")
        self.assertIn(APP.id, window.cards)
        self.assertIs(window.pages.currentWidget(), self.page)
        with patch(
            "appglider.app_page.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes
        ):
            self.page.remove_button.click()
        self.assertEqual(window.pages.currentIndex(), 0)
        self.assertNotIn(APP.id, window.cards)
        self.assertNotIn(APP.id, [app.id for app in self.library.apps])
        self.assertIn("Removed", window.footer.text())

    def test_an_installed_app_cannot_be_removed_from_the_library(self) -> None:
        self.install("1.0.0")
        self.window.open_app(APP.id)
        self.assertFalse(self.page.remove_button.isEnabled())
        with patch("appglider.app_page.QMessageBox.question") as ask:
            self.page.remove_from_library()
        ask.assert_not_called()
        self.assertIn(APP.id, [app.id for app in self.library.apps])

    def manage_dividers_shown(self) -> int:
        return sum(not d.isHidden() for d in self.page.manage_dividers.values())

    def test_manage_shows_only_the_action_that_applies(self) -> None:
        page = self.page
        self.window.open_app(APP.id)
        self.assertTrue(page.remove_row.isVisibleTo(page))
        self.assertTrue(page.remove_button.isEnabled())
        self.assertFalse(page.uninstall_row.isVisibleTo(page))
        self.assertEqual(self.manage_dividers_shown(), 1)
        self.assertEqual(page.remove_row.hint.text(), "No application files are deleted")
        self.install("1.0.0")
        self.assertTrue(page.uninstall_row.isVisibleTo(page))
        self.assertTrue(page.uninstall_button.isEnabled())
        self.assertFalse(page.remove_row.isVisibleTo(page))
        self.assertEqual(self.manage_dividers_shown(), 1)
        self.assertTrue(page.rollback_row.isVisibleTo(page))

    def test_manage_busy_state_disables_the_visible_button(self) -> None:
        self.install("1.0.0")
        self.window.open_app(APP.id)
        with patch.object(self.window, "task", object()):
            self.page.refresh()
        self.assertTrue(self.page.uninstall_row.isVisibleTo(self.page))
        self.assertFalse(self.page.uninstall_button.isEnabled())

    def test_focus_moves_to_remove_when_the_focused_uninstall_button_disappears(self) -> None:
        self.install("1.0.0")
        self.window.open_app(APP.id)
        self.window.activateWindow()
        self.page.uninstall_button.setFocus()
        self.qt.processEvents()
        if QApplication.focusWidget() is not self.page.uninstall_button:
            self.skipTest("The platform does not give the test window keyboard focus")
        self.library.remove(APP)
        self.window.scan_installed()
        self.qt.processEvents()
        self.assertIs(QApplication.focusWidget(), self.page.remove_button)

    # Versions and release notes

    def test_versions_show_the_installed_copy_the_previous_copy_and_their_size(self) -> None:
        self.install("1.0.0", "1.1.0")
        self.window.open_app(APP.id)
        previous = self.page.versions["previous"]
        self.assertTrue(previous.isVisibleTo(self.page))
        size = self.library.scan()[APP.id].previous.stat().st_size
        self.assertEqual(
            previous.text.text(),
            f"v1.0.0 · kept for roll back · {format_size(size)}",
        )
        self.assertEqual(self.page.versions["installed"].text.text(), "v1.1.0")
        self.assertIn(
            str(self.library.scan()[APP.id].path), self.page.versions["installed"].toolTip()
        )
        self.assertEqual(self.page.checked_value.text(), "Not checked")

    def test_stable_and_preview_versions_and_the_check_time_come_from_the_release_info(
        self,
    ) -> None:
        info = self.release_info()
        self.window.release_infos[APP.id] = info
        self.window.open_app(APP.id)
        self.assertEqual(self.page.versions["stable"].text.text(), "v1.0.0")
        self.assertEqual(self.page.versions["preview"].text.text(), "v1.1.0-beta.1")
        self.assertTrue(self.page.checked_value.text().startswith("Today "))
        self.window.release_infos[APP.id] = replace(info, preview=None)
        self.window.refresh()
        self.assertFalse(self.page.versions["preview"].isVisibleTo(self.page))

    def test_whats_new_shows_the_offered_release_with_its_date_size_and_link(self) -> None:
        payload = appimage_bytes()
        release = replace(
            release_for("1.4.0", payload),
            notes="## Changes\n\n- Faster import\n- Fixed a crash",
            published="2026-10-19",
        )
        self.window.releases[APP.id] = release
        self.window.open_app(APP.id)
        self.assertEqual(self.page.notes_heading.text(), "WHAT'S NEW IN 1.4.0")
        self.assertEqual(
            self.page.release_meta.text(),
            f"Released 19 Oct 2026 · {format_size(release.size)} · checksum published",
        )
        self.assertIn("Faster import", self.page.notes_view.toPlainText())
        self.assertTrue(self.page.notes_view.isVisibleTo(self.page))
        self.assertTrue(self.page.notes_empty.isHidden())
        with patch("appglider.app_page.QDesktopServices.openUrl") as opened:
            self.page.notes_link.click()
        opened.assert_called_once_with(QUrl(f"https://github.com/{APP.repo}/releases/tag/v1.4.0"))

    def test_missing_notes_say_so_and_still_link_to_the_release(self) -> None:
        self.window.releases[APP.id] = release_for("1.4.0", appimage_bytes())
        self.window.open_app(APP.id)
        self.assertTrue(self.page.notes_view.isHidden())
        self.assertEqual(self.page.notes_empty.text(), "No release notes were published.")
        self.assertFalse(self.page.notes_empty.isHidden())
        self.assertTrue(self.page.notes_link.isVisibleTo(self.page))
        self.assertNotIn("Released", self.page.release_meta.text())

    def test_notes_never_load_resources_and_only_web_links_open(self) -> None:
        browser = NotesBrowser()
        self.addCleanup(browser.deleteLater)
        self.assertTrue(browser.isReadOnly())
        self.assertFalse(browser.openLinks())
        self.assertFalse(browser.openExternalLinks())
        for kind in range(1, 6):
            for url in (
                "https://example.com/image.png",
                "file:///etc/passwd",
                "/relative/image.png",
            ):
                self.assertIsNone(browser.loadResource(kind, QUrl(url)))
        loaded = []
        original = NotesBrowser.loadResource

        def spy(self, kind, name):
            loaded.append(name)
            return original(self, kind, name)

        with patch.object(NotesBrowser, "loadResource", spy):
            browser.set_notes(
                '![remote](https://example.com/x.png)\n\n<img src="file:///etc/passwd">\n\ntext'
            )
            browser.show()
            self.qt.processEvents()
        self.assertTrue(all(browser.loadResource(2, name) is None for name in loaded))
        self.assertFalse(browser.document().resource(2, QUrl("https://example.com/x.png")))
        with patch("appglider.app_page.QDesktopServices.openUrl") as opened:
            for url in (
                "file:///etc/passwd",
                "javascript:alert(1)",
                "ftp://example.com/x",
                "mailto:a@example.com",
                "relative/page.html",
                "https:///nohost",
            ):
                browser.anchorClicked.emit(QUrl(url))
            opened.assert_not_called()
            browser.anchorClicked.emit(QUrl("https://example.com/page"))
            browser.anchorClicked.emit(QUrl("http://example.com/page"))
        self.assertEqual(
            [call.args[0].toString() for call in opened.call_args_list],
            ["https://example.com/page", "http://example.com/page"],
        )

    def test_long_notes_scroll_inside_a_bounded_panel(self) -> None:
        release = replace(
            release_for("1.4.0", appimage_bytes()),
            notes="\n\n".join(f"- change number {n}" for n in range(200)),
        )
        self.window.releases[APP.id] = release
        self.window.open_app(APP.id)
        self.qt.processEvents()
        self.assertLessEqual(self.page.notes_view.height(), 260)
        self.assertGreater(self.page.notes_view.verticalScrollBar().maximum(), 0)

    def test_the_page_needs_no_horizontal_scrolling_at_the_minimum_window_size(self) -> None:
        self.window.releases[APP.id] = replace(
            release_for("1.4.0", appimage_bytes()), notes="Some notes", published="2026-10-19"
        )
        self.install("1.3.0", "1.3.1")
        self.window.resize(860, 560)
        self.window.open_app(APP.id)
        self.qt.processEvents()
        content = self.page.widget()
        self.assertLessEqual(content.minimumSizeHint().width(), self.page.viewport().width())
        self.assertEqual(self.page.horizontalScrollBar().maximum(), 0)

    def test_columns_stack_when_a_wide_font_leaves_too_little_room(self) -> None:
        self.install("1.3.0", "1.3.1")
        self.window.resize(1400, 700)
        self.window.open_app(APP.id)
        for _ in range(3):
            self.qt.processEvents()
        self.assertEqual(self.page.columns.direction(), QBoxLayout.Direction.LeftToRight)
        # Stands in for a wider desktop font, which the stylesheet's pixel sizes rule out here.
        self.page.notes_panel.setMinimumWidth(900)
        for _ in range(3):
            self.qt.processEvents()
        self.assertEqual(self.page.columns.direction(), QBoxLayout.Direction.TopToBottom)
        self.assertLessEqual(
            self.page.widget().minimumSizeHint().width(), self.page.viewport().width()
        )

    def test_up_to_date_apps_show_the_notes_of_their_current_release(self) -> None:
        self.install("1.0.0")
        release = replace(release_for("1.0.0", appimage_bytes()), notes="Current notes")
        self.window.releases[APP.id] = release
        self.window.refresh()
        self.window.open_app(APP.id)
        self.assertEqual(self.page.chip.text(), "✓ Up to date")
        self.assertIn("Current notes", self.page.notes_view.toPlainText())

    def test_the_helpers_format_dates_sizes_versions_and_links(self) -> None:
        self.assertEqual(format_date("2026-01-05"), "05 Jan 2026")
        self.assertEqual(format_date("garbage"), "garbage")
        self.assertEqual(format_size(84_400_000), "84 MB")
        self.assertEqual(format_size(10), "1 MB")
        self.assertEqual(format_size(2_500_000_000), "2.5 GB")
        self.assertEqual(version_text("1.2.3"), "v1.2.3")
        self.assertEqual(version_text("nightly"), "nightly")
        release = Release(
            "0.6",
            "App.AppImage",
            "https://github.com/o/r/releases/download/studio-v0.6/App.AppImage",
            1,
            "0" * 64,
        )
        self.assertEqual(
            release_page_url(release), "https://github.com/o/r/releases/tag/studio-v0.6"
        )
        tagged = replace(release, url=release.url.replace("studio-v0.6", "v1%2B2"))
        self.assertEqual(release_page_url(tagged), "https://github.com/o/r/releases/tag/v1%2B2")

    def test_the_result_of_a_check_arrives_in_the_labels(self) -> None:
        info = self.release_info()
        self.window.open_app(APP.id)
        with patch("appglider.window.discover_releases", return_value=info):
            self.page.check_button.click()
            self.assertEqual(self.page.check_button.text(), "Checking…")
            self.assertFalse(self.page.check_button.isEnabled())
            self.wait_for_task()
        self.assertEqual(self.page.check_button.text(), "Check now")
        self.assertEqual(self.page.versions["stable"].text.text(), "v1.0.0")
        self.assertTrue(self.page.checked_value.text().startswith("Today "))
        self.assertEqual(self.window.release_infos[APP.id], info)


class ControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt = QApplication.instance() or QApplication([])

    def test_a_switch_toggles_with_space_and_the_mouse_and_paints_in_both_states(self) -> None:
        switch = Switch()
        self.addCleanup(switch.deleteLater)
        switch.setAccessibleName("Hold")
        switch.show()
        toggles: list[bool] = []
        switch.toggled.connect(toggles.append)
        switch.setFocus()
        QTest.keyClick(switch, Qt.Key.Key_Space)
        self.assertTrue(switch.isChecked())
        QTest.keyClick(switch, Qt.Key.Key_Space)
        QTest.mouseClick(switch, Qt.MouseButton.LeftButton)
        self.assertEqual(toggles, [True, False, True])
        self.assertEqual(switch.accessibleName(), "Hold")
        self.assertTrue(switch.focusPolicy() & Qt.FocusPolicy.TabFocus)
        on = switch.grab().toImage()
        switch.setChecked(False)
        switch.setEnabled(False)
        off = switch.grab().toImage()
        self.assertNotEqual(on, off)

    def test_a_segmented_control_is_exclusive_and_reports_only_user_changes(self) -> None:
        segmented = Segmented(("Stable", "Preview"))
        self.addCleanup(segmented.deleteLater)
        segmented.show()
        changes: list[int] = []
        segmented.currentIndexChanged.connect(changes.append)
        self.assertEqual(segmented.currentIndex(), 0)
        segmented.setCurrentIndex(1)
        self.assertEqual(changes, [])
        self.assertTrue(segmented.buttons[1].isChecked())
        self.assertFalse(segmented.buttons[0].isChecked())
        QTest.mouseClick(segmented.buttons[0], Qt.MouseButton.LeftButton)
        self.assertEqual(changes, [0])
        segmented.setCurrentIndex(1, notify=True)
        self.assertEqual(changes, [0, 1])
