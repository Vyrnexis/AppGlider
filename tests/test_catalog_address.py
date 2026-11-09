"""Importing a catalog from a GitHub address: the address, the download and the Settings row."""

import os
import unittest
from io import BytesIO
from threading import Event
from unittest.mock import patch
from urllib.error import HTTPError

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import test_add_settings_ux as ux
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from appglider.core.catalogfile import MAX_BYTES, catalog_address
from appglider.github import http
from appglider.github.versions import Cancelled
from appglider.ui.pages.catalog_preview import CatalogPreviewDialog

RAW = "https://raw.githubusercontent.com/owner/repo/main/apps.toml"
CATALOG = b'format = 1\nname = "Shared"\n[[app]]\nrepo = "someone/newapp"\n'


class AddressTests(unittest.TestCase):
    def test_accepted_forms_map_to_the_raw_address(self) -> None:
        for text in (
            "https://github.com/owner/repo/blob/main/apps.toml",
            "https://github.com/owner/repo/raw/main/apps.toml",
            "https://raw.githubusercontent.com/owner/repo/main/apps.toml",
            "github.com/owner/repo/blob/main/apps.toml",
            "www.github.com/owner/repo/blob/main/apps.toml",
            "http://github.com/owner/repo/blob/main/apps.toml",
            "http://www.github.com/owner/repo/blob/main/apps.toml",
            "raw.githubusercontent.com/owner/repo/main/apps.toml",
            "  https://github.com/owner/repo/blob/main/apps.toml?plain=1#L1  ",
            "HTTPS://GITHUB.COM/owner/repo/blob/main/apps.toml",
        ):
            with self.subTest(text=text):
                self.assertEqual(catalog_address(text), RAW)

    def test_folders_and_encoded_parts_are_kept(self) -> None:
        self.assertEqual(
            catalog_address("github.com/owner/repo/blob/v1.0/lists/my%20apps.toml"),
            "https://raw.githubusercontent.com/owner/repo/v1.0/lists/my%20apps.toml",
        )

    def test_refused_forms_raise_a_plain_sentence(self) -> None:
        for text in (
            "",
            "https://example.com/owner/repo/blob/main/apps.toml",
            "https://gitlab.com/owner/repo/blob/main/apps.toml",
            "https://user:pw@github.com/owner/repo/blob/main/apps.toml",
            "https://github.com:8080/owner/repo/blob/main/apps.toml",
            "ftp://github.com/owner/repo/blob/main/apps.toml",
            "https://github.com/owner/repo/blob/main/../apps.toml",
            "https://github.com/owner/repo/blob/main/./apps.toml",
            "https://github.com/owner/repo/blob/main/%2e%2e/apps.toml",
            "https://github.com/owner/repo/blob/main//apps.toml",
            "https://github.com/owner/repo/blob/main/apps\x00.toml",
            "https://github.com/owner/repo/blob/main/apps\n.toml",
            "github.com/owner/repo",
            "github.com/owner/repo/",
            "github.com/owner",
            "github.com/owner/repo/tree/main/lists",
            "github.com/owner/repo/blob/main",
            "github.com/owner/repo/releases/latest",
            "github.com/-bad/repo/blob/main/apps.toml",
            "github.com/owner/re po/blob/main/apps.toml",
            "github.com/owner/../blob/main/apps.toml",
            "raw.githubusercontent.com/owner/repo/main",
            "raw.githubusercontent.com/owner/repo",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError) as caught:
                catalog_address(text)
            self.assertTrue(str(caught.exception))

    def test_a_bare_repository_asks_for_the_file(self) -> None:
        with self.assertRaises(ValueError) as caught:
            catalog_address("github.com/owner/repo")
        self.assertIn("Link to the catalog file itself", str(caught.exception))


class Reply(BytesIO):
    def __enter__(self) -> "Reply":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def geturl(self) -> str:
        return RAW


class DownloadTests(unittest.TestCase):
    def opener(self, reply: object) -> object:
        class Opener:
            urls: list[str] = []

            def open(self, req, timeout=None):
                self.urls.append(req.full_url)
                if isinstance(reply, Exception):
                    raise reply
                return reply

        return Opener()

    def test_the_raw_address_is_requested_and_the_body_returned(self) -> None:
        opener = self.opener(Reply(CATALOG))
        with patch.object(http, "build_opener", return_value=opener):
            self.assertEqual(http.catalog_bytes(RAW, Event()), CATALOG)
        self.assertEqual(opener.urls, [RAW])

    def test_a_missing_file_has_its_own_message(self) -> None:
        error = HTTPError(RAW, 404, "Not Found", {}, BytesIO())  # type: ignore[arg-type]
        with (
            patch.object(http, "build_opener", return_value=self.opener(error)),
            self.assertRaises(ValueError) as caught,
        ):
            http.catalog_bytes(RAW, Event())
        self.assertEqual(str(caught.exception), "No catalog file was found there.")

    def test_other_statuses_keep_the_general_message(self) -> None:
        error = HTTPError(RAW, 500, "Oops", {}, BytesIO())  # type: ignore[arg-type]
        with (
            patch.object(http, "build_opener", return_value=self.opener(error)),
            self.assertRaises(ValueError) as caught,
        ):
            http.catalog_bytes(RAW, Event())
        self.assertIn("HTTP 500", str(caught.exception))

    def test_the_size_limit_holds(self) -> None:
        with (
            patch.object(
                http, "build_opener", return_value=self.opener(Reply(b"x" * (MAX_BYTES + 1)))
            ),
            self.assertRaises(ValueError),
        ):
            http.catalog_bytes(RAW, Event())
        with patch.object(http, "build_opener", return_value=self.opener(Reply(b"x" * MAX_BYTES))):
            self.assertEqual(len(http.catalog_bytes(RAW, Event())), MAX_BYTES)

    def test_offline_is_unreachable(self) -> None:
        from urllib.error import URLError

        with (
            patch.object(http, "build_opener", return_value=self.opener(URLError("down"))),
            self.assertRaises(http.Unreachable),
        ):
            http.catalog_bytes(RAW, Event())

    def test_a_cancelled_download_does_not_start(self) -> None:
        cancel = Event()
        cancel.set()
        with patch.object(http, "build_opener") as build, self.assertRaises(Cancelled):
            http.catalog_bytes(RAW, cancel)
        build.assert_not_called()

    def test_an_untrusted_address_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            http.catalog_bytes("https://example.com/apps.toml", Event())


class AddressRowTests(ux.SettingsBase):
    def wait(self, condition) -> None:
        for _ in range(200):
            self.qt.processEvents()
            if condition():
                return
            QTest.qWait(25)
        self.fail(f"timed out: {self.window.footer.text()}")

    def run_import(self, text: str, fetch) -> None:
        page = self.page
        page.address.setText(text)
        with patch("appglider.ui.pages.settings_page.catalog_bytes", fetch):
            page.address.returnPressed.emit()
            self.wait(lambda: not page.importing)

    def test_the_row_has_a_named_field_and_a_button_that_waits_for_text(self) -> None:
        page = self.page
        self.assertEqual(page.address.accessibleName(), "Catalog address")
        self.assertEqual(
            page.address.placeholderText(), "github.com/owner/repo/blob/main/apps.toml"
        )
        self.assertEqual(page.address_button.text(), "Import")
        self.assertFalse(page.address_button.isEnabled())
        page.address.setText("github.com/owner/repo/blob/main/apps.toml")
        self.assertTrue(page.address_button.isEnabled())
        with self.busy():
            page.update_state()
            self.assertFalse(page.address_button.isEnabled())
            self.assertFalse(page.address.isEnabled())
        page.update_state()
        self.assertTrue(page.address_button.isEnabled())
        page.address.clear()
        self.assertFalse(page.address_button.isEnabled())

    def test_an_address_downloads_previews_and_adds_the_apps(self) -> None:
        page = self.page
        before = len(self.window.apps)
        urls: list[str] = []

        def fetch(url: str, cancel: Event) -> bytes:
            urls.append(url)
            return CATALOG

        with patch.object(
            CatalogPreviewDialog, "exec", return_value=QDialog.DialogCode.Accepted
        ) as shown:
            self.run_import("github.com/owner/repo/blob/main/apps.toml", fetch)
        shown.assert_called_once()
        self.assertEqual(urls, [RAW])
        self.assertIn("Added 1 app from apps.toml.", page.catalog_note.text())
        self.assertEqual(page.address.text(), "")
        self.assertEqual(len(self.window.apps), before + 1)
        self.assertFalse(self.window.busy)

    def test_the_field_and_buttons_wait_while_downloading(self) -> None:
        page = self.page
        seen: list[tuple[bool, bool, bool, str]] = []

        def fetch(url: str, cancel: Event) -> bytes:
            seen.append(
                (
                    page.address.isEnabled(),
                    page.import_button.isEnabled(),
                    self.window.busy,
                    page.catalog_note.text(),
                )
            )
            return CATALOG

        with patch.object(CatalogPreviewDialog, "exec", return_value=QDialog.DialogCode.Rejected):
            self.run_import("github.com/owner/repo/blob/main/apps.toml", fetch)
        self.assertEqual(seen, [(False, False, True, "Downloading the catalog…")])
        self.assertTrue(page.address.isEnabled())
        self.assertEqual(page.address.text(), "github.com/owner/repo/blob/main/apps.toml")

    def test_an_invalid_address_shows_an_error_and_makes_no_request(self) -> None:
        page = self.page
        page.address.setText("https://example.com/apps.toml")
        with patch("appglider.ui.pages.settings_page.catalog_bytes") as fetch:
            page.address_button.click()
        fetch.assert_not_called()
        self.assertFalse(page.importing)
        self.assertEqual(page.catalog_note.objectName(), "settingError")
        self.assertTrue(page.catalog_note.text().startswith("Cannot import from that address: "))

    def test_a_missing_file_shows_the_error(self) -> None:
        page = self.page

        def fetch(url: str, cancel: Event) -> bytes:
            raise ValueError("No catalog file was found there.")

        self.run_import("github.com/owner/repo/blob/main/apps.toml", fetch)
        self.assertEqual(
            page.catalog_note.text(),
            "Cannot import from that address: No catalog file was found there.",
        )
        self.assertEqual(page.catalog_note.objectName(), "settingError")
        self.assertTrue(page.address.isEnabled())

    def test_a_file_that_is_not_a_catalog_shows_the_error(self) -> None:
        page = self.page
        self.run_import("github.com/owner/repo/blob/main/apps.toml", lambda u, c: b"not = [valid")
        self.assertTrue(page.catalog_note.text().startswith("Cannot import from that address: "))
        self.assertEqual(page.catalog_note.objectName(), "settingError")

    def test_nothing_starts_while_the_window_is_busy(self) -> None:
        page = self.page
        page.address.setText("github.com/owner/repo/blob/main/apps.toml")
        with (
            self.busy(),
            patch("appglider.ui.pages.settings_page.catalog_bytes") as fetch,
        ):
            page.import_address()
        fetch.assert_not_called()
        self.assertFalse(page.importing)


if __name__ == "__main__":
    unittest.main()
