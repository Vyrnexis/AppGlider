"""Build the x86_64 release without using the host Python or graphics libraries."""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
from pathlib import Path
from xml.etree import ElementTree

from trim_appdir import trim

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
BUILD = ROOT / "build" / "appimage"
CACHE = ROOT / "build" / "downloads"
METAINFO = "io.github.vyrnexis.AppGlider.appdata.xml"
UPDATE_INFORMATION = "gh-releases-zsync|Vyrnexis|AppGlider|latest|AppGlider-*x86_64.AppImage.zsync"


def run(*args: str, cwd: Path = ROOT) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def installed_update_information(image: Path) -> str:
    """Ask the image runtime for the update information embedded in it."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"APPDIR", "APPIMAGE", "OWD", "ARGV0"}
    }
    result = subprocess.run(
        [str(image), "--appimage-updateinformation"],
        capture_output=True,
        text=True,
        timeout=60,
        env=environment,
    )
    return result.stdout.strip()


def download(item: dict[str, str]) -> Path:
    path = CACHE / item["name"]
    if not path.exists():
        temporary = path.with_suffix(path.suffix + ".part")
        request = urllib.request.Request(
            item["url"], headers={"Accept": "application/octet-stream"}
        )
        with urllib.request.urlopen(request, timeout=120) as source:
            with temporary.open("wb") as target:
                shutil.copyfileobj(source, target)
        temporary.replace(path)
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != item["sha256"]:
        raise ValueError(f"Checksum mismatch: {path}. Remove it and review the download lock.")
    return path


def main() -> None:
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise SystemExit("Build on Linux x86_64 with Python 3.11 or newer.")
    BUILD.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    downloads = json.loads((PACKAGING / "downloads.json").read_text())
    binaries = {key: download(value) for key, value in downloads.items()}
    for binary in binaries.values():
        binary.chmod(0o755)
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    wheel_dir = BUILD / "wheels"
    wheel_dir.mkdir(exist_ok=True)
    run(sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheel_dir), str(ROOT))
    wheel = wheel_dir / f"appglider-{version}-py3-none-any.whl"
    # Keep staging disposable; never include the source tree or a user's library.
    with tempfile.TemporaryDirectory(prefix="stage-", dir=BUILD) as staging:
        stage = Path(staging)
        recipe = stage / "appglider"
        recipe.mkdir()
        shutil.copy2(PACKAGING / "appglider.desktop", recipe)
        metainfo = PACKAGING / METAINFO
        newest = ElementTree.parse(metainfo).find("releases/release")
        if newest is None or newest.get("version") != version:
            raise SystemExit(f"Add a {version} release at the top of {metainfo.name}.")
        # python-appimage ships this in place of its own Python metadata.
        shutil.copy2(metainfo, recipe)
        icon = ROOT / "src/appglider/assets/icons/appglider-taskbar.png"
        shutil.copy2(icon, recipe / "appglider.png")
        (recipe / "requirements.txt").write_text(
            (PACKAGING / "runtime-requirements.txt").read_text() + f"\n{wheel}\n"
        )
        run(
            sys.executable,
            "-m",
            "python_appimage",
            "build",
            "app",
            "--base-image",
            str(binaries["python"]),
            "--no-packaging",
            str(recipe),
            cwd=stage,
        )
        appdir = stage / "AppGlider-x86_64"
        shipped = sorted(path.name for path in (appdir / "usr/share/metainfo").iterdir())
        if shipped != [METAINFO]:
            raise SystemExit(f"Unexpected AppStream metadata in the AppDir: {shipped}")
        (appdir / "AppRun").unlink()
        shutil.copy2(PACKAGING / "AppRun", appdir / "AppRun")
        (appdir / "AppRun").chmod(0o755)
        libdir = appdir / "usr/lib"
        libdir.mkdir(exist_ok=True)
        for name, item in json.loads((PACKAGING / "ubuntu-libs.json").read_text()).items():
            deb = download(item)
            with tempfile.TemporaryDirectory(dir=stage) as unpack:
                directory = Path(unpack)
                run("ar", "x", str(deb), cwd=directory)
                (payload,) = directory.glob("data.tar.*")
                run("tar", "xf", str(payload), cwd=directory)
                for lib in directory.glob("usr/lib/x86_64-linux-gnu/*.so*"):
                    shutil.copy2(lib, libdir / lib.name, follow_symlinks=False)
                copyright_file = directory / "usr/share/doc" / name / "copyright"
                if copyright_file.is_file():
                    notices = appdir / "usr/share/doc/appglider/bundled-libraries"
                    notices.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(copyright_file, notices / f"{name}.copyright")
        trim(appdir)
        output = ROOT / "dist"
        output.mkdir(exist_ok=True)
        image = output / f"AppGlider-{version}-x86_64.AppImage"
        staged_image = stage / image.name
        zsync = image.with_name(image.name + ".zsync")
        # Never ship a .zsync that does not belong to this build.
        zsync.unlink(missing_ok=True)
        # appimagetool's extract-and-run mode would otherwise change into $OWD.
        os.environ.pop("OWD", None)
        os.environ["ARCH"] = "x86_64"
        os.environ["VERSION"] = version
        run(
            str(binaries["tool"]),
            "--appimage-extract-and-run",
            "--runtime-file",
            str(binaries["runtime"]),
            "--comp",
            "zstd",
            "--no-appstream",
            "--updateinformation",
            UPDATE_INFORMATION,
            str(appdir),
            str(staged_image),
            cwd=stage,
        )
        # appimagetool writes the .zsync into its working directory, using its own zsyncmake.
        made = stage / (staged_image.name + ".zsync")
        if made.is_file():
            made.replace(zsync)
        elif os.environ.get("GITHUB_ACTIONS") == "true":
            raise SystemExit("appimagetool did not write the release .zsync file; install zsync.")
        else:
            print("Skipped the .zsync file: zsyncmake is not installed.")
        # Replace the inode after packing so an already running image keeps its contents.
        staged_image.replace(image)
        embedded = installed_update_information(image)
        if embedded != UPDATE_INFORMATION:
            raise SystemExit(f"Unexpected update information in {image.name}: {embedded!r}")
        with image.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        (output / "SHA256SUMS.txt").write_text(f"{digest}  {image.name}\n")
        print(f"Built {image}")


if __name__ == "__main__":
    main()
