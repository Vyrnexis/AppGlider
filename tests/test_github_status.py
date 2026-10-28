"""Request status observes real paths without spending extra GitHub requests."""

import tempfile
import unittest
from email.message import Message
from pathlib import Path
from threading import Event
from unittest.mock import patch
from urllib.error import HTTPError

from test_library import APP, appimage_bytes, release_for
from test_prereleases import metadata
from test_releases import Response

from appglider.github import assets, download, http, pages
from appglider.github import status as github_status
from appglider.github.release_info import discover_releases


class GitHubStatusTests(unittest.TestCase):
    def setUp(self):
        for item in (
            patch.object(github_status, "_status", github_status.GitHubStatus()),
            patch.dict(http._RETRY_AFTER, {}, clear=True),
            patch.object(github_status.time, "time", return_value=1000),
        ):
            item.start()
            self.addCleanup(item.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cache = Path(temporary.name)
        self.headers = {
            "X-RateLimit-Remaining": "48",
            "X-RateLimit-Limit": "60",
            "X-RateLimit-Reset": "1600",
        }

    def test_normal_api_responses_report_counts_and_expire_without_guessing(self):
        response = Response("https://api.github.com/repos/owner/tool", self.headers)
        with patch.object(http, "build_opener") as opener:
            opener.return_value.open.return_value = response
            self.assertIs(http.request(response.url), response)
            label, details, limited = http.github_request_status()
            self.assertEqual(label, "GitHub: 48/60")
            self.assertIn("48 of 60 (last reported)", details)
            self.assertFalse(limited)
            with patch.object(github_status.time, "time", return_value=1601):
                label, details, limited = http.github_request_status()
                self.assertEqual(label, "GitHub status")
                self.assertIn("unknown", details)
                self.assertFalse(limited)
            opener.return_value.open.assert_called_once()

    def test_quota_is_plentiful_only_while_a_large_fresh_count_is_known(self):
        self.assertFalse(github_status.quota_plentiful())
        github_status.observe_headers(self.headers)
        self.assertTrue(github_status.quota_plentiful())
        github_status.observe_headers({**self.headers, "X-RateLimit-Remaining": "29"})
        self.assertFalse(github_status.quota_plentiful())
        github_status.observe_headers(self.headers)
        with patch.object(github_status.time, "time", return_value=1601):
            self.assertFalse(github_status.quota_plentiful())

    def test_rate_limit_errors_and_restored_cooldowns_show_retry_time(self):
        headers = Message()
        for name, value in {**self.headers, "X-RateLimit-Remaining": "0"}.items():
            headers[name] = value
        error = HTTPError("https://api.github.com/", 403, "Limited", headers, None)
        with patch.object(http, "build_opener") as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(http.RateLimited):
                http.request("https://api.github.com/")
        label, details, limited = http.github_request_status()
        self.assertEqual(label, "GitHub: limited")
        self.assertIn("0 of 60", details)
        self.assertIn("retry in 10 min", details)
        self.assertTrue(limited)
        with patch.object(github_status.time, "time", return_value=1601):
            self.assertFalse(http.github_request_status()[2])
            self.assertIn("unknown", http.github_request_status()[1])
        (self.cache / "limits.json").write_text('{"github.com": 1120}')
        http.restore_rate_limits(self.cache)
        self.assertIn("Public GitHub pages paused: retry in 2 min", http.github_request_status()[1])

    def test_secondary_limit_does_not_claim_the_primary_allowance_is_empty(self):
        github_status.observe_headers(self.headers)
        http._RETRY_AFTER["api.github.com"] = 1060
        label, details, limited = http.github_request_status()
        self.assertTrue(limited)
        self.assertEqual(label, "GitHub: limited")
        self.assertIn("48 of 60", details)
        self.assertIn("retry in 1 min", details)

    def test_missing_malformed_and_non_api_headers_do_not_invent_counts(self):
        for headers in (
            {},
            {**self.headers, "X-RateLimit-Remaining": "bad"},
            {**self.headers, "X-RateLimit-Remaining": "61"},
            {**self.headers, "X-RateLimit-Reset": "NaN"},
        ):
            with self.subTest(headers=headers):
                github_status.observe_headers(headers)
                self.assertIn("unknown", http.github_request_status()[1])
        response = Response("https://github.com/owner/tool", self.headers)
        with patch.object(http, "build_opener") as opener:
            opener.return_value.open.return_value = response
            http.request(response.url)
        self.assertIn("unknown", http.github_request_status()[1])

    def test_api_cache_failure_and_public_fallback_preserve_check_times(self):
        with patch.object(assets, "api_entries", return_value=[metadata("1.2.3")]):
            info = discover_releases(APP, Event(), self.cache)
        self.assertIn("Source: GitHub API", http.github_request_status()[1])
        checked = http.github_request_status()[1].split("Last successful release check:")[1]
        with patch.object(assets, "api_entries") as api:
            self.assertEqual(discover_releases(APP, Event(), self.cache), info)
            api.assert_not_called()
        label, details, _ = http.github_request_status()
        self.assertEqual(label, "GitHub: cached")
        self.assertIn("Last successful release check:" + checked, details)
        with patch.object(assets, "api_entries", side_effect=OSError("Offline")):
            with self.assertRaisesRegex(OSError, "Offline"):
                discover_releases(APP, Event(), self.cache, force=True)
        self.assertEqual(http.github_request_status()[1], details)
        with (
            patch.object(
                assets, "api_entries", side_effect=http.RateLimited("api.github.com", 1600)
            ),
            patch.object(pages, "page_entries", return_value=[metadata("1.2.3")]),
            patch.object(pages, "page_release_asset", return_value=info.stable.release),
        ):
            discover_releases(APP, Event(), self.cache, force=True)
        self.assertIn("Source: Public GitHub pages", http.github_request_status()[1])

    def test_install_discovery_and_its_cached_result_are_observed(self):
        release = release_for("1.2.3", appimage_bytes())
        with patch.object(assets, "api_release", return_value=release) as api:
            self.assertEqual(download.latest_release(APP, Event(), self.cache), release)
            self.assertIn("Source: GitHub API", http.github_request_status()[1])
            self.assertEqual(download.latest_release(APP, Event(), self.cache), release)
            self.assertEqual(http.github_request_status()[0], "GitHub: cached")
            api.assert_called_once()
