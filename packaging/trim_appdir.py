"""Keep the Widgets runtime and the bundled libraries its desktop plugins need."""

import re
import shutil
import subprocess
from pathlib import Path


def needed(binary: Path) -> set[str]:
    result = subprocess.run(
        ["readelf", "-d", str(binary)], check=True, text=True, capture_output=True
    )
    return set(re.findall(r"\(NEEDED\).*\[(.*?)\]", result.stdout))


def trim(appdir: Path) -> None:
    # shortcut: Widgets-only bindings; extend this set when the app imports another Qt module.
    python = appdir / "opt/python3.13"
    site = python / "lib/python3.13/site-packages"
    pyside = site / "PySide6"
    qt = pyside / "Qt"
    for path in (
        site / "pip",
        *site.glob("pip-*.dist-info"),
        python / "lib/python3.13/ensurepip",
        python / "include",
        pyside / "include",
        pyside / "typesystems",
        pyside / "glue",
        pyside / "doc",
        pyside / "scripts",
        qt / "qml",
        qt / "metatypes",
        qt / "libexec",
    ):
        if path.exists():
            shutil.rmtree(path)
    # The base image's Tk toolkit, IDLE editor, turtle demos and SQLite: AppGlider uses Qt and
    # never imports them, and only their own extension modules need the bundled libraries.
    stdlib = python / "lib/python3.13"
    for path in (
        stdlib / "tkinter",
        stdlib / "idlelib",
        stdlib / "turtledemo",
        stdlib / "sqlite3",
        appdir / "usr/share/tcltk",
    ):
        if path.exists():
            shutil.rmtree(path)
    (stdlib / "turtle.py").unlink(missing_ok=True)
    for pattern in ("_tkinter.*.so", "_sqlite3.*.so"):
        for path in (stdlib / "lib-dynload").glob(pattern):
            path.unlink()
    for name in ("libtcl8.6.so", "libtk8.6.so", "libsqlite3.so"):
        (appdir / "usr/lib" / name).unlink(missing_ok=True)
    for path in pyside.glob("Qt*.abi3.so"):
        if path.name not in {"QtCore.abi3.so", "QtGui.abi3.so", "QtWidgets.abi3.so"}:
            path.unlink()
    for path in pyside.glob("*.pyi"):
        path.unlink()
    for name in (
        "assistant",
        "designer",
        "linguist",
        "lrelease",
        "lupdate",
        "qmlformat",
        "qmllint",
        "qmlls",
        "svgtoqml",
    ):
        (pyside / name).unlink(missing_ok=True)
    (pyside / "libpyside6qml.abi3.so.6.12").unlink(missing_ok=True)
    plugins = qt / "plugins"
    for name in (
        "designer",
        "qmltooling",
        "sqldrivers",
        "printsupport",
        "vectorimageformats",
        "wayland-graphics-integration-server",
        "egldeviceintegrations",
    ):
        shutil.rmtree(plugins / name)
    for plugin in (plugins / "platforms").glob("*.so"):
        if plugin.name not in {
            "libqxcb.so",
            "libqwayland.so",
            "libqoffscreen.so",
            "libqminimal.so",
        }:
            plugin.unlink()
    # These optional plugins reference Qt modules absent from the Essentials wheel.
    (plugins / "platforminputcontexts/libqtvirtualkeyboardplugin.so").unlink()
    (plugins / "imageformats/libqpdf.so").unlink()
    for path in (qt / "translations").iterdir():
        if not path.name.startswith(("qtbase_", "qt_")):
            path.unlink()

    # Follow ELF dependencies from retained bindings and plugins, including ICU and Wayland.
    libraries = {path.name: path for path in (qt / "lib").iterdir()}
    roots = [*pyside.glob("*.so*"), *plugins.rglob("*.so")]
    pending = list(roots)
    visited: set[Path] = set()
    while pending:
        binary = pending.pop()
        if binary in visited:
            continue
        visited.add(binary)
        for name in needed(binary):
            if name in libraries:
                pending.append(libraries[name])
            elif name.startswith("libQt6"):
                raise ValueError(f"Missing Qt dependency: {binary.name} needs {name}")
    for path in libraries.values():
        if path not in visited:
            path.unlink()
    print(f"Retained {len(visited)} Qt bindings, plugins and shared libraries.")


if __name__ == "__main__":
    import sys

    trim(Path(sys.argv[1]).resolve())
