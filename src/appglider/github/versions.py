"""Release tag parsing, version ordering and cancellation helpers."""

import platform
import re
from threading import Event

from appglider.core.catalog import App

VERSION = r"[0-9]+(?:\.[0-9]+){1,3}(?:-[A-Za-z0-9.-]+)?(?:\+[A-Za-z0-9.-]+)?"


class Cancelled(Exception):
    """The user cancelled an operation before activation."""


def check_cancel(cancel: Event) -> None:
    if cancel.is_set():
        raise Cancelled("Operation cancelled. Your installed version was kept.")


def architecture() -> str:
    machine = platform.machine().lower()
    aliases = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}
    if machine not in aliases:
        raise ValueError(f"AppImage downloads are not supported on {machine} yet.")
    return aliases[machine]


def stems(app: App) -> tuple[str, ...]:
    stem = app.asset_stem or app.id
    former = {"pdfcraft": "printcraft", "solidcraft": "solvecraft"}
    if app.id in former:
        return (stem, former[app.id])
    # A moved repository keeps its first id, whose name its files often still use.
    first = app.id.partition("__")[2] if app.id.startswith("github-") else ""
    return (stem, first) if first and first != stem else (stem,)


def release_version(tag: object) -> str | None:
    """Extract a comparable version without changing the tag used in GitHub URLs."""
    if not isinstance(tag, str) or len(tag) > 128:
        return None
    match = re.fullmatch(rf"(?:[A-Za-z][A-Za-z0-9_-]*[-_/])?[vV]?({VERSION})", tag)
    return match[1] if match else None


def prerelease_date(parts: list[str]) -> int:
    """The first YYYYMMDD part of a prerelease suffix, or 0 when it has none."""
    for part in parts:
        if len(part) == 8 and part.isdigit():
            year, month, day = int(part[:4]), int(part[4:6]), int(part[6:])
            if 1970 <= year <= 2199 and 1 <= month <= 12 and 1 <= day <= 31:
                return int(part)
    return 0


def version_key(
    version: str | None,
) -> tuple[int, int, int, int, int, int, tuple[tuple[int, int, str], ...]]:
    """Order versions; prereleases of one version by date when their tags carry one.

    Labels sort by name (alpha < beta < rc), so a project renaming its test builds, such as
    "preview" to "nightly", would otherwise make every newer build look older.
    """
    match = re.fullmatch(
        r"(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([^+]+))?(?:\+.*)?", version or ""
    )
    if not match:
        return (0, 0, 0, 0, 0, 0, ())
    suffix = match[5] or ""
    parts = [part for part in suffix.split(".") if part]
    identifiers = tuple((0, int(part), "") if part.isdigit() else (1, 0, part) for part in parts)
    return (
        int(match[1]),
        int(match[2]),
        int(match[3] or 0),
        int(match[4] or 0),
        int(not suffix),
        prerelease_date(parts),
        identifiers,
    )
