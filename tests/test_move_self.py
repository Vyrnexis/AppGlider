"""Placing an AppImage in a folder, refusing AppGlider's own repository, moving AppGlider."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gui_helpers import close_window
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from test_library import appimage_bytes

from appglider.core.catalog import OWN_REPO, OWN_REPO_NOTE, custom_app
from appglider.core.catalogfile import CatalogEntry
from appglider.github.http import RepositoryMoved
from appglider.install import self_update
from appglider.install.library import Library
from appglider.install.place import place_appimage
from appglider.ui.window import LauncherWindow

NAME = "AppGlider-0.9.7-x86_64.AppImage"


class PlaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "Downloads" / NAME
        self.source.parent.mkdir()
        self.source.write_bytes(appimage_bytes(b"me"))
        self.folder = self.root / "Apps"

    def test_copies_to_the_folder_executable_and_keeps_the_source(self) -> None:
        placed = place_appimage(self.source, self.folder)
        self.assertEqual(placed, self.folder / NAME)
        self.assertEqual(placed.read_bytes(), self.source.read_bytes())
        self.assertEqual(placed.stat().st_mode & 0o777, 0o755)
        self.assertTrue(self.source.exists())
        self.assertEqual([p.name for p in self.folder.iterdir()], [NAME])

    def test_an_existing_target_is_refused_and_left_alone(self) -> None:
        self.folder.mkdir()
        (self.folder / NAME).write_bytes(b"other")
        with self.assertRaisesRegex(ValueError, "already exists"):
            place_appimage(self.source, self.folder)
        self.assertEqual((self.folder / NAME).read_bytes(), b"other")
        self.assertEqual([p.name for p in self.folder.iterdir()], [NAME])

    def test_replace_takes_the_place_of_an_older_appimage_of_the_same_name(self) -> None:
        self.folder.mkdir()
        (self.folder / NAME).write_bytes(appimage_bytes(b"older"))
        placed = place_appimage(self.source, self.folder, replace=True)
        self.assertEqual(placed.read_bytes(), self.source.read_bytes())
        self.assertEqual([p.name for p in self.folder.iterdir()], [NAME])

    def test_replace_still_keeps_a_file_that_is_not_an_appimage(self) -> None:
        self.folder.mkdir()
        (self.folder / NAME).write_bytes(b"other")
        with self.assertRaisesRegex(ValueError, "already exists"):
            place_appimage(self.source, self.folder, replace=True)
        self.assertEqual((self.folder / NAME).read_bytes(), b"other")

    def test_the_same_file_is_not_copied_onto_itself(self) -> None:
        self.assertEqual(place_appimage(self.source, self.source.parent), self.source)
        self.assertEqual([p.name for p in self.source.parent.iterdir()], [NAME])

    def test_symlinks_and_non_files_are_refused(self) -> None:
        link = self.root / "link.AppImage"
        link.symlink_to(self.source)
        for bad in (link, self.root, self.root / "missing.AppImage"):
            with self.assertRaisesRegex(ValueError, "not a plain file"):
                place_appimage(bad, self.folder)

    def test_a_failed_check_leaves_nothing_behind(self) -> None:
        self.source.write_bytes(b"not an appimage")
        with self.assertRaises(ValueError):
            place_appimage(self.source, self.folder)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_too_little_space_is_refused(self) -> None:
        with patch("appglider.install.place.FREE_SPACE_MARGIN", 1 << 60):
            with self.assertRaisesRegex(ValueError, "free disk space"):
                place_appimage(self.source, self.folder)
        self.assertEqual(list(self.folder.iterdir()), [])


class OwnRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "data", self.root / "apps", self.root / "desktop")

    def test_the_library_refuses_it_in_any_case(self) -> None:
        for text in (OWN_REPO, "vyrnexis/appglider", "https://github.com/Vyrnexis/AppGlider.git"):
            with self.assertRaises(ValueError) as error:
                self.library.add_repository(custom_app(text))
            self.assertEqual(str(error.exception), OWN_REPO_NOTE)
        self.assertNotIn(OWN_REPO.casefold(), [a.repo.casefold() for a in self.library.apps])

    def test_a_catalog_import_skips_it(self) -> None:
        plan = self.library.plan_import([CatalogEntry(OWN_REPO, "AppGlider")])
        self.assertEqual(plan.new, ())

    def open_add_page(self):
        settings = QSettings(str(self.root / "s.ini"), QSettings.Format.IniFormat)
        window = LauncherWindow(settings, self.library)
        self.addCleanup(close_window, window)
        window.open_add_app()
        return window, window.add_page

    def test_the_add_page_says_why_before_any_lookup(self) -> None:
        _, page = self.open_add_page()
        page.url.setText("https://github.com/vyrnexis/appglider")
        with patch("appglider.ui.pages.add_page.latest_release") as latest:
            page.inspect_repository()
        latest.assert_not_called()
        self.assertIsNone(page.task)
        self.assertEqual(page.status.text(), OWN_REPO_NOTE)
        self.assertFalse(page.add_button.isEnabled())

    def test_a_repository_that_moved_to_it_is_refused_too(self) -> None:
        _, page = self.open_add_page()
        page.url.setText("https://github.com/someone/old-glider")

        def latest(app, cancel, cache_dir=None):
            raise RepositoryMoved("someone/old-glider", OWN_REPO)

        with (
            patch("appglider.ui.pages.add_page.latest_release", side_effect=latest),
            patch("appglider.ui.pages.add_page.discover_icons", return_value=()),
        ):
            page.inspect_repository()
            deadline = time.monotonic() + 5
            while page.task is not None and time.monotonic() < deadline:
                QTest.qWait(10)
        self.assertIn(OWN_REPO_NOTE, page.status.text())
        self.assertFalse(page.add_button.isEnabled())


class MoveSelfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.apps = self.root / "apps"
        self.apps.mkdir()
        self.image = self.root / "Downloads" / NAME
        self.image.parent.mkdir()
        self.image.write_bytes(appimage_bytes(b"me"))
        self.settings = QSettings(str(self.root / "s.ini"), QSettings.Format.IniFormat)
        self.settings.setValue("installation/folder", str(self.apps))
        library = Library(self.root / "data", self.apps, self.root / "desktop")
        self.window = LauncherWindow(self.settings, library)
        self.addCleanup(close_window, self.window)
        self.page = self.window.settings_page

    def visible_with(self, image: Path | None) -> bool:
        with patch("appglider.install.self_update.running_image", return_value=image):
            self.page.update_move_row()
        return not self.page.move_row.isHidden()

    def test_the_action_shows_only_for_a_packaged_image_outside_the_folder(self) -> None:
        self.assertTrue(self.visible_with(self.image))
        self.assertIn(str(self.image.parent), self.page.move_row.hint.text())
        self.assertFalse(self.visible_with(self.apps / NAME))
        self.assertFalse(self.visible_with(None))

    def test_changing_the_folder_updates_the_action(self) -> None:
        with patch("appglider.install.self_update.running_image", return_value=self.image):
            self.page.folder.setText(str(self.image.parent))
            self.page.apply_folder()
            self.assertTrue(self.page.move_row.isHidden())
            self.page.folder.setText(str(self.apps))
            self.page.apply_folder()
            self.assertFalse(self.page.move_row.isHidden())

    def click_move(self, answer=QMessageBox.StandardButton.Yes):
        with patch(
            "appglider.ui.pages.settings_page.MessageBox.question", return_value=answer
        ) as ask:
            self.page.move_button.click()
        return ask

    def test_moving_asks_first_and_says_what_will_happen(self) -> None:
        with (
            patch("appglider.install.self_update.running_image", return_value=self.image),
            patch.object(self.window, "move_self") as move,
        ):
            ask = self.click_move(QMessageBox.StandardButton.No)
            move.assert_not_called()
            title, text = ask.call_args.args[1:3]
            self.assertEqual(title, "Move AppGlider here?")
            self.assertIn("restarts", text)
            self.assertIn(str(self.image), text)
            self.assertIn("removed", text)
            self.click_move()
            move.assert_called_once()

    def test_moving_copies_records_and_starts_the_copy(self) -> None:
        started: list[tuple[Path, str]] = []
        with (
            patch("appglider.install.self_update.running_image", return_value=self.image),
            patch.object(
                self.window, "start_image", side_effect=lambda p, n: started.append((p, n))
            ),
            patch.object(self.window, "point_entries_at", return_value=[]),
        ):
            self.click_move()
            deadline = time.monotonic() + 5
            while self.window.task is not None and time.monotonic() < deadline:
                QTest.qWait(10)
        new = self.apps / NAME
        self.assertEqual([p for p, _ in started], [new])
        self.assertTrue(self.image.exists())
        self.assertEqual(self.settings.value(self_update.REPLACED_KEY), str(self.image))
        self.assertEqual(self.settings.value(self_update.READY_KEY), str(new))

    def test_moving_replaces_an_older_copy_already_in_the_folder(self) -> None:
        (self.apps / NAME).write_bytes(appimage_bytes(b"older"))
        started: list[tuple[Path, str]] = []
        with (
            patch("appglider.install.self_update.running_image", return_value=self.image),
            patch("appglider.ui.window.running", return_value=False),
            patch.object(
                self.window, "start_image", side_effect=lambda p, n: started.append((p, n))
            ),
            patch.object(self.window, "point_entries_at", return_value=[]),
        ):
            self.click_move()
            deadline = time.monotonic() + 5
            while self.window.task is not None and time.monotonic() < deadline:
                QTest.qWait(10)
        new = self.apps / NAME
        self.assertEqual([p for p, _ in started], [new])
        self.assertEqual(new.read_bytes(), self.image.read_bytes())

    def test_cancelling_the_move_changes_nothing_and_does_not_restart(self) -> None:
        def cancel_then_place(source, folder, cancel=None, **kwargs):
            self.window.cancel_task()
            return place_appimage(source, folder, cancel, **kwargs)

        with (
            patch("appglider.install.self_update.running_image", return_value=self.image),
            patch("appglider.ui.window.place_appimage", side_effect=cancel_then_place),
            patch.object(self.window, "start_image") as start,
        ):
            self.click_move()
            deadline = time.monotonic() + 5
            while self.window.task is not None and time.monotonic() < deadline:
                QTest.qWait(10)
        start.assert_not_called()
        self.assertEqual(list(self.apps.iterdir()), [])
        self.assertFalse(self.settings.contains(self_update.REPLACED_KEY))

    def test_moving_while_an_update_check_runs_says_to_wait(self) -> None:
        with (
            patch("appglider.install.self_update.running_image", return_value=self.image),
            patch.object(type(self.window.checks), "busy", new_callable=PropertyMock) as busy,
        ):
            busy.return_value = True
            self.window.move_self(self.apps)
        self.assertIsNone(self.window.task)
        self.assertEqual(
            self.window.footer.text(), "Wait for the update check to finish, then try again."
        )

    def test_a_running_copy_in_the_folder_is_not_replaced(self) -> None:
        (self.apps / NAME).write_bytes(appimage_bytes(b"older"))
        with (
            patch("appglider.install.self_update.running_image", return_value=self.image),
            patch("appglider.ui.window.running", return_value=True),
            patch.object(self.window, "start_image") as start,
            patch("appglider.ui.window.MessageBox.warning") as warning,
        ):
            self.click_move()
            deadline = time.monotonic() + 5
            while self.window.task is not None and time.monotonic() < deadline:
                QTest.qWait(10)
        start.assert_not_called()
        self.assertIn("is running", warning.call_args.args[2])
        self.assertEqual((self.apps / NAME).read_bytes(), appimage_bytes(b"older"))

    def test_a_refused_move_says_so_in_plain_text_and_does_not_restart(self) -> None:
        (self.apps / NAME).write_bytes(b"other")
        with (
            patch("appglider.install.self_update.running_image", return_value=self.image),
            patch.object(self.window, "start_image") as start,
            patch("appglider.ui.window.MessageBox.warning") as warning,
        ):
            self.click_move()
            deadline = time.monotonic() + 5
            while self.window.task is not None and time.monotonic() < deadline:
                QTest.qWait(10)
        start.assert_not_called()
        self.assertEqual(warning.call_args.args[1], "Couldn't move AppGlider")
        self.assertIn("already exists", warning.call_args.args[2])
        self.assertIn("Couldn't move AppGlider", self.window.footer.text())
        self.assertIn("already exists", self.window.footer.text())
        self.assertFalse(self.settings.contains(self_update.REPLACED_KEY))

    def test_the_new_copy_removes_the_old_file_from_the_other_folder(self) -> None:
        new = self.apps / NAME
        new.write_bytes(appimage_bytes(b"me"))
        self_update.record_move(self.settings, self.image, new)
        with (
            patch("appglider.install.self_update.running_image", return_value=new),
            patch("appglider.install.self_update.library.running", return_value=False),
        ):
            self_update.cleanup(self.settings)
        self.assertFalse(self.image.exists())
        self.assertTrue(new.exists())
        for key in (self_update.REPLACED_KEY, self_update.READY_KEY, self_update.MOVED_KEY):
            self.assertFalse(self.settings.contains(key))

    def test_a_file_in_another_folder_is_still_kept_after_an_update(self) -> None:
        new = self.apps / NAME
        new.write_bytes(appimage_bytes(b"me"))
        self.settings.setValue(self_update.REPLACED_KEY, str(self.image))
        self.settings.setValue(self_update.READY_KEY, str(new))
        with patch("appglider.install.self_update.running_image", return_value=new):
            self_update.cleanup(self.settings)
        self.assertTrue(self.image.exists())


if __name__ == "__main__":
    unittest.main()
