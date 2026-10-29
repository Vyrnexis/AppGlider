# Development and packaging

## Layout

```text
src/appglider/   Application code, icons and asset licences
tests/           Automated application tests
packaging/       AppImage scripts, entry point and dependency locks
.github/         GitHub Actions workflow
docs/            User guide, technical notes, design brief, roadmap, development and
                 build docs, screenshots, example catalogs and branding sources
```

Local generated directories are ignored by Git:

- `.venv/` — development environment.
- `build/` — packaging environment, cached downloads and temporary build files.
- `dist/` — finished AppImage, its `.zsync` file and checksum file.
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

The interface is one main window with a sidebar and stacked pages. Code lives in
`src/appglider/`, grouped by what it touches:

| Module | Responsibility |
| --- | --- |
| `app.py`, `__init__.py`, `__main__.py` | Entry point, launcher command, package version |
| `instance.py` | Single-instance lock and the local socket that wakes the running window |
| `core/catalog.py`, `core/catalogfile.py` | Built-in and custom repositories, release and build preferences; catalog files |
| `core/preferences.py`, `core/storage.py` | Theme, accent, check interval, pinned apps and order; atomic persistence |
| `github/versions.py` | Tag parsing (`release_version()`), version ordering (`version_key()`), cancellation |
| `github/http.py` | Restricted GitHub requests, rate limits and the ETag cache |
| `github/assets.py` | Release data, AppImage and CPU selection, build choice, checksums, the REST API |
| `github/pages.py` | Release discovery from public pages when the allowance is used up |
| `github/download.py` | Release cache and verified, cancellable downloads |
| `github/release_info.py`, `github/status.py` | Stable and preview metadata; GitHub status for display, with no requests |
| `github/icons.py` | Bounded icon discovery and image normalisation |
| `install/library.py` | Managed installs, ownership, rollback, launching and uninstall |
| `install/appimage.py` | AppImage header checks and the clean launch environment |
| `install/shortcuts.py`, `install/desktopentry.py` | Menu entries, the login entry and the app's own `.desktop` details |
| `install/launch_options.py` | Per-app arguments and environment variables |
| `install/self_update.py` | Check for, download and install new AppGlider versions; remove the replaced image |
| `ui/window.py` | Main window: sidebar, banners, footer, tasks, theme selection |
| `ui/tray.py` | Tray icon, its menu and update notifications |
| `ui/jobs.py`, `ui/release_checks.py` | Cancellable workers and Qt result signals; the background release-check queue |
| `ui/rows.py`, `ui/app_status.py` | Library row widgets, drag reordering; the status chip logic shared with the App page |
| `ui/theme.py`, `ui/controls.py` | Colour tokens, palette, stylesheet and painted icons; switch, segmented and space-bar controls |
| `ui/pages/app_page.py` | App page: release notes, versions, preferences (including launch options), Manage |
| `ui/pages/settings_page.py` | Settings page and its six sections (instant apply), including Space used |
| `ui/pages/add_page.py`, `ui/pages/catalog_preview.py` | Add app page; the read-only preview shown before a catalog is imported |

Visual values (colours, radii, sizes) are defined once, as theme tokens, in `ui/theme.py`;
[the design brief](design.md) describes them and the rules for new screens. Sidebar and
check-button icons are drawn with `QPainter` in the current theme's colours, so they need
no image files. App icons in SVG form are turned into PNG through Qt's SVG image plugin,
which the AppImage build keeps.

## Checks

```sh
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/python -m unittest discover -s tests -q
```

Set `QT_QPA_PLATFORM=offscreen` to run the tests without a display. Do not use `-O` for
tests or the smoke test: assertions are part of the checks. GUI tests use
Qt's offscreen platform, disposable settings and controlled downloads, so they never touch
your real AppGlider library or install real apps. `tests/catalog_fixture.py` provides the
example catalog's apps to tests that need a library with apps in it.

## Build the AppImage

Build on Linux x86_64 with Python 3.11 or newer, `binutils`, `tar` and `zstd` installed
(plus `zsync`, for the `.zsync` file). The script ignores `APPDIR`, `APPIMAGE`, `ARGV0` and `OWD`, so it also works from a
terminal that was itself started inside an AppImage.

```sh
python3 -m venv build/packaging-env
build/packaging-env/bin/python -m pip install -r packaging/build-requirements.txt
build/packaging-env/bin/python packaging/build_appimage.py
```

Output goes to `dist/`: `AppGlider-<version>-x86_64.AppImage` and `SHA256SUMS.txt`.
The build embeds update information (`gh-releases-zsync|Vyrnexis|AppGlider|latest|…`),
checks it with `--appimage-updateinformation`, and writes
`AppGlider-<version>-x86_64.AppImage.zsync` so AppImageUpdate and similar tools can update the
image. If appimagetool cannot write that file, a local build skips it and CI stops.
The version comes from `pyproject.toml`.

Test the package without FUSE, using a disposable first-run home:

```sh
python3 packaging/smoke_test.py dist/AppGlider-0.9.5-x86_64.AppImage
```

The smoke test runs twice: once as an empty first run, and once with two sample repositories
so the app rows are exercised too. Use the exact filename of your build (the version comes
from `pyproject.toml`). Verify the checksum from inside `dist/` with
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

`packaging/io.github.vyrnexis.AppGlider.appdata.xml` is the AppStream metadata (description,
release list and screenshot URLs) that software catalogs and AppStream tools read. It replaces
the Python runtime's own metadata. Its screenshots point at files in `docs/previews/`. Add each new version at the top of its `<releases>` list; the
build stops if the newest entry does not match the project version. Check it with
`appstreamcli validate packaging/io.github.vyrnexis.AppGlider.appdata.xml`.

Review these files together when updating the runtime:

| File | Purpose |
| --- | --- |
| `packaging/build-requirements.txt` | Build-tool version |
| `packaging/runtime-requirements.txt` | Bundled Python dependencies |
| `packaging/downloads.json` | Python AppImage (Python 3.13.17), packaging tool and runtime hashes |
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

Each topic has one home; link to it from elsewhere instead of repeating it.

| Document | Holds |
| --- | --- |
| `README.md` | What AppGlider is, features, install, first steps and links; short |
| `docs/usage.md` | Everyday behaviour, buttons and settings, in plain language, plus what to try when something goes wrong |
| `docs/technical-notes.md` | How it works: release matching, request budgets, files, identifiers, recovery |
| `docs/development.md` | Contributor setup, source layout, checks and local builds (this page) |
| `docs/builds.md` | GitHub Actions and the release process |
| `docs/design.md` | Interface layout, tokens and wording rules |
| `docs/branding.md` | Icons and brand colours |
| `docs/roadmap.md` | Shipped work and unapproved ideas |
| `site/` | The website, appglider.org |

The README and the user guide are for everyday users, who are not expected to be technical:
plain language, no jargon, and what to try when something goes wrong. Update
`docs/usage.md` whenever a button, menu item or setting changes, and re-take the screenshots
in `docs/previews/` after visible interface changes or a version change (the version shows in
the sidebar). Take them in the Dark theme from a disposable library, with the window at
1040×720; the app page (1040×1110) and the Add app page (1040×980) are taller so the whole
form shows. Use neutral paths such as `/home/you/.local/share/appglider/apps`, never your own. The website
keeps its own copies in `site/assets/`, so refresh those at the same time.

The user guide uses annotated copies, `docs/previews/guide-*.png`: numbered pink outlines
(`#ff4d8d`, a colour the interface never uses) drawn at the live widgets' positions, with
the badge just outside each outline, and a numbered key under each picture in the guide.
Draw them from the same disposable library as the plain screenshots, so the two match, and
keep the numbers in the guide's keys in step with the pictures.

## Website

`site/` is the website at [appglider.org](https://appglider.org/). Cloudflare Pages
publishes that folder from `main` on every push; there is no build step. It is plain HTML,
`styles.css` and `site.js`, with no external fonts, scripts or tracking. `_headers` sets a
strict Content Security Policy, so keep scripts and styles in their files: no inline
`style` attributes or event handlers. The only outside request is `site.js` asking GitHub's
API for the latest release.

- **Version:** `data-version` on `<body>` is the only hard-coded version, a fallback until
  `site.js` reads the latest release; update it when you release. Download buttons always
  fall back to the latest-release page, never a guessed file name.
- **Screenshots:** `site/assets/appglider-*.png` are copies of `docs/previews/`. The share
  image `appglider-social.png` (1200×630) shows the Library screenshot, so redraw it when
  that changes.
- **Search engines:** keep the title, description, structured data (`application/ld+json`)
  and `sitemap.xml` accurate. Update `<lastmod>` in the sitemap when the page changes.
- **Preview locally:** `python3 -m http.server --bind 127.0.0.1 --directory site`.
