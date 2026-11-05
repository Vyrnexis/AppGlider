"""Copy an AppImage into a folder safely, under its own name."""

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from appglider.github.icons import normalise_icon
from appglider.github.versions import check_cancel, release_version
from appglider.install.appimage import FREE_SPACE_MARGIN, check_appimage
from appglider.install.desktopentry import (
    MAX_ICON_FILE,
    _icon_name,
    _read_regular,
    _text,
    parse_groups,
)

COPY_CHUNK = 1024 * 1024


def _copy_chunks(source: Path, stage: Path, cancel: Event | None) -> None:
    """Copy `source` to `stage` in chunks, checking `cancel` between them; flushed to disk."""
    with source.open("rb") as reader, stage.open("wb") as writer:
        while chunk := reader.read(COPY_CHUNK):
            if cancel is not None:
                check_cancel(cancel)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    if cancel is not None:
        check_cancel(cancel)


def place_appimage(
    source: Path, folder: Path, cancel: Event | None = None, *, replace: bool = False
) -> Path:
    """Copy `source` into `folder` and return the new file; the source is never changed.

    The copy is made under a hidden temporary name, checked and made executable, and only
    then renamed, so a failure leaves nothing behind. A file already there under that name
    is kept, unless `replace` is set and it is an AppImage itself; when it is the source,
    nothing is copied. When `cancel` is set during the copy, the temporary file is removed
    and `Cancelled` is raised.
    """
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"{source.name} is not a plain file.")
    target = folder / source.name
    if os.path.lexists(target):
        if target.is_file() and not target.is_symlink() and target.samefile(source):
            return target
        if not (replace and replaceable(target)):
            raise ValueError(f"{source.name} already exists in {folder}.")
    folder.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(folder).free < source.stat().st_size + FREE_SPACE_MARGIN:
        raise ValueError(f"There is not enough free disk space in {folder}.")
    descriptor, name = tempfile.mkstemp(prefix=".appglider-", suffix=".part", dir=folder)
    os.close(descriptor)
    stage = Path(name)
    try:
        _copy_chunks(source, stage, cancel)
        stage.chmod(0o755)
        check_appimage(stage)
        os.replace(stage, target)
    finally:
        stage.unlink(missing_ok=True)
    return target


def replaceable(target: Path) -> bool:
    """Whether `target` is a plain AppImage file that a newer copy may take the place of."""
    if target.is_symlink() or not target.is_file():
        return False
    try:
        check_appimage(target)
    except (OSError, ValueError):
        return False
    return True


# File-name words that come after the application's name: the CPU and the system.
_NAME_ENDS = {"x86_64", "x86", "amd64", "x64", "aarch64", "arm64", "linux", "appimage"}


@dataclass(frozen=True)
class FileDetails:
    """What AppGlider shows before adding an AppImage from this computer."""

    name: str
    version: str  # "" when neither the AppImage nor its file name gives one
    size: int
    icon: bytes | None  # a PNG, or None for the default icon
    note: str = ""  # shown with the details when they could not be read from inside the file


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


READ_SECONDS = 20
NO_READER_NOTE = (
    "The name and version come from the file name. AppGlider reads the real details once "
    "the file is added. Install squashfs-tools to read them from the AppImage now."
)
UNREADABLE_NOTE = (
    "AppGlider could not read the details inside this file, so the name and version come "
    "from the file name. It reads the real details once the file is added."
)


def squashfs_offset(path: Path) -> int | None:
    """Where an AppImage's squashfs image starts: the end of its ELF runtime, or None.

    The runtime is an ELF program and the image is appended right after its section headers.
    """
    try:
        with path.open("rb") as source:
            head = source.read(64)
            if len(head) < 64 or head[:5] != b"\x7fELF\x02":
                return None
            shoff = int.from_bytes(head[0x28:0x30], "little")
            entry = int.from_bytes(head[0x3A:0x3C], "little")
            count = int.from_bytes(head[0x3C:0x3E], "little")
            offset = shoff + entry * count
            source.seek(offset)
            return offset if source.read(4) == b"hsqs" else None
    except OSError:
        return None


def _unsquash(
    tool: str, source: Path, offset: int, destination: Path, *names: str
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            tool,
            "-n",
            "-f",
            "-o",
            str(offset),
            "-d",
            str(destination),
            str(source.resolve()),
            *names,
        ],
        capture_output=True,
        timeout=READ_SECONDS,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _fetch(tool: str, source: Path, offset: int, root: Path, relative: Path) -> Path | None:
    """Extract one file, following a link that stays inside the tree; never runs the file."""
    for _ in range(3):
        if relative.name.startswith("-") or relative.parts[:1] in ((), ("..",), (".",)):
            return None
        _unsquash(tool, source, offset, root, relative.as_posix())
        found = root / relative
        if not found.is_symlink():
            return relative if found.is_file() else None
        link = os.readlink(found)
        if os.path.isabs(link):
            return None
        relative = Path(os.path.normpath(relative.parent / link))
    return None


def _read_inside(source: Path) -> tuple[str, str, bytes | None] | None:
    """Name, version and icon from inside the file with unsquashfs, or None if it can't."""
    tool = shutil.which("unsquashfs")
    offset = squashfs_offset(source)
    if tool is None or offset is None:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="appglider-file-") as temporary:
            root = Path(temporary) / "squashfs-root"
            listing = subprocess.run(
                [tool, "-n", "-o", str(offset), "-l", str(source.resolve())],
                capture_output=True,
                timeout=READ_SECONDS,
                check=False,
                stdin=subprocess.DEVNULL,
            )
            if listing.returncode != 0:
                return None
            names = sorted(
                line.removeprefix("squashfs-root/")
                for line in listing.stdout.decode("utf-8", "replace").splitlines()
                if re.fullmatch(r"squashfs-root/[^/]+\.desktop", line)
            )
            if not names:
                return None
            found = _fetch(tool, source, offset, root, Path(names[0]))
            text = _read_regular(root, found) if found is not None else None
            if text is None:
                return None
            entry = parse_groups(text).get("Desktop Entry", {})
            name = _text(entry.get("Name", ""), 128)
            version = _text(entry.get("X-AppImage-Version", ""), 128)
            icon_name = entry.get("Icon", "").strip()
            candidates = [f"{icon_name}.png", f"{icon_name}.svg"] if _icon_name(icon_name) else []
            for relative in (*candidates, ".DirIcon"):
                icon_file = _fetch(tool, source, offset, root, Path(relative))
                if icon_file is None:
                    continue
                with (root / icon_file).open("rb") as handle:
                    data = handle.read(MAX_ICON_FILE + 1)
                if 0 < len(data) <= MAX_ICON_FILE:
                    return name, version, data
            return name, version, None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def describe_appimage(source: Path) -> FileDetails:
    """Read an AppImage's name, version and icon from inside it; the file is not changed.

    It must be a plain type 2 AppImage for this CPU. The file is never run: its squashfs image
    is read with `unsquashfs` at the offset found in the ELF header. Without that tool, or when
    it cannot read the image, the details come from the file name and `note` says so.
    """
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"{source.name} is not a plain file.")
    check_appimage(source)
    size = source.stat().st_size
    inside = _read_inside(source)
    if inside is None:
        note = NO_READER_NOTE if shutil.which("unsquashfs") is None else UNREADABLE_NOTE
        return FileDetails(
            name_from_file(source.name), version_from_name(source.name), size, None, note
        )
    name, raw_version, raw_icon = inside
    icon = None
    if raw_icon is not None:
        try:
            icon = normalise_icon(raw_icon)
        except ValueError:
            icon = None
    version = release_version(raw_version) or version_from_name(source.name)
    return FileDetails(name or name_from_file(source.name), version, size, icon)
