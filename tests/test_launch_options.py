"""Per-app launch options, menu entries with app metadata and the startup refresh."""

import io
import json
import shlex
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from catalog_fixture import stock
from test_library import APP, appimage_bytes, ignore_progress, release_for

from appglider.install.launch_options import (
    LaunchOptions,
    format_arguments,
    format_environment,
    load_launch_options,
    parse_arguments,
    parse_environment,
)
from appglider.install.library import Installed, Library

GROUPS = {
    "Desktop Entry": {
        "Categories": "Office;",
        "MimeType": "application/pdf;",
        "Exec": "app %F",
        "Actions": "new;",
    },
    "Desktop Action new": {"Name": "New", "Exec": "app --new"},
}
OLD_ENTRY = (
    "[Desktop Entry]\nType=Application\nName=Old\nExec=x %F\n"
    "Categories=Graphics;\nX-AppGlider=true\n"
)


class OptionParsingTests(unittest.TestCase):
    def test_arguments_split_like_a_shell(self) -> None:
        self.assertEqual(parse_arguments('--a "b c" d\\ e'), ("--a", "b c", "d e"))
        self.assertEqual(parse_arguments("   "), ())

    def test_unbalanced_quotes_and_control_characters_are_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "could not be read"):
            parse_arguments('--a "b')
        with self.assertRaisesRegex(ValueError, "plain text"):
            parse_arguments("a\x01b")
        with self.assertRaisesRegex(ValueError, "at most"):
            parse_arguments(" ".join(["x"] * 40))

    def test_environment_pairs_are_validated(self) -> None:
        self.assertEqual(
            parse_environment('A=1 B_2="two words" C= A=3'),
            (("A", "3"), ("B_2", "two words"), ("C", "")),
        )
        for bad in ("novalue", "1A=x", "A-B=x", "=x", "A B=x"):
            with self.assertRaisesRegex(ValueError, "not a valid variable"):
                parse_environment(bad)
        with self.assertRaisesRegex(ValueError, "could not be read"):
            parse_environment('A="x')
        with self.assertRaisesRegex(ValueError, "plain text"):
            parse_environment("A=x\x01y")

    def test_formatting_round_trips(self) -> None:
        options = LaunchOptions(("--a", "b c"), (("X", "1 2"), ("Y", "")))
        self.assertEqual(parse_arguments(format_arguments(options)), options.arguments)
        self.assertEqual(parse_environment(format_environment(options)), options.environment)


class LibraryCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = stock(
            Library(self.root / "data", self.root / "apps", desktop_dir=self.root / "desktop")
        )
        self.shortcut = self.library.desktop_dir / f"appglider-{APP.id}.desktop"
        self.path = self.library.data_dir / "launch-options.json"
        self.extract = patch(
            "appglider.install.shortcuts.extract_desktop_groups", return_value=None
        )
        self.extract.start()
        self.addCleanup(self.extract.stop)

    def install(self, version: str) -> Installed:
        data = appimage_bytes(version.encode())
        with patch("appglider.github.http.request", return_value=io.BytesIO(data)):
            return self.library.install(APP, release_for(version, data), Event(), ignore_progress)


class LaunchOptionsTests(LibraryCase):
    def test_options_persist_per_app_and_clear_cleanly(self) -> None:
        self.assertEqual(self.library.launch_options(APP.id), LaunchOptions())
        self.library.set_launch_options(APP.id, LaunchOptions((), ()))
        self.assertFalse(self.path.exists())
        options = LaunchOptions(("--a", "b c"), (("X", "1"),))
        self.library.set_launch_options(APP.id, options)
        self.assertEqual(
            json.loads(self.path.read_text()), {APP.id: {"args": ["--a", "b c"], "env": {"X": "1"}}}
        )
        self.assertEqual(load_launch_options(self.path), {APP.id: options})
        self.library.set_launch_options(APP.id, LaunchOptions())
        self.assertEqual(json.loads(self.path.read_text()), {})
        with self.assertRaisesRegex(ValueError, "not in the catalog"):
            self.library.set_launch_options("nothing", options)

    def test_a_corrupt_file_is_preserved_and_reported(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for text in ("{", '{"a": 1}', '{"a": {"env": {"1x": "y"}}}', '{"a": {"args": [1]}}'):
            self.path.write_text(text)
            with self.assertRaisesRegex(ValueError, "file was preserved"):
                self.library.set_launch_options(APP.id, LaunchOptions(("x",), ()))
            self.assertEqual(self.path.read_text(), text)

    def test_launch_adds_arguments_and_environment(self) -> None:
        installed = self.install("1.0.0")
        self.library.set_launch_options(
            APP.id, LaunchOptions(("--profile", "a b"), (("MY_VAR", "x y"),))
        )
        with (
            patch.dict("os.environ", {"MY_VAR": "old", "KEEP": "1"}),
            patch("appglider.install.library.subprocess.Popen") as spawn,
        ):
            spawn.return_value.poll.return_value = None
            self.library.launch(APP, installed)
        self.assertEqual(spawn.call_args.args[0], [str(installed.path), "--profile", "a b"])
        environment = spawn.call_args.kwargs["env"]
        self.assertEqual(environment["MY_VAR"], "x y")
        self.assertEqual(environment["KEEP"], "1")
        self.assertEqual(environment["APPIMAGELAUNCHER_DISABLE"], "1")
        self.assertNotIn("shell", spawn.call_args.kwargs)

    def test_launch_without_options_is_unchanged(self) -> None:
        installed = self.install("1.0.0")
        with patch("appglider.install.library.subprocess.Popen") as spawn:
            spawn.return_value.poll.return_value = None
            self.library.launch(APP, installed)
        self.assertEqual(spawn.call_args.args[0], [str(installed.path)])

    def test_saving_rewrites_an_existing_shortcut_and_never_creates_one(self) -> None:
        installed = self.install("1.0.0")
        options = LaunchOptions(("--x",), (("A", "b"),))
        self.library.set_launch_options(APP.id, options)
        line = next(
            line for line in self.shortcut.read_text().splitlines() if line.startswith("Exec=")
        )
        self.assertEqual(
            shlex.split(line[5:]),
            ["env", "APPIMAGELAUNCHER_DISABLE=1", "A=b", str(installed.path), "--x", "%F"],
        )
        self.shortcut.unlink()
        self.library.set_launch_options(APP.id, LaunchOptions(("--y",), ()))
        self.assertFalse(self.shortcut.exists())

    def test_saving_leaves_a_foreign_entry_alone_and_reports_nothing_lost(self) -> None:
        self.install("1.0.0")
        self.shortcut.write_text("[Desktop Entry]\nName=Mine\n")
        self.library.set_launch_options(APP.id, LaunchOptions(("--y",), ()))
        self.assertEqual(self.shortcut.read_text(), "[Desktop Entry]\nName=Mine\n")
        self.assertEqual(self.library.launch_options(APP.id).arguments, ("--y",))

    def test_removing_a_repository_forgets_its_options(self) -> None:
        self.library.set_launch_options(APP.id, LaunchOptions(("--x",), ()))
        self.library.remove_repository(APP.id)
        self.assertEqual(json.loads(self.path.read_text()), {})

    def test_the_desktop_database_refresh_can_be_left_to_the_caller(self) -> None:
        with patch.object(self.library, "update_desktop_database") as refresh:
            self.library.set_launch_options(APP.id, LaunchOptions(("--a",), ()), False)
            refresh.assert_not_called()
            self.library.set_launch_options(APP.id, LaunchOptions(("--b",), ()))
            refresh.assert_called_once()

    def test_options_are_not_part_of_a_catalog_export(self) -> None:
        before = self.library.export_entries()
        self.library.set_launch_options(
            APP.id, LaunchOptions(("--secret",), (("TOKEN", "hunter2"),))
        )
        self.assertEqual(self.library.export_entries(), before)
        self.assertNotIn("hunter2", repr(before))


class MenuMetadataTests(LibraryCase):
    def test_install_writes_the_apps_own_details(self) -> None:
        calls: list[Path] = []

        def read(source, app, environment):
            calls.append(source)
            with self.library._lock(timeout=0.2):  # the library lock is free while reading
                pass
            return GROUPS

        with patch("appglider.install.shortcuts.extract_desktop_groups", side_effect=read):
            installed = self.install("1.0.0")
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(calls[0], installed.path)  # read from the verified download
        text = self.shortcut.read_text()
        self.assertIn("Categories=Office;\n", text)
        self.assertIn("MimeType=application/pdf;\n", text)
        self.assertIn("Actions=new;\n", text)
        self.assertIn("X-AppGlider-Metadata=2\n", text)
        self.assertNotIn("Graphics", text)

    def test_without_details_the_entry_is_a_working_utility_entry(self) -> None:
        installed = self.install("1.0.0")
        text = self.shortcut.read_text()
        self.assertIn("Categories=Utility;\n", text)
        self.assertIn(f'Exec="env" "APPIMAGELAUNCHER_DISABLE=1" "{installed.path}" %F\n', text)

    def test_failed_activation_keeps_the_cache_of_the_active_version(self) -> None:
        installed = self.install("1.0.0")
        with patch("appglider.install.shortcuts.extract_desktop_groups", return_value=GROUPS):
            self.library.collect_desktop_metadata(APP, installed.path, installed.path)
        cache = self.library.data_dir / "desktop" / f"{APP.id}.json"
        before = cache.read_text()
        other = {"Desktop Entry": {"Categories": "Game;"}}
        data = appimage_bytes(b"1.1.0")
        with (
            patch("appglider.github.http.request", return_value=io.BytesIO(data)),
            patch("appglider.install.shortcuts.extract_desktop_groups", return_value=other),
            patch.object(self.library, "_save", side_effect=OSError("disk full")),
        ):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.library.install(APP, release_for("1.1.0", data), Event(), ignore_progress)
        self.assertEqual(cache.read_text(), before)
        self.library.create_shortcut(APP)
        self.assertIn("Categories=Office;", self.shortcut.read_text())

    def test_rollback_reads_the_previous_version(self) -> None:
        self.install("1.0.0")
        self.install("1.1.0")
        with patch(
            "appglider.install.shortcuts.extract_desktop_groups", return_value=GROUPS
        ) as read:
            self.library.rollback(APP)
        read.assert_called_once()
        self.assertIn("Categories=Office;", self.shortcut.read_text())

    def test_cached_details_do_not_apply_to_a_changed_file(self) -> None:
        installed = self.install("1.0.0")
        with patch("appglider.install.shortcuts.extract_desktop_groups", return_value=GROUPS):
            self.library.collect_desktop_metadata(APP, installed.path, installed.path)
        self.library.create_shortcut(APP)
        self.assertIn("Categories=Office;", self.shortcut.read_text())
        installed.path.write_bytes(appimage_bytes(b"changed in place"))
        self.library.create_shortcut(APP)
        self.assertIn("Categories=Utility;", self.shortcut.read_text())

    def test_repair_reads_the_details_when_none_are_saved(self) -> None:
        self.install("1.0.0")
        self.shortcut.unlink()
        with patch(
            "appglider.install.shortcuts.extract_desktop_groups", return_value=GROUPS
        ) as read:
            self.library.create_shortcut(APP)
            self.library.create_shortcut(APP)  # saved now: not read again
        read.assert_called_once()
        self.assertIn("Categories=Office;", self.shortcut.read_text())

    def test_tampered_cache_values_are_sanitised_again(self) -> None:
        installed = self.install("1.0.0")
        with patch("appglider.install.shortcuts.extract_desktop_groups", return_value=GROUPS):
            self.library.collect_desktop_metadata(APP, installed.path, installed.path)
        cache = self.library.data_dir / "desktop" / f"{APP.id}.json"
        data = json.loads(cache.read_text())
        data["groups"]["Desktop Entry"]["Categories"] = "Office;\nExec=evil"
        cache.write_text(json.dumps(data))
        self.library.create_shortcut(APP)
        text = self.shortcut.read_text()
        self.assertNotIn("evil", text)
        self.assertIn("Categories=Utility;", text)
        cache.write_text("not json")
        self.library.create_shortcut(APP)
        self.assertIn("Categories=Utility;", self.shortcut.read_text())

    def test_uninstall_removes_the_cache(self) -> None:
        installed = self.install("1.0.0")
        with patch("appglider.install.shortcuts.extract_desktop_groups", return_value=GROUPS):
            self.library.collect_desktop_metadata(APP, installed.path, installed.path)
        cache = self.library.data_dir / "desktop" / f"{APP.id}.json"
        self.assertTrue(cache.exists())
        self.library.remove(APP)
        self.assertFalse(cache.exists())

    def test_the_desktop_database_is_refreshed_after_changes_when_available(self) -> None:
        with (
            patch(
                "appglider.install.shortcuts.shutil.which",
                return_value="/usr/bin/update-desktop-database",
            ),
            patch("appglider.install.shortcuts.subprocess.run") as run,
        ):
            self.install("1.0.0")
        self.assertEqual(
            run.call_args.args[0],
            ["/usr/bin/update-desktop-database", str(self.library.desktop_dir)],
        )
        self.assertEqual(run.call_args.kwargs["timeout"], 10)
        with patch("appglider.install.shortcuts.shutil.which", return_value=None):
            self.library.update_desktop_database()  # nothing to run, nothing raised
        with (
            patch("appglider.install.shortcuts.shutil.which", return_value="/x"),
            patch("appglider.install.shortcuts.subprocess.run", side_effect=OSError("gone")),
        ):
            self.library.update_desktop_database()


class StartupRefreshTests(LibraryCase):
    def test_old_entries_are_upgraded_once_and_missing_ones_stay_missing(self) -> None:
        installed = self.install("1.0.0")
        self.shortcut.write_text(OLD_ENTRY)
        with (
            patch(
                "appglider.install.shortcuts.extract_desktop_groups", return_value=GROUPS
            ) as read,
            patch("appglider.install.shortcuts.shutil.which", return_value="/bin/udd"),
            patch("appglider.install.shortcuts.subprocess.run") as run,
        ):
            self.assertEqual(self.library.refresh_shortcuts(), 1)
            self.assertEqual(self.library.refresh_shortcuts(), 0)
        read.assert_called_once()
        run.assert_called_once()
        text = self.shortcut.read_text()
        self.assertIn("Categories=Office;", text)
        self.assertIn(str(installed.path), text)
        self.assertEqual(list(self.library.desktop_dir.glob("*.desktop")), [self.shortcut])

        self.shortcut.unlink()
        with patch(
            "appglider.install.shortcuts.extract_desktop_groups", return_value=GROUPS
        ) as read:
            self.assertEqual(self.library.refresh_shortcuts(), 0)
        read.assert_not_called()
        self.assertFalse(self.shortcut.exists())

    def test_entries_from_the_previous_marker_gain_the_launcher_bypass(self) -> None:
        installed = self.install("1.0.0")
        self.shortcut.write_text(
            OLD_ENTRY.replace("Exec=x %F", f'Exec="{installed.path}" %F')
            + "X-AppGlider-Metadata=1\n"
        )
        with patch("appglider.install.shortcuts.extract_desktop_groups", return_value=GROUPS):
            self.assertEqual(self.library.refresh_shortcuts(), 1)
        self.assertIn(
            f'Exec="env" "APPIMAGELAUNCHER_DISABLE=1" "{installed.path}" %F\n',
            self.shortcut.read_text(),
        )

    def test_foreign_and_symlinked_entries_are_not_touched(self) -> None:
        self.install("1.0.0")
        for write in ("foreign", "link"):
            self.shortcut.unlink(missing_ok=True)
            if write == "foreign":
                self.shortcut.write_text("[Desktop Entry]\nName=Mine\n")
            else:
                target = self.root / "elsewhere.desktop"
                target.write_text(OLD_ENTRY)
                self.shortcut.symlink_to(target)
            before = self.shortcut.read_text()
            with patch("appglider.install.shortcuts.extract_desktop_groups", return_value=GROUPS):
                self.assertEqual(self.library.refresh_shortcuts(), 0)
            self.assertEqual(self.shortcut.read_text(), before)

    def test_unmanaged_and_uninstalled_apps_are_skipped(self) -> None:
        external = self.root / "photocraft.AppImage"
        external.write_bytes(appimage_bytes())
        self.library._save({APP.id: Installed(external, None, False)})
        self.shortcut.parent.mkdir(parents=True)
        self.shortcut.write_text(OLD_ENTRY)
        with patch(
            "appglider.install.shortcuts.extract_desktop_groups", return_value=GROUPS
        ) as read:
            self.assertEqual(self.library.refresh_shortcuts(), 0)
        read.assert_not_called()
        self.assertEqual(self.shortcut.read_text(), OLD_ENTRY)

    def test_a_cancelled_refresh_stops_before_the_next_app(self) -> None:
        self.install("1.0.0")
        self.shortcut.write_text(OLD_ENTRY)
        cancel = Event()
        cancel.set()
        self.assertEqual(self.library.refresh_shortcuts(cancel), 0)
        self.assertEqual(self.shortcut.read_text(), OLD_ENTRY)


if __name__ == "__main__":
    unittest.main()
