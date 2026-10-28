"""Release preferences survive restarts, quota fallback and rollback without downgrades."""

import hashlib
import io
import json
import tempfile
import unittest
from dataclasses import asdict, replace
from html import escape
from pathlib import Path
from threading import Event
from unittest.mock import patch

from catalog_fixture import stock
from test_library import APP, appimage_bytes, ignore_progress
from test_releases import Response, public_assets

from appglider.core.catalog import custom_app
from appglider.github import assets, download, http, pages, versions
from appglider.install.library import Library


def metadata(version, prerelease=False, draft=False):
    name = f"Vendor-{version}-x64.AppImage"
    payload = appimage_bytes()
    return {
        "tag_name": "v" + version,
        "prerelease": prerelease,
        "draft": draft,
        "assets": [
            {
                "name": name,
                "browser_download_url": f"{APP.source_url}/releases/download/v{version}/{name}",
                "size": len(payload),
                "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
            }
        ],
    }


def release_list_html(entries, latest_tag=""):
    result = ""
    for entry in entries:
        tag = entry["tag_name"]
        label = (
            "Draft"
            if entry.get("draft")
            else "Pre-release"
            if entry.get("prerelease")
            else "Latest"
            if tag == latest_tag
            else ""
        )
        result += (
            f'<section data-release-anchor="release-{escape(tag)}">'
            f'<a href="{APP.source_url}/releases/tag/{escape(tag)}">Release {escape(tag)}</a>'
            f'<span class="Label Label--success">{label}</span>'
            '<div class="markdown-body"><span class="Label">Latest</span>'
            '<span class="Label">Pre-release</span></div></section>'
        )
    return result.encode()


class PrereleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = stock(Library(self.root / "data", self.root / "apps", self.root / "desktop"))
        self.app = replace(APP, include_prereleases=True)
        self.arch = patch.object(versions, "architecture", return_value="x86_64")
        self.arch.start()
        self.addCleanup(self.arch.stop)

    def api(self, app, stable, entries):
        def read(url):
            if url.endswith("/latest"):
                if stable is None:
                    raise http.MissingResource("404")
                return json.dumps(stable).encode()
            return json.dumps(entries).encode()

        with patch.object(http, "read_metadata", side_effect=read):
            return assets.api_release(app, Event())

    def test_semantic_prerelease_ordering_and_build_metadata(self):
        ordered = [
            "1.2.3-alpha.1",
            "1.2.3-beta.2",
            "1.2.3-beta.10",
            "1.2.3-rc.2",
            "1.2.3-rc.10",
            "1.2.3",
            "1.2.4-beta.1",
        ]
        self.assertEqual(sorted(reversed(ordered), key=versions.version_key), ordered)
        self.assertEqual(
            versions.version_key("1.2.3+build.2"), versions.version_key("1.2.3+build.10")
        )

    def test_stable_default_and_opted_in_selection(self):
        stable = metadata("1.2.3")
        entries = [
            metadata("1.3.0-beta.2", True),
            metadata("9.0.0", draft=True),
            metadata("1.3.0-beta.10", True),
            stable,
        ]
        self.assertEqual(self.api(APP, stable, entries).version, "1.2.3")
        selected = self.api(self.app, stable, entries)
        self.assertEqual(selected.version, "1.3.0-beta.10")
        self.assertTrue(selected.prerelease)
        self.assertFalse(selected.is_latest)
        stable = metadata("1.3.0")
        selected = self.api(self.app, stable, entries + [stable])
        self.assertEqual(selected.version, "1.3.0")
        self.assertTrue(selected.is_latest)
        self.assertTrue(selected.newer_than("1.3.0-beta.10"))

    def test_only_prereleases_explains_opt_in_and_never_accepts_drafts(self):
        entries = [metadata("1.0.0-beta.1", True)]
        with self.assertRaisesRegex(ValueError, "Only prereleases"):
            self.api(APP, None, entries)
        self.assertTrue(self.api(self.app, None, entries).prerelease)
        with self.assertRaisesRegex(ValueError, "No published release"):
            self.api(self.app, None, [metadata("9.0.0", draft=True)])

    def test_latest_badge_uses_designated_release_not_every_stable_version(self):
        selected = self.api(self.app, metadata("1.0.0"), [metadata("1.1.0")])
        self.assertEqual(selected.version, "1.1.0")
        self.assertFalse(selected.is_latest)

    def test_release_list_scan_is_bounded(self):
        def read(url):
            if url.endswith("/latest"):
                raise http.MissingResource("404")
            page = int(url.rsplit("=", 1)[1])
            return json.dumps(
                [metadata(f"1.0.0-beta.{page * 10 + i}", True) for i in range(30)]
            ).encode()

        with patch.object(http, "read_metadata", side_effect=read) as reader:
            self.assertEqual(assets.api_release(self.app, Event()).version, "1.0.0-beta.39")
            self.assertEqual(reader.call_count, 2)

    def test_api_and_public_prerelease_selection_agree(self):
        stable = metadata("1.2.3")
        beta = metadata("1.3.0-beta.10", True)
        entries = [metadata("1.3.0-beta.2", True), stable, beta]
        expected = self.api(self.app, stable, entries)
        with (
            patch.object(
                http,
                "request",
                side_effect=[
                    Response(f"{APP.source_url}/releases/tag/v1.2.3"),
                    Response(expected.url, {"Content-Length": str(expected.size)}),
                ],
            ),
            patch.object(
                http,
                "read_metadata",
                side_effect=[release_list_html(entries, "v1.2.3"), public_assets(expected)],
            ),
        ):
            self.assertEqual(pages.page_release(self.app, Event()), expected)

    def test_public_only_prereleases_and_quota_fallback(self):
        beta = metadata("1.0.0-beta.1", True)
        expected = self.api(self.app, None, [beta])
        for app in (APP, self.app):
            with (
                self.subTest(include=app.include_prereleases),
                patch.object(
                    http,
                    "request",
                    side_effect=[
                        http.MissingResource("404"),
                        Response(expected.url, {"Content-Length": str(expected.size)}),
                    ],
                ),
                patch.object(
                    http,
                    "read_metadata",
                    side_effect=[release_list_html([beta]), public_assets(expected)],
                ),
            ):
                if app.include_prereleases:
                    with patch.object(
                        assets,
                        "api_release",
                        side_effect=http.RateLimited("api.github.com", 100),
                    ):
                        self.assertEqual(download.latest_release(app, Event()), expected)
                else:
                    with self.assertRaisesRegex(ValueError, "Only prereleases"):
                        pages.page_release(app, Event())

    def test_public_listing_reads_additional_pages_and_requires_owned_release_links(self):
        stable = metadata("1.2.3")
        beta = metadata("1.3.0-beta.10", True)
        expected = self.api(self.app, stable, [beta])
        first = release_list_html([stable], "v1.2.3") + b'<a rel="next" href="?page=2">Next</a>'
        with (
            patch.object(
                http,
                "request",
                side_effect=[
                    Response(f"{APP.source_url}/releases/tag/v1.2.3"),
                    Response(expected.url, {"Content-Length": str(expected.size)}),
                ],
            ),
            patch.object(
                http,
                "read_metadata",
                side_effect=[
                    first,
                    release_list_html([beta]),
                    public_assets(expected),
                ],
            ) as reader,
        ):
            self.assertEqual(pages.page_release(self.app, Event()), expected)
            self.assertEqual(reader.call_args_list[1].args[0], APP.source_url + "/releases?page=2")
        parser = pages.ReleaseListParser(APP)
        parser.feed(release_list_html([beta]).decode().replace(APP.repo, "other/repository"))
        self.assertEqual(parser.entries, [])

    def test_build_metadata_is_supported_and_installation_enforces_opt_in(self):
        beta = self.api(self.app, None, [metadata("1.3.0-beta.1+build.4", True)])
        self.assertEqual(beta.version, "1.3.0-beta.1+build.4")
        with self.assertRaisesRegex(ValueError, "Enable prereleases"):
            self.library.install(APP, beta, Event(), ignore_progress)
        self.assertEqual(self.library.scan(), {})
        self.assertFalse(self.library.apps_dir.exists())

    def test_prefixed_short_versions_install_reopen_update_and_roll_back(self):
        for version in ("0.6", "0.7"):
            data = metadata(version)
            data["tag_name"] = "studio-v" + version
            data["assets"][0]["browser_download_url"] = data["assets"][0][
                "browser_download_url"
            ].replace("/v" + version + "/", "/studio-v" + version + "/")
            release = self.api(APP, data, [])
            with patch.object(http, "request", return_value=io.BytesIO(appimage_bytes())):
                self.library.install(APP, release, Event(), ignore_progress)
            reopened = Library(
                self.library.data_dir, self.library.apps_dir, self.library.desktop_dir
            )
            self.assertEqual(reopened.scan()[APP.id].version, version)
        self.library.rollback(APP)
        self.assertEqual(self.library.scan()[APP.id].version, "0.6")

    def test_channel_caches_are_separate_and_legacy_stable_cache_still_reads(self):
        cache = self.root / "cache"
        stable = self.api(APP, metadata("1.2.3"), [])
        beta = self.api(self.app, None, [metadata("1.3.0-beta.1", True)])
        for app, result in ((APP, stable), (self.app, beta)):
            with patch.object(assets, "api_release", return_value=result):
                self.assertEqual(download.latest_release(app, Event(), cache), result)
        self.assertNotEqual(
            download.release_cache_path(APP, cache), download.release_cache_path(self.app, cache)
        )
        self.assertEqual(download.cached_release(APP, cache), stable)
        self.assertEqual(download.cached_release(self.app, cache), beta)
        path = download.release_cache_path(APP, cache)
        data = json.loads(path.read_text())
        for key in ("prerelease", "is_latest"):
            data["release"].pop(key)
        path.write_text(json.dumps(data))
        self.assertEqual(download.cached_release(APP, cache), stable)
        data["release"] = asdict(beta)
        path.write_text(json.dumps(data))
        self.assertIsNone(download.cached_release(APP, cache))

    def test_preferences_for_builtin_and_custom_apps_preserve_existing_files(self):
        custom = custom_app("owner/tool")
        self.library.add_repository(custom)
        path = self.library.data_dir / "repositories.json"
        contents = path.read_bytes()
        for app in (APP, custom):
            self.library.set_release_preference(app.id, True)
        self.assertEqual(path.read_bytes(), contents)
        reopened = Library(self.library.data_dir, self.library.apps_dir)
        apps = {app.id: app for app in reopened.apps}
        self.assertTrue(apps[APP.id].include_prereleases)
        self.assertTrue(apps[custom.id].include_prereleases)
        self.library.remove_repository(custom.id)
        self.library.add_repository(custom)
        self.assertFalse(
            next(app for app in self.library.apps if app.id == custom.id).include_prereleases
        )

    def test_bad_preferences_are_preserved_and_rejected(self):
        path = self.library.data_dir / "release-preferences.json"
        path.parent.mkdir(exist_ok=True)
        contents = json.dumps({APP.id: "false"}).encode()
        path.write_bytes(contents)
        with self.assertRaisesRegex(ValueError, "preserved"):
            self.library.apps
        self.assertEqual(path.read_bytes(), contents)

    def test_install_stable_after_beta_rollback_flags_and_no_silent_downgrade(self):
        self.library.set_release_preference(APP.id, True)
        app = next(app for app in self.library.apps if app.id == APP.id)
        beta = self.api(app, None, [metadata("1.3.0-beta.1", True)])
        stable = self.api(app, metadata("1.3.0"), [])

        def install(release):
            with patch.object(http, "request", return_value=io.BytesIO(appimage_bytes())):
                return self.library.install(app, release, Event(), ignore_progress)

        self.assertTrue(install(beta).prerelease)
        self.assertFalse(install(stable).prerelease)
        self.library.rollback(app)
        self.assertTrue(self.library.scan()[APP.id].prerelease)
        install(stable)
        before = (self.library.data_dir / "library.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "older than"):
            install(beta)
        self.assertEqual((self.library.data_dir / "library.json").read_bytes(), before)
