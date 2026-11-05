"""Adding and replacing files: the fallback note, reading the real details, cancelling."""

import os
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import test_manual_apps as manual
from gui_helpers import close_window
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_install_fixes import Base as FileBase

from appglider.core.catalog import manual_app
from appglider.github.versions import Cancelled
from appglider.install import place
from appglider.install.desktopentry import AppImageIdentity
from appglider.install.library import LEGACY_FOLDER, Library
from appglider.install.place import NO_READER_NOTE, UNREADABLE_NOTE, place_appimage
from appglider.ui.window import LauncherWindow


class LegacyFolderTests(FileBase):
    def test_a_folder_with_two_plus_signs_is_still_owned_but_new_ones_are_not_made(self) -> None:
        app = self.add("Old-1.0.0.AppImage", b"a", "1.0.0")
        root = self.library.records()[app.id].path.parent.parent
        legacy = root / "1.0.0+a+b12345678"
        legacy.mkdir()
        self.assertEqual(self.library._owned(app, legacy / "x.AppImage"), root)
        for bad in ("1.0.0+a+b+c", "nonsense", "1.0.0+"):
            with self.subTest(bad), self.assertRaises(ValueError):
                self.library._owned(app, root / bad / "x.AppImage")
        self.assertRegex("1.0.0+a+b12345678", LEGACY_FOLDER)
        new = Library._manual_folder(root, "1.0.0+a", "ab" * 32)
        self.assertNotRegex(new.name, LEGACY_FOLDER)


class CancelCopyTests(FileBase):
    def test_a_cancelled_copy_leaves_no_stage_and_raises_cancelled(self) -> None:
        source = self.source("Big-1.0.AppImage")
        source.write_bytes(source.read_bytes() + bytes(3 * place.COPY_CHUNK))
        folder = self.root / "dest"
        cancel = Event()
        real = place.check_cancel
        calls = []

        def count(event: Event) -> None:
            calls.append(1)
            if len(calls) == 2:
                event.set()
            real(event)

        with patch("appglider.install.place.check_cancel", side_effect=count):
            with self.assertRaises(Cancelled):
                place_appimage(source, folder, cancel)
        self.assertEqual(os.listdir(folder), [])

    def test_an_uncancelled_copy_is_complete_and_executable(self) -> None:
        source = self.source("Ok-1.0.AppImage")
        placed = place_appimage(source, self.root / "dest", Event())
        self.assertEqual(placed.read_bytes(), source.read_bytes())
        self.assertTrue(os.access(placed, os.X_OK))

    def test_add_and_replace_stop_when_cancelled_and_clean_up(self) -> None:
        cancel = Event()
        cancel.set()
        source = self.source("Sketchpad-1.2.0.AppImage")
        source.write_bytes(source.read_bytes() + bytes(2 * place.COPY_CHUNK))
        app = manual_app("Sketchpad", source.name)
        with self.assertRaises(Cancelled):
            self.library.add_file(app, source, "1.2.0", None, cancel)
        self.assertEqual(self.library.records(), {})
        self.assertFalse((self.root / "apps" / app.id).exists())
        kept = self.add("Kept-1.0.0.AppImage", b"k", "1.0.0")
        before = self.library.records()[kept.id]
        newer = self.source("Kept-1.1.0.AppImage", b"n" * (2 * place.COPY_CHUNK))
        with self.assertRaises(Cancelled):
            self.library.replace_file(kept, newer, "1.1.0", cancel)
        self.assertEqual(self.library.records()[kept.id], before)
        self.assertEqual(
            sorted(p.name for p in before.path.parent.parent.iterdir() if p.is_dir()),
            [before.path.parent.name],
        )


class IdentifyAfterAddTests(FileBase):
    IDENTITY = AppImageIdentity("Fancy", "3.4.5", None)

    def test_the_real_details_are_read_from_the_placed_copy_when_asked(self) -> None:
        source = self.source("fancy-x86_64.AppImage")
        app = manual_app("fancy", source.name)
        seen = []

        def identity(path: Path, environment: object) -> AppImageIdentity:
            seen.append(path)
            return self.IDENTITY

        with patch("appglider.install.library.read_identity", side_effect=identity):
            self.library.add_file(app, source, "", None, identify=True)
        added = self.library.apps[-1]
        record = self.library.records()[added.id]
        self.assertEqual((added.name, record.version), ("Fancy", "3.4.5"))
        self.assertEqual(seen, [record.path])  # the copy, never the picked file

    def test_a_name_the_user_edited_is_kept_and_nothing_is_read_unasked(self) -> None:
        source = self.source("fancy-x86_64.AppImage")
        app = manual_app("My own name", source.name)
        with patch("appglider.install.library.read_identity", return_value=self.IDENTITY):
            self.library.add_file(app, source, "", None, identify=True)
        self.assertEqual(self.library.apps[-1].name, "My own name")
        other = self.source("plain-x86_64.AppImage", b"2")
        with patch("appglider.install.library.read_identity") as read:
            self.library.add_file(manual_app("plain", other.name), other, "", None)
        read.assert_not_called()

    def test_replace_reads_the_version_from_the_placed_copy(self) -> None:
        app = self.add("A-1.0.0.AppImage", b"a", "1.0.0")
        newer = self.source("A-latest.AppImage", b"b")
        with patch("appglider.install.library.read_identity", return_value=self.IDENTITY):
            self.library.replace_file(app, newer, "", identify=True)
        self.assertEqual(self.library.records()[app.id].version, "3.4.5")


class GuiBase(manual.Base):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setStyle("Fusion")

    def setUp(self) -> None:
        super().setUp()
        self.settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
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

    def choose(self, source: Path, info=None) -> None:
        page = self.window.add_page
        self.window.open_add_app()
        with (
            patch(
                "appglider.ui.pages.add_page.describe_appimage",
                return_value=info or manual.details(),
            ),
            patch(
                "appglider.ui.pages.add_page.QFileDialog.getOpenFileName",
                return_value=(str(source), ""),
            ),
        ):
            page.choose_file()
            self.wait(lambda: page.task is None)


class NoteTests(GuiBase):
    def test_the_fallback_note_is_shown_and_the_read_happens_on_add(self) -> None:
        page = self.window.add_page
        info = manual.details(note=NO_READER_NOTE)
        self.choose(self.source(), info)
        self.assertIn(NO_READER_NOTE, page.checksum.text())
        self.assertIn("once the file is added", NO_READER_NOTE)
        self.assertIn("once the file is added", UNREADABLE_NOTE)
        with patch.object(self.library, "add_file", wraps=self.library.add_file) as add:
            page.add_button.click()
            self.wait(lambda: page.task is None)
        self.assertTrue(add.call_args.kwargs["identify"])

    def test_no_note_when_the_details_were_read(self) -> None:
        page = self.window.add_page
        self.choose(self.source(), manual.details())
        self.assertNotIn("squashfs", page.checksum.text())
        with patch.object(self.library, "add_file", wraps=self.library.add_file) as add:
            page.add_button.click()
            self.wait(lambda: page.task is None)
        self.assertFalse(add.call_args.kwargs["identify"])

    def test_the_add_receives_the_tasks_cancel_event(self) -> None:
        page = self.window.add_page
        self.choose(self.source())
        with patch.object(self.library, "add_file", wraps=self.library.add_file) as add:
            page.add_button.click()
            self.wait(lambda: page.task is None)
        self.assertIsInstance(add.call_args.args[4], Event)

    def test_replace_passes_cancel_and_identify_for_a_fallback_file(self) -> None:
        app, _ = self.add()
        self.window.scan_installed()
        newer = self.source("Sketchpad-1.3.0-x86_64.AppImage", b"two")
        with (
            patch(
                "appglider.ui.window.describe_appimage",
                return_value=manual.details(version="1.3.0", note=UNREADABLE_NOTE),
            ),
            patch("appglider.ui.window.QFileDialog.getOpenFileName", return_value=(str(newer), "")),
            patch.object(self.library, "replace_file", wraps=self.library.replace_file) as replace,
        ):
            self.window.replace_file(app.id)
            self.wait(lambda: self.window.task is None)
        self.assertIsInstance(replace.call_args.args[3], Event)
        self.assertTrue(replace.call_args.kwargs["identify"])


class SelfUpdateFailureTests(GuiBase):
    def test_a_failed_self_update_download_shows_a_warning_dialog(self) -> None:
        window = self.window
        window.self_state = replace(window.self_state, tag="v9.9.9")
        image = self.root / "AppGlider-0.9.8-x86_64.AppImage"
        with (
            patch("appglider.ui.window.self_update.running_image", return_value=image),
            patch("appglider.ui.window.self_update.can_replace", return_value=True),
            patch(
                "appglider.ui.window.self_update.download_update",
                side_effect=ValueError("The checksum did not match."),
            ),
            patch("appglider.ui.window.MessageBox.warning") as warning,
        ):
            window.update_self()
            self.wait(lambda: window.task is None)
        self.assertEqual(
            warning.call_args.args[1:], ("Couldn't update AppGlider", "The checksum did not match.")
        )


if __name__ == "__main__":
    unittest.main()
