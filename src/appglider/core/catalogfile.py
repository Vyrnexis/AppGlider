"""Plain-text TOML catalogs: read, validate and write lists of GitHub repositories."""

import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from appglider.core.catalog import MAX_DESCRIPTION, App, custom_app, default_description

EXAMPLE_CATALOG = Path(__file__).parent.parent / "assets" / "catalogs" / "artcraft.toml"
FORMAT = 1
MAX_BYTES = 256 * 1024
MAX_APPS = 100

TOP_KEYS = {"format", "name", "app"}
APP_KEYS = {"repo", "name", "description", "stable_only", "build"}


class CatalogError(ValueError):
    """The file as a whole cannot be used; nothing from it should be imported."""


@dataclass(frozen=True)
class CatalogEntry:
    repo: str
    name: str
    description: str = ""
    stable_only: bool = True
    build: str = ""


@dataclass(frozen=True)
class ParsedCatalog:
    name: str
    entries: tuple[CatalogEntry, ...]
    skipped: tuple[str, ...]
    ignored: tuple[str, ...]


def _plain(value: str, limit: int) -> bool:
    return 0 < len(value) <= limit and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def _entry(number: int, table: object, seen: set[str]) -> tuple[CatalogEntry | None, str]:
    label = f"app {number}"
    if not isinstance(table, dict):
        return None, f"{label}: expected a table"
    repo = table.get("repo")
    if not isinstance(repo, str):
        return None, f"{label}: repo is required and must be text"
    label = f"{label} ({repo[:80]})"
    name = table.get("name")
    description = table.get("description", "")
    stable_only = table.get("stable_only", True)
    build = table.get("build", "")
    if name is not None and not isinstance(name, str):
        return None, f"{label}: name must be text"
    if not isinstance(description, str) or (
        description and not _plain(description, MAX_DESCRIPTION)
    ):
        return (
            None,
            f"{label}: description must be plain text of at most {MAX_DESCRIPTION} characters",
        )
    if type(stable_only) is not bool:
        return None, f"{label}: stable_only must be true or false"
    if not isinstance(build, str):
        return None, f"{label}: build must be text"
    try:
        app = custom_app(repo, name, not stable_only, build, description)
    except ValueError as error:
        return None, f"{label}: {error}"
    if app.repo.casefold() in seen:
        return None, f"{label}: listed more than once"
    seen.add(app.repo.casefold())
    return CatalogEntry(app.repo, app.name, description, stable_only, build), ""


def parse(data: bytes | str) -> ParsedCatalog:
    """Validate a catalog. File-level problems raise; bad entries are skipped and reported."""
    raw = data.encode() if isinstance(data, str) else data
    if len(raw) > MAX_BYTES:
        raise CatalogError("The catalog is larger than 256 KiB.")
    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise CatalogError("The catalog is not valid UTF-8 text.") from error
    except tomllib.TOMLDecodeError as error:
        raise CatalogError(f"The catalog is not valid TOML: {error}") from error
    version = document.get("format")
    if type(version) is not int:
        raise CatalogError("The catalog needs a format number, such as: format = 1")
    if version != FORMAT:
        raise CatalogError(
            f"This catalog uses format {version}; this AppGlider reads format {FORMAT}."
            if version < FORMAT
            else f"This catalog uses format {version}, which needs a newer AppGlider."
        )
    title = document.get("name", "")
    if title != "" and (not isinstance(title, str) or not _plain(title, 128)):
        raise CatalogError("The catalog name must be plain text of at most 128 characters.")
    tables = document.get("app", [])
    if not isinstance(tables, list):
        raise CatalogError("Entries must be written as [[app]] tables.")
    if len(tables) > MAX_APPS:
        raise CatalogError(f"The catalog lists more than {MAX_APPS} apps.")
    ignored = sorted(document.keys() - TOP_KEYS)
    entries: list[CatalogEntry] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for number, table in enumerate(tables, 1):
        if isinstance(table, dict):
            ignored.extend(f"app {number}: {key}" for key in sorted(table.keys() - APP_KEYS))
        entry, problem = _entry(number, table, seen)
        if entry is not None:
            entries.append(entry)
        else:
            skipped.append(problem)
    return ParsedCatalog(title, tuple(entries), tuple(skipped), tuple(ignored))


def read(path: Path) -> ParsedCatalog:
    """Read a catalog file without loading more than the size limit."""
    try:
        with path.open("rb") as source:
            raw = source.read(MAX_BYTES + 1)
    except OSError as error:
        raise CatalogError(f"Cannot read {path}: {error.strerror or error}") from error
    return parse(raw)


def _quote(value: str) -> str:
    escapes = {"\\": "\\\\", '"': '\\"', "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f"}
    out = []
    for char in value:
        if char in escapes:
            out.append(escapes[char])
        elif char == "\r":
            out.append("\\r")
        elif ord(char) < 32 or ord(char) == 127:
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def render(entries: Iterable[CatalogEntry], name: str = "") -> str:
    """Write a catalog that `parse` reads back unchanged."""
    lines = [
        "# AppGlider catalog: a list of GitHub repositories whose AppImages AppGlider can manage.",
        "# Edit it with any text editor. Importing adds these entries; nothing is installed.",
        f"format = {FORMAT}",
    ]
    if name:
        lines.append(f"name = {_quote(name)}")
    for entry in sorted(entries, key=lambda item: (item.name.casefold(), item.repo)):
        lines += ["", "[[app]]", f"repo = {_quote(entry.repo)}", f"name = {_quote(entry.name)}"]
        if entry.description:
            lines.append(f"description = {_quote(entry.description)}")
        lines.append(f"stable_only = {'true' if entry.stable_only else 'false'}")
        if entry.build:
            lines.append(f"build = {_quote(entry.build)}")
    return "\n".join(lines) + "\n"


def entry_for(app: App) -> CatalogEntry:
    """Describe a library app as a catalog entry."""
    return CatalogEntry(
        app.repo,
        app.name,
        "" if app.description == default_description(app.repo) else app.description,
        not app.include_prereleases,
        app.build_variant,
    )
