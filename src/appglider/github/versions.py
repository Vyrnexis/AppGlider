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


def version_key(
    version: str | None,
) -> tuple[int, int, int, int, int, tuple[tuple[int, int, str], ...]]:
    match = re.fullmatch(
        r"(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([^+]+))?(?:\+.*)?", version or ""
    )
    if not match:
        return (0, 0, 0, 0, 0, ())
    suffix = match[5] or ""
    identifiers = tuple(
        (0, int(part), "") if part.isdigit() else (1, 0, part) for part in suffix.split(".") if part
    )
    return (
        int(match[1]),
        int(match[2]),
        int(match[3] or 0),
        int(match[4] or 0),
        int(not suffix),
        identifiers,
    )
