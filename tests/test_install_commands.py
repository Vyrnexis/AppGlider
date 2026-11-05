"""Commands run before and after an install, add or replace."""

import io
import os
import shlex
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import patch

from catalog_fixture import APPS, stock
from gui_helpers import close_window
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_library import APP, appimage_bytes, ignore_progress, release_for

from appglider.core.catalog import manual_app
from appglider.github.versions import Cancelled
from appglider.install import hooks
from appglider.install.hooks import InstallCommandFailed, InstallCommands, command_problem
from appglider.install.library import Library
from appglider.ui.window import LauncherWindow

NAME = "Sketchpad-1.2.0-x86_64.AppImage"


class CommandBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scripts = self.root / "scripts"
        self.scripts.mkdir()
        self.library = stock(
            Library(
                self.root / "data",
                self.root / "apps",
                desktop_dir=self.root / "desktop",
                autostart_dir=self.root / "autostart",
            )
        )

    def script(self, name: str, body: str) -> str:
        path = self.scripts / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)
        return shlex.quote(str(path))

    def install(self, version: str, warnings: list[str] | None = None, cancel=None):
        data = appimage_bytes(version.encode())
        release = release_for(version, data)
        with patch("appglider.github.http.request", return_value=io.BytesIO(data)):
            return self.library.install(APP, release, cancel or Event(), ignore_progress, warnings)

    def files_under(self, folder: Path) -> list[str]:
        return sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())

    def source(self, name: str = NAME, payload: bytes = b"one") -> Path:
        folder = self.root / "Downloads"
        folder.mkdir(exist_ok=True)
        path = folder / name
        path.write_bytes(appimage_bytes(payload))
        return path

    def cancel_when_started(self, marker: Path, cancel: Event) -> threading.Thread:
        def wait() -> None:
            deadline = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            time.sleep(0.2)
            cancel.set()

        thread = threading.Thread(target=wait)
        thread.start()
        return thread


class PlaceholderAndEnvironmentTests(CommandBase):
    def test_file_placeholder_replaces_inside_arguments(self) -> None:
        out = self.root / "argv.txt"
        self.library.commands = InstallCommands(
            before=self.script("rec.sh", f'printf "%s\\n" "$@" > {shlex.quote(str(out))}')
            + " --path={file} extra"
        )
        installed = self.install("1.0.0")
        lines = out.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("--path="))
        self.assertIn(".download-", lines[0])  # the staged file, before activation
        self.assertEqual(lines[1], "extra")
        self.assertTrue(installed.path.is_file())

    def test_path_is_appended_when_there_is_no_placeholder(self) -> None:
        out = self.root / "argv.txt"
        self.library.commands = InstallCommands(
            after=self.script("rec.sh", f'printf "%s\\n" "$@" > {shlex.quote(str(out))}') + " one"
        )
        installed = self.install("1.0.0")
        self.assertEqual(out.read_text().splitlines(), ["one", str(installed.path)])

    def test_a_program_under_home_is_expanded(self) -> None:
        with patch.dict(os.environ, {"HOME": "/home/someone"}):
            arguments = hooks._arguments("~/bin/scan --quiet", Path("/apps/x.AppImage"))
        self.assertEqual(arguments, ["/home/someone/bin/scan", "--quiet", "/apps/x.AppImage"])

    def test_environment_is_set_and_appimage_variables_are_removed(self) -> None:
        out = self.root / "env.txt"
        body = (
            '{ echo "stage=$APPGLIDER_STAGE"; echo "file=$APPGLIDER_FILE"; '
            'echo "app=$APPGLIDER_APP"; echo "id=$APPGLIDER_APP_ID"; '
            'echo "version=$APPGLIDER_VERSION"; echo "old=$APPGLIDER_OLD_VERSION"; '
            'echo "appimage=${APPIMAGE-unset}"; echo "appdir=${APPDIR-unset}"; '
            'echo "ld=${LD_LIBRARY_PATH-unset}"; echo "cwd=$(pwd)"; } '
            f"> {shlex.quote(str(out))}.$APPGLIDER_STAGE"
        )
        command = self.script("env.sh", body)
        self.library.commands = InstallCommands(before=command, after=command)
        self.install("1.0.0")
        with patch.dict(
            os.environ,
            {"APPIMAGE": "/x.AppImage", "APPDIR": "/mnt", "LD_LIBRARY_PATH": "/mnt/lib"},
        ):
            installed = self.install("1.1.0")

        def read(stage: str) -> dict[str, str]:
            lines = Path(f"{out}.{stage}").read_text().splitlines()
            return dict(line.split("=", 1) for line in lines)

        before, after = read("before"), read("after")
        self.assertEqual(before["stage"], "before")
        self.assertEqual(after["stage"], "after")
        for seen in (before, after):
            self.assertEqual(seen["app"], APP.name)
            self.assertEqual(seen["id"], APP.id)
            self.assertEqual(seen["version"], "1.1.0")
            self.assertEqual(seen["old"], "1.0.0")
            self.assertEqual(seen["appimage"], "unset")
            self.assertEqual(seen["appdir"], "unset")
            self.assertEqual(seen["ld"], "unset")
        self.assertEqual(after["file"], str(installed.path))
        self.assertEqual(Path(after["cwd"]).resolve(), installed.path.parent.resolve())

    def test_first_install_has_an_empty_old_version(self) -> None:
        out = self.root / "old.txt"
        self.library.commands = InstallCommands(
            before=self.script(
                "old.sh", f'echo "[$APPGLIDER_OLD_VERSION]" > {shlex.quote(str(out))}'
            )
        )
        self.install("1.0.0")
        self.assertEqual(out.read_text().strip(), "[]")

    def test_empty_commands_start_nothing(self) -> None:
        with patch("appglider.install.hooks._execute") as popen:
            self.install("1.0.0")
            self.install("1.1.0")
            source = self.source()
            self.library.add_file(manual_app("Sketchpad", NAME), source, "1.2.0", None)
        popen.assert_not_called()

    def test_rollback_runs_no_command(self) -> None:
        self.install("1.0.0")
        self.install("1.1.0")
        marker = self.root / "ran"
        self.library.commands = InstallCommands(
            before=self.script("ran.sh", f"touch {shlex.quote(str(marker))}"),
            after=self.script("ran2.sh", f"touch {shlex.quote(str(marker))}"),
        )
        self.library.rollback(APP)
        self.assertFalse(marker.exists())

    def test_a_version_that_is_already_active_runs_no_command(self) -> None:
        installed = self.install("1.0.0")
        marker = self.root / "ran"
        self.library.commands = InstallCommands(
            before=self.script("ran.sh", f"touch {shlex.quote(str(marker))}"),
            after=self.script("ran2.sh", f"touch {shlex.quote(str(marker))}"),
        )
        again = self.install("1.0.0")
        self.assertEqual(again.path, installed.path)
        self.assertFalse(marker.exists())


class BeforeFailureTests(CommandBase):
    def test_a_failed_before_command_keeps_the_old_version_and_leaves_no_stage(self) -> None:
        first = self.install("1.0.0")
        self.library.commands = InstallCommands(
            before=self.script("fail.sh", "echo not today >&2; exit 3")
        )
        with self.assertRaises(InstallCommandFailed) as raised:
            self.install("1.1.0")
        message = str(raised.exception)
        self.assertIn(APP.name, message)
        self.assertIn("1.1.0", message)
        self.assertIn("exit code 3", message)
        self.assertIn("not today", message)
        self.assertTrue(message.endswith("Your current version was kept."))
        self.assertEqual(self.library.records()[APP.id].path, first.path)
        self.assertEqual(self.library.records()[APP.id].version, "1.0.0")
        names = self.files_under(self.root / "apps" / APP.id)
        self.assertFalse([name for name in names if ".download-" in name])
        self.assertEqual(len([n for n in names if n.endswith(".AppImage")]), 1)

    def test_a_failed_first_install_leaves_nothing_and_records_nothing(self) -> None:
        self.library.commands = InstallCommands(before=self.script("fail.sh", "exit 1"))
        with self.assertRaisesRegex(InstallCommandFailed, f"{APP.name} was not installed."):
            self.install("1.0.0")
        self.assertNotIn(APP.id, self.library.records())
        self.assertFalse((self.root / "apps" / APP.id).exists())

    def test_a_command_that_cannot_start_fails_the_install(self) -> None:
        self.library.commands = InstallCommands(before="/nonexistent/program")
        with self.assertRaisesRegex(InstallCommandFailed, "couldn't start"):
            self.install("1.0.0")
        self.assertNotIn(APP.id, self.library.records())

    def test_failure_on_a_reused_file_keeps_the_record(self) -> None:
        self.install("1.0.0")
        self.install("1.1.0")
        self.library.rollback(APP)  # 1.0.0 active, 1.1.0 still on disk
        self.library.commands = InstallCommands(before=self.script("fail.sh", "exit 2"))
        with self.assertRaises(InstallCommandFailed):
            self.install("1.1.0")
        self.assertEqual(self.library.records()[APP.id].version, "1.0.0")

    def test_add_file_failure_leaves_nothing_behind(self) -> None:
        self.library.commands = InstallCommands(before=self.script("fail.sh", "exit 4"))
        source = self.source()
        app = manual_app("Sketchpad", NAME)
        with self.assertRaisesRegex(InstallCommandFailed, "Sketchpad was not added."):
            self.library.add_file(app, source, "1.2.0", None)
        self.assertEqual(self.library.records(), {})
        self.assertFalse(any(known.id == app.id for known in self.library.apps))
        self.assertFalse((self.root / "apps" / app.id).exists())
        self.assertTrue(source.exists())

    def test_replace_file_failure_keeps_the_current_file(self) -> None:
        app = manual_app("Sketchpad", NAME)
        self.library.add_file(app, self.source(), "1.2.0", None)
        current = self.library.records()[app.id]
        newer = self.source("Sketchpad-1.3.0-x86_64.AppImage", b"two")
        self.library.commands = InstallCommands(before=self.script("fail.sh", "exit 5"))
        with self.assertRaisesRegex(InstallCommandFailed, "exit code 5"):
            self.library.replace_file(app, newer, "1.3.0")
        self.assertEqual(self.library.records()[app.id], current)
        self.assertEqual(
            [p.name for p in current.path.parent.parent.iterdir() if p.is_dir()],
            [current.path.parent.name],
        )


class AfterCommandTests(CommandBase):
    def test_a_failed_after_command_warns_and_keeps_the_install(self) -> None:
        self.library.commands = InstallCommands(after=self.script("fail.sh", "echo oops; exit 3"))
        warnings: list[str] = []
        installed = self.install("1.0.0", warnings)
        self.assertEqual(len(warnings), 1)
        self.assertIn(f"The after-install command failed for {APP.name}", warnings[0])
        self.assertIn("exit code 3", warnings[0])
        self.assertIn("oops", warnings[0])
        self.assertEqual(self.library.records()[APP.id].path, installed.path)
        self.assertTrue(installed.path.is_file())

    def test_a_good_after_command_adds_no_warning(self) -> None:
        self.library.commands = InstallCommands(after=self.script("ok.sh", "exit 0"))
        warnings: list[str] = []
        self.install("1.0.0", warnings)
        self.assertEqual(warnings, [])

    def test_add_and_replace_report_after_command_warnings(self) -> None:
        self.library.commands = InstallCommands(after=self.script("fail.sh", "exit 6"))
        app = manual_app("Sketchpad", NAME)
        found: list[str] = []
        self.library.add_file(app, self.source(), "1.2.0", None, warnings=found)
        self.assertEqual(len(found), 1)
        self.assertIn(app.id, self.library.records())
        newer = self.source("Sketchpad-1.3.0-x86_64.AppImage", b"two")
        self.library.replace_file(app, newer, "1.3.0", warnings=found)
        self.assertEqual(len(found), 2)
        self.assertEqual(self.library.records()[app.id].version, "1.3.0")

    def test_cancel_during_the_after_command_is_a_warning(self) -> None:
        pidfile = self.root / "pid"
        self.library.commands = InstallCommands(
            after=self.script("slow.sh", f"echo $$ > {shlex.quote(str(pidfile))}; sleep 30")
        )
        cancel = Event()
        warnings: list[str] = []
        thread = self.cancel_when_started(pidfile, cancel)
        installed = self.install("1.0.0", warnings, cancel)
        thread.join()
        self.assertEqual(len(warnings), 1)
        self.assertIn("cancelled", warnings[0])
        self.assertEqual(self.library.records()[APP.id].path, installed.path)


class RunningTests(CommandBase):
    def test_cancel_kills_the_whole_process_group(self) -> None:
        pidfile = self.root / "child.pid"
        self.library.commands = InstallCommands(
            before=self.script(
                "tree.sh", f"sleep 60 &\necho $! > {shlex.quote(str(pidfile))}\nwait"
            )
        )
        cancel = Event()
        thread = self.cancel_when_started(pidfile, cancel)
        with self.assertRaises(Cancelled):
            self.install("1.0.0", None, cancel)
        thread.join()
        child = int(pidfile.read_text())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        with self.assertRaises(ProcessLookupError):
            os.kill(child, 0)
        self.assertNotIn(APP.id, self.library.records())

    def test_a_before_command_that_runs_too_long_fails_the_install(self) -> None:
        self.library.commands = InstallCommands(before=self.script("slow.sh", "sleep 60"))
        with (
            patch.object(hooks, "BEFORE_TIMEOUT", 0.3),
            self.assertRaisesRegex(InstallCommandFailed, "timed out"),
        ):
            self.install("1.0.0")
        self.assertNotIn(APP.id, self.library.records())

    def test_an_after_command_that_runs_too_long_is_a_warning(self) -> None:
        self.library.commands = InstallCommands(after=self.script("slow.sh", "sleep 60"))
        warnings: list[str] = []
        with patch.object(hooks, "AFTER_TIMEOUT", 0.3):
            self.install("1.0.0", warnings)
        self.assertEqual(len(warnings), 1)
        self.assertIn("timed out", warnings[0])

    def test_the_last_line_of_long_output_is_kept(self) -> None:
        loop = "i=0; while [ $i -lt 500 ]; do echo line$i; i=$((i+1)); done; exit 9"
        self.library.commands = InstallCommands(before=self.script("noisy.sh", loop))
        with self.assertRaisesRegex(InstallCommandFailed, "line499"):
            self.install("1.0.0")

    def test_progress_names_the_commands(self) -> None:
        self.library.commands = InstallCommands(
            before=self.script("ok.sh", "exit 0"), after=self.script("ok2.sh", "exit 0")
        )
        messages: list[str] = []
        data = appimage_bytes(b"1.0.0")
        with patch("appglider.github.http.request", return_value=io.BytesIO(data)):
            self.library.install(
                APP,
                release_for("1.0.0", data),
                Event(),
                lambda message, done, total: messages.append(message),
            )
        self.assertIn(f"Checking {APP.name} with your before-install command…", messages)
        self.assertIn("Running your after-install command…", messages)


class CommandProblemTests(unittest.TestCase):
    def test_empty_and_valid_commands_have_no_problem(self) -> None:
        self.assertEqual(command_problem(""), "")
        self.assertEqual(command_problem("   "), "")
        self.assertEqual(command_problem("sh -c 'echo {file}'"), "")
        self.assertEqual(command_problem("/bin/sh"), "")

    def test_bad_quotes_are_explained(self) -> None:
        self.assertEqual(command_problem("echo 'oops"), "Check the quotes in the command.")

    def test_a_missing_program_is_named(self) -> None:
        self.assertEqual(
            command_problem("no-such-program-here --x"), "no-such-program-here was not found."
        )
        self.assertEqual(command_problem("/nonexistent/tool"), "/nonexistent/tool was not found.")

    def test_a_file_that_is_not_executable_is_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            plain = Path(folder) / "plain"
            plain.write_text("x")
            self.assertIn("was not found", command_problem(str(plain)))


class WindowTests(CommandBase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setApplicationName("AppGlider tests")
        cls.qt.setStyle("Fusion")

    def setUp(self) -> None:
        super().setUp()
        self.settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.settings.setValue("appearance/theme", "Light")
        check = patch("appglider.install.self_update.check", return_value=None)
        check.start()
        self.addCleanup(check.stop)
        self.window = LauncherWindow(self.settings, self.library)
        self.window.show()
        self.qt.processEvents()
        self.addCleanup(close_window, self.window)

    def wait_for_task(self) -> None:
        deadline = time.monotonic() + 10
        while self.window.task is not None and time.monotonic() < deadline:
            QTest.qWait(10)
            time.sleep(0.001)
        self.assertIsNone(self.window.task, "Background operation did not finish")

    def test_an_after_install_warning_reaches_the_footer_and_a_dialog(self) -> None:
        self.library.commands = InstallCommands(after=self.script("fail.sh", "exit 3"))
        data = appimage_bytes(b"1.0.0")
        release = release_for("1.0.0", data)
        with (
            patch("appglider.ui.window.latest_release", return_value=release),
            patch("appglider.github.http.request", return_value=io.BytesIO(data)),
            patch("appglider.ui.window.MessageBox.warning") as warning,
        ):
            self.window.install_app(APP.id)
            self.wait_for_task()
        self.assertTrue(self.window.footer.text().startswith(f"{APP.name} v1.0.0 is ready"))
        self.assertIn("The after-install command failed", self.window.footer.text())
        self.assertIn("exit code 3", warning.call_args.args[2])
        self.assertEqual(self.library.records()[APP.id].version, "1.0.0")

    def test_update_all_continues_after_a_before_failure_on_one_app(self) -> None:
        first, second = APPS[0], APPS[1]
        old, new = appimage_bytes(b"1.0.0"), appimage_bytes(b"1.1.0")
        for app in (first, second):
            name = f"{app.id}-1.0.0.AppImage"
            current = replace(
                release_for("1.0.0", old),
                name=name,
                url=f"https://github.com/{app.repo}/releases/download/v1.0.0/{name}",
            )
            with patch("appglider.github.http.request", return_value=io.BytesIO(old)):
                self.library.install(app, current, Event(), ignore_progress)
            name = f"{app.id}-1.1.0.AppImage"
            self.window.releases[app.id] = replace(
                release_for("1.1.0", new),
                name=name,
                url=f"https://github.com/{app.repo}/releases/download/v1.1.0/{name}",
            )
        self.window.scan_installed()
        self.library.commands = InstallCommands(
            before=self.script(
                "pick.sh", f'case "$APPGLIDER_APP_ID" in {first.id}) exit 7;; esac; exit 0'
            )
        )
        with (
            patch(
                "appglider.ui.window.latest_release",
                side_effect=lambda app, cancel, cache: self.window.releases[app.id],
            ),
            patch("appglider.github.http.request", side_effect=lambda *a, **k: io.BytesIO(new)),
            patch("appglider.ui.window.MessageBox.warning") as warning,
        ):
            self.window.update_all()
            self.wait_for_task()
        self.assertEqual(self.window.installed[second.id].version, "1.1.0")
        self.assertEqual(self.window.installed[first.id].version, "1.0.0")
        self.assertEqual(self.window.footer.text(), "Updated 1 app. 1 could not be updated.")
        self.assertIn("exit code 7", warning.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
