"""Icons, status line, keyboard use and accessible names of the main window."""

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from catalog_fixture import stock
from gui_helpers import close_window
from PySide6.QtCore import QSettings, Qt, QtMsgType, qInstallMessageHandler
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QLineEdit,
    QWidget,
)

from appglider.core.preferences import install_commands
from appglider.install.hooks import InstallCommands
from appglider.install.library import Library
from appglider.ui.pages.settings_page import checked_folder
from appglider.ui.theme import THEMES, stylesheet
from appglider.ui.window import LauncherWindow


class PolishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setApplicationName("AppGlider tests")
        cls.qt.setStyle("Fusion")

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = QSettings(
            str(Path(self.temp.name) / "settings.ini"), QSettings.Format.IniFormat
        )
        self.settings.setValue("appearance/theme", "Light")
        library = stock(
            Library(
                Path(self.temp.name) / "data",
                Path(self.temp.name) / "apps",
                desktop_dir=Path(self.temp.name) / "desktop",
            )
        )
        self.window = LauncherWindow(self.settings, library)
        self.window.resize(860, 560)
        self.window.show()
        self.qt.processEvents()
        self.addCleanup(close_window, self.window)

    def first_row(self):
        return next(iter(self.window.cards.values()))

    def test_sidebar_and_check_icons_are_drawn_and_follow_the_theme(self) -> None:
        window = self.window
        buttons = (window.nav_library, window.nav_updates, window.nav_add, window.nav_settings)

        def images() -> list[bytes]:
            return [
                bytes(button.icon().pixmap(18, 18).toImage().constBits())
                for button in (*buttons, window.check_button)
            ]

        light = images()
        for button in (*buttons, window.check_button):
            self.assertFalse(button.icon().isNull())
        self.settings.setValue("appearance/theme", "Dark")
        window.apply_theme()
        dark = images()
        for before, after in zip(light, dark, strict=True):
            self.assertTrue(after)
            self.assertNotEqual(before, after)
        self.assertEqual(len({*dark[:4]}), 4)

    def test_status_line_keeps_its_height_so_the_list_does_not_jump(self) -> None:
        window = self.window
        self.assertEqual(window.footer.text(), "")
        self.assertTrue(window.footer.isHidden())
        self.assertFalse(window.status_line.isHidden())
        height = window.status_line.minimumHeight()
        self.assertGreater(height, 0)
        window.footer.setText("Saved icon for X.")
        self.assertFalse(window.footer.isHidden())
        window.footer.setText("")
        self.assertTrue(window.footer.isHidden())
        window.cancel_button.show()
        window.update_status_line()
        window.cancel_button.hide()
        window.update_status_line()
        self.assertEqual(window.status_line.minimumHeight(), height)
        window.scan_installed()
        self.assertNotIn("installed with AppGlider", window.footer.text())
        self.assertFalse(window.status_line.isHidden())

    def test_a_message_expires_once_nothing_is_running(self) -> None:
        window = self.window
        window.footer.setText("Added something.")
        window.expire_footer()
        self.assertEqual(window.footer.text(), "")
        self.assertTrue(window.footer.isHidden())

    def test_ctrl_f_focuses_search_from_any_page(self) -> None:
        window = self.window
        window.open_settings("General")
        QTest.keyClick(window, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
        self.assertIs(window.pages.currentWidget(), window.list_page)
        self.assertTrue(window.search.hasFocus())

    def test_escape_leaves_the_app_and_add_pages(self) -> None:
        window = self.window
        app_id = next(iter(window.cards))
        window.open_app(app_id)
        self.assertIs(window.pages.currentWidget(), window.app_page)
        QTest.keyClick(window, Qt.Key.Key_Escape)
        self.assertIs(window.pages.currentWidget(), window.list_page)
        window.open_add_app()
        QTest.keyClick(window.add_page.url, Qt.Key.Key_Escape)
        self.assertIs(window.pages.currentWidget(), window.list_page)
        self.assertTrue(window.nav_library.isChecked())

    def test_enter_and_space_on_a_row_name_open_the_app_page(self) -> None:
        window = self.window
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Space):
            row = self.first_row()
            row.name_button.setFocus()
            QTest.keyClick(row.name_button, key)
            self.assertIs(window.pages.currentWidget(), window.app_page)
            self.assertEqual(window.app_page.app_id, row.app.id)
            window.show_view("library")
        self.assertEqual(row.name_button.focusPolicy(), Qt.FocusPolicy.TabFocus)

    def test_tab_visits_the_toolbar_before_the_rows(self) -> None:
        window = self.window
        window.search.setFocus()
        seen = []
        for _ in range(8):
            QTest.keyClick(window, Qt.Key.Key_Tab)
            focus = QApplication.focusWidget()
            seen.append(focus)
        self.assertIn(window.options_button, seen)
        names = [widget for widget in seen if widget.objectName() == "appName"]
        self.assertTrue(names)
        self.assertLess(seen.index(window.options_button), seen.index(names[0]))

    def test_every_control_on_every_page_has_an_accessible_name(self) -> None:
        window = self.window
        window.open_app(next(iter(window.cards)))
        pages = [("list", window.list_page), ("app", window.app_page), ("add", window.add_page)]
        problems = []

        def audit(label: str, page: QWidget) -> None:
            for widget in page.findChildren(QWidget):
                if not isinstance(widget, QAbstractButton | QComboBox | QLineEdit):
                    continue
                if isinstance(widget.parent(), QComboBox | QLineEdit):  # Qt's own parts
                    continue
                text = widget.text() if isinstance(widget, QAbstractButton) else ""
                if not (widget.accessibleName() or re.search(r"\w", text)):
                    problems.append((label, type(widget).__name__, widget.objectName()))

        for label, page in pages:
            window.show_page(page)
            self.qt.processEvents()
            audit(label, page)
        for name in window.settings_page.sections:
            name.click()
            audit(f"settings {name.text()}", window.settings_page)
        audit("sidebar", window.findChild(QWidget, "sidebar"))
        row = self.first_row()
        audit("row", row)
        self.assertEqual(problems, [])

    def test_the_tray_menu_and_window_use_the_check_for_updates_wording(self) -> None:
        window = self.window
        self.assertEqual(window.tray_check_action.text(), "Check for updates")
        self.assertEqual(window.check_button.accessibleName(), "Check for updates")
        texts = {button.text() for button in window.findChildren(QAbstractButton)}
        self.assertNotIn("Refresh releases", texts)
        self.assertNotIn("Application preferences", texts)
        window.refresh()
        self.assertEqual(window.tray.toolTip(), "AppGlider · Updates: 0")

    def test_unwritable_folder_is_refused(self) -> None:
        folder = Path(self.temp.name) / "locked"
        folder.mkdir()
        folder.chmod(0o500)
        self.addCleanup(folder.chmod, 0o700)
        if os.access(folder, os.W_OK):
            self.skipTest("Permissions are not enforced for this user")
        chosen, problem = checked_folder(str(folder / "apps" / "deeper"))
        self.assertIsNone(chosen)
        self.assertIn("can't write", problem)
        self.assertIsNotNone(checked_folder(str(Path(self.temp.name) / "new" / "apps"))[0])

    def test_busy_resets_an_unapplied_folder_edit(self) -> None:
        page = self.window.settings_page
        applied = page.applied_folder
        page.folder.setText("/somewhere/else")
        with patch.object(self.window, "task", object()):
            page.update_state()
        self.assertEqual(page.folder.text(), applied)

    def test_command_rows_save_and_apply(self) -> None:
        page = self.window.settings_page
        page.before_command.setText("  echo {file}  ")
        page.before_command.editingFinished.emit()
        page.after_command.setText("echo done")
        page.after_command.editingFinished.emit()
        self.assertEqual(self.settings.value("install/before-command"), "echo {file}")
        self.assertEqual(self.settings.value("install/after-command"), "echo done")
        self.assertEqual(
            self.window.library.commands,
            InstallCommands("echo {file}", "echo done"),
        )
        page.after_command.setText("")
        page.after_command.editingFinished.emit()
        self.assertEqual(self.window.library.commands.after, "")

    def test_invalid_command_shows_the_problem_and_keeps_the_old_one(self) -> None:
        page = self.window.settings_page
        page.before_command.setText("echo {file}")
        page.before_command.editingFinished.emit()
        page.before_command.setText("echo 'unclosed")
        page.before_command.editingFinished.emit()
        self.assertFalse(page.before_error.isHidden())
        self.assertNotEqual(page.before_error.text(), "")
        self.assertEqual(self.settings.value("install/before-command"), "echo {file}")
        self.assertEqual(self.window.library.commands.before, "echo {file}")
        page.before_command.setText("echo again")
        page.before_command.editingFinished.emit()
        self.assertTrue(page.before_error.isHidden())

    def test_command_rows_wait_for_a_running_task(self) -> None:
        page = self.window.settings_page
        page.before_command.setText("echo {file}")
        page.before_command.editingFinished.emit()
        page.before_command.setText("echo unapplied")
        with patch.object(self.window, "task", object()):
            page.update_state()
            self.assertTrue(page.before_command.isEnabled())
            self.assertFalse(page.commands_wait_note.isHidden())
            page.before_command.editingFinished.emit()
            self.assertEqual(page.before_command.text(), "echo unapplied")  # kept, not dropped
            self.assertEqual(self.settings.value("install/before-command"), "echo {file}")
        page.update_state()
        self.assertTrue(page.commands_wait_note.isHidden())
        self.assertEqual(page.before_command.text(), "echo unapplied")
        self.assertEqual(self.settings.value("install/before-command"), "echo unapplied")

    def test_saved_commands_apply_at_startup(self) -> None:
        self.settings.setValue("install/before-command", " echo {file} ")
        self.settings.setValue("install/after-command", "echo hi")
        library = Library(
            Path(self.temp.name) / "data2",
            Path(self.temp.name) / "apps2",
            desktop_dir=Path(self.temp.name) / "desktop2",
        )
        window = LauncherWindow(self.settings, stock(library))
        self.addCleanup(close_window, window)
        self.assertEqual(window.library.commands, InstallCommands("echo {file}", "echo hi"))
        self.assertEqual(window.settings_page.before_command.text(), "echo {file}")

    def test_install_commands_helper_ignores_bad_values(self) -> None:
        self.assertEqual(install_commands(self.settings), InstallCommands())
        self.settings.setValue("install/before-command", ["a", "b"])
        self.settings.setValue("install/after-command", "  x  ")
        self.assertEqual(install_commands(self.settings), InstallCommands("", "x"))

    def test_move_waits_for_the_update_check(self) -> None:
        page = self.window.settings_page
        with (
            patch.object(self.window.checks, "tasks", {"x": object()}),
            patch.object(page, "move_source", return_value=Path("/tmp/AppGlider.AppImage")),
            patch.object(self.window, "move_self") as move,
        ):
            page.update_state()
            self.assertFalse(page.move_button.isEnabled())
            page.move_self()
        move.assert_not_called()
        self.assertEqual(
            self.window.footer.text(), "Wait for the update check to finish, then try again."
        )


class StylesheetPathTests(unittest.TestCase):
    def test_cache_path_with_space_and_apostrophe_keeps_the_stylesheet(self) -> None:
        qt = QApplication.instance() or QApplication([])
        messages: list[str] = []

        def handler(kind: QtMsgType, context: object, message: str) -> None:
            messages.append(message)

        with tempfile.TemporaryDirectory() as temp:
            cache = str(Path(temp) / "o'brien home" / "cache")
            previous_sheet = qt.styleSheet()
            self.addCleanup(qt.setStyleSheet, previous_sheet)
            with patch("appglider.ui.theme.QStandardPaths.writableLocation", return_value=cache):
                sheet = stylesheet(THEMES["Light"], "#2f6fed", False)
            previous = qInstallMessageHandler(handler)
            try:
                qt.setStyleSheet(sheet)
                # Qt parses the sheet when the first widget is polished.
                combo = QComboBox()
                combo.ensurePolished()
                combo.deleteLater()
            finally:
                qInstallMessageHandler(previous)
        self.assertEqual([m for m in messages if "Could not parse" in m], [])


if __name__ == "__main__":
    unittest.main()
