"""Look for a newer AppGlider release. Plain functions: nothing here needs Qt.

Only GitHub's "Latest" release page is read (a redirect, never the API), so this check uses
none of GitHub's request allowance and only ever sees stable releases.
"""

import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event

from appglider import APP_VERSION
from appglider.releases import latest_tag, release_version, version_key

REPO = "Vyrnexis/AppGlider"
# Key in the announced-versions record; app ids never contain a colon, so it cannot clash.
NOTIFY_KEY = "AppGlider:self"
NO_IMAGE_HINT = "update it the way you installed it"


class CheckFailed(Exception):
    """GitHub's release page gave no clear answer."""


def release_url(tag: str) -> str:
    return f"https://github.com/{REPO}/releases/tag/{tag}"


def newer_version(tag: object) -> str | None:
    """The version a stable tag names when it is strictly newer than this AppGlider."""
    version, current = release_version(tag), release_version(APP_VERSION)
    if version is None or current is None or "-" in version:
        return None
    return version if version_key(version) > version_key(current) else None


def check(cancel: Event) -> str | None:
    """The tag of a newer stable release, or None when this version is current.

    Raises CheckFailed when the release page gives no clear answer.
    """
    tag = latest_tag(REPO, cancel)
    if tag is None:
        raise CheckFailed("GitHub did not name a latest release.")
    return tag if newer_version(tag) else None


def running_image() -> Path | None:
    """The AppImage this process was started from, or None for any other kind of start."""
    image, directory = os.environ.get("APPIMAGE"), os.environ.get("APPDIR")
    if image and directory and Path(sys.prefix).resolve().is_relative_to(Path(directory).resolve()):
        return Path(image).absolute()
    return None


def can_replace(image: Path) -> bool:
    """Whether the file can be swapped for a newer one: a regular file in a writable folder."""
    return image.is_file() and os.access(image.parent, os.W_OK | os.X_OK)


@dataclass(frozen=True)
class SelfUpdate:
    """What the last check learned. A zero time means no check has finished yet."""

    checked_at: float = 0.0
    tag: str = ""
    failed: bool = False

    @property
    def version(self) -> str:
        """The offered version, or an empty string when there is nothing newer."""
        return (newer_version(self.tag) or "") if self.tag else ""

    @property
    def url(self) -> str:
        return release_url(self.tag)

    def hint(self, image: Path | None) -> str:
        """One line for the About page."""
        when = f"{datetime.fromtimestamp(self.checked_at):%H:%M}"
        if self.failed:
            return f"Couldn't check · {when}"
        if self.version:
            available = f"Version {self.version} is available"
            return f"{available} · {NO_IMAGE_HINT}" if image is None else available
        return f"Up to date · checked {when}" if self.checked_at else "Not checked yet"
