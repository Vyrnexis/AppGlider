"""What an app's row and App page say about it: one status, worked out in one place."""

from dataclasses import dataclass
from datetime import datetime

from appglider.core.catalog import App
from appglider.github.assets import Release
from appglider.github.release_info import ReleaseInfo
from appglider.github.versions import version_key
from appglider.install.library import Installed


@dataclass(frozen=True)
class AppStatus:
    """The primary action (`mode`) and the status chip (`state`, `text`, `tooltip`)."""

    mode: str
    state: str
    text: str
    tooltip: str
    build_required: bool


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
) -> AppStatus:
    """`missing` is AppGlider's record of an installation whose file has since gone."""
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
    if working:
        mode = "cancel"
    elif installed and build_required:
        mode = "build"
    elif newer:
        mode = "update"
    elif installed:
        mode = "launch"
    else:
        mode = "reinstall" if missing else "install"
    stamp = datetime.fromtimestamp(info.checked_at).strftime("%d %b, %H:%M") if info else ""
    details: list[str] = []
    if working:
        state, text = "busy", f"{working}…"
    elif mode == "build":
        state, text = "build", "Choose build"
        details.append("Choose a build before updating")
    elif mode == "update" and release:
        state, text = "update", f"↑ {release.version}"
        details.append(f"Update available: v{release.version}")
    elif held and installed:
        state, text = "held", f"‖ Held · {pending} out" if pending else "‖ Held"
        details.append(
            f"Held at v{installed.version} · v{pending} available"
            if pending
            else f"Held at v{installed.version}"
        )
    elif missing and not installed:
        state, text = "missing", "⚠ File missing"
        details.append(f"The AppImage is no longer at {missing.path}")
        details.append("Reinstall it, or forget the missing copy from its page")
    elif refreshing:
        state, text = "none", "Checking…"
        details.append("Checking for updates…")
    elif installed and (refresh_error or (error and release is None)):
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
            details.append(f"v{release.version} is a pre-release.")
        elif release.is_latest:
            details.append(f"v{release.version} is GitHub's latest release.")
    if refresh_error or error:
        details.append(refresh_error or error)
    if not installed:
        details.append("Release checked when installing")
    elif stamp:
        details.append(
            f"Last checked {stamp}" + (" · check needed" if info and not info.fresh() else "")
        )
    else:
        details.append("Release information not checked")
    return AppStatus(mode, state, text, "\n".join(details), build_required)
