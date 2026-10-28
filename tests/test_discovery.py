"""Discover real release assets without assuming repository-based filenames."""

import json
import tempfile
import time
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from threading import Event
from unittest.mock import patch
from urllib.parse import quote

from catalog_fixture import APPS
from test_library import APP, appimage_bytes, release_for
from test_releases import Response, public_assets

from appglider.core.catalog import custom_app
from appglider.core.storage import atomic_json
from appglider.github import assets, download, http, pages, versions


class DiscoveryTests(unittest.TestCase):
    def asset(self, name, app=APP, tag="v1.2.3"):
        prefix = f"{app.source_url}/releases/download/{tag}/"
        return {
            "name": name,
            "browser_download_url": prefix + quote(name, safe=""),
            "size": 100,
            "digest": "sha256:" + "a" * 64,
        }

    def select(self, names, arch="x86_64"):
        return assets.select_release(
            APP, {"tag_name": "v1.2.3", "assets": [self.asset(name) for name in names]}, arch
        )

    def test_generic_names_extensions_separators_and_architecture_aliases(self):
        for name in (
            "UltiMaker-Cura-1.2.3-linux-X64.AppImage",
            "Vendor_Tool_amd64_2026.APPIMAGE",
            "Branded Desktop x86-64.AppImage",
            "Branded-x86_64.AppImage",
            "Desktop.AppImage",
            "Unlabelled-64bit.AppImage",
        ):
            with self.subTest(name=name):
                self.assertEqual(self.select([name]).name, name)
        self.assertEqual(
            self.select(["Vendor_ARM64.AppImage"], "aarch64").name, "Vendor_ARM64.AppImage"
        )

    def test_wrong_architectures_and_companion_files_are_excluded(self):
        for name in (
            "Vendor-ARM64.AppImage",
            "Vendor-aarch64.AppImage",
            "Vendor-i686.AppImage",
            "Vendor-x86.AppImage",
            "Vendor-armv7l.AppImage",
            "Vendor-riscv64.AppImage",
            "Vendor-ppc64le.AppImage",
            "Vendor-s390x.AppImage",
            "Vendor-x64-arm64.AppImage",
            "Vendor-x64.AppImage.zsync",
            "Vendor-x64.AppImage.asc",
            "Vendor-x64.AppImage.sha256",
            "Vendor-x64.exe",
        ):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "No supported"):
                self.select([name])

    def test_cpu_labels_use_boundaries_and_explicit_host_assets_are_preferred(self):
        self.assertEqual(self.select(["armchair.AppImage"]).name, "armchair.AppImage")
        self.assertEqual(
            self.select(["Vendor.AppImage", "Vendor-arm64.AppImage", "Other_AMD64.AppImage"]).name,
            "Other_AMD64.AppImage",
        )
        self.assertEqual(
            self.select(["Vendor.AppImage", "Vendor-arm64.AppImage"]).name, "Vendor.AppImage"
        )

    def test_ambiguous_candidates_do_not_depend_on_asset_order(self):
        for names in (
            ["Desktop-x64.AppImage", "Desktop-debug-x64.AppImage"],
            ["Desktop.AppImage", "Other.AppImage"],
            ["photocraft-1.2.3-x86_64.AppImage", "PhotoCraft-1.2.3-x86_64.AppImage"],
        ):
            for order in (names, names[::-1]):
                with (
                    self.subTest(names=order),
                    self.assertRaisesRegex(ValueError, "Multiple AppImages"),
                ):
                    self.select(order)

    def test_builtin_and_former_names_keep_the_existing_preference(self):
        for app in APPS:
            stem = {"pdfcraft": "printcraft", "solidcraft": "solvecraft"}.get(app.id, app.id)
            name = f"{stem}-1.2.3-linux-x86_64.AppImage"
            with self.subTest(app=app.id):
                selected = assets.select_release(
                    app,
                    {
                        "tag_name": "v1.2.3",
                        "assets": [self.asset("Other-x64.AppImage", app), self.asset(name, app)],
                    },
                    "x86_64",
                )
                self.assertEqual(selected.name, name)

    def test_unsafe_names_urls_and_invalid_sizes_are_rejected(self):
        for name in ("../Desktop.AppImage", "Desktop\\x64.AppImage", "Desktop\n.AppImage"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "filename"):
                self.select([name])
        asset = self.asset("Desktop x64.AppImage")
        for url in (
            asset["browser_download_url"] + "?other=1",
            asset["browser_download_url"] + "#fragment",
            asset["browser_download_url"].replace(APP.repo, "someone/else"),
            asset["browser_download_url"].replace("v1.2.3", "v9.9.9"),
            asset["browser_download_url"].replace("Desktop", "desktop"),
        ):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "repository"):
                assets.select_release(
                    APP,
                    {"tag_name": "v1.2.3", "assets": [{**asset, "browser_download_url": url}]},
                    "x86_64",
                )
        for size in (False, 0, -1, assets.MAX_DOWNLOAD + 1):
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, "size"):
                assets.select_release(
                    APP, {"tag_name": "v1.2.3", "assets": [{**asset, "size": size}]}, "x86_64"
                )

    def test_cura_api_public_fallback_and_cache_agree(self):
        app = custom_app("https://github.com/Ultimaker/Cura")
        name = "UltiMaker-Cura-5.13.0-linux-X64.AppImage"
        asset = self.asset(name, app, "5.13.0")
        asset["browser_download_url"] = asset["browser_download_url"].replace(
            "ultimaker/cura", "Ultimaker/Cura"
        )
        asset["size"] = 334154232
        asset["digest"] = "sha256:100f068127b2598167f00ba4db0e0699ded45adf97bbab2d22ff171a1e1ecc40"
        expected = assets.Release(
            "5.13.0", name, asset["browser_download_url"], asset["size"], asset["digest"][7:]
        )
        html = (Path(__file__).parent / "fixtures/cura-release-assets.html").read_bytes()
        with patch.object(versions, "architecture", return_value="x86_64"):
            with patch.object(
                http,
                "read_metadata",
                return_value=json.dumps(
                    {
                        "tag_name": "5.13.0",
                        "assets": [self.asset(name + ".asc", app, "5.13.0"), asset],
                    }
                ).encode(),
            ):
                self.assertEqual(assets.api_release(app, Event()), expected)
            with tempfile.TemporaryDirectory() as directory:
                cache = Path(directory)
                with (
                    patch.object(
                        assets,
                        "api_release",
                        side_effect=http.RateLimited("api.github.com", time.time() + 60),
                    ),
                    patch.object(http, "read_metadata", return_value=html) as metadata,
                    patch.object(
                        http,
                        "request",
                        side_effect=[
                            Response(f"{app.source_url}/releases/tag/5.13.0"),
                            Response(expected.url, {"Content-Length": str(expected.size)}),
                        ],
                    ) as request,
                ):
                    found = download.latest_release(app, Event(), cache)
                    self.assertEqual(found, expected)
                    metadata.assert_called_once_with(
                        f"{app.source_url}/releases/expanded_assets/5.13.0"
                    )
                    request.assert_called_with(expected.url, method="HEAD")
                self.assertEqual(download.cached_release(app, cache), found)
                atomic_json(
                    cache / f"{app.id}-x86_64.json",
                    {"checked_at": time.time(), "release": asdict(expected)},
                )
                self.assertEqual(download.cached_release(app, cache), expected)

    def test_public_digest_must_name_the_selected_asset(self):
        release = release_for("1.2.3", appimage_bytes())
        html = (
            public_assets(release, False)
            + (
                f'<clipboard-copy aria-label="Copy to clipboard digest for {release.name}.asc" '
                f'value="sha256:{release.sha256}"></clipboard-copy>'
            ).encode()
        )
        with (
            patch.object(
                http, "request", return_value=Response(f"{APP.source_url}/releases/tag/v1.2.3")
            ),
            patch.object(http, "read_metadata", side_effect=[html, http.MissingResource("404")]),
            self.assertRaisesRegex(ValueError, "No GitHub SHA-256 digest"),
        ):
            pages.page_release(APP, Event())

    def test_public_assets_preserve_escaped_names_and_download_urls(self):
        base = release_for("1.2.3", appimage_bytes())
        name = "Desktop & Design x64.APPIMAGE"
        release = replace(
            base, name=name, url=base.url.rsplit("/", 1)[0] + "/" + quote(name, safe="")
        )
        with (
            patch.object(versions, "architecture", return_value="x86_64"),
            patch.object(http, "read_metadata", return_value=public_assets(release)),
            patch.object(
                http,
                "request",
                side_effect=[
                    Response(f"{APP.source_url}/releases/tag/v1.2.3"),
                    Response(release.url, {"Content-Length": str(release.size)}),
                ],
            ),
        ):
            self.assertEqual(pages.page_release(APP, Event()), release)

    def test_public_and_api_report_ambiguity_consistently(self):
        base = release_for("1.2.3", appimage_bytes())
        listing = [self.asset(name) for name in ("Desktop-x64.AppImage", "Portable-x64.AppImage")]
        html = b"".join(
            public_assets(replace(base, name=asset["name"], url=asset["browser_download_url"]))
            for asset in listing
        )
        with (
            patch.object(versions, "architecture", return_value="x86_64"),
            patch.object(
                http,
                "read_metadata",
                return_value=json.dumps({"tag_name": "v1.2.3", "assets": listing}).encode(),
            ),
            self.assertRaisesRegex(ValueError, "Multiple AppImages"),
        ):
            assets.api_release(APP, Event())
        with (
            patch.object(versions, "architecture", return_value="x86_64"),
            patch.object(http, "read_metadata", return_value=html),
            patch.object(
                http, "request", return_value=Response(f"{APP.source_url}/releases/tag/v1.2.3")
            ) as request,
            self.assertRaisesRegex(ValueError, "Multiple AppImages"),
        ):
            pages.page_release(APP, Event())
        request.assert_called_once_with(f"{APP.source_url}/releases/latest")

    def test_public_parser_rejects_conflicting_digests_and_ignores_foreign_links(self):
        release = release_for("1.2.3", appimage_bytes())
        parser = pages.ReleaseAssetsParser(release.url.rsplit("/", 1)[0] + "/")
        parser.feed(public_assets(release).decode())
        self.assertEqual(parser.digests[release.name], "sha256:" + release.sha256)
        with self.assertRaisesRegex(ValueError, "conflicting SHA-256"):
            parser.feed(public_assets(replace(release, sha256="0" * 64)).decode())
        parser = pages.ReleaseAssetsParser(release.url.rsplit("/", 1)[0] + "/")
        parser.feed(
            public_assets(
                replace(release, url=release.url.replace(APP.repo, "other/repo"))
            ).decode()
        )
        self.assertEqual(parser.assets, {})

    def test_changed_public_markup_fails_without_guessing(self):
        with (
            patch.object(
                http, "request", return_value=Response(f"{APP.source_url}/releases/tag/v1.2.3")
            ),
            patch.object(http, "read_metadata", return_value=b"<div>sha256:1234</div>"),
            self.assertRaisesRegex(ValueError, "public asset listing"),
        ):
            pages.page_release(APP, Event())
