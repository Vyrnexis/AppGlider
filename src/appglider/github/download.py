"""Cached release lookup and verified, cancellable AppImage downloads."""

import contextlib
import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import asdict
from http.client import HTTPException
from pathlib import Path
from threading import Event
from typing import Any
from urllib.parse import unquote

from appglider.core.catalog import App
from appglider.core.storage import atomic_json
from appglider.github import assets, http, pages, versions
from appglider.github import status as github_status
from appglider.github.assets import MAX_DOWNLOAD, Release, build_cache_suffix, select_release
from appglider.github.http import MAX_METADATA, RateLimited, restore_rate_limits, save_rate_limits
from appglider.github.versions import check_cancel

CACHE_SECONDS = 5 * 60


DOWNLOAD_CHUNK = 1024 * 1024


PROGRESS_INTERVAL = 0.1


Progress = Callable[[str, int, int], None]


def cached_release(app: App, cache_dir: Path) -> Release | None:
    try:
        path = release_cache_path(app, cache_dir)
        if path.stat().st_size > MAX_METADATA:
            return None
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return None
        checked_at = data.get("checked_at")
        if (
            not isinstance(checked_at, (float, int))
            or isinstance(checked_at, bool)
            or not 0 <= time.time() - checked_at < CACHE_SECONDS
        ):
            return None
        release = release_from_cache(app, data["release"])
        github_status.release_result("Cached information", app.repo, checked_at)
        return release
    except (OSError, ValueError, KeyError, TypeError):
        return None


def release_from_cache(app: App, item: Any) -> Release:
    prefix = f"https://github.com/{app.repo}/releases/download/"
    url = item["url"]
    if not isinstance(url, str) or not url.lower().startswith(prefix.lower()):
        raise ValueError("Invalid cached download URL.")
    tag = unquote(url[len(prefix) :].split("/", 1)[0])
    release = select_release(
        app,
        {
            "tag_name": tag,
            "prerelease": item.get("prerelease", False),
            "is_latest": item.get("is_latest", True),
            "body": item.get("notes", ""),
            "published_at": item.get("published", ""),
            "assets": [
                {
                    "name": item["name"],
                    "browser_download_url": url,
                    "size": item["size"],
                    "digest": "sha256:" + item["sha256"],
                }
            ],
        },
        versions.architecture(),
    )
    if not release.sha256 or release.version != item["version"]:
        raise ValueError("Invalid cached release.")
    return release


def release_cache_path(app: App, cache_dir: Path) -> Path:
    suffix = "-prereleases" if app.include_prereleases else ""
    return cache_dir / f"{app.id}-{versions.architecture()}{suffix}{build_cache_suffix(app)}.json"


def latest_release(app: App, cancel: Event, cache_dir: Path | None = None) -> Release:
    check_cancel(cancel)
    if cache_dir is not None:
        cached = cached_release(app, cache_dir)
        if cached is not None:
            return cached
        restore_rate_limits(cache_dir)
    try:
        release = assets.api_release(app, cancel)
        github_status.release_result("GitHub API", app.repo, time.time())
    except RateLimited:
        if cache_dir is not None:
            save_rate_limits(cache_dir)
        release = pages.page_release(app, cancel)
        github_status.release_result("Public GitHub pages", app.repo, time.time())
    check_cancel(cancel)
    if cache_dir is not None:
        with contextlib.suppress(OSError):
            atomic_json(
                release_cache_path(app, cache_dir),
                {"checked_at": time.time(), "release": asdict(release)},
            )
    return release


def download(release: Release, destination: Path, cancel: Event, progress: Progress) -> None:
    if not re.fullmatch(r"[a-f0-9]{64}", release.sha256):
        raise ValueError("A published SHA-256 checksum is required before downloading.")
    digest = hashlib.sha256()
    received = 0
    last_report = 0.0
    with http.request(release.url) as response, destination.open("wb") as output:
        headers = getattr(response, "headers", None)
        length = headers.get("Content-Length") if headers is not None else None
        if length is not None and length.isdigit() and int(length) != release.size:
            raise ValueError("The download size did not match the published size.")
        while True:
            check_cancel(cancel)
            try:
                chunk = response.read(DOWNLOAD_CHUNK)
            except (OSError, HTTPException) as error:
                raise ValueError(
                    "The download was interrupted. The installed version was kept."
                ) from error
            if not chunk:
                break
            received += len(chunk)
            if received > release.size or received > MAX_DOWNLOAD:
                raise ValueError("The download exceeded its published size.")
            digest.update(chunk)
            output.write(chunk)
            now = time.monotonic()
            if now - last_report >= PROGRESS_INTERVAL:
                last_report = now
                progress("Downloading AppImage", received, release.size)
        progress("Downloading AppImage", received, release.size)
        output.flush()
        os.fsync(output.fileno())
    check_cancel(cancel)
    if received != release.size:
        raise ValueError("The download was incomplete. The installed version was kept.")
    if digest.hexdigest() != release.sha256:
        raise ValueError("SHA-256 verification failed. The installed version was kept.")
