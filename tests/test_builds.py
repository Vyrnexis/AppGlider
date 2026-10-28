"""Remember explicit AppImage variants across release channels without guessing compatibility."""

import hashlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import patch

from catalog_fixture import APPS, stock
from test_library import appimage_bytes, ignore_progress
from test_releases import Response, public_assets

from appglider.core.catalog import custom_app
from appglider.github import assets, download, http, pages, versions
from appglider.github.release_info import cached_info, discover_releases, info_path
from appglider.install.library import Library

BAMBU = custom_app("bambulab/BambuStudio", "Bambu Studio")


def bambu_metadata(
    version="02.08.02.61", *, preview=False, variants=("22.04", "24.04"), timestamp="20260820225108"
):
    payload = appimage_bytes()
    names = [f"BambuStudio_ubuntu{variant}-v{version}-{timestamp}.AppImage" for variant in variants]
    return {
        "tag_name": f"v{version}",
        "prerelease": preview,
        "is_latest": not preview,
        "assets": [
            {
                "name": name,
                "browser_download_url": f"{BAMBU.source_url}/releases/download/v{version}/{name}",
                "size": len(payload),
                "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
            }
            for name in names
        ],
    }


def chosen_bambu(data=None, index=1):
    data = data or bambu_metadata()
    pattern = assets.asset_variant(
        data["assets"][index]["name"], data["tag_name"].removeprefix("v")
    )
    return replace(BAMBU, build_variant=pattern)


class BuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / "cache"
        self.library = stock(Library(self.root / "data", self.root / "apps", self.root / "desktop"))
        arch = patch.object(versions, "architecture", return_value="x86_64")
        arch.start()
        self.addCleanup(arch.stop)

    def test_four_part_versions_preserve_numeric_and_preview_ordering(self):
        ordered = [
            "02.08.02.9",
            "2.8.2.10-beta.2",
            "2.8.2.10-beta.10",
            "02.08.02.10",
            "02.08.02.61",
            "2.8.3",
            "02.09.00.12",
        ]
        self.assertEqual(sorted(reversed(ordered), key=versions.version_key), ordered)
        self.assertEqual(versions.version_key("2.8.2"), versions.version_key("02.08.02.0"))
        app = replace(chosen_bambu(), include_prereleases=True)
        stable = bambu_metadata()
        preview = bambu_metadata("02.09.00.12", preview=True)
        for include, version in ((False, "02.08.02.61"), (True, "02.09.00.12")):
            entry = assets.newest_release([preview, stable], include)
            selected = assets.select_release(
                replace(app, include_prereleases=include), entry, "x86_64"
            )
            self.assertEqual(selected.version, version)
            self.assertEqual(selected.prerelease, include)
            self.assertIn("ubuntu24.04", selected.name)
        newer_stable = bambu_metadata("02.09.00.13")
        entry = assets.newest_release([preview, newer_stable, stable], True)
        selected = assets.select_release(app, entry, "x86_64")
        self.assertFalse(selected.prerelease)
        self.assertTrue(selected.newer_than(preview["tag_name"].removeprefix("v")))

    def test_multiple_candidates_require_explicit_choice_and_keep_cpu_filtering(self):
        data = bambu_metadata()
        foreign = {**data["assets"][0], "name": "BambuStudio-arm64.AppImage"}
        with self.assertRaises(assets.BuildChoiceRequired) as failure:
            assets.select_asset(BAMBU, [foreign, *data["assets"]], "02.08.02.61", "x86_64")
        choices = failure.exception.choices
        self.assertEqual(len(choices), 2)
        self.assertTrue(all("arm64" not in name for _, name in choices))
        for pattern, name in choices:
            selected = assets.select_release(replace(BAMBU, build_variant=pattern), data, "x86_64")
            self.assertEqual(selected.name, name)
        single = bambu_metadata(variants=("22.04",))
        self.assertEqual(
            assets.select_release(BAMBU, single, "x86_64").name, single["assets"][0]["name"]
        )

    def test_saved_variant_survives_version_and_timestamp_changes_but_never_switches(self):
        app = chosen_bambu()
        newer = bambu_metadata("02.09.00.12", timestamp="20261025123456")
        self.assertIn("ubuntu24.04", assets.select_release(app, newer, "x86_64").name)
        missing = bambu_metadata("02.09.00.12", variants=("22.04",))
        with self.assertRaisesRegex(
            assets.BuildChoiceRequired, "saved build is unavailable"
        ) as failure:
            assets.select_release(app, missing, "x86_64")
        self.assertEqual(len(failure.exception.choices), 1)
        self.assertIn("22.04", failure.exception.choices[0][1])

    def test_colliding_build_dates_remain_selectable_without_arbitrary_matching(self):
        data = bambu_metadata(variants=("24.04",))
        data["assets"] += bambu_metadata(variants=("24.04",), timestamp="20260821120000")["assets"]
        with self.assertRaises(assets.BuildChoiceRequired) as failure:
            assets.select_release(BAMBU, data, "x86_64")
        choices = failure.exception.choices
        self.assertNotEqual(choices[0][0], choices[1][0])
        for pattern, filename in choices:
            selected = assets.select_release(replace(BAMBU, build_variant=pattern), data, "x86_64")
            self.assertEqual(selected.name, filename)

    def test_case_distinct_choices_stay_separate_in_selection_and_caches(self):
        data = bambu_metadata(variants=("24.04",))
        original = data["assets"][0]
        renamed = original["name"].swapcase()
        data["assets"].append(
            {
                **original,
                "name": renamed,
                "browser_download_url": original["browser_download_url"].rsplit("/", 1)[0]
                + "/"
                + renamed,
            }
        )
        with self.assertRaises(assets.BuildChoiceRequired) as failure:
            assets.select_release(BAMBU, data, "x86_64")
        apps = [replace(BAMBU, build_variant=pattern) for pattern, _ in failure.exception.choices]
        self.assertNotEqual(
            download.release_cache_path(apps[0], self.cache),
            download.release_cache_path(apps[1], self.cache),
        )
        for app, (_, filename) in zip(apps, failure.exception.choices, strict=True):
            selected = assets.select_release(app, data, "x86_64")
            self.assertEqual(selected.name, filename)

    def test_api_public_fallback_and_cache_agree_on_selected_build(self):
        data = bambu_metadata()
        app = chosen_bambu(data)
        expected = assets.select_release(app, data, "x86_64")
        public = b"".join(
            public_assets(assets.select_release(chosen_bambu(data, index), data, "x86_64"))
            for index in range(len(data["assets"]))
        )
        with patch.object(http, "read_metadata", return_value=json.dumps(data).encode()):
            self.assertEqual(download.latest_release(app, Event(), self.cache), expected)
        other = chosen_bambu(data, 0)
        with patch.object(http, "read_metadata", return_value=json.dumps(data).encode()):
            self.assertIn("ubuntu22.04", download.latest_release(other, Event(), self.cache).name)
        self.assertNotEqual(
            download.release_cache_path(app, self.cache),
            download.release_cache_path(other, self.cache),
        )
        self.assertEqual(download.cached_release(app, self.cache), expected)
        with (
            patch.object(
                http,
                "request",
                side_effect=[
                    Response(BAMBU.source_url + "/releases/tag/" + data["tag_name"]),
                    Response(expected.url, {"Content-Length": str(expected.size)}),
                ],
            ) as request,
            patch.object(http, "read_metadata", return_value=public),
        ):
            self.assertEqual(pages.page_release(app, Event()), expected)
            request.assert_called_with(expected.url, method="HEAD")

    def test_discovery_retains_both_channels_and_caches_missing_build_attention(self):
        app = chosen_bambu()
        entries = [bambu_metadata(), bambu_metadata("02.09.00.12", preview=True)]
        with patch.object(assets, "api_entries", return_value=entries):
            info = discover_releases(app, Event(), self.cache)
            self.assertIn("ubuntu24.04", info.choice(False).release.name)
            self.assertEqual(info.choice(True).version, "02.09.00.12")
            other = chosen_bambu(index=0)
            other_info = discover_releases(other, Event(), self.cache)
            self.assertIn("ubuntu22.04", other_info.stable.release.name)
        self.assertNotEqual(info_path(app, self.cache), info_path(other, self.cache))
        self.assertEqual(cached_info(app, self.cache), info)
        with patch.object(
            assets,
            "api_entries",
            return_value=[bambu_metadata("02.10.00.0", variants=("22.04",))],
        ):
            missing = discover_releases(app, Event(), self.cache, force=True)
        self.assertTrue(missing.stable.build_required)
        self.assertIsNone(missing.stable.release)
        self.assertEqual(cached_info(app, self.cache), missing)
        self.assertIsNone(download.cached_release(app, self.cache))

    def test_preferences_are_additive_persist_and_reject_corruption_without_data_loss(self):
        self.assertEqual(self.library.apps, APPS)
        self.assertFalse((self.library.data_dir / "build-preferences.json").exists())
        app = chosen_bambu()
        self.library.add_repository(app)
        repository_file = self.library.data_dir / "repositories.json"
        original = repository_file.read_bytes()
        self.library.set_release_preference(app.id, True)
        other = chosen_bambu(index=0)
        self.library.set_build_variant(app.id, other.build_variant)
        reopened = Library(self.library.data_dir, self.library.apps_dir)
        actual = next(known for known in reopened.apps if known.id == app.id)
        self.assertTrue(actual.include_prereleases)
        self.assertEqual(actual.build_variant, other.build_variant)
        self.assertEqual(repository_file.read_bytes(), original)
        path = self.library.data_dir / "build-preferences.json"
        before = path.read_bytes()
        with (
            patch("appglider.core.storage.os.replace", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            self.library.set_build_variant(app.id, app.build_variant)
        self.assertEqual(path.read_bytes(), before)
        for invalid in (False, "../bad.AppImage", "bad\n.AppImage", "not an AppImage"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.library.set_build_variant(app.id, invalid)
        path.write_text(json.dumps({app.id: False}))
        broken = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "preserved"):
            self.library.apps
        self.assertEqual(path.read_bytes(), broken)
        path.write_bytes(before)
        self.library.remove_repository(app.id)
        self.library.add_repository(BAMBU)
        self.assertFalse(self.library.apps[-1].build_variant)

    def test_renamed_preview_build_requires_confirmation_and_retains_both_channels(self):
        stable = bambu_metadata()
        preview = bambu_metadata("02.08.04.57", preview=True)
        for asset in preview["assets"]:
            for key in ("name", "browser_download_url"):
                asset[key] = (
                    asset[key].replace("ubuntu22.04", "ubu22").replace("ubuntu24.04", "ubu24")
                )
        with patch.object(assets, "api_entries", return_value=[stable, preview]):
            stable_choice = discover_releases(chosen_bambu(), Event(), self.cache)
            self.assertIsNotNone(stable_choice.choice(False).release)
            self.assertTrue(stable_choice.choice(True).build_required)
            with self.assertRaises(assets.BuildChoiceRequired) as failure:
                assets.select_release(replace(BAMBU, include_prereleases=True), preview, "x86_64")
            pattern, _ = failure.exception.choices[1]
            preview_choice = discover_releases(
                replace(BAMBU, include_prereleases=True, build_variant=pattern), Event(), self.cache
            )
            self.assertIn("ubu24", preview_choice.choice(True).release.name)
            self.assertTrue(preview_choice.stable.build_required)

    def test_four_part_install_update_and_rollback_keep_correct_build_and_records(self):
        app = replace(chosen_bambu(), include_prereleases=True)
        self.library.add_repository(app)
        payload = appimage_bytes()
        with patch.object(http, "request", return_value=io.BytesIO(payload)):
            first = self.library.install(
                app,
                assets.select_release(app, bambu_metadata(), "x86_64"),
                Event(),
                ignore_progress,
            )
        preview = assets.select_release(app, bambu_metadata("02.09.00.12", preview=True), "x86_64")
        with patch.object(http, "request", return_value=io.BytesIO(payload)):
            second = self.library.install(app, preview, Event(), ignore_progress)
        self.assertTrue(second.prerelease)
        self.assertEqual(second.previous, first.path)
        self.library.rollback(app)
        self.assertEqual(
            self.library.scan()[app.id],
            replace(first, previous=second.path, previous_prerelease=True),
        )
        self.assertIn("ubuntu24.04", first.path.name)
        self.library.remove(app)
        self.assertEqual(self.library.scan(), {})
