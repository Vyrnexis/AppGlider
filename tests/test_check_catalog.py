"""The community catalog check (packaging/check_catalog.py), with GitHub replaced."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from appglider.core.catalog import custom_app
from appglider.core.catalogfile import parse
from appglider.github import http
from appglider.github.assets import BuildChoiceRequired, Release
from appglider.github.release_info import ReleaseInfo, ReleaseOption

ROOT = Path(__file__).parents[1]
_spec = importlib.util.spec_from_file_location(
    "check_catalog", ROOT / "packaging" / "check_catalog.py"
)
assert _spec is not None and _spec.loader is not None
check_catalog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_catalog)
COMMUNITY = ROOT / "docs" / "catalogs" / "community.toml"


def release() -> Release:
    return Release("1.0", "App.AppImage", "https://example.invalid/a", 100, "0" * 64)


def info(option: ReleaseOption | None) -> ReleaseInfo:
    return ReleaseInfo(option, None, 0.0)


def catalog(*repos: str) -> str:
    return 'format = 1\nname = "t"\n' + "".join(f'[[app]]\nrepo = "{r}"\n' for r in repos)


class CheckTests(unittest.TestCase):
    def run_check(self, text: str, discover) -> tuple[int, list[str]]:
        lines: list[str] = []
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "c.toml"
            path.write_text(text)
            with patch.object(check_catalog, "discover_releases", discover):
                code = check_catalog.main([str(path)], lines.append)
        return code, lines

    def test_good_catalog_passes(self) -> None:
        code, lines = self.run_check(
            catalog("a/one", "b/two"), lambda *a, **k: info(ReleaseOption("1.0", release()))
        )
        self.assertEqual(code, 0)
        self.assertEqual(lines[-1], "2 passed, 0 with problems.")

    def test_skipped_and_duplicate_entries_fail(self) -> None:
        text = catalog("a/one", "A/One", "not a repo")
        code, lines = self.run_check(text, lambda *a, **k: info(ReleaseOption("1.0", release())))
        self.assertEqual(code, 1)
        self.assertTrue(any("listed more than once" in line for line in lines))
        self.assertEqual(sum("PROBLEM" in line for line in lines), 2)

    def test_unparsable_file_fails(self) -> None:
        code, lines = self.run_check("format = 7", lambda *a, **k: self.fail("no network"))
        self.assertEqual(code, 1)
        self.assertTrue(any("cannot be used" in line for line in lines))

    def test_repository_in_the_example_catalog_fails(self) -> None:
        repo = check_catalog.EXAMPLE.read_text().split('repo = "')[1].split('"')[0]
        code, lines = self.run_check(
            catalog(repo.upper()), lambda *a, **k: self.fail("no network needed")
        )
        self.assertEqual(code, 1)
        self.assertTrue(any("built-in example catalog" in line for line in lines))

    def test_moved_repository_names_the_new_address(self) -> None:
        def moved(*a, **k):
            raise http.RepositoryMoved("a/one", "new/home")

        code, lines = self.run_check(catalog("a/one"), moved)
        self.assertEqual(code, 1)
        self.assertTrue(any("new/home" in line for line in lines))

    def test_missing_appimage_fails(self) -> None:
        code, lines = self.run_check(
            catalog("a/one"),
            lambda *a, **k: info(ReleaseOption("1.0", None, "No x86_64 AppImage was found.")),
        )
        self.assertEqual(code, 1)
        self.assertTrue(any("No x86_64 AppImage" in line for line in lines))

    def test_no_release_fails(self) -> None:
        code, _ = self.run_check(catalog("a/one"), lambda *a, **k: info(None))
        self.assertEqual(code, 1)

    def test_build_choice_asks_for_build(self) -> None:
        app = custom_app("a/one")

        def ambiguous(*a, **k):
            raise BuildChoiceRequired(app, [], "1.0", "x86_64")

        code, lines = self.run_check(catalog("a/one"), ambiguous)
        self.assertEqual(code, 1)
        self.assertTrue(any("build =" in line for line in lines))

        code, lines = self.run_check(
            catalog("a/one"), lambda *a, **k: info(ReleaseOption("1.0", None, "many", True))
        )
        self.assertEqual(code, 1)
        self.assertTrue(any("build =" in line for line in lines))

    def test_rate_limit_is_a_distinct_failure(self) -> None:
        def limited(*a, **k):
            raise http.RateLimited("api.github.com", 0)

        code, lines = self.run_check(catalog("a/one"), limited)
        self.assertEqual(code, check_catalog.EXIT_LIMITED)
        self.assertNotEqual(code, 1)
        self.assertTrue(any("again later" in line for line in lines))

    def test_no_arguments_fails(self) -> None:
        self.assertEqual(check_catalog.main([], lambda text: None), 1)


class CommunityCatalogTests(unittest.TestCase):
    def test_parses_without_skipped_entries(self) -> None:
        parsed = parse(COMMUNITY.read_bytes())
        self.assertEqual(parsed.skipped, ())
        self.assertEqual(parsed.ignored, ())
        self.assertTrue(parsed.entries)

    def test_is_in_alphabetical_order_by_name(self) -> None:
        names = [entry.name for entry in parse(COMMUNITY.read_bytes()).entries]
        self.assertEqual(names, sorted(names, key=str.casefold))

    def test_does_not_repeat_the_example_catalog(self) -> None:
        listed = {entry.repo.casefold() for entry in parse(COMMUNITY.read_bytes()).entries}
        self.assertFalse(listed & check_catalog.example_repos())


class TokenTests(unittest.TestCase):
    def test_a_token_goes_only_to_the_api(self) -> None:
        from urllib.request import Request

        seen = {}

        def fake_open(req, host):
            seen[host] = req.get_header("Authorization")

        with patch.object(check_catalog.http, "_open", fake_open):
            check_catalog.use_token("secret")
            check_catalog.http._open(Request("https://api.github.com/x"), "api.github.com")
            check_catalog.http._open(Request("https://github.com/x"), "github.com")
        self.assertEqual(seen, {"api.github.com": "Bearer secret", "github.com": None})


if __name__ == "__main__":
    unittest.main()
