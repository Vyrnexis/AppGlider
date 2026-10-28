import io
import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from catalog_fixture import APPS, STARTER_ENTRIES, stock
from test_library import APP, appimage_bytes, ignore_progress, release_for

from appglider.core.catalog import custom_app
from appglider.core.catalogfile import EXAMPLE_CATALOG, CatalogEntry, parse, read, render
from appglider.install.library import Library


class CatalogImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.library = Library(root / "data", root / "apps", desktop_dir=root / "desktop")

    def saved(self, name: str) -> object:
        return json.loads((self.library.data_dir / name).read_text())

    def test_import_adds_new_entries_with_their_preferences(self) -> None:
        plan = self.library.import_catalog(
            [
                CatalogEntry("owner/one", "One"),
                CatalogEntry("owner/two", "Two", "Does two things", False, "Two.AppImage"),
            ]
        )
        self.assertEqual([entry.repo for entry in plan.new], ["owner/one", "owner/two"])
        self.assertEqual(plan.existing, ())
        apps = {app.repo: app for app in self.library.apps}
        two = apps["owner/two"]
        self.assertEqual(
            (two.name, two.description, two.include_prereleases, two.build_variant),
            ("Two", "Does two things", True, "Two.AppImage"),
        )
        self.assertTrue(apps["owner/one"].description.startswith("AppImage from "))
        self.assertEqual(
            self.saved("repositories.json"),
            [
                {"repo": "owner/one", "name": "One"},
                {"repo": "owner/two", "name": "Two", "description": "Does two things"},
            ],
        )
        self.assertEqual(self.saved("release-preferences.json"), {two.id: True})
        self.assertEqual(self.saved("build-preferences.json"), {two.id: "Two.AppImage"})

    def test_existing_apps_are_left_unchanged_and_reported(self) -> None:
        stock(self.library)
        mine = custom_app("owner/mine", "My name", True, "Mine.AppImage")
        self.library.add_repository(mine)
        before = self.library.apps
        plan = self.library.import_catalog(
            [
                CatalogEntry("OWNER/Mine", "Renamed", "Other", True, ""),
                CatalogEntry(APPS[0].repo, "Different name"),
                CatalogEntry("owner/fresh", "Fresh"),
                CatalogEntry("owner/fresh", "Fresh again"),
            ]
        )
        self.assertEqual([entry.repo for entry in plan.new], ["owner/fresh"])
        self.assertEqual(len(plan.existing), 3)  # includes the repeated entry
        after = self.library.apps
        self.assertEqual(after[: len(before)], before)
        self.assertEqual(len(after), len(before) + 1)
        self.assertEqual(next(app for app in after if app.repo == "owner/mine"), mine)

    def test_nothing_new_writes_nothing(self) -> None:
        stock(self.library)
        path = self.library.data_dir / "repositories.json"
        before = path.read_bytes()
        plan = self.library.import_catalog([CatalogEntry(APPS[0].repo, APPS[0].name)])
        self.assertEqual((plan.new, len(plan.existing)), ((), 1))
        self.assertEqual(path.read_bytes(), before)

    def test_import_respects_the_library_limit_without_partial_writes(self) -> None:
        stock(self.library)
        room = 100 - len(APPS)
        entries = [CatalogEntry(f"owner/app{i}", f"App {i}") for i in range(room + 1)]
        before = (self.library.data_dir / "repositories.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "up to 100"):
            self.library.import_catalog(entries)
        self.assertEqual(self.library.apps, APPS)
        self.assertEqual((self.library.data_dir / "repositories.json").read_bytes(), before)
        self.library.import_catalog(entries[:room])
        self.assertEqual(len(self.library.apps), 100)

    def test_installs_made_with_the_old_built_in_ids_match_again_after_importing(self) -> None:
        stock(self.library)
        data = appimage_bytes()
        with patch("appglider.github.http.request", return_value=io.BytesIO(data)):
            self.library.install(APP, release_for("1.0.0", data), Event(), ignore_progress)
        (self.library.data_dir / "repositories.json").unlink()  # as in a 0.4.0 library
        fresh = Library(
            self.library.data_dir, self.library.apps_dir, desktop_dir=self.library.desktop_dir
        )
        self.assertEqual(fresh.apps, ())
        self.assertEqual(fresh.scan(), {})
        self.assertEqual([orphan.app_id for orphan in fresh.orphans()], [APP.id])
        fresh.import_catalog(STARTER_ENTRIES)
        self.assertEqual(fresh.orphans(), [])
        self.assertEqual(fresh.scan()[APP.id].version, "1.0.0")

    def test_the_bundled_example_catalog_matches_the_published_one_and_imports_cleanly(
        self,
    ) -> None:
        published = Path(__file__).parents[1] / "docs" / "catalogs" / "artcraft.toml"
        self.assertEqual(EXAMPLE_CATALOG.read_bytes(), published.read_bytes())
        catalog = read(EXAMPLE_CATALOG)
        self.assertEqual((catalog.skipped, catalog.ignored), ((), ()))
        plan = self.library.import_catalog(catalog.entries)
        self.assertEqual(len(plan.new), 14)
        self.assertEqual({app.id for app in self.library.apps}, {app.id for app in APPS})

    def test_invalid_entry_is_refused_before_anything_is_written(self) -> None:
        with self.assertRaises(ValueError):
            self.library.import_catalog(
                [CatalogEntry("owner/good", "Good"), CatalogEntry("https://evil.test/a/b", "Bad")]
            )
        self.assertFalse((self.library.data_dir / "repositories.json").exists())

    def test_description_survives_a_restart_and_old_files_still_load(self) -> None:
        self.library.add_repository(custom_app("owner/described", "D", description="About D"))
        reopened = Library(
            self.library.data_dir, self.library.apps_dir, desktop_dir=self.library.desktop_dir
        )
        self.assertEqual(
            next(app for app in reopened.apps if app.repo == "owner/described").description,
            "About D",
        )
        (self.library.data_dir / "repositories.json").write_text(
            '[{"repo": "owner/old", "name": "Old"}]'
        )
        old = Library(
            self.library.data_dir, self.library.apps_dir, desktop_dir=self.library.desktop_dir
        )
        self.assertEqual(old.apps[-1].description, "AppImage from owner/old")

    def test_bad_saved_description_keeps_the_file_untouched(self) -> None:
        path = self.library.data_dir / "repositories.json"
        path.parent.mkdir(parents=True)
        path.write_text('[{"repo": "owner/x", "name": "X", "description": 5}]')
        broken = Library(
            self.library.data_dir, self.library.apps_dir, desktop_dir=self.library.desktop_dir
        )
        with self.assertRaisesRegex(ValueError, "file was preserved"):
            broken.apps
        self.assertIn('"description": 5', path.read_text())

    def test_export_then_import_into_an_empty_library_reproduces_the_catalog(self) -> None:
        self.library.import_catalog(
            [CatalogEntry("owner/one", "One", "About one", False, "One.AppImage")]
        )
        text = render(self.library.export_entries(), "Backup")
        other_root = Path(self.temp.name) / "other"
        other = Library(other_root / "data", other_root / "apps", desktop_dir=other_root / "d")
        parsed = parse(text)
        self.assertEqual(parsed.skipped, ())
        other.import_catalog(parsed.entries)
        mine = {app.repo: app for app in self.library.apps}
        theirs = {app.repo: app for app in other.apps}
        self.assertEqual(mine.keys(), theirs.keys())
        self.assertEqual(mine["owner/one"], theirs["owner/one"])


if __name__ == "__main__":
    unittest.main()
