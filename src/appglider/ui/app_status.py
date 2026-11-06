"""What an app's row and App page say about it: one status, worked out in one place."""

from dataclasses import dataclass
from datetime import datetime

from appglider.core.catalog import App
from appglider.github.assets import Release
from appglider.github.http import UNREACHABLE
from appglider.github.release_info import ReleaseInfo
from appglider.github.versions import version_key
from appglider.install.library import Installed

# The `working` state of an app that Update all has not reached yet.
WAITING = "Waiting"


@dataclass(frozen=True)
class AppStatus:
    """The primary action (`mode`) and the status chip (`state`, `text`, `tooltip`)."""

    mode: str
    state: str
    text: str
    tooltip: str
    build_required: bool


MANUAL_TEXT = "◇ Manual"
RUNNING_TEXT = "▶ Running"
# Chips that say nothing needing action: a running app shows "Running" in their place.
QUIET_TEXTS = ("✓ Up to date", "Only previews", "Not checked", MANUAL_TEXT)


def version_text(version: str | None) -> str:
    """Show a version with a leading v when it starts with a digit."""
    if not version:
        return "unknown"
    return f"v{version}" if version[:1].isdigit() else version


def busy_text(working: str, progress: int | None) -> str:
    """ "Updating…", or "Updating… 43%" once the work reports how far it has come."""
    if progress is None:
        return f"{working}…"
    return f"{working}… {max(0, min(100, progress))}%"


def manual_status(
    installed: Installed | None,
    working: str,
    missing: Installed | None,
    running: bool = False,
    progress: int | None = None,
) -> AppStatus:
    """An app added from a file has no releases: it is launched, replaced or removed."""
    if working:
        return AppStatus("cancel", "busy", busy_text(working, progress), "", False)
    if installed:
        tooltip = "Added from a file\nAppGlider cannot check it for updates"
        if running:
            return AppStatus("launch", "busy", RUNNING_TEXT, f"Running now\n{tooltip}", False)
        return AppStatus("launch", "none", MANUAL_TEXT, tooltip, False)
    if missing:
        tooltip = f"The AppImage is no longer at {missing.path}\nChoose a file to replace it"
        return AppStatus("replace", "missing", "⚠ File missing", tooltip, False)
    return AppStatus("replace", "none", "Not installed", "", False)


def app_status(
    app: App,
    installed: Installed | None,
    release: Release | None,
    error: str = "",
    info: ReleaseInfo | None = None,
    refreshing: bool = False,
    refresh_error: str = "",
    working: str = "",
    missing: Installed | None = None,
    running: bool = False,
    progress: int | None = None,
) -> AppStatus:
    """`missing` is AppGlider's record of an installation whose file has since gone.

    `running` says the installed copy is running now; `progress` is the percentage of the
    current work, shown in the busy chip when known.
    """
    if app.manual:
        return manual_status(installed, working, missing, running, progress)
    held = bool(installed and app.hold_updates)
    newer_release = bool(installed and release and release.newer_than(installed.version))
    newer = newer_release and not held
    choice = info.choice(app.include_prereleases) if info else None
    build_required = bool(choice and choice.build_required) and not held
    pending = ""
    if held and installed:
        if newer_release and release:
            pending = release.version
        elif (
            choice
            and choice.build_required
            and version_key(choice.version) > version_key(installed.version)
        ):
            pending = choice.version
    waiting = bool(
        installed
        and installed.prerelease
        and not app.include_prereleases
        and (info is not None or release is not None)
        and not newer
        and (release is None or release.version != installed.version)
    )
    if working and working != WAITING:
        mode = "cancel"
    elif installed and build_required:
        mode = "build"
    elif newer:
        mode = "update"
    elif installed:
        mode = "launch"
    else:
        mode = "reinstall" if missing else "install"
    # Offline is not a failed check: the row keeps what was known before.
    offline = (refresh_error or error) == UNREACHABLE
    stamp = datetime.fromtimestamp(info.checked_at).strftime("%d %b, %H:%M") if info else ""
    details: list[str] = []
    if working == WAITING:
        state, text = "none", "Waiting"
        details.append("Waiting its turn in Update all")
    elif working:
        state, text = "busy", busy_text(working, progress)
    elif mode == "build":
        state, text = "build", "Choose build"
        details.append("Choose a build before updating")
    elif mode == "update" and release:
        state, text = "update", f"↑ {version_text(release.version)}"
        details.append(f"Update available: {version_text(release.version)}")
    elif held and installed:
        state = "held"
        text = f"‖ Held · {version_text(pending)} out" if pending else "‖ Held"
        details.append(
            f"Held at {version_text(installed.version)} · {version_text(pending)} available"
            if pending
            else f"Held at {version_text(installed.version)}"
        )
    elif missing and not installed:
        state, text = "missing", "⚠ File missing"
        details.append(f"The AppImage is no longer at {missing.path}")
        details.append("Reinstall it, or forget the missing copy from its page")
    elif refreshing:
        state, text = "none", "Checking…"
        details.append("Checking for updates…")
    elif installed and not offline and (refresh_error or (error and release is None)):
        state, text = "failed", "Check failed"
    elif waiting:
        state, text = "preview", "Waiting for stable"
        details.append("Waiting for a newer stable release")
    elif installed and installed.prerelease:
        state, text = "preview", "Preview channel"
        details.append("A preview version, which may be less reliable.")
    elif installed and (release or choice):
        state, text = "ok", "✓ Up to date"
    elif installed and info and info.stable is None:
        state, text = "none", "Only previews"
        details.append("Only previews are available; enable previews to use them.")
    elif installed:
        state, text = "none", "Not checked"
    else:
        state, text = "none", "Not installed"
    if release and state != "busy":
        if release.prerelease:
            details.append(f"{version_text(release.version)} is a pre-release.")
        elif release.is_latest:
            details.append(f"{version_text(release.version)} is GitHub's latest release.")
    if offline:
        details.append("Offline: could not reach GitHub. Showing what was known before.")
    elif refresh_error or error:
        details.append(refresh_error or error)
    if not installed:
        details.append("Release checked when installing")
    elif stamp:
        details.append(
            f"Last checked {stamp}" + (" · check needed" if info and not info.fresh() else "")
        )
    else:
        details.append("Release information not checked")
    if running and installed and not working:
        if text in QUIET_TEXTS:
            details.insert(0, f"Running now · {text.removeprefix('✓ ')}")
            state, text = "busy", RUNNING_TEXT
        else:
            details.insert(0, "Running now")
    return AppStatus(mode, state, text, "\n".join(details), build_required)
