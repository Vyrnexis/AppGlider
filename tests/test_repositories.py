"""Custom repositories persist safely and join the existing app lifecycle."""

import hashlib
import io
import json
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
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox
from test_builds import BAMBU, bambu_metadata, chosen_bambu
from test_library import appimage_bytes

from appglider import APP_ICON
from appglider.core.catalog import custom_app, load_apps
from appglider.github import assets, http
from appglider.github.assets import Release, select_release
from appglider.github.icons import IconCandidate, normalise_icon
from appglider.github.release_info import ReleaseInfo, ReleaseOption, discover_releases
from appglider.github.versions import architecture, check_cancel
from appglider.install.library import Library
from appglider.ui.jobs import TaskResult
from appglider.ui.pages.catalog_preview import CatalogPreviewDialog
from appglider.ui.window import LauncherWindow

CUSTOM = custom_app("https://github.com/emmertex/lightcraft-classic", "LightCraft Classic")


def custom_release(version="0.9.4", payload=None):
    payload = payload if payload is not None else appimage_bytes()
    name = f"{CUSTOM.asset_stem}-{version}-linux-{architecture()}.AppImage"
    return Release(
        version,
        name,
        f"https://github.com/{CUSTOM.repo}/releases/download/v{version}/{name}",
        len(payload),
        hashlib.sha256(payload).hexdigest(),
    )


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "data", self.root / "apps", self.root / "desktop")

    def install(self, version):
        payload = appimage_bytes(version.encode())
        with patch("appglider.github.http.request", return_value=io.BytesIO(payload)):
            return self.library.install(
                CUSTOM, custom_release(version, payload), Event(), lambda *a: None
            )

    def test_repository_addresses_are_normalized_and_ids_do_not_collide(self):
        self.assertEqual(
            custom_app("  https://github.com/Emmertex/Lightcraft-Classic.git/ ", CUSTOM.name),
            CUSTOM,
        )
        self.assertEqual(custom_app("emmertex/lightcraft-classic", CUSTOM.name), CUSTOM)
        other = custom_app("another-owner/lightcraft-classic")
        self.assertNotEqual(CUSTOM.id, other.id)
        self.assertEqual(CUSTOM.asset_stem, other.asset_stem)
        self.assertEqual(CUSTOM.icon, APP_ICON)

    def test_invalid_sources_names_and_path_traversal_are_rejected(self):
        for address in (
            "http://github.com/owner/app",
            "https://github.com.evil.test/owner/app",
            "https://user@github.com/owner/app",
            "https://github.com:443/owner/app",
            "https://github.com/owner/app?download=true",
            "https://github.com/owner/app#readme",
            "https://github.com/owner/app/releases",
            "owner/..",
            "../app",
            "owner/app/../other",
            "owner/ap\np",
            "owner/app%2fother",
            "",
            "owner",
            "owner/app\\other",
        ):
            with self.subTest(address=address), self.assertRaises(ValueError):
                custom_app(address)
        for name in ("", " ", "bad\nname", "x" * 129):
            with self.subTest(name=name), self.assertRaises(ValueError):
                custom_app(CUSTOM.repo, name)

    def test_catalog_persists_without_altering_the_library(self):
        self.assertEqual(self.library.apps, ())
        self.assertFalse(self.library.data_dir.exists())
        self.library.add_repository(CUSTOM)
        self.assertEqual(self.library.apps, (CUSTOM,))
        reopened = Library(self.library.data_dir, self.library.apps_dir)
        self.assertEqual(reopened.apps[-1], CUSTOM)
        self.assertFalse((self.library.data_dir / "library.json").exists())
        self.assertEqual(len(APPS), 14)
        self.library.remove_repository(CUSTOM.id)
        self.assertEqual(reopened.apps, ())

    def test_known_repositories_keep_their_established_ids_and_icons(self):
        self.assertEqual(
            {app.id for app in APPS},
            {
                "photocraft", "vectorcraft", "filmcraft", "lightcraft", "pdfcraft",
                "effectcraft", "designcraft", "figcraft", "wordcraft", "soundcraft",
                "deckcraft", "cadcraft", "gridcraft", "solidcraft",
            },
        )  # fmt: skip
        for app in APPS:
            with self.subTest(app=app.id):
                self.assertEqual(custom_app(app.repo).id, app.id)
                self.assertNotEqual(self.library.icon_path(app), APP_ICON)
        self.assertEqual(custom_app("someone/photocraft").id, "github-someone__photocraft")
        self.assertEqual(self.library.icon_path(custom_app("someone/photocraft")), APP_ICON)

    def test_duplicates_are_rejected_and_every_repository_can_be_removed(self):
        stock(self.library)
        self.library.add_repository(CUSTOM)
        for app in (
            CUSTOM,
            custom_app("EMMERTEX/LIGHTCRAFT-CLASSIC", "Other name"),
            custom_app(APPS[0].repo),
        ):
            with self.subTest(repo=app.repo), self.assertRaisesRegex(ValueError, "already"):
                self.library.add_repository(app)
        self.library.remove_repository(APPS[0].id)
        self.assertNotIn(APPS[0].id, {app.id for app in self.library.apps})
        with self.assertRaisesRegex(ValueError, "not in the library"):
            self.library.remove_repository(APPS[0].id)
        with self.assertRaisesRegex(ValueError, "Invalid"):
            self.library.add_repository(replace(CUSTOM, id="../../escape"))

    def test_removing_or_readding_a_repository_clears_a_stale_update_hold(self):
        self.library.add_repository(CUSTOM)
        holds = self.library.data_dir / "update-holds.json"
        holds.write_text(json.dumps({CUSTOM.id: True}))
        self.assertTrue(self.library.apps[-1].hold_updates)
        self.library.remove_repository(CUSTOM.id)
        self.assertEqual(json.loads(holds.read_text()), {})
        holds.write_text(json.dumps({CUSTOM.id: True}))
        self.library.add_repository(CUSTOM)
        self.assertFalse(self.library.apps[-1].hold_updates)

    def test_corrupt_catalog_is_preserved_and_blocks_mutations(self):
        self.library.add_repository(CUSTOM)
        path = self.library.data_dir / "repositories.json"
        for content in (
            "broken JSON",
            "{}",
            '[{"repo": "owner/app"}]',
            '[{"repo": "../app", "name": "Bad"}]',
        ):
            path.write_text(content)
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, "preserved"):
                self.library.add_repository(CUSTOM)
            self.assertEqual(path.read_text(), content)
        path.write_text(json.dumps([{"repo": CUSTOM.repo, "name": CUSTOM.name}] * 2))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            load_apps(path)

    def test_failed_catalog_save_keeps_original_file(self):
        self.library.add_repository(CUSTOM)
        path = self.library.data_dir / "repositories.json"
        original = path.read_bytes()
        with patch("appglider.core.storage.os.replace", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                self.library.add_repository(custom_app("other/another-app"))
        self.assertEqual(path.read_bytes(), original)

    def test_custom_install_update_rollback_restart_and_remove_preserve_ownership(self):
        with self.assertRaisesRegex(ValueError, "Add this repository"):
            self.install("0.9.3")
        self.library.add_repository(CUSTOM)
        first = self.install("0.9.3")
        second = self.install("0.9.4")
        self.assertNotEqual(first.path, second.path)
        self.assertEqual(second.path.parent.parent.name, CUSTOM.id)
        self.assertTrue((second.path.parent.parent / ".appglider").is_file())
        with self.assertRaisesRegex(ValueError, "Uninstall"):
            self.library.remove_repository(CUSTOM.id)
        self.library.rollback(CUSTOM)
        reopened = Library(self.library.data_dir, self.library.apps_dir, self.library.desktop_dir)
        self.assertEqual(reopened.scan()[CUSTOM.id].path, first.path)
        shortcut = self.library.desktop_dir / f"appglider-{CUSTOM.id}.desktop"
        self.assertIn("LightCraft Classic (AppGlider)", shortcut.read_text())
        self.library.remove(CUSTOM)
        self.library.remove_repository(CUSTOM.id)
        self.assertEqual(reopened.scan(), {})
        self.assertEqual(reopened.apps, ())
        self.assertFalse(shortcut.exists())

    def test_a_record_whose_file_was_moved_away_does_not_block_removal(self):
        self.library.add_repository(CUSTOM)
        installed = self.install("0.9.4")
        shortcut = self.library.desktop_dir / f"appglider-{CUSTOM.id}.desktop"
        self.assertTrue(shortcut.is_file())
        # AppImageLauncher, for one, moves an AppImage it integrates into ~/Applications.
        moved = self.root / "Applications" / installed.path.name
        moved.parent.mkdir()
        installed.path.rename(moved)
        self.assertEqual(self.library.scan(), {})
        self.library.remove_repository(CUSTOM.id)
        self.assertEqual(self.library.apps, ())
        self.assertEqual(json.loads((self.library.data_dir / "library.json").read_text()), {})
        self.assertFalse(shortcut.exists())
        self.assertTrue(moved.is_file())
        self.library.add_repository(CUSTOM)
        self.assertEqual(self.install("0.9.4").path, installed.path)

    def test_only_a_missing_copy_can_be_forgotten(self):
        self.library.add_repository(CUSTOM)
        installed = self.install("0.9.4")
        self.assertEqual(self.library.missing(), {})
        with self.assertRaisesRegex(ValueError, "missing"):
            self.library.forget_missing(CUSTOM.id)
        installed.path.unlink()
        self.assertEqual(list(self.library.missing()), [CUSTOM.id])
        self.library.forget_missing(CUSTOM.id)
        self.assertEqual(self.library.missing(), {})
        self.assertIn(CUSTOM, self.library.apps)
        self.assertFalse((self.library.desktop_dir / f"appglider-{CUSTOM.id}.desktop").exists())

    def test_custom_filename_matching_is_separate_from_storage_id(self):
        release = custom_release()
        data = {
            "tag_name": "v0.9.4",
            "assets": [
                {
                    "name": release.name,
                    "browser_download_url": release.url.replace("emmertex", "Emmertex"),
                    "size": release.size,
                    "digest": "sha256:" + release.sha256,
                }
            ],
        }
        selected = select_release(CUSTOM, data, architecture())
        self.assertEqual(selected.name, release.name)
        for asset in data["assets"]:
            asset["name"] = asset["name"].replace("-linux-", "-")
            asset["browser_download_url"] = asset["browser_download_url"].replace("-linux-", "-")
        self.assertEqual(
            select_release(CUSTOM, data, architecture()).name, release.name.replace("-linux-", "-")
        )
        data["assets"][0]["browser_download_url"] = release.url.replace(CUSTOM.repo, "another/app")
        with self.assertRaisesRegex(ValueError, "repository"):
            select_release(CUSTOM, data, architecture())


class RepositoryGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setStyle("Fusion")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = stock(Library(self.root / "data", self.root / "apps", self.root / "desktop"))
        self.settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        icons = patch("appglider.ui.pages.add_page.discover_icons", return_value=())
        icons.start()
        self.addCleanup(icons.stop)
        self.settings.setValue("appearance/theme", "Light")
        self.window = LauncherWindow(self.settings, self.library)
        self.window.show()
        self.addCleanup(close_window, self.window)
        self.dialog = self.window.add_page
        self.catalogs = self.window.settings_page
        self.qt.processEvents()

    def write_catalog(self, text):
        path = self.root / "catalog.toml"
        path.write_text(text)
        return path

    def run_import(self, path, accept=True):
        previews = []

        def exec_preview(preview):
            previews.append(preview)
            return preview.result() or (1 if accept else 0)

        with (
            patch(
                "appglider.ui.pages.settings_page.QFileDialog.getOpenFileName",
                return_value=(str(path), ""),
            ),
            patch.object(CatalogPreviewDialog, "exec", exec_preview),
        ):
            self.catalogs.import_catalog()
        return previews

    def test_import_previews_then_adds_and_cancel_adds_nothing(self):
        path = self.write_catalog(
            'format = 1\nname = "Tools"\n[[app]]\nrepo = "owner/one"\nname = "One"\n'
            'description = "First tool"\n[[app]]\nrepo = "https://evil.test/x/y"\n'
            f'[[app]]\nrepo = "{APPS[0].repo}"\nextra = 1\n'
        )
        (preview,) = self.run_import(path, accept=False)
        text = preview.details.toPlainText()
        self.assertIn("ADD  One  (owner/one, stable releases only)", text)
        self.assertIn("First tool", text)
        self.assertIn("ALREADY IN YOUR LIBRARY", text)
        self.assertIn("SKIPPED", text)
        self.assertIn("app 3: extra", text)
        self.assertTrue(preview.add_button.isEnabled())
        self.assertEqual(self.library.apps, APPS)
        self.run_import(path)
        self.assertEqual([app.repo for app in self.library.apps[len(APPS) :]], ["owner/one"])
        self.assertIn("Added 1 app", self.window.footer.text())
        self.assertIn("owner/one", {app.repo for app in self.window.apps})

    def test_import_reports_unusable_files_without_changing_the_library(self):
        for text in ("format = 2", "not toml [", "name = 'no format'"):
            with self.subTest(text=text):
                self.assertEqual(self.run_import(self.write_catalog(text)), [])
                self.assertIn("Cannot import catalog.toml", self.window.footer.text())
        self.assertEqual(self.run_import(self.root / "missing.toml"), [])
        self.assertIn("Cannot import missing.toml", self.window.footer.text())
        self.assertEqual(self.library.apps, APPS)

    def test_preview_with_nothing_new_cannot_be_accepted(self):
        path = self.write_catalog(f'format = 1\n[[app]]\nrepo = "{APPS[0].repo}"\n')
        (preview,) = self.run_import(path, accept=False)
        self.assertFalse(preview.add_button.isEnabled())

    def test_export_writes_a_catalog_that_imports_back(self):
        self.library.add_repository(custom_app("owner/one", "One", True, "One.AppImage"))
        target = self.root / "out.toml"
        with patch(
            "appglider.ui.pages.settings_page.QFileDialog.getSaveFileName",
            return_value=(str(target), ""),
        ):
            self.catalogs.export_catalog()
        self.assertIn(f"Exported {len(APPS) + 1} apps to out.toml", self.window.footer.text())
        text = target.read_text()
        self.assertTrue(text.startswith("# AppGlider catalog"))
        self.assertIn('repo = "owner/one"', text)
        self.assertIn("stable_only = false", text)
        with patch(
            "appglider.ui.pages.settings_page.QFileDialog.getSaveFileName", return_value=("", "")
        ):
            self.catalogs.export_catalog()
        self.assertIn("Exported", self.window.footer.text())
        blocked = self.root  # a directory cannot be written as a file
        with patch(
            "appglider.ui.pages.settings_page.QFileDialog.getSaveFileName",
            return_value=(str(blocked), ""),
        ):
            self.catalogs.export_catalog()
        self.assertIn("Cannot export", self.window.footer.text())

    def test_imported_text_is_shown_as_plain_text(self):
        path = self.write_catalog(
            'format = 1\n[[app]]\nrepo = "owner/evil"\nname = "<b>Bold</b>"\n'
            'description = "<a href=\\"http://x.test\\">click</a>"\n'
        )
        self.run_import(path)
        card = self.window.cards["github-owner__evil"]
        self.assertEqual(card.subtitle.full_text, '<a href="http://x.test">click</a>')
        self.assertEqual(card.subtitle.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(card.findChild(type(card.action_button), "appName").text(), "<b>Bold</b>")

    def test_the_example_catalog_button_previews_then_adds_the_example_apps(self):
        empty = self.root / "empty"
        library = Library(empty / "data", empty / "apps", empty / "desktop")
        window = LauncherWindow(self.settings, library)
        self.addCleanup(close_window, window)
        page = window.settings_page
        self.assertTrue(page.example_button.isEnabled())
        previews = []

        def exec_preview(preview, accept=True):
            previews.append(preview)
            return 1 if accept else 0

        with patch.object(CatalogPreviewDialog, "exec", exec_preview):
            page.import_example_catalog()
        self.assertEqual(len(library.apps), 14)
        (preview,) = previews
        self.assertIn("ArtCraft apps and SolidCraft", preview.findChild(QLabel).text())
        self.assertIn("Added 14 apps from artcraft.toml", window.footer.text())
        with patch.object(CatalogPreviewDialog, "exec", exec_preview):
            page.import_example_catalog()  # everything is already there
        self.assertEqual(len(previews), 2)
        self.assertFalse(previews[1].add_button.isEnabled())

    def test_catalog_actions_are_disabled_while_a_task_runs(self):
        page = self.catalogs
        for button in (page.import_button, page.example_button, page.export_button):
            self.assertTrue(button.isEnabled())
        self.window.task = object()
        try:
            self.window.refresh()
            for button in (page.import_button, page.example_button, page.export_button):
                self.assertFalse(button.isEnabled())
            with patch("appglider.ui.pages.settings_page.QFileDialog.getOpenFileName") as chooser:
                page.import_catalog()
                chooser.assert_not_called()
            with patch.object(CatalogPreviewDialog, "exec") as preview:
                page.import_example_catalog()
                preview.assert_not_called()
        finally:
            self.window.task = None
            self.window.refresh()
        self.assertTrue(page.import_button.isEnabled())

    def wait_for_task(self, owner):
        deadline = time.monotonic() + 5
        while owner.task is not None and time.monotonic() < deadline:
            QTest.qWait(10)
            time.sleep(0.001)  # Let Python worker threads run between Qt waits.
        self.assertIsNone(owner.task, "Background operation did not finish")

    def inspect(self):
        if not self.dialog.adding:
            QTest.mouseClick(self.window.nav_add, Qt.MouseButton.LeftButton)
        self.dialog.url.setText(CUSTOM.source_url)
        self.dialog.name.setText(CUSTOM.name)
        with patch(
            "appglider.ui.pages.add_page.latest_release", return_value=custom_release()
        ) as latest:
            QTest.mouseClick(self.dialog.inspect_button, Qt.MouseButton.LeftButton)
            self.wait_for_task(self.dialog)
            latest.assert_called_once()
            self.assertEqual(latest.call_args.args[0], CUSTOM)
        self.assertTrue(self.dialog.add_button.isEnabled())

    def test_the_add_flow_is_a_page_in_the_main_window_and_opens_the_new_app(self):
        self.assertIsNot(self.window.pages.currentWidget(), self.dialog)
        QTest.mouseClick(self.window.nav_add, Qt.MouseButton.LeftButton)
        self.assertIs(self.window.pages.currentWidget(), self.dialog)
        self.assertTrue(self.window.nav_add.isChecked())
        self.assertTrue(self.dialog.release_preview.isHidden())
        self.assertTrue(self.dialog.icon_group.isHidden())
        self.inspect()
        self.assertTrue(self.dialog.release_preview.isVisible())
        self.assertTrue(self.dialog.icon_group.isVisible())
        self.dialog.add_repository()
        self.assertIs(self.window.pages.currentWidget(), self.window.app_page)
        self.assertEqual(self.window.app_page.app_id, CUSTOM.id)
        self.assertEqual(
            self.window.footer.text(), f"Added {CUSTOM.name}. Install it from its page."
        )
        self.assertFalse(self.window.add_page.adding)
        self.assertTrue(self.window.nav_library.isChecked())
        self.assertIn(CUSTOM, self.library.apps)

    def test_leaving_the_add_page_discards_it(self):
        self.window.open_add_app()
        self.dialog.url.setText(CUSTOM.source_url)
        self.window.nav_library.click()
        self.assertIsNot(self.window.pages.currentWidget(), self.dialog)
        self.assertFalse(self.dialog.adding)
        self.assertEqual(self.library.apps, APPS)
        self.window.open_add_app()
        self.assertEqual(self.dialog.url.text(), "")
        self.assertEqual(self.dialog.release_channel.currentIndex(), 0)

    def test_add_page_is_unavailable_while_a_task_runs(self):
        self.window.task = object()
        try:
            self.window.refresh()
            self.assertFalse(self.window.nav_add.isEnabled())
            self.window.open_add_app()
            self.assertIsNot(self.window.pages.currentWidget(), self.dialog)
        finally:
            self.window.task = None
            self.window.refresh()

    def test_combobox_popups_have_readable_rows_and_contrast_in_both_themes(self):
        self.window.open_add_app()
        self.dialog.set_icon_options(
            (IconCandidate("one.png", normalise_icon(APP_ICON.read_bytes())),)
        )
        for theme in ("Light", "Dark"):
            self.settings.setValue("appearance/theme", theme)
            self.window.apply_theme()
            for owner, combo in (
                (self.dialog, self.dialog.icon_choices),
                (self.catalogs, self.catalogs.check_interval),
            ):
                with self.subTest(theme=theme, control=combo.accessibleName()):
                    self.window.pages.setCurrentWidget(owner)
                    self.qt.processEvents()
                    combo.showPopup()
                    QTest.qWait(10)
                    try:
                        view = combo.view()
                        for row in range(combo.count()):
                            rect = view.visualRect(combo.model().index(row, 0))
                            self.assertGreaterEqual(rect.height(), view.fontMetrics().height() + 12)
                            text_width = view.fontMetrics().horizontalAdvance(combo.itemText(row))
                            self.assertGreaterEqual(rect.width(), text_width + 20)
                        palette = view.palette()
                        self.assertNotEqual(
                            palette.color(QPalette.ColorRole.Text),
                            palette.color(QPalette.ColorRole.Base),
                        )
                        self.assertNotEqual(
                            palette.color(QPalette.ColorRole.HighlightedText),
                            palette.color(QPalette.ColorRole.Highlight),
                        )
                    finally:
                        combo.hidePopup()

    def test_inspect_preview_add_and_restart_update_library_immediately(self):
        self.inspect()
        self.assertIn("v0.9.4", self.dialog.version.text())
        self.assertEqual(self.dialog.filename.text(), custom_release().name)
        self.assertIn("SHA-256", self.dialog.checksum.text())
        QTest.mouseClick(self.dialog.add_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.app_page.app_id, CUSTOM.id)
        self.assertEqual(len(self.window.cards), 15)
        self.assertEqual(self.window.library_count.text(), "15")
        self.assertEqual(self.window.cards[CUSTOM.id].app.name, CUSTOM.name)
        self.assertFalse(self.dialog.add_button.isEnabled())
        self.assertEqual(self.window.cards[CUSTOM.id].action_button.text(), "Install")
        reopened = LauncherWindow(
            self.settings, Library(self.library.data_dir, self.library.apps_dir)
        )
        self.addCleanup(reopened.deleteLater)
        self.addCleanup(reopened.close)
        self.assertIn(CUSTOM.id, reopened.cards)
        self.assertEqual(reopened.library_count.text(), "15")
        self.assertIn("lightcraft", reopened.cards)
        self.window.open_app(CUSTOM.id)
        with patch(
            "appglider.ui.pages.app_page.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.window.app_page.remove_button.click()
        self.assertNotIn(CUSTOM.id, self.window.cards)
        self.assertEqual(len(self.window.cards), 14)
        self.assertEqual(self.window.library_count.text(), "14")

    def test_edits_invalidate_preview_and_invalid_or_duplicate_sources_do_not_fetch(self):
        self.inspect()
        self.dialog.name.setText("Changed name")
        self.assertFalse(self.dialog.add_button.isEnabled())
        self.assertIsNone(self.dialog.release)
        for address in ("https://evil.test/a/b", APPS[0].source_url):
            self.dialog.url.setText(address)
            with patch("appglider.ui.pages.add_page.latest_release") as latest:
                self.dialog.inspect_repository()
                latest.assert_not_called()
            self.assertIsNone(self.dialog.task)
            self.assertFalse(self.dialog.add_button.isEnabled())

    def test_missing_appimage_or_checksum_is_reported_without_saving(self):
        self.window.open_add_app()
        self.dialog.url.setText(CUSTOM.source_url)
        with patch(
            "appglider.ui.pages.add_page.latest_release",
            side_effect=ValueError("No published SHA-256 checksum"),
        ):
            self.dialog.inspect_repository()
            self.wait_for_task(self.dialog)
        self.assertIn("No published SHA-256", self.dialog.status.text())
        self.assertFalse(self.dialog.add_button.isEnabled())
        self.assertEqual(self.library.apps, APPS)

    def test_discovered_icon_preview_selection_persistence_and_shortcuts(self):
        first = normalise_icon(
            b'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
            b'<path d="M0 0H32V32H0Z" fill="red"/></svg>'
        )
        second = normalise_icon(
            b'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
            b'<path d="M0 0H32V32H0Z" fill="blue"/></svg>'
        )
        choices = (IconCandidate("assets/red.svg", first), IconCandidate("assets/blue.svg", second))
        with patch("appglider.ui.pages.add_page.discover_icons", return_value=choices):
            self.inspect()
        self.assertFalse(self.dialog.icon_preview.pixmap().isNull())
        self.dialog.icon_choices.setCurrentIndex(1)
        self.assertEqual(self.dialog.selected_icon, second)
        self.dialog.add_repository()
        cached = self.library.icon_path(CUSTOM)
        self.assertEqual(cached.read_bytes(), second)
        self.assertEqual(
            self.window.cards[CUSTOM.id].icon.pixmap().toImage().pixelColor(0, 0).name(), "#0000ff"
        )
        payload = appimage_bytes()
        with patch("appglider.github.http.request", return_value=io.BytesIO(payload)):
            self.library.install(CUSTOM, custom_release(payload=payload), Event(), lambda *a: None)
        self.window.scan_installed()
        shortcut = self.library.desktop_dir / f"appglider-{CUSTOM.id}.desktop"
        self.assertIn(f"Icon={cached}", shortcut.read_text())
        self.assertEqual(cached.read_bytes(), second)
        self.assertFalse(self.window.tray_apps_menu.actions()[0].icon().isNull())

    def test_icon_lookup_failure_still_allows_the_repository_to_be_added(self):
        with patch("appglider.ui.pages.add_page.discover_icons", side_effect=ValueError("quota")):
            self.inspect()
        self.dialog.add_repository()
        self.assertIn(CUSTOM, self.library.apps)

    def test_add_multi_build_repo_remembers_choice_and_keeps_preview_preference_independent(self):
        entries = [bambu_metadata(), bambu_metadata("02.09.00.12", preview=True)]
        with (
            patch.object(assets, "api_entries", return_value=entries),
            patch("appglider.ui.pages.add_page.discover_icons", return_value=()),
            patch("appglider.install.library.download") as download,
        ):
            self.window.open_add_app()
            self.dialog.url.setText(BAMBU.source_url)
            self.dialog.name.setText(BAMBU.name)
            self.dialog.inspect_repository()
            self.wait_for_task(self.dialog)
            self.assertTrue(self.dialog.build_group.isVisibleTo(self.dialog))
            self.assertEqual(self.dialog.build_combo.count(), 3)
            self.assertFalse(self.dialog.add_button.isEnabled())
            self.assertIn("Ubuntu 24.04", self.dialog.build_combo.itemText(2))
            self.dialog.build_combo.setCurrentIndex(2)
            self.wait_for_task(self.dialog)
            self.assertTrue(self.dialog.add_button.isEnabled())
            self.assertIn("ubuntu24.04", self.dialog.filename.text())
            self.dialog.add_repository()
            saved = next(app for app in self.library.apps if app.id == BAMBU.id)
            self.assertEqual(saved.build_variant, chosen_bambu().build_variant)
            self.assertEqual(self.window.cards[saved.id].action_button.text(), "Install")
            download.assert_not_called()
        self.assertEqual(self.library.scan(), {})

    def test_switching_channel_and_build_retains_repository_icons_and_selection(self):
        first = normalise_icon(APP_ICON.read_bytes())
        selected = normalise_icon(
            b'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
            b'<path d="M0 0H32V32H0Z" fill="blue"/></svg>'
        )
        icons = (IconCandidate("icon.png", first), IconCandidate("blue.svg", selected))
        preview = bambu_metadata("02.08.04.57", preview=True)
        for asset in preview["assets"]:
            for key in ("name", "browser_download_url"):
                asset[key] = (
                    asset[key].replace("ubuntu24.04", "ubu24").replace("ubuntu22.04", "ubu22")
                )
        with (
            patch.object(assets, "api_entries", return_value=[bambu_metadata(), preview]),
            patch(
                "appglider.ui.pages.add_page.discover_icons",
                side_effect=[icons, http.RateLimited("api.github.com", 1)],
            ) as discover,
        ):
            self.window.open_add_app()
            self.dialog.url.setText(BAMBU.source_url)
            self.dialog.name.setText(BAMBU.name)
            self.dialog.inspect_repository()
            self.wait_for_task(self.dialog)
            self.dialog.build_combo.setCurrentIndex(2)
            self.wait_for_task(self.dialog)
            self.dialog.icon_choices.setCurrentIndex(1)
            self.assertEqual(self.dialog.selected_icon, selected)
            self.dialog.release_channel.setCurrentIndex(1, notify=True)
            self.assertEqual(self.dialog.selected_icon, selected)
            self.dialog.inspect_repository()
            self.wait_for_task(self.dialog)
            self.dialog.build_combo.setCurrentIndex(2)
            self.wait_for_task(self.dialog)
            self.assertEqual(self.dialog.icon_options, icons)
            self.assertEqual(self.dialog.selected_icon, selected)
            self.assertEqual(self.dialog.icon_choices.currentIndex(), 1)
            self.assertNotIn("default icon", self.dialog.status.text())
            discover.assert_called_once()
            self.dialog.add_repository()
        saved = next(app for app in self.library.apps if app.id == BAMBU.id)
        self.assertTrue(saved.include_prereleases)
        self.assertEqual(self.library.icon_path(saved).read_bytes(), selected)

    def test_channel_changes_keep_explicit_default_and_local_icon_until_repository_changes(self):
        png = normalise_icon(APP_ICON.read_bytes())
        choices = (IconCandidate("repository.png", png),)
        with patch("appglider.ui.pages.add_page.discover_icons", return_value=choices) as discover:
            self.inspect()
            self.dialog.icon_choices.setCurrentIndex(1)
            self.assertIsNone(self.dialog.selected_icon)
            self.dialog.release_channel.setCurrentIndex(1, notify=True)
            with patch("appglider.ui.pages.add_page.latest_release", return_value=custom_release()):
                self.dialog.inspect_repository()
                self.wait_for_task(self.dialog)
            self.assertIsNone(self.dialog.selected_icon)
            self.assertEqual(self.dialog.icon_choices.currentIndex(), 1)
            image = self.root / "local.png"
            image.write_bytes(png)
            with patch(
                "appglider.ui.pages.add_page.QFileDialog.getOpenFileName",
                return_value=(str(image), ""),
            ):
                self.dialog.choose_icon()
            self.dialog.release_channel.setCurrentIndex(0, notify=True)
            with patch("appglider.ui.pages.add_page.latest_release", return_value=custom_release()):
                self.dialog.inspect_repository()
                self.wait_for_task(self.dialog)
            self.assertEqual(self.dialog.selected_icon, png)
            self.assertEqual(self.dialog.icon_options[0].name, "local.png")
            discover.assert_called_once()
        self.dialog.url.setText("https://github.com/another/application")
        self.assertEqual(self.dialog.icon_options, ())
        self.assertIsNone(self.dialog.selected_icon)

    def test_failed_icon_lookup_explains_fallback_and_can_be_retried(self):
        icon = IconCandidate("application.png", normalise_icon(APP_ICON.read_bytes()))
        with patch(
            "appglider.ui.pages.add_page.discover_icons",
            side_effect=[ValueError("request limit reached"), (icon,)],
        ) as discover:
            self.inspect()
            self.assertTrue(self.dialog.add_button.isEnabled())
            self.assertIn("default icon", self.dialog.status.text())
            self.assertIn("request limit reached", self.dialog.status.text())
            with patch("appglider.ui.pages.add_page.latest_release", return_value=custom_release()):
                self.dialog.inspect_repository()
                self.wait_for_task(self.dialog)
            self.assertEqual(discover.call_count, 2)
            self.assertEqual(self.dialog.selected_icon, icon.png)
            self.assertEqual(self.dialog.status.text(), "Ready to add.")

    def test_install_discovers_ambiguity_and_opens_build_preferences_without_downloading(self):
        self.library.add_repository(BAMBU)
        self.window.scan_installed()
        with (
            patch.object(assets, "api_entries", return_value=[bambu_metadata()]),
            patch.object(self.window, "choose_build") as choose,
            patch("appglider.install.library.download") as download,
        ):
            self.window.install_app(BAMBU.id)
            self.wait_for_task(self.window)
            choose.assert_called_once_with(BAMBU.id)
            download.assert_not_called()
        self.assertEqual(self.library.scan(), {})

    def test_changing_build_while_background_check_runs_discards_old_results_and_rechecks(self):
        selected = chosen_bambu()
        self.library.add_repository(selected)
        payload = appimage_bytes()
        with patch.object(http, "request", return_value=io.BytesIO(payload)):
            self.library.install(
                selected,
                select_release(selected, bambu_metadata(), architecture()),
                Event(),
                lambda *args: None,
            )
        with patch.object(assets, "api_entries", return_value=[bambu_metadata()]):
            old_info = discover_releases(selected, Event(), self.library.data_dir / "release-cache")
        for failed in (False, True):
            self.library.set_build_variant(selected.id, selected.build_variant)
            self.window.scan_installed()
            with (
                self.subTest(failed=failed),
                patch("appglider.ui.window.QThreadPool.globalInstance") as pool,
            ):
                self.window.checks.add([selected.id])
                self.library.set_build_variant(selected.id, chosen_bambu(index=0).build_variant)
                self.window.catalog_changed()
                signals = self.window.checks.tasks[selected.id].signals
                if failed:
                    signals.failed.emit("Old build failed")
                else:
                    signals.finished.emit(
                        TaskResult("Old build checked", release_infos={selected.id: old_info})
                    )
                self.assertEqual(pool.return_value.start.call_count, 2)
                self.assertEqual(
                    self.window.checks.variants[selected.id], chosen_bambu(index=0).build_variant
                )
                self.assertNotIn(selected.id, self.window.release_infos)
                self.assertNotIn(selected.id, self.window.release_errors)
                self.window.checks.tasks[selected.id].signals.finished.emit(TaskResult("Cancelled"))

    def test_missing_variant_card_requires_choice_before_updating_and_keeps_installed_copy(self):
        selected = chosen_bambu()
        self.library.add_repository(selected)
        payload = appimage_bytes()
        with patch.object(http, "request", return_value=io.BytesIO(payload)):
            installed = self.library.install(
                selected,
                select_release(selected, bambu_metadata(), architecture()),
                Event(),
                lambda *args: None,
            )
        self.window.scan_installed()
        entries = [bambu_metadata("02.09.00.12", variants=("22.04",))]
        cache = self.library.data_dir / "release-cache"
        with patch.object(assets, "api_entries", return_value=entries):
            info = discover_releases(selected, Event(), cache, force=True)
        self.window.release_check_done(
            selected.id,
            selected.build_variant,
            TaskResult("Checked", release_infos={selected.id: info}),
            "",
        )
        self.window.refresh()
        card = self.window.cards[selected.id]
        self.assertEqual(card.action_button.text(), "Choose build")
        self.assertEqual(card.chip.property("state"), "build")
        self.assertEqual(card.chip.text(), "Choose build")
        self.assertTrue(card.action_button.isVisibleTo(card))
        self.assertTrue(card.launch_action.isEnabled())
        self.assertEqual(self.window.updates_count.text(), "1")
        self.assertTrue(self.window.update_all_button.isHidden())
        with (
            patch.object(assets, "api_entries", return_value=entries),
            patch("appglider.install.library.download") as download,
            patch("appglider.ui.pages.app_page.QInputDialog.getItem") as picker,
        ):
            QTest.mouseClick(card.action_button, Qt.MouseButton.LeftButton)
            self.assertIs(self.window.pages.currentWidget(), self.window.app_page)
            self.wait_for_task(self.window)
            picker.assert_not_called()  # one build is left, so nothing to choose between
            download.assert_not_called()
        self.assertEqual(
            next(app for app in self.library.apps if app.id == selected.id).build_variant, ""
        )
        self.assertIn("Build preference saved", self.window.footer.text())
        self.assertEqual(self.library.scan()[selected.id], installed)
        self.assertTrue(installed.path.is_file())
        self.assertEqual(self.window.cards[selected.id].action_button.text(), "Update")
        with patch.object(http, "request", return_value=io.BytesIO(payload)):
            self.window.install_app(selected.id)
            self.wait_for_task(self.window)
        updated = self.library.scan()[selected.id]
        self.assertIn("ubuntu22.04", updated.path.name)
        self.assertEqual(updated.previous, installed.path)

    def test_leaving_the_page_cancels_inspection_and_ignores_its_result(self):
        self.window.open_add_app()
        self.dialog.url.setText(CUSTOM.source_url)

        def lookup(app, cancel, cache):
            cancel.wait(timeout=1)
            check_cancel(cancel)
            return custom_release()

        with patch("appglider.ui.pages.add_page.latest_release", side_effect=lookup):
            self.dialog.inspect_repository()
            task = self.dialog.task
            self.window.nav_library.click()
            self.assertTrue(task.cancel.is_set())
            self.wait_for_task(self.dialog)
        self.assertIsNone(self.dialog.release)
        self.assertFalse(self.dialog.closing)
        self.assertEqual(self.library.apps, APPS)
        self.assertIsNot(self.window.pages.currentWidget(), self.dialog)

    def test_a_result_arriving_after_leaving_does_not_fill_the_form_again(self):
        self.window.open_add_app()
        self.dialog.url.setText(CUSTOM.source_url)
        gate = Event()

        def lookup(app, cancel, cache):
            gate.wait(5)
            return custom_release()  # ignores the cancel: the result still arrives

        with patch("appglider.ui.pages.add_page.latest_release", side_effect=lookup):
            self.dialog.inspect_repository()
            self.window.nav_library.click()
            self.assertIsNotNone(self.dialog.task)
            gate.set()
            self.wait_for_task(self.dialog)
        self.assertIsNone(self.dialog.release)
        self.assertTrue(self.dialog.release_preview.isHidden())
        self.assertFalse(self.dialog.add_button.isEnabled())

    def test_stopping_a_search_says_so_and_late_messages_do_not_hide_it(self):
        self.window.open_add_app()
        self.dialog.url.setText(CUSTOM.source_url)
        release = Event()

        def lookup(app, cancel, progress=None):
            release.wait(5)
            progress("Looking through folders… 9 pages so far")  # arrives after the stop
            check_cancel(cancel)
            return ()

        with (
            patch("appglider.ui.pages.add_page.latest_release", return_value=custom_release()),
            patch("appglider.ui.pages.add_page.discover_icons", side_effect=lookup),
        ):
            self.dialog.inspect_repository()
            self.window.nav_library.click()
            self.assertEqual(self.dialog.status.text(), "Stopping… one moment.")
            release.set()
            self.wait_for_task(self.dialog)

    def test_the_inspection_flow_also_reports_icon_search_progress(self):
        self.window.open_add_app()
        self.dialog.url.setText(CUSTOM.source_url)
        self.dialog.name.setText(CUSTOM.name)
        proceed, shown = Event(), []

        def lookup(app, cancel, progress=None):
            progress("Looking through folders… 3 pages so far")
            proceed.wait(5)
            return ()

        with (
            patch("appglider.ui.pages.add_page.latest_release", return_value=custom_release()),
            patch("appglider.ui.pages.add_page.discover_icons", side_effect=lookup),
        ):
            QTest.mouseClick(self.dialog.inspect_button, Qt.MouseButton.LeftButton)
            deadline = time.monotonic() + 3
            while "3 pages so far" not in self.dialog.status.text() and time.monotonic() < deadline:
                QTest.qWait(10)
            shown.append(self.dialog.status.text())
            self.assertTrue(self.dialog.busy_bar.isVisibleTo(self.dialog))
            proceed.set()
            self.wait_for_task(self.dialog)
        self.assertIn("3 pages so far", shown[0])
        self.assertTrue(self.dialog.busy_bar.isHidden())

    def test_icon_save_failure_does_not_undo_repository_addition(self):
        png = normalise_icon(APP_ICON.read_bytes())
        with patch(
            "appglider.ui.pages.add_page.discover_icons",
            return_value=(IconCandidate("icon.png", png),),
        ):
            self.inspect()
        with patch.object(self.library, "set_icon", side_effect=OSError("Disk full")):
            self.dialog.add_repository()
        self.assertIn(CUSTOM, self.library.apps)
        self.assertIn("Icon could not be saved", self.window.footer.text())
        self.assertIn(CUSTOM.id, self.window.cards)

    def test_custom_app_install_update_tray_and_repository_removal_guard(self):
        self.inspect()
        self.dialog.add_repository()
        payload = appimage_bytes()
        with (
            patch("appglider.ui.window.latest_release", return_value=custom_release()),
            patch("appglider.github.http.request", return_value=io.BytesIO(payload)),
            patch("appglider.ui.window.QMessageBox.warning") as warning,
        ):
            self.window.install_app(CUSTOM.id)
            self.wait_for_task(self.window)
            warning.assert_not_called()
        self.assertEqual(self.window.cards[CUSTOM.id].action_button.text(), "Launch")
        self.assertEqual(self.window.filter_installed.text(), "Installed (1)")
        self.assertIn(
            CUSTOM.name, [action.text() for action in self.window.tray_apps_menu.actions()]
        )
        self.window.open_app(CUSTOM.id)
        self.assertTrue(self.window.app_page.remove_row.isHidden())
        self.assertIn(CUSTOM, self.library.apps)
        self.window.app_page.back_button.click()
        release = custom_release("0.9.5")
        info = ReleaseInfo(ReleaseOption(release.version, release), None, time.time())
        with patch("appglider.ui.window.discover_releases", return_value=info) as discover:
            self.window.refresh_releases()
            deadline = time.monotonic() + 10
            while self.window.checks.busy and time.monotonic() < deadline:
                QTest.qWait(10)
                time.sleep(0.001)
            self.assertFalse(self.window.checks.busy)
            discover.assert_called_once()
            self.assertEqual(discover.call_args.args[0].id, CUSTOM.id)
        self.assertEqual(self.window.updates_count.text(), "1")
        with (
            patch("appglider.ui.window.latest_release", return_value=custom_release("0.9.5")),
            patch("appglider.github.http.request", return_value=io.BytesIO(payload)),
        ):
            self.window.update_all()
            self.wait_for_task(self.window)
        self.assertEqual(self.window.installed[CUSTOM.id].version, "0.9.5")
        with patch(
            "appglider.ui.window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.window.remove_app(CUSTOM.id)
            self.wait_for_task(self.window)
        self.assertEqual(self.window.filter_installed.text(), "Installed (0)")
        self.window.open_app(CUSTOM.id)
        with patch(
            "appglider.ui.pages.app_page.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.window.app_page.remove_button.click()
        self.assertNotIn(CUSTOM.id, self.window.cards)
        self.assertEqual(self.library.scan(), {})

    def test_prerelease_only_repository_can_be_added_after_explicit_opt_in(self):
        self.window.open_add_app()
        self.dialog.url.setText(CUSTOM.source_url)
        self.dialog.name.setText(CUSTOM.name)
        beta = replace(custom_release("0.10.0-beta.1"), prerelease=True, is_latest=False)

        def inspect(app, cancel, cache):
            if not app.include_prereleases:
                raise ValueError(
                    "Only prereleases are available. Choose 'Stable and prerelease versions'."
                )
            return beta

        with patch("appglider.ui.pages.add_page.latest_release", side_effect=inspect):
            self.dialog.inspect_repository()
            self.wait_for_task(self.dialog)
            self.assertIn("Only prereleases", self.dialog.status.text())
            self.assertFalse(self.dialog.add_button.isEnabled())
            self.dialog.release_channel.setCurrentIndex(1, notify=True)
            self.dialog.inspect_repository()
            self.wait_for_task(self.dialog)
        self.assertEqual(self.dialog.version_label.badge.text(), "Pre-release")
        self.assertTrue(self.dialog.add_button.isEnabled())
        self.dialog.add_repository()
        self.assertTrue(self.window.cards[CUSTOM.id].app.include_prereleases)
        reopened = Library(self.library.data_dir, self.library.apps_dir)
        self.assertTrue(reopened.apps[-1].include_prereleases)
        self.assertEqual(self.dialog.release_channel.currentIndex(), 1)

    def test_direct_add_app_opens_the_add_form(self):
        self.window.nav_add.click()
        self.assertTrue(self.dialog.adding)
        self.assertTrue(self.dialog.isVisible())
        self.assertTrue(self.dialog.url.hasFocus())


if __name__ == "__main__":
    unittest.main()
