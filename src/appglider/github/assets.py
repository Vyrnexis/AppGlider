"""Release data, AppImage asset and build selection, checksums and the REST API."""

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import date
from threading import Event
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from appglider.core.catalog import App
from appglider.github import http, versions
from appglider.github.http import MissingResource
from appglider.github.versions import check_cancel, release_version, stems, version_key

LISTING_PAGE_SIZE = 20


MAX_NOTES = 20_000


MAX_DOWNLOAD = 4 * 1024 * 1024 * 1024


ASSET_ARCHITECTURES = {
    "x86_64": ("x86_64", "x86-64", "x64", "amd64"),
    "aarch64": ("aarch64", "arm64"),
    "x86": ("x86", "i386", "i486", "i586", "i686", "ia32"),
    "arm": ("arm", "armhf", "armel", "armv6l", "armv7", "armv7l", "armv7hf"),
    "riscv64": ("riscv64",),
    "ppc64": ("ppc64", "ppc64le"),
    "s390x": ("s390x",),
}


ARCH_LABELS = {alias: arch for arch, aliases in ASSET_ARCHITECTURES.items() for alias in aliases}


ARCH_PATTERN = re.compile(
    r"(?<![a-z0-9])("
    + "|".join(map(re.escape, sorted(ARCH_LABELS, key=len, reverse=True)))
    + r")(?![a-z0-9])",
    re.I,
)


@dataclass(frozen=True)
class Release:
    version: str
    name: str
    url: str
    size: int
    sha256: str
    prerelease: bool = False
    is_latest: bool = True
    notes: str = ""
    published: str = ""

    def newer_than(self, installed_version: str | None) -> bool:
        return installed_version is not None and version_key(self.version) > version_key(
            installed_version
        )


def preferred_asset_names(app: App, version: str, arch: str) -> Iterator[str]:
    for stem in stems(app):
        for filename_arch in ASSET_ARCHITECTURES.get(arch, (arch,)):
            yield f"{stem}-{version}-linux-{filename_arch}.AppImage"
            yield f"{stem}-linux-{filename_arch}.AppImage"
            yield f"{stem}-{version}-{filename_arch}.AppImage"
            yield f"{stem}-{filename_arch}.AppImage"


def asset_variant(name: str, version: str, *, keep_timestamp: bool = False) -> str:
    # Keep distribution/edition text; only the release version and build date may change.
    pattern = name.replace(version, "{version}")
    if not keep_timestamp:
        pattern = re.sub(r"(?<=[_.-])\d{8}(?:\d{6})?(?=[_.-]|$)", "{timestamp}", pattern)
    return pattern


def variant_label(pattern: str) -> str:
    label = re.sub(r"[_.-]*v?\{version\}|[_.-]*\{timestamp\}|\.appimage$", "", pattern, flags=re.I)
    return re.sub(r"ubuntu(?=\d)", "Ubuntu ", label, flags=re.I).replace("_", " ").strip("- .")


def build_cache_suffix(app: App) -> str:
    return (
        "-build-" + hashlib.sha256(app.build_variant.encode()).hexdigest()[:16]
        if app.build_variant
        else ""
    )


class BuildChoiceRequired(ValueError):
    def __init__(
        self,
        app: App,
        assets: list[dict[str, Any]],
        version: str,
        arch: str,
        *,
        missing: bool = False,
    ) -> None:
        self.app_id = app.id
        self.missing = missing
        patterns = [asset_variant(asset["name"], version) for asset in assets]
        counts = Counter(pattern.casefold() for pattern in patterns)
        self.choices = tuple(
            (
                asset_variant(
                    asset["name"], version, keep_timestamp=counts[pattern.casefold()] > 1
                ),
                asset["name"],
            )
            for asset, pattern in zip(assets, patterns, strict=True)
        )
        names = ", ".join(asset["name"] for asset in assets[:5])
        super().__init__(
            "The saved build is unavailable. Choose a build again: " + names
            if missing
            else f"Multiple AppImages match {arch}; cannot choose safely: {names}"
        )


def select_asset(app: App, assets: list[Any], version: str, arch: str) -> dict[str, Any]:
    labelled = []
    unlabelled = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        if not isinstance(name, str) or not name.casefold().endswith(".appimage"):
            continue
        if len(name) > 512 or any(ord(c) < 32 or ord(c) == 127 or c in "/\\" for c in name):
            raise ValueError("The release has an invalid AppImage filename.")
        architectures = {ARCH_LABELS[m[0].lower()] for m in ARCH_PATTERN.finditer(name)}
        if architectures == {arch}:
            labelled.append(asset)
        elif not architectures:
            unlabelled.append(asset)
    candidates = labelled or unlabelled
    if not candidates:
        raise ValueError(f"No supported {arch} AppImage filename was found in this release.")
    if app.build_variant:
        variants = [
            {
                asset_variant(asset["name"], version),
                asset_variant(asset["name"], version, keep_timestamp=True),
            }
            for asset in candidates
        ]
        selected = [
            asset
            for asset, patterns in zip(candidates, variants, strict=True)
            if app.build_variant in patterns
        ]
        if not selected:
            selected = [
                asset
                for asset, patterns in zip(candidates, variants, strict=True)
                if app.build_variant.casefold() in {pattern.casefold() for pattern in patterns}
            ]
        if len(selected) == 1:
            return selected[0]
        raise BuildChoiceRequired(app, candidates, version, arch, missing=not selected)
    for expected in preferred_asset_names(app, version, arch):
        preferred = [a for a in candidates if a["name"].casefold() == expected.casefold()]
        if preferred:
            candidates = preferred
            break
    if len(candidates) != 1:
        raise BuildChoiceRequired(app, candidates, version, arch)
    return candidates[0]


def select_release(app: App, data: object, arch: str) -> Release:
    if not isinstance(data, dict) or data.get("draft"):
        raise ValueError("No published release is available.")
    prerelease = data.get("prerelease", False)
    is_latest = data.get("is_latest", not prerelease)
    if type(prerelease) is not bool or type(is_latest) is not bool:
        raise ValueError("GitHub returned invalid release flags.")
    if prerelease and not app.include_prereleases:
        raise ValueError(
            "Only prereleases are available. Choose 'Stable and prerelease versions' to use them."
        )
    tag = data.get("tag_name")
    version = release_version(tag)
    if version is None:
        raise ValueError(
            "This release's version tag is unsupported; expected e.g. v1.2.3 or studio-v0.6.0."
        )
    assets = data.get("assets")
    if not isinstance(assets, list):
        raise ValueError("This release has no downloadable files.")
    asset = select_asset(app, assets, version, arch)
    name = asset["name"]
    url = asset.get("browser_download_url")
    prefix = f"https://github.com/{app.repo}/releases/download/"
    if (
        not isinstance(url, str)
        or not url.lower().startswith(prefix.lower())
        or unquote(url[len(prefix) :]) != f"{tag}/{name}"
        or urlsplit(url).query
        or urlsplit(url).fragment
        or any(c.isspace() for c in url)
    ):
        raise ValueError("The release's AppImage URL does not match its repository.")
    size = asset.get("size")
    if type(size) is not int or not 0 < size <= MAX_DOWNLOAD:
        raise ValueError("The AppImage has an invalid download size.")
    digest = asset.get("digest", "")
    sha256 = digest[7:] if isinstance(digest, str) and digest.startswith("sha256:") else ""
    if not re.fullmatch(r"[a-fA-F0-9]{64}", sha256):
        sha256 = ""
    return Release(
        version,
        name,
        url,
        size,
        sha256.lower(),
        prerelease,
        is_latest and not prerelease,
        release_notes(data.get("body")),
        published_date(data.get("published_at")),
    )


def release_notes(value: object) -> str:
    """Return the release description, bounded; anything that is not text is ignored."""
    return value[:MAX_NOTES] if isinstance(value, str) else ""


def published_date(value: object) -> str:
    """Return the YYYY-MM-DD date of an ISO timestamp, or an empty string when invalid."""
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ][\w:.+-]{0,32})?", value):
        try:
            date.fromisoformat(value[:10])
        except ValueError:
            return ""
        return value[:10]
    return ""


def checksums(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or not re.fullmatch(r"[a-fA-F0-9]{64}", parts[0]):
            continue
        name, digest = parts[1].lstrip("*"), parts[0].lower()
        if name in result and result[name] != digest:
            raise ValueError("The release publishes conflicting SHA-256 checksums.")
        result[name] = digest
    return result


def with_checksum(app: App, tag: str, release: Release, cancel: Event) -> Release:
    if release.sha256:
        return release
    url = f"https://github.com/{app.repo}/releases/download/{quote(tag, safe='')}/SHA256SUMS.txt"
    check_cancel(cancel)
    try:
        manifest = http.read_metadata(url).decode("utf-8")
    except MissingResource as error:
        raise ValueError(
            "No GitHub SHA-256 digest or SHA256SUMS.txt is available for this AppImage."
        ) from error
    digest = checksums(manifest).get(release.name)
    if digest is None:
        raise ValueError("No published SHA-256 checksum was found for this AppImage.")
    return replace(release, sha256=digest)


def newest_release(entries: list[Any], include_prereleases: bool) -> dict[str, Any]:
    published = [entry for entry in entries if isinstance(entry, dict) and not entry.get("draft")]
    eligible = [entry for entry in published if include_prereleases or not entry.get("prerelease")]
    if not eligible and any(entry.get("prerelease") for entry in published):
        raise ValueError(
            "Only prereleases are available. Choose 'Stable and prerelease versions' to use them."
        )
    recognised = [entry for entry in eligible if release_version(entry.get("tag_name")) is not None]
    if not recognised:
        if eligible:
            appimage = any(
                isinstance(asset, dict)
                and isinstance(asset.get("name"), str)
                and asset["name"].lower().endswith(".appimage")
                for entry in eligible
                if isinstance(entry.get("assets"), list)
                for asset in entry["assets"]
            )
            found = "An AppImage was found" if appimage else "Published releases were found"
            raise ValueError(f"{found}, but no release has a supported version tag.")
        raise ValueError("No published release with a supported version was found.")
    return max(recognised, key=lambda entry: version_key(release_version(entry["tag_name"])))


def api_entries(app: App, cancel: Event) -> list[Any]:
    check_cancel(cancel)
    prefix = f"https://api.github.com/repos/{app.repo}/releases"
    try:
        stable = json.loads(http.read_metadata(prefix + "/latest"))
    except MissingResource:
        stable = None
    if stable is not None and not isinstance(stable, dict):
        raise ValueError("GitHub returned invalid release metadata.")
    if not app.include_prereleases and stable is not None:
        return [{**stable, "is_latest": True}]
    check_cancel(cancel)
    # One request for the newest releases; asset-heavy nightly repositories make full pages huge.
    items = json.loads(http.read_metadata(f"{prefix}?per_page={LISTING_PAGE_SIZE}&page=1"))
    if not isinstance(items, list):
        raise ValueError("GitHub returned an invalid release listing.")
    entries: list[Any] = []
    for item in items:
        if isinstance(item, dict):
            entries.append(
                {
                    **item,
                    "is_latest": bool(stable and item.get("tag_name") == stable.get("tag_name")),
                }
            )
    if stable is not None:
        entries.append({**stable, "is_latest": True})
    return entries


def api_release(app: App, cancel: Event) -> Release:
    data = newest_release(api_entries(app, cancel), app.include_prereleases)
    release = select_release(app, data, versions.architecture())
    return with_checksum(app, str(data["tag_name"]), release, cancel)
