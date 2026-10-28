import tempfile
import tomllib
import unittest
from pathlib import Path

from catalog_fixture import APPS

from appglider.core.catalog import custom_app
from appglider.core.catalogfile import (
    MAX_APPS,
    MAX_BYTES,
    CatalogEntry,
    CatalogError,
    entry_for,
    parse,
    read,
    render,
)


class ParseTests(unittest.TestCase):
    def test_minimal_and_full_entries(self) -> None:
        catalog = parse(
            """
format = 1
name = "My catalog"

[[app]]
repo = "https://github.com/Owner/Some-App"

[[app]]
repo = "owner/other"
name = "Other"
description = "Does things"
stable_only = false
build = "Other-x86_64.AppImage"
"""
        )
        self.assertEqual(catalog.name, "My catalog")
        self.assertEqual(
            catalog.entries,
            (
                CatalogEntry("owner/some-app", "Some App"),
                CatalogEntry("owner/other", "Other", "Does things", False, "Other-x86_64.AppImage"),
            ),
        )
        self.assertEqual((catalog.skipped, catalog.ignored), ((), ()))

    def test_empty_catalog_is_valid(self) -> None:
        self.assertEqual(parse("format = 1").entries, ())

    def test_file_level_problems_reject_everything(self) -> None:
        cases = {
            "not toml [": "not valid TOML",
            "name = 'x'": "format number",
            "format = '1'": "format number",
            "format = true": "format number",
            "format = 2": "newer AppGlider",
            "format = 0": "reads format 1",
            "format = 1\nname = 5": "catalog name",
            "format = 1\nname = ''.join": "not valid TOML",
            "format = 1\napp = 3": r"\[\[app\]\] tables",
            'format = 1\nname = "a\\u0007b"': "catalog name",
        }
        for text, message in cases.items():
            with self.subTest(text=text), self.assertRaisesRegex(CatalogError, message):
                parse(text)

    def test_limits(self) -> None:
        many = "format = 1\n" + "[[app]]\nrepo = 'a/b'\n" * (MAX_APPS + 1)
        with self.assertRaisesRegex(CatalogError, "more than 100"):
            parse(many)
        with self.assertRaisesRegex(CatalogError, "256 KiB"):
            parse("format = 1\n#" + "x" * MAX_BYTES)
        with self.assertRaisesRegex(CatalogError, "UTF-8"):
            parse(b"format = 1\nname = '\xff'")

    def test_bad_entries_are_skipped_and_reported_while_good_ones_stay(self) -> None:
        catalog = parse(
            """
format = 1
[[app]]
repo = "owner/good"
[[app]]
repo = "https://evil.test/owner/app"
[[app]]
name = "no repo"
[[app]]
repo = "owner/two"
stable_only = "yes"
[[app]]
repo = "owner/three"
build = "../escape"
[[app]]
repo = "owner/four"
description = "line one\\nline two"
[[app]]
repo = "OWNER/GOOD"
[[app]]
repo = "owner/five"
name = 5
"""
        )
        self.assertEqual([entry.repo for entry in catalog.entries], ["owner/good"])
        self.assertEqual(len(catalog.skipped), 7)
        joined = "\n".join(catalog.skipped)
        for expected in (
            "GitHub repository URL",
            "repo is required",
            "stable_only",
            "Invalid AppImage build",
            "description",
            "listed more than once",
            "name must be text",
        ):
            self.assertIn(expected, joined)

    def test_unknown_keys_are_ignored_and_reported(self) -> None:
        catalog = parse(
            'format = 1\nextra = 1\n[[app]]\nrepo = "owner/app"\nicon = "x.png"\nnote = 2\n'
        )
        self.assertEqual(len(catalog.entries), 1)
        self.assertEqual(catalog.ignored, ("extra", "app 1: icon", "app 1: note"))

    def test_text_is_not_interpreted(self) -> None:
        catalog = parse(
            'format = 1\n[[app]]\nrepo = "owner/app"\nname = "<b>Bold</b> & \\"quoted\\""\n'
            'description = "<a href=\\"http://x\\">click</a>"\n'
        )
        self.assertEqual(catalog.entries[0].name, '<b>Bold</b> & "quoted"')
        self.assertEqual(catalog.entries[0].description, '<a href="http://x">click</a>')


class RenderTests(unittest.TestCase):
    def test_render_round_trips_through_parse_and_tomllib(self) -> None:
        entries = (
            CatalogEntry("owner/zeta", "Zeta"),
            CatalogEntry(
                "owner/alpha", 'Alpha "A" \\ back', "Spaces and ünïcode ✓", False, "A.AppImage"
            ),
        )
        text = render(entries, "Name with # hash")
        tomllib.loads(text)
        parsed = parse(text)
        self.assertEqual(parsed.name, "Name with # hash")
        self.assertEqual(set(parsed.entries), {*entries})
        self.assertEqual([entry.repo for entry in parsed.entries], ["owner/alpha", "owner/zeta"])
        self.assertEqual((parsed.skipped, parsed.ignored), ((), ()))
        self.assertTrue(text.startswith("# AppGlider catalog"))

    def test_render_escapes_control_characters_so_the_file_stays_valid(self) -> None:
        nasty = "a\x00b\x07c\x7fd\re\nf"
        data = tomllib.loads(render([CatalogEntry("owner/app", nasty)]))
        self.assertEqual(data["app"][0]["name"], nasty)

    def test_entry_for_hides_generated_descriptions_and_keeps_curated_ones(self) -> None:
        custom = custom_app("owner/app", "App", True, "App.AppImage")
        self.assertEqual(
            entry_for(custom), CatalogEntry("owner/app", "App", "", False, "App.AppImage")
        )
        built_in = entry_for(APPS[0])
        self.assertEqual(built_in.description, APPS[0].description)
        self.assertEqual(built_in.repo, APPS[0].repo)

    def test_built_in_apps_survive_export_and_reimport(self) -> None:
        parsed = parse(render(entry_for(app) for app in APPS))
        self.assertEqual(len(parsed.entries), len(APPS))
        self.assertEqual(parsed.skipped, ())
        self.assertEqual({entry.repo for entry in parsed.entries}, {app.repo for app in APPS})


class StarterCatalogTests(unittest.TestCase):
    def test_the_published_starter_catalog_is_valid_and_matches_the_built_in_apps(self) -> None:
        path = Path(__file__).parents[1] / "docs" / "catalogs" / "artcraft.toml"
        catalog = read(path)
        self.assertEqual((catalog.skipped, catalog.ignored), ((), ()))
        self.assertEqual(
            {entry.repo: (entry.name, entry.description) for entry in catalog.entries},
            {app.repo: (app.name, app.description) for app in APPS},
        )


class ReadTests(unittest.TestCase):
    def test_read_file_and_errors(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "list.toml"
            path.write_text('format = 1\n[[app]]\nrepo = "owner/app"\n')
            self.assertEqual(read(path).entries[0].repo, "owner/app")
            with self.assertRaisesRegex(CatalogError, "Cannot read"):
                read(Path(folder) / "missing.toml")
            path.write_bytes(b"format = 1\n#" + b"x" * MAX_BYTES)
            with self.assertRaisesRegex(CatalogError, "256 KiB"):
                read(path)


if __name__ == "__main__":
    unittest.main()
