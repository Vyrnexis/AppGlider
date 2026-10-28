"""Discover both release channels and retain verified information for offline cards."""

import contextlib
import json
import re
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from threading import Event
from typing import Any, Literal

from appglider.core.catalog import App
from appglider.core.storage import atomic_json
from appglider.github import assets, download, http, pages, versions
from appglider.github import status as github_status
from appglider.github.assets import Release
from appglider.github.versions import VERSION, check_cancel, release_version, version_key


@dataclass(frozen=True)
class ReleaseOption:
    version: str
    release: Release | None
    error: str = ""
    build_required: bool = False


@dataclass(frozen=True)
class ReleaseInfo:
    stable: ReleaseOption | None
    preview: ReleaseOption | None
    checked_at: float
    newer_stable: ReleaseOption | None = None
    build_variant: str = ""

    def choice(self, include_previews: bool) -> ReleaseOption | None:
        choices = (
            [self.stable, self.preview, self.newer_stable] if include_previews else [self.stable]
        )
        return max(
            (choice for choice in choices if choice is not None),
            key=lambda choice: version_key(choice.version),
            default=None,
        )

    def error(self, include_previews: bool) -> str:
        choice = self.choice(include_previews)
        if choice is not None:
            return choice.error
        return "Only prereleases are available. Choose Stable + previews to use them."

    def fresh(self) -> bool:
        return 0 <= time.time() - self.checked_at < download.CACHE_SECONDS


def info_path(app: App, cache_dir: Path) -> Path:
    return (
        cache_dir
        / f"{app.id}-{versions.architecture()}{assets.build_cache_suffix(app)}-catalog.json"
    )


def cached_info(app: App, cache_dir: Path) -> ReleaseInfo | None:
    try:
        path = info_path(app, cache_dir)
        if path.stat().st_size > http.MAX_METADATA:
            return None
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or data.get("build_variant", "") != app.build_variant:
            return None
        checked_at = data["checked_at"]
        if type(checked_at) not in (int, float) or not 0 < checked_at <= time.time() + 300:
            return None
        options: list[ReleaseOption | None] = []
        for channel, prerelease in (("stable", False), ("preview", True), ("newer_stable", False)):
            item = data.get(channel)
            if item is None:
                options.append(None)
                continue
            version, error = item["version"], item["error"]
            build_required = item.get("build_required", False)
            if (
                not isinstance(version, str)
                or len(version) > 128
                or not re.fullmatch(VERSION, version)
                or not isinstance(error, str)
                or len(error) > 4096
                or type(build_required) is not bool
            ):
                return None
            release = (
                download.release_from_cache(replace(app, include_prereleases=True), item["release"])
                if item["release"] is not None
                else None
            )
            if release is None and not error:
                return None
            if release and (release.version != version or release.prerelease != prerelease):
                return None
            if release and build_required:
                return None
            options.append(ReleaseOption(version, release, error, build_required))
        if not any(options):
            return None
        return ReleaseInfo(options[0], options[1], checked_at, options[2], app.build_variant)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def remember_release(app: App, release: Release, cache_dir: Path) -> ReleaseInfo | None:
    info = cached_info(app, cache_dir)
    if info is None:
        return None
    option = ReleaseOption(release.version, release)
    if release.prerelease:
        info = replace(info, preview=option)
    elif release.is_latest:
        info = replace(info, stable=option)
    else:
        info = replace(info, newer_stable=option)
    # Only this channel was rechecked; keep the catalog timestamp conservative.
    with contextlib.suppress(OSError):
        atomic_json(info_path(app, cache_dir), asdict(info))
    return info


def inspect_entries(
    app: App, entries: list[Any], cancel: Event, *, public: bool = False
) -> ReleaseInfo:
    options: list[ReleaseOption | None] = []
    version_errors: list[ValueError] = []
    for channel in ("stable", "preview", "newer_stable"):
        prerelease = channel == "preview"
        candidates = [
            entry
            for entry in entries
            if isinstance(entry, dict)
            and not entry.get("draft")
            and entry.get("prerelease", False) is prerelease
        ]
        if channel == "stable":
            designated = [entry for entry in candidates if entry.get("is_latest")]
            candidates = designated or candidates
        if not candidates:
            options.append(None)
            continue
        try:
            data = assets.newest_release(candidates, True)
        except ValueError as error:
            version_errors.append(error)
            options.append(None)
            continue
        version = release_version(data["tag_name"])
        assert version is not None  # newest_release only returns recognised versions.
        if channel == "newer_stable" and (
            options[0] is None or version_key(version) <= version_key(options[0].version)
        ):
            options.append(None)
            continue
        try:
            check_cancel(cancel)
            if public:
                release = pages.page_release_asset(app, data, cancel)
            else:
                release = assets.select_release(app, data, versions.architecture())
                release = assets.with_checksum(app, data["tag_name"], release, cancel)
        except http.RateLimited:
            raise
        except assets.BuildChoiceRequired as error:
            options.append(ReleaseOption(version, None, str(error)[:4096], True))
        except ValueError as error:
            options.append(ReleaseOption(version, None, str(error)[:4096]))
        else:
            options.append(ReleaseOption(version, release))
    if not any(options):
        if version_errors:
            raise version_errors[0]
        raise ValueError("No published release with a supported version was found.")
    return ReleaseInfo(options[0], options[1], time.time(), options[2], app.build_variant)


def unchanged_source(
    app: App, cached: ReleaseInfo, cache_dir: Path, cancel: Event
) -> Literal["GitHub release page", "GitHub release feed"] | None:
    """Say how we know nothing new was published, or None when that cannot be shown.

    A stable-only app compares GitHub's "Latest" page with the stable release we know. An app
    that includes previews compares the newest version in the public release feed with the
    newest version we know. Neither uses the API allowance.
    """
    if not app.include_prereleases:
        if cached.stable is None:
            return None
        tag = http.latest_tag(app.repo, cancel)
        if tag is not None and release_version(tag) == cached.stable.version:
            return "GitHub release page"
        return None
    known = [
        option.version
        for option in (cached.stable, cached.preview, cached.newer_stable)
        if option is not None
    ]
    tags = http.feed_tags(app, cache_dir, cancel) if known else None
    versions = [v for v in (release_version(tag) for tag in tags or []) if v]
    if versions and version_key(max(versions, key=version_key)) <= (
        version_key(max(known, key=version_key))
    ):
        return "GitHub release feed"
    return None


def discover_releases(
    app: App, cancel: Event, cache_dir: Path, *, force: bool = False, cheap: bool = False
) -> ReleaseInfo:
    """Find the releases of an app.

    With `cheap`, an app whose release information is already saved is first checked without
    the API: stable-only apps by where GitHub's "Latest" page redirects, apps with previews by
    GitHub's public release feed. The API is asked only when something newer appeared or the
    answer was unclear.
    """
    check_cancel(cancel)
    cached = cached_info(app, cache_dir)
    if not force and cached and cached.fresh():
        github_status.release_result("Cached information", app.repo, cached.checked_at)
        return cached
    source = unchanged_source(app, cached, cache_dir, cancel) if cheap and cached else None
    if cached and source:
        info = replace(cached, checked_at=time.time())
        github_status.release_result(source, app.repo, info.checked_at)
        with contextlib.suppress(OSError):
            atomic_json(info_path(app, cache_dir), asdict(info))
        return info
    http.restore_rate_limits(cache_dir)
    app = replace(app, include_prereleases=True)
    try:
        info = inspect_entries(app, assets.api_entries(app, cancel), cancel)
        github_status.release_result("GitHub API", app.repo, info.checked_at)
    except http.RateLimited:
        http.save_rate_limits(cache_dir)
        info = inspect_entries(app, pages.page_entries(app, cancel), cancel, public=True)
        github_status.release_result("Public GitHub pages", app.repo, info.checked_at)
    check_cancel(cancel)
    with contextlib.suppress(OSError):
        atomic_json(info_path(app, cache_dir), asdict(info))
        # Keep the existing download cache format and its five-minute safety window.
        for include in (False, True):
            choice = info.choice(include)
            if choice and choice.release:
                atomic_json(
                    download.release_cache_path(
                        replace(app, include_prereleases=include), cache_dir
                    ),
                    {"checked_at": info.checked_at, "release": asdict(choice.release)},
                )
            else:
                download.release_cache_path(
                    replace(app, include_prereleases=include), cache_dir
                ).unlink(missing_ok=True)
    return info
