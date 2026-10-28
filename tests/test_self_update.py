"""AppGlider's check for its own new releases: the pure rules, then the window around them."""

import hashlib
import io
import json
import os
import tempfile
import time
import unittest
from collections import namedtuple
from contextlib import ExitStack
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from catalog_fixture import stock
from gui_helpers import close_window
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_library import APP, appimage_bytes, release_for

from appglider import APP_VERSION
from appglider import app as app_module
from appglider.github.assets import Release
from appglider.github.versions import Cancelled, check_cancel
from appglider.install import self_update
from appglider.install.library import Library
from appglider.install.self_update import CheckFailed, SelfUpdate
from appglider.instance import SingleInstance
from appglider.ui.jobs import TaskResult
from appglider.ui.window import LauncherWindow

URL = "https://github.com/Vyrnexis/AppGlider/releases/tag/"
ASSET = "https://github.com/Vyrnexis/AppGlider/releases/download/"


def appglider_release(
    version: str = "0.9.1",
    payload: bytes | None = None,
    name: str | None = None,
    prerelease: bool = False,
    digest: str | None = None,
) -> tuple[Release, bytes]:
    """A release of AppGlider itself and the bytes its file would download as."""
    data = appimage_bytes(b"new appglider") if payload is None else payload
    file = name or f"AppGlider-{version}-x86_64.AppImage"
    release = Release(
        version,
        file,
        f"{ASSET}v{version}/{file}",
        len(data),
        digest or hashlib.sha256(data).hexdigest(),
        prerelease,
    )
    return release, data


def leftovers(folder: Path, keep: Path) -> list[str]:
    """Names of every AppGlider file in a folder other than the one that should be there."""
    return sorted(
        entry.name
        for entry in folder.iterdir()
        if entry.name.casefold().lstrip(".").startswith("appglider") and entry != keep
    )


@patch("appglider.install.self_update.APP_VERSION", "0.8.1")
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
        with patch("appglider.install.self_update.APP_VERSION", "development"):
            self.assertIsNone(self_update.newer_version("v9.9.9"))

    def test_check_returns_the_original_tag_only_when_newer(self) -> None:
        with patch("appglider.install.self_update.latest_tag", return_value="v0.9.1") as latest:
            cancel = Event()
            self.assertEqual(self_update.check(cancel), "v0.9.1")
            latest.assert_called_once_with("Vyrnexis/AppGlider", cancel)
        for tag in ("v0.8.1", "v0.7.0"):
            with patch("appglider.install.self_update.latest_tag", return_value=tag):
                self.assertIsNone(self_update.check(Event()))

    def test_check_never_falls_back_to_the_api(self) -> None:
        with (
            patch("appglider.install.self_update.latest_tag", return_value=None),
            patch("appglider.github.http.request") as request,
            patch("appglider.github.download.latest_release") as api,
        ):
            with self.assertRaises(CheckFailed):
                self_update.check(Event())
            request.assert_not_called()
            api.assert_not_called()

    def test_a_cancelled_check_raises_instead_of_answering(self) -> None:
        cancel = Event()
        cancel.set()
        with patch("appglider.github.http.redirect_location") as redirect:
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
        from appglider.core.catalog import KNOWN_IDS, custom_app

        self.assertNotIn(self_update.NOTIFY_KEY, KNOWN_IDS.values())
        self.assertIn(":", self_update.NOTIFY_KEY)
        self.assertNotIn(":", custom_app("owner/repo").id)


class ImageTests(unittest.TestCase):
    def test_running_image_needs_the_appimage_and_a_python_inside_its_mount(self) -> None:
        with (
            patch.dict(os.environ, {"APPDIR": "/tmp/mount", "APPIMAGE": "/home/a b/G.AppImage"}),
            patch("appglider.install.self_update.sys.prefix", "/tmp/mount/opt/python"),
        ):
            self.assertEqual(self_update.running_image(), Path("/home/a b/G.AppImage"))

    def test_other_ways_of_starting_have_no_image(self) -> None:
        with (
            patch.dict(os.environ, {"APPDIR": "/tmp/other", "APPIMAGE": "/other.AppImage"}),
            patch("appglider.install.self_update.sys.prefix", "/project/.venv"),
        ):
            self.assertIsNone(self_update.running_image())
        with (
            patch.dict(os.environ, {"APPDIR": "/tmp/mount"}, clear=True),
            patch("appglider.install.self_update.sys.prefix", "/tmp/mount/opt/python"),
        ):
            self.assertIsNone(self_update.running_image())
        with (
            patch.dict(os.environ, {"APPIMAGE": "/a.AppImage"}, clear=True),
            patch("appglider.install.self_update.sys.prefix", "/tmp/mount/opt/python"),
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


@patch("appglider.install.self_update.APP_VERSION", "0.8.1")
class DownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "apps"
        self.folder.mkdir()
        self.image = self.folder / "AppGlider.AppImage"
        self.image.write_bytes(appimage_bytes(b"running"))
        self.image.chmod(0o755)
        self.before = (self.image.read_bytes(), self.image.stat().st_mtime_ns)
        self.messages: list[tuple[str, int, int]] = []
        self.cancel = Event()

    def fetch(
        self, release_and_data: tuple[Release, bytes], tag: str = "v0.9.1"
    ) -> self_update.Downloaded:
        release, data = release_and_data
        with (
            patch("appglider.install.self_update.latest_release", return_value=release) as latest,
            patch("appglider.github.http.request", return_value=io.BytesIO(data)),
        ):
            try:
                return self_update.download_update(
                    self.image,
                    tag,
                    self.folder / "cache",
                    self.cancel,
                    lambda *args: self.messages.append(args),
                )
            finally:
                self.latest = latest

    def assert_untouched(self) -> None:
        self.assertEqual((self.image.read_bytes(), self.image.stat().st_mtime_ns), self.before)
        self.assertEqual(leftovers(self.folder, self.image), [])

    def test_the_file_lands_next_to_the_running_image_verified_and_executable(self) -> None:
        release, data = appglider_release()
        done = self.fetch((release, data))
        target = self.folder / "AppGlider-0.9.1-x86_64.AppImage"
        self.assertEqual(done, self_update.Downloaded(self.image, target, "0.9.1"))
        self.assertEqual(target.read_bytes(), data)
        self.assertEqual(target.stat().st_mode & 0o777, 0o755)
        self.assertEqual(leftovers(self.folder, self.image), [target.name])
        self.assertEqual(self.image.read_bytes(), self.before[0])
        self.assertTrue(self.messages)
        self.assertEqual(
            {message for message, *_ in self.messages}, {"Downloading AppGlider 0.9.1…"}
        )
        app, _cancel, cache = self.latest.call_args.args
        self.assertEqual(app.repo, "vyrnexis/appglider")
        self.assertFalse(app.include_prereleases)
        self.assertEqual(cache, self.folder / "cache")

    def test_the_release_keeps_its_own_name_when_it_follows_the_pattern(self) -> None:
        for name, expected in (
            ("appglider-0.9.1-X86_64.appimage", "appglider-0.9.1-X86_64.appimage"),
            ("AppGlider-0.9.1.AppImage", "AppGlider-0.9.1.AppImage"),
            ("Something-else.AppImage", "AppGlider-0.9.1-x86_64.AppImage"),
        ):
            with self.subTest(name=name):
                release, data = appglider_release(name=name)
                self.assertEqual(self.fetch((release, data)).new.name, expected)
                (self.folder / expected).unlink()

    def test_a_wrong_checksum_leaves_nothing_behind(self) -> None:
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.fetch(appglider_release(digest="0" * 64))
        self.assert_untouched()

    def test_a_download_without_an_appimage_header_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "AppImage"):
            self.fetch(appglider_release(payload=b"not an appimage at all, just text"))
        self.assert_untouched()
        header = bytearray(appimage_bytes())
        header[18:20] = (1).to_bytes(2, "little")  # another CPU
        with self.assertRaisesRegex(ValueError, "architecture"):
            self.fetch(appglider_release(payload=bytes(header)))
        self.assert_untouched()

    def test_a_published_checksum_is_required(self) -> None:
        release, data = appglider_release()
        release = Release(**{**release.__dict__, "sha256": ""})
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.fetch((release, data))
        self.assert_untouched()

    def test_a_size_that_differs_from_the_published_one_is_refused(self) -> None:
        release, data = appglider_release()
        with self.assertRaises(ValueError):
            self.fetch((release, data + b"extra"))
        self.assert_untouched()

    def test_a_cancel_leaves_nothing_behind(self) -> None:
        self.cancel.set()
        with self.assertRaises(Cancelled):
            self.fetch(appglider_release())
        self.assert_untouched()

    def test_a_cancel_after_the_last_byte_still_leaves_nothing(self) -> None:
        release, data = appglider_release()

        def verified(path: Path) -> None:
            self.cancel.set()

        with patch("appglider.install.appimage.check_appimage", side_effect=verified):
            with self.assertRaises(Cancelled):
                self.fetch((release, data))
        self.assert_untouched()

    def test_only_a_newer_stable_tag_is_ever_downloaded(self) -> None:
        for tag in ("v0.8.1", "0.8.1", "v0.8.0", "v0.9.0-beta.1", "garbage", ""):
            with self.subTest(tag=tag):
                with self.assertRaisesRegex(ValueError, "newer stable"):
                    self.fetch(appglider_release(), tag=tag)
                self.latest.assert_not_called()
                self.assert_untouched()

    def test_the_release_must_be_the_one_the_check_found(self) -> None:
        with self.assertRaisesRegex(ValueError, "0.9.2 is now the latest"):
            self.fetch(appglider_release("0.9.2"), tag="v0.9.1")
        self.assert_untouched()
        with self.assertRaisesRegex(ValueError, "0.9.1 is now the latest"):
            self.fetch(appglider_release("0.9.1", prerelease=True), tag="v0.9.1")
        self.assert_untouched()

    def test_an_existing_file_of_that_name_is_replaced_only_after_verification(self) -> None:
        target = self.folder / "AppGlider-0.9.1-x86_64.AppImage"
        target.write_bytes(b"stale partial copy")
        with self.assertRaises(ValueError):
            self.fetch(appglider_release(digest="1" * 64))
        self.assertEqual(target.read_bytes(), b"stale partial copy")
        with self.assertRaises(ValueError):
            self.fetch(appglider_release(payload=b"junk that is not an appimage"))
        self.assertEqual(target.read_bytes(), b"stale partial copy")
        self.assertEqual(leftovers(self.folder, self.image), [target.name])
        release, data = appglider_release()
        self.assertEqual(self.fetch((release, data)).new.read_bytes(), data)
        self.assertEqual(leftovers(self.folder, self.image), [target.name])

    def test_a_symbolic_link_or_folder_of_that_name_is_left_alone(self) -> None:
        elsewhere = self.folder / "elsewhere"
        elsewhere.write_bytes(b"precious")
        link = self.folder / "AppGlider-0.9.1-x86_64.AppImage"
        link.symlink_to(elsewhere)
        with self.assertRaisesRegex(ValueError, "plain file"):
            self.fetch(appglider_release())
        self.assertEqual(elsewhere.read_bytes(), b"precious")
        self.assertTrue(link.is_symlink())
        link.unlink()
        link.mkdir()
        with self.assertRaisesRegex(ValueError, "plain file"):
            self.fetch(appglider_release())
        link.rmdir()
        self.assert_untouched()

    def test_the_running_image_is_never_the_target(self) -> None:
        self.image.unlink()
        running = self.folder / "AppGlider-0.9.1-x86_64.AppImage"
        running.write_bytes(appimage_bytes(b"running"))
        self.image = running
        self.before = (running.read_bytes(), running.stat().st_mtime_ns)
        with self.assertRaisesRegex(ValueError, "already called"):
            self.fetch(appglider_release())
        self.assert_untouched()

    def test_not_enough_free_space_stops_before_anything_is_written(self) -> None:
        usage = namedtuple("usage", "total used free")
        with patch(
            "appglider.install.self_update.shutil.disk_usage", return_value=usage(10, 10, 10)
        ):
            with self.assertRaisesRegex(ValueError, "free disk space"):
                self.fetch(appglider_release())
        self.assert_untouched()

    def test_progress_and_cancel_reach_the_download(self) -> None:
        release, data = appglider_release()

        def interrupted(release: Release, path: Path, cancel: Event, progress: object) -> None:
            path.write_bytes(b"partial")
            self.cancel.set()
            check_cancel(cancel)

        with patch("appglider.install.self_update.download", side_effect=interrupted):
            with (
                patch("appglider.install.self_update.latest_release", return_value=release),
                self.assertRaises(Cancelled),
            ):
                self_update.download_update(
                    self.image, "v0.9.1", self.folder, self.cancel, lambda *args: None
                )
        self.assert_untouched()


class CleanupTests(unittest.TestCase):
    NEW = "AppGlider-0.9.1-x86_64.AppImage"
    OLD = "AppGlider-0.8.1-x86_64.AppImage"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "apps"
        self.folder.mkdir()
        self.settings = QSettings(str(self.root / "s.ini"), QSettings.Format.IniFormat)
        self.image = self.make(self.NEW, b"new")
        self.old = self.make(self.OLD, b"old")
        self.state = {"running": False, "image": self.image}
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(
            patch(
                "appglider.install.self_update.running_image",
                side_effect=lambda: self.state["image"],
            )
        )
        self.is_running = stack.enter_context(
            patch(
                "appglider.install.self_update.library.running",
                side_effect=lambda p: self.state["running"],
            )
        )
        self.record(self.old, self.image)

    def make(self, name: str, extra: bytes, folder: Path | None = None) -> Path:
        path = (folder or self.folder) / name
        path.write_bytes(appimage_bytes(extra))
        return path

    def record(self, old: Path | str, ready: Path | str, version: str = "0.9.1") -> None:
        self.settings.setValue(self_update.REPLACED_KEY, str(old))
        self.settings.setValue(self_update.READY_KEY, str(ready))
        self.settings.setValue(self_update.VERSION_KEY, version)

    def keys(self) -> dict[str, str]:
        return {
            key: str(self.settings.value(key))
            for key in (self_update.REPLACED_KEY, self_update.READY_KEY, self_update.VERSION_KEY)
            if self.settings.contains(key)
        }

    def refused(self, old: Path) -> None:
        self.record(old, self.image)
        self_update.cleanup(self.settings)
        self.assertTrue(old.is_symlink() or old.exists(), f"{old} was deleted")
        self.assertEqual(self.keys(), {})  # the new version is running, so the records are done

    def test_the_replaced_file_is_deleted_once_the_new_one_runs(self) -> None:
        self_update.cleanup(self.settings)
        self.assertFalse(self.old.exists())
        self.assertTrue(self.image.exists())
        self.assertEqual(self.keys(), {})
        self.assertEqual([p.name for p in self.folder.iterdir()], [self.NEW])

    def test_a_name_in_other_case_still_counts(self) -> None:
        old = self.make("appglider-0.7.0-X86_64.APPIMAGE", b"older")
        self.record(old, self.image)
        self_update.cleanup(self.settings)
        self.assertFalse(old.exists())

    def test_a_file_in_another_folder_is_kept(self) -> None:
        other = self.root / "other"
        other.mkdir()
        self.refused(self.make(self.OLD, b"elsewhere", other))

    def test_a_file_that_is_not_named_like_a_release_is_kept(self) -> None:
        for name in (
            "notes.AppImage",
            "AppGlider.AppImage",
            "AppGlider-0.8.1.txt",
            "Other-1.AppImage",
        ):
            with self.subTest(name=name):
                self.refused(self.make(name, b"mine"))

    def test_a_symbolic_link_is_kept_with_its_target(self) -> None:
        target = self.make("AppGlider-0.1.0-x86_64.AppImage", b"target")
        link = self.folder / self.OLD
        self.old.unlink()
        link.symlink_to(target)
        self.refused(link)
        self.assertTrue(target.exists())

    def test_a_file_that_is_not_an_appimage_is_kept(self) -> None:
        self.old.write_bytes(b"my own notes, saved under that name")
        self.refused(self.old)
        self.assertEqual(self.old.read_bytes(), b"my own notes, saved under that name")

    def test_a_folder_is_kept(self) -> None:
        self.old.unlink()
        self.old.mkdir()
        self.refused(self.old)

    def test_a_file_that_is_still_running_is_kept_and_remembered(self) -> None:
        other = self.make("AppGlider-0.9.0-x86_64.AppImage", b"other")
        self.state["running"] = True
        self.record(self.old, other)  # the recorded new file is not the one running now
        self_update.cleanup(self.settings)
        self.assertTrue(self.old.exists())
        self.assertEqual(self.keys()[self_update.REPLACED_KEY], str(self.old))
        self.is_running.assert_called_once_with(self.old)
        self.state["running"] = False
        self_update.cleanup(self.settings)  # the next start
        self.assertFalse(self.old.exists())
        self.assertEqual(self.keys().get(self_update.REPLACED_KEY), None)

    def test_after_a_restart_the_old_file_still_closing_is_removed_on_a_later_try(self) -> None:
        # The real restart: the new version runs while the old one is still shutting down.
        self.state["running"] = True
        self_update.cleanup(self.settings)
        self.assertTrue(self.old.exists())
        self.assertEqual(self.keys(), {self_update.REPLACED_KEY: str(self.old)})
        self.state["running"] = False
        self_update.cleanup(self.settings)
        self.assertFalse(self.old.exists())
        self.assertEqual(self.keys(), {})

    def test_the_running_image_itself_is_never_deleted(self) -> None:
        self.refused(self.image)
        self.assertTrue(self.image.exists())

    def test_without_an_appimage_nothing_is_touched(self) -> None:
        self.state["image"] = None
        before = self.keys()
        self_update.cleanup(self.settings)
        self.assertTrue(self.old.exists())
        self.assertEqual(self.keys(), before)

    def test_a_vanished_old_file_clears_its_record(self) -> None:
        other = self.make("AppGlider-0.9.0-x86_64.AppImage", b"other")
        self.record(self.old, other)
        self.old.unlink()
        self_update.cleanup(self.settings)
        self.assertNotIn(self_update.REPLACED_KEY, self.keys())
        self.assertIn(self_update.READY_KEY, self.keys())

    def test_a_vanished_new_file_forgets_everything_and_deletes_nothing(self) -> None:
        ready = self.folder / "AppGlider-0.9.9-x86_64.AppImage"
        self.record(self.old, ready)
        self_update.cleanup(self.settings)
        self.assertTrue(self.old.exists())
        self.assertEqual(self.keys(), {})

    def test_other_files_are_never_deleted(self) -> None:
        extras = [
            self.make("AppGlider-0.7.0-x86_64.AppImage", b"user's older download"),
            self.make("Other.AppImage", b"unrelated"),
        ]
        self_update.cleanup(self.settings)
        self.assertFalse(self.old.exists())
        self.assertTrue(all(extra.exists() for extra in extras))

    def test_nothing_recorded_means_nothing_happens(self) -> None:
        self_update.forget(self.settings)
        self_update.cleanup(self.settings)
        self.assertTrue(self.old.exists())

    def test_startup_skips_the_cleanup_in_smoke_mode(self) -> None:
        with (
            patch("appglider.app.cleanup") as cleanup,
            patch("appglider.app.QSettings") as settings,
        ):
            app_module.remove_replaced_image(True)
            cleanup.assert_not_called()
            settings.assert_not_called()
            app_module.remove_replaced_image(False)
            cleanup.assert_called_once_with(settings.return_value)


@patch("appglider.install.self_update.APP_VERSION", "0.8.1")
class ReadyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = QSettings(str(self.root / "s.ini"), QSettings.Format.IniFormat)
        self.image = self.root / "AppGlider-0.8.1-x86_64.AppImage"
        self.image.write_bytes(appimage_bytes())
        self.new = self.root / "AppGlider-0.9.1-x86_64.AppImage"
        self.new.write_bytes(appimage_bytes(b"new"))
        self.settings.setValue(self_update.REPLACED_KEY, str(self.image))
        self.settings.setValue(self_update.READY_KEY, str(self.new))
        self.settings.setValue(self_update.VERSION_KEY, "0.9.1")
        patcher = patch("appglider.install.self_update.running_image", return_value=self.image)
        self.running = patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_downloaded_newer_file_is_ready(self) -> None:
        self.assertEqual(
            self_update.ready_update(self.settings), self_update.Ready(self.new, "0.9.1")
        )

    def test_a_vanished_file_clears_the_records(self) -> None:
        self.new.unlink()
        self.assertIsNone(self_update.ready_update(self.settings))
        for key in (self_update.READY_KEY, self_update.REPLACED_KEY, self_update.VERSION_KEY):
            self.assertFalse(self.settings.contains(key))

    def test_a_file_that_is_no_longer_an_appimage_or_newer_is_not_offered(self) -> None:
        self.new.write_bytes(b"garbage")
        self.assertIsNone(self_update.ready_update(self.settings))
        self.assertFalse(self.settings.contains(self_update.READY_KEY))
        self.new.write_bytes(appimage_bytes())
        self_update.record(self.settings, self_update.Downloaded(self.image, self.new, "0.8.1"))
        self.assertIsNone(self_update.ready_update(self.settings))

    def test_the_running_file_is_not_its_own_update(self) -> None:
        self.running.return_value = self.new
        self.assertIsNone(self_update.ready_update(self.settings))

    def test_without_an_appimage_nothing_is_offered_and_nothing_is_cleared(self) -> None:
        self.running.return_value = None
        self.assertIsNone(self_update.ready_update(self.settings))
        self.assertTrue(self.settings.contains(self_update.READY_KEY))

    def test_hint(self) -> None:
        self.assertEqual(
            SelfUpdate(1.0, "v0.9.1").hint(self.image, "0.9.1"),
            "Version 0.9.1 is ready · restart to use it",
        )


class WaitingForTheLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.first = SingleInstance(Path(self.temp.name))
        self.second = SingleInstance(Path(self.temp.name))
        self.assertTrue(self.first.acquire())
        self.addCleanup(self.first.release)
        self.addCleanup(self.second.release)
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self, free_after: int | None = None):
        """A fake time module; the first instance lets go after that many sleeps."""
        fake = MagicMock()
        fake.monotonic.side_effect = lambda: self.now

        def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)
            self.now += seconds
            if free_after is not None and len(self.sleeps) == free_after:
                self.first.release()

        fake.sleep.side_effect = sleep
        return patch("appglider.instance.time", fake)

    def test_it_acquires_as_soon_as_the_old_instance_lets_go_without_waking_it(self) -> None:
        woken: list[bool] = []
        self.first.activated.connect(lambda: woken.append(True))
        with self.clock(free_after=3):
            self.assertTrue(self.second.acquire_after_update())
        self.assertEqual(len(self.sleeps), 3)
        self.assertTrue(all(0.05 <= seconds <= 0.25 for seconds in self.sleeps))
        QTest.qWait(100)
        self.assertEqual(woken, [])

    def test_after_the_timeout_it_wakes_the_running_instance_and_gives_up(self) -> None:
        with (
            self.clock(),
            patch.object(self.second, "_wake_existing") as wake,
        ):
            self.assertFalse(self.second.acquire_after_update())
        wake.assert_called_once_with()
        self.assertGreaterEqual(sum(self.sleeps), 14.5)
        self.assertLessEqual(sum(self.sleeps), 16)

    def test_a_quiet_start_does_not_wake_it_even_after_the_timeout(self) -> None:
        with self.clock(), patch.object(self.second, "_wake_existing") as wake:
            self.assertFalse(self.second.acquire_after_update(wake=False))
        wake.assert_not_called()

    def test_the_flag_chooses_the_waiting_acquire(self) -> None:
        instance = MagicMock()
        app_module.acquire_instance(instance, quiet=False, after_update=True)
        instance.acquire_after_update.assert_called_once_with(wake=True)
        instance.acquire.assert_not_called()
        instance = MagicMock()
        app_module.acquire_instance(instance, quiet=True, after_update=False)
        instance.acquire.assert_called_once_with(wake=False)
        instance.acquire_after_update.assert_not_called()


@patch("appglider.install.self_update.APP_VERSION", "0.8.1")
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
        stack.enter_context(
            patch("appglider.install.self_update.check", side_effect=self.fake_check)
        )
        self.running = stack.enter_context(
            patch("appglider.install.self_update.running_image", return_value=self.image)
        )
        self.opened: list[str] = []
        stack.enter_context(
            patch(
                "appglider.ui.window.QDesktopServices.openUrl",
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
        with patch("appglider.github.http.request", return_value=io.BytesIO(payload)):
            self.window.library.install(
                APP, release_for(version, payload), Event(), lambda *args: None
            )
        self.window.scan_installed()

    def notifier(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(
            patch("appglider.ui.window.QSystemTrayIcon.isSystemTrayAvailable", return_value=True)
        )
        stack.enter_context(patch.object(self.window.tray, "isVisible", return_value=True))
        stack.enter_context(patch.object(self.window, "isActiveWindow", return_value=False))
        return stack.enter_context(patch.object(self.window.tray, "showMessage"))

    def test_nothing_shows_before_a_check(self) -> None:
        window = self.window
        self.assertTrue(window.self_banner.isHidden())
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.hint(), "Not checked yet")
        self.assertEqual(window.settings_page.self_row.label.text(), f"AppGlider {APP_VERSION}")
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
        with patch("appglider.install.self_update.can_replace", return_value=False):
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
        with patch("appglider.ui.window.github_status.quota_low", return_value=True):
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

    # Updating AppGlider itself

    def update(self, release_and_data: tuple[Release, bytes], tag: str = "v0.9.1") -> None:
        """Offer the tag, press the banner button and wait for the task to finish."""
        release, data = release_and_data
        self.check(tag)
        with (
            patch("appglider.install.self_update.latest_release", return_value=release) as latest,
            patch("appglider.github.http.request", return_value=io.BytesIO(data)),
        ):
            self.latest = latest
            self.window.self_update_button.click()
            self.finish_task()

    def finish_task(self) -> None:
        deadline = time.monotonic() + 10
        while self.window.task is not None and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertIsNone(self.window.task, "The task did not finish")

    @property
    def new_image(self) -> Path:
        return self.image.parent / "AppGlider-0.9.1-x86_64.AppImage"

    @property
    def menu_entry(self) -> Path:
        return self.image.parent / "desktop" / "appglider.desktop"

    def assert_still_offered(self) -> None:
        window = self.window
        self.assertEqual(window.self_banner_label.text(), "<b>AppGlider 0.9.1</b> is available")
        self.assertEqual(window.self_update_button.text(), "Update AppGlider")
        self.assertTrue(window.self_update_button.isEnabled())
        self.assertTrue(window.self_banner.isVisibleTo(window.list_page))
        self.assertIsNone(window.self_ready)
        for key in (self_update.READY_KEY, self_update.REPLACED_KEY, self_update.VERSION_KEY):
            self.assertFalse(self.settings.contains(key))
        self.assertEqual(self.image.read_bytes(), b"x")
        self.assertEqual(leftovers(self.image.parent, self.image), [])
        self.assertFalse(self.menu_entry.exists())

    def test_updating_downloads_next_to_the_running_image_and_becomes_ready(self) -> None:
        window = self.window
        release, data = appglider_release()
        self.update((release, data))
        self.assertEqual(self.new_image.read_bytes(), data)
        self.assertEqual(self.new_image.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.image.read_bytes(), b"x")  # the running file is never touched
        self.assertEqual(leftovers(self.image.parent, self.image), [self.new_image.name])
        self.assertEqual(
            window.self_banner_label.text(),
            "<b>AppGlider 0.9.1</b> is ready. It starts the next time you open AppGlider.",
        )
        self.assertEqual(window.self_update_button.text(), "Restart now")
        self.assertNotEqual(window.self_update_button.objectName(), "primary")
        self.assertTrue(window.self_update_button.isEnabled())
        self.assertTrue(window.self_notes_button.isHidden())
        self.assertTrue(window.self_banner.isVisibleTo(window.list_page))
        self.assertEqual(self.hint(), "Version 0.9.1 is ready · restart to use it")
        self.assertIn("AppGlider 0.9.1 is ready", window.footer.text())
        self.assertEqual(self.settings.value(self_update.READY_KEY), str(self.new_image))
        self.assertEqual(self.settings.value(self_update.REPLACED_KEY), str(self.image))
        self.assertEqual(self.settings.value(self_update.VERSION_KEY), "0.9.1")
        lines = self.menu_entry.read_text().splitlines()
        self.assertIn("X-AppGlider=true", lines)
        self.assertIn(f'Exec="{self.new_image}"', lines)
        self.assertFalse((self.image.parent / "autostart" / "appglider.desktop").exists())
        self.assertFalse(window.progress_bar.isVisible())
        self.assertEqual(self.opened, [])

    def test_an_existing_login_entry_follows_the_update(self) -> None:
        window = self.window
        entry = window.library.enable_autostart([str(self.image)])
        self.update(appglider_release())
        lines = entry.read_text().splitlines()
        self.assertIn(f'Exec="{self.new_image}" "--tray"', lines)
        self.assertIn("X-AppGlider=true", lines)
        self.assertEqual(
            [p.name for p in (self.image.parent / "autostart").iterdir()], ["appglider.desktop"]
        )

    def test_a_foreign_login_entry_is_left_alone(self) -> None:
        entry = self.image.parent / "autostart" / "appglider.desktop"
        entry.parent.mkdir()
        entry.write_text("[Desktop Entry]\nExec=something-else\n")
        self.update(appglider_release())
        self.assertEqual(entry.read_text(), "[Desktop Entry]\nExec=something-else\n")
        self.assertEqual(self.window.self_update_button.text(), "Restart now")

    def test_a_foreign_menu_entry_is_left_alone_and_the_update_still_completes(self) -> None:
        self.menu_entry.parent.mkdir()
        self.menu_entry.write_text("[Desktop Entry]\nExec=theirs\n")
        self.update(appglider_release())
        self.assertEqual(self.menu_entry.read_text(), "[Desktop Entry]\nExec=theirs\n")
        self.assertIn("The menu shortcut was not updated", self.window.footer.text())
        self.assertEqual(self.window.self_update_button.text(), "Restart now")
        self.assertTrue(self.new_image.exists())

    def test_a_failed_update_changes_nothing_and_keeps_the_banner(self) -> None:
        bad_header = bytearray(appimage_bytes())
        bad_header[8:11] = b"AI\x01"
        cases = {
            "checksum": appglider_release(digest="2" * 64),
            "magic": appglider_release(payload=b"definitely not an appimage"),
            "type": appglider_release(payload=bytes(bad_header)),
            "newer": appglider_release("0.9.2"),
            "preview": appglider_release(prerelease=True),
        }
        for label, case in cases.items():
            with self.subTest(label):
                self.update(case)
                self.assert_still_offered()
                self.assertIn("Couldn't update AppGlider", self.window.footer.text())
                self.assertEqual(self.latest.call_count, 1)

    def test_a_cancelled_update_leaves_nothing_and_can_be_tried_again(self) -> None:
        window = self.window
        self.check("v0.9.1")
        release, data = appglider_release()
        started = Event()

        def slow(release: Release, path: Path, cancel: Event, progress: object) -> None:
            path.write_bytes(b"partial")
            started.set()
            cancel.wait(10)
            check_cancel(cancel)

        with (
            patch("appglider.install.self_update.latest_release", return_value=release),
            patch("appglider.install.self_update.download", side_effect=slow),
        ):
            window.self_update_button.click()
            self.assertEqual(window.footer.text(), "Downloading AppGlider 0.9.1…")
            self.assertTrue(window.cancel_button.isVisibleTo(window))
            deadline = time.monotonic() + 10
            while not started.is_set() and time.monotonic() < deadline:
                QTest.qWait(10)
            self.assertTrue(started.is_set())
            self.assertEqual(len(leftovers(self.image.parent, self.image)), 1)  # the hidden stage
            window.cancel_task()
            self.finish_task()
        self.assert_still_offered()
        self.assertFalse(window.cancel_button.isVisible())
        with (
            patch("appglider.install.self_update.latest_release", return_value=release),
            patch("appglider.github.http.request", return_value=io.BytesIO(data)),
        ):
            window.self_update_button.click()
            self.finish_task()
        self.assertEqual(window.self_update_button.text(), "Restart now")

    def test_the_download_shows_progress_in_the_footer(self) -> None:
        window = self.window
        self.check("v0.9.1")
        release, data = appglider_release()
        seen: list[str] = []
        window.task_progress = lambda message, *rest: seen.append(message)  # type: ignore[method-assign]
        with (
            patch("appglider.install.self_update.latest_release", return_value=release),
            patch("appglider.github.http.request", return_value=io.BytesIO(data)),
        ):
            window.self_update_button.click()
            self.finish_task()
        self.assertIn("Downloading AppGlider 0.9.1…", seen)

    def test_without_a_replaceable_image_the_button_only_opens_the_page(self) -> None:
        self.check("v0.9.1")
        release, data = appglider_release()
        with (
            patch("appglider.install.self_update.can_replace", return_value=False),
            patch("appglider.install.self_update.latest_release") as latest,
        ):
            self.window.refresh()
            self.window.self_update_button.click()
        latest.assert_not_called()
        self.assertIsNone(self.window.task)
        self.assertEqual(self.opened, [URL + "v0.9.1"])
        self.assertEqual(leftovers(self.image.parent, self.image), [])

    def test_the_banner_button_is_disabled_while_any_task_runs(self) -> None:
        window = self.window
        self.check("v0.9.1")
        gate = Event()
        window.start_task("Working…", lambda cancel, progress: gate.wait(10) and TaskResult("ok"))
        self.addCleanup(gate.set)
        self.assertFalse(window.self_update_button.isEnabled())
        window.self_update_button.click()
        window.update_self()
        self.assertEqual(self.opened, [])
        self.assertEqual(window.footer.text(), "Working…")
        gate.set()
        self.finish_task()
        self.assertTrue(window.self_update_button.isEnabled())
        window.self_ready = self_update.Ready(self.new_image, "0.9.1")
        window.refresh()
        gate.clear()
        window.start_task("Working…", lambda cancel, progress: gate.wait(10) and TaskResult("ok"))
        self.assertEqual(window.self_update_button.text(), "Restart now")
        self.assertFalse(window.self_update_button.isEnabled())
        gate.set()
        self.finish_task()

    def ready_window(self) -> None:
        self.new_image.write_bytes(appimage_bytes(b"new"))
        self.new_image.chmod(0o755)
        self.window.self_ready = self_update.Ready(self.new_image, "0.9.1")
        self.window.refresh()

    def test_restart_now_starts_the_new_image_waiting_for_the_lock_then_quits(self) -> None:
        window = self.window
        self.ready_window()
        with (
            patch.dict(os.environ, {"APPIMAGE": "/old.AppImage", "APPDIR": "/old", "HOME": "/h"}),
            patch("appglider.ui.window.subprocess.Popen") as popen,
            patch.object(window, "quit_launcher") as quit_launcher,
        ):
            window.self_update_button.click()
        args, kwargs = popen.call_args
        self.assertEqual(args[0], [str(self.new_image), "--after-update"])
        self.assertEqual(kwargs["cwd"], self.image.parent)
        self.assertTrue(kwargs["start_new_session"])
        self.assertTrue(kwargs.get("close_fds", True))
        self.assertNotIn("APPIMAGE", kwargs["env"])
        self.assertNotIn("APPDIR", kwargs["env"])
        self.assertIn("HOME", kwargs["env"])
        quit_launcher.assert_called_once_with()

    def test_restart_now_starts_a_real_detached_process(self) -> None:
        # No process mock: the real start call is part of the test.
        window = self.window
        self.ready_window()
        marker = self.new_image.parent / "started"
        self.new_image.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
        self.new_image.chmod(0o755)
        with (
            patch("appglider.ui.window.check_appimage"),
            patch.object(window, "quit_launcher") as quit_launcher,
        ):
            window.self_update_button.click()
        quit_launcher.assert_called_once_with()
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(marker.exists())

    def test_restart_now_stays_running_when_the_new_image_cannot_start(self) -> None:
        window = self.window
        self.ready_window()
        with (
            patch(
                "appglider.ui.window.subprocess.Popen",
                side_effect=PermissionError("Permission denied"),
            ),
            patch.object(window, "quit_launcher") as quit_launcher,
        ):
            window.self_update_button.click()
        quit_launcher.assert_not_called()
        self.assertEqual(window.footer.text(), "Couldn't start AppGlider 0.9.1: Permission denied")
        self.assertEqual(window.self_update_button.text(), "Restart now")

    def test_restart_now_refuses_a_file_that_is_gone_or_not_an_appimage(self) -> None:
        window = self.window
        self.ready_window()
        for damage in (self.new_image.unlink, lambda: self.new_image.write_bytes(b"junk")):
            damage()
            with (
                patch("appglider.ui.window.subprocess.Popen") as process,
                patch.object(window, "quit_launcher") as quit_launcher,
            ):
                window.restart_self()
            process.assert_not_called()
            quit_launcher.assert_not_called()
            self.assertIn("Couldn't start AppGlider 0.9.1", window.footer.text())

    def test_restart_now_refuses_while_a_task_or_a_release_check_runs(self) -> None:
        window = self.window
        self.ready_window()
        with (
            patch("appglider.ui.window.subprocess.Popen") as process,
            patch.object(window, "quit_launcher") as quit_launcher,
        ):
            window.task = object()  # type: ignore[assignment]
            window.restart_self()
            window.task = None
            with patch.object(type(window.checks), "busy", True):
                window.restart_self()
                window.refresh()
                self.assertFalse(window.self_update_button.isEnabled())
                window.self_update_button.click()
        process.assert_not_called()
        quit_launcher.assert_not_called()

    def test_running_the_old_file_again_shows_the_ready_banner_not_a_download(self) -> None:
        self.ready_window()
        self_update.record(
            self.settings, self_update.Downloaded(self.image, self.new_image, "0.9.1")
        )
        library = self.window.library
        again = LauncherWindow(self.settings, library)
        self.addCleanup(close_window, again)
        again.show()
        self.qt.processEvents()
        self.assertEqual(again.self_ready, self_update.Ready(self.new_image, "0.9.1"))
        self.assertTrue(again.self_banner.isVisibleTo(again.list_page))
        self.assertEqual(again.self_update_button.text(), "Restart now")
        self.assertIn("is ready", again.self_banner_label.text())
        self.assertEqual(
            again.settings_page.self_row.hint.text(), "Version 0.9.1 is ready · restart to use it"
        )
        with patch("appglider.install.self_update.check", return_value="v0.9.1"):
            again.start_self_check()
            deadline = time.monotonic() + 10
            while again.self_task is not None and time.monotonic() < deadline:
                QTest.qWait(10)
        self.assertEqual(again.self_update_button.text(), "Restart now")  # still not "available"
        self.assertIsNone(again.task)

    def test_a_vanished_ready_file_falls_back_to_the_usual_banner(self) -> None:
        self_update.record(
            self.settings, self_update.Downloaded(self.image, self.new_image, "0.9.1")
        )
        library = self.window.library
        again = LauncherWindow(self.settings, library)
        self.addCleanup(close_window, again)
        self.assertIsNone(again.self_ready)
        self.assertTrue(again.self_banner.isHidden())
        for key in (self_update.READY_KEY, self_update.REPLACED_KEY, self_update.VERSION_KEY):
            self.assertFalse(self.settings.contains(key))
        self.check("v0.9.1")
        self.assertEqual(self.window.self_update_button.text(), "Update AppGlider")
