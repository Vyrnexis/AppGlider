# Development and packaging

## Layout

```text
src/appglider/   Application code, icons and asset licences
tests/           Automated application tests
packaging/       AppImage scripts, entry point and dependency locks
.github/         GitHub Actions workflow
docs/            User guide, technical notes, roadmap, development and build docs,
                 example catalogs and branding sources
```

Local generated directories are ignored by Git:

- `.venv/` — development environment.
- `build/` — packaging environment, cached downloads and temporary build files.
- `dist/` — finished AppImage and checksum file.
- `.backups/` — local recovery archives; preserve these when cleaning generated files.

Keep obsolete research and extra screenshots in `.backups/`, outside the published docs.
Loose AppImages, logs, local `.env` files and editor temporary files are also ignored.

## Set up

Use Python 3.11 or newer on a Linux desktop:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -O -m appglider
```

## Source layout

The interface is one main window with a sidebar and stacked pages, all in `src/appglider/`:

| Module | Responsibility |
| --- | --- |
| `window.py` | Main window: sidebar, Library and Updates pages, rows, drag reordering, tray, theme stylesheet |
| `app_page.py` | App page: release notes, versions, preferences, Manage; shared panel and setting-row helpers |
| `app_status.py` | The status chip and main action of an app, from its install and release state |
| `settings_page.py` | Settings page and its six sections (instant apply) |
| `add_page.py` | Add app page: inspect a repository, choose build and icon, add |
| `controls.py` | Switch and segmented controls |
| `preferences.py` | Theme, accent and update-check choices, and migration of retired themes |
| `catalog_preview.py` | The read-only preview shown before a catalog is imported |

Visual values (colours, radii, sizes) are defined once, as theme tokens, in `window.py`;
[the design brief](design.md) describes them and the rules for new screens. Sidebar and
check-button icons are drawn with `QPainter` in the current theme's colours, so the
AppImage needs no SVG support.

## Checks

```sh
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/python -m unittest discover -s tests -q
```

Run the tests with `QT_QPA_PLATFORM=offscreen` set if you have no display. GUI tests use
Qt's offscreen platform, disposable settings and controlled downloads, so they never touch
your real AppGlider library or install real apps. `tests/catalog_fixture.py` provides the
example catalog's apps to tests that need a library with apps in it.

## Build the AppImage

Build on Linux x86_64 with Python 3.11 or newer, `binutils`, `tar` and `zstd` installed.

```sh
python3 -m venv build/packaging-env
build/packaging-env/bin/python -m pip install -r packaging/build-requirements.txt
build/packaging-env/bin/python packaging/build_appimage.py
```

Output goes to `dist/`: `AppGlider-<version>-x86_64.AppImage` and `SHA256SUMS.txt`.
The version comes from `pyproject.toml`.

Test the package without FUSE, using a disposable first-run home:

```sh
python3 packaging/smoke_test.py dist/AppGlider-<version>-x86_64.AppImage
```

The smoke test runs twice: once as an empty first run, and once with two sample repositories
so the app rows are exercised too. Verify the checksum from inside `dist/` with
`sha256sum -c SHA256SUMS.txt`.
Before a release, also exercise the tray, folder picker, themes and launching on real
X11/Wayland desktops. The headless check does not qualify those interactions.

## Bundled runtime

The build stages a portable [python-appimage](https://python-appimage.readthedocs.io/en/latest/apps/)
Python runtime, installs the application wheel and pinned runtime requirements, and bundles
selected Ubuntu 22.04 X11 libraries. User settings, installed apps and the development
environment are excluded.

`trim_appdir.py` removes unused Qt modules, QML components, development tools and pip.
It follows shared-library dependencies from retained Widgets bindings and desktop plugins.
Update its retained binding list if the application starts importing another Qt module.
Runtime licence texts and source/replacement instructions are bundled with the application;
keep [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) current when dependencies change.

`packaging/io.github.vyrnexis.AppGlider.appdata.xml` is the AppStream metadata that software
catalogs such as [appimage.github.io](https://appimage.github.io/) read. It replaces the
Python runtime's own metadata. Add each new version at the top of its `<releases>` list; the
build stops if the newest entry does not match the project version. Check it with
`appstreamcli validate packaging/io.github.vyrnexis.AppGlider.appdata.xml`.

Review these files together when updating the runtime:

| File | Purpose |
| --- | --- |
| `packaging/build-requirements.txt` | Build-tool version |
| `packaging/runtime-requirements.txt` | Bundled Python dependencies |
| `packaging/downloads.json` | Python AppImage, packaging tool and runtime hashes |
| `packaging/ubuntu-libs.json` | Bundled X11 library downloads and hashes |

Some upstream tool URLs use mutable `continuous` releases. A changed download fails the
hash check deliberately; review the replacement before updating its recorded hash.
The AppImage runtime is pinned to a specific GitHub asset ID and SHA-256; its cache
filename includes that ID. The download requests binary content from GitHub's asset API.

## Local cleanup

Extracted AppImages, old comparison builds, logs and tool caches are disposable.
Keep `.backups/` separate from build output, and retain `dist/` while testing the current
AppImage. Recreating `build/packaging-env` and cached downloads requires network access.

See [GitHub Actions](builds.md) for the automated build process, and the
[technical notes](technical-notes.md) for how the application itself behaves.

## Documentation

- `README.md` and `docs/usage.md` are for everyday users, who are not expected to be
  technical: plain language, no jargon, and what to try when something goes wrong.
- `docs/technical-notes.md` holds the detail (release matching, request budgets, files,
  recovery). Put new technical explanations there and link to them from the guide.
- Update `docs/usage.md` whenever a button, menu item or setting changes, and re-take the
  screenshots in `docs/previews/` (taken in the Dark theme from a disposable library, with the
  window at about 1040×720; the Add app page is taller so the whole form shows) after visible interface changes.
