"""Menu metadata read from an AppImage's own .desktop file.

The file is untrusted text from a downloaded program. Only a few descriptive keys are kept,
each sanitised; AppGlider writes its own Name, Exec, Icon and ownership marker.
"""

import contextlib
import os
import re
import shlex
import signal
import stat
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from appglider.catalog import App

Groups = dict[str, dict[str, str]]

EXTRACT_SECONDS = 20
MAX_DESKTOP_BYTES = 64 * 1024
MAX_ACTIONS = 10
MAX_ACTION_ARGUMENTS = 16
LIST_ITEM = re.compile(r"[A-Za-z0-9._+\-/]{1,100}")
IDENTIFIER = re.compile(r"[A-Za-z0-9-]{1,40}")
WM_CLASS = re.compile(r"[A-Za-z0-9._+\-]{1,100}")
KEY = re.compile(r"[A-Za-z0-9-]+")
FIELD_CODE = re.compile(r"%[a-zA-Z]")
ENTRY_KEYS = frozenset(
    {
        "Categories",
        "MimeType",
        "Keywords",
        "Comment",
        "StartupWMClass",
        "StartupNotify",
        "Terminal",
        "Exec",
        "Actions",
    }
)
RUNTIME_VARIABLES = (
    "APPIMAGE_EXTRACT_AND_RUN",
    "APPIMAGE_SILENT_INSTALL",
    "DESKTOPINTEGRATION",
    "NO_CLEANUP",
    "TARGET_APPIMAGE",
)


@dataclass(frozen=True)
class DesktopAction:
    id: str
    name: str
    arguments: tuple[str, ...]


@dataclass(frozen=True)
class DesktopMetadata:
    categories: tuple[str, ...] = ()
    mime_types: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    comment: str = ""
    wm_class: str = ""
    startup_notify: bool | None = None
    terminal: bool = False
    urls: bool = False
    actions: tuple[DesktopAction, ...] = ()


def _clean(value: str) -> bool:
    return not any(ord(c) < 32 or ord(c) == 127 for c in value)


def _text(value: str, limit: int) -> str:
    value = value.strip()
    return value if value and len(value) <= limit and _clean(value) and "\\" not in value else ""


def _items(value: str, pattern: re.Pattern[str] | None, count: int) -> tuple[str, ...]:
    """The distinct `;`-separated items that are safe to write back, at most `count`."""
    items: list[str] = []
    if _clean(value):
        for item in (part.strip() for part in value.split(";")):
            fits = pattern.fullmatch(item) if pattern else len(item) <= 40
            if item and "\\" not in item and fits and item not in items:
                items.append(item)
    return tuple(items[:count])


def parse_groups(text: str) -> Groups:
    """Read `[Group]` sections of `Key=value` lines; localised keys and comments are skipped."""
    groups: Groups = {}
    current: dict[str, str] | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1]
            current = None if name in groups else groups.setdefault(name, {})
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if current is not None and separator and KEY.fullmatch(key):
            current.setdefault(key, value.strip())
    return groups


def keep_known(groups: Groups) -> Groups | None:
    """Only the keys and groups this module reads, or None without a [Desktop Entry]."""
    entry = groups.get("Desktop Entry")
    if entry is None:
        return None
    kept: Groups = {"Desktop Entry": {k: v for k, v in entry.items() if k in ENTRY_KEYS}}
    for name, group in groups.items():
        action = name.removeprefix("Desktop Action ")
        if action != name and IDENTIFIER.fullmatch(action):
            kept[name] = {k: v for k, v in group.items() if k in ("Name", "Exec")}
    return kept


def _arguments(exec_line: str) -> tuple[tuple[str, ...], bool, bool]:
    """The arguments of an Exec line without its program and field codes.

    Also returns whether the line takes URLs (%u or %U) and whether it was usable at all.
    """
    if not _clean(exec_line):
        return (), False, False
    try:
        words = shlex.split(exec_line)
    except ValueError:
        return (), False, False
    if not words or os.path.basename(words[0]) == "env":
        return (), False, False
    urls = False
    arguments = []
    for word in words[1:]:
        plain = word.replace("%%", "")
        urls = urls or bool(re.search(r"%[uU]", plain))
        if not FIELD_CODE.search(plain):
            arguments.append(word.replace("%%", "%"))
    if len(arguments) > MAX_ACTION_ARGUMENTS or any(len(a) > 200 for a in arguments):
        return (), urls, False
    return tuple(arguments), urls, True


def metadata_from_groups(groups: Mapping[str, Mapping[str, str]]) -> DesktopMetadata:
    entry = groups.get("Desktop Entry", {})
    notify = entry.get("StartupNotify", "").strip().lower()
    wm_class = entry.get("StartupWMClass", "").strip()
    actions = []
    for action_id in _items(entry.get("Actions", ""), IDENTIFIER, MAX_ACTIONS):
        group = groups.get(f"Desktop Action {action_id}", {})
        name = _text(group.get("Name", ""), 100)
        arguments, _, usable = _arguments(group.get("Exec", ""))
        if name and usable:
            actions.append(DesktopAction(action_id, name, arguments))
    return DesktopMetadata(
        _items(entry.get("Categories", ""), LIST_ITEM, 12),
        _items(entry.get("MimeType", ""), LIST_ITEM, 64),
        _items(entry.get("Keywords", ""), None, 20),
        _text(entry.get("Comment", ""), 200),
        wm_class if WM_CLASS.fullmatch(wm_class) else "",
        notify == "true" if notify in ("true", "false") else None,
        entry.get("Terminal", "").strip().lower() == "true",
        _arguments(entry.get("Exec", ""))[1],
        tuple(actions),
    )


def valid_groups(data: object) -> Groups | None:
    """Saved groups with the shape parse_groups gives, or None for anything else."""
    if not isinstance(data, dict) or len(data) > MAX_ACTIONS + 1:
        return None
    for name, group in data.items():
        if (
            not isinstance(name, str)
            or not isinstance(group, dict)
            or len(group) > len(ENTRY_KEYS)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in group.items())
        ):
            return None
    return data


def _extract(path: Path, pattern: str, directory: Path, environment: Mapping[str, str]) -> None:
    process = subprocess.Popen(
        [str(path), "--appimage-extract", pattern],
        cwd=directory,
        env={k: v for k, v in environment.items() if k not in RUNTIME_VARIABLES},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        process.wait(timeout=EXTRACT_SECONDS)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            os.killpg(process.pid, signal.SIGKILL)
        process.kill()
        process.wait()
        raise


def _read_regular(root: Path, relative: Path) -> str | None:
    """Read a small regular file strictly inside the extracted tree, or None."""
    target = root / relative
    try:
        if not stat.S_ISREG(target.lstat().st_mode):
            return None
        if not target.resolve().is_relative_to(root.resolve()):
            return None
        with target.open("rb") as source:
            data = source.read(MAX_DESKTOP_BYTES + 1)
        if len(data) > MAX_DESKTOP_BYTES:
            return None
        return data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _matches(app: App, name: str) -> bool:
    simple = re.sub(r"[^a-z0-9]", "", name.casefold().removesuffix(".desktop"))
    needles = {re.sub(r"[^a-z0-9]", "", item.casefold()) for item in (app.asset_stem, app.name)}
    return any(needle and needle in simple for needle in needles)


def extract_desktop_groups(path: Path, app: App, environment: Mapping[str, str]) -> Groups | None:
    """Read the known parts of the .desktop file an AppImage carries, or None if unavailable.

    Runs the file's own type 2 runtime to extract only its .desktop file into a temporary
    directory. Call it only for an AppImage that has been verified and is owned by AppGlider.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="appglider-desktop-") as temporary:
            directory = Path(temporary)
            _extract(path, "*.desktop", directory, environment)
            root = directory / "squashfs-root"
            names = sorted(
                (entry.name for entry in root.glob("*.desktop")),
                key=lambda name: (not _matches(app, name), name),
            )
            if not names:
                return None
            relative = Path(names[0])
            if (root / relative).is_symlink():
                link = os.readlink(root / relative)
                if os.path.isabs(link):
                    return None
                relative = Path(os.path.normpath(link))
                if relative.parts[:1] in ((), ("..",), (".",)):
                    return None
                _extract(path, relative.as_posix(), directory, environment)
            text = _read_regular(root, relative)
            return keep_known(parse_groups(text)) if text is not None else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
