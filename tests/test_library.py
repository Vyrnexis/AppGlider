"""Install lifecycle and launch proof using temporary files and controlled downloads."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event, Thread
from unittest.mock import MagicMock, patch

from catalog_fixture import APPS, stock

from appglider import APP_TASKBAR_ICON
from appglider.library import (
    Installed,
    Library,
    Orphan,
    check_appimage,
    desktop_exec,
    previous_copy,
)
from appglider.releases import (
    Cancelled,
    Release,
    architecture,
    download,
    latest_release,
    select_release,
    version_key,
)
from appglider.storage import atomic_json

APP = APPS[0]


def appimage_bytes(extra: bytes = b"test payload") -> bytes:
    header = bytearray(20)
    header[:4] = b"\x7fELF"
    header[4:6] = b"\x02\x01"
    header[8:11] = b"AI\x02"
    header[18:20] = (62 if architecture() == "x86_64" else 183).to_bytes(2, "little")
    return bytes(header) + extra


def release_for(version: str, data: bytes) -> Release:
    name = f"{APP.id}-{version}-linux-{architecture()}.AppImage"
    return Release(
        version,
        name,
        f"https://github.com/{APP.repo}/releases/download/v{version}/{name}",
        len(data),
        hashlib.sha256(data).hexdigest(),
    )


def ignore_progress(message: str, received: int, total: int) -> None:
    pass


class LibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.external = self.root / "external"
        self.external.mkdir()
        self.library = stock(
            Library(
                self.root / "data",
                self.root / "apps",
                desktop_dir=self.root / "desktop",
                autostart_dir=self.root / "autostart",
            )
        )

    def install(self, version: str):
        data = appimage_bytes(version.encode())
        release = release_for(version, data)
        with patch("appglider.releases.request", return_value=io.BytesIO(data)):
            return self.library.install(APP, release, Event(), ignore_progress)

    def test_update_hold_round_trip_is_lazy_and_cleared_on_uninstall(self) -> None:
        path = self.library.data_dir / "update-holds.json"
        self.library.set_update_hold(APP.id, False)
        self.assertFalse(path.exists())
        with self.assertRaises(ValueError):
            self.library.set_update_hold(APP.id, True)  # not installed
        self.install("1.0.0")
        self.library.set_update_hold(APP.id, True)
        self.assertEqual(json.loads(path.read_text()), {APP.id: True})
        self.assertTrue(self.library.apps[0].hold_updates)
        self.library.set_update_hold(APP.id, False)
        self.assertFalse(self.library.apps[0].hold_updates)
        self.library.set_update_hold(APP.id, True)
        self.library.remove(APP)
        self.assertFalse(self.library.apps[0].hold_updates)
        self.assertEqual(json.loads(path.read_text()), {})

    def test_unmanaged_installs_cannot_be_held(self) -> None:
        location = self.external / "photocraft.AppImage"
        location.write_bytes(appimage_bytes())
        self.library._save({APP.id: Installed(location, None, False)})
        with self.assertRaisesRegex(ValueError, "installed by AppGlider"):
            self.library.set_update_hold(APP.id, True)
        self.library.set_update_hold(APP.id, False)

    def test_corrupt_update_holds_are_preserved_and_block_changes(self) -> None:
        self.install("1.0.0")
        path = self.library.data_dir / "update-holds.json"
        for text in ("{broken", '{"x": 1}'):
            path.write_text(text)
            with self.assertRaisesRegex(ValueError, "preserved"):
                self.library.set_update_hold(APP.id, True)
            with self.assertRaisesRegex(ValueError, "preserved"):
                self.library.set_release_preference(APP.id, True)
            self.assertEqual(path.read_text(), text)

    def test_unregistered_appimages_are_not_part_of_the_library(self) -> None:
        external = self.external / f"photocraft-1.2.0-linux-{architecture()}.AppImage"
        external.write_bytes(appimage_bytes())
        self.library.apps_dir.mkdir()
        loose = self.library.apps_dir / external.name
        loose.write_bytes(appimage_bytes())
        self.assertEqual(self.library.scan(), {})
        self.assertEqual(external.read_bytes(), appimage_bytes())
        self.assertEqual(loose.read_bytes(), appimage_bytes())
        self.assertFalse((self.library.data_dir / "library.json").exists())

    def test_legacy_external_link_is_ignored_and_does_not_block_own_install(self) -> None:
        location = self.external / "photocraft.AppImage"
        location.write_bytes(appimage_bytes(b"original external copy"))
        self.library._save({APP.id: Installed(location, None, False)})
        self.assertEqual(self.library.scan(), {})
        installed = self.install("1.0.0")
        reopened = Library(self.root / "data", self.root / "apps")
        self.assertEqual(reopened.scan()[APP.id].path, installed.path)
        self.assertTrue(reopened.scan()[APP.id].managed)
        self.assertEqual(location.read_bytes(), appimage_bytes(b"original external copy"))
        self.library.remove(APP)
        self.assertTrue(location.is_file())
        self.assertEqual(self.library.scan(), {})

    def test_rejects_invalid_or_wrong_architecture_appimages(self) -> None:
        location = self.root / "bad.AppImage"
        for content in (
            b"not an appimage",
            appimage_bytes()[:18] + b"\xff\xff",
            appimage_bytes()[:4] + b"\x01\x01" + appimage_bytes()[6:],
            appimage_bytes()[:5] + b"\x00" + appimage_bytes()[6:],
        ):
            location.write_bytes(content)
            with self.assertRaises(ValueError):
                check_appimage(location)
        self.assertFalse((self.root / "data" / "library.json").exists())

    def test_unlabelled_appimage_with_wrong_cpu_cannot_replace_installed_version(self) -> None:
        current = self.install("1.0.0")
        records = (self.library.data_dir / "library.json").read_bytes()
        payload = bytearray(appimage_bytes())
        payload[18:20] = (183 if architecture() == "x86_64" else 62).to_bytes(2, "little")
        content = bytes(payload)
        name = "Desktop.AppImage"
        release = select_release(
            APP,
            {
                "tag_name": "v1.1.0",
                "assets": [
                    {
                        "name": name,
                        "browser_download_url": f"{APP.source_url}/releases/download/v1.1.0/{name}",
                        "size": len(content),
                        "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
                    }
                ],
            },
            architecture(),
        )
        with (
            patch("appglider.releases.request", return_value=io.BytesIO(content)),
            self.assertRaisesRegex(ValueError, "different CPU architecture"),
        ):
            self.library.install(APP, release, Event(), ignore_progress)
        self.assertEqual((self.library.data_dir / "library.json").read_bytes(), records)
        self.assertEqual(self.library.scan()[APP.id], current)
        self.assertFalse((current.path.parent.parent / "1.1.0" / name).exists())

    def test_install_update_rollback_forward_and_remove_keep_user_files(self) -> None:
        first = self.install("1.0.0")
        second = self.install("1.1.0")
        self.assertEqual(second.previous, first.path)
        self.assertTrue(first.path.is_file())
        self.assertTrue(os.access(second.path, os.X_OK))
        self.library.rollback(APP)
        self.assertEqual(self.library.scan()[APP.id].path, first.path)
        # Updating after a rollback should reuse the already verified version, not overwrite it.
        restored = self.install("1.1.0")
        self.assertEqual(restored.path, second.path)
        note = second.path.parent / "user-note.txt"
        note.write_text("keep me")
        document = self.root / "drawing.psd"
        document.write_text("my document")
        self.library.remove(APP)
        self.assertNotIn(APP.id, self.library.scan())
        self.assertFalse(first.path.exists())
        self.assertFalse(second.path.exists())
        self.assertEqual(note.read_text(), "keep me")
        self.assertEqual(document.read_text(), "my document")

    def test_bad_checksum_and_cancellation_keep_current_installation(self) -> None:
        first = self.install("1.0.0")
        data = appimage_bytes(b"new release")
        release = release_for("1.1.0", data)
        for cancelled in (False, True):
            event = Event()
            if cancelled:
                event.set()
            with (
                self.subTest(cancelled=cancelled),
                patch(
                    "appglider.releases.request",
                    return_value=io.BytesIO(b"x" * len(data)),
                ),
            ):
                with self.assertRaises((ValueError, Cancelled)):
                    self.library.install(APP, release, event, ignore_progress)
                self.assertEqual(self.library.scan()[APP.id].path, first.path)
                self.assertTrue(first.path.is_file())
                self.assertFalse(list(first.path.parent.parent.glob(".download-*")))

    def test_state_write_failure_keeps_old_active_version(self) -> None:
        first = self.install("1.0.0")
        data = appimage_bytes(b"new release")
        with (
            patch("appglider.releases.request", return_value=io.BytesIO(data)),
            patch.object(self.library, "_save", side_effect=OSError("disk full")),
        ):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.library.install(APP, release_for("1.1.0", data), Event(), ignore_progress)
        self.assertEqual(self.library.scan()[APP.id].path, first.path)
        self.assertTrue(first.path.is_file())

    def test_corrupt_state_is_preserved_and_blocks_mutation(self) -> None:
        self.library.data_dir.mkdir(exist_ok=True)
        state = self.library.data_dir / "library.json"
        state.write_text("broken JSON")
        with self.assertRaisesRegex(ValueError, "preserved"):
            self.install("1.0.0")
        self.assertEqual(state.read_text(), "broken JSON")

    def test_failed_uninstall_restores_both_versions_and_shortcut(self) -> None:
        first = self.install("1.0.0")
        second = self.install("1.1.0")
        state = self.library.data_dir / "library.json"
        shortcut = self.library.desktop_dir / f"appglider-{APP.id}.desktop"
        originals = {path: path.read_bytes() for path in (first.path, second.path, state, shortcut)}
        rename = Path.rename

        def fail_second_stage(path, target):
            if path == first.path:
                raise OSError("cannot stage previous version")
            return rename(path, target)

        for failure in (
            patch.object(self.library, "_save", side_effect=OSError("disk full")),
            patch.object(Path, "rename", autospec=True, side_effect=fail_second_stage),
        ):
            with failure, self.assertRaises(OSError):
                self.library.remove(APP)
            for path, contents in originals.items():
                self.assertEqual(path.read_bytes(), contents)
            self.assertEqual(self.library.scan()[APP.id], second)
            self.assertFalse(list(second.path.parent.parent.glob("*/.uninstall-*")))

    def test_uninstall_preserves_recovery_files_if_restoration_or_cleanup_fails(self) -> None:
        installed = self.install("1.0.0")
        contents = installed.path.read_bytes()
        state = self.library.data_dir / "library.json"
        original_state = state.read_bytes()
        rename = Path.rename

        def fail_restore(path, target):
            if path.name.startswith(".uninstall-"):
                raise OSError("cannot restore")
            return rename(path, target)

        with (
            patch.object(self.library, "_save", side_effect=OSError("disk full")),
            patch.object(Path, "rename", autospec=True, side_effect=fail_restore),
            self.assertRaisesRegex(
                OSError, "Restore these preserved AppImages manually"
            ) as failure,
        ):
            self.library.remove(APP)
        (recovery,) = installed.path.parent.glob(".uninstall-*")
        self.assertIn(str(recovery), str(failure.exception))
        self.assertEqual(recovery.read_bytes(), contents)
        self.assertEqual(state.read_bytes(), original_state)
        recovery.rename(installed.path)
        unlink = Path.unlink

        def fail_cleanup(path, *args, **kwargs):
            if path.name.startswith(".uninstall-"):
                raise OSError("cannot delete")
            return unlink(path, *args, **kwargs)

        with (
            patch.object(Path, "unlink", autospec=True, side_effect=fail_cleanup),
            self.assertRaisesRegex(OSError, "was uninstalled, but cleanup failed") as failure,
        ):
            self.library.remove(APP)
        (recovery,) = installed.path.parent.glob(".uninstall-*")
        self.assertIn(str(recovery), str(failure.exception))
        self.assertEqual(recovery.read_bytes(), contents)
        self.assertNotIn(APP.id, self.library.records())
        self.assertFalse((self.library.desktop_dir / f"appglider-{APP.id}.desktop").exists())

    def test_ownership_and_running_checks_protect_installed_files(self) -> None:
        installed = self.install("1.0.0")
        with patch("appglider.library.running", return_value=True):
            with self.assertRaisesRegex(ValueError, "Close"):
                self.library.remove(APP)
        self.assertTrue(installed.path.exists())
        marker = installed.path.parent.parent / ".appglider"
        marker.write_text("not ours")
        with self.assertRaisesRegex(ValueError, "not owned"):
            self.library.remove(APP)
        self.assertTrue(installed.path.exists())

    def test_install_refuses_unowned_folder_and_directory_symlink(self) -> None:
        app_dir = self.library.apps_dir / APP.id
        app_dir.mkdir(parents=True)
        sentinel = app_dir / "user-file"
        sentinel.write_text("keep")
        with self.assertRaises((ValueError, OSError)):
            self.install("1.0.0")
        self.assertEqual(sentinel.read_text(), "keep")
        sentinel.unlink()
        app_dir.rmdir()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / ".appglider").write_text(self.library._marker(APP))
        app_dir.symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic"):
            self.install("1.0.0")
        self.assertFalse((elsewhere / "1.0.0").exists())

    def test_failed_download_does_not_trust_an_incomplete_file(self) -> None:
        data = appimage_bytes()
        release = release_for("1.0.0", data)
        with patch("appglider.releases.request", return_value=io.BytesIO(data[:20])):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                download(release, self.root / "download", Event(), ignore_progress)

    def test_launch_uses_exact_argv_and_clears_bundled_environment(self) -> None:
        installed = self.install("1.0.0")
        with (
            patch.dict(
                os.environ,
                {
                    "APPIMAGE": "/launcher.AppImage",
                    "QT_PLUGIN_PATH": "/launcher/qt",
                    "APPDIR": "/launcher",
                    "SSL_CERT_FILE": "/launcher/certifi/cacert.pem",
                    "SSL_CERT_DIR": "/etc/ssl/certs",
                },
            ),
            patch("appglider.library.subprocess.Popen") as spawn,
        ):
            spawn.return_value.poll.return_value = None
            self.library.launch(APP, installed)
            self.assertEqual(spawn.call_args.args[0], [str(installed.path)])
            self.assertNotIn("APPIMAGE", spawn.call_args.kwargs["env"])
            self.assertNotIn("QT_PLUGIN_PATH", spawn.call_args.kwargs["env"])
            self.assertNotIn("SSL_CERT_FILE", spawn.call_args.kwargs["env"])
            self.assertEqual(spawn.call_args.kwargs["env"]["SSL_CERT_DIR"], "/etc/ssl/certs")
            self.assertTrue(spawn.call_args.kwargs["start_new_session"])
            self.assertNotIn("shell", spawn.call_args.kwargs)

    def test_missing_ownership_marker_gives_a_clear_error_and_removes_nothing(self) -> None:
        self.install("1.0.0")
        installed = self.install("1.1.0")
        (installed.path.parent.parent / ".appglider").unlink()
        data = appimage_bytes(b"new release")
        for action in (
            lambda: self.library.launch(APP, installed),
            lambda: self.library.rollback(APP),
            lambda: self.library.install(APP, release_for("1.1.0", data), Event(), ignore_progress),
        ):
            with self.assertRaisesRegex(ValueError, "not owned|another application"):
                action()
        self.assertTrue(installed.path.is_file())

    def test_cancelled_install_leaves_no_empty_version_folder(self) -> None:
        first = self.install("1.0.0")
        data = appimage_bytes(b"new release")
        cancel = Event()
        cancel.set()
        with (
            patch("appglider.releases.request", return_value=io.BytesIO(data)),
            self.assertRaises(Cancelled),
        ):
            self.library.install(APP, release_for("1.1.0", data), cancel, ignore_progress)
        self.assertFalse((first.path.parent.parent / "1.1.0").exists())
        self.assertTrue(first.path.is_file())

    def test_stale_download_files_are_cleaned_but_recent_ones_and_others_stay(self) -> None:
        first = self.install("1.0.0")
        root = first.path.parent.parent
        stale, recent, other = root / ".download-old", root / ".download-new", root / "notes.txt"
        for path in (stale, recent, other):
            path.write_text("x")
        old = time.time() - 48 * 60 * 60
        os.utime(stale, (old, old))
        self.install("1.1.0")
        self.assertFalse(stale.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(other.exists())

    def test_uninstall_removes_empty_app_folder_but_keeps_unexpected_files(self) -> None:
        installed = self.install("1.0.0")
        root = installed.path.parent.parent
        self.library.remove(APP)
        self.assertFalse(root.exists())
        installed = self.install("1.0.0")
        (root / "user-data.txt").write_text("keep me")
        self.library.remove(APP)
        self.assertEqual((root / "user-data.txt").read_text(), "keep me")
        self.assertTrue((root / ".appglider").is_file())

    def test_nixos_without_appimage_support_explains_the_setting_instead_of_failing(
        self,
    ) -> None:
        installed = self.install("1.0.0")
        root = self.root / "nixos"
        marker, binfmt = root / "NIXOS", root / "binfmt_misc"
        binfmt.mkdir(parents=True)
        marker.write_text("")
        (binfmt / "status").write_text("enabled\n")
        (binfmt / "register").write_text("")
        (binfmt / "DOSWin").write_text("enabled\ninterpreter /usr/bin/wine\nmagic 4d5a\n")
        environment = {key: value for key, value in os.environ.items() if key != "NIX_LD"}

        def launch() -> MagicMock:
            with (
                patch("appglider.library.NIXOS_MARKER", marker),
                patch("appglider.library.BINFMT_DIR", binfmt),
                patch.dict(os.environ, environment, clear=True),
                patch("appglider.library.subprocess.Popen") as spawn,
            ):
                spawn.return_value.poll.return_value = None
                self.library.processes.clear()
                self.library.launch(APP, installed)
                return spawn

        with self.assertRaisesRegex(ValueError, r"programs\.appimage\.binfmt = true") as raised:
            launch()
        self.assertIn("nixos-rebuild switch", str(raised.exception))

        environment["NIX_LD"] = "/run/current-system/sw/share/nix-ld/lib/ld.so"
        launch().assert_called_once()
        del environment["NIX_LD"]

        (binfmt / "appimage-type2").write_text(
            "enabled\ninterpreter /nix/store/x-appimage-run\nflags: \noffset 0\n"
            "magic 7f454c46000000000000414902\n"
        )
        launch().assert_called_once()

        (binfmt / "appimage-type2").write_text("disabled\nmagic 7f454c46000000000000414902\n")
        with self.assertRaises(ValueError):
            launch()

        (binfmt / "status").unlink()  # binfmt_misc not mounted: unknown, so do not block
        launch().assert_called_once()

        marker.unlink()
        (binfmt / "status").write_text("enabled\n")
        launch().assert_called_once()

    def test_oversized_launch_log_is_rotated(self) -> None:
        installed = self.install("1.0.0")
        log = self.library.data_dir / "logs" / f"{APP.id}.log"
        log.parent.mkdir(parents=True)
        log.write_bytes(b"x" * (6 * 1024 * 1024))
        with patch("appglider.library.subprocess.Popen") as spawn:
            spawn.return_value.poll.return_value = None
            self.library.launch(APP, installed)
        self.assertEqual(log.stat().st_size, 0)
        self.assertEqual(log.with_name(log.name + ".1").stat().st_size, 6 * 1024 * 1024)

    def test_other_changes_succeed_while_a_download_is_in_progress(self) -> None:
        data = appimage_bytes(b"slow")
        release = release_for("1.0.0", data)
        started, finish = Event(), Event()

        class Slow(io.BytesIO):
            def read(self, size=-1):
                started.set()
                finish.wait(5)
                return super().read(size)

        outcome = []

        def run() -> None:
            try:
                outcome.append(self.library.install(APP, release, Event(), ignore_progress))
            except Exception as error:  # pragma: no cover - reported by the assertion below
                outcome.append(error)

        with patch("appglider.releases.request", return_value=Slow(data)):
            worker = Thread(target=run)
            worker.start()
            try:
                self.assertTrue(started.wait(5))
                self.library.set_release_preference(APP.id, True)
                self.library.set_build_variant(APP.id, "")
            finally:
                finish.set()
                worker.join(10)
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], Installed)
        self.assertEqual(self.library.scan()[APP.id].version, "1.0.0")

    def test_install_aborts_if_the_library_changed_during_the_download(self) -> None:
        first = self.install("1.0.0")
        data = appimage_bytes(b"new release")
        release = release_for("1.1.0", data)
        other = release_for("1.0.5", appimage_bytes(b"other"))

        class Interfering(io.BytesIO):
            def read(inner, size=-1):  # noqa: N805
                if not inner.tell():
                    # Another launcher activates a different version mid-download.
                    with patch(
                        "appglider.releases.request",
                        return_value=io.BytesIO(appimage_bytes(b"other")),
                    ):
                        self.library.install(APP, other, Event(), ignore_progress)
                return super().read(size)

        with (
            patch("appglider.releases.request", return_value=Interfering(data)),
            self.assertRaisesRegex(ValueError, "library changed"),
        ):
            self.library.install(APP, release, Event(), ignore_progress)
        record = self.library.scan()[APP.id]
        self.assertEqual(record.version, "1.0.5")
        self.assertTrue(first.path.parent.exists())
        self.assertFalse((first.path.parent.parent / "1.1.0").exists())
        self.assertFalse(list(first.path.parent.parent.glob(".download-*")))

    def test_catalog_is_cached_but_picks_up_edits_and_keeps_reporting_corruption(self) -> None:
        self.assertEqual(self.library.apps, self.library.apps)
        repositories = self.library.data_dir / "repositories.json"
        atomic_json(repositories, [{"repo": "owner/example-app", "name": "Example"}])
        self.assertEqual(self.library.apps[-1].repo, "owner/example-app")
        repositories.write_text("{broken")
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, "Cannot read"):
                self.library.apps
        atomic_json(repositories, [])
        self.assertEqual(self.library.apps, ())
        atomic_json(repositories, [{"repo": APP.repo, "name": APP.name}])
        self.assertEqual(self.library.apps[0].repo, APP.repo)
        self.library.set_release_preference(APP.id, True)
        self.assertTrue(self.library.apps[0].include_prereleases)

    def test_install_needs_free_space_for_the_download(self) -> None:
        data = appimage_bytes()
        release = release_for("1.0.0", data)
        usage = shutil._ntuple_diskusage(10**12, 10**12 - 1024, 1024)
        with (
            patch("appglider.library.shutil.disk_usage", return_value=usage),
            patch("appglider.library.download") as fetch,
        ):
            with self.assertRaisesRegex(ValueError, "not enough free disk space"):
                self.library.install(APP, release, Event(), ignore_progress)
            fetch.assert_not_called()
        self.assertNotIn(APP.id, self.library.records())

    def test_lock_waits_briefly_then_reports_another_launcher(self) -> None:
        with self.library._lock():
            with self.assertRaisesRegex(ValueError, "Another launcher"):
                with self.library._lock(timeout=0.1):
                    pass
        holder = Event()

        def hold() -> None:
            with self.library._lock():
                holder.set()
                time.sleep(0.2)

        worker = Thread(target=hold)
        worker.start()
        self.assertTrue(holder.wait(5))
        with self.library._lock(timeout=3):
            pass
        worker.join(5)

    def test_launch_smoke_with_harmless_native_executable(self) -> None:
        data = bytearray(Path(shutil.which("true")).read_bytes())
        data[8:11] = b"AI\x02"
        payload = bytes(data)
        with patch("appglider.releases.request", return_value=io.BytesIO(payload)):
            installed = self.library.install(
                APP, release_for("1.0.0", payload), Event(), ignore_progress
            )
        self.library.launch(APP, installed)
        self.assertEqual(self.library.processes[APP.id].wait(timeout=5), 0)

    def test_launch_refuses_legacy_external_link(self) -> None:
        path = self.external / "photocraft.AppImage"
        path.write_bytes(appimage_bytes())
        with self.assertRaisesRegex(ValueError, "Only apps installed"):
            self.library.launch(APP, Installed(path, None, False))

    def test_menu_shortcut_tracks_active_version_without_duplicates(self) -> None:
        first = self.install("1.0.0")
        shortcut = self.library.desktop_dir / f"appglider-{APP.id}.desktop"
        self.assertIn("Name=PhotoCraft (AppGlider)", shortcut.read_text())
        self.assertIn(str(first.path), shortcut.read_text())
        second = self.install("1.1.0")
        self.assertEqual(list(self.library.desktop_dir.glob("*.desktop")), [shortcut])
        self.assertIn(str(second.path), shortcut.read_text())
        self.assertNotIn(str(first.path), shortcut.read_text())
        self.library.rollback(APP)
        self.assertIn(str(first.path), shortcut.read_text())
        shortcut.unlink()
        self.library.create_shortcut(APP)
        self.assertIn(str(first.path), shortcut.read_text())
        self.assertEqual(list(self.library.desktop_dir.glob("*.desktop")), [shortcut])

    def test_launcher_shortcut_is_stable_and_preserves_foreign_entries(
        self,
    ) -> None:
        shortcut = self.library.create_launcher_shortcut([sys.executable, "-O", "-m", "appglider"])
        self.assertIn("Name=AppGlider\n", shortcut.read_text())
        self.assertIn(desktop_exec([sys.executable, "-O", "-m", "appglider"]), shortcut.read_text())
        self.assertTrue((self.library.data_dir / "icons" / "appglider.png").is_file())
        self.assertEqual(
            self.library.create_launcher_shortcut([sys.executable, "-O", "-m", "appglider"]),
            shortcut,
        )
        self.assertEqual(list(self.library.desktop_dir.glob("*.desktop")), [shortcut])
        shortcut.write_text("[Desktop Entry]\nName=Someone else's shortcut\n")
        with self.assertRaisesRegex(OSError, "another application"):
            self.library.create_launcher_shortcut([sys.executable, "-O", "-m", "appglider"])
        self.assertIn("Someone else's shortcut", shortcut.read_text())

    def test_unrecognised_entries_are_preserved_until_explicitly_discarded(self) -> None:
        self.install("1.0.0")
        state = self.library.data_dir / "library.json"
        entries = json.loads(state.read_text())
        orphan = {"path": "/gone/Old.AppImage", "version": "2.0", "managed": True}
        entries["old-app"] = orphan
        atomic_json(state, entries)
        self.assertEqual(set(self.library.records()), {APP.id})
        self.assertEqual(self.library.orphans(), [Orphan("old-app", "/gone/Old.AppImage", "2.0")])
        self.install("1.1.0")
        self.assertEqual(json.loads(state.read_text())["old-app"], orphan)
        self.library.discard_orphans({"unrelated"})
        self.assertEqual(len(self.library.orphans()), 1)
        self.library.discard_orphans({"old-app"})
        self.assertEqual(self.library.orphans(), [])
        self.assertEqual(set(json.loads(state.read_text())), {APP.id})
        self.assertEqual(self.library.records()[APP.id].version, "1.1.0")

    def test_malformed_entries_still_fail_without_rewriting_the_file(self) -> None:
        state = self.library.data_dir / "library.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text('{"old-app": "not an object"}')
        with self.assertRaisesRegex(ValueError, "preserved"):
            self.library.records()
        self.assertEqual(state.read_text(), '{"old-app": "not an object"}')

    def test_finished_processes_are_reaped(self) -> None:
        done = subprocess.Popen([sys.executable, "-c", "pass"])
        done.wait()
        running = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(lambda: (running.kill(), running.wait()))
        self.library.processes = {"done": done, "running": running}
        self.library.reap_processes()
        self.assertEqual(list(self.library.processes), ["running"])

    def test_refresh_keeps_a_working_shortcut_unless_a_newer_image_takes_over(self) -> None:
        old, new = Path(self.temp.name) / "old.AppImage", Path(self.temp.name) / "new.AppImage"
        old.write_bytes(b"old")
        new.write_bytes(b"new")
        os.utime(old, (1_000, 1_000))
        os.utime(new, (2_000, 2_000))
        shortcut = self.library.refresh_launcher_shortcut([str(old)], packaged=True)
        self.assertIn(desktop_exec([str(old)]), shortcut.read_text())
        development = ["/project/.venv/bin/python", "-O", "-m", "appglider"]
        self.library.refresh_launcher_shortcut(development, packaged=False)
        self.assertIn(desktop_exec([str(old)]), shortcut.read_text())
        self.library.refresh_launcher_shortcut([str(new)], packaged=True)
        self.assertIn(desktop_exec([str(new)]), shortcut.read_text())
        self.library.refresh_launcher_shortcut([str(old)], packaged=True)
        self.assertIn(desktop_exec([str(new)]), shortcut.read_text())
        new.unlink()
        self.library.refresh_launcher_shortcut(development, packaged=False)
        self.assertIn(desktop_exec(development), shortcut.read_text())

    def test_refresh_replaces_a_stale_icon_copy_without_rewriting_the_shortcut(self) -> None:
        image = Path(self.temp.name) / "glider.AppImage"
        image.write_bytes(b"image")
        shortcut = self.library.refresh_launcher_shortcut([str(image)], packaged=True)
        icon = self.library.data_dir / "icons" / "appglider.png"
        icon.write_bytes(b"icon from an older release")
        before = shortcut.read_text()
        self.library.refresh_launcher_shortcut([str(image)], packaged=True)
        self.assertEqual(icon.read_bytes(), APP_TASKBAR_ICON.read_bytes())
        self.assertEqual(shortcut.read_text(), before)

    def test_autostart_enable_writes_a_marked_tray_entry_and_disable_removes_it(self) -> None:
        self.assertFalse(self.library.autostart_enabled())
        path = self.library.enable_autostart(["/home/a b/AppGlider.AppImage"])
        self.assertEqual(path, self.root / "autostart" / "appglider.desktop")
        lines = path.read_text().splitlines()
        self.assertIn('Exec="/home/a b/AppGlider.AppImage" "--tray"', lines)
        for line in ("Type=Application", "Terminal=false", "X-AppGlider=true"):
            self.assertIn(line, lines)
        self.assertIn("X-GNOME-Autostart-enabled=true", lines)
        self.assertTrue((self.root / "data" / "icons" / "appglider.png").is_file())
        self.assertTrue(self.library.autostart_enabled())
        self.library.disable_autostart()
        self.assertFalse(path.exists())
        self.library.disable_autostart()  # already off

    def test_autostart_leaves_foreign_files_and_symlinks_alone(self) -> None:
        directory = self.root / "autostart"
        directory.mkdir()
        path = directory / "appglider.desktop"
        path.write_text("[Desktop Entry]\nExec=/bin/true\n")
        self.assertFalse(self.library.autostart_enabled())
        with self.assertRaisesRegex(OSError, "another application"):
            self.library.enable_autostart(["/x"])
        with self.assertRaisesRegex(OSError, "another application"):
            self.library.disable_autostart()
        self.library.refresh_autostart(["/x"], packaged=True)
        self.assertEqual(path.read_text(), "[Desktop Entry]\nExec=/bin/true\n")
        path.unlink()
        target = self.root / "target.desktop"
        target.write_text("[Desktop Entry]\nX-AppGlider=true\n")
        path.symlink_to(target)
        self.assertFalse(self.library.autostart_enabled())
        with self.assertRaises(OSError):
            self.library.enable_autostart(["/x"])
        with self.assertRaises(OSError):
            self.library.disable_autostart()
        self.assertTrue(path.is_symlink())
        self.assertEqual(target.read_text(), "[Desktop Entry]\nX-AppGlider=true\n")

    def test_autostart_refresh_repairs_a_moved_command_but_never_creates_the_entry(self) -> None:
        old, new = self.root / "old.AppImage", self.root / "new.AppImage"
        self.library.refresh_autostart([str(old)], packaged=True)
        self.assertFalse(self.library.autostart_file.exists())
        self.library.enable_autostart([str(old)])
        # The old image is gone, so any start repairs the entry.
        self.library.refresh_autostart([str(new)], packaged=False)
        self.assertIn(f'Exec="{new}" "--tray"', self.library.autostart_file.read_text())
        before = self.library.autostart_file.stat().st_mtime_ns
        self.library.refresh_autostart([str(new)], packaged=True)
        self.assertEqual(self.library.autostart_file.stat().st_mtime_ns, before)

    def test_autostart_refresh_keeps_a_working_entry_for_dev_runs_and_older_images(self) -> None:
        current, other = self.root / "current.AppImage", self.root / "other.AppImage"
        current.write_bytes(b"current")
        other.write_bytes(b"other")
        os.utime(other, ns=(1, 1))  # older than the entry's target
        self.library.enable_autostart([str(current)])
        entry = self.library.autostart_file.read_text()
        self.library.refresh_autostart([sys.executable, "-O", "-m", "appglider"], packaged=False)
        self.library.refresh_autostart([str(other)], packaged=True)
        self.assertEqual(self.library.autostart_file.read_text(), entry)
        os.utime(other)  # now newer: a newer packaged image takes over
        self.library.refresh_autostart([str(other)], packaged=True)
        self.assertIn(f'Exec="{other}" "--tray"', self.library.autostart_file.read_text())

    def test_refresh_still_refuses_foreign_shortcuts(self) -> None:
        shortcut = self.library.desktop_dir / "appglider.desktop"
        shortcut.parent.mkdir(parents=True)
        shortcut.write_text("[Desktop Entry]\nExec=/bin/true\n")
        with self.assertRaisesRegex(OSError, "another application"):
            self.library.refresh_launcher_shortcut(["/x.AppImage"], packaged=True)

    def test_packaged_shortcut_accepts_image_path_with_spaces(self) -> None:
        shortcut = self.library.create_launcher_shortcut(["/home/a b/AppGlider.AppImage"])
        self.assertIn('Exec="/home/a b/AppGlider.AppImage"\n', shortcut.read_text())

    def test_failed_shortcut_write_preserves_the_previous_entry(self) -> None:
        self.install("1.0.0")
        self.library.create_launcher_shortcut(["/first/AppGlider.AppImage"])
        replace = os.replace
        for name, repair in (
            (f"appglider-{APP.id}.desktop", lambda: self.library.create_shortcut(APP)),
            ("appglider.desktop", lambda: self.library.create_launcher_shortcut(["/second"])),
        ):
            shortcut = self.library.desktop_dir / name
            original = shortcut.read_bytes()

            def fail_shortcut(source, destination):
                if destination == shortcut:
                    raise OSError("disk full")
                return replace(source, destination)

            with (
                patch("appglider.storage.os.replace", side_effect=fail_shortcut),
                self.assertRaisesRegex(OSError, "disk full"),
            ):
                repair()
            self.assertEqual(shortcut.read_bytes(), original)
            self.assertEqual(list(self.library.desktop_dir.glob(f".{name}-*")), [])

    def test_shortcut_commands_quote_special_characters_and_reject_control_characters(self) -> None:
        self.assertEqual(desktop_exec(["/a path/100%/app", "-m"]), '"/a path/100%%/app" "-m"')
        self.assertEqual(desktop_exec(['a"$`\\b']), '"a\\\\"\\\\$\\\\`\\\\\\\\b"')
        with self.assertRaisesRegex(ValueError, "control characters"):
            desktop_exec(["/bad\npath"])

    def test_reopen_preserves_library_and_shortcut_ownership(self) -> None:
        installed = self.install("1.0.0")
        marker = installed.path.parent.parent / ".appglider"
        self.assertEqual(marker.read_text(), f"AppGlider\n{APP.id}\n{APP.repo}\n")
        state = (self.library.data_dir / "library.json").read_bytes()
        shortcut = self.library.desktop_dir / f"appglider-{APP.id}.desktop"
        shortcut.write_text(
            shortcut.read_text().replace("PhotoCraft (AppGlider)", "Outdated application label")
        )
        reopened = Library(
            self.library.data_dir, self.library.apps_dir, desktop_dir=self.library.desktop_dir
        )
        self.assertEqual(reopened.scan()[APP.id], installed)
        reopened.create_shortcut(APP)
        self.assertIn("Name=PhotoCraft (AppGlider)", shortcut.read_text())
        self.assertEqual((self.library.data_dir / "library.json").read_bytes(), state)
        self.assertEqual(list(self.library.desktop_dir.glob("*.desktop")), [shortcut])

    def test_shortcut_repair_and_uninstall_preserve_other_shortcuts(self) -> None:
        self.install("1.0.0")
        own = self.library.desktop_dir / f"appglider-{APP.id}.desktop"
        own.write_text("[Desktop Entry]\nName=Someone else's shortcut\n")
        contents = own.read_text()
        with self.assertRaisesRegex(OSError, "not owned"):
            self.library.create_shortcut(APP)
        original = self.library.desktop_dir / "ai.storyteller.photocraft.desktop"
        original.write_text("[Desktop Entry]\nName=PhotoCraft from another installer\n")
        self.library.remove(APP)
        self.assertEqual(own.read_text(), contents)
        self.assertIn("another installer", original.read_text())

    def test_uninstall_removes_only_menu_entries_that_launch_the_removed_appimage(self) -> None:
        first = self.install("1.0.0")
        second = self.install("1.1.0")
        directory = self.library.desktop_dir

        def entry(name: str, exec_line: str) -> Path:
            path = directory / name
            path.write_text(f"[Desktop Entry]\nName={name}\n{exec_line}\n")
            return path

        own_app = entry("ai.storyteller.photocraft.desktop", f'Exec="{second.path}" %F')
        old_version = entry("old-version.desktop", f"Exec={first.path} %U")
        other_app = entry("other.desktop", 'Exec="/usr/bin/other" %F')
        no_command = entry("none.desktop", "Terminal=false")
        broken = entry("broken.desktop", 'Exec="unterminated')
        self.library.remove(APP)
        self.assertFalse(own_app.exists())
        self.assertFalse(old_version.exists())
        for kept in (other_app, no_command, broken):
            self.assertTrue(kept.exists())

    def test_shortcut_repair_preserves_existing_and_dangling_symlink_targets(self) -> None:
        self.install("1.0.0")
        shortcut = self.library.desktop_dir / f"appglider-{APP.id}.desktop"
        target = self.external / "foreign.desktop"
        contents = "[Desktop Entry]\nName=Foreign\nX-AppGlider=true\n"
        for existing in (True, False):
            with self.subTest(existing=existing):
                if existing:
                    target.write_text(contents)
                else:
                    target.unlink()
                shortcut.unlink()
                shortcut.symlink_to(target)
                with self.assertRaisesRegex(OSError, "not owned"):
                    self.library.create_shortcut(APP)
                self.assertTrue(shortcut.is_symlink())
                self.assertEqual(target.exists(), existing)
                if existing:
                    self.assertEqual(target.read_text(), contents)


class PreviousCopyTests(unittest.TestCase):
    def test_the_kept_copy_is_described_by_version_and_size_or_not_at_all(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name) / "1.0.0"
            folder.mkdir()
            kept = folder / "app.AppImage"
            kept.write_bytes(b"x" * 1234)
            current = Installed(
                Path(name) / "1.1.0" / "app.AppImage", "1.1.0", True, kept, False, True
            )
            described = previous_copy(current)
            assert described is not None
            self.assertEqual(
                (described.path, described.version, described.size, described.prerelease),
                (kept, "1.0.0", 1234, True),
            )
            self.assertIsNone(previous_copy(None))
            self.assertIsNone(previous_copy(Installed(current.path, "1.1.0", True)))
            self.assertIsNone(previous_copy(Installed(current.path, "1.1.0", True, folder)))
            kept.unlink()
            self.assertIsNone(previous_copy(current))


class ReleaseTests(unittest.TestCase):
    def metadata(self) -> dict:
        data = appimage_bytes()
        release = release_for("1.2.3", data)
        return {
            "tag_name": "v1.2.3",
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": release.name,
                    "browser_download_url": release.url,
                    "size": len(data),
                    "digest": "sha256:" + release.sha256,
                },
            ],
        }

    def test_selects_only_correct_architecture_and_repository(self) -> None:
        metadata = self.metadata()
        selected = select_release(APP, metadata, architecture())
        self.assertEqual(selected.version, "1.2.3")
        with self.assertRaisesRegex(ValueError, "No supported wrong-arch AppImage filename"):
            select_release(APP, metadata, "wrong-arch")
        metadata["assets"][0]["browser_download_url"] = "https://evil.example/payload.AppImage"
        with self.assertRaisesRegex(ValueError, "repository"):
            select_release(APP, metadata, architecture())

    def test_checksum_fallback_matches_filename_not_another_asset(self) -> None:
        metadata = self.metadata()
        expected = metadata["assets"][0].pop("digest").removeprefix("sha256:")
        filename = metadata["assets"][0]["name"]
        with patch(
            "appglider.releases.read_metadata",
            side_effect=[json.dumps(metadata).encode(), f"{expected}  {filename}\n".encode()],
        ):
            self.assertEqual(latest_release(APP, Event()).sha256, expected)
        with patch(
            "appglider.releases.read_metadata",
            side_effect=[
                json.dumps(metadata).encode(),
                f"{expected}  other-file.AppImage\n".encode(),
            ],
        ):
            with self.assertRaisesRegex(ValueError, "checksum"):
                latest_release(APP, Event())

    def test_rejects_prereleases_unsafe_tags_and_bad_size(self) -> None:
        for key, value in (("draft", True), ("prerelease", True), ("tag_name", "../evil")):
            metadata = self.metadata()
            metadata[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                select_release(APP, metadata, architecture())
        metadata = self.metadata()
        metadata["assets"][0]["size"] = -1
        with self.assertRaises(ValueError):
            select_release(APP, metadata, architecture())

    def test_release_notes_and_date_come_from_the_api_and_are_bounded_and_validated(self) -> None:
        metadata = {
            **self.metadata(),
            "body": "## New\n\n- one",
            "published_at": "2026-10-19T14:02:11Z",
        }
        release = select_release(APP, metadata, architecture())
        self.assertEqual((release.notes, release.published), ("## New\n\n- one", "2026-10-19"))
        long = select_release(APP, {**metadata, "body": "x" * 50_000}, architecture())
        self.assertEqual(len(long.notes), 20_000)
        for bad in (None, 5, ["x"], {"a": 1}):
            self.assertEqual(
                select_release(APP, {**metadata, "body": bad}, architecture()).notes, ""
            )
        for bad in (None, 20261019, "yesterday", "2026-13-45", "2026-10-19" + "T" * 200, ""):
            self.assertEqual(
                select_release(APP, {**metadata, "published_at": bad}, architecture()).published, ""
            )
        plain = select_release(APP, self.metadata(), architecture())
        self.assertEqual((plain.notes, plain.published), ("", ""))

    def test_versions_compare_numerically_and_do_not_guess_unknown_versions(self) -> None:
        self.assertGreater(version_key("1.10.0"), version_key("1.9.0"))
        self.assertGreater(version_key("1.0.0"), version_key("1.0.0-rc.1"))
        self.assertFalse(release_for("1.0.0", appimage_bytes()).newer_than(None))


if __name__ == "__main__":
    unittest.main()
