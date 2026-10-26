"""AppGlider's check for its own new releases: the pure rules, then the window around them."""

import io
import json
import os
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from threading import Event
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from catalog_fixture import stock
from gui_helpers import close_window
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_library import APP, appimage_bytes, release_for

from appglider import self_update
from appglider.jobs import TaskResult
from appglider.library import Library
from appglider.releases import Cancelled
from appglider.self_update import CheckFailed, SelfUpdate
from appglider.window import LauncherWindow

URL = "https://github.com/Vyrnexis/AppGlider/releases/tag/"


@patch("appglider.self_update.APP_VERSION", "0.8.1")
class PureTests(unittest.TestCase):
    def test_only_a_strictly_newer_stable_tag_is_offered(self) -> None:
        for tag, expected in (
            ("v0.9.1", "0.9.1"),
            ("0.9.1", "0.9.1"),
            ("v0.8.2", "0.8.2"),
            ("v0.8.1.1", "0.8.1.1"),
            ("v0.10.0", "0.10.0"),
            ("v1.0", "1.0"),
            ("v0.8.1", None),
            ("0.8.1", None),
            ("v0.8", None),
            ("v0.8.0", None),
            ("v0.7.9", None),
            ("v0.9.0-beta.1", None),
            ("v1.0.0-rc.1", None),
            ("garbage", None),
            ("", None),
            (None, None),
            (7, None),
        ):
            with self.subTest(tag=tag):
                self.assertEqual(self_update.newer_version(tag), expected)

    def test_an_unreadable_running_version_never_offers_anything(self) -> None:
        with patch("appglider.self_update.APP_VERSION", "development"):
            self.assertIsNone(self_update.newer_version("v9.9.9"))

    def test_check_returns_the_original_tag_only_when_newer(self) -> None:
        with patch("appglider.self_update.latest_tag", return_value="v0.9.1") as latest:
            cancel = Event()
            self.assertEqual(self_update.check(cancel), "v0.9.1")
            latest.assert_called_once_with("Vyrnexis/AppGlider", cancel)
        for tag in ("v0.8.1", "v0.7.0"):
            with patch("appglider.self_update.latest_tag", return_value=tag):
                self.assertIsNone(self_update.check(Event()))

    def test_check_never_falls_back_to_the_api(self) -> None:
        with (
            patch("appglider.self_update.latest_tag", return_value=None),
            patch("appglider.releases.request") as request,
            patch("appglider.releases.latest_release") as api,
        ):
            with self.assertRaises(CheckFailed):
                self_update.check(Event())
            request.assert_not_called()
            api.assert_not_called()

    def test_a_cancelled_check_raises_instead_of_answering(self) -> None:
        cancel = Event()
        cancel.set()
        with patch("appglider.releases.redirect_location") as redirect:
            with self.assertRaises(Cancelled):
                self_update.check(cancel)
            redirect.assert_not_called()

    def test_state_describes_itself(self) -> None:
        stamp = time.mktime((2026, 5, 4, 13, 7, 0, 0, 0, -1))
        image = Path("/home/a/AppGlider.AppImage")
        self.assertEqual(SelfUpdate().hint(image), "Not checked yet")
        self.assertEqual(SelfUpdate(stamp).hint(image), "Up to date · checked 13:07")
        self.assertEqual(SelfUpdate(stamp, "v0.8.1").hint(image), "Up to date · checked 13:07")
        found = SelfUpdate(stamp, "v0.9.1")
        self.assertEqual(found.version, "0.9.1")
        self.assertEqual(found.url, URL + "v0.9.1")
        self.assertEqual(found.hint(image), "Version 0.9.1 is available")
        self.assertEqual(
            found.hint(None), f"Version 0.9.1 is available · {self_update.NO_IMAGE_HINT}"
        )
        self.assertEqual(SelfUpdate(stamp, failed=True).hint(image), "Couldn't check · 13:07")
        self.assertEqual(SelfUpdate(stamp).hint(None), "Up to date · checked 13:07")

    def test_the_notification_key_cannot_be_an_app_id(self) -> None:
        from appglider.catalog import KNOWN_IDS, custom_app

        self.assertNotIn(self_update.NOTIFY_KEY, KNOWN_IDS.values())
        self.assertIn(":", self_update.NOTIFY_KEY)
        self.assertNotIn(":", custom_app("owner/repo").id)


class ImageTests(unittest.TestCase):
    def test_running_image_needs_the_appimage_and_a_python_inside_its_mount(self) -> None:
        with (
            patch.dict(os.environ, {"APPDIR": "/tmp/mount", "APPIMAGE": "/home/a b/G.AppImage"}),
            patch("appglider.self_update.sys.prefix", "/tmp/mount/opt/python"),
        ):
            self.assertEqual(self_update.running_image(), Path("/home/a b/G.AppImage"))

    def test_other_ways_of_starting_have_no_image(self) -> None:
        with (
            patch.dict(os.environ, {"APPDIR": "/tmp/other", "APPIMAGE": "/other.AppImage"}),
            patch("appglider.self_update.sys.prefix", "/project/.venv"),
        ):
            self.assertIsNone(self_update.running_image())
        with (
            patch.dict(os.environ, {"APPDIR": "/tmp/mount"}, clear=True),
            patch("appglider.self_update.sys.prefix", "/tmp/mount/opt/python"),
        ):
            self.assertIsNone(self_update.running_image())
        with (
            patch.dict(os.environ, {"APPIMAGE": "/a.AppImage"}, clear=True),
            patch("appglider.self_update.sys.prefix", "/tmp/mount/opt/python"),
        ):
            self.assertIsNone(self_update.running_image())

    def test_can_replace_needs_a_regular_file_in_a_writable_folder(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name) / "apps"
            folder.mkdir()
            image = folder / "AppGlider.AppImage"
            self.assertFalse(self_update.can_replace(image))  # missing
            image.write_bytes(b"x")
            self.assertTrue(self_update.can_replace(image))
            self.assertFalse(self_update.can_replace(folder))  # a folder is not an image
            if os.geteuid() != 0:
                folder.chmod(0o500)
                try:
                    self.assertFalse(self_update.can_replace(image))
                finally:
                    folder.chmod(0o700)
                self.assertTrue(self_update.can_replace(image))


@patch("appglider.self_update.APP_VERSION", "0.8.1")
class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setApplicationName("AppGlider tests")
        cls.qt.setStyle("Fusion")

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.image = root / "AppGlider.AppImage"
        self.image.write_bytes(b"x")
        self.settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
        self.settings.setValue("appearance/theme", "Light")
        self.answer: object = None
        self.calls = 0
        self.gate: Event | None = None
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch("appglider.self_update.check", side_effect=self.fake_check))
        self.running = stack.enter_context(
            patch("appglider.self_update.running_image", return_value=self.image)
        )
        self.opened: list[str] = []
        stack.enter_context(
            patch(
                "appglider.window.QDesktopServices.openUrl",
                side_effect=lambda url: self.opened.append(url.toString()),
            )
        )
        library = stock(
            Library(
                root / "data",
                root / "apps",
                desktop_dir=root / "desktop",
                autostart_dir=root / "autostart",
            )
        )
        self.window = LauncherWindow(self.settings, library)
        self.window.show()
        self.qt.processEvents()
        self.addCleanup(close_window, self.window)
        self.addCleanup(self.release)

    def fake_check(self, cancel: Event) -> str | None:
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(5)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer if isinstance(self.answer, str) else None

    def release(self) -> None:
        if self.gate is not None:
            self.gate.set()

    def wait(self) -> None:
        deadline = time.monotonic() + 10
        while self.window.self_task is not None and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertIsNone(self.window.self_task, "The check did not finish")

    def check(self, answer: object, **flags: bool) -> None:
        self.answer = answer
        self.window.start_self_check(**flags)
        self.wait()

    def hint(self) -> str:
        return self.window.settings_page.self_row.hint.text()

    def install_app(self, version: str = "1.0.0") -> None:
        payload = appimage_bytes()
        with patch("appglider.releases.request", return_value=io.BytesIO(payload)):
            self.window.library.install(
                APP, release_for(version, payload), Event(), lambda *args: None
            )
        self.window.scan_installed()

    def notifier(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(
            patch("appglider.window.QSystemTrayIcon.isSystemTrayAvailable", return_value=True)
        )
        stack.enter_context(patch.object(self.window.tray, "isVisible", return_value=True))
        stack.enter_context(patch.object(self.window, "isActiveWindow", return_value=False))
        return stack.enter_context(patch.object(self.window.tray, "showMessage"))

    def test_nothing_shows_before_a_check(self) -> None:
        window = self.window
        self.assertTrue(window.self_banner.isHidden())
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.hint(), "Not checked yet")
        self.assertEqual(window.settings_page.self_row.label.text(), "AppGlider 0.8.1")
        self.assertEqual(window.settings_page.self_check_button.text(), "Check now")
        self.assertTrue(window.settings_page.self_check_button.isEnabled())

    def test_a_newer_release_shows_the_banner_above_the_updates_banner(self) -> None:
        window = self.window
        self.check("v0.9.1")
        self.assertTrue(window.self_banner.isVisibleTo(window.list_page))
        self.assertEqual(window.self_banner_label.text(), "<b>AppGlider 0.9.1</b> is available")
        self.assertEqual(window.self_notes_button.text(), "What's new ↗")
        self.assertEqual(window.self_update_button.text(), "Update AppGlider")
        self.assertNotEqual(window.self_update_button.objectName(), "primary")
        layout = window.list_page.layout()
        self.assertLess(layout.indexOf(window.self_banner), layout.indexOf(window.banner))
        window.self_notes_button.click()
        self.assertEqual(self.opened, [URL + "v0.9.1"])
        window.self_update_button.click()  # for now it opens the same page
        self.assertEqual(self.opened, [URL + "v0.9.1"] * 2)
        self.assertEqual(self.hint(), "Version 0.9.1 is available")

    def test_banner_also_shows_on_the_updates_page_and_keeps_the_original_tag(self) -> None:
        window = self.window
        self.check("studio-v0.9.1")
        window.show_view("updates")
        self.assertTrue(window.self_banner.isVisibleTo(window.list_page))
        window.self_notes_button.click()
        self.assertEqual(self.opened, [URL + "studio-v0.9.1"])

    def test_without_an_appimage_the_button_opens_the_releases_page(self) -> None:
        self.running.return_value = None
        self.check("v0.9.1")
        window = self.window
        self.assertEqual(window.self_update_button.text(), "Releases on GitHub ↗")
        self.assertTrue(self.hint().endswith(self_update.NO_IMAGE_HINT))
        window.self_update_button.click()
        self.assertEqual(self.opened, [URL + "v0.9.1"])

    def test_an_image_in_a_folder_that_cannot_be_written_gets_the_releases_button(self) -> None:
        with patch("appglider.self_update.can_replace", return_value=False):
            self.check("v0.9.1")
        self.assertEqual(self.window.self_update_button.text(), "Releases on GitHub ↗")

    def test_banner_goes_away_when_no_newer_release_remains(self) -> None:
        self.check("v0.9.1")
        self.check(None, explicit=True)
        self.assertTrue(self.window.self_banner.isHidden())
        self.assertRegex(self.hint(), r"^Up to date · checked \d\d:\d\d$")

    def test_it_is_not_an_app_and_is_not_counted_as_an_update(self) -> None:
        window = self.window
        self.install_app()
        window.releases[APP.id] = release_for("1.1.0", appimage_bytes())
        window.refresh()
        self.assertEqual(window.updates_count.text(), "1")
        library_rows = set(window.cards)
        self.check("v0.9.1")
        self.assertEqual(window.updates_count.text(), "1")
        self.assertEqual(set(window.cards), library_rows)
        self.assertNotIn("AppGlider", window.banner_text())
        self.assertIn("1 update ready", window.banner_text())
        self.assertEqual(window.search.placeholderText(), f"Search {len(window.apps)} apps")
        window.show_view("updates")
        self.assertEqual(window.search.placeholderText(), "Search 1 apps")

    def test_alone_it_adds_no_count_but_does_light_the_tray_badge(self) -> None:
        window = self.window
        self.assertFalse(window.tray_has_updates)
        self.check("v0.9.1")
        self.assertTrue(window.updates_count.isHidden())
        self.assertTrue(window.banner.isHidden())
        self.assertFalse(window.update_all_button.isVisible())
        self.assertTrue(window.tray_has_updates)
        self.assertIn("AppGlider 0.9.1 available", window.tray.toolTip())
        self.check(None)
        self.assertFalse(window.tray_has_updates)

    def test_hint_states(self) -> None:
        self.assertEqual(self.hint(), "Not checked yet")
        self.check(None, explicit=True)
        self.assertRegex(self.hint(), r"^Up to date · checked \d\d:\d\d$")
        self.check("v0.9.1")
        self.assertEqual(self.hint(), "Version 0.9.1 is available")
        self.check(CheckFailed("no answer"), explicit=True)
        self.assertRegex(self.hint(), r"^Couldn't check · \d\d:\d\d$")
        self.assertIn("Couldn't check", self.window.footer.text())
        self.check(None, explicit=True)
        self.assertRegex(self.hint(), r"^Up to date · checked \d\d:\d\d$")

    def test_background_failures_stay_silent(self) -> None:
        self.check(CheckFailed("no answer"))
        self.assertEqual(self.hint(), "Not checked yet")
        self.assertEqual(self.window.footer.text(), "")
        self.assertTrue(self.window.self_banner.isHidden())
        self.check("v0.9.1")
        self.check(CheckFailed("no answer"))
        self.assertEqual(self.hint(), "Version 0.9.1 is available")
        self.assertTrue(self.window.self_banner.isVisibleTo(self.window.list_page))

    def test_check_now_is_disabled_while_checking_and_never_runs_twice(self) -> None:
        window = self.window
        page = window.settings_page
        window.open_settings("About")
        self.gate = Event()
        self.answer = "v0.9.1"
        page.self_check_button.click()
        self.assertEqual(page.self_check_button.text(), "Checking…")
        self.assertFalse(page.self_check_button.isEnabled())
        window.start_self_check(explicit=True)
        window.check_for_updates()
        self.assertIsNone(window.task)  # the window's task slot stays free for installs
        self.gate.set()
        self.wait()
        self.assertEqual(self.calls, 1)
        self.assertEqual(page.self_check_button.text(), "Check now")
        self.assertTrue(page.self_check_button.isEnabled())
        self.assertEqual(self.hint(), "Version 0.9.1 is available")

    def test_an_explicit_request_during_a_background_check_still_reports_failure(self) -> None:
        window = self.window
        self.gate = Event()
        self.answer = CheckFailed("no answer")
        window.start_self_check()
        window.start_self_check(explicit=True)
        self.gate.set()
        self.wait()
        self.assertEqual(self.calls, 1)
        self.assertRegex(self.hint(), r"^Couldn't check")

    def test_a_check_does_not_block_a_task(self) -> None:
        window = self.window
        self.gate = Event()
        window.start_self_check()
        window.start_task("Working…", lambda cancel, progress: TaskResult("ok"))
        self.assertIsNotNone(window.task)
        deadline = time.monotonic() + 10
        while window.task is not None and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertIsNone(window.task)
        self.assertIsNotNone(window.self_task)

    def test_results_after_quitting_are_ignored_and_no_check_starts(self) -> None:
        window = self.window
        self.gate = Event()
        self.answer = "v0.9.1"
        window.start_self_check(explicit=True)
        window.quitting = True
        self.gate.set()
        self.wait()
        self.assertTrue(window.self_banner.isHidden())
        self.assertEqual(window.self_state, SelfUpdate())
        window.start_self_check(explicit=True)
        self.assertIsNone(window.self_task)
        self.assertEqual(self.calls, 1)

    def test_a_schedule_of_only_when_i_choose_skips_automatic_checks(self) -> None:
        window = self.window
        self.settings.setValue("General/check-interval-hours", 0)
        window.startup_check()
        window.scheduled_check()
        self.assertIsNone(window.self_task)
        self.assertEqual(self.calls, 0)
        window.check_for_updates()  # the toolbar and tray command
        self.wait()
        self.assertEqual(self.calls, 1)
        window.settings_page.self_check_button.click()  # About → Check now
        self.wait()
        self.assertEqual(self.calls, 2)

    def test_startup_and_schedule_check_even_with_no_apps_installed(self) -> None:
        window = self.window
        self.assertFalse(window.installed)
        self.settings.setValue("General/check-interval-hours", 6)
        window.startup_check()
        self.wait()
        self.assertEqual(self.calls, 1)
        with patch("appglider.window.github_status.quota_low", return_value=True):
            window.scheduled_check()
        self.wait()
        self.assertEqual(self.calls, 2)

    def test_the_check_command_asks_about_apps_and_about_appglider(self) -> None:
        window = self.window
        self.install_app()
        with patch.object(window, "refresh_releases") as refresh:
            window.check_button.click()
            refresh.assert_called_once_with(force=True)
        self.wait()
        self.assertEqual(self.calls, 1)
        with patch.object(window, "refresh_releases") as refresh:
            window.tray_check_action.trigger()
            refresh.assert_called_once_with(force=True)
        self.wait()
        self.assertEqual(self.calls, 2)

    def test_startup_runs_the_app_checks_as_before(self) -> None:
        with patch.object(self.window, "refresh_releases") as refresh:
            self.window.startup_check()
        refresh.assert_called_once_with()
        self.wait()

    def test_a_scheduled_check_announces_each_version_once_when_enabled(self) -> None:
        window = self.window
        shown = self.notifier()
        self.settings.setValue("notifications/updates", True)
        self.answer = "v0.9.1"
        window.scheduled_check()
        self.wait()
        shown.assert_called_once()
        self.assertEqual(shown.call_args.args[1], "AppGlider 0.9.1 is available.")
        self.assertEqual(
            json.loads(self.settings.value("notifications/seen")),
            {self_update.NOTIFY_KEY: "0.9.1"},
        )
        window.scheduled_check()
        self.wait()
        shown.assert_called_once()
        self.answer = "v0.9.2"
        window.scheduled_check()
        self.wait()
        self.assertEqual(shown.call_count, 2)
        self.assertEqual(shown.call_args.args[1], "AppGlider 0.9.2 is available.")

    def test_it_never_notifies_when_notifications_are_off_or_the_check_is_not_scheduled(
        self,
    ) -> None:
        window = self.window
        shown = self.notifier()
        self.answer = "v0.9.1"
        window.scheduled_check()
        self.wait()
        shown.assert_not_called()
        self.settings.setValue("notifications/updates", True)
        self.check("v0.9.2")  # startup and manual checks never notify
        self.check("v0.9.3", explicit=True)
        shown.assert_not_called()

    def test_app_and_appglider_updates_share_one_notification(self) -> None:
        window = self.window
        shown = self.notifier()
        self.settings.setValue("notifications/updates", True)
        self.install_app()
        window.releases[APP.id] = release_for("1.1.0", appimage_bytes())
        self.answer = "v0.9.1"
        window.notify_pending = True
        window.start_self_check(notify=True)
        window.release_checks_drained()  # the app checks end first; wait for the other
        shown.assert_not_called()
        self.wait()
        shown.assert_called_once()
        self.assertEqual(
            shown.call_args.args[1],
            f"2 updates are waiting: AppGlider 0.9.1, {APP.name} 1.1.0.",
        )
        self.assertEqual(
            json.loads(self.settings.value("notifications/seen")),
            {APP.id: "1.1.0", self_update.NOTIFY_KEY: "0.9.1"},
        )

    def test_app_notifications_keep_their_own_entries(self) -> None:
        window = self.window
        self.install_app()
        window.releases[APP.id] = release_for("1.1.0", appimage_bytes())
        window.notify_updates()
        self.assertEqual(window.notified, {APP.id: "1.1.0"})

    def test_an_unchecked_session_keeps_the_announcement_it_remembers(self) -> None:
        window = self.window
        window.notified = {self_update.NOTIFY_KEY: "0.9.1"}
        window.notify_updates()
        self.assertEqual(window.notified, {self_update.NOTIFY_KEY: "0.9.1"})
        self.check(None, explicit=True)  # checked, and nothing is waiting any more
        window.notify_updates()
        self.assertEqual(window.notified, {})
