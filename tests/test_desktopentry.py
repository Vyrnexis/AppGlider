"""Menu metadata read from an AppImage's own .desktop file."""

import os
import stat
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from catalog_fixture import APPS

from appglider.install import desktopentry
from appglider.install.appimage import clean_environment
from appglider.install.desktopentry import (
    DesktopMetadata,
    extract_desktop_groups,
    keep_known,
    metadata_from_groups,
    parse_groups,
)
from appglider.install.launch_options import LaunchOptions
from appglider.install.shortcuts import shortcut_text

APP = APPS[0]

SAMPLE = textwrap.dedent(
    """\
    # comment
    [Desktop Entry]
    Type=Application
    Name=PhotoCraft
    Name[de]=Fotos
    Exec=photocraft --no-sandbox %U
    TryExec=photocraft
    Icon=photocraft
    DBusActivatable=true
    Path=/tmp
    NoDisplay=true
    X-Custom=yes
    Comment=Edit photos
    Categories=Graphics;2DGraphics;Bad Item;$(evil);;Graphics;
    MimeType=image/png;image/jpeg;x-scheme-handler/photo;bad value;
    Keywords=photo;edit;
    StartupWMClass=PhotoCraft
    StartupNotify=true
    Actions=new;bad/id;broken;
    [Desktop Action new]
    Name=New image
    Exec=photocraft --new %f
    [Desktop Action bad/id]
    Name=Skipped
    Exec=photocraft --skipped
    [Desktop Action broken]
    Name=No command
    """
)


def fake_appimage(directory: Path, body: str) -> Path:
    path = directory / "fake.AppImage"
    path.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class ParsingTests(unittest.TestCase):
    def metadata(self, text: str) -> DesktopMetadata:
        groups = keep_known(parse_groups(text))
        assert groups is not None
        return metadata_from_groups(groups)

    def test_known_fields_are_kept_and_filtered(self) -> None:
        metadata = self.metadata(SAMPLE)
        self.assertEqual(metadata.categories, ("Graphics", "2DGraphics"))
        self.assertEqual(metadata.mime_types, ("image/png", "image/jpeg", "x-scheme-handler/photo"))
        self.assertEqual(metadata.keywords, ("photo", "edit"))
        self.assertEqual(metadata.comment, "Edit photos")
        self.assertEqual(metadata.wm_class, "PhotoCraft")
        self.assertIs(metadata.startup_notify, True)
        self.assertTrue(metadata.urls)
        self.assertFalse(metadata.terminal)

    def test_actions_keep_their_arguments_but_not_the_program_or_field_codes(self) -> None:
        metadata = self.metadata(SAMPLE)
        self.assertEqual(len(metadata.actions), 1)
        action = metadata.actions[0]
        self.assertEqual(
            (action.id, action.name, action.arguments), ("new", "New image", ("--new",))
        )

    def test_unwanted_keys_are_dropped_before_anything_is_saved(self) -> None:
        groups = keep_known(parse_groups(SAMPLE))
        assert groups is not None
        for key in ("TryExec", "Icon", "DBusActivatable", "Path", "NoDisplay", "X-Custom", "Name"):
            self.assertNotIn(key, groups["Desktop Entry"])

    def test_control_characters_and_backslashes_discard_the_value(self) -> None:
        groups = {
            "Desktop Entry": {
                "Comment": "hi\nExec=evil",
                "Categories": "Graphics;\nUtility;",
                "MimeType": "image/png;\x01",
                "Keywords": "a\tb;c",
                "StartupWMClass": "Bad\nClass",
                "Actions": "new;",
            },
            "Desktop Action new": {"Name": "New\r", "Exec": "app --x\n--y"},
        }
        metadata = metadata_from_groups(groups)
        self.assertEqual(metadata, DesktopMetadata())
        self.assertEqual(metadata_from_groups({"Desktop Entry": {"Comment": "a\\sb"}}).comment, "")

    def test_lists_are_capped(self) -> None:
        entry = {"Categories": ";".join(f"C{n}" for n in range(50)), "Actions": ""}
        self.assertEqual(len(metadata_from_groups({"Desktop Entry": entry}).categories), 12)
        names = [f"a{n}" for n in range(30)]
        groups = {"Desktop Entry": {"Actions": ";".join(names)}}
        for name in names:
            groups[f"Desktop Action {name}"] = {"Name": name, "Exec": "app"}
        self.assertEqual(len(metadata_from_groups(groups).actions), 10)

    def test_actions_with_an_env_wrapper_or_too_many_arguments_are_skipped(self) -> None:
        groups = {
            "Desktop Entry": {"Actions": "a;b;c;"},
            "Desktop Action a": {"Name": "A", "Exec": "env X=1 app"},
            "Desktop Action b": {"Name": "B", "Exec": "app " + " ".join(["x"] * 40)},
            "Desktop Action c": {"Name": "C", "Exec": 'app "--title=100%% sure" %U'},
        }
        actions = metadata_from_groups(groups).actions
        self.assertEqual([(a.id, a.arguments) for a in actions], [("c", ("--title=100% sure",))])

    def test_terminal_and_notify_values(self) -> None:
        metadata = self.metadata("[Desktop Entry]\nTerminal=true\nStartupNotify=false\n")
        self.assertTrue(metadata.terminal)
        self.assertIs(metadata.startup_notify, False)
        self.assertIsNone(self.metadata("[Desktop Entry]\nStartupNotify=maybe\n").startup_notify)

    def test_text_without_a_desktop_entry_has_no_metadata(self) -> None:
        self.assertIsNone(keep_known(parse_groups("[Other]\nCategories=Graphics;\n")))


class ExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / "calls.log"
        self.source = self.root / "source"
        self.source.mkdir()

    def script(self, body: str) -> Path:
        return fake_appimage(self.root, body)

    def extract(self, path: Path) -> dict[str, dict[str, str]] | None:
        return extract_desktop_groups(path, APP, clean_environment())

    def test_a_symlinked_desktop_file_is_followed_inside_the_tree(self) -> None:
        (self.source / "real.desktop").write_text(SAMPLE)
        path = self.script(
            f"""\
            echo "$PWD|$1|$2|$APPIMAGE_EXTRACT_AND_RUN" >> {self.log}
            mkdir -p squashfs-root/usr/share/applications
            case "$2" in
              '*.desktop') ln -s usr/share/applications/p.desktop squashfs-root/p.desktop ;;
              *) cp {self.source}/real.desktop squashfs-root/"$2" ;;
            esac
            """
        )
        with patch.dict(os.environ, {"APPIMAGE_EXTRACT_AND_RUN": "1"}):
            groups = self.extract(path)
        assert groups is not None
        self.assertEqual(groups["Desktop Entry"]["Comment"], "Edit photos")
        calls = [line.split("|") for line in self.log.read_text().splitlines()]
        self.assertEqual([call[1] for call in calls], ["--appimage-extract"] * 2)
        self.assertEqual(
            [call[2] for call in calls], ["*.desktop", "usr/share/applications/p.desktop"]
        )
        self.assertTrue(all(call[3] == "" for call in calls))
        self.assertIn("appglider-desktop-", calls[0][0])
        self.assertFalse(Path(calls[0][0]).exists())

    def test_a_plain_top_level_file_needs_one_call(self) -> None:
        (self.source / "a.desktop").write_text("[Desktop Entry]\nCategories=Office;\n")
        path = self.script(
            f"""\
            echo call >> {self.log}
            mkdir -p squashfs-root && cp {self.source}/a.desktop squashfs-root/a.desktop
            """
        )
        groups = self.extract(path)
        assert groups is not None
        self.assertEqual(groups["Desktop Entry"]["Categories"], "Office;")
        self.assertEqual(len(self.log.read_text().splitlines()), 1)

    def test_the_file_matching_the_app_is_preferred(self) -> None:
        path = self.script(
            """\
            mkdir -p squashfs-root
            printf '[Desktop Entry]\\nComment=other\\n' > squashfs-root/aaa.desktop
            printf '[Desktop Entry]\\nComment=mine\\n' > squashfs-root/PhotoCraft.desktop
            """
        )
        groups = self.extract(path)
        assert groups is not None
        self.assertEqual(groups["Desktop Entry"]["Comment"], "mine")

    def test_links_leaving_the_tree_or_chained_links_are_refused(self) -> None:
        secret = self.root / "secret.desktop"
        secret.write_text("[Desktop Entry]\nComment=secret\n")
        for target in (str(secret), "../../secret.desktop", "usr/../../secret.desktop", "."):
            path = self.script(
                f"""\
                mkdir -p squashfs-root
                ln -s '{target}' squashfs-root/a.desktop
                """
            )
            self.assertIsNone(self.extract(path), target)
        chained = self.script(
            """\
            mkdir -p squashfs-root/usr
            case "$2" in
              '*.desktop') ln -s usr/b.desktop squashfs-root/a.desktop ;;
              *) ln -s "$PWD/squashfs-root/a.desktop" squashfs-root/usr/b.desktop ;;
            esac
            """
        )
        self.assertIsNone(self.extract(chained))

    def test_a_link_through_a_directory_link_cannot_escape(self) -> None:
        (self.root / "outside").mkdir()
        (self.root / "outside" / "a.desktop").write_text("[Desktop Entry]\nComment=out\n")
        path = self.script(
            f"""\
            mkdir -p squashfs-root
            case "$2" in
              '*.desktop') ln -s dir/a.desktop squashfs-root/a.desktop ;;
              *) ln -s {self.root}/outside squashfs-root/dir ;;
            esac
            """
        )
        self.assertIsNone(self.extract(path))

    def test_failures_give_none(self) -> None:
        self.assertIsNone(self.extract(self.root / "missing.AppImage"))
        self.assertIsNone(self.extract(self.script("exit 3\n")))
        self.assertIsNone(self.extract(self.script("mkdir -p squashfs-root\n")))
        big = self.script(
            "mkdir -p squashfs-root\n"
            "head -c 70000 /dev/zero | tr '\\0' 'a' > squashfs-root/a.desktop\n"
        )
        self.assertIsNone(self.extract(big))
        binary = self.script(
            "mkdir -p squashfs-root; printf '\\377\\376' > squashfs-root/a.desktop\n"
        )
        self.assertIsNone(self.extract(binary))

    def test_a_runtime_that_hangs_is_killed(self) -> None:
        path = self.script(f"echo $$ > {self.log}\nsleep 30\n")
        with patch.object(desktopentry, "EXTRACT_SECONDS", 0.3):
            started = time.monotonic()
            self.assertIsNone(self.extract(path))
        self.assertLess(time.monotonic() - started, 5)


class EntryTextTests(unittest.TestCase):
    def test_without_metadata_the_entry_is_plain_but_marked(self) -> None:
        text = shortcut_text(APP, Path("/apps/a b.AppImage"), Path("/i.png"), None, LaunchOptions())
        self.assertIn("Name=PhotoCraft (AppGlider)\n", text)
        self.assertIn('Exec="/apps/a b.AppImage" %F\n', text)
        self.assertIn("Categories=Utility;\n", text)
        self.assertIn("X-AppGlider=true\nX-AppGlider-Metadata=1\n", text)
        self.assertNotIn("MimeType", text)
        self.assertNotIn("Graphics", text)

    def test_metadata_fills_the_descriptive_keys_only(self) -> None:
        groups = keep_known(parse_groups(SAMPLE))
        assert groups is not None
        text = shortcut_text(
            APP,
            Path("/apps/p.AppImage"),
            Path("/i.png"),
            metadata_from_groups(groups),
            LaunchOptions(),
        )
        for line in (
            "Name=PhotoCraft (AppGlider)",
            "Comment=Edit photos",
            'Exec="/apps/p.AppImage" %U',
            "Icon=/i.png",
            "Terminal=false",
            "Categories=Graphics;2DGraphics;",
            "MimeType=image/png;image/jpeg;x-scheme-handler/photo;",
            "Keywords=photo;edit;",
            "StartupWMClass=PhotoCraft",
            "StartupNotify=true",
            "Actions=new;",
            "[Desktop Action new]",
            "Name=New image",
            'Exec="/apps/p.AppImage" "--new"',
        ):
            self.assertIn(line + "\n", text)
        for unwanted in ("TryExec", "DBusActivatable", "NoDisplay", "X-Custom", "Path="):
            self.assertNotIn(unwanted, text)

    def test_launch_options_shape_the_exec_lines(self) -> None:
        groups = keep_known(parse_groups(SAMPLE))
        assert groups is not None
        options = LaunchOptions(("--profile", "a b", "50%"), (("GTK_THEME", "Adwaita:dark"),))
        text = shortcut_text(
            APP, Path("/apps/p.AppImage"), Path("/i.png"), metadata_from_groups(groups), options
        )
        self.assertIn(
            'Exec="env" "GTK_THEME=Adwaita:dark" "/apps/p.AppImage" "--profile" "a b" "50%%" %U\n',
            text,
        )
        self.assertIn('Exec="env" "GTK_THEME=Adwaita:dark" "/apps/p.AppImage" "--new"\n', text)


if __name__ == "__main__":
    unittest.main()
