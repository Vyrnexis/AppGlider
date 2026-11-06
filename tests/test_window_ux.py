"""Main window and cross-page flows: uninstall and forget, rollback hold, banner, welcome."""

import io
import os
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from catalog_fixture import stock
from gui_helpers import close_window
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QMessageBox
from test_library import APP, appimage_bytes, release_for

from appglider.github.download import download
from appglider.install.library import Installed, Library
from appglider.instance import SingleInstance
from appglider.ui.controls import WAIT_TIP, MessageBox
from appglider.ui.jobs import TaskResult
from appglider.ui.window import LauncherWindow, update_all_message, when_text

CONFIRM = "appglider.ui.window.MessageBox.confirm"


def make_library(root: str) -> Library:
    return Library(
        Path(root) / "data",
        Path(root) / "apps",
        desktop_dir=Path(root) / "desktop",
        autostart_dir=Path(root) / "autostart",
    )


class WindowCase(unittest.TestCase):
    stocked = True

    @classmethod
    def setUpClass(cls) -> None:
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setStyle("Fusion")

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = QSettings(
            str(Path(self.temp.name) / "settings.ini"), QSettings.Format.IniFormat
        )
        self.settings.setValue("appearance/theme", "Light")
        check = patch("appglider.install.self_update.check", return_value=None)
        check.start()
        self.addCleanup(check.stop)
        self.library = make_library(self.temp.name)
        if self.stocked:
            stock(self.library)
        self.window = LauncherWindow(self.settings, self.library)
        self.window.show()
        self.qt.processEvents()
        self.addCleanup(close_window, self.window)

    def install(self, *versions: str) -> None:
        for version in versions:
            payload = appimage_bytes(version.encode())
            with patch("appglider.github.http.request", return_value=io.BytesIO(payload)):
                self.library.install(
                    APP, release_for(version, payload), Event(), lambda *args: None
                )
        self.window.scan_installed()

    def wait_for_task(self) -> None:
        deadline = time.monotonic() + 8
        while self.window.task is not None and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertIsNone(self.window.task, "Background operation did not finish")


class ConfirmTests(WindowCase):
    def press(self, label: str, tick: bool = False):
        def exec_(box: QMessageBox) -> int:
            for check in box.findChildren(QCheckBox):
                if tick:
                    check.setChecked(True)
            for button in box.buttons():
                if button.text() == label:
                    button.click()
            return 0

        return patch.object(QMessageBox, "exec", new=exec_)

    def test_cancel_is_the_default_and_the_checkbox_starts_unticked(self) -> None:
        seen = {}

        def exec_(box: QMessageBox) -> int:
            seen["default"] = box.defaultButton().text()
            seen["escape"] = box.escapeButton().text()
            seen["tick"] = box.checkBox().isChecked()
            seen["names"] = [button.text() for button in box.buttons()]
            return 0

        with patch.object(QMessageBox, "exec", new=exec_):
            accepted, ticked = MessageBox.confirm(None, "T", "Body", "Uninstall", "Also remove")
        self.assertEqual((accepted, ticked), (False, False))
        self.assertEqual(seen["default"], "Cancel")
        self.assertEqual(seen["escape"], "Cancel")
        self.assertFalse(seen["tick"])
        self.assertIn("Uninstall", seen["names"])

    def test_the_action_button_and_the_tick_are_reported(self) -> None:
        with self.press("Uninstall", tick=True):
            result = MessageBox.confirm(None, "T", "Body", "Uninstall", "Also remove")
        self.assertEqual(result, (True, True))


class UninstallTests(WindowCase):
    def test_uninstall_keeps_the_card_by_default(self) -> None:
        self.install("1.0.0")
        with patch(CONFIRM, return_value=(True, False)) as ask:
            self.window.remove_app(APP.id)
            self.wait_for_task()
        self.assertEqual(ask.call_args.args[1], f"Uninstall {APP.name}?")
        self.assertEqual(ask.call_args.args[3], "Uninstall")
        self.assertEqual(ask.call_args.args[4], "Also remove from my library")
        self.assertNotIn(APP.id, self.window.installed)
        self.assertIn(APP.id, self.window.cards)
        self.assertEqual(
            self.window.footer.text(),
            f"Uninstalled {APP.name}. It stays in your library, so you can install it again.",
        )

    def test_the_tick_also_removes_it_from_the_library(self) -> None:
        self.install("1.0.0")
        with patch(CONFIRM, return_value=(True, True)):
            self.window.remove_app(APP.id)
            self.wait_for_task()
        self.assertNotIn(APP.id, self.window.cards)
        self.assertEqual(
            self.window.footer.text(),
            f"Uninstalled {APP.name} and removed it from your library.",
        )

    def test_cancelling_changes_nothing(self) -> None:
        self.install("1.0.0")
        with patch(CONFIRM, return_value=(False, True)):
            self.window.remove_app(APP.id)
        self.assertIsNone(self.window.task)
        self.assertIn(APP.id, self.window.installed)

    def test_a_running_app_offers_force_close_before_asking(self) -> None:
        self.install("1.0.0")
        with (
            patch.object(self.window, "app_running", return_value=True),
            patch.object(self.window, "offer_force_close") as offer,
            patch(CONFIRM) as ask,
        ):
            self.window.remove_app(APP.id)
            self.window.rollback_app(APP.id)
        ask.assert_not_called()
        self.assertEqual(offer.call_count, 2)

    def test_forgetting_a_missing_copy_can_also_remove_the_card(self) -> None:
        self.install("1.0.0")
        self.window.installed[APP.id].path.unlink()
        self.window.scan_installed()
        self.assertIn(APP.id, self.window.missing)
        with patch(CONFIRM, return_value=(True, True)) as ask:
            self.window.forget_missing(APP.id)
        self.assertEqual(ask.call_args.args[3:5], ("Forget", "Also remove from my library"))
        self.assertNotIn(APP.id, self.window.cards)
        self.assertIn("removed it from your library", self.window.footer.text())

    def test_the_menu_offers_exactly_one_way_out_for_each_state(self) -> None:
        card = self.window.cards[APP.id]

        def offered() -> list[str]:
            return [
                action.text()
                for action in (card.remove_action, card.forget_action, card.unlist_action)
                if action.isVisible()
            ]

        self.assertEqual(offered(), ["Remove from library…"])
        self.install("1.0.0")
        self.assertEqual(offered(), ["Uninstall…"])
        self.window.installed[APP.id].path.unlink()
        self.window.scan_installed()
        self.assertEqual(offered(), ["Forget missing copy…"])

    def test_remove_from_library_is_refused_for_a_missing_copy(self) -> None:
        self.install("1.0.0")
        self.window.installed[APP.id].path.unlink()
        self.window.scan_installed()
        with patch(CONFIRM) as ask:
            self.window.remove_from_library(APP.id)
        ask.assert_not_called()


class RollbackTests(WindowCase):
    def test_rollback_holds_the_restored_version_when_ticked(self) -> None:
        self.install("1.0.0", "1.1.0")
        with patch(CONFIRM, return_value=(True, True)) as ask:
            self.window.rollback_app(APP.id)
            self.wait_for_task()
        self.assertEqual(ask.call_args.args[3], "Roll back")
        self.assertIn("Hold at v1.0.0", ask.call_args.args[4])
        self.assertTrue(ask.call_args.kwargs["checked"])
        self.assertEqual(self.window.installed[APP.id].version, "1.0.0")
        self.assertTrue(next(a for a in self.library.apps if a.id == APP.id).hold_updates)
        self.assertIn("held", self.window.footer.text())

    def test_rollback_without_the_tick_sets_no_hold(self) -> None:
        self.install("1.0.0", "1.1.0")
        with patch(CONFIRM, return_value=(True, False)):
            self.window.rollback_app(APP.id)
            self.wait_for_task()
        self.assertFalse(next(a for a in self.library.apps if a.id == APP.id).hold_updates)


class MessageTests(unittest.TestCase):
    def test_update_all_summary_names_what_was_skipped(self) -> None:
        self.assertEqual(
            update_all_message(3, 0, skipped=["Krita", "GIMP"]),
            "Updated 3 apps. Skipped because running: Krita, GIMP. "
            "Close them and press Update all again.",
        )
        self.assertEqual(update_all_message(1, 0, cancelled=True), "Cancelled. Updated 1 app.")
        self.assertEqual(
            update_all_message(0, 0, choices=["A", "B"]),
            "No apps were updated. Needs a build choice: A, B.",
        )

    def test_a_day_other_than_today_is_named(self) -> None:
        now = time.time()
        self.assertRegex(when_text(now), r"^\d\d:\d\d$")
        self.assertTrue(when_text(now - 86400).startswith("yesterday "))
        self.assertNotRegex(when_text(now - 5 * 86400), r"^(yesterday )?\d\d:\d\d$")

    def test_a_bad_checksum_says_what_was_kept(self) -> None:
        payload = appimage_bytes()
        release = release_for("1.0.0", payload)
        bad = release.__class__(**{**release.__dict__, "sha256": "0" * 64})
        for installed, tail in ((True, "Your current version was kept."), (False, "Nothing")):
            with (
                patch("appglider.github.http.request", return_value=io.BytesIO(payload)),
                tempfile.TemporaryDirectory() as folder,
                self.assertRaises(ValueError) as caught,
            ):
                download(
                    bad,
                    Path(folder) / "file",
                    Event(),
                    lambda *args: None,
                    version_installed=installed,
                )
            text = str(caught.exception)
            self.assertIn("did not match the checksum the project published", text)
            self.assertIn(tail, text)

    def test_a_second_launch_that_cannot_wake_the_first_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            first, second = SingleInstance(Path(temp)), SingleInstance(Path(temp))
            self.assertTrue(first.acquire())
            self.addCleanup(first.release)
            first.socket_path.unlink()
            self.assertFalse(second.acquire())
            self.assertTrue(second.wake_failed)


class WindowBehaviourTests(WindowCase):
    def test_the_banner_offers_choose_build_when_only_choices_wait(self) -> None:
        app = self.window.apps[0]
        with patch.object(self.window, "update_groups", return_value=([], [app])):
            text = self.window.banner_text()
        self.assertIn("1 update needs a choice", text)
        self.assertNotIn("ready", text)

    def test_check_for_updates_stays_enabled_with_no_apps_installed(self) -> None:
        self.assertFalse(self.window.installed)
        self.assertTrue(self.window.check_button.isEnabled())
        self.assertIn("only AppGlider", self.window.check_button.toolTip())

    def test_escape_clears_a_search_before_anything_else(self) -> None:
        self.window.search.setFocus()
        self.window.search.setText("zzz")
        self.window.go_back()
        self.assertEqual(self.window.search.text(), "")

    def test_escape_leaves_settings(self) -> None:
        self.window.open_settings("General")
        self.assertIs(self.window.pages.currentWidget(), self.window.settings_page)
        self.window.go_back()
        self.assertIs(self.window.pages.currentWidget(), self.window.list_page)

    def test_busy_controls_say_to_wait(self) -> None:
        with patch("appglider.ui.window.QThreadPool.globalInstance"):
            self.window.start_task("Working", lambda cancel, progress: TaskResult("Done"))
        self.assertFalse(self.window.nav_add.isEnabled())
        self.assertEqual(self.window.nav_add.toolTip(), WAIT_TIP)
        card = next(iter(self.window.cards.values()))
        self.assertEqual(card.action_button.toolTip(), WAIT_TIP)

    def test_a_tray_message_says_what_happened(self) -> None:
        with patch.object(self.window, "tray_message") as say:
            self.window.task_finished(TaskResult("Body", title="PhotoCraft updated"))
        say.assert_called_once_with("PhotoCraft updated", "Body")

    def test_the_first_hide_to_the_tray_explains_itself_once(self) -> None:
        with patch.object(self.window.tray, "showMessage") as show:
            self.window.first_hide_hint()
            self.window.first_hide_hint()
        self.assertEqual(show.call_count, 1)
        self.assertEqual(show.call_args.args[0], "AppGlider is still running")

    def test_a_launch_says_so_without_the_log_path(self) -> None:
        self.window.footer.setText("Launched X.")
        self.window.footer.setToolTip("Log: /x")
        self.window.footer.setText("Next")
        self.assertEqual(self.window.footer.toolTip(), "")

    def test_failed_checks_are_named_in_the_footer(self) -> None:
        self.install("1.0.0")
        self.window.release_errors[APP.id] = "GitHub says no"
        self.assertEqual(
            self.window.check_outcome(), f"Check failed for {APP.name}: GitHub says no"
        )

    def test_an_installed_app_without_a_version_shows_a_dash(self) -> None:
        card = self.window.cards[APP.id]
        card.set_state(Installed(Path("/x"), None, True), None, False)
        self.assertEqual(card.installed_version.text(), "—")
        self.assertIn("Version not reported", card.installed_version.toolTip())


class WelcomeTests(WindowCase):
    stocked = False

    def test_the_welcome_hides_what_has_nothing_to_act_on(self) -> None:
        window = self.window
        self.assertTrue(window.welcome.isVisibleTo(window))
        for widget in (window.search, window.filters, window.options_button, window.check_button):
            self.assertFalse(widget.isVisibleTo(window), widget)
        self.assertEqual(window.welcome_add_button.text(), "Add your first app")
        self.assertRegex(window.welcome_example_button.text(), r"adds \d+ creative apps")


if __name__ == "__main__":
    unittest.main()
