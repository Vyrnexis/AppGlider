"""Discover bounded repository artwork and turn it into a local PNG icon."""

import configparser
import json
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from threading import Event

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
from PySide6.QtGui import QImage, QImageReader, QPixmap

from appglider import APP_ICON
from appglider.core.catalog import App
from appglider.github import http
from appglider.github import status as github_status
from appglider.github.http import read_metadata
from appglider.github.versions import check_cancel

MAX_ICON_BYTES = 2 * 1024 * 1024
PLAIN_DOCTYPE = re.compile(
    r'<!DOCTYPE\s+svg(?:\s+PUBLIC\s+"[^"<>\[\]]*"\s+"[^"<>\[\]]*")?\s*>', re.I
)


@dataclass(frozen=True)
class IconCandidate:
    name: str
    png: bytes


_PIXMAPS: dict[tuple[str, int, int, int], QPixmap] = {}


def icon_pixmap(path: Path, size: int) -> QPixmap:
    # QImage avoids QPixmap's file cache retaining an icon replaced within the same second.
    # The cache key includes the file's mtime and size, so a replaced icon is read again.
    try:
        info = path.stat()
        key = (str(path), info.st_mtime_ns, info.st_size, size)
    except OSError:
        key = None
    if key is not None and key in _PIXMAPS:
        return _PIXMAPS[key]
    image = QImage(str(path))
    if image.isNull():
        image = QImage(str(APP_ICON))
    pixmap = QPixmap.fromImage(
        image.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    )
    if key is not None:
        if len(_PIXMAPS) >= 256:
            _PIXMAPS.clear()
        _PIXMAPS[key] = pixmap
    return pixmap


def normalise_icon(data: bytes) -> bytes:
    if not data or len(data) > MAX_ICON_BYTES:
        raise ValueError("Choose a PNG or SVG icon smaller than 2 MiB.")
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    kind = reader.format().data()
    if kind not in {b"png", b"svg"}:
        raise ValueError("The icon must be a valid PNG or SVG image.")
    if kind == b"svg":
        # Rasterise self-contained artwork; never resolve links into local files or the network.
        try:
            text_svg = data.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError("SVG icons must use UTF-8 encoding.") from error
        # Editors often write the standard SVG 1.1 declaration; one without an internal
        # subset defines nothing, so it is removed rather than refused.
        text_svg = PLAIN_DOCTYPE.sub("", text_svg, count=1)
        if "<!DOCTYPE" in text_svg.upper() or "<!ENTITY" in text_svg.upper():
            raise ValueError("SVG icons must not contain document types or entities.")
        try:
            document = ET.fromstring(text_svg)
        except ET.ParseError as error:
            raise ValueError("The SVG icon is malformed.") from error
        for element in document.iter():
            for key, value in element.attrib.items():
                if key.rsplit("}", 1)[-1] == "href" and not value.startswith("#"):
                    raise ValueError("SVG icons must not reference external images or files.")
            text = " ".join([*element.attrib.values(), element.text or ""])
            if "@import" in text.lower() or any(
                not value.strip(" \t\r\n\"'").startswith("#")
                for value in re.findall(r"url\s*\((.*?)\)", text, re.I | re.S)
            ):
                raise ValueError("SVG icons must not load external resources.")
        buffer.close()
        buffer.setData(QByteArray(text_svg.encode()))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer, b"svg")
    size = reader.size()
    if not size.isValid() or max(size.width(), size.height()) > 4096:
        raise ValueError("Icon dimensions must be between 1 and 4096 pixels.")
    # SVG scales losslessly to 512; raster icons are only ever shrunk, never enlarged.
    if kind == b"svg" or max(size.width(), size.height()) > 512:
        reader.setScaledSize(size.scaled(QSize(512, 512), Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        raise ValueError("The icon image could not be read.")
    output = QBuffer()
    output.open(QIODevice.OpenModeFlag.WriteOnly)
    # PySide6's runtime requires str here although its stub declares bytes.
    if not image.save(output, "PNG"):  # type: ignore[call-overload]
        raise ValueError("The icon could not be converted to PNG.")
    return bytes(output.data().data())


MAX_LISTINGS = 40
WALK_SECONDS = 60
LISTING_PAUSE = 0.1
EXTRA_PAGES = 6  # after a strong candidate appears, look at this many more pages (at least)

# Folder words, matched as whole words, that suggest artwork; and ones that never do.
ICON_WORDS = {
    "icon",
    "icons",
    "logo",
    "logos",
    "brand",
    "branding",
    "hicolor",
    "exports",
    "pixmaps",
}
FOLDER_WORDS = ICON_WORDS | {
    "asset", "assets", "resource", "resources", "res", "image", "images", "img", "linux",
    "packaging", "share", "apps", "app", "desktop", "appimage", "static", "public", "art",
    "media", "build", "scalable", "artwork", "data",
}  # fmt: skip
SKIPPED_WORDS = {
    "3rdparty", "thirdparty", "third", "party", "external", "extern", "deps", "dependencies",
    "vendor", "vendors", "node", "modules", "test", "tests", "testing", "docs", "doc", "macos",
    "darwin", "mac", "windows", "win", "win32", "android", "ios", "git", "fonts", "locale",
    "locales", "translations", "i18n", "example", "examples", "sample", "samples", "flatpak",
    "snap", "cmake", "contrib", "tools", "scripts", "script", "website", "web",
}  # fmt: skip
SIZE_FOLDER = re.compile(r"(\d+)x\d+")
# Words in a file name that mark a variant (another platform, a banner...), not the app icon.
VARIANT_WORDS = {
    "mac", "macos", "osx", "win", "windows", "ios", "android", "title", "about", "gradient",
    "grayscale", "splash", "badge", "banner", "screenshot", "wordmark",
}  # fmt: skip
# Folders or names for a project's preview builds, whose logo is often a recoloured variant.
PREVIEW_WORDS = {"insider", "insiders", "nightly", "canary", "beta", "alpha", "preview", "unstable"}


def words(path: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", path).lower())


def folder_priority(path: str) -> int:
    found = words(path)
    return 2 * sum(w in ICON_WORDS for w in found) + sum(
        w in FOLDER_WORDS or w.isdigit() for w in found
    )


def worth_opening(path: str, depth: int) -> bool:
    found = words(path)
    if any(w in SKIPPED_WORDS for w in found):
        return False
    return depth <= 1 or any(w in FOLDER_WORDS for w in found)


def api_files(app: App, cancel: Event) -> dict[str, int | None] | None:
    """Every small regular file of the repository from one API request, or None if unusable."""
    check_cancel(cancel)
    try:
        tree = json.loads(
            read_metadata(f"https://api.github.com/repos/{app.repo}/git/trees/HEAD?recursive=1")
        )
    except ValueError:
        return None  # rate limited, or a listing too large to read: browse the web pages
    if (
        not isinstance(tree, dict)
        or tree.get("truncated")
        or not isinstance(tree.get("tree"), list)
    ):
        return None
    files: dict[str, int | None] = {}
    for item in tree["tree"]:
        if not isinstance(item, dict):
            continue
        path, size = item.get("path"), item.get("size")
        if (
            isinstance(path, str)
            and len(path) <= 512
            and not any(ord(c) < 32 or ord(c) == 127 for c in path)
            and not any(part in {"", ".", ".."} for part in path.split("/"))
            and type(size) is int
            and 0 < size <= MAX_ICON_BYTES
            and item.get("type") == "blob"
            and item.get("mode") in {"100644", "100755"}
        ):
            files[path] = size
    return files


def is_strong(path: str, slug: str) -> bool:
    """A file very likely to be the app's own icon: named after the project, or in hicolor."""
    name = PurePosixPath(path)
    if name.suffix.lower() not in {".png", ".svg"}:
        return False
    if any(word in VARIANT_WORDS for word in words(name.stem)):
        return False
    stem = re.sub(r"[^a-z0-9]", "", name.stem.lower())
    return (len(slug) >= 4 and slug in stem) or "hicolor" in path.lower()


def web_files(
    app: App, cancel: Event, notify: Callable[[int], None] | None = None
) -> tuple[dict[str, int | None], str]:
    """Find files by browsing the repository's web folders, without any API request.

    Every top-level and second-level folder is opened; deeper ones only when their path
    looks like artwork, most promising first. The walk is capped in pages and in time.
    It stops early: once a strong candidate has been seen on page N, it opens a few more pages
    (at least six, or N again) in case a better sibling exists, then finishes. `notify` is told
    how many pages have been opened. Also returns the repository's current name, which differs
    from the saved one if it moved.
    """
    repo = app.repo
    slug = re.sub(r"[^a-z0-9]", "", app.repo.split("/")[1].lower())
    strong_at: int | None = None
    files: dict[str, int | None] = {}
    queue: list[tuple[str, int]] = [("", 0)]
    started, opened = time.monotonic(), 0
    while queue and opened < MAX_LISTINGS and time.monotonic() - started < WALK_SECONDS:
        if strong_at is not None and opened >= strong_at + max(EXTRA_PAGES, strong_at):
            break
        check_cancel(cancel)
        queue.sort(key=lambda entry: (-folder_priority(entry[0]), entry[1]))
        path, depth = queue.pop(0)
        try:
            items, repo = http.web_listing(repo, path, cancel)
        except ValueError:
            if opened == 0:
                return files, repo  # the very first page failed: nothing to learn here
            continue
        opened += 1
        if notify is not None:
            notify(opened)
        sizes = [
            (int(match.group(1)), item["name"])
            for item in items
            if item["contentType"] == "directory" and (match := SIZE_FOLDER.fullmatch(item["name"]))
        ]
        biggest = {name for _, name in sorted(sizes)[-2:]}  # hicolor-style: the largest only
        for item in items:
            if item["contentType"] == "file":
                files[item["path"]] = None
                if strong_at is None and is_strong(item["path"], slug):
                    strong_at = opened
            elif (
                depth < 8
                and worth_opening(item["path"], depth)
                and (not SIZE_FOLDER.fullmatch(item["name"]) or item["name"] in biggest)
            ):
                queue.append((item["path"], depth + 1))
        cancel.wait(LISTING_PAUSE)
    return files, repo


def discover_icons(
    app: App, cancel: Event, progress: Callable[[str], None] | None = None
) -> tuple[IconCandidate, ...]:
    """Offer up to four icon candidates found in the repository.

    With plenty of GitHub API allowance left, one API request lists the files. Otherwise (low
    allowance, rate limited, or a repository too big for that listing) the files are found by
    browsing the repository's web pages. Either way the images themselves are read from
    raw.githubusercontent.com, which costs no API requests. `progress` receives short status
    messages for the user, so a slow search does not look like a hang.
    """
    check_cancel(cancel)
    say = progress or (lambda text: None)
    repo = app.repo
    files = None
    if not github_status.quota_low():
        say(f"Reading {app.name}'s file list…")
        files = api_files(app, cancel)
    if files is None:
        say(f"Looking through {app.name}'s folders… (large projects can take up to half a minute)")
        files, repo = web_files(
            app,
            cancel,
            lambda pages: say(f"Looking through {app.name}'s folders… {pages} pages so far"),
        )
    say("Checking the possible icons…")

    def read_file(path: str) -> bytes:
        return http.raw_bytes(repo, path, MAX_ICON_BYTES, cancel)

    declared: set[str] = set()
    for path in sorted(path for path in files if path.endswith(".desktop"))[:2]:
        try:
            config = configparser.ConfigParser(interpolation=None, strict=False)
            config.read_string(read_file(path).decode("utf-8"))
            name = config.get("Desktop Entry", "Icon").strip().lower()
            if name.endswith((".png", ".svg", ".xpm", ".ico")):
                name = name.rsplit(".", 1)[0]
            declared.add(name)
        except (OSError, ValueError, configparser.Error):
            continue

    slug = re.sub(r"[^a-z0-9]", "", app.repo.split("/")[1].lower())

    def resolution_of(lower: str) -> int:
        """A size written in the path: a 512x512 folder, or a name like icon_192px or icon-256."""
        sizes = re.findall(r"(?:^|/)(\d+)x\d+(?:/|$)", lower)
        sizes += re.findall(r"(\d{2,4})px", lower) + re.findall(r"[-_](\d{2,4})\.[a-z]+$", lower)
        return max((int(n) for n in sizes), default=0)

    def named_after_project(path: str) -> bool:
        stem = re.sub(r"[^a-z0-9]", "", PurePosixPath(path).stem.lower())
        return len(slug) >= 4 and slug in stem

    def rank(path: str) -> tuple[int, int, int, int, int, int, str]:
        lower = path.lower()
        stem = PurePosixPath(lower).stem
        resolution = resolution_of(lower)
        return (
            int(stem not in declared),
            int(any(word in PREVIEW_WORDS for word in words(path))),
            int(slug not in re.sub(r"[^a-z0-9]", "", stem)),
            int("linux" not in lower and "hicolor" not in lower),
            int(PurePosixPath(lower).suffix != ".png"),
            -min(resolution, 512),
            lower,
        )

    candidates = [
        path
        for path in files
        if PurePosixPath(path).suffix.lower() in {".png", ".svg"}
        and not any(
            word in path.lower()
            for word in (
                "screenshot",
                "banner",
                "wordmark",
                "node_modules",
                "vendor/",
                "macos",
                "darwin",
                "windows",
                "win32",
            )
        )
        and not any(word in VARIANT_WORDS for word in words(PurePosixPath(path).stem))
        and (
            PurePosixPath(path.lower()).stem in declared
            or any(word in path.lower() for word in ("icon", "logo", "brand/"))
            or named_after_project(path)
        )
    ]
    result: list[IconCandidate] = []
    seen: set[str] = set()
    for path in sorted(candidates, key=rank):
        stem = PurePosixPath(path).stem.lower()
        if stem in seen:
            continue
        if len(seen) >= 8:
            break
        seen.add(stem)
        try:
            png = normalise_icon(read_file(path))
        except (OSError, ValueError):
            continue
        result.append(IconCandidate(path, png))
        if len(result) == 4:
            break
    check_cancel(cancel)
    # Artwork far from square (page corners, wide logos) goes after the real icons.
    return tuple(sorted(result, key=lambda candidate: not squarish(candidate.png)))


def squarish(png: bytes) -> bool:
    """Whether a PNG from normalise_icon is close to square, read from its header."""
    width, height = int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
    return bool(width and height) and max(width, height) <= 1.2 * min(width, height)
