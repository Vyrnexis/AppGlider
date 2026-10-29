"""Checks and environment shared by installing, launching and reading AppImages."""

import os
from pathlib import Path

from appglider.github.versions import architecture

# AppImageLauncher otherwise offers to "integrate" an AppImage it sees start, which moves
# the file into ~/Applications and out of the folder AppGlider manages.
NO_INTEGRATION = {"APPIMAGELAUNCHER_DISABLE": "1"}


def check_appimage(path: Path) -> None:
    with path.open("rb") as source:
        head = source.read(20)
    if (
        head[:4] != b"\x7fELF"
        or head[4:6] != b"\x02\x01"
        or head[8:11] != b"AI\x02"
        or len(head) < 20
    ):
        raise ValueError("The selected file is not a type 2 AppImage.")
    machine = int.from_bytes(head[18:20], "little")
    expected = {"x86_64": 62, "aarch64": 183}[architecture()]
    if machine != expected:
        raise ValueError("This AppImage was built for a different CPU architecture.")


def clean_environment() -> dict[str, str]:
    """This process's environment without what a running AppImage sets for itself.

    Another AppImage started with it must not inherit our mount's libraries or certificates.
    """
    environment = os.environ.copy()
    appdir = environment.get("APPDIR")
    if appdir:
        for name in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
            value = environment.get(name)
            if value and Path(value).is_relative_to(Path(appdir)):
                environment.pop(name)
    for name in (
        "APPIMAGE",
        "APPDIR",
        "ARGV0",
        "OWD",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "PYTHONHOME",
        "PYTHONPATH",
        "QT_PLUGIN_PATH",
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        "QML2_IMPORT_PATH",
        "QML_IMPORT_PATH",
    ):
        environment.pop(name, None)
    return environment | NO_INTEGRATION
