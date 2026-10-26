"""Look for a newer AppGlider release, download it next to the running AppImage and tidy up.

The check reads only GitHub's "Latest" release page (a redirect, never the API), so it uses
none of GitHub's request allowance and only ever sees stable releases. The download is the
same verified one app installs use. Plain functions: only the saved settings come from Qt.
"""

import contextlib
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event

from PySide6.QtCore import QSettings

from appglider import APP_VERSION, library
from appglider.catalog import custom_app
from appglider.releases import (
    Progress,
    Release,
    check_cancel,
    download,
    latest_release,
    latest_tag,
    release_version,
    version_key,
)

REPO = "Vyrnexis/AppGlider"
# Key in the announced-versions record; app ids never contain a colon, so it cannot clash.
NOTIFY_KEY = "AppGlider:self"
NO_IMAGE_HINT = "update it the way you installed it"
# The new file waiting to be run, the file it replaces (for the new version to remove) and
# the new file's version, so the ready state survives a restart of the old file.
READY_KEY = "selfupdate/ready"
REPLACED_KEY = "selfupdate/replaced"
VERSION_KEY = "selfupdate/version"
AFTER_UPDATE_FLAG = "--after-update"


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


def is_image_name(name: str) -> bool:
    """Whether a file name looks like an AppGlider release: AppGlider-*.AppImage."""
    folded = name.casefold()
    return folded.startswith("appglider-") and folded.endswith(".appimage")


def target_name(release: Release) -> str:
    """The release's own file name when it follows the AppGlider naming, else the usual one."""
    if is_image_name(release.name) and Path(release.name).name == release.name:
        return release.name
    return f"AppGlider-{release.version}-x86_64.AppImage"


def same_file(first: Path, second: Path) -> bool:
    try:
        return first.samefile(second)
    except OSError:
        return first.absolute() == second.absolute()


@dataclass(frozen=True)
class Downloaded:
    """A verified new AppImage in place beside the one that is running."""

    old: Path
    new: Path
    version: str


def download_update(
    image: Path, tag: str, cache_dir: Path, cancel: Event, progress: Progress
) -> Downloaded:
    """Download and verify the release the check found, next to the running image.

    The file is built under a hidden temporary name in the same folder, checked and made
    executable, and only then renamed to its final name, so a failure or a cancel leaves
    nothing behind and the running image is never touched.
    """
    version = newer_version(tag)
    if version is None:
        raise ValueError("That is not a newer stable version of AppGlider.")
    release = latest_release(custom_app(REPO, "AppGlider"), cancel, cache_dir)
    if release.prerelease or release.version != version:
        raise ValueError(
            f"AppGlider {release.version} is now the latest release. "
            "Check for updates, then try again."
        )
    target = image.parent / target_name(release)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ValueError(f"{target.name} already exists and is not a plain file.")
    if same_file(target, image):
        raise ValueError(f"The running file is already called {target.name}.")
    if shutil.disk_usage(image.parent).free < release.size + library.FREE_SPACE_MARGIN:
        raise ValueError("There is not enough free disk space for this download.")
    descriptor, name = tempfile.mkstemp(prefix=".AppGlider-update-", dir=image.parent)
    os.close(descriptor)
    stage = Path(name)
    try:

        def report(message: str, received: int, total: int) -> None:
            progress(f"Downloading AppGlider {version}…", received, total)

        download(release, stage, cancel, report)
        library.check_appimage(stage)
        stage.chmod(0o755)
        check_cancel(cancel)
        os.replace(stage, target)
    finally:
        stage.unlink(missing_ok=True)
    return Downloaded(image, target, version)


def record(settings: QSettings, done: Downloaded) -> None:
    settings.setValue(REPLACED_KEY, str(done.old))
    settings.setValue(READY_KEY, str(done.new))
    settings.setValue(VERSION_KEY, done.version)
    settings.sync()


def forget(settings: QSettings, *keys: str) -> None:
    for key in keys or (READY_KEY, REPLACED_KEY, VERSION_KEY):
        settings.remove(key)
    settings.sync()


def eligible(old: Path, image: Path) -> bool:
    """Whether a replaced file is one an update may delete, apart from whether it still runs."""
    if old.absolute() == image.absolute() or old.parent.absolute() != image.parent.absolute():
        return False
    if old.is_symlink() or not old.is_file() or not is_image_name(old.name):
        return False
    try:
        library.check_appimage(old)
    except (OSError, ValueError):
        return False
    return True


def removable(old: Path, image: Path) -> bool:
    """Whether a replaced file may be deleted now by the AppGlider running from ``image``."""
    return eligible(old, image) and not library.running(old)


def cleanup(settings: QSettings) -> None:
    """Remove the file the last update replaced, and drop the records once they are used up.

    Nothing else is ever deleted. A file that cannot go yet (it is still running, say) stays
    recorded for the next start, unless it is already gone.
    """
    image = running_image()
    ready = str(settings.value(READY_KEY, "", type=str))
    old = str(settings.value(REPLACED_KEY, "", type=str))
    if image is None or not (ready or old):
        return
    if ready and not same_file(Path(ready), image) and not os.path.lexists(ready):
        forget(settings)  # the new file vanished, so the old one must not be removed for it
        return
    waiting = False
    if old and eligible(Path(old), image):
        # Only a file that is still running is tried again at the next start.
        waiting = library.running(Path(old))
        if not waiting:
            with contextlib.suppress(OSError):
                Path(old).unlink()
    if ready and same_file(Path(ready), image):
        forget(settings, READY_KEY, VERSION_KEY)
    if old and not waiting:
        forget(settings, REPLACED_KEY)


@dataclass(frozen=True)
class Ready:
    """A newer AppGlider that was downloaded earlier and has not been started yet."""

    path: Path
    version: str


def ready_update(settings: QSettings) -> Ready | None:
    """The downloaded update waiting for a restart, when this is an AppImage that can use it."""
    value = str(settings.value(READY_KEY, "", type=str))
    version = str(settings.value(VERSION_KEY, "", type=str))
    image = running_image()
    if image is None or not value:
        return None
    path = Path(value)
    usable = (
        newer_version(version) is not None
        and not same_file(path, image)
        and path.is_file()
        and not path.is_symlink()
        and is_image_name(path.name)
    )
    if usable:
        with contextlib.suppress(OSError, ValueError):
            library.check_appimage(path)
            return Ready(path, version)
    forget(settings)
    return None


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

    def hint(self, image: Path | None, ready: str = "") -> str:
        """One line for the About page; ``ready`` is a downloaded version awaiting a restart."""
        if ready:
            return f"Version {ready} is ready · restart to use it"
        when = f"{datetime.fromtimestamp(self.checked_at):%H:%M}"
        if self.failed:
            return f"Couldn't check · {when}"
        if self.version:
            available = f"Version {self.version} is available"
            return f"{available} · {NO_IMAGE_HINT}" if image is None else available
        return f"Up to date · checked {when}" if self.checked_at else "Not checked yet"
