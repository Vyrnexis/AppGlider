"""Add page, Settings and catalog preview: errors, flow order, commands, folder and focus."""

import os
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import test_manual_apps as manual
from catalog_fixture import APPS, stock
from gui_helpers import close_window
from PySide6.QtCore import QSettings, QStandardPaths, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel
from test_file_flow import GuiBase
from test_repositories import CUSTOM, custom_release

from appglider import APP_ICON
from appglider.github.icons import IconCandidate, normalise_icon
from appglider.install.library import Library
from appglider.ui.pages import add_page as add_module
from appglider.ui.pages.add_page import FILE_NOTE, start_folder
from appglider.ui.pages.catalog_preview import CatalogPreviewDialog
from appglider.ui.pages.settings_page import WAIT_NOTE, SettingsPage, not_saved
from appglider.ui.theme import THEMES, stylesheet
from appglider.ui.window import LauncherWindow


class AddPageTests(GuiBase):
    def setUp(self) -> None:
        super().setUp()
        icons = patch("appglider.ui.pages.add_page.discover_icons", return_value=())
        icons.start()
        self.addCleanup(icons.stop)
        self.page = self.window.add_page

    def look_up(self) -> None:
        page = self.page
        self.window.open_add_app()
        page.url.setText(CUSTOM.source_url)
        page.name.setText(CUSTOM.name)
        with patch("appglider.ui.pages.add_page.latest_release", return_value=custom_release()):
            page.inspect_button.click()
            self.wait(lambda: page.task is None)

    def test_errors_are_red_and_progress_is_muted_with_an_accessible_name(self) -> None:
        page = self.page
        self.window.open_add_app()
        self.assertEqual(page.status.accessibleName(), "Add app status")
        page.url.setText("not a repository")
        page.inspect_repository()
        self.assertEqual(page.status.objectName(), "settingError")
        self.look_up()
        self.assertEqual(page.status.objectName(), "muted")
        self.assertIn("Ready to add", page.status.text())

    def test_a_duplicate_names_the_app_already_in_the_library(self) -> None:
        page = self.page
        self.window.open_add_app()
        page.url.setText(f"https://github.com/{APPS[0].repo}")
        page.inspect_repository()
        self.assertEqual(page.status.text(), f"Already in your library as {APPS[0].name}.")
        self.assertEqual(page.status.objectName(), "settingError")

    def test_the_file_panel_comes_last_and_the_result_sits_above_the_buttons(self) -> None:
        page = self.page
        self.choose(self.source())

        def top(widget) -> int:
            return widget.mapTo(page.widget(), widget.rect().topLeft()).y()

        self.assertLess(top(page.url), top(page.release_preview))
        self.assertLess(top(page.release_preview), top(page.icon_group))
        self.assertLess(top(page.icon_group), top(page.add_button))
        self.assertLess(top(page.add_button), top(page.file_frame))

    def test_a_chosen_file_hides_the_repository_controls_and_is_not_discarded(self) -> None:
        page = self.page
        self.choose(self.source())
        self.assertTrue(page.inspect_button.isHidden())
        self.assertTrue(page.channel_row.isHidden())
        self.assertEqual(page.file_button.text(), "Choose another…")
        self.assertIn(manual.NAME, page.file_chosen.text())
        self.assertTrue(page.install_button.isHidden())
        page.inspect_repository()
        self.assertIsNotNone(page.manual)
        self.assertEqual(page.status.objectName(), "settingError")
        page.clear_file()
        self.assertIsNone(page.manual)
        self.assertFalse(page.inspect_button.isHidden())
        self.assertTrue(page.file_clear_button.isHidden())

    def test_the_file_note_is_said_once(self) -> None:
        page = self.page
        self.choose(self.source())
        texts = [label.text() for label in page.findChildren(QLabel)]
        self.assertEqual(sum(FILE_NOTE in text for text in texts), 1)

    def test_a_file_copy_drives_a_determinate_bar_beside_the_button(self) -> None:
        page = self.page
        self.choose(self.source())
        page.name.setText("My Sketchpad")
        release = Event()
        self.addCleanup(release.set)

        def slow(app, source, version, png, cancel=None, progress=None, **kwargs):
            progress("Copying…", 50, 100)
            release.wait(5)
            raise ValueError("stopped")

        with patch.object(self.library, "add_file", side_effect=slow):
            page.add_button.click()
            self.wait(lambda: page.busy_bar.maximum() == 1000)
            self.assertEqual(page.busy_bar.value(), 500)
            self.assertFalse(page.busy_bar.isHidden())
            release.set()
            self.wait(lambda: page.task is None)
        self.assertEqual(page.busy_bar.maximum(), 0)
        self.assertEqual(page.status.objectName(), "settingError")

    def test_pickers_open_in_downloads_with_a_home_fallback(self) -> None:
        page = self.page
        downloads = str(self.downloads)
        self.window.open_add_app()
        with patch.object(QStandardPaths, "writableLocation", return_value=downloads):
            self.assertEqual(start_folder(), downloads)
            with patch.object(
                add_module.QFileDialog, "getOpenFileName", return_value=("", "")
            ) as pick:
                page.choose_file()
                self.assertEqual(pick.call_args.args[2], downloads)
        with patch.object(QStandardPaths, "writableLocation", return_value=""):
            self.assertEqual(start_folder(), str(Path.home()))

    def test_a_chosen_icon_file_is_added_to_the_options_and_selected(self) -> None:
        page = self.page
        self.look_up()
        png = normalise_icon(APP_ICON.read_bytes())
        page.set_icon_options((IconCandidate("one.png", png), IconCandidate("two.png", png)))
        image = self.root / "mine.png"
        image.write_bytes(png)
        with patch.object(add_module.QFileDialog, "getOpenFileName", return_value=(str(image), "")):
            page.choose_icon()
        self.assertEqual(
            [icon.name for icon in page.icon_options], ["one.png", "two.png", "mine.png"]
        )
        self.assertEqual(page.icon_choices.currentIndex(), 2)
        self.assertEqual(page.selected_icon, png)

    def test_one_primary_button_and_a_tooltip_on_the_disabled_add(self) -> None:
        page = self.page
        self.window.open_add_app()
        self.assertEqual(page.inspect_button.objectName(), "primary")
        self.assertEqual(page.install_button.objectName(), "")
        self.assertEqual(page.add_button.objectName(), "")
        self.assertFalse(page.add_button.isEnabled())
        self.assertIn("Look up an app", page.add_button.toolTip())
        self.assertIn("Look up an app", page.install_button.toolTip())
        self.assertEqual(page.inspect_button.text(), "Look up app")
        self.look_up()
        self.assertEqual(page.inspect_button.objectName(), "")
        self.assertEqual(page.install_button.objectName(), "primary")
        self.assertEqual(page.add_button.objectName(), "")
        self.assertEqual(page.install_button.text(), "Add and install")
        self.assertEqual(page.checksum.text(), "Checksum found; checked after download")

    def test_a_file_has_a_single_primary_add(self) -> None:
        page = self.page
        self.choose(self.source())
        self.assertEqual(page.add_button.objectName(), "primary")
        self.assertTrue(page.install_button.isHidden())
        self.assertEqual(page.inspect_button.objectName(), "")

    def test_add_and_install_emits_its_own_signal(self) -> None:
        page = self.page
        self.look_up()
        added, installing = [], []
        page.added.connect(lambda *args: added.append(args))
        page.add_and_install.connect(lambda *args: installing.append(args))
        page.install_button.click()
        self.assertEqual(added, [])
        self.assertEqual([item[0] for item in installing], [CUSTOM.id])
        self.assertIn(CUSTOM.id, {app.id for app in self.library.apps})

    def test_add_to_library_still_emits_added(self) -> None:
        page = self.page
        self.look_up()
        added, installing = [], []
        page.added.connect(lambda *args: added.append(args))
        page.add_and_install.connect(lambda *args: installing.append(args))
        page.add_button.click()
        self.assertEqual([item[0] for item in added], [CUSTOM.id])
        self.assertEqual(installing, [])

    def test_changing_the_release_channel_after_a_lookup_says_to_look_up_again(self) -> None:
        page = self.page
        self.look_up()
        page.release_channel.setCurrentIndex(1, notify=True)
        self.assertEqual(page.status.text(), "Release channel changed. Inspect again.")
        self.assertFalse(page.install_button.isEnabled())

    def test_enter_in_the_name_field_adds_when_ready(self) -> None:
        page = self.page
        self.look_up()
        added, installing = [], []
        page.added.connect(lambda *args: added.append(args))
        page.add_and_install.connect(lambda *args: installing.append(args))
        page.name.returnPressed.emit()
        self.assertEqual([item[0] for item in added], [CUSTOM.id])
        self.assertEqual(installing, [])  # Enter never starts a download

    def test_enter_in_the_name_field_does_nothing_before_it_is_ready(self) -> None:
        page = self.page
        self.window.open_add_app()
        page.name.returnPressed.emit()
        self.assertEqual(len(self.library.apps), len(APPS))

    def test_the_user_visible_wording_avoids_prerelease(self) -> None:
        page = self.page
        names = [button.accessibleName() for button in page.release_channel.buttons]
        self.assertFalse(any("prerelease" in name.lower() for name in names))


class SettingsBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setStyle("Fusion")

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.library = stock(Library(self.root / "data", self.root / "apps", self.root / "desktop"))
        self.window = LauncherWindow(self.settings, self.library)
        self.window.resize(1040, 700)
        self.window.show()
        self.addCleanup(close_window, self.window)
        self.page = self.window.settings_page
        self.qt.processEvents()

    def busy(self):
        return patch.object(self.window, "task", object())


class InstallCommandsTests(SettingsBase):
    def test_the_commands_live_in_their_own_panel_at_the_end_of_updates(self) -> None:
        page = self.page
        headings = [label.accessibleName() for label in page.stack.widget(1).findChildren(QLabel)]
        self.assertEqual(headings.count("Install commands"), 1)
        texts = [label.text() for label in page.stack.widget(1).findChildren(QLabel)]
        self.assertIn("Advanced: run your own programs on each install. See the user guide.", texts)
        self.assertIn("How often AppGlider looks for new versions.", texts)
        self.assertIn("Runs after each install. A failure only shows a warning.", texts)

    def test_refused_commands_say_not_saved_and_that_the_old_one_is_used(self) -> None:
        page = self.page
        page.before_command.setText("echo 'open")
        page.before_command.editingFinished.emit()
        self.assertEqual(
            page.before_error.text(),
            "Not saved: check the quotes. The previous command is still used.",
        )
        page.before_command.setText("nosuchprogram-xyz {file}")
        page.before_command.editingFinished.emit()
        self.assertEqual(
            page.before_error.text(),
            "Not saved: nosuchprogram-xyz was not found. The previous command is still used.",
        )
        self.assertEqual(
            not_saved("x was not found."),
            "Not saved: x was not found. The previous command is still used.",
        )

    def test_a_saved_command_shows_saved_for_a_moment(self) -> None:
        page = self.page
        self.assertTrue(page.command_saved["before"].isHidden())
        page.before_command.setText("echo {file}")
        page.before_command.editingFinished.emit()
        self.assertFalse(page.command_saved["before"].isHidden())
        timer = page.command_timers["before"]
        self.assertTrue(timer.isActive())
        self.assertEqual(timer.interval(), 3000)
        timer.timeout.emit()
        self.assertTrue(page.command_saved["before"].isHidden())
        page.before_command.editingFinished.emit()  # nothing changed: no new "Saved"
        self.assertTrue(page.command_saved["before"].isHidden())

    def test_a_command_typed_during_a_task_is_kept_and_saved_afterwards(self) -> None:
        page = self.page
        page.before_command.setText("echo late")
        with self.busy():
            page.update_state()
            self.assertFalse(page.commands_wait_note.isHidden())
            self.assertEqual(page.commands_wait_note.text(), WAIT_NOTE)
            page.before_command.editingFinished.emit()
            self.assertEqual(page.before_command.text(), "echo late")
            self.assertEqual(self.window.library.commands.before, "")
        page.update_state()
        self.assertEqual(self.window.library.commands.before, "echo late")
        self.assertFalse(page.command_saved["before"].isHidden())


class FolderAndCatalogTests(SettingsBase):
    def test_a_missing_folder_is_not_applied_and_says_why(self) -> None:
        page = self.page
        before = page.applied_folder
        page.folder.setText(str(self.root / "nowhere"))
        page.folder.editingFinished.emit()
        self.assertEqual(page.applied_folder, before)
        self.assertIn("does not exist", page.folder_error.text())
        self.assertTrue(page.folder_note.isHidden())

    def test_an_existing_folder_applies_on_enter_and_confirms_inline(self) -> None:
        page = self.page
        chosen = self.root / "elsewhere"
        chosen.mkdir()
        page.folder.setText(str(chosen))
        page.folder.editingFinished.emit()
        self.assertEqual(page.applied_folder, str(chosen))
        self.assertFalse(page.folder_note.isHidden())
        self.assertEqual(page.folder_note.text(), "Applies to apps you install next.")
        page.folder.editingFinished.emit()  # leaving the field afterwards keeps the note
        self.assertFalse(page.folder_note.isHidden())
        page.folder.textEdited.emit("x")
        self.assertTrue(page.folder_note.isHidden())

    def test_tasks_show_one_muted_line_on_the_blocked_panels(self) -> None:
        page = self.page
        self.assertTrue(all(note.isHidden() for note in page.wait_notes))
        with self.busy():
            page.update_state()
            self.assertEqual(len(page.wait_notes), 3)
            for note in page.wait_notes:
                self.assertEqual(note.text(), WAIT_NOTE)
                self.assertEqual(note.objectName(), "muted")
                self.assertFalse(note.isHidden())
        page.update_state()
        self.assertTrue(all(note.isHidden() for note in page.wait_notes))

    def test_the_import_result_is_also_shown_in_the_catalogs_panel(self) -> None:
        page = self.page
        page.say_catalog("Added 2 apps from x.toml.")
        self.assertEqual(page.catalog_note.text(), "Added 2 apps from x.toml.")
        self.assertEqual(page.catalog_note.objectName(), "muted")
        self.assertEqual(self.window.footer.text(), "Added 2 apps from x.toml.")
        page.say_catalog("Cannot import x.toml: bad.", error=True)
        self.assertEqual(page.catalog_note.objectName(), "settingError")

    def test_catalog_wording_does_not_mention_toml(self) -> None:
        page = self.page
        for widget in (page.import_button, page.export_button):
            self.assertNotIn("TOML", widget.toolTip())
        texts = [label.text() for label in page.stack.widget(2).findChildren(QLabel)]
        self.assertFalse(any("TOML" in text for text in texts))

    def test_without_a_system_tray_the_close_to_tray_row_is_disabled(self) -> None:
        with patch("appglider.ui.pages.settings_page.tray_available", return_value=False):
            page = SettingsPage(self.window)
        self.addCleanup(page.deleteLater)
        self.assertFalse(page.close_to_tray.isEnabled())
        hints = [label.text() for label in page.findChildren(QLabel)]
        self.assertIn("No system tray found on this desktop.", hints)
        with patch("appglider.ui.pages.settings_page.tray_available", return_value=True):
            other = SettingsPage(self.window)
        self.addCleanup(other.deleteLater)
        self.assertTrue(other.close_to_tray.isEnabled())


class FocusTests(SettingsBase):
    def test_section_buttons_and_swatches_take_the_keyboard_and_show_a_ring(self) -> None:
        page = self.page
        for button in (*page.sections, *page.accents.values()):
            self.assertNotEqual(button.focusPolicy(), Qt.FocusPolicy.NoFocus, button.text())
        for name, dark in (("Dark", True), ("Light", False)):
            sheet = stylesheet(THEMES[name], "#3563d8", dark)
            self.assertIn("QPushButton#sectionNav:focus", sheet)
            self.assertIn("QPushButton#swatch:focus", sheet)

    def test_tab_moves_the_focus_through_the_section_buttons_and_swatches(self) -> None:
        page = self.page
        self.window.open_settings("General")
        page.sections[0].setFocus()
        seen = set()
        for _ in range(12):
            QTest.keyClick(self.window, Qt.Key.Key_Tab)
            focused = self.qt.focusWidget()
            if focused is not None:
                seen.add(focused)
        self.assertTrue(any(widget in seen for widget in page.accents.values()))


class CatalogPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt = QApplication.instance() or QApplication([])

    def test_enter_accepts_and_a_preview_with_nothing_new_closes(self) -> None:
        from appglider.core.catalogfile import CatalogEntry, ParsedCatalog
        from appglider.install.library import ImportPlan

        entry = CatalogEntry("owner/one", "One")
        catalog = ParsedCatalog("Tools", (entry,), (), ())
        full = CatalogPreviewDialog(catalog, ImportPlan((entry,), ()), None)  # type: ignore[arg-type]
        self.addCleanup(full.deleteLater)
        self.assertTrue(full.add_button.isDefault())
        self.assertEqual(full.add_button.text(), "Add 1 app")
        empty = CatalogPreviewDialog(catalog, ImportPlan((), (entry,)), None)  # type: ignore[arg-type]
        self.addCleanup(empty.deleteLater)
        self.assertTrue(empty.add_button.isDefault())
        self.assertEqual(empty.add_button.text(), "Close")
        self.assertTrue(empty.add_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
