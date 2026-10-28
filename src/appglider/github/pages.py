"""Release discovery from GitHub's public pages when the API is unavailable."""

import re
from dataclasses import replace
from html.parser import HTMLParser
from threading import Event
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlsplit

from appglider.core.catalog import App
from appglider.github import http, versions
from appglider.github.assets import (
    MAX_DOWNLOAD,
    Release,
    newest_release,
    select_release,
    with_checksum,
)
from appglider.github.http import MissingResource
from appglider.github.versions import check_cancel

RELEASE_PAGES = 3


class ReleaseListParser(HTMLParser):
    """Read release headers, excluding labels and links in release notes."""

    def __init__(self, app: App) -> None:
        super().__init__()
        self.tag_prefix = f"{app.source_url}/releases/tag/"
        self.entries: list[dict[str, Any]] = []
        self.current: dict[str, Any] | None = None
        self.section_depth = 0
        self.header = False
        self.label = False
        self.owned_tag = False
        self.next_page = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "a" and attributes.get("rel") == "next":
            self.next_page = True
        if tag == "section":
            anchor = attributes.get("data-release-anchor") or ""
            if self.current is not None:
                self.section_depth += 1
            elif anchor.startswith("release-"):
                self.current = {
                    "tag_name": anchor[8:],
                    "prerelease": False,
                    "is_latest": False,
                    "draft": False,
                }
                self.section_depth = 1
                self.header = True
                self.owned_tag = False
        if self.current is None or not self.header:
            return
        classes = (attributes.get("class") or "").split()
        if "markdown-body" in classes:
            self.header = False
        if tag == "a":
            url = urljoin("https://github.com", attributes.get("href") or "")
            if url.lower().startswith(self.tag_prefix.lower()):
                self.owned_tag |= unquote(url[len(self.tag_prefix) :]) == self.current["tag_name"]
        if tag == "span" and "Label" in classes:
            self.label = True

    def handle_data(self, data: str) -> None:
        if self.current is not None and self.header and self.label:
            key = {"Pre-release": "prerelease", "Latest": "is_latest", "Draft": "draft"}.get(
                data.strip()
            )
            if key:
                self.current[key] = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "span":
            self.label = False
        if tag == "section" and self.current is not None:
            self.section_depth -= 1
            if not self.section_depth:
                if self.owned_tag:
                    self.entries.append(self.current)
                self.current = None
                self.label = False


class ReleaseAssetsParser(HTMLParser):
    """Read GitHub's asset links and filename-labelled digest controls."""

    def __init__(self, download_prefix: str) -> None:
        super().__init__()
        self.download_prefix = download_prefix
        self.assets: dict[str, dict[str, Any]] = {}
        self.digests: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "a":
            url = urljoin("https://github.com", attributes.get("href") or "")
            # Compare decoded paths so a tag containing "/" matches whether GitHub links it
            # as "app/v1" or "app%2Fv1".
            decoded, prefix = unquote(url), unquote(self.download_prefix)
            if decoded.lower().startswith(prefix.lower()):
                name = decoded[len(prefix) :]
                if name in self.assets and self.assets[name]["browser_download_url"] != url:
                    raise ValueError("GitHub returned conflicting asset download links.")
                # GitHub displays rounded sizes; HEAD supplies the exact size after selection.
                self.assets[name] = {"name": name, "browser_download_url": url, "size": 1}
        if tag == "clipboard-copy":
            label = attributes.get("aria-label") or ""
            prefix = "Copy to clipboard digest for "
            digest = attributes.get("value") or ""
            if label.startswith(prefix) and re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest):
                name = label[len(prefix) :]
                if name in self.digests and self.digests[name] != digest.lower():
                    raise ValueError("GitHub returned conflicting SHA-256 digests for an asset.")
                self.digests[name] = digest.lower()


def page_entries(app: App, cancel: Event) -> list[Any]:
    """Public release URLs remain usable when the REST API quota is exhausted."""
    check_cancel(cancel)
    try:
        with http.request(f"https://github.com/{app.repo}/releases/latest") as response:
            location = str(response.geturl())
    except MissingResource:
        location = ""
    if location.rstrip("/").casefold() == f"{app.source_url}/releases".casefold():
        location = ""
    prefix = f"https://github.com/{app.repo}/releases/tag/"
    if location and not location.lower().startswith(prefix.lower()):
        raise ValueError("GitHub did not identify a stable release for this repository.")
    tag = unquote(location[len(prefix) :])
    if tag and (
        len(tag) > 128
        or any(c.isspace() or ord(c) < 32 for c in tag)
        or urlsplit(location).query
        or urlsplit(location).fragment
    ):
        raise ValueError("GitHub returned an invalid release tag.")
    data: dict[str, Any] = {"tag_name": tag, "prerelease": False, "is_latest": True}
    if app.include_prereleases or not tag:
        entries: list[Any] = []
        for page in range(1, RELEASE_PAGES + 1):
            check_cancel(cancel)
            parser_list = ReleaseListParser(app)
            url = f"{app.source_url}/releases" + (f"?page={page}" if page > 1 else "")
            parser_list.feed(http.read_metadata(url).decode("utf-8"))
            parser_list.close()
            if not parser_list.entries:
                raise ValueError("Could not read GitHub's public release listing; retry later.")
            entries.extend(parser_list.entries)
            if not parser_list.next_page:
                break
        if tag:
            entries.append(data)
        return entries
    return [data]


def page_release(app: App, cancel: Event) -> Release:
    data = newest_release(page_entries(app, cancel), app.include_prereleases)
    return page_release_asset(app, data, cancel)


def page_release_asset(app: App, data: dict[str, Any], cancel: Event) -> Release:
    tag = data["tag_name"]
    download_prefix = f"https://github.com/{app.repo}/releases/download/{quote(tag, safe='')}/"
    check_cancel(cancel)
    parser = ReleaseAssetsParser(download_prefix)
    parser.feed(
        http.read_metadata(
            f"https://github.com/{app.repo}/releases/expanded_assets/{quote(tag, safe='')}"
        ).decode("utf-8")
    )
    parser.close()
    if not parser.assets:
        raise ValueError("Could not read GitHub's public asset listing; retry discovery later.")
    assets = [
        {**asset, "digest": parser.digests.get(name, "")} for name, asset in parser.assets.items()
    ]
    release = select_release(app, {**data, "assets": assets}, versions.architecture())
    release = with_checksum(app, tag, release, cancel)
    check_cancel(cancel)
    with http.request(release.url, method="HEAD") as response:
        length = response.headers.get("Content-Length", "")
    if not length.isdigit() or len(length) > 12:
        raise ValueError("GitHub did not provide the AppImage's download size.")
    size = int(length)
    if not 0 < size <= MAX_DOWNLOAD:
        raise ValueError("The AppImage has an invalid download size.")
    return replace(release, size=size)
