"""Repositories renamed or transferred on GitHub keep working under their new address."""

import hashlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gui_helpers import close_window
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_library import appimage_bytes

from appglider.core.catalog import custom_app, load_apps
from appglider.github import assets, http
from appglider.github.assets import Release, RepositoryMismatch
from appglider.github.http import RepositoryMoved
from appglider.github.release_info import inspect_entries
from appglider.github.versions import architecture
from appglider.install.library import Library
from appglider.ui.window import LauncherWindow

OLD = custom_app("emmertex/lightcraft-classic", "LightCraft Classic")
NEW = "emmertex/chromaforge"


def name_for(version: str) -> str:
    return f"lightcraft-classic-{version}-linux-{architecture()}.AppImage"


def release_data(repo: str, version: str = "26.44.1") -> dict[str, object]:
    name = name_for(version)
    return {
        "tag_name": f"v{version}",
        "is_latest": True,
        "assets": [
            {
                "name": name,
                "browser_download_url": f"https://github.com/{repo}/releases/download/v{version}/{name}",
                "size": 10,
                "digest": "sha256:" + "a" * 64,
            }
        ],
    }


def release_for(repo: str, version: str, payload: bytes) -> Release:
    name = name_for(version)
    return Release(
        version,
        name,
        f"https://github.com/{repo}/releases/download/v{version}/{name}",
        len(payload),
        hashlib.sha256(payload).hexdigest(),
    )


class DetectionTests(unittest.TestCase):
    def test_the_redirect_names_the_new_repository(self) -> None:
        for location, expected in (
            ("https://github.com/emmertex/chromaforge", NEW),
            ("/emmertex/chromaforge", NEW),
            ("https://github.com/Emmertex/LightCraft-Classic", None),  # only the case changed
            ("https://github.com/login?return_to=x", None),
            ("https://example.com/emmertex/chromaforge", None),
            ("https://github.com/emmertex/chromaforge/releases", None),
            (None, None),
        ):
            with (
                self.subTest(location=location),
                patch("appglider.github.http.redirect_location", return_value=location),
            ):
                self.assertEqual(http.moved_to(OLD.repo), expected)

    def test_files_from_the_new_address_are_accepted_only_when_github_redirects_there(
        self,
    ) -> None:
        with self.assertRaises(RepositoryMismatch) as raised:
            assets.select_release(OLD, release_data(NEW), architecture())
        self.assertEqual(raised.exception.repo, NEW)
        entries = [release_data(NEW)]
        with patch("appglider.github.assets.api_entries", return_value=entries):
            for target, expected in ((NEW, RepositoryMoved), ("someone/else", RepositoryMismatch)):
                with (
                    self.subTest(target=target),
                    patch("appglider.github.http.moved_to", return_value=target),
                    self.assertRaises(expected),
                ):
                    assets.api_release(OLD, Event())
        with (
            patch("appglider.github.http.moved_to", return_value=NEW),
            self.assertRaises(RepositoryMoved) as moved,
        ):
            inspect_entries(OLD, entries, Event())
        self.assertEqual((moved.exception.old, moved.exception.new), (OLD.repo, NEW))

    def test_files_from_an_unrelated_repository_still_fail_as_before(self) -> None:
        data = release_data(OLD.repo)
        data["assets"][0]["browser_download_url"] = "https://example.com/x.AppImage"  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "does not match its repository"):
            assets.select_release(OLD, data, architecture())


class LibraryMoveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "data", self.root / "apps", self.root / "desktop")
        self.library.add_repository(OLD)

    def install(self, repo: str, version: str) -> None:
        payload = appimage_bytes(version.encode())
        app = next(app for app in self.library.apps if app.id == OLD.id)
        with patch("appglider.github.http.request", return_value=io.BytesIO(payload)):
            self.library.install(app, release_for(repo, version, payload), Event(), lambda *a: None)

    def test_a_move_keeps_the_id_install_and_preferences(self) -> None:
        self.install(OLD.repo, "26.44.0")
        self.library.set_update_hold(OLD.id, True)
        moved = self.library.move_repository(OLD.id, NEW)
        self.assertEqual((moved.id, moved.repo, moved.name), (OLD.id, NEW, OLD.name))
        self.assertEqual(moved.description, "AppImage from emmertex/chromaforge")
        self.assertEqual(self.library.moves, [(OLD.name, OLD.repo, NEW)])
        saved = json.loads((self.library.data_dir / "repositories.json").read_text())
        self.assertEqual(saved, [{"repo": NEW, "name": OLD.name, "id": OLD.id}])
        reopened = Library(self.library.data_dir, self.library.apps_dir, self.library.desktop_dir)
        app = reopened.apps[0]
        self.assertEqual((app.id, app.repo), (OLD.id, NEW))
        self.assertTrue(app.hold_updates)
        self.assertIn(OLD.id, reopened.scan())
        marker = (self.library.apps_dir / OLD.id / ".appglider").read_text()
        self.assertIn(NEW, marker)
        # Updating from the new address replaces the copy installed from the old one.
        self.library.set_update_hold(OLD.id, False)
        self.install(NEW, "26.44.1")
        self.assertEqual(reopened.scan()[OLD.id].version, "26.44.1")
        self.library.remove(reopened.apps[0])
        self.assertEqual(reopened.scan(), {})

    def test_moving_onto_a_repository_already_listed_is_refused(self) -> None:
        self.library.add_repository(custom_app(NEW))
        with self.assertRaisesRegex(ValueError, "already in your library"):
            self.library.move_repository(OLD.id, NEW)
        self.assertEqual(self.library.apps[0].repo, OLD.repo)

    def test_following_moves_runs_again_with_the_new_address(self) -> None:
        seen: list[str] = []

        def run(app):
            seen.append(app.repo)
            if app.repo == OLD.repo:
                raise RepositoryMoved(OLD.repo, NEW)
            return app.id

        self.assertEqual(self.library.following_moves(self.library.apps[0], run), OLD.id)
        self.assertEqual(seen, [OLD.repo, NEW])

    def test_a_saved_id_must_look_like_one_appglider_gives(self) -> None:
        path = self.library.data_dir / "repositories.json"
        for app_id in ("../escape", "photo craft", "github-x__..", 5):
            with self.subTest(app_id=app_id):
                path.write_text(json.dumps([{"repo": NEW, "name": "X", "id": app_id}]))
                with self.assertRaisesRegex(ValueError, "preserved"):
                    load_apps(path)
        path.write_text(json.dumps([{"repo": NEW, "name": "X", "id": "photocraft"}]))
        self.assertEqual(load_apps(path)[0].id, "photocraft")


class WindowMoveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.library = Library(root / "data", root / "apps", root / "desktop")
        self.library.add_repository(OLD)
        settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
        self.window = LauncherWindow(settings, self.library)
        self.addCleanup(close_window, self.window)

    def wait_for_task(self) -> None:
        deadline = time.monotonic() + 5
        while self.window.task is not None and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertIsNone(self.window.task)

    def test_installing_a_moved_app_records_the_new_address_and_says_so(self) -> None:
        payload = appimage_bytes(b"chromaforge")

        def latest(app, cancel, cache_dir=None):
            if app.repo == OLD.repo:
                raise RepositoryMoved(OLD.repo, NEW)
            return release_for(NEW, "26.44.1", payload)

        with (
            patch("appglider.ui.window.latest_release", side_effect=latest),
            patch("appglider.github.http.request", return_value=io.BytesIO(payload)),
        ):
            self.window.install_app(OLD.id)
            self.wait_for_task()
        self.assertEqual(self.library.apps[0].repo, NEW)
        self.assertEqual(self.window.apps[0].repo, NEW)
        self.assertIn(OLD.id, self.window.installed)
        self.assertIn("moved to emmertex/chromaforge on GitHub", self.window.footer.text())
        self.assertIn("ready to launch", self.window.footer.text())

    def test_adding_by_the_old_address_adds_the_new_one(self) -> None:
        self.library.remove_repository(OLD.id)
        self.window.scan_installed()
        page = self.window.add_page
        self.window.open_add_app()
        page.url.setText("https://github.com/emmertex/lightcraft-classic")
        payload = appimage_bytes(b"chromaforge")

        def latest(app, cancel, cache_dir=None):
            if app.repo == OLD.repo:
                raise RepositoryMoved(OLD.repo, NEW)
            return release_for(NEW, "26.44.1", payload)

        with (
            patch("appglider.ui.pages.add_page.latest_release", side_effect=latest),
            patch("appglider.ui.pages.add_page.discover_icons", return_value=()),
        ):
            page.inspect_repository()
            deadline = time.monotonic() + 5
            while page.task is not None and time.monotonic() < deadline:
                QTest.qWait(10)
        self.assertEqual(page.url.text(), "https://github.com/emmertex/chromaforge")
        self.assertIn("moved to emmertex/chromaforge", page.status.text())
        self.assertTrue(page.add_button.isEnabled())
        page.add_button.click()
        self.assertEqual([app.repo for app in self.library.apps], [NEW])


if __name__ == "__main__":
    unittest.main()
