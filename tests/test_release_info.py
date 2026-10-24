"""Catalog discovery is independent of preferences and keeps safe offline information."""

import io
import json
import tempfile
import time
import unittest
from dataclasses import replace
from email.message import Message
from pathlib import Path
from threading import Event
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from test_library import APP
from test_prereleases import metadata, release_list_html
from test_releases import Response, public_assets

from appglider import github_status, releases
from appglider.release_info import cached_info, discover_releases, info_path, remember_release


class ReleaseInfoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)
        self.stable = {**metadata("1.2.3"), "is_latest": True}
        self.beta = metadata("1.3.0-beta.10", True)
        self.entries = [self.beta, self.stable]

    def discover(self):
        with patch.object(releases, "api_entries", return_value=self.entries):
            return discover_releases(APP, Event(), self.cache, force=True)

    def test_both_channels_use_two_api_requests_and_keep_user_preference(self):
        with patch.object(
            releases,
            "read_metadata",
            side_effect=[json.dumps(self.stable).encode(), json.dumps(self.entries).encode()],
        ) as read:
            info = discover_releases(APP, Event(), self.cache)
        self.assertEqual(read.call_count, 2)
        self.assertIn("per_page=20", read.call_args.args[0])
        self.assertEqual(info.choice(False).version, "1.2.3")
        self.assertEqual(info.choice(True).version, "1.3.0-beta.10")
        self.assertFalse(APP.include_prereleases)
        self.assertTrue(info.stable.release.is_latest)
        self.assertFalse(info.preview.release.is_latest)
        self.assertEqual(cached_info(APP, self.cache), info)

    def test_prefixed_short_versions_keep_channel_ordering_and_cache(self):
        self.stable = {**metadata("0.6"), "is_latest": True}
        self.beta = metadata("0.7-beta.10", True)
        for data in (self.stable, self.beta):
            old_tag = data["tag_name"]
            data["tag_name"] = "studio-" + old_tag
            for asset in data["assets"]:
                asset["browser_download_url"] = asset["browser_download_url"].replace(
                    old_tag, data["tag_name"]
                )
        self.entries = [self.beta, self.stable, metadata("0.7-beta.2", True)]
        info = self.discover()
        self.assertEqual(info.choice(False).version, "0.6")
        self.assertEqual(info.choice(True).version, "0.7-beta.10")
        self.assertEqual(cached_info(APP, self.cache), info)
        self.assertEqual(releases.cached_release(APP, self.cache), info.stable.release)

    def test_unsupported_tags_keep_the_actionable_discovery_error(self):
        self.entries = [{**self.stable, "tag_name": "nightly"}]
        with self.assertRaisesRegex(ValueError, "AppImage.*version tag"):
            self.discover()

    def test_stable_replaces_preview_and_designated_latest_is_preserved(self):
        self.entries.append({**metadata("1.4.0"), "is_latest": False})
        info = self.discover()
        self.assertEqual(info.choice(False).version, "1.2.3")
        self.assertEqual(info.choice(True).version, "1.4.0")
        self.assertFalse(info.choice(True).release.is_latest)
        self.assertEqual(cached_info(APP, self.cache), info)
        self.stable = {**metadata("1.3.0"), "is_latest": True}
        self.entries = [self.stable, self.beta]
        info = self.discover()
        self.assertEqual(info.choice(True).version, "1.3.0")
        self.assertFalse(info.choice(True).release.prerelease)

    def test_only_preview_needs_opt_in_and_obsolete_preview_is_not_selected(self):
        self.entries = [self.beta]
        info = self.discover()
        self.assertIsNone(info.choice(False))
        self.assertIn("Only prereleases", info.error(False))
        self.assertTrue(info.choice(True).release.prerelease)
        self.entries = [self.stable, metadata("1.2.0-beta.1", True)]
        self.assertEqual(self.discover().choice(True).version, "1.2.3")

    def test_unusable_new_preview_is_explained_without_falling_back_when_opted_in(self):
        self.beta["assets"] = []
        info = self.discover()
        self.assertIsNotNone(info.choice(False).release)
        self.assertIsNone(info.choice(True).release)
        self.assertTrue(info.error(True))
        self.assertIsNone(
            releases.cached_release(replace(APP, include_prereleases=True), self.cache)
        )
        self.assertEqual(cached_info(APP, self.cache), info)

    def test_offline_refresh_keeps_stale_cache_and_download_cache_expires(self):
        info = self.discover()
        path = info_path(APP, self.cache)
        data = json.loads(path.read_text())
        data["checked_at"] = time.time() - 3600
        path.write_text(json.dumps(data))
        contents = path.read_bytes()
        stale = cached_info(APP, self.cache)
        self.assertIsNotNone(stale)
        self.assertFalse(stale.fresh())
        with patch.object(releases, "api_entries", side_effect=OSError("Offline")):
            with self.assertRaisesRegex(OSError, "Offline"):
                discover_releases(APP, Event(), self.cache)
        self.assertEqual(path.read_bytes(), contents)
        for include in (False, True):
            channel = replace(APP, include_prereleases=include)
            item_path = releases.release_cache_path(channel, self.cache)
            item = json.loads(item_path.read_text())
            item["checked_at"] = data["checked_at"]
            item_path.write_text(json.dumps(item))
            self.assertIsNone(releases.cached_release(channel, self.cache))
        self.assertEqual(cached_info(APP, self.cache).stable, info.stable)

    def test_fresh_cache_avoids_network_and_manual_refresh_bypasses_it(self):
        info = self.discover()
        with patch.object(releases, "api_entries", return_value=self.entries) as read:
            self.assertEqual(discover_releases(APP, Event(), self.cache), info)
            read.assert_not_called()
            discover_releases(APP, Event(), self.cache, force=True)
            read.assert_called_once()

    def test_corrupt_or_foreign_cached_information_is_rejected(self):
        self.discover()
        path = info_path(APP, self.cache)
        original = json.loads(path.read_text())
        for value in (None, True, float("nan"), 1e100):
            path.write_text(json.dumps({**original, "checked_at": value}))
            self.assertIsNone(cached_info(APP, self.cache))
        for channel in ("stable", "preview"):
            data = json.loads(json.dumps(original))
            data[channel]["release"]["url"] = (
                "https://github.com/other/app/releases/download/v1/a.AppImage"
            )
            path.write_text(json.dumps(data))
            self.assertIsNone(cached_info(APP, self.cache))
        path.write_text("[]")
        self.assertIsNone(cached_info(APP, self.cache))

    def test_release_notes_are_kept_in_the_catalog_cache_and_old_caches_still_load(self):
        self.stable = {
            **self.stable,
            "body": "Stable notes",
            "published_at": "2026-10-01T10:00:00Z",
        }
        self.beta = {**self.beta, "body": "Beta notes", "published_at": "2026-10-10T10:00:00Z"}
        self.entries = [self.beta, self.stable]
        info = self.discover()
        self.assertEqual(
            (info.stable.release.notes, info.stable.release.published),
            ("Stable notes", "2026-10-01"),
        )
        self.assertEqual(info.preview.release.notes, "Beta notes")
        self.assertEqual(cached_info(APP, self.cache), info)
        written = releases.release_cache_path(APP, self.cache)
        self.assertEqual(releases.cached_release(APP, self.cache), info.stable.release)
        self.assertIn("Stable notes", written.read_text())
        path = info_path(APP, self.cache)
        data = json.loads(path.read_text())
        for channel in ("stable", "preview"):
            for key in ("notes", "published"):
                del data[channel]["release"][key]
        path.write_text(json.dumps(data))
        old = cached_info(APP, self.cache)
        self.assertIsNotNone(old)
        self.assertEqual(old.stable.release, replace(info.stable.release, notes="", published=""))
        self.assertEqual(old.preview.release.notes, "")
        data["stable"]["release"]["notes"] = ["not text"]
        path.write_text(json.dumps(data))
        self.assertEqual(cached_info(APP, self.cache).stable.release.notes, "")
        remembered = remember_release(APP, info.stable.release, self.cache)
        self.assertEqual(remembered.stable.release.notes, "Stable notes")
        self.assertEqual(cached_info(APP, self.cache).stable.release.notes, "Stable notes")

    def test_quota_fallback_discovers_both_channels_with_the_same_checksums(self):
        info = self.discover()
        with (
            patch.object(
                releases, "api_entries", side_effect=releases.RateLimited("api.github.com", 0)
            ),
            patch.object(
                releases,
                "request",
                side_effect=[
                    Response(APP.source_url + "/releases/tag/v1.2.3"),
                    Response(
                        info.stable.release.url, {"Content-Length": str(info.stable.release.size)}
                    ),
                    Response(
                        info.preview.release.url, {"Content-Length": str(info.preview.release.size)}
                    ),
                ],
            ),
            patch.object(
                releases,
                "read_metadata",
                side_effect=[
                    release_list_html(self.entries, "v1.2.3"),
                    public_assets(info.stable.release),
                    public_assets(info.preview.release),
                ],
            ),
        ):
            found = discover_releases(APP, Event(), self.cache, force=True)
        self.assertEqual(found.stable, info.stable)
        self.assertEqual(found.preview, info.preview)

    def test_new_download_information_updates_one_channel_without_faking_freshness(self):
        info = self.discover()
        release = replace(
            info.stable.release,
            version="1.3.0",
            name=info.stable.release.name.replace("1.2.3", "1.3.0"),
            url=info.stable.release.url.replace("1.2.3", "1.3.0"),
        )
        updated = remember_release(APP, release, self.cache)
        self.assertEqual(updated.stable.release, release)
        self.assertEqual(updated.preview, info.preview)
        self.assertEqual(updated.checked_at, info.checked_at)
        self.assertEqual(cached_info(APP, self.cache), updated)

    def cheap(self, tag, **options):
        with (
            patch.object(releases, "latest_tag", return_value=tag) as latest,
            patch.object(releases, "api_entries", return_value=self.entries) as api,
        ):
            info = discover_releases(
                options.pop("app", APP), Event(), self.cache, force=True, cheap=True
            )
        return info, latest, api

    def test_cheap_check_reuses_saved_information_when_the_latest_tag_is_unchanged(self):
        first = self.discover()
        time.sleep(0.01)
        info, latest, api = self.cheap("v1.2.3")
        api.assert_not_called()
        latest.assert_called_once_with(APP.repo, unittest.mock.ANY)
        self.assertEqual(
            (info.stable, info.preview, info.newer_stable),
            (first.stable, first.preview, first.newer_stable),
        )
        self.assertGreater(info.checked_at, first.checked_at)
        self.assertEqual(cached_info(APP, self.cache), info)
        self.assertEqual(github_status._status.source, "GitHub release page")
        self.assertEqual(github_status._status.repo, APP.repo)

    def test_cheap_check_asks_the_api_when_the_tag_changed_or_the_answer_is_unclear(self):
        self.discover()
        for tag in ("v1.3.0", None):
            with self.subTest(tag=tag):
                self.entries = [{**metadata("1.3.0"), "is_latest": True}]
                info, _, api = self.cheap(tag)
                api.assert_called_once()
                self.assertEqual(info.stable.version, "1.3.0")
        self.entries = [self.beta, self.stable]
        self.discover()  # back to 1.2.3 for the checks below

    def test_cheap_check_is_skipped_without_saved_information_or_when_not_asked(self):
        info, latest, api = self.cheap("v1.2.3")  # nothing saved yet
        latest.assert_not_called()
        api.assert_called_once()
        with (
            patch.object(releases, "latest_tag", return_value="v1.2.3") as latest,
            patch.object(releases, "feed_tags") as feed,
            patch.object(releases, "api_entries", return_value=self.entries) as api,
        ):
            api.reset_mock()
            discover_releases(APP, Event(), self.cache, force=True)  # not asked to be cheap
            latest.assert_not_called()
            feed.assert_not_called()
            api.assert_called_once()

    def test_a_fresh_cache_needs_no_request_at_all_even_when_cheap(self):
        self.discover()
        with (
            patch.object(releases, "latest_tag") as latest,
            patch.object(releases, "api_entries") as api,
        ):
            discover_releases(APP, Event(), self.cache, cheap=True)
        latest.assert_not_called()
        api.assert_not_called()

    def with_previews(self, tags):
        app = replace(APP, include_prereleases=True)
        with (
            patch.object(releases, "feed_tags", return_value=tags) as feed,
            patch.object(releases, "latest_tag") as latest,
            patch.object(releases, "api_entries", return_value=self.entries) as api,
        ):
            info = discover_releases(app, Event(), self.cache, force=True, cheap=True)
        latest.assert_not_called()  # previews never use the Latest page
        return info, feed, api

    def test_apps_with_previews_use_the_feed_and_skip_the_api_when_nothing_is_newer(self):
        first = self.discover()  # knows 1.2.3 and 1.3.0-beta.10
        time.sleep(0.01)
        for tags in (["v1.3.0-beta.10", "v1.2.3"], ["v1.2.3"], ["v1.3.0-beta.2", "v1.2.0"]):
            with self.subTest(tags=tags):
                info, feed, api = self.with_previews(tags)
                feed.assert_called_once()
                api.assert_not_called()
                self.assertEqual((info.stable, info.preview), (first.stable, first.preview))
                self.assertGreater(info.checked_at, first.checked_at)
                self.assertEqual(github_status._status.source, "GitHub release feed")

    def test_apps_with_previews_ask_the_api_when_the_feed_shows_something_newer_or_unclear(self):
        self.discover()
        newer = {**metadata("1.3.0-beta.11", True)}
        for tags in (["v1.3.0-beta.11", "v1.2.3"], ["v1.3.0"], None, [], ["nightly"]):
            with self.subTest(tags=tags):
                self.entries = [newer, self.stable]
                info, _, api = self.with_previews(tags)
                api.assert_called_once()
                self.assertEqual(info.preview.version, "1.3.0-beta.11")
                self.entries = [self.beta, self.stable]
                self.discover()

    def test_the_feed_is_not_used_without_saved_information(self):
        info, feed, api = self.with_previews(["v1.2.3"])
        feed.assert_not_called()
        api.assert_called_once()


class FeedTests(unittest.TestCase):
    ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <link type="text/html" rel="alternate" href="https://github.com/owner/repo/releases"/>
  <entry>
    <id>tag:github.com,2008:Repository/1/v1.3.0-beta.1</id>
    <link rel="alternate" type="text/html" href="https://github.com/Owner/Repo/releases/tag/v1.3.0-beta.1"/>
    <content type="html">&lt;a href="https://github.com/owner/repo/releases/tag/v9.9.9"&gt;spoof&lt;/a&gt;</content>
  </entry>
  <entry>
    <link rel="alternate" type="text/html" href="https://github.com/owner/repo/releases/tag/app%2Fv1.2.3"/>
  </entry>
  <entry>
    <link rel="alternate" type="text/html" href="https://github.com/other/repo/releases/tag/v7.0.0"/>
  </entry>
  <entry>
    <link rel="alternate" type="text/html" href="https://github.com/owner/repo/releases/tag/nightly"/>
  </entry>
  <entry>
    <link rel="alternate" type="text/html" href="https://github.com/owner/repo/releases/tag/v1.3.0-beta.1"/>
  </entry>
</feed>"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)
        self.app = replace(APP, repo="owner/repo")

    def tags(self, result):
        with patch.object(releases, "feed_request", return_value=result) as request:
            tags = releases.feed_tags(self.app, self.cache, Event())
        return tags, request

    def test_tags_come_from_this_repositorys_release_links_only_and_are_remembered(self):
        tags, request = self.tags((200, self.ATOM.encode(), 'W/"a"'))
        self.assertEqual(tags, ["v1.3.0-beta.1", "app/v1.2.3"])
        request.assert_called_once_with("owner/repo", "")
        saved = json.loads((self.cache / f"{self.app.id}-feed.json").read_text())
        self.assertEqual(saved, {"etag": 'W/"a"', "tags": tags})

    def test_a_saved_etag_makes_the_request_conditional_and_a_304_reuses_the_saved_tags(self):
        self.tags((200, self.ATOM.encode(), 'W/"a"'))
        tags, request = self.tags((304, b"", 'W/"a"'))
        request.assert_called_once_with("owner/repo", 'W/"a"')
        self.assertEqual(tags, ["v1.3.0-beta.1", "app/v1.2.3"])

    def test_failures_and_unusable_saved_files_mean_unknown(self):
        self.assertIsNone(self.tags(None)[0])
        (self.cache / f"{self.app.id}-feed.json").write_text("not json")
        tags, request = self.tags(None)
        self.assertIsNone(tags)
        request.assert_called_once_with("owner/repo", "")  # a bad file is never trusted
        (self.cache / f"{self.app.id}-feed.json").write_text('{"etag": 5, "tags": "x"}')
        self.assertEqual(self.tags((200, b"<feed/>", "e"))[0], [])

    def test_cancellation_is_honoured_before_any_request(self):
        cancel = Event()
        cancel.set()
        with (
            patch.object(releases, "feed_request") as request,
            self.assertRaises(releases.Cancelled),
        ):
            releases.feed_tags(self.app, self.cache, cancel)
        request.assert_not_called()

    def opener(self, *, error=None, body=b"<feed/>", headers=None):
        class Reply(io.BytesIO):
            def __init__(self):
                super().__init__(body)
                self.headers = headers or {"ETag": 'W/"x"'}

        class Opener:
            def open(self, request, timeout):
                self.request, self.timeout = request, timeout
                if error is not None:
                    raise error
                return Reply()

        opener = Opener()
        return opener, patch.object(releases, "build_opener", return_value=opener)

    def test_feed_request_is_conditional_bounded_and_never_follows_redirects(self):
        opener, patched = self.opener()
        with patched:
            self.assertEqual(
                releases.feed_request("owner/repo", 'W/"old"'), (200, b"<feed/>", 'W/"x"')
            )
        self.assertEqual(opener.request.full_url, "https://github.com/owner/repo/releases.atom")
        self.assertEqual(opener.request.get_header("If-none-match"), 'W/"old"')
        self.assertEqual(opener.timeout, 20)

    def test_feed_request_outcomes(self):
        def http(code):
            return HTTPError("u", code, "x", Message(), io.BytesIO())

        with self.opener(error=http(304))[1]:
            self.assertEqual(releases.feed_request("o/r", 'W/"e"'), (304, b"", 'W/"e"'))
            self.assertIsNone(releases.feed_request("o/r", ""))  # a 304 needs a saved ETag
        for failure in (http(404), http(429), http(500), URLError("x"), TimeoutError()):
            with self.subTest(failure=failure), self.opener(error=failure)[1]:
                self.assertIsNone(releases.feed_request("o/r", ""))
        with self.opener(body=b"x" * (releases.MAX_FEED + 1))[1]:
            self.assertIsNone(releases.feed_request("o/r", ""))


class WebFetchTests(unittest.TestCase):
    def opener(self, *, error=None, body=b"", final=None):
        class Reply(io.BytesIO):
            def __enter__(self):
                return self

            def geturl(self):
                return final or self.url

        class Opener:
            def open(self, request, timeout):
                self.request, self.timeout = request, timeout
                if error is not None:
                    raise error
                reply = Reply(body)
                reply.url = request.full_url
                return reply

        opener = Opener()
        patched = patch.object(releases, "build_opener", return_value=opener)
        return opener, patched

    def http(self, code):
        return HTTPError("u", code, "x", Message(), io.BytesIO())

    def test_raw_files_come_from_the_raw_host_with_a_quoted_path_and_a_size_limit(self):
        opener, patched = self.opener(body=b"PNGDATA")
        with patched as build:
            data = releases.raw_bytes("owner/repo", "assets/my icon#1.png", 100, Event())
        self.assertEqual(data, b"PNGDATA")
        self.assertEqual(
            opener.request.full_url,
            "https://raw.githubusercontent.com/owner/repo/HEAD/assets/my%20icon%231.png",
        )
        self.assertTrue(opener.request.get_header("User-agent").startswith("AppGlider/"))
        self.assertEqual(opener.timeout, 20)
        (handler,) = build.call_args.args  # redirects are followed only to trusted hosts
        self.assertIsInstance(handler, releases.GitHubRedirect)
        with self.opener(body=b"x" * 101)[1], self.assertRaisesRegex(ValueError, "larger"):
            releases.raw_bytes("owner/repo", "a.png", 100, Event())

    def test_failures_become_plain_errors_and_never_rate_limit_the_api(self):
        for error, message in (
            (self.http(404), "HTTP 404"),
            (self.http(429), "HTTP 429"),
            (self.http(500), "HTTP 500"),
            (URLError("offline"), "Could not reach GitHub"),
            (TimeoutError(), "Could not reach GitHub"),
            (ValueError("GitHub redirected to an untrusted download location."), "untrusted"),
        ):
            with self.subTest(error=error), self.opener(error=error)[1]:
                with self.assertRaisesRegex(ValueError, message):
                    releases.raw_bytes("owner/repo", "a.png", 100, Event())
        self.assertEqual(releases._RETRY_AFTER.get("raw.githubusercontent.com", 0), 0)

    def test_fetches_honour_cancellation_and_trusted_hosts(self):
        cancel = Event()
        cancel.set()
        with (
            patch.object(releases, "build_opener") as build,
            self.assertRaises(releases.Cancelled),
        ):
            releases.raw_bytes("owner/repo", "a.png", 100, cancel)
        with self.assertRaises(releases.Cancelled):
            releases.web_listing("owner/repo", "", cancel)
        build.assert_not_called()
        self.assertTrue(releases.valid_url("https://raw.githubusercontent.com/o/r/HEAD/a.png"))
        for url in (
            "https://raw.githubusercontent.com.evil.test/o/r/HEAD/a.png",
            "http://raw.githubusercontent.com/o/r/HEAD/a.png",
            "https://user@raw.githubusercontent.com/o/r/HEAD/a.png",
        ):
            self.assertFalse(releases.valid_url(url))

    def page(self, items):
        payload = {"payload": {"codeViewTreeRoute": {"tree": {"items": items}}}}
        return json.dumps(payload).encode()

    def test_a_folder_listing_is_read_from_the_web_page_as_json(self):
        good = {"name": "icon.png", "path": "assets/icon.png", "contentType": "file"}
        folder = {"name": "icons", "path": "assets/icons", "contentType": "directory"}
        junk = [
            {"name": "x", "path": "../escape", "contentType": "file"},
            {"name": "x", "path": "a//b", "contentType": "file"},
            {"name": "x", "path": "a\nb", "contentType": "file"},
            {"name": "x", "path": "a/b", "contentType": "symlink"},
            {"name": 5, "path": "a/b", "contentType": "file"},
            {"name": "x", "path": "y" * 600, "contentType": "file"},
            "text",
        ]
        opener, patched = self.opener(body=self.page([good, folder, *junk]))
        with patched:
            items, repo = releases.web_listing("owner/repo", "assets", Event())
        self.assertEqual((items, repo), ([good, folder], "owner/repo"))
        self.assertEqual(opener.request.full_url, "https://github.com/owner/repo/tree/HEAD/assets")
        self.assertEqual(opener.request.get_header("Accept"), "application/json")
        opener, patched = self.opener(body=self.page([good]))
        with patched:
            releases.web_listing("owner/repo", "", Event())
        self.assertEqual(opener.request.full_url, "https://github.com/owner/repo/tree/HEAD")

    def test_a_moved_repository_reports_its_current_name_so_later_requests_skip_the_redirect(self):
        for final, expected in (
            ("https://github.com/OrcaSlicer/OrcaSlicer/tree/HEAD", "OrcaSlicer/OrcaSlicer"),
            ("https://github.com/New-Owner/new.repo/tree/HEAD/assets/icons", "New-Owner/new.repo"),
            ("https://github.com/owner/repo/tree/HEAD/assets", "owner/repo"),
            ("https://github.com/blob/only", "owner/repo"),  # not a tree page: keep the saved name
            ("https://github.com/../../x/tree/HEAD", "owner/repo"),
            ("https://github.com/bad name/x y/tree/HEAD", "owner/repo"),
        ):
            with self.subTest(final=final), self.opener(body=self.page([]), final=final)[1]:
                self.assertEqual(releases.web_listing("owner/repo", "", Event())[1], expected)

    def test_a_listing_in_an_unexpected_form_is_an_error_not_a_guess(self):
        for body in (b"not json", b"[]", b'{"payload": {}}', b'{"payload": 5}', b"null"):
            with self.subTest(body=body), self.opener(body=body)[1]:
                with self.assertRaisesRegex(ValueError, "expected form"):
                    releases.web_listing("owner/repo", "", Event())
        with self.opener(body=self.page("not a list"))[1]:
            self.assertEqual(releases.web_listing("owner/repo", "", Event())[0], [])
        with self.opener(body=b"x" * (releases.MAX_LISTING + 1))[1]:
            with self.assertRaises(ValueError):
                releases.web_listing("owner/repo", "", Event())
        with self.opener(error=self.http(301))[1], self.assertRaisesRegex(ValueError, "HTTP 301"):
            releases.web_listing("owner/repo", "", Event())  # a redirect that could not be used


class LatestTagTests(unittest.TestCase):
    def tag(self, location, repo="owner/repo"):
        with patch.object(releases, "redirect_location", return_value=location):
            return releases.latest_tag(repo, Event())

    def test_tags_are_read_from_the_redirect_of_this_repository(self):
        base = "https://github.com/Owner/Repo/releases/tag/"
        self.assertEqual(self.tag(base + "v1.2.3"), "v1.2.3")
        self.assertEqual(self.tag("/owner/repo/releases/tag/v0.6"), "v0.6")
        self.assertEqual(self.tag(base + "app%2Fv1.2.3"), "app/v1.2.3")
        self.assertEqual(self.tag(base + "studio-v0.6.0"), "studio-v0.6.0")

    def test_anything_else_is_unknown_so_callers_fall_back_to_the_api(self):
        for location in (
            None,
            "",
            "https://github.com/owner/repo/releases",
            "https://github.com/owner/repo",
            "https://github.com/other/renamed/releases/tag/v1.0.0",
            "https://evil.example/owner/repo/releases/tag/v1.0.0",
            "http://github.com/owner/repo/releases/tag/v1.0.0",
            "https://github.com/owner/repo/releases/tag/nightly",
            "https://github.com/owner/repo/releases/tag/",
        ):
            with self.subTest(location=location):
                self.assertIsNone(self.tag(location))

    def test_cancellation_is_honoured_before_any_request(self):
        cancel = Event()
        cancel.set()
        with (
            patch.object(releases, "redirect_location") as read,
            self.assertRaises(releases.Cancelled),
        ):
            releases.latest_tag("owner/repo", cancel)
        read.assert_not_called()

    def opener(self, *, error=None, response=None):
        class Opener:
            def open(self, request, timeout):
                self.request, self.timeout = request, timeout
                if error is not None:
                    raise error
                return response or io.BytesIO()

        opener = Opener()
        return opener, patch.object(releases, "build_opener", return_value=opener)

    def http(self, code, location=""):
        headers = Message()
        if location:
            headers["Location"] = location
        return HTTPError(
            "https://github.com/owner/repo/releases/latest", code, "x", headers, io.BytesIO()
        )

    def test_redirect_location_reports_a_redirect_without_following_it(self):
        url = "https://github.com/owner/repo/releases/latest"
        opener, patched = self.opener(error=self.http(302, "/owner/repo/releases/tag/v1.0.0"))
        with patched:
            self.assertEqual(releases.redirect_location(url), "/owner/repo/releases/tag/v1.0.0")
        self.assertEqual(opener.request.get_method(), "HEAD")
        self.assertEqual(opener.timeout, 10)
        self.assertTrue(opener.request.get_header("User-agent").startswith("AppGlider/"))

    def test_redirect_location_is_none_for_other_answers_and_failures(self):
        url = "https://github.com/owner/repo/releases/latest"
        for outcome in (
            {"error": self.http(404)},
            {"error": self.http(429)},
            {"error": self.http(302)},  # no Location header
            {"error": self.http(500, "/x")},
            {"error": URLError("offline")},
            {"error": TimeoutError()},
            {"response": io.BytesIO(b"a normal page")},
        ):
            with self.subTest(outcome=list(outcome)), self.opener(**outcome)[1]:
                self.assertIsNone(releases.redirect_location(url))
        for other in (
            "https://api.github.com/repos/owner/repo/releases/latest",
            "https://example.com/owner/repo/releases/latest",
            "http://github.com/owner/repo/releases/latest",
        ):
            with self.subTest(url=other), self.opener(error=self.http(302, "/x"))[1]:
                self.assertIsNone(releases.redirect_location(other))

    def test_the_redirect_handler_never_follows(self):
        handler = releases.NoRedirect()
        self.assertIsNone(
            handler.redirect_request(
                releases.Request("https://github.com/a/b/releases/latest"),
                io.BytesIO(),
                302,
                "Found",
                Message(),
                "https://github.com/a/b/releases/tag/v1",
            )
        )
