"""Install, update and launch this launcher’s private AppImage library."""

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from appglider import APP_ICON
from appglider.core.catalog import (
    App,
    custom_app,
    default_description,
    load_apps,
    load_build_preferences,
    load_release_preferences,
    load_update_holds,
    valid_build_variant,
)
from appglider.core.catalogfile import CatalogEntry, entry_for
from appglider.core.storage import atomic_bytes, atomic_json
from appglider.github.assets import Release
from appglider.github.download import Progress, download
from appglider.github.http import file_stamp
from appglider.github.icons import MAX_ICON_BYTES, normalise_icon
from appglider.github.versions import VERSION, check_cancel, version_key
from appglider.install.appimage import check_appimage, clean_environment
from appglider.install.launch_options import LaunchOptions, load_launch_options, options_json
from appglider.install.shortcuts import ShortcutsMixin

FREE_SPACE_MARGIN = 64 * 1024 * 1024
MAX_LOG_BYTES = 5 * 1024 * 1024
STALE_STAGE_SECONDS = 24 * 60 * 60
LOCK_WAIT_SECONDS = 3.0
MAX_APPS = 100


@dataclass(frozen=True)
class Orphan:
    """A library entry for an application this catalog does not currently know."""

    app_id: str
    path: str
    version: str


@dataclass(frozen=True)
class ImportPlan:
    new: tuple[CatalogEntry, ...]
    existing: tuple[CatalogEntry, ...]


@dataclass(frozen=True)
class Installed:
    path: Path
    version: str | None
    managed: bool = False
    previous: Path | None = None
    prerelease: bool = False
    previous_prerelease: bool = False


@dataclass(frozen=True)
class PreviousCopy:
    path: Path
    version: str
    prerelease: bool
    size: int


def previous_copy(installed: Installed | None) -> PreviousCopy | None:
    """Describe the kept previous copy, or None when there is none or it is not a file."""
    if installed is None or installed.previous is None:
        return None
    try:
        info = installed.previous.stat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    return PreviousCopy(
        installed.previous,
        installed.previous.parent.name,
        installed.previous_prerelease,
        info.st_size,
    )


@dataclass(frozen=True)
class _InstallPlan:
    root: Path
    old: Installed | None
    destination: Path
    exists: bool
    made_root: bool


NIXOS_MARKER = Path("/etc/NIXOS")
BINFMT_DIR = Path("/proc/sys/fs/binfmt_misc")
NIXOS_APPIMAGE_HELP = (
    "NixOS cannot start AppImages until AppImage support is turned on. Add "
    "programs.appimage.enable = true; and programs.appimage.binfmt = true; to your NixOS "
    "configuration, run sudo nixos-rebuild switch, then try again."
)


def appimages_registered() -> bool | None:
    """Whether the kernel hands AppImages to a runner; None when that cannot be read."""
    try:
        if BINFMT_DIR.joinpath("status").read_text().strip() != "enabled":
            return False
        entries = list(BINFMT_DIR.iterdir())
    except OSError:
        return None
    for entry in entries:
        if entry.name in ("register", "status"):
            continue
        try:
            lines = entry.read_text().splitlines()
        except OSError:
            continue
        # Type 2 AppImages are recognised by the bytes "AI\x02" (hex 414902).
        if lines[:1] == ["enabled"] and any(
            line.startswith("magic ") and "414902" in line for line in lines
        ):
            return True
    return False


def nixos_cannot_start() -> bool:
    """NixOS stops generic Linux programs, which AppImages carry, unless they run through
    its AppImage support (binfmt) or nix-ld."""
    return (
        NIXOS_MARKER.exists() and not os.environ.get("NIX_LD") and appimages_registered() is False
    )


def running(path: Path) -> bool:
    target = os.fsencode(path.resolve())
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            if (process / "exe").resolve() == path.resolve():
                return True
            with (process / "environ").open("rb") as source:
                environment = source.read(1024 * 1024).split(b"\0")
            if b"APPIMAGE=" + target in environment:
                return True
        except OSError:
            continue
    return False


class Library(ShortcutsMixin):
    def __init__(
        self,
        data_dir: Path,
        apps_dir: Path,
        desktop_dir: Path | None = None,
        autostart_dir: Path | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.apps_dir = apps_dir
        home = Path.home()
        xdg_data = Path(os.environ.get("XDG_DATA_HOME", str(home / ".local/share")))
        self.desktop_dir = desktop_dir if desktop_dir is not None else xdg_data / "applications"
        xdg_config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
        self.autostart_dir = (
            autostart_dir if autostart_dir is not None else xdg_config / "autostart"
        )
        self.processes: dict[str, subprocess.Popen[bytes]] = {}
        self._catalog: tuple[tuple[tuple[int, int, int] | None, ...], tuple[App, ...]] | None = None

    @property
    def apps(self) -> tuple[App, ...]:
        paths = (
            self.data_dir / "repositories.json",
            self.data_dir / "release-preferences.json",
            self.data_dir / "build-preferences.json",
            self.data_dir / "update-holds.json",
        )
        stamps = tuple(file_stamp(path) for path in paths)
        if self._catalog is not None and self._catalog[0] == stamps:
            return self._catalog[1]
        # Failures raise before anything is stored, so a corrupt file is reported every time.
        apps = load_apps(*paths)
        self._catalog = (stamps, apps)
        return apps

    def _save_build_variant(self, app_id: str, variant: str) -> None:
        if not valid_build_variant(variant):
            raise ValueError("Invalid AppImage build preference.")
        path = self.data_dir / "build-preferences.json"
        preferences = load_build_preferences(path)
        if variant:
            preferences[app_id] = variant
        else:
            preferences.pop(app_id, None)
        if preferences or path.exists():
            atomic_json(path, preferences)

    def set_build_variant(self, app_id: str, variant: str) -> None:
        with self._lock():
            if app_id not in {app.id for app in self.apps}:
                raise ValueError("This application is not in the catalog.")
            self._save_build_variant(app_id, variant)

    def _save_release_preference(self, app_id: str, include: bool) -> None:
        if type(include) is not bool:
            raise ValueError("Invalid release preference.")
        path = self.data_dir / "release-preferences.json"
        preferences = load_release_preferences(path)
        if include:
            preferences[app_id] = True
        else:
            preferences.pop(app_id, None)
        if preferences or path.exists():
            atomic_json(path, preferences)

    def set_release_preference(self, app_id: str, include: bool) -> None:
        with self._lock():
            if app_id not in {app.id for app in self.apps}:
                raise ValueError("This application is not in the catalog.")
            self._save_release_preference(app_id, include)

    def _save_update_hold(self, app_id: str, held: bool) -> None:
        if type(held) is not bool:
            raise ValueError("Invalid update hold.")
        path = self.data_dir / "update-holds.json"
        holds = load_update_holds(path)
        if held:
            holds[app_id] = True
        else:
            holds.pop(app_id, None)
        if holds or path.exists():
            atomic_json(path, holds)

    def set_update_hold(self, app_id: str, held: bool) -> None:
        with self._lock():
            if app_id not in {app.id for app in self.apps}:
                raise ValueError("This application is not in the catalog.")
            record = self.records().get(app_id)
            if held and (record is None or not record.managed):
                raise ValueError("Only applications installed by AppGlider can be held.")
            self._save_update_hold(app_id, held)

    def launch_options(self, app_id: str) -> LaunchOptions:
        return load_launch_options(self.data_dir / "launch-options.json").get(
            app_id, LaunchOptions()
        )

    def _save_launch_options(self, app_id: str, options: LaunchOptions) -> None:
        path = self.data_dir / "launch-options.json"
        saved = load_launch_options(path)
        if options:
            saved[app_id] = options
        else:
            saved.pop(app_id, None)
        if saved or path.exists():
            atomic_json(path, options_json(saved))

    def set_launch_options(
        self, app_id: str, options: LaunchOptions, refresh_database: bool = True
    ) -> None:
        """Save the options and, when the app has a menu entry of ours, rewrite it to match.

        The desktop database refresh runs a program; the Qt thread passes False and runs
        `update_desktop_database` elsewhere.
        """
        with self._lock():
            app = next((app for app in self.apps if app.id == app_id), None)
            if app is None:
                raise ValueError("This application is not in the catalog.")
            self._save_launch_options(app_id, options)
            installed = self.records().get(app_id)
            shortcut = self.desktop_dir / f"appglider-{app_id}.desktop"
            if (
                installed is not None
                and installed.managed
                and installed.path.is_file()
                and self._owned_shortcut(shortcut)
            ):
                try:
                    self._owned(app, installed.path)
                    self._shortcut(app, installed.path)
                except (OSError, ValueError) as error:
                    raise ValueError(
                        f"The options were saved, but the menu shortcut could not be updated: "
                        f"{error}"
                    ) from error
        if refresh_database:
            self.update_desktop_database()

    def icon_path(self, app: App) -> Path:
        path = self.data_dir / "icons" / f"{app.id}.png"
        if not path.parent.is_symlink() and not path.is_symlink():
            try:
                if path.stat().st_size <= MAX_ICON_BYTES:
                    with path.open("rb") as source:
                        if source.read(8) == b"\x89PNG\r\n\x1a\n":
                            return path
            except OSError:
                pass
        return app.icon

    def set_icon(self, app: App, data: bytes | None) -> None:
        png = normalise_icon(data if data is not None else APP_ICON.read_bytes())
        with self._lock():
            if app not in self.apps:
                raise ValueError("This repository is not in the library.")
            directory = self.data_dir / "icons"
            if directory.is_symlink():
                raise ValueError("The icon folder is a symbolic link; it was left untouched.")
            atomic_bytes(directory / f"{app.id}.png", png)

    def add_repository(self, app: App) -> None:
        if app != custom_app(
            app.repo, app.name, app.include_prereleases, app.build_variant, app.description
        ):
            raise ValueError("Invalid custom repository details.")
        with self._lock():
            apps = self.apps
            if any(known.repo.casefold() == app.repo.casefold() for known in apps):
                raise ValueError("This repository is already in the catalog.")
            self.records()
            self._save_release_preference(app.id, app.include_prereleases)
            self._save_build_variant(app.id, app.build_variant)
            self._save_update_hold(app.id, app.hold_updates)
            self._save_repositories((*apps, app))

    def plan_import(self, entries: Sequence[CatalogEntry]) -> ImportPlan:
        """Say which catalog entries would be added and which are already in the library."""
        known = {app.repo.casefold() for app in self.apps}
        new: list[CatalogEntry] = []
        existing: list[CatalogEntry] = []
        for entry in entries:
            if entry.repo.casefold() in known:
                existing.append(entry)
            else:
                known.add(entry.repo.casefold())
                new.append(entry)
        return ImportPlan(tuple(new), tuple(existing))

    def import_catalog(self, entries: Sequence[CatalogEntry]) -> ImportPlan:
        """Add the entries that are not in the library yet; existing apps are left unchanged."""
        with self._lock():
            apps = self.apps
            plan = self.plan_import(entries)
            if len(apps) + len(plan.new) > MAX_APPS:
                raise ValueError(
                    f"The library supports up to {MAX_APPS} repositories; "
                    f"this import would make {len(apps) + len(plan.new)}."
                )
            added = tuple(
                custom_app(
                    entry.repo, entry.name, not entry.stable_only, entry.build, entry.description
                )
                for entry in plan.new
            )
            if added:
                self.records()
                for app in added:
                    self._save_release_preference(app.id, app.include_prereleases)
                    self._save_build_variant(app.id, app.build_variant)
                    self._save_update_hold(app.id, app.hold_updates)
                self._save_repositories((*apps, *added))
            return plan

    def export_entries(self) -> tuple[CatalogEntry, ...]:
        return tuple(entry_for(app) for app in self.apps)

    def remove_repository(self, app_id: str) -> None:
        with self._lock():
            apps = self.apps
            if app_id not in {app.id for app in apps}:
                raise ValueError("This repository is not in the library.")
            if app_id in self.records():
                raise ValueError(
                    "Uninstall this application's managed copy before removing its repository."
                )
            self._save_repositories(tuple(app for app in apps if app.id != app_id))
            # An orphan preference is harmless: adding this repository resets it.
            with contextlib.suppress(OSError):
                self._save_release_preference(app_id, False)
            with contextlib.suppress(OSError):
                self._save_build_variant(app_id, "")
            with contextlib.suppress(OSError):
                self._save_update_hold(app_id, False)
            with contextlib.suppress(OSError, ValueError):
                self._save_launch_options(app_id, LaunchOptions())
            with contextlib.suppress(OSError):
                icon = self.data_dir / "icons" / f"{app_id}.png"
                if not icon.parent.is_symlink():
                    icon.unlink(missing_ok=True)

    def _save_repositories(self, apps: tuple[App, ...]) -> None:
        if len(apps) > MAX_APPS:
            raise ValueError(f"The library supports up to {MAX_APPS} repositories.")
        atomic_json(
            self.data_dir / "repositories.json",
            [
                {"repo": app.repo, "name": app.name}
                | (
                    {}
                    if app.description == default_description(app.repo)
                    else {"description": app.description}
                )
                for app in apps
            ],
        )

    def _entries(self) -> dict[str, object]:
        path = self.data_dir / "library.json"
        if not path.exists():
            return {}
        try:
            if path.stat().st_size > 1024 * 1024:
                raise ValueError("The library file is too large.")
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                raise ValueError("Invalid library records.")
        except (OSError, ValueError) as error:
            raise ValueError(f"Cannot read {path}. The file was preserved: {error}") from error
        return data

    def _orphan_entries(self) -> dict[str, dict[str, object]]:
        known_ids = {app.id for app in self.apps}
        return {
            app_id: item
            for app_id, item in self._entries().items()
            if app_id not in known_ids and isinstance(item, dict)
        }

    def orphans(self) -> list[Orphan]:
        return [
            Orphan(app_id, str(item.get("path", ""))[:512], str(item.get("version") or "")[:128])
            for app_id, item in self._orphan_entries().items()
        ]

    def discard_orphans(self, app_ids: set[str]) -> None:
        """Forget the listed unrecognised entries; their files on disk are never touched."""
        with self._lock():
            self._save(self.records(), drop=app_ids)

    def records(self) -> dict[str, Installed]:
        path = self.data_dir / "library.json"
        data = self._entries()
        try:
            known_ids = {app.id for app in self.apps}
            result = {}
            for app_id, item in data.items():
                if not isinstance(item, dict):
                    raise ValueError("Invalid library entry.")
                if app_id not in known_ids:
                    continue  # kept in the file by _save until the user discards it
                location, version = item.get("path"), item.get("version")
                previous, managed = item.get("previous"), item.get("managed")
                prerelease = item.get("prerelease", False)
                previous_prerelease = item.get("previous_prerelease", False)
                if (
                    not isinstance(location, str)
                    or not Path(location).is_absolute()
                    or type(managed) is not bool
                    or type(prerelease) is not bool
                    or type(previous_prerelease) is not bool
                    or (
                        version is not None
                        and (
                            not isinstance(version, str)
                            or len(version) > 128
                            or not re.fullmatch(VERSION, version)
                        )
                    )
                    or (
                        previous is not None
                        and (not isinstance(previous, str) or not Path(previous).is_absolute())
                    )
                ):
                    raise ValueError("Invalid installation record.")
                result[app_id] = Installed(
                    Path(location),
                    version,
                    managed,
                    Path(previous) if previous is not None else None,
                    prerelease,
                    previous_prerelease,
                )
            return result
        except (OSError, ValueError) as error:
            raise ValueError(f"Cannot read {path}. The file was preserved: {error}") from error

    def reap_processes(self) -> None:
        """Forget launched applications that have exited so they do not linger as zombies."""
        self.processes = {key: proc for key, proc in self.processes.items() if proc.poll() is None}

    def _save(self, records: dict[str, Installed], drop: set[str] | None = None) -> None:
        saved: dict[str, object] = {
            app_id: item
            for app_id, item in self._orphan_entries().items()
            if app_id not in (drop or ())
        }
        for app_id, item in records.items():
            saved[app_id] = {
                "path": str(item.path),
                "version": item.version,
                "managed": item.managed,
                "previous": str(item.previous) if item.previous else None,
                "prerelease": item.prerelease,
                "previous_prerelease": item.previous_prerelease,
            }
        atomic_json(self.data_dir / "library.json", saved)

    @contextlib.contextmanager
    def _lock(self, timeout: float = LOCK_WAIT_SECONDS) -> Iterator[None]:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with (self.data_dir / "library.lock").open("a") as lock:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as error:
                    if time.monotonic() >= deadline:
                        raise ValueError(
                            "Another launcher is changing this library. Try again shortly."
                        ) from error
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def scan(self) -> dict[str, Installed]:
        return {
            key: value
            for key, value in self.records().items()
            if value.managed and value.path.is_file()
        }

    def launch(self, app: App, installed: Installed) -> Path:
        if not installed.managed:
            raise ValueError("Only apps installed by AppGlider can be launched here.")
        self._owned(app, installed.path)
        check_appimage(installed.path)
        if not os.access(installed.path, os.X_OK):
            raise ValueError("Make this AppImage executable in file properties, then try again.")
        if nixos_cannot_start():
            raise ValueError(NIXOS_APPIMAGE_HELP)
        options = self.launch_options(app.id)
        environment = {**clean_environment(), **dict(options.environment)}
        log = self.data_dir / "logs" / f"{app.id}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        self.reap_processes()
        if self.processes.get(app.id) is not None:
            raise ValueError(f"{app.name} is already running from this launcher.")
        with contextlib.suppress(OSError):
            if log.stat().st_size > MAX_LOG_BYTES:
                os.replace(log, log.with_name(log.name + ".1"))
        with log.open("ab") as output:
            process = subprocess.Popen(
                [str(installed.path), *options.arguments],
                cwd=installed.path.parent,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        self.processes[app.id] = process
        return log

    def _marker(self, app: App) -> str:
        return f"AppGlider\n{app.id}\n{app.repo}\n"

    def _has_marker(self, root: Path, app: App) -> bool:
        try:
            return (root / ".appglider").read_text() == self._marker(app)
        except (OSError, UnicodeDecodeError):
            return False

    def _owned(self, app: App, path: Path) -> Path:
        root = path.parent.parent
        if (
            root.name != app.id
            or root.is_symlink()
            or path.parent.is_symlink()
            or path.is_symlink()
            or not re.fullmatch(VERSION, path.parent.name)
            or path.suffix.lower() != ".appimage"
            or not self._has_marker(root, app)
        ):
            raise ValueError("This path is not owned by AppGlider; it was left untouched.")
        return root

    def _clear_stale_stages(self, root: Path) -> None:
        # Interrupted downloads leave hidden staging files; only old regular files qualify.
        with contextlib.suppress(OSError):
            for stage in root.glob(".download-*"):
                if (
                    not stage.is_symlink()
                    and stage.is_file()
                    and time.time() - stage.stat().st_mtime > STALE_STAGE_SECONDS
                ):
                    stage.unlink()

    def _remove_file(self, app: App, path: Path) -> None:
        self._owned(app, path)
        if running(path):
            raise ValueError(f"{app.name} is running. Close it before removing this version.")
        path.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            path.parent.rmdir()

    def _install_checks(
        self, app: App, release: Release
    ) -> tuple[dict[str, Installed], Installed | None]:
        # Settings may change during a download, so use the catalog's current preferences.
        current = next((a for a in self.apps if a.id == app.id and a.repo == app.repo), None)
        if current is None:
            raise ValueError("Add this repository to the catalog before installing its app.")
        if release.prerelease and not current.include_prereleases:
            raise ValueError("Enable prereleases for this application before installing a preview.")
        if not re.fullmatch(VERSION, release.version) or Path(release.name).name != release.name:
            raise ValueError("The release has an unsafe filename or version.")
        records = self.records()
        old = records.get(app.id)
        if old and not old.managed:
            # Ignore legacy links; a successful install replaces only their registry entry.
            old = None
        if old and version_key(release.version) < version_key(old.version):
            raise ValueError(
                "The selected release is older than the installed version; it was kept."
            )
        return records, old

    def _prepare_install(self, app: App, release: Release) -> _InstallPlan:
        _, old = self._install_checks(app, release)
        root = self._owned(app, old.path) if old else self.apps_dir.resolve() / app.id
        if root.is_symlink():
            raise ValueError("The installation folder is a symbolic link.")
        made_root = not root.exists()
        if made_root:
            root.mkdir(parents=True)
            (root / ".appglider").write_text(self._marker(app))
        elif not self._has_marker(root, app):
            raise ValueError("The installation folder belongs to another application.")
        try:
            self._clear_stale_stages(root)
            version_dir = root / release.version
            if version_dir.is_symlink():
                raise ValueError("The installation folder is a symbolic link.")
            destination = version_dir / release.name
            if destination.is_symlink():
                raise ValueError("The installed file is a symbolic link.")
            exists = destination.exists()
            if exists:
                self._verify_existing(release, destination)
        except BaseException:
            if made_root:
                self._remove_empty_root(root)
            raise
        return _InstallPlan(root, old, destination, exists, made_root)

    def _verify_existing(self, release: Release, destination: Path) -> None:
        with destination.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != release.sha256 or destination.stat().st_size != release.size:
            raise ValueError("The existing version has a different checksum; it was kept.")
        check_appimage(destination)

    def _remove_empty_root(self, root: Path) -> None:
        # Drop the ownership marker and folder only when nothing else is left inside.
        with contextlib.suppress(OSError):
            marker = root / ".appglider"
            if [entry.name for entry in root.iterdir()] == [marker.name]:
                marker.unlink()
                root.rmdir()

    def install(self, app: App, release: Release, cancel: Event, progress: Progress) -> Installed:
        # The download runs outside the library lock so other changes are not blocked by it.
        with self._lock():
            plan = self._prepare_install(app, release)
        if plan.old and plan.exists and plan.old.path == plan.destination:
            check_cancel(cancel)
            return plan.old
        stage: Path | None = None
        try:
            if not plan.exists:
                fd, temporary = tempfile.mkstemp(prefix=".download-", dir=plan.root)
                os.close(fd)
                stage = Path(temporary)
                if shutil.disk_usage(plan.root).free < release.size + FREE_SPACE_MARGIN:
                    raise ValueError("There is not enough free disk space for this download.")
                download(release, stage, cancel, progress)
                check_appimage(stage)
                stage.chmod(0o755)
            check_cancel(cancel)
            # Reading the menu metadata runs the AppImage's own runtime, so it happens here,
            # on the verified file, rather than while the library lock is held.
            # It is only saved once the install is active, so a failed one leaves the
            # details of the version that stays.
            metadata = self.read_desktop_metadata(
                app, stage if stage is not None else plan.destination, plan.destination
            )
            with self._lock():
                installed = self._activate_install(app, release, plan, stage)
                self.save_desktop_metadata(app, metadata)
                old = plan.old
                if old and old.previous and old.previous != installed.path:
                    with contextlib.suppress(OSError, ValueError):
                        self._remove_file(app, old.previous)
                try:
                    self._shortcut(app, installed.path)
                except OSError as error:
                    raise ValueError(
                        f"{app.name} was installed, but its shortcut could not be saved: {error}"
                    ) from error
            self.update_desktop_database()
        except BaseException:
            if plan.made_root and not plan.exists:
                self._remove_empty_root(plan.root)
            raise
        finally:
            if stage is not None:
                stage.unlink(missing_ok=True)
        return installed

    def _activate_install(
        self, app: App, release: Release, plan: _InstallPlan, stage: Path | None
    ) -> Installed:
        records, old = self._install_checks(app, release)
        if old != plan.old:
            raise ValueError(
                f"The library changed while {app.name} was downloading. Try the update again."
            )
        destination = plan.destination
        version_dir = destination.parent
        if version_dir.is_symlink() or destination.is_symlink():
            raise ValueError("The installation folder is a symbolic link.")
        made_dir = not version_dir.exists()
        created = False
        try:
            version_dir.mkdir(exist_ok=True)
            if destination.exists():
                if plan.exists:
                    # Hashed when prepared; only re-check what could change cheaply.
                    check_appimage(destination)
                    if destination.stat().st_size != release.size:
                        raise ValueError("The existing version changed; it was kept.")
                else:
                    self._verify_existing(release, destination)
            elif stage is not None:
                os.replace(stage, destination)
                created = True
            else:
                raise ValueError("The downloaded file is missing. Try the update again.")
            if old and old.path == destination:
                return old
            installed = Installed(
                destination,
                release.version,
                True,
                old.path if old else None,
                release.prerelease,
                old.prerelease if old else False,
            )
            records[app.id] = installed
            self._save(records)
        except BaseException:
            if created:
                with contextlib.suppress(OSError):
                    destination.unlink(missing_ok=True)
            if made_dir:
                with contextlib.suppress(OSError):
                    version_dir.rmdir()
            raise
        return installed

    def rollback(self, app: App) -> None:
        metadata = None
        with contextlib.suppress(OSError, ValueError):
            kept = self.records().get(app.id)
            if kept and kept.managed and kept.previous:
                self._owned(app, kept.previous)
                check_appimage(kept.previous)
                metadata = self.read_desktop_metadata(app, kept.previous, kept.previous)
        with self._lock():
            records = self.records()
            old = records.get(app.id)
            if not old or not old.managed or not old.previous:
                raise ValueError("There is no previous managed version to restore.")
            self._owned(app, old.path)
            self._owned(app, old.previous)
            check_appimage(old.previous)
            records[app.id] = Installed(
                old.previous,
                old.previous.parent.name,
                True,
                old.path,
                old.previous_prerelease,
                old.prerelease,
            )
            self._save(records)
            self.save_desktop_metadata(app, metadata)
            self._shortcut(app, old.previous)
        self.update_desktop_database()

    def remove(self, app: App) -> None:
        with self._lock():
            records = self.records()
            old = records.get(app.id)
            if not old or not old.managed:
                raise ValueError("Only applications installed by this launcher can be removed.")
            paths = list(dict.fromkeys(p for p in (old.path, old.previous) if p is not None))
            root = self._owned(app, old.path)
            for path in paths:
                self._owned(app, path)
                if running(path):
                    raise ValueError(f"Close {app.name} before removing it.")
                if path.exists() and not path.is_file():
                    raise ValueError(f"The AppImage path is not a regular file: {path}")
            # Keep both binaries recoverable until the library change has been saved.
            staged: list[tuple[Path, Path]] = []
            try:
                for path in paths:
                    if not path.exists():
                        continue
                    fd, temporary = tempfile.mkstemp(prefix=".uninstall-", dir=path.parent)
                    os.close(fd)
                    stage = Path(temporary)
                    try:
                        path.rename(stage)
                    except OSError:
                        stage.unlink()
                        raise
                    staged.append((path, stage))
                del records[app.id]
                self._save(records)
            except OSError as error:
                recovery = []
                for path, stage in reversed(staged):
                    try:
                        stage.rename(path)
                    except OSError:
                        recovery.append(f"{stage} → {path}")
                if recovery:
                    raise OSError(
                        f"Uninstall failed ({error}). Restore these preserved AppImages manually: "
                        + "; ".join(recovery)
                    ) from error
                raise
            errors = []
            for path, stage in staged:
                try:
                    stage.unlink()
                except OSError as error:
                    errors.append(f"{stage}: {error}")
                with contextlib.suppress(OSError):
                    path.parent.rmdir()
            self._remove_empty_root(root)
            try:
                self._save_update_hold(app.id, False)
            except (OSError, ValueError) as error:
                errors.append(str(error))
            with contextlib.suppress(OSError):
                self._metadata_path(app).unlink(missing_ok=True)
            shortcut = self.desktop_dir / f"appglider-{app.id}.desktop"
            try:
                if (
                    not shortcut.is_symlink()
                    and shortcut.is_file()
                    and "X-AppGlider=true" in shortcut.read_text().splitlines()
                ):
                    shortcut.unlink()
            except OSError as error:
                errors.append(f"{shortcut}: {error}")
            errors.extend(self._remove_dangling_shortcuts(paths))
            if errors:
                raise OSError(
                    f"{app.name} was uninstalled, but cleanup failed: " + "; ".join(errors)
                )
