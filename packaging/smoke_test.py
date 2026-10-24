"""Open the packaged GUI with a disposable first-run home, without FUSE."""

import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

SAMPLE_REPOSITORIES = [
    {"repo": "owner/first-app", "name": "First App"},
    {"repo": "owner/second-app", "name": "Second App"},
]


def run(image: Path, home: Path) -> dict[str, object]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTHON", "QT_", "XDG_"))
        and key not in {"APPDIR", "APPIMAGE", "LD_LIBRARY_PATH", "SSL_CERT_FILE", "SSL_CERT_DIR"}
    }
    environment.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / ".config"),
        XDG_DATA_HOME=str(home / ".local/share"),
        XDG_CACHE_HOME=str(home / ".cache"),
        QT_QPA_PLATFORM="offscreen",
    )
    result = subprocess.run(
        [str(image), "--appimage-extract-and-run", "--smoke-test"],
        env=environment,
        cwd=home,
        text=True,
        capture_output=True,
        timeout=90,
    )
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return next(json.loads(line) for line in result.stdout.splitlines() if line.startswith("{"))


def main() -> None:
    image = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="appglider first run ") as temporary:
        home = Path(temporary)
        report = run(image, home)
        assert report["app"] == "AppGlider" and report["icon"], report
        assert report["update_icon"] and report["update_checks_hours"] == 6, report
        project = Path(__file__).resolve().parents[1] / "pyproject.toml"
        version = tomllib.loads(project.read_text())["project"]["version"]
        assert report["version"] == version, report
        assert report["header_version"] == f"v{version}", report
        assert report["refresh_action"] == "Check for updates", report
        assert report["https_certificates"] > 0, report
        assert report["installed"] == 0 and report["catalog"] == 0, report
        assert report["welcome"] is True, report
        assert report["example_catalog"] == 14, report
        assert report["release_preferences"] == 0 and report["card_dropdowns"] == 0, report
        assert report["python"].startswith("3.13."), report
        assert Path(report["prefix"]).is_relative_to(report["appdir"]), report
        assert report["command"] == [str(image)], report
        assert Path(report["data"]).is_relative_to(home), report
        shortcut = home / ".local/share/applications/appglider.desktop"
        assert str(image) in shortcut.read_text(), shortcut.read_text()
        assert report["appdir"] not in shortcut.read_text(), shortcut.read_text()
        print(json.dumps(report, indent=2))
    # A library with repositories builds cards, so the card menus are exercised too.
    with tempfile.TemporaryDirectory(prefix="appglider cards ") as temporary:
        home = Path(temporary)
        data = home / ".local/share/appglider"
        data.mkdir(parents=True)
        (data / "repositories.json").write_text(json.dumps(SAMPLE_REPOSITORIES))
        cards = run(image, home)
        assert cards["catalog"] == len(SAMPLE_REPOSITORIES), cards
        assert cards["welcome"] is False, cards
        assert cards["release_preferences"] == len(SAMPLE_REPOSITORIES), cards
        assert cards["card_dropdowns"] == 0, cards
    print("Packaged empty first run, repository cards and stable menu shortcut passed.")


if __name__ == "__main__":
    main()
