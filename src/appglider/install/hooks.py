"""Commands the user chose to run before and after AppGlider installs an app."""

import collections
import contextlib
import os
import shlex
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from appglider.core.catalog import App
from appglider.github.versions import Cancelled
from appglider.install.appimage import clean_environment

BEFORE_TIMEOUT = 600.0
AFTER_TIMEOUT = 120.0
POLL_SECONDS = 0.1
OUTPUT_LINES = 20
OUTPUT_BYTES = 4096


@dataclass(frozen=True)
class InstallCommands:
    before: str = ""  # a command line run before an install is activated; empty = off
    after: str = ""  # a command line run once it is active; empty = off


class InstallCommandFailed(ValueError):
    """The before-install command did not succeed, so the install was stopped."""


def command_problem(text: str) -> str:
    """Why this command line cannot be run, or "" when it is empty or fine."""
    try:
        words = shlex.split(text)
    except ValueError:
        return "Check the quotes in the command."
    if not words:
        return ""
    program = words[0]
    if "/" in program:
        path = Path(program).expanduser()
        found = path.is_file() and os.access(path, os.X_OK)
    else:
        found = shutil.which(program) is not None
    return "" if found else f"{program} was not found."


def _arguments(text: str, file: Path) -> list[str]:
    words = shlex.split(text)
    if not words:
        raise ValueError("The command is empty.")
    if "/" in words[0]:
        words[0] = str(Path(words[0]).expanduser())  # as command_problem checked it
    path = str(file)
    if any("{file}" in word for word in words):
        return [word.replace("{file}", path) for word in words]
    return [*words, path]


def _kill_group(process: "subprocess.Popen[bytes]") -> None:
    with contextlib.suppress(OSError):
        os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        process.kill()
    process.wait()


def _last_line(tail: collections.deque[str]) -> str:
    for line in reversed(tail):
        if line.strip():
            return line.strip()[-300:]
    return ""


def _execute(
    text: str,
    file: Path,
    stage: str,
    app: App,
    version: str,
    old_version: str,
    cancel: Event,
    timeout: float,
) -> tuple[str, str]:
    """Run one command. Returns (what went wrong or "", its last output line)."""
    try:
        arguments = _arguments(text, file)
    except ValueError as error:
        return f"couldn't start ({error})", ""
    environment = clean_environment() | {
        "APPGLIDER_STAGE": stage,
        "APPGLIDER_FILE": str(file),
        "APPGLIDER_APP": app.name,
        "APPGLIDER_APP_ID": app.id,
        "APPGLIDER_VERSION": version,
        "APPGLIDER_OLD_VERSION": old_version,
    }
    try:
        process = subprocess.Popen(
            arguments,
            cwd=file.parent,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as error:
        return f"couldn't start ({error.strerror or error})", ""
    tail: collections.deque[str] = collections.deque(maxlen=OUTPUT_LINES)

    def collect() -> None:
        assert process.stdout is not None
        with contextlib.suppress(OSError, ValueError):
            for raw in process.stdout:
                tail.append(raw.decode("utf-8", "replace").rstrip()[:OUTPUT_BYTES])

    reader = threading.Thread(target=collect, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            if cancel.is_set():
                _kill_group(process)
                raise Cancelled("Cancelled.")
            if time.monotonic() >= deadline:
                _kill_group(process)
                return "timed out", _last_line(tail)
            time.sleep(POLL_SECONDS)
    except BaseException:
        if process.poll() is None:
            _kill_group(process)
        raise
    finally:
        reader.join(1)
        if process.stdout is not None and not reader.is_alive():
            process.stdout.close()
    if process.returncode == 0:
        return "", _last_line(tail)
    return f"exit code {process.returncode}", _last_line(tail)


def run_before(
    commands: InstallCommands,
    app: App,
    file: Path,
    version: str,
    old_version: str,
    cancel: Event,
    progress: Callable[[str, int, int], None],
    kept: str,
) -> None:
    """Run the before-install command; raises InstallCommandFailed to stop the install.

    `kept` ends the message: what stays as it was.
    """
    if not commands.before.strip():
        return
    progress(f"Checking {app.name} with your before-install command…", 0, 0)
    reason, line = _execute(
        commands.before, file, "before", app, version, old_version, cancel, BEFORE_TIMEOUT
    )
    if not reason:
        return
    shown = f"{app.name} v{version}" if version[:1].isdigit() else f"{app.name} {version}".strip()
    detail = f" {line}" if line else ""
    raise InstallCommandFailed(
        f"The before-install command failed for {shown}: {reason}.{detail} {kept}"
    )


def run_after(
    commands: InstallCommands,
    app: App,
    file: Path,
    version: str,
    old_version: str,
    cancel: Event,
    progress: Callable[[str, int, int], None],
) -> str:
    """Run the after-install command. Returns a warning for the user, or "" when it went well."""
    if not commands.after.strip():
        return ""
    progress("Running your after-install command…", 0, 0)
    try:
        reason, line = _execute(
            commands.after, file, "after", app, version, old_version, cancel, AFTER_TIMEOUT
        )
    except Cancelled:
        return f"The after-install command was cancelled for {app.name}."
    if not reason:
        return ""
    detail = f" {line}" if line else ""
    return f"The after-install command failed for {app.name}: {reason}.{detail}"
