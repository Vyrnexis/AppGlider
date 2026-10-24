"""Rate-limit recovery keeps release checks bounded and downloads verified."""

import io
import json
import tempfile
import time
import unittest
from dataclasses import asdict, replace
from email.message import Message
from html import escape
from pathlib import Path
from threading import Event
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import quote

from test_library import APP, appimage_bytes, release_for

from appglider import releases
from appglider.catalog import custom_app
from appglider.storage import atomic_json


class Response(io.BytesIO):
    def __init__(self, url, headers=None):
        super().__init__()
        self.url = url
        self.headers = headers or {}

    def geturl(self):
        return self.url


def public_assets(release, digest=True):
    html = f'<li><a href="{escape(release.url, quote=True)}">{escape(release.name)}</a>'
    if digest:
        name = escape(release.name, quote=True)
        html += (
            f'<clipboard-copy aria-label="Copy to clipboard digest for {name}" '
            f'value="sha256:{release.sha256}"></clipboard-copy>'
        )
    return (html + "</li>").encode()


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)
        self.release = release_for("1.2.3", appimage_bytes())
        self.cache_file = self.cache / f"{APP.id}-{releases.architecture()}.json"
        cooldown = patch.dict(releases._RETRY_AFTER, {}, clear=True)
        cooldown.start()
        self.addCleanup(cooldown.stop)

    def assert_release_paths(self, app, data, release, arch):
        tag = quote(data["tag_name"], safe="")
        fallback = replace(
            release,
            url=f"{app.source_url}/releases/download/{tag}/{release.name}",
        )
        with patch.object(releases, "architecture", return_value=arch):
            self.assertEqual(releases.select_release(app, data, arch), release)
            with patch.object(releases, "read_metadata", return_value=json.dumps(data).encode()):
                self.assertEqual(releases.api_release(app, Event()), release)
            with (
                patch.object(
                    releases,
                    "request",
                    side_effect=[
                        Response(f"{app.source_url}/releases/tag/{data['tag_name']}"),
                        Response(fallback.url, {"Content-Length": str(release.size)}),
                    ],
                ) as request,
                patch.object(releases, "read_metadata", return_value=public_assets(fallback)),
            ):
                self.assertEqual(releases.page_release(app, Event()), fallback)
                request.assert_called_with(fallback.url, method="HEAD")
            atomic_json(
                self.cache / f"{app.id}-{arch}.json",
                {"checked_at": time.time(), "release": asdict(release)},
            )
            self.assertEqual(releases.cached_release(app, self.cache), release)

    def test_tag_versions_accept_unambiguous_prefixes_and_short_versions(self):
        for tag, version in (
            ("studio-v0.6.0", "0.6.0"),
            ("release-1.2.3", "1.2.3"),
            ("app/v1.2.3-beta.10+build.4", "1.2.3-beta.10+build.4"),
            ("v0.6", "0.6"),
            ("V1.2.3", "1.2.3"),
            ("v02.08.02.61", "02.08.02.61"),
        ):
            with self.subTest(tag=tag):
                self.assertEqual(releases.release_version(tag), version)
        for tag in (
            None,
            123,
            "nightly",
            "v1",
            "1.2.3.4.5",
            "1.2.3/v2.3.4",
            "v1.2.3 trailing",
            "../v1.2.3",
            "v1.2.3\n",
            "v1.2.3-../../bad",
            "x" * 129 + "-v1.2.3",
        ):
            with self.subTest(tag=tag):
                self.assertIsNone(releases.release_version(tag))
        self.assertEqual(releases.version_key("0.6"), releases.version_key("0.6.0"))
        self.assertGreater(releases.version_key("0.10"), releases.version_key("0.9.9"))

    def test_network_failures_are_friendly_and_transient_ones_are_retried(self):
        unavailable = HTTPError("https://api.github.com/", 503, "Down", Message(), io.BytesIO())
        with (
            patch.object(releases.time, "sleep") as sleep,
            patch.object(releases, "build_opener") as opener,
        ):
            opener.return_value.open.side_effect = [unavailable, TimeoutError(), Response("ok")]
            self.assertEqual(releases.request("https://api.github.com/").url, "ok")
            self.assertEqual(sleep.call_count, 2)
            opener.return_value.open.side_effect = [unavailable] * 3
            with self.assertRaisesRegex(ValueError, "temporarily unavailable.*503"):
                releases.request("https://api.github.com/")
            opener.return_value.open.side_effect = URLError(OSError("Name or service not known"))
            opener.return_value.open.reset_mock()
            with self.assertRaisesRegex(ValueError, "Could not reach GitHub"):
                releases.request("https://api.github.com/")
            opener.return_value.open.assert_called_once()
            opener.return_value.open.side_effect = ConnectionResetError()
            with self.assertRaisesRegex(ValueError, "Could not reach GitHub"):
                releases.read_metadata("https://api.github.com/")

    def test_interrupted_download_reports_a_plain_error(self):
        class Broken(io.BytesIO):
            def read(self, size=-1):
                raise ConnectionResetError()

        with patch.object(releases, "request", return_value=Broken()):
            with self.assertRaisesRegex(ValueError, "download was interrupted"):
                releases.download(
                    self.release, self.cache / "download", Event(), lambda *args: None
                )

    def test_api_etags_make_repeat_requests_conditional(self):
        url = "https://api.github.com/repos/owner/app/releases/latest"
        seen = []

        def fake_request(target, *, method="GET", etag=""):
            seen.append(etag)
            if etag:
                raise HTTPError(target, 304, "Not Modified", Message(), io.BytesIO())
            response = Response(target, {"ETag": '"abc"'})
            response.write(b'{"tag_name": "v1"}')
            response.seek(0)
            return response

        with (
            patch.object(releases, "request", fake_request),
            patch.object(releases, "_ETAG_FILE", self.cache / "etags.json"),
        ):
            first = releases.read_metadata(url)
            second = releases.read_metadata(url)
            self.assertEqual(first, second)
            self.assertEqual(seen, ["", '"abc"'])
            (self.cache / "etags.json").write_text("{broken")
            self.assertEqual(releases.read_metadata(url), first)
            self.assertEqual(seen[-1], "")

    def test_etag_cache_is_bounded_and_skips_other_hosts(self):
        with patch.object(releases, "_ETAG_FILE", self.cache / "etags.json"):
            for number in range(releases.ETAG_ENTRIES + 5):
                releases._remember_etag(f"https://api.github.com/{number}", '"e"', b"{}")
            releases._remember_etag("https://api.github.com/big", '"e"', b"x" * 600_000)
            entries = json.loads((self.cache / "etags.json").read_text())
        self.assertEqual(len(entries), releases.ETAG_ENTRIES)
        self.assertNotIn("https://api.github.com/0", entries)
        self.assertNotIn("https://api.github.com/big", entries)

    def test_download_rejects_a_mismatched_content_length_before_reading(self):
        response = Response("x", {"Content-Length": str(self.release.size + 1)})
        with patch.object(releases, "request", return_value=response):
            with self.assertRaisesRegex(ValueError, "did not match the published size"):
                releases.download(self.release, self.cache / "download", Event(), lambda *a: None)

    def test_download_progress_is_throttled_but_reports_completion(self):
        data = appimage_bytes(b"x" * (3 * releases.DOWNLOAD_CHUNK))
        release = release_for("1.2.3", data)
        response = Response("x", {"Content-Length": str(len(data))})
        response.write(data)
        response.seek(0)
        reports = []
        with (
            patch.object(releases, "request", return_value=response),
            patch.object(releases.time, "monotonic", return_value=1000.0),
        ):
            releases.download(
                release, self.cache / "download", Event(), lambda *args: reports.append(args[1])
            )
        self.assertEqual(reports[-1], len(data))
        self.assertLessEqual(len(reports), 2)

    def test_public_asset_links_match_slash_tags_in_either_form(self):
        app = custom_app("getcadcraft/studio")
        name = "CadCraft-Studio-0.6-x86_64.AppImage"
        prefix = f"{app.source_url}/releases/download/{quote('studio/v0.6.0', safe='')}/"
        for href in (prefix + name, f"{app.source_url}/releases/download/studio/v0.6.0/{name}"):
            with self.subTest(href=href):
                parser = releases.ReleaseAssetsParser(prefix)
                parser.feed(f'<a href="{href}">{name}</a>')
                self.assertEqual(list(parser.assets), [name])
        parser = releases.ReleaseAssetsParser(prefix)
        parser.feed(f'<a href="{app.source_url}/releases/download/studio/v9.9.9/{name}">x</a>')
        self.assertEqual(parser.assets, {})

    def test_cadcraft_prefixed_and_short_tags_agree_across_discovery_paths(self):
        app = custom_app("getcadcraft/studio")
        name = "CadCraft-Studio-0.6-x86_64.AppImage"
        sha256 = "0730125c546d3764e4171c445e6f163f6642116f2b2978e7943f232f34d5e12a"
        for tag, version in (
            ("studio-v0.6.0", "0.6.0"),
            ("studio-v0.6", "0.6"),
            ("studio/v0.6.0", "0.6.0"),
        ):
            with self.subTest(tag=tag):
                url = f"{app.source_url}/releases/download/{quote(tag, safe='')}/{name}"
                release = releases.Release(version, name, url, 106793464, sha256)
                data = {
                    "tag_name": tag,
                    "assets": [
                        {
                            "name": name,
                            "browser_download_url": url,
                            "size": release.size,
                            "digest": "sha256:" + sha256,
                        }
                    ],
                }
                self.assert_release_paths(app, data, release, "x86_64")
                without_digest = replace(release, sha256="")
                with patch.object(
                    releases, "read_metadata", return_value=f"{sha256} *{name}".encode()
                ) as read:
                    self.assertEqual(
                        releases.with_checksum(app, tag, without_digest, Event()), release
                    )
                    read.assert_called_once_with(
                        f"{app.source_url}/releases/download/{quote(tag, safe='')}/SHA256SUMS.txt"
                    )
                data["assets"][0]["browser_download_url"] = url.replace(
                    quote(tag, safe=""), version
                )
                with self.assertRaisesRegex(ValueError, "repository"):
                    releases.select_release(app, data, "x86_64")

    def test_unsupported_tags_report_an_appimage_separately_from_no_releases(self):
        data = {"tag_name": "nightly", "assets": [{"name": "Tool.AppImage"}]}
        with self.assertRaisesRegex(ValueError, "AppImage.*version tag"):
            releases.newest_release([data], False)
        with self.assertRaisesRegex(ValueError, "No published release"):
            releases.newest_release([], False)
        for assets in (None, "Tool.AppImage", [None, {"name": "Tool.AppImage.asc"}]):
            with (
                self.subTest(assets=assets),
                self.assertRaisesRegex(ValueError, "Published releases.*version tag"),
            ):
                releases.newest_release([{**data, "assets": assets}], False)

    def test_limit_headers_trigger_cooldown_without_repeated_requests(self):
        headers = Message()
        headers["X-RateLimit-Remaining"] = "0"
        headers["X-RateLimit-Reset"] = "1600"
        error = HTTPError("https://api.github.com/", 403, "Forbidden", headers, io.BytesIO())
        with (
            patch.object(releases.time, "time", return_value=1000),
            patch.object(releases, "build_opener") as opener,
        ):
            opener.return_value.open.side_effect = error
            for _ in range(2):
                with self.assertRaises(releases.RateLimited) as raised:
                    releases.request("https://api.github.com/")
                self.assertEqual(raised.exception.retry_at, 1600)
            opener.return_value.open.assert_called_once()
            opener.return_value.open.side_effect = None
            with patch.object(releases.time, "time", return_value=1601):
                releases.request("https://api.github.com/")
            self.assertEqual(opener.return_value.open.call_count, 2)

    def test_retry_after_and_body_only_rate_limits(self):
        for code, body, expected in ((429, b"", 1300), (403, b"API rate limit exceeded", 1300)):
            headers = Message()
            headers["Retry-After"] = "300"
            if code == 403:
                del headers["Retry-After"]
                expected = 1060
            error = HTTPError("https://api.github.com/", code, "Limited", headers, io.BytesIO(body))
            with (
                self.subTest(code=code),
                patch.dict(releases._RETRY_AFTER, {}, clear=True),
                patch.object(releases.time, "time", return_value=1000),
                patch.object(releases, "build_opener") as opener,
            ):
                opener.return_value.open.side_effect = error
                with self.assertRaises(releases.RateLimited) as raised:
                    releases.request("https://api.github.com/")
                self.assertEqual(raised.exception.retry_at, expected)

    def test_ordinary_permission_error_does_not_trigger_fallback(self):
        error = HTTPError(
            "https://api.github.com/", 403, "Forbidden", Message(), io.BytesIO(b"Denied")
        )
        with (
            patch.object(releases, "build_opener") as opener,
            patch.object(releases, "page_release") as fallback,
        ):
            opener.return_value.open.side_effect = error
            with self.assertRaisesRegex(ValueError, "denied access"):
                releases.latest_release(APP, Event(), self.cache)
            fallback.assert_not_called()
        self.assertEqual(releases._RETRY_AFTER, {})

    def test_public_fallback_requires_published_checksum_and_size(self):
        tag_url = f"https://github.com/{APP.repo}/releases/tag/v{self.release.version}"
        sums = f"{self.release.sha256} *{self.release.name}\n".encode()
        with (
            patch.object(releases, "request") as request,
            patch.object(
                releases, "read_metadata", side_effect=[public_assets(self.release, False), sums]
            ) as metadata,
        ):
            request.side_effect = [
                Response(tag_url),
                Response(self.release.url, {"Content-Length": str(self.release.size)}),
            ]
            self.assertEqual(releases.page_release(APP, Event()), self.release)
            request.assert_any_call(self.release.url, method="HEAD")
            self.assertEqual(metadata.call_count, 2)
            metadata.assert_called_with(self.release.url.rsplit("/", 1)[0] + "/SHA256SUMS.txt")

    def test_appimage_names_without_linux_work_for_api_fallback_and_cache(self):
        arch = releases.architecture()
        for name in (f"{APP.id}-1.2.3-{arch}.AppImage", f"{APP.id}-{arch}.AppImage"):
            with self.subTest(name=name):
                release = replace(
                    self.release, name=name, url=self.release.url.rsplit("/", 1)[0] + "/" + name
                )
                data = {
                    "tag_name": "v1.2.3",
                    "assets": [
                        {
                            "name": name,
                            "browser_download_url": release.url,
                            "size": release.size,
                            "digest": "sha256:" + release.sha256,
                        }
                    ],
                }
                self.assert_release_paths(APP, data, release, arch)
                other_arch = "aarch64" if arch == "x86_64" else "x86_64"
                with self.assertRaisesRegex(ValueError, "AppImage filename"):
                    releases.select_release(APP, data, other_arch)

    def test_mixed_case_appimage_names_keep_the_actual_download_name(self):
        app = custom_app("https://github.com/mitcad/Mitcad")
        arch = releases.architecture()
        name = f"Mitcad-0.13.1-{arch}.AppImage"
        url = f"https://github.com/mitcad/Mitcad/releases/download/v0.13.1/{name}"
        release = releases.Release("0.13.1", name, url, self.release.size, self.release.sha256)
        asset = {
            "name": name,
            "browser_download_url": url,
            "size": release.size,
            "digest": "sha256:" + release.sha256,
        }
        data = {"tag_name": "v0.13.1", "assets": [None, {"name": None}, asset]}
        self.assert_release_paths(app, data, release, arch)
        other_arch = "aarch64" if arch == "x86_64" else "x86_64"
        with self.assertRaisesRegex(ValueError, "AppImage filename"):
            releases.select_release(app, data, other_arch)
        asset["browser_download_url"] = url.rsplit("/", 1)[0] + "/" + name.lower()
        with self.assertRaisesRegex(ValueError, "repository"):
            releases.select_release(app, data, arch)

    def test_architecture_aliases_work_for_api_fallback_and_cache(self):
        app = custom_app("https://github.com/therichronicles/EarthCraft")
        for arch, alias in (("x86_64", "x64"), ("x86_64", "amd64"), ("aarch64", "arm64")):
            with self.subTest(arch=arch, alias=alias):
                name = f"earthcraft-0.2.0-linux-{alias}.AppImage"
                url = f"{app.source_url}/releases/download/v0.2.0/{name}"
                release = releases.Release("0.2.0", name, url, 100, self.release.sha256)
                asset = {
                    "name": name,
                    "browser_download_url": url,
                    "size": release.size,
                    "digest": "sha256:" + release.sha256,
                }
                data = {"tag_name": "v0.2.0", "assets": [asset]}
                self.assert_release_paths(app, data, release, arch)
                other_arch = "aarch64" if arch == "x86_64" else "x86_64"
                with self.assertRaisesRegex(ValueError, "AppImage filename"):
                    releases.select_release(app, data, other_arch)
                zsync = {**asset, "name": name + ".zsync", "browser_download_url": url + ".zsync"}
                with self.assertRaisesRegex(ValueError, "AppImage filename"):
                    releases.select_release(app, {**data, "assets": [zsync]}, arch)
                canonical = {
                    **asset,
                    "name": name.replace(alias, arch),
                    "browser_download_url": url.replace(alias, arch),
                }
                selected = releases.select_release(
                    app, {**data, "assets": [asset, canonical]}, arch
                )
                self.assertEqual(selected.name, canonical["name"])

    def test_checksum_manifest_rejects_conflicting_entries(self):
        line = f"{self.release.sha256} *{self.release.name}\n"
        self.assertEqual(
            releases.checksums("invalid line\n" + line * 2),
            {self.release.name: self.release.sha256},
        )
        with self.assertRaisesRegex(ValueError, "conflicting"):
            releases.checksums(line + f"{'0' * 64} {self.release.name}\n")

    def test_public_fallback_rejects_wrong_repository_missing_checksum_or_size(self):
        tag_url = f"https://github.com/{APP.repo}/releases/tag/v{self.release.version}"
        sums = public_assets(self.release)
        cases = (
            ("https://github.com/someone/else/releases/tag/v1.2.3", sums, "1"),
            (tag_url, b"", "1"),
            (tag_url, sums, ""),
            (tag_url, sums, "0"),
            (tag_url, sums, "not a number"),
            (tag_url, sums, "1" * 13),
            (tag_url, sums, str(releases.MAX_DOWNLOAD + 1)),
        )
        for location, manifest, length in cases:
            with (
                self.subTest(location=location, manifest=manifest, length=length),
                patch.object(releases, "request") as request,
                patch.object(releases, "read_metadata", return_value=manifest),
            ):
                request.side_effect = [
                    Response(location),
                    Response("unused", {"Content-Length": length}),
                ]
                with self.assertRaises(ValueError):
                    releases.page_release(APP, Event())

    def test_fresh_cache_survives_restart_and_avoids_requests(self):
        with (
            patch.object(releases, "api_release", return_value=self.release) as api,
            patch.object(releases, "page_release") as fallback,
        ):
            self.assertEqual(releases.latest_release(APP, Event(), self.cache), self.release)
            releases._RETRY_AFTER.clear()
            self.assertEqual(releases.latest_release(APP, Event(), self.cache), self.release)
            api.assert_called_once()
            fallback.assert_not_called()

    def test_release_notes_survive_the_cache_and_old_caches_still_load(self):
        notes = replace(self.release, notes="## What's new\n\n- one", published="2026-10-19")
        now = time.time()
        atomic_json(self.cache_file, {"checked_at": now, "release": asdict(notes)})
        self.assertEqual(releases.cached_release(APP, self.cache), notes)
        old = asdict(self.release)
        del old["notes"], old["published"]
        atomic_json(self.cache_file, {"checked_at": now, "release": old})
        self.assertEqual(releases.cached_release(APP, self.cache), self.release)
        self.assertEqual(releases.cached_release(APP, self.cache).notes, "")
        for field, value, expected in (
            ("notes", 5, ""),
            ("notes", ["x"], ""),
            ("notes", "x" * 50_000, "x" * releases.MAX_NOTES),
            ("published", "not a date", ""),
            ("published", 20261019, ""),
        ):
            with self.subTest(field=field):
                item = {**asdict(notes), field: value}
                atomic_json(self.cache_file, {"checked_at": now, "release": item})
                loaded = releases.cached_release(APP, self.cache)
                self.assertEqual(getattr(loaded, field), expected)

    def test_expired_or_invalid_cache_is_refetched(self):
        bad_items = (
            {
                "checked_at": time.time() - releases.CACHE_SECONDS - 1,
                "release": asdict(self.release),
            },
            {"checked_at": True, "release": asdict(self.release)},
            {"checked_at": time.time(), "release": []},
            {
                "checked_at": time.time(),
                "release": {**asdict(self.release), "url": "https://evil.test/a"},
            },
            {"checked_at": time.time(), "release": {**asdict(self.release), "sha256": "bad"}},
            {"checked_at": time.time(), "release": {**asdict(self.release), "size": 0}},
            {"checked_at": time.time(), "release": {**asdict(self.release), "version": "9.9.9"}},
        )
        for data in bad_items:
            with self.subTest(data=data):
                atomic_json(self.cache_file, data)
                with patch.object(releases, "api_release", return_value=self.release) as api:
                    self.assertEqual(
                        releases.latest_release(APP, Event(), self.cache), self.release
                    )
                    api.assert_called_once()
        self.cache_file.write_text("not JSON")
        self.assertIsNone(releases.cached_release(APP, self.cache))

    def test_persisted_cooldown_uses_public_fallback_after_restart(self):
        until = time.time() + 600
        releases._RETRY_AFTER["api.github.com"] = until
        with patch.object(releases, "page_release", return_value=self.release):
            self.assertEqual(releases.latest_release(APP, Event(), self.cache), self.release)
        self.assertEqual(
            json.loads((self.cache / "limits.json").read_text())["api.github.com"], until
        )
        self.cache_file.unlink()
        releases._RETRY_AFTER.clear()
        with (
            patch.object(releases, "page_release", return_value=self.release) as fallback,
            patch.object(releases, "build_opener") as opener,
        ):
            self.assertEqual(releases.latest_release(APP, Event(), self.cache), self.release)
            opener.assert_not_called()
            fallback.assert_called_once()

    def test_cache_write_failure_does_not_block_release_lookup(self):
        with (
            patch.object(releases, "api_release", return_value=self.release),
            patch.object(releases, "atomic_json", side_effect=OSError("Read only")),
        ):
            self.assertEqual(releases.latest_release(APP, Event(), self.cache), self.release)

    def test_expired_cache_does_not_masquerade_as_success_when_offline(self):
        atomic_json(self.cache_file, {"checked_at": 0, "release": asdict(self.release)})
        with patch.object(releases, "api_release", side_effect=OSError("Offline")):
            with self.assertRaisesRegex(OSError, "Offline"):
                releases.latest_release(APP, Event(), self.cache)

    def test_opener_is_shared_and_rate_limits_are_saved_publicly(self) -> None:
        with patch.object(releases, "build_opener") as build:
            first, second = releases._opener(), releases._opener()
            self.assertIs(first, second)
            build.assert_called_once()
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(releases._RETRY_AFTER, {"api.github.com": time.time() + 60}, clear=True),
        ):
            releases.save_rate_limits(Path(temp))
            saved = json.loads((Path(temp) / "limits.json").read_text())
            self.assertEqual(saved, releases._RETRY_AFTER)


if __name__ == "__main__":
    unittest.main()
