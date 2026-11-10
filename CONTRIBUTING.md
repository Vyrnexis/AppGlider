# Contributing to AppGlider

Thank you for wanting to help. Bug fixes, tests, documentation and new features are all
welcome. This page explains what fits AppGlider, so the time you spend ends up in a release.

## Start with an issue

For anything bigger than a small fix, please
[open an issue](https://github.com/Vyrnexis/AppGlider/issues/new/choose) or start a
[discussion](https://github.com/Vyrnexis/AppGlider/discussions) before writing code. Saying
what you plan to change, and why, lets us agree on the approach first. That way a pull
request is not turned down after you have done the work.

Small fixes, such as a typo, a broken link or an obvious bug with a test, can go straight to
a pull request.

## What fits AppGlider

AppGlider is meant to be easy for people who are not technical: install, update and launch
AppImages from GitHub releases, safely, with as little to configure as possible. Changes
that keep it simple, safe and predictable fit best.

These rules protect users and are not negotiable:

- **A published SHA-256 is required.** AppGlider only installs a file when GitHub publishes a
  SHA-256 for it (or the release's `SHA256SUMS.txt` lists it), and the downloaded file must
  match. There is no way to skip or override this.
- **Files come from the app's own GitHub release.** Downloads are limited to that
  repository's release files. A renamed or moved repository is only followed when GitHub
  redirects the old address to it.
- **Only AppGlider's own installs are touched.** It never adopts, changes or deletes
  AppImages, documents or settings it did not install, and it never downgrades an app by
  itself.
- **Nothing is lost on failure.** A failed or cancelled install or update keeps the working
  version, and library changes are written atomically.

These have been considered and are not planned for now, so please ask before working on
them: a GitHub account or token, app stores and other download sources (such as
AppImageHub or other forges), and installing files without a published checksum.

The [roadmap](docs/roadmap.md) lists ideas that may come next. Being listed there does not
mean an idea is approved, so an issue is still the place to start.

## Add an app to the community catalog

[`docs/catalogs/community.toml`](docs/catalogs/community.toml) is a shared list of apps that
anyone can import from **Settings → Catalogs → Import from an address**. You can add an app
with a pull request. It qualifies when:

- it is open source or freely available,
- it publishes x86_64 AppImages in its GitHub releases,
- it is actively maintained, and
- it is not already in the catalog or in the built-in example catalog.

Add an `[[app]]` entry with `repo`, a plain `name` and a short `description`, in
alphabetical order by name. Add `build = "…"` only when the project publishes several x86_64
AppImages and AppGlider cannot choose one. The format is described under
[Catalogs](docs/catalogs.md#the-file-format).

A check runs on the pull request. It confirms that the entry is valid and not a duplicate,
that the repository exists, and that its latest release has an AppImage AppGlider can
install. You can run it yourself with
`python packaging/check_catalog.py docs/catalogs/community.toml`. If GitHub's request limit
stops it, run it again later. The maintainer reads each pull request and merges it by hand.

## Interface changes

The interface follows the [design brief](docs/design.md): a sidebar, compact rows with one
status chip and one main button, and plain wording without jargon. For a visible change,
please include a screenshot or a mockup in the issue or pull request.

## Setting up and checking your work

[Development and packaging](docs/development.md) explains the setup. In short, with
Python 3.11 or newer on Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -O -m appglider
```

Before opening a pull request, run the same checks as GitHub Actions:

```sh
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -q
```

- Add a test for any change in behaviour. Tests use Python's built-in `unittest` and
  the helpers in `tests/`; please do not add pytest or other test frameworks.
- Tests must not use the network or your real AppGlider library. Use the existing helpers,
  which work in temporary folders.
- Code must work on Python 3.11, even if you use a newer version.
- Please do not add new dependencies without discussing it first. The AppImage bundles
  everything it needs, and each dependency makes it larger.

## Pull requests

- Keep each pull request to one change, and say which issue it addresses.
- Describe what you changed, why, and how you tested it.
- Write commit messages as lowercase
  [Conventional Commits](https://www.conventionalcommits.org/) under 72 characters, for
  example `fix(library): keep the old version when a download fails`.
- Update the pages under `docs/` (the user guide and the "How it works" pages) when behaviour that users see changes.

## Security problems

If you find something that could put users at risk, please do not open a public issue.
Email [contact@appglider.org](mailto:contact@appglider.org) instead.

## Licence

AppGlider is licensed under the
[GNU General Public License, version 3 or later](LICENSE). By contributing, you agree that
your contribution is shared under the same licence.
