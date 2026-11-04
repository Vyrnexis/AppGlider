"""Copy an AppImage into a folder safely, under its own name."""

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from appglider.github.icons import normalise_icon
from appglider.github.versions import release_version
from appglider.install.appimage import FREE_SPACE_MARGIN, check_appimage, clean_environment
from appglider.install.desktopentry import read_identity


def place_appimage(source: Path, folder: Path) -> Path:
    """Copy `source` into `folder` and return the new file; the source is never changed.

    The copy is made under a hidden temporary name, checked and made executable, and only
    then renamed, so a failure leaves nothing behind. A file already there under that name
    is never replaced; when it is the source itself, nothing is copied.
    """
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"{source.name} is not a plain file.")
    target = folder / source.name
    if os.path.lexists(target):
        if target.is_file() and not target.is_symlink() and target.samefile(source):
            return target
        raise ValueError(f"{source.name} already exists in {folder}.")
    folder.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(folder).free < source.stat().st_size + FREE_SPACE_MARGIN:
        raise ValueError(f"There is not enough free disk space in {folder}.")
    descriptor, name = tempfile.mkstemp(prefix=f".{source.name}.", suffix=".part", dir=folder)
    os.close(descriptor)
    stage = Path(name)
    try:
        shutil.copyfile(source, stage)
        stage.chmod(0o755)
        check_appimage(stage)
        os.replace(stage, target)
    finally:
        stage.unlink(missing_ok=True)
    return target


# File-name words that come after the application's name: the CPU and the system.
_NAME_ENDS = {"x86_64", "x86", "amd64", "x64", "aarch64", "arm64", "linux", "appimage"}


@dataclass(frozen=True)
class FileDetails:
    """What AppGlider shows before adding an AppImage from this computer."""

    name: str
    version: str  # "" when neither the AppImage nor its file name gives one
    size: int
    icon: bytes | None  # a PNG, or None for the default icon


def version_from_name(file_name: str) -> str:
    """The first part of a file name that reads as a version, or ""."""
    stem = re.sub(r"\.appimage$", "", file_name, flags=re.IGNORECASE)
    for part in re.split(r"[-_ ]+", stem):
        if "." in part and (version := release_version(part)):
            return version
    return ""


def name_from_file(file_name: str) -> str:
    """The words of a file name before its version or CPU, as a plain display name."""
    stem = re.sub(r"\.appimage$", "", file_name, flags=re.IGNORECASE)
    words: list[str] = []
    for part in re.split(r"[-_ ]+", stem):
        if not part or part.casefold() in _NAME_ENDS or ("." in part and release_version(part)):
            break
        words.append(part)
    return " ".join(words) or stem


def describe_appimage(source: Path) -> FileDetails:
    """Read an AppImage's name, version and icon from inside it; the file is not changed.

    It must be a plain type 2 AppImage for this CPU. The AppImage's own runtime extracts a few
    small files, so a file that is not executable is first copied to a temporary one.
    """
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"{source.name} is not a plain file.")
    check_appimage(source)
    size = source.stat().st_size
    with tempfile.TemporaryDirectory(prefix="appglider-file-") as temporary:
        target = source
        if not os.access(source, os.X_OK):
            if shutil.disk_usage(temporary).free < size + FREE_SPACE_MARGIN:
                raise ValueError("There is not enough free disk space to read this file.")
            target = Path(temporary) / "app.AppImage"
            shutil.copyfile(source, target)
            target.chmod(0o700)
        identity = read_identity(target, clean_environment())
    icon = None
    if identity.icon is not None:
        try:
            icon = normalise_icon(identity.icon)
        except ValueError:
            icon = None
    version = release_version(identity.version) or version_from_name(source.name)
    return FileDetails(identity.name or name_from_file(source.name), version, size, icon)
