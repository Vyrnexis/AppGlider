"""Repository images are bounded, self-contained and cached without touching app files."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from catalog_fixture import stock
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from appglider import APP_ICON
from appglider.catalog import custom_app
from appglider.icons import (
    MAX_ICON_BYTES,
    discover_icons,
    icon_pixmap,
    is_strong,
    normalise_icon,
    web_files,
)
from appglider.library import Library
from appglider.releases import Cancelled, RateLimited

SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">'
    b'<path d="M0 0H64V64H0Z" fill="#39b3d7"/></svg>'
)
CUSTOM = custom_app("owner/example-app")


class IconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = stock(Library(self.root / "data", self.root / "apps", self.root / "desktop"))

    def test_png_and_svg_are_normalised_to_bounded_png(self):
        for data in (SVG, APP_ICON.read_bytes()):
            with self.subTest(kind=data[:4]):
                png = normalise_icon(data)
                image = QImage.fromData(png, "PNG")
                self.assertFalse(image.isNull())
                self.assertEqual(max(image.width(), image.height()), 512)
                self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_the_standard_svg_document_type_line_is_accepted(self):
        # Inkscape and most editors write it; VSCodium's icons carry it, for example.
        doctype = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN"'
            b' "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n'
        )
        image = QImage.fromData(normalise_icon(doctype + SVG), "PNG")
        self.assertEqual(image.size().width(), 512)

    def test_raster_icons_are_shrunk_but_never_enlarged(self):
        for side, expected in ((32, 32), (1024, 512)):
            with self.subTest(side=side):
                source = QImage(side, side, QImage.Format.Format_ARGB32)
                source.fill(0xFF39B3D7)
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                source.save(buffer, "PNG")
                image = QImage.fromData(normalise_icon(bytes(buffer.data().data())), "PNG")
                self.assertEqual((image.width(), image.height()), (expected, expected))

    def test_changed_icon_files_are_read_again_despite_the_pixmap_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "icon.png"
            for colour, stamp in ((0xFF0000, 1), (0x0000FF, 2)):
                image = QImage(8, 8, QImage.Format.Format_RGB32)
                image.fill(colour)
                image.save(str(path))
                os.utime(path, ns=(stamp * 10**9, stamp * 10**9))
                pixmap = icon_pixmap(path, 8)
                self.assertEqual(pixmap.toImage().pixel(4, 4) & 0xFFFFFF, colour)
            self.assertIs(icon_pixmap(path, 8), icon_pixmap(path, 8))

    def test_invalid_large_and_external_resource_images_are_rejected(self):
        invalid = [
            b"",
            b"not an image",
            b"x" * (MAX_ICON_BYTES + 1),
            SVG.replace(b'width="64"', b'width="100000"'),
            SVG.replace(b"<path", b'<image href="file:///etc/passwd"/><path'),
            SVG.replace(b"<path", b'<image href="https://example.com/icon.png"/><path'),
            b'<!DOCTYPE svg [<!ENTITY x "hello">]>' + SVG,
            b'<!DOCTYPE svg PUBLIC "a" "b" [<!ENTITY x "hello">]>' + SVG,
            b'<!DOCTYPE svg PUBLIC "a" "b"><!DOCTYPE svg [<!ENTITY x "y">]>' + SVG,
            SVG.replace(b"<path", b'<style>@import "https://example.com/a.css";</style><path'),
            SVG.replace(b'fill="#39b3d7"', b'fill="url(file:///tmp/image)"'),
        ]
        for data in invalid:
            with self.subTest(data=data[:80]), self.assertRaises(ValueError):
                normalise_icon(data)

    def repository(self, files):
        """A fake repository: the API tree, the raw files and the web folder listings."""
        entries = [
            {"path": path, "sha": "a" * 40, "mode": "100644", "type": "blob", "size": len(data)}
            for path, data in files.items()
        ]
        tree = json.dumps({"tree": entries, "truncated": False}).encode()

        def raw(repo, path, limit, cancel):
            self.assertEqual(repo, "owner/example-app")
            if path not in files:
                raise ValueError("GitHub answered HTTP 404.")
            return files[path]

        def listing(repo, path, cancel):
            self.assertEqual(repo, "owner/example-app")
            prefix = path + "/" if path else ""
            names = {}
            for file in files:
                if file.startswith(prefix):
                    head, _, rest = file[len(prefix) :].partition("/")
                    names[head] = "directory" if rest else "file"
            items = [
                {"name": name, "path": prefix + name, "contentType": kind}
                for name, kind in sorted(names.items())
            ]
            return items, repo

        return entries, tree, raw, listing

    def discover(self, files, *, quota_low=False, tree=None):
        entries, api_tree, raw, listing = self.repository(files)
        metadata = (
            patch("appglider.icons.read_metadata", return_value=tree or api_tree)
            if not isinstance(tree, Exception)
            else patch("appglider.icons.read_metadata", side_effect=tree)
        )
        with (
            metadata as api,
            patch("appglider.icons.releases.raw_bytes", side_effect=raw) as raw_read,
            patch("appglider.icons.releases.web_listing", side_effect=listing) as web,
            patch("appglider.icons.github_status.quota_low", return_value=quota_low),
            patch("appglider.icons.LISTING_PAUSE", 0),
        ):
            result = discover_icons(CUSTOM, Event())
        return result, api, raw_read, web

    LAYOUT = {
        "packaging/application.desktop": b"[Desktop Entry]\nIcon=actual-app\n",
        "assets/logo.svg": SVG.replace(b"39b3d7", b"112233"),
        "assets/linux/hicolor/512x512/apps/actual-app.svg": SVG,
        "assets/screenshot.svg": SVG,
    }

    def test_with_plenty_of_allowance_one_api_request_lists_files_and_images_come_from_raw(self):
        files = {**self.LAYOUT, "assets/linked-icon.svg": SVG}
        entries, tree, _, _ = self.repository(files)
        entries[-1]["mode"] = "120000"  # a symbolic link is never read
        tree = json.dumps({"tree": entries, "truncated": False}).encode()
        result, api, raw_read, web = self.discover(files, tree=tree)
        self.assertEqual(
            [icon.name for icon in result],
            ["assets/linux/hicolor/512x512/apps/actual-app.svg", "assets/logo.svg"],
        )
        self.assertTrue(all(icon.png.startswith(b"\x89PNG") for icon in result))
        api.assert_called_once()
        self.assertIn("/repos/owner/example-app/git/trees/HEAD", api.call_args.args[0])
        web.assert_not_called()
        read = [call.args[1] for call in raw_read.call_args_list]
        self.assertNotIn("assets/linked-icon.svg", read)
        self.assertNotIn("assets/screenshot.svg", read)

    def test_the_web_walk_finds_the_same_icons_without_any_api_request(self):
        for label, options in (
            ("low allowance", {"quota_low": True}),
            ("rate limited", {"tree": RateLimited("api.github.com", 1)}),
            (
                "too big for the listing",
                {"tree": json.dumps({"tree": [], "truncated": True}).encode()},
            ),
            ("unusable listing", {"tree": b"[]"}),
        ):
            with self.subTest(label):
                result, api, _, web = self.discover(self.LAYOUT, **options)
                self.assertEqual(
                    [icon.name for icon in result],
                    ["assets/linux/hicolor/512x512/apps/actual-app.svg", "assets/logo.svg"],
                )
                self.assertTrue(web.called)
                if options.get("quota_low"):
                    api.assert_not_called()

    def test_reverse_dns_icon_names_and_project_names_decide_the_ranking(self):
        files = {
            "app.desktop": b"[Desktop Entry]\nIcon=ai.vendor.example-app\n",
            "assets/icons/zzz-logo.png": SVG,
            "assets/icons/ai.vendor.example-app.svg": SVG,
            "assets/icons/example-app-icon.svg": SVG,
            "assets/icons/aaa-icon.svg": SVG,
        }
        result, *_ = self.discover(files)
        names = [icon.name for icon in result]
        self.assertEqual(names[0], "assets/icons/ai.vendor.example-app.svg")  # declared name
        self.assertEqual(names[1], "assets/icons/example-app-icon.svg")  # matches the project
        plain = {path: data for path, data in files.items() if path != "app.desktop"}
        plain.pop("assets/icons/ai.vendor.example-app.svg")
        result, *_ = self.discover(plain)
        self.assertEqual(result[0].name, "assets/icons/example-app-icon.svg")

    def test_images_named_after_the_project_count_and_platform_variants_do_not(self):
        files = {
            f"resources/images/{name}": SVG
            for name in (
                "ExampleApp.svg",
                "ExampleApp.png",
                "ExampleApp_192px.png",
                "ExampleApp_128px.png",
                "ExampleApp_32px.png",
                "ExampleApp-mac_128px.png",
                "ExampleApp_about.svg",
                "ExampleApp_gradient.svg",
                "ExampleApp_192px_grayscale.png",
                "ExampleAppTitle.png",
                "unrelated.png",
                "ams_icon.svg",
            )
        }
        result, *_ = self.discover(files)
        names = [icon.name.rsplit("/", 1)[1] for icon in result]
        self.assertEqual(
            names[:3], ["ExampleApp_192px.png", "ExampleApp_128px.png", "ExampleApp_32px.png"][:3]
        )
        self.assertNotIn("ExampleApp-mac_128px.png", names)
        self.assertNotIn("ExampleApp_about.svg", names)
        self.assertNotIn("ExampleApp_192px_grayscale.png", names)
        self.assertNotIn("unrelated.png", names)

    def test_preview_build_logos_and_non_square_artwork_come_after_the_icon(self):
        wide = SVG.replace(b'width="64"', b'width="200"')
        files = {
            "icons/insider/example_app.svg": SVG.replace(b"39b3d7", b"ff8800"),
            "icons/stable/example_app.svg": SVG,
            "icons/corner_512.svg": wide,
        }
        result, *_ = self.discover(files)
        self.assertEqual(
            [icon.name for icon in result], ["icons/stable/example_app.svg", "icons/corner_512.svg"]
        )

    def test_a_very_short_project_name_does_not_match_everything_containing_it(self):
        short = {f"images/{n}.png": SVG for n in ("app", "happy", "map", "icon")}
        entries = [
            {"path": path, "sha": "a" * 40, "mode": "100644", "type": "blob", "size": len(SVG)}
            for path in short
        ]
        tree = json.dumps({"tree": entries, "truncated": False}).encode()
        with (
            patch("appglider.icons.read_metadata", return_value=tree),
            patch("appglider.icons.releases.raw_bytes", return_value=SVG),
            patch("appglider.icons.github_status.quota_low", return_value=False),
        ):
            found = [icon.name for icon in discover_icons(custom_app("owner/app"), Event())]
        self.assertEqual(found, ["images/icon.png"])  # "app" is too short to count as a name

    def test_bad_candidates_are_bounded_and_missing_images_are_skipped(self):
        files = {f"assets/icon-{n}.png": b"broken" + bytes([n]) for n in range(20)}
        result, api, raw_read, _ = self.discover(files)
        self.assertEqual(result, ())
        api.assert_called_once()
        self.assertEqual(raw_read.call_count, 8)
        result, *_ = self.discover({"assets/icon.svg": SVG, "assets/icon-b.svg": SVG})
        self.assertEqual(len(result), 2)

    def test_cancellation_is_honoured_before_any_request_and_during_the_walk(self):
        cancel = Event()
        cancel.set()
        with (
            patch("appglider.icons.read_metadata") as api,
            patch("appglider.icons.releases.web_listing") as web,
            self.assertRaises(Cancelled),
        ):
            discover_icons(CUSTOM, cancel)
        api.assert_not_called()
        web.assert_not_called()
        cancel = Event()

        def listing(repo, path, event):
            cancel.set()
            return [{"name": "a", "path": "a", "contentType": "directory"}], repo

        with (
            patch("appglider.icons.github_status.quota_low", return_value=True),
            patch("appglider.icons.releases.web_listing", side_effect=listing),
            patch("appglider.icons.LISTING_PAUSE", 0),
            self.assertRaises(Cancelled),
        ):
            discover_icons(CUSTOM, cancel)

    def test_a_moved_repository_is_followed_by_its_new_name(self):
        calls, reads = [], []

        def listing(repo, path, cancel):
            calls.append(repo)
            if path == "":
                return [
                    {"name": "assets", "path": "assets", "contentType": "directory"},
                    {"name": "logo.svg", "path": "logo.svg", "contentType": "file"},
                ], "new-owner/new-name"
            return [], repo

        def raw(repo, path, limit, cancel):
            reads.append(repo)
            return SVG

        with (
            patch("appglider.icons.github_status.quota_low", return_value=True),
            patch("appglider.icons.releases.web_listing", side_effect=listing),
            patch("appglider.icons.releases.raw_bytes", side_effect=raw),
            patch("appglider.icons.LISTING_PAUSE", 0),
        ):
            result = discover_icons(CUSTOM, Event())
        self.assertEqual([icon.name for icon in result], ["logo.svg"])
        self.assertEqual(calls[0], "owner/example-app")
        self.assertEqual(set(calls[1:]), {"new-owner/new-name"})  # no redirect on later pages
        self.assertEqual(reads, ["new-owner/new-name"])

    def test_a_listing_too_large_for_the_api_falls_back_to_the_web_walk(self):
        for error in (
            ValueError("The release metadata is too large."),
            RateLimited("api.github.com", 1),
        ):
            with self.subTest(error=error):
                result, api, _, web = self.discover(self.LAYOUT, tree=error)
                self.assertEqual(len(result), 2)
                api.assert_called_once()
                self.assertTrue(web.called)

    def walk(self, listing, **limits):
        with (
            patch("appglider.icons.releases.web_listing", side_effect=listing) as web,
            patch("appglider.icons.LISTING_PAUSE", 0),
        ):
            for name, value in limits.items():
                patcher = patch(f"appglider.icons.{name}", value)
                patcher.start()
                self.addCleanup(patcher.stop)
            files, _ = web_files(CUSTOM, Event())
        return files, [call.args[1] for call in web.call_args_list]

    def test_the_walk_opens_top_levels_then_only_artwork_like_folders_and_skips_dependencies(self):
        tree = {
            "": ["src", "docs", "assets", "3rdParty", "packaging", "README.md"],
            "src": ["Gui", "Mod", "3rdParty", "main.cpp"],
            "src/Gui": ["Icons", "Dialogs", "x.cpp"],
            "src/Gui/Icons": ["app.svg"],
            "src/Gui/Dialogs": ["dialog.cpp"],
            "src/Mod": ["Part", "a.py"],
            "src/Mod/Part": ["part.svg"],
            "assets": ["icons", "notes.md"],
            "assets/icons": ["app", "ui"],
            "assets/icons/app": ["linux/exports/hicolor"],
            "assets/icons/app/linux/exports/hicolor": ["16x16", "128x128", "512x512", "scalable"],
            "assets/icons/app/linux/exports/hicolor/512x512": ["apps"],
            "assets/icons/app/linux/exports/hicolor/512x512/apps": ["app.png"],
            "assets/icons/app/linux/exports/hicolor/128x128": ["apps"],
            "assets/icons/app/linux/exports/hicolor/128x128/apps": ["app.png"],
            "assets/icons/app/linux/exports/hicolor/16x16": ["apps"],
            "assets/icons/app/linux/exports/hicolor/16x16/apps": ["app.png"],
            "assets/icons/app/linux/exports/hicolor/scalable": ["apps"],
            "assets/icons/app/linux/exports/hicolor/scalable/apps": ["app.svg"],
            "packaging": ["linux"],
            "packaging/linux": ["app.desktop"],
        }

        def listing(repo, path, cancel):
            return [
                {
                    "name": name.rsplit("/", 1)[-1],
                    "path": (path + "/" if path else "") + name,
                    "contentType": "directory"
                    if (path + "/" if path else "") + name in tree
                    else "file",
                }
                for name in tree.get(path, [])
            ], repo

        files, opened = self.walk(listing, EXTRA_PAGES=1000)  # no early stop here
        self.assertNotIn("3rdParty", opened)
        self.assertNotIn("docs", opened)
        self.assertNotIn("src/3rdParty", opened)
        self.assertIn("src/Gui", opened)  # second-level folders are all looked at
        self.assertIn("src/Gui/Icons", opened)  # then the artwork-like one inside
        self.assertNotIn("src/Gui/Dialogs", opened)  # but not the others
        self.assertNotIn("src/Mod/Part", opened)
        hicolor = "assets/icons/app/linux/exports/hicolor"
        self.assertIn(f"{hicolor}/512x512/apps", opened)
        self.assertIn(f"{hicolor}/128x128/apps", opened)
        self.assertNotIn(f"{hicolor}/16x16", opened)  # only the largest sizes
        self.assertIn("src/Gui/Icons/app.svg", files)
        self.assertTrue(all(size is None for size in files.values()))
        self.assertEqual(opened[0], "")

    def endless_with_strong_file_on(self, page):
        """Every folder holds three more folders; the project-named icon shows up on `page`."""
        opened = []

        def listing(repo, path, cancel):
            opened.append(path)
            names = ("assets", "icons", "images")  # artwork-like, so the walk keeps going
            items = [
                {"name": n, "path": f"{path}/{n}".lstrip("/"), "contentType": "directory"}
                for n in names
            ]
            if len(opened) == page:
                items.append(
                    {"name": "ExampleApp.png", "path": "ExampleApp.png", "contentType": "file"}
                )
            return items, repo

        return listing

    def test_the_walk_stops_a_little_after_the_first_strong_candidate(self):
        for page, total in ((2, 8), (4, 10), (12, 24)):  # page + max(6, page)
            with self.subTest(page=page):
                files, opened = self.walk(self.endless_with_strong_file_on(page))
                self.assertEqual(len(opened), total)
                self.assertIn("ExampleApp.png", files)
        _, opened = self.walk(self.endless_with_strong_file_on(99))  # none: the page cap applies
        self.assertEqual(len(opened), 40)

    def test_what_counts_as_a_strong_candidate(self):
        for path, strong in (
            ("res/ExampleApp.png", True),
            ("res/example_app_128px.svg", True),
            ("assets/hicolor/512x512/apps/whatever.png", True),
            ("res/ExampleApp-mac_128px.png", False),
            ("res/ExampleApp_about.svg", False),
            ("res/ExampleApp.ico", False),
            ("res/other.png", False),
        ):
            with self.subTest(path=path):
                self.assertEqual(is_strong(path, "exampleapp"), strong)
        self.assertFalse(is_strong("res/app.png", "app"))  # names under four letters never match
        self.assertTrue(is_strong("x/hicolor/app.png", "app"))

    def test_the_search_reports_what_it_is_doing_so_a_slow_one_does_not_look_frozen(self):
        messages = []
        with (
            patch("appglider.icons.read_metadata", side_effect=RateLimited("api.github.com", 1)),
            patch(
                "appglider.icons.releases.web_listing",
                side_effect=self.endless_with_strong_file_on(2),
            ),
            patch("appglider.icons.releases.raw_bytes", return_value=SVG),
            patch("appglider.icons.github_status.quota_low", return_value=False),
            patch("appglider.icons.LISTING_PAUSE", 0),
        ):
            discover_icons(CUSTOM, Event(), messages.append)
        self.assertEqual(messages[0], "Reading Example App's file list…")
        self.assertIn("up to half a minute", messages[1])
        pages = [m for m in messages if "pages so far" in m]
        self.assertEqual(
            pages, [f"Looking through Example App's folders… {n} pages so far" for n in range(1, 9)]
        )
        self.assertEqual(messages[-1], "Checking the possible icons…")
        with (
            patch("appglider.icons.github_status.quota_low", return_value=True),
            patch("appglider.icons.releases.web_listing", return_value=([], "owner/example-app")),
        ):
            discover_icons(CUSTOM, Event())  # progress is optional

    def test_the_walk_is_capped_in_pages_and_time_and_survives_page_failures(self):
        def endless(repo, path, cancel):
            return [
                {"name": f"d{i}", "path": f"{path}/d{i}".lstrip("/"), "contentType": "directory"}
                for i in range(3)
            ], repo

        _, opened = self.walk(endless, MAX_LISTINGS=7)
        self.assertEqual(len(opened), 7)
        with patch("appglider.icons.time.monotonic", side_effect=[0, 0, 100, 100]):
            _, opened = self.walk(endless)
        self.assertEqual(len(opened), 1)  # the time budget ended it

        def first_fails(repo, path, cancel):
            raise ValueError("HTTP 500")

        files, opened = self.walk(first_fails)
        self.assertEqual((files, opened), ({}, [""]))

        def later_fails(repo, path, cancel):
            if path:
                raise ValueError("HTTP 500")
            return [
                {"name": "assets", "path": "assets", "contentType": "directory"},
                {"name": "logo.svg", "path": "logo.svg", "contentType": "file"},
            ], repo

        files, opened = self.walk(later_fails)
        self.assertEqual(list(files), ["logo.svg"])  # what was learnt before the failure stays

    def test_saved_icon_survives_restart_and_failed_replacement(self):
        self.library.add_repository(CUSTOM)
        self.library.set_icon(CUSTOM, SVG)
        path = self.library.icon_path(CUSTOM)
        original = path.read_bytes()
        self.assertNotEqual(path, APP_ICON)
        reopened = Library(self.library.data_dir, self.library.apps_dir, self.library.desktop_dir)
        self.assertEqual(reopened.icon_path(CUSTOM), path)
        self.assertEqual(reopened.apps[-1], CUSTOM)
        with patch("appglider.storage.os.replace", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                reopened.set_icon(CUSTOM, SVG.replace(b"39b3d7", b"112233"))
        with self.assertRaises(ValueError):
            reopened.set_icon(CUSTOM, b"not an image")
        self.assertEqual(path.read_bytes(), original)
        self.library.remove_repository(CUSTOM.id)
        self.assertFalse(path.exists())

    def test_unknown_and_symlinked_icon_locations_are_preserved(self):
        with self.assertRaisesRegex(ValueError, "not in the library"):
            self.library.set_icon(CUSTOM, SVG)
        self.library.add_repository(CUSTOM)
        external = self.root / "external"
        external.mkdir()
        directory = self.library.data_dir / "icons"
        directory.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            self.library.set_icon(CUSTOM, SVG)
        self.assertEqual(self.library.icon_path(CUSTOM), APP_ICON)
        self.assertEqual(list(external.iterdir()), [])
