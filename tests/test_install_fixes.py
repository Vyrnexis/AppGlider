"""Regression tests for the install layer: previewing a file, folders, rollback and records."""

import os
import stat
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from catalog_fixture import stock
from PySide6.QtCore import QSettings
from test_library import appimage_bytes

from appglider.core.catalog import manual_app, valid_file_name
from appglider.github.versions import VERSION
from appglider.install import self_update
from appglider.install.library import Library
from appglider.install.place import (
    NO_READER_NOTE,
    describe_appimage,
    place_appimage,
    squashfs_offset,
)

FAKE_TOOL = """#!/bin/sh
echo "$@" >> "$0.log"
case " $* " in
*" -l "*) printf 'squashfs-root\\nsquashfs-root/fancy.desktop\\nsquashfs-root/fancy.png\\n';;
*)
  prev=; for last; do [ "$prev" = -d ] && dest=$last; prev=$last; done
  mkdir -p "$dest"
  case "$last" in
  fancy.desktop) printf '[Desktop Entry]\\nName=Fancy\\nIcon=fancy\\nX-AppImage-Version=3.4.5\\n' \\
    > "$dest/fancy.desktop";;
  fancy.png) printf PNGDATA > "$dest/fancy.png";;
  esac;;
esac
"""


def elf_with_squashfs(extra: bytes = b"") -> bytes:
    """A type 2 header with section headers ending at byte 228, where the image starts."""
    header = bytearray(appimage_bytes(b"")) + bytes(44)
    header[0x28:0x30] = (100).to_bytes(8, "little")
    header[0x3A:0x3C] = (64).to_bytes(2, "little")
    header[0x3C:0x3E] = (2).to_bytes(2, "little")
    return bytes(header) + bytes(228 - len(header)) + b"hsqs" + extra


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = stock(Library(self.root / "data", self.root / "apps", self.root / "desktop"))
        self.downloads = self.root / "Downloads"
        self.downloads.mkdir()

    def source(self, name: str, payload: bytes = b"one") -> Path:
        path = self.downloads / name
        path.write_bytes(appimage_bytes(payload))
        return path

    def add(self, name: str = "Sketchpad-1.2.0.AppImage", payload: bytes = b"one", version="1.2.0"):
        source = self.source(name, payload)
        app = manual_app("Sketchpad", source.name)
        self.library.add_file(app, source, version, None)
        return app


class DescribeTests(Base):
    def test_a_picked_file_is_read_without_running_or_changing_it(self) -> None:
        image = self.downloads / "Fancy-3.4.5-x86_64.AppImage"
        image.write_bytes(elf_with_squashfs())
        image.chmod(0o644)
        tool = self.root / "unsquashfs"
        tool.write_text(FAKE_TOOL)
        tool.chmod(0o755)
        self.assertEqual(squashfs_offset(image), 228)
        before = sorted(os.listdir(self.downloads))
        with (
            patch("appglider.install.place.shutil.which", return_value=str(tool)),
            patch("appglider.install.place.normalise_icon", side_effect=lambda data: data),
        ):
            info = describe_appimage(image)
        self.assertEqual(
            (info.name, info.version, info.icon, info.note), ("Fancy", "3.4.5", b"PNGDATA", "")
        )
        self.assertEqual(stat.S_IMODE(image.stat().st_mode), 0o644)  # not made executable
        self.assertEqual(sorted(os.listdir(self.downloads)), before)
        log = Path(f"{tool}.log").read_text()
        self.assertIn("-o 228", log)
        self.assertNotIn("--appimage-extract", log)

    def test_without_a_reader_the_preview_comes_from_the_file_name_and_says_so(self) -> None:
        image = self.source("Cool-App-1.2.0-x86_64.AppImage")
        with patch("appglider.install.place.shutil.which", return_value=None):
            info = describe_appimage(image)
        self.assertEqual((info.name, info.version, info.icon), ("Cool App", "1.2.0", None))
        self.assertEqual(info.note, NO_READER_NOTE)
        self.assertFalse(os.access(image, os.X_OK))


class FolderNameTests(Base):
    def test_a_version_with_build_metadata_still_gets_an_owned_folder(self) -> None:
        first = self.add("One-1.0.0.AppImage", b"a", "1.0.0+build5")
        record = self.library.records()[first.id]
        # A second file of the same version needs a distinguishing folder.
        folder = Library._manual_folder(record.path.parent.parent, "1.0.0+build5", "ab" * 32)
        self.assertRegex(folder.name, f"^{VERSION}$")
        self.assertEqual(folder.name, "1.0.0+build5.abababab")
        self.library._owned(first, folder / "x.AppImage")  # the marker check accepts it
        plain = Library._manual_folder(record.path.parent.parent, "1.0.0", "cd" * 32)
        self.assertEqual(plain.name, "1.0.0")
        taken = Library._manual_folder(record.path.parent.parent, "", "cd" * 32)
        self.assertRegex(taken.name, f"^{VERSION}$")

    def test_replacing_with_the_same_version_keeps_the_file_owned(self) -> None:
        app = self.add("A-1.0.0.AppImage", b"a", "1.0.0+x")
        newer = self.source("A-1.0.0b.AppImage", b"b")
        self.library.replace_file(app, newer, "1.0.0+x")
        record = self.library.records()[app.id]
        self.library._owned(app, record.path)
        self.assertRegex(record.path.parent.name, f"^{VERSION}$")


class AddFileCommitTests(Base):
    def test_a_failing_shortcut_keeps_the_committed_add_and_warns(self) -> None:
        source = self.source("Sketchpad-1.2.0.AppImage")
        app = manual_app("Sketchpad", source.name)
        with patch.object(Library, "_shortcut", side_effect=ValueError("no menu")):
            warning = self.library.add_file(app, source, "1.2.0", None)
        self.assertIn("shortcut", warning)
        record = self.library.records()[app.id]
        self.assertTrue(record.path.is_file())
        self.assertIn(app, self.library.apps)

    def test_a_late_failure_after_the_saves_does_not_delete_the_file(self) -> None:
        source = self.source("Sketchpad-1.2.0.AppImage")
        app = manual_app("Sketchpad", source.name)
        with patch.object(Library, "update_desktop_database", side_effect=OSError("db")):
            self.library.add_file(app, source, "1.2.0", None)
        self.assertTrue(self.library.records()[app.id].path.is_file())

    def test_a_failure_before_the_saves_leaves_nothing(self) -> None:
        source = self.source("Sketchpad-1.2.0.AppImage")
        app = manual_app("Sketchpad", source.name)
        with patch.object(Library, "_save", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.library.add_file(app, source, "1.2.0", None)
        self.assertNotIn(app, self.library.apps)
        self.assertFalse((self.library.apps_dir / app.id).exists())


class SelfUpdateRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = QSettings(str(self.root / "s.ini"), QSettings.Format.IniFormat)
        self.first = self.root / "one" / "AppGlider-0.9.7-x86_64.AppImage"
        self.update = self.root / "one" / "AppGlider-0.9.8-x86_64.AppImage"
        self.moved = self.root / "two" / "AppGlider-0.9.7-x86_64.AppImage"
        for path in (self.first, self.update, self.moved):
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(appimage_bytes(path.name.encode()))

    def test_a_move_after_a_pending_update_removes_the_unused_download(self) -> None:
        done = self_update.Downloaded(self.first, self.update, "0.9.8")
        self_update.record(self.settings, done)
        with patch("appglider.install.self_update.library.running", return_value=False):
            self_update.record_move(self.settings, self.first, self.moved)
        self.assertFalse(self.update.exists())
        self.assertEqual(self.settings.value(self_update.READY_KEY), str(self.moved))
        self.assertTrue(self.settings.value(self_update.MOVED_KEY, False, type=bool))

    def test_an_update_after_a_move_removes_the_file_left_behind_and_clears_the_flag(self) -> None:
        self_update.record_move(self.settings, self.first, self.moved)
        done = self_update.Downloaded(self.moved, self.update, "0.9.8")
        with patch("appglider.install.self_update.library.running", return_value=False):
            self_update.record(self.settings, done)
        self.assertFalse(self.first.exists())
        self.assertFalse(self.settings.contains(self_update.MOVED_KEY))
        # With the flag gone, a file in another folder is no longer eligible for deletion.
        self.assertFalse(
            self_update.eligible(
                self.first, self.moved, bool(self.settings.contains(self_update.MOVED_KEY))
            )
        )


class ForgetAndRollbackTests(Base):
    def test_forgetting_a_missing_manual_app_removes_its_owned_folder(self) -> None:
        app = self.add("A-1.0.0.AppImage", b"a", "1.0.0")
        newer = self.source("A-1.1.0.AppImage", b"b")
        self.library.replace_file(app, newer, "1.1.0")
        record = self.library.records()[app.id]
        root = record.path.parent.parent
        record.path.unlink()
        self.library.forget_missing(app.id)
        self.assertFalse(root.exists())
        self.assertNotIn(app.id, self.library.records())

    def test_rollback_updates_the_file_name_the_app_shows(self) -> None:
        app = self.add("A-1.0.0.AppImage", b"a", "1.0.0")
        self.library.replace_file(app, self.source("A-1.1.0.AppImage", b"b"), "1.1.0")
        self.assertEqual(
            next(a for a in self.library.apps if a.id == app.id).file, "A-1.1.0.AppImage"
        )
        self.library.rollback(next(a for a in self.library.apps if a.id == app.id))
        shown = next(a for a in self.library.apps if a.id == app.id)
        self.assertEqual(shown.file, "A-1.0.0.AppImage")
        self.assertEqual(self.library.records()[app.id].path.name, shown.file)
        self.assertEqual(replace(shown, file=shown.file), shown)


class StageNameTests(Base):
    def test_a_longest_valid_name_can_be_placed(self) -> None:
        name = "a" * (255 - len(".AppImage")) + ".AppImage"
        self.assertTrue(valid_file_name(name))
        source = self.source(name)
        placed = place_appimage(source, self.root / "dest")
        self.assertTrue(placed.is_file())
        self.assertEqual(os.listdir(self.root / "dest"), [name])

    def test_names_over_255_bytes_are_not_valid(self) -> None:
        name = "é" * 130 + ".AppImage"  # 130 characters, 269 bytes
        self.assertFalse(valid_file_name(name))


if __name__ == "__main__":
    unittest.main()
