"""Apps added from an AppImage file on this computer (no GitHub repository)."""

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from catalog_fixture import APPS, stock
from gui_helpers import close_window
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QBoxLayout, QMessageBox
from test_library import appimage_bytes

from appglider.core.catalog import load_apps, manual_app, valid_app_id
from appglider.github.versions import Cancelled
from appglider.install.desktopentry import read_identity
from appglider.install.library import AppRunning, Library, previous_copy
from appglider.install.place import FileDetails, name_from_file, version_from_name
from appglider.ui.app_status import app_status
from appglider.ui.jobs import TaskResult
from appglider.ui.window import LauncherWindow

NAME = "Sketchpad-1.2.0-x86_64.AppImage"


def details(**changes) -> FileDetails:
    values = {"name": "Sketchpad", "version": "1.2.0", "size": 100, "icon": None}
    return FileDetails(**(values | changes))


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = stock(Library(self.root / "data", self.root / "apps", self.root / "desktop"))
        self.downloads = self.root / "Downloads"
        self.downloads.mkdir()

    def source(self, name: str = NAME, payload: bytes = b"one") -> Path:
        path = self.downloads / name
        path.write_bytes(appimage_bytes(payload))
        return path

    def add(self, name: str = NAME, payload: bytes = b"one", version: str = "1.2.0"):
        source = self.source(name, payload)
        app = manual_app("Sketchpad", source.name)
        self.library.add_file(app, source, version, None)
        return app, source


class IdAndStorageTests(Base):
    def test_ids_cannot_collide_with_repository_ids(self) -> None:
        app = manual_app("A", "a.AppImage")
        self.assertRegex(app.id, r"^file-[0-9a-f]{12}$")
        self.assertNotEqual(app.id, manual_app("A", "a.AppImage").id)
        self.assertFalse(valid_app_id(app.id))  # never a repository id
        self.assertTrue(app.manual)
        for bad in ("a.txt", "/x/a.AppImage", ".AppImage", ".hidden.AppImage"):
            with self.assertRaises(ValueError):
                manual_app("A", bad)
        with self.assertRaises(ValueError):
            manual_app("A", "a.AppImage", app_id="github-x__y")

    def test_storage_round_trip_and_old_data_still_loads(self) -> None:
        before = (self.library.data_dir / "repositories.json").read_text()
        app, _ = self.add()
        entries = json.loads((self.library.data_dir / "repositories.json").read_text())
        self.assertEqual(entries[-1], {"id": app.id, "name": "Sketchpad", "file": NAME})
        self.assertEqual(entries[:-1], json.loads(before))  # old entries unchanged
        self.assertEqual(load_apps(self.library.data_dir / "repositories.json")[-1], app)
        self.assertEqual(self.library.apps[:-1], APPS)
        self.assertEqual(self.library.apps[-1], app)

    def test_bad_file_entries_are_refused_and_the_file_preserved(self) -> None:
        path = self.library.data_dir / "repositories.json"
        path.write_text(json.dumps([{"id": "github-a__b", "name": "X", "file": NAME}]))
        with self.assertRaisesRegex(ValueError, "preserved"):
            load_apps(path)

    def test_export_skips_manual_apps_and_import_ignores_them(self) -> None:
        self.add()
        self.assertEqual(len(self.library.export_entries()), len(APPS))
        plan = self.library.plan_import(self.library.export_entries())
        self.assertEqual(plan.new, ())


class AddFileTests(Base):
    def test_add_copies_the_file_under_the_marker_and_leaves_the_original(self) -> None:
        app, source = self.add()
        installed = self.library.records()[app.id]
        self.assertTrue(installed.managed)
        self.assertEqual(installed.path, self.library.apps_dir.resolve() / app.id / "1.2.0" / NAME)
        self.assertEqual(installed.path.read_bytes(), source.read_bytes())
        self.assertTrue(installed.path.stat().st_mode & stat.S_IXUSR)
        self.assertTrue(source.exists())
        self.assertEqual(installed.version, "1.2.0")
        self.assertTrue((installed.path.parent.parent / ".appglider").is_file())
        self.assertTrue((self.library.desktop_dir / f"appglider-{app.id}.desktop").is_file())
        self.assertEqual(self.library.scan()[app.id].path, installed.path)

    def test_unknown_version_and_icon(self) -> None:
        source = self.source("Odd.AppImage")
        app = manual_app("Odd", source.name)
        png = APPS and (Path(__file__).parents[1] / "src/appglider/assets/icons/photocraft.png")
        self.library.add_file(app, source, "", png.read_bytes())
        installed = self.library.records()[app.id]
        self.assertIsNone(installed.version)
        self.assertRegex(installed.path.parent.name, r"^0\.0\+[0-9a-f]{8}$")
        self.assertTrue((self.library.data_dir / "icons" / f"{app.id}.png").is_file())

    def test_a_bad_file_is_refused_and_leaves_nothing(self) -> None:
        bad = self.downloads / "bad.AppImage"
        bad.write_bytes(b"not an appimage")
        app = manual_app("Bad", bad.name)
        with self.assertRaises(ValueError):
            self.library.add_file(app, bad, "1.0", None)
        self.assertNotIn(app, self.library.apps)
        self.assertFalse((self.library.apps_dir / app.id).exists())

    def test_the_same_file_twice_is_refused(self) -> None:
        self.add()
        copy = self.source("Copy-of-it.AppImage")
        again = manual_app("Again", copy.name)
        with self.assertRaisesRegex(ValueError, "already in the library"):
            self.library.add_file(again, copy, "1.2.0", None)
        self.assertEqual(len(self.library.apps), len(APPS) + 1)
        self.assertFalse((self.library.apps_dir / again.id).exists())

    def test_uninstall_removes_the_app_and_keeps_the_original(self) -> None:
        app, source = self.add()
        with patch("appglider.install.library.running", return_value=False):
            self.library.remove_file_app(app)
        self.assertNotIn(app, self.library.apps)
        self.assertFalse((self.library.apps_dir / app.id).exists())
        self.assertTrue(source.exists())

    def test_version_helpers(self) -> None:
        self.assertEqual(version_from_name("App_v2.3.4_amd64.AppImage"), "2.3.4")
        self.assertEqual(version_from_name("App.AppImage"), "")
        self.assertEqual(name_from_file("Cool-App-1.2.0-x86_64.AppImage"), "Cool App")
        self.assertEqual(name_from_file("plain.AppImage"), "plain")

    def test_identity_is_read_from_inside_the_appimage(self) -> None:
        script = self.root / "fake.AppImage"
        script.write_text(
            '#!/bin/sh\nmkdir -p squashfs-root\ncase "$2" in\n'
            "'*.desktop') printf '[Desktop Entry]\\nName=Fancy\\nIcon=fancy\\n"
            "X-AppImage-Version=3.4.5\\n' > squashfs-root/fancy.desktop;;\n"
            "fancy.png) printf PNGDATA > squashfs-root/fancy.png;;\nesac\n"
        )
        script.chmod(0o755)
        identity = read_identity(script, {})
        self.assertEqual((identity.name, identity.version), ("Fancy", "3.4.5"))
        self.assertEqual(identity.icon, b"PNGDATA")

    def test_identity_follows_a_linked_desktop_file(self) -> None:
        script = self.root / "linked.AppImage"
        script.write_text(
            '#!/bin/sh\nmkdir -p squashfs-root/usr/share/applications\ncase "$2" in\n'
            "'*.desktop') ln -sf usr/share/applications/linked.desktop "
            "squashfs-root/linked.desktop;;\n"
            "usr/share/applications/linked.desktop) printf '[Desktop Entry]\\nName=Linked\\n' "
            "> squashfs-root/usr/share/applications/linked.desktop;;\nesac\n"
        )
        script.chmod(0o755)
        self.assertEqual(read_identity(script, {}).name, "Linked")


class ReplaceTests(Base):
    def test_replace_keeps_the_old_file_for_roll_back(self) -> None:
        app, _ = self.add()
        old = self.library.records()[app.id].path
        newer = self.source("Sketchpad-1.3.0-x86_64.AppImage", b"two")
        self.library.replace_file(app, newer, "1.3.0")
        record = self.library.records()[app.id]
        self.assertEqual(record.version, "1.3.0")
        self.assertEqual(record.previous, old)
        self.assertTrue(old.is_file())
        self.assertEqual(self.library.apps[-1].file, newer.name)
        copy = previous_copy(record)
        self.assertEqual(copy.version if copy else None, "1.2.0")
        self.library.rollback(app)
        back = self.library.records()[app.id]
        self.assertEqual((back.path, back.version), (old, "1.2.0"))
        self.assertEqual(back.previous, record.path)
        self.assertEqual(previous_copy(back).version, "1.3.0")  # type: ignore[union-attr]

    def test_replace_with_the_same_file_or_an_unknown_version(self) -> None:
        app, source = self.add()
        with self.assertRaisesRegex(ValueError, "already in use"):
            self.library.replace_file(app, source, "1.2.0")
        unknown = self.source("Mystery.AppImage", b"three")
        self.library.replace_file(app, unknown, "")
        record = self.library.records()[app.id]
        self.assertIsNone(record.version)
        self.assertEqual(previous_copy(record).version, "1.2.0")  # type: ignore[union-attr]
        newer = self.source("Sketchpad-9.AppImage", b"four")
        self.library.replace_file(app, newer, "")
        self.assertIsNone(self.library.records()[app.id].version)
        self.library.rollback(app)
        self.assertIsNone(self.library.records()[app.id].version)

    def test_replace_while_portable_data_would_move_needs_the_app_closed(self) -> None:
        app, _ = self.add()
        old = self.library.records()[app.id].path
        (old.parent / (old.name + ".home")).mkdir()
        newer = self.source("Sketchpad-1.3.0-x86_64.AppImage", b"two")
        with patch("appglider.install.library.running", return_value=True):
            with self.assertRaises(AppRunning):
                self.library.replace_file(app, newer, "1.3.0")
        self.assertEqual(self.library.records()[app.id].path, old)
        self.library.replace_file(app, newer, "1.3.0")
        moved = self.library.records()[app.id].path
        self.assertTrue((moved.parent / (moved.name + ".home")).is_dir())


class StatusTests(Base):
    def test_manual_chip_and_mode(self) -> None:
        app, _ = self.add()
        installed = self.library.records()[app.id]
        status = app_status(app, installed, None)
        self.assertEqual((status.mode, status.state, status.text), ("launch", "none", "◇ Manual"))
        missing = app_status(app, None, None, missing=installed)
        self.assertEqual((missing.mode, missing.state), ("replace", "missing"))
        busy = app_status(app, installed, None, working="Replacing")
        self.assertEqual((busy.mode, busy.text), ("cancel", "Replacing…"))


class ManualGuiTests(Base):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setStyle("Fusion")

    def setUp(self) -> None:
        super().setUp()
        self.settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.settings.setValue("appearance/theme", "Light")
        self.window = LauncherWindow(self.settings, self.library)
        self.window.show()
        self.addCleanup(close_window, self.window)
        self.qt.processEvents()

    def wait(self, condition) -> None:
        for _ in range(200):
            self.qt.processEvents()
            if condition():
                return
            QTest.qWait(25)
        self.fail(f"timed out: {self.window.footer.text()}")

    def choose(self, source: Path, info: FileDetails | None = None) -> None:
        page = self.window.add_page
        self.window.open_add_app()
        with patch("appglider.ui.pages.add_page.describe_appimage", return_value=info or details()):
            with patch(
                "appglider.ui.pages.add_page.QFileDialog.getOpenFileName",
                return_value=(str(source), ""),
            ):
                page.choose_file()
            self.wait(lambda: page.task is None)

    def test_add_page_file_section_fills_details_and_adds(self) -> None:
        page = self.window.add_page
        self.window.open_add_app()
        self.assertTrue(page.file_button.isEnabled())
        self.assertIn("cannot check", page.file_frame.findChildren(type(page.status))[1].text())
        source = self.source()
        self.choose(source)
        self.assertEqual(page.name.text(), "Sketchpad")
        self.assertTrue(page.release_preview.isVisible())
        self.assertIn(NAME, page.filename.text())
        self.assertTrue(page.add_button.isEnabled())
        page.name.setText("My Sketchpad")
        self.assertTrue(page.add_button.isEnabled())
        self.assertIsNotNone(page.manual)
        page.name.setText("")
        self.assertFalse(page.add_button.isEnabled())
        page.name.setText("My Sketchpad")
        page.add_button.click()
        self.wait(lambda: self.window.app_page.app_id is not None)
        app = self.library.apps[-1]
        self.assertEqual((app.name, app.file), ("My Sketchpad", NAME))
        self.assertTrue(source.exists())
        self.assertEqual(self.window.footer.text(), "Added My Sketchpad.")

    def test_adding_a_file_still_reports_when_the_page_was_left(self) -> None:
        page, window = self.window.add_page, self.window
        self.choose(self.source())
        page.name.setText("My Sketchpad")
        with patch.object(self.library, "add_file", wraps=self.library.add_file) as add:
            page.add_button.click()
            page.leave()  # the user goes elsewhere while the copy runs
            window.show_page(window.settings_page)
            self.wait(lambda: page.task is None)
        add.assert_called_once()
        self.assertEqual([app.name for app in self.library.apps][-1], "My Sketchpad")
        self.assertIn(self.library.apps[-1].id, window.cards)
        self.assertIs(window.pages.currentWidget(), window.settings_page)  # not pulled away
        self.assertEqual(window.footer.text(), "Added My Sketchpad.")

    def test_other_actions_wait_while_a_file_is_copied(self) -> None:
        page, window = self.window.add_page, self.window
        self.choose(self.source())
        page.name.setText("My Sketchpad")
        release = Event()
        self.addCleanup(release.set)

        def slow(app, source, version, png, cancel=None, **kwargs):
            release.wait(5)
            return ""

        with patch.object(self.library, "add_file", side_effect=slow):
            page.add_button.click()
            self.assertTrue(window.busy)
            self.assertFalse(window.nav_add.isEnabled())
            window.start_task("Working…", lambda cancel, progress: TaskResult("ok"))
            self.assertIsNone(window.task)
            release.set()
            self.wait(lambda: page.task is None)
        self.assertFalse(window.busy)

    def failing_copy(self, *, leave: bool) -> Mock:
        page, window = self.window.add_page, self.window
        self.choose(self.source())
        page.name.setText("My Sketchpad")
        with (
            patch.object(self.library, "add_file", side_effect=ValueError("No space left.")),
            patch("appglider.ui.window.MessageBox.warning") as warning,
        ):
            page.add_button.click()
            if leave:
                window.show_page(window.settings_page)
                page.leave()
            self.wait(lambda: page.task is None)
        return warning

    def test_a_copy_that_fails_after_leaving_the_page_opens_a_dialog(self) -> None:
        warning = self.failing_copy(leave=True)
        warning.assert_called_once()
        self.assertEqual(warning.call_args.args[1:], ("Couldn't add the file", "No space left."))

    def test_a_copy_that_fails_on_the_page_keeps_the_inline_status(self) -> None:
        warning = self.failing_copy(leave=False)
        warning.assert_not_called()
        self.assertEqual(self.window.add_page.status.text(), "No space left.")

    def test_the_add_button_becomes_cancel_while_a_file_is_copied(self) -> None:
        page = self.window.add_page
        self.choose(self.source())
        page.name.setText("My Sketchpad")
        started, release = Event(), Event()
        self.addCleanup(release.set)

        def slow(app, source, version, png, cancel=None, **kwargs):
            started.set()
            cancel.wait(5)
            raise Cancelled("Operation cancelled. Your installed version was kept.")

        before = list(self.library.apps)
        with patch.object(self.library, "add_file", side_effect=slow):
            page.add_button.click()
            self.assertEqual(page.add_button.text(), "Cancel")
            self.assertTrue(page.add_button.isEnabled())
            page.add_button.click()
            self.assertEqual(page.add_button.text(), "Cancelling…")
            self.assertFalse(page.add_button.isEnabled())
            self.wait(lambda: page.task is None)
        self.assertEqual(page.add_button.text(), "Add to library")
        self.assertTrue(page.add_button.isEnabled())
        self.assertEqual(page.status.text(), f"Cancelled. {NAME} was not added.")
        self.assertIsNotNone(page.manual)
        self.assertEqual(list(self.library.apps), before)

    def test_cancelling_a_real_copy_leaves_nothing_behind(self) -> None:
        page = self.window.add_page
        self.choose(self.source())
        page.name.setText("My Sketchpad")
        real = self.library.add_file

        def cancel_first(app, source, version, png, cancel=None, **kwargs):
            cancel.set()
            return real(app, source, version, png, cancel, **kwargs)

        apps = list(self.library.apps)
        before = sorted(p for p in self.library.data_dir.rglob("*"))
        with patch.object(self.library, "add_file", side_effect=cancel_first):
            page.add_button.click()
            self.wait(lambda: page.task is None)
        self.assertEqual(list(self.library.apps), apps)
        self.assertEqual(sorted(p for p in self.library.data_dir.rglob("*")), before)
        self.assertIsNotNone(page.manual)

    def test_the_inspect_button_becomes_cancel_while_a_repository_is_checked(self) -> None:
        page = self.window.add_page
        self.window.open_add_app()
        release = Event()
        self.addCleanup(release.set)

        def slow(app, cancel, cache):
            cancel.wait(5)
            raise Cancelled("stopped")

        page.url.setText("https://github.com/someone/sketchpad")
        with patch("appglider.ui.pages.add_page.latest_release", side_effect=slow):
            page.inspect_button.click()
            self.assertEqual(page.inspect_button.text(), "Cancel")
            self.assertTrue(page.inspect_button.isEnabled())
            page.inspect_button.click()
            self.assertEqual(page.inspect_button.text(), "Cancelling…")
            self.assertFalse(page.inspect_button.isEnabled())
            self.wait(lambda: page.task is None)
        self.assertEqual(page.inspect_button.text(), "Inspect repository")
        self.assertTrue(page.inspect_button.isEnabled())
        self.assertEqual(page.status.text(), "Inspection cancelled.")

    def test_a_manual_app_page_keeps_one_column_stretch_in_either_direction(self) -> None:
        page = self.window.app_page
        columns = page.columns
        for left_empty in (False, True):
            for direction, wide in (
                (QBoxLayout.Direction.LeftToRight, True),
                (QBoxLayout.Direction.TopToBottom, False),
            ):
                with self.subTest(left_empty=left_empty, wide=wide):
                    columns.setDirection(direction)
                    page.left_empty = left_empty
                    page.apply_column_stretch()
                    self.assertEqual(columns.stretch(0), 13 if wide and not left_empty else 0)
                    self.assertEqual(columns.stretch(1), 10 if wide else 0)

    def test_a_bad_or_duplicate_file_shows_a_plain_message(self) -> None:
        page = self.window.add_page
        bad = self.downloads / "bad.AppImage"
        bad.write_bytes(b"nope")
        self.window.open_add_app()
        with patch(
            "appglider.ui.pages.add_page.QFileDialog.getOpenFileName", return_value=(str(bad), "")
        ):
            page.choose_file()
        self.wait(lambda: page.task is None)
        self.assertIn("not a type 2 AppImage", page.status.text())
        self.assertIsNone(page.manual)
        self.assertFalse(page.add_button.isEnabled())
        self.add()
        self.choose(self.source("Other.AppImage"))
        page.add_button.click()
        self.wait(lambda: page.task is None)
        self.assertIn("already in the library", page.status.text())

    def test_row_and_app_page_for_a_manual_app(self) -> None:
        app, _ = self.add()
        self.window.scan_installed()
        row = self.window.cards[app.id]
        self.assertEqual(row.chip.text(), "◇ Manual")
        self.assertEqual(row.chip.property("state"), "none")
        self.assertEqual(row.subtitle.full_text, "Added from a file")
        self.assertEqual(row.action_button.text(), "Launch")
        self.assertTrue(row.repository_action.isVisible() is False)
        self.assertFalse(self.window.has_update(app.id))
        self.assertEqual(self.window.updates_count.text(), "0")
        self.assertIsNone(self.window.checkable_app(app.id))
        self.window.checks.add([app.id])
        self.assertFalse(self.window.checks.busy)
        self.window.open_app(app.id)
        page = self.window.app_page
        self.assertTrue(page.notes_frame.isHidden())
        self.assertTrue(page.versions_frame.isHidden())
        for row_ in (page.channel_row, page.hold_row, page.build_row):
            self.assertTrue(row_.isHidden())
        self.assertFalse(page.replace_row.isHidden())
        self.assertTrue(page.channel_chip.isHidden())
        self.assertIn(NAME, page.source.text())

    def test_update_all_and_release_refresh_skip_manual_apps(self) -> None:
        app, _ = self.add()
        self.window.scan_installed()
        self.window.refresh_releases(force=True)
        self.assertFalse(self.window.checks.is_checking(app.id))
        self.window.update_all()
        self.assertIsNone(self.window.task)

    def test_replace_from_the_app_page_runs_as_a_task_and_keeps_rollback(self) -> None:
        app, _ = self.add()
        self.window.scan_installed()
        newer = self.source("Sketchpad-1.3.0-x86_64.AppImage", b"two")
        with (
            patch("appglider.ui.window.describe_appimage", return_value=details(version="1.3.0")),
            patch("appglider.ui.window.QFileDialog.getOpenFileName", return_value=(str(newer), "")),
        ):
            self.window.replace_file(app.id)
            self.assertIsNotNone(self.window.task)
            self.wait(lambda: self.window.task is None)
        record = self.library.records()[app.id]
        self.assertEqual(record.version, "1.3.0")
        self.assertIsNotNone(previous_copy(record))
        self.assertTrue(self.window.cards[app.id].rollback_action.isVisible())

    def test_uninstall_drops_a_manual_app(self) -> None:
        app, _ = self.add()
        self.window.scan_installed()
        with (
            patch(
                "appglider.ui.window.MessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ),
            patch("appglider.install.library.running", return_value=False),
        ):
            self.window.remove_app(app.id)
            self.wait(lambda: self.window.task is None)
        self.assertNotIn(app, self.library.apps)
        self.assertNotIn(app.id, self.window.cards)


if __name__ == "__main__":
    unittest.main()
