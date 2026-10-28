"""Menu entries, the launcher shortcut, autostart and saved desktop metadata."""

import contextlib
import json
import shlex
import shutil
import subprocess
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

from appglider import APP_TASKBAR_ICON
from appglider.core.catalog import App
from appglider.core.storage import atomic_bytes, atomic_json
from appglider.install.appimage import check_appimage, clean_environment
from appglider.install.desktopentry import (
    DesktopMetadata,
    Groups,
    extract_desktop_groups,
    metadata_from_groups,
    valid_groups,
)
from appglider.install.launch_options import LaunchOptions

if TYPE_CHECKING:
    from appglider.install.library import Installed

AUTOSTART_FLAG = "--tray"
METADATA_MARKER = "X-AppGlider-Metadata=1"
DATABASE_SECONDS = 10


def desktop_exec(arguments: list[str]) -> str:
    quoted = []
    for argument in arguments:
        if any(ord(c) < 32 or ord(c) == 127 for c in argument):
            raise ValueError("The shortcut command contains control characters.")
        escaped = argument.replace("\\", "\\\\\\\\").replace("%", "%%")
        for char in ('"', "`", "$"):
            escaped = escaped.replace(char, "\\\\" + char)
        quoted.append(f'"{escaped}"')
    return " ".join(quoted)


class ShortcutsMixin:
    """Menu entry, launcher and autostart behaviour of `Library`.

    The members below belong to `Library`; they are declared here so the mixin type-checks on
    its own. The stubs exist only for the type checker and never replace `Library`'s own.
    """

    data_dir: Path
    desktop_dir: Path
    autostart_dir: Path

    if TYPE_CHECKING:

        @property
        def apps(self) -> tuple[App, ...]: ...

        def records(self) -> dict[str, Installed]: ...

        def launch_options(self, app_id: str) -> LaunchOptions: ...

        def icon_path(self, app: App) -> Path: ...

        def _lock(self, timeout: float = ...) -> contextlib.AbstractContextManager[None]: ...

        def _owned(self, app: App, path: Path) -> Path: ...

    def _remove_dangling_shortcuts(self, removed: list[Path]) -> list[str]:
        """Delete menu entries, such as an app's own, that launch a removed AppImage."""
        targets = {str(path) for path in removed}
        errors: list[str] = []
        try:
            entries = sorted(self.desktop_dir.glob("*.desktop"))
        except OSError:
            return errors
        for entry in entries:
            try:
                if entry.is_symlink() or not entry.is_file():
                    continue
                lines = entry.read_text().splitlines()
                command = next(shlex.split(line[5:]) for line in lines if line.startswith("Exec="))
            except (OSError, ValueError, StopIteration):
                continue
            if command and command[0] in targets:
                try:
                    entry.unlink()
                except OSError as error:
                    errors.append(f"{entry}: {error}")
        return errors

    def create_shortcut(self, app: App) -> None:
        with contextlib.suppress(OSError, ValueError):
            current = self.records().get(app.id)
            if current and current.managed and current.path.is_file():
                self._owned(app, current.path)
                check_appimage(current.path)
                self.collect_desktop_metadata(app, current.path, current.path)
        with self._lock():
            installed = self.records().get(app.id)
            if not installed or not installed.managed or not installed.path.is_file():
                raise ValueError("Install this app with AppGlider first.")
            self._owned(app, installed.path)
            self._shortcut(app, installed.path)
        self.update_desktop_database()

    def refresh_shortcuts(self, cancel: Event | None = None) -> int:
        """Give menu entries written before app metadata was read their app's own details.

        Only entries AppGlider wrote for apps it manages are touched, and none is created.
        Run it away from the Qt thread: it starts each app's runtime to read the entry.
        """
        changed = 0
        for app in self.apps:
            if cancel is not None and cancel.is_set():
                break
            shortcut = self.desktop_dir / f"appglider-{app.id}.desktop"
            try:
                installed = self.records().get(app.id)
                if (
                    installed is None
                    or not installed.managed
                    or not installed.path.is_file()
                    or not self._owned_shortcut(shortcut)
                    or METADATA_MARKER in shortcut.read_text().splitlines()
                ):
                    continue
                self._owned(app, installed.path)
                check_appimage(installed.path)
                self.collect_desktop_metadata(app, installed.path, installed.path)
                with self._lock():
                    current = self.records().get(app.id)
                    if current is None or current.path != installed.path:
                        continue
                    if METADATA_MARKER in shortcut.read_text().splitlines():
                        continue
                    self._shortcut(app, installed.path)
                changed += 1
            except (OSError, ValueError, UnicodeDecodeError):
                continue
        if changed:
            self.update_desktop_database()
        return changed

    def refresh_launcher_shortcut(self, arguments: list[str], packaged: bool) -> Path:
        """Write the menu entry unless that would needlessly repoint a working one.

        An existing entry is kept while its target still exists, except that a packaged
        image newer than that target takes over. Development runs never repoint it.
        """
        shortcut = self.desktop_dir / "appglider.desktop"
        if shortcut.is_file() and not shortcut.is_symlink():
            lines = shortcut.read_text().splitlines()
            current = next((line[5:] for line in lines if line.startswith("Exec=")), "")
            if "X-AppGlider=true" in lines:
                self._refresh_launcher_icon()
                if current == desktop_exec(arguments):
                    return shortcut
                try:
                    age = Path(shlex.split(current)[0]).stat().st_mtime_ns
                    newer = packaged and Path(arguments[0]).stat().st_mtime_ns > age
                except (ValueError, IndexError, OSError):
                    newer = True
                if not newer:
                    return shortcut
        return self.create_launcher_shortcut(arguments)

    def _refresh_launcher_icon(self) -> None:
        """Replace the shortcut's icon copy when a new AppGlider ships a different icon."""
        icon = self.data_dir / "icons" / "appglider.png"
        if icon.parent.is_symlink() or icon.is_symlink():
            return
        with contextlib.suppress(OSError):
            data = APP_TASKBAR_ICON.read_bytes()
            if not icon.is_file() or icon.read_bytes() != data:
                atomic_bytes(icon, data)

    def _write_launcher_icon(self) -> Path:
        icon = self.data_dir / "icons" / "appglider.png"
        if icon.parent.is_symlink() or icon.is_symlink():
            raise OSError("The shortcut icon path is a symbolic link; it was left untouched.")
        atomic_bytes(icon, APP_TASKBAR_ICON.read_bytes())
        return icon

    @property
    def autostart_file(self) -> Path:
        return self.autostart_dir / "appglider.desktop"

    def _autostart_lines(self) -> list[str] | None:
        """Return our autostart entry's lines, or None if it is absent, foreign or a link."""
        path = self.autostart_file
        try:
            if path.is_symlink() or not path.is_file():
                return None
            lines = path.read_text().splitlines()
        except (OSError, ValueError):
            return None
        return lines if "X-AppGlider=true" in lines else None

    def autostart_enabled(self) -> bool:
        return self._autostart_lines() is not None

    def enable_autostart(self, arguments: list[str]) -> Path:
        """Write the login entry that opens AppGlider in the tray."""
        command = desktop_exec([*arguments, AUTOSTART_FLAG])
        path = self.autostart_file
        if (path.is_symlink() or path.exists()) and self._autostart_lines() is None:
            raise OSError(
                f"{path} already exists and belongs to another application; it was left untouched."
            )
        self.autostart_dir.mkdir(parents=True, exist_ok=True)
        icon = self._write_launcher_icon()
        atomic_bytes(
            path,
            (
                "[Desktop Entry]\nType=Application\nName=AppGlider\n"
                "Comment=Install, update and launch Linux AppImages\n"
                f"Exec={command}\nIcon={icon}\nTerminal=false\n"
                "X-GNOME-Autostart-enabled=true\nX-AppGlider=true\n"
            ).encode(),
        )
        return path

    def disable_autostart(self) -> None:
        """Remove our entry; a link or a file without our marker is never touched."""
        path = self.autostart_file
        if self._autostart_lines() is not None:
            path.unlink(missing_ok=True)
        elif path.is_symlink() or path.exists():
            raise OSError(f"{path} belongs to another application; it was left untouched.")

    def refresh_autostart(self, arguments: list[str], packaged: bool) -> None:
        """Repair our entry when its target is gone or a newer packaged image runs.

        Like the menu shortcut, a development run or an older image never repoints a working
        entry, and the entry is never created here.
        """
        lines = self._autostart_lines()
        if lines is None:
            return
        current = next((line[5:] for line in lines if line.startswith("Exec=")), "")
        if current == desktop_exec([*arguments, AUTOSTART_FLAG]):
            return
        try:
            age = Path(shlex.split(current)[0]).stat().st_mtime_ns
            newer = packaged and Path(arguments[0]).stat().st_mtime_ns > age
        except (ValueError, IndexError, OSError):
            newer = True
        if newer:
            self.enable_autostart(arguments)

    def create_launcher_shortcut(self, arguments: list[str]) -> Path:
        command = desktop_exec(arguments)
        self.desktop_dir.mkdir(parents=True, exist_ok=True)
        shortcut = self.desktop_dir / "appglider.desktop"
        if shortcut.is_symlink() or (
            shortcut.exists() and "X-AppGlider=true" not in shortcut.read_text().splitlines()
        ):
            raise OSError("The existing AppGlider shortcut belongs to another application.")
        icon = self._write_launcher_icon()
        atomic_bytes(
            shortcut,
            (
                "[Desktop Entry]\nType=Application\nName=AppGlider\n"
                "Comment=Install, update and launch Linux AppImages\n"
                f"Exec={command}\nIcon={icon}\nTerminal=false\n"
                "Categories=Utility;\nX-AppGlider=true\n"
            ).encode(),
        )
        return shortcut

    def _owned_shortcut(self, shortcut: Path) -> bool:
        """Whether this is a regular menu entry that AppGlider wrote."""
        try:
            return (
                not shortcut.is_symlink()
                and shortcut.is_file()
                and "X-AppGlider=true" in shortcut.read_text().splitlines()
            )
        except (OSError, UnicodeDecodeError):
            return False

    def _metadata_path(self, app: App) -> Path:
        return self.data_dir / "desktop" / f"{app.id}.json"

    @staticmethod
    def _stamp(path: Path) -> dict[str, object]:
        info = path.stat()
        return {"path": str(path), "size": info.st_size, "mtime_ns": info.st_mtime_ns}

    def read_desktop_metadata(
        self, app: App, source: Path, installed: Path
    ) -> tuple[dict[str, object], Groups | None] | None:
        """Read the menu details inside a verified AppImage, without saving them.

        `source` is the file to read; `installed` is where it is, or is about to be, kept,
        which is what the saved details are tied to along with its size and modification
        time. Returns None when the saved details already match or cannot be read, and
        otherwise what `save_desktop_metadata` keeps. This runs the AppImage's own runtime.
        """
        try:
            stamp = {**self._stamp(source), "path": str(installed)}
            cached = self._cached_groups(app)
            if cached is not None and cached[0] == stamp:
                return None
            return stamp, extract_desktop_groups(source, app, clean_environment())
        except (OSError, ValueError):
            return None

    def save_desktop_metadata(
        self, app: App, read: tuple[dict[str, object], Groups | None] | None
    ) -> None:
        """Keep what `read_desktop_metadata` returned; without details the shortcut is plain."""
        if read is None:
            return
        stamp, groups = read
        try:
            path = self._metadata_path(app)
            if groups is None:
                path.unlink(missing_ok=True)
            elif not path.parent.is_symlink():
                atomic_json(path, {**stamp, "groups": groups})
        except (OSError, ValueError):
            pass

    def collect_desktop_metadata(self, app: App, source: Path, installed: Path) -> None:
        """Read and keep the menu details of a file that is already the active one."""
        self.save_desktop_metadata(app, self.read_desktop_metadata(app, source, installed))

    def _cached_groups(self, app: App) -> tuple[dict[str, object], Groups] | None:
        try:
            path = self._metadata_path(app)
            if path.parent.is_symlink() or path.is_symlink() or path.stat().st_size > 256 * 1024:
                return None
            data = json.loads(path.read_text())
            groups = valid_groups(data["groups"])
            stamp = {key: data[key] for key in ("path", "size", "mtime_ns")}
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return (stamp, groups) if groups is not None else None

    def _desktop_metadata(self, app: App, path: Path) -> DesktopMetadata | None:
        cached = self._cached_groups(app)
        try:
            if cached is None or cached[0] != self._stamp(path):
                return None
        except OSError:
            return None
        return metadata_from_groups(cached[1])

    def update_desktop_database(self) -> None:
        """Ask the desktop to notice file types in the entries; a missing tool is fine."""
        tool = shutil.which("update-desktop-database")
        if tool is None:
            return
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                [tool, str(self.desktop_dir)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=DATABASE_SECONDS,
                check=False,
            )

    def _shortcut(self, app: App, path: Path) -> None:
        self.desktop_dir.mkdir(parents=True, exist_ok=True)
        shortcut = self.desktop_dir / f"appglider-{app.id}.desktop"
        if shortcut.is_symlink() or (
            shortcut.exists() and "X-AppGlider=true" not in shortcut.read_text().splitlines()
        ):
            raise OSError("An existing desktop shortcut is not owned by this launcher.")
        icon = self.data_dir / "icons" / f"{app.id}.png"
        if icon.parent.is_symlink() or icon.is_symlink():
            raise OSError("The shortcut icon path is a symbolic link; it was left untouched.")
        source = self.icon_path(app)
        if source.resolve() != icon.resolve():
            atomic_bytes(icon, source.read_bytes())
        try:
            options = self.launch_options(app.id)
        except ValueError:
            options = LaunchOptions()  # launching reports the unreadable file
        text = shortcut_text(app, path, icon, self._desktop_metadata(app, path), options)
        atomic_bytes(shortcut, text.encode())


def shortcut_text(
    app: App,
    path: Path,
    icon: Path,
    metadata: DesktopMetadata | None,
    options: LaunchOptions,
) -> str:
    """The menu entry for an installed app, using the app's own details when it gave any."""
    metadata = metadata or DesktopMetadata()
    prefix = (
        ["env", *(f"{name}={value}" for name, value in options.environment)]
        if options.environment
        else []
    )
    code = "%U" if metadata.urls else "%F"
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={app.name} (AppGlider)",
    ]
    if metadata.comment:
        lines.append(f"Comment={metadata.comment}")
    lines += [
        f"Exec={desktop_exec([*prefix, str(path), *options.arguments])} {code}",
        f"Icon={icon}",
        f"Terminal={'true' if metadata.terminal else 'false'}",
        f"Categories={''.join(f'{item};' for item in metadata.categories) or 'Utility;'}",
    ]
    for key, values in (("MimeType", metadata.mime_types), ("Keywords", metadata.keywords)):
        if values:
            lines.append(f"{key}={''.join(f'{item};' for item in values)}")
    if metadata.wm_class:
        lines.append(f"StartupWMClass={metadata.wm_class}")
    if metadata.startup_notify is not None:
        lines.append(f"StartupNotify={'true' if metadata.startup_notify else 'false'}")
    if metadata.actions:
        lines.append(f"Actions={''.join(f'{action.id};' for action in metadata.actions)}")
    lines += ["X-AppGlider=true", METADATA_MARKER]
    for action in metadata.actions:
        command = desktop_exec([*prefix, str(path), *action.arguments])
        lines += ["", f"[Desktop Action {action.id}]", f"Name={action.name}", f"Exec={command}"]
    return "\n".join(lines) + "\n"
