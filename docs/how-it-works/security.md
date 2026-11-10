# Security

What AppGlider checks before it installs anything, what it does and does not run, and how to report a problem. The rules that protect users are also listed in [Contributing](../../CONTRIBUTING.md#what-fits-appglider).

## Checksums

A published SHA-256 checksum is required before anything is installed. AppGlider uses
GitHub's own asset digest when available, otherwise a matching entry in the release's
`SHA256SUMS.txt`. When the API allowance is exhausted, the public-page fallback reads
the asset links and filename-labelled digests from GitHub's public release listing. If
GitHub changes that page or no checksum can be found, AppGlider stops and explains why.

## What AppGlider runs

- **Apps run only when you start them.** AppGlider never runs an app to install it or to check it for updates. Release information is read from GitHub's metadata, and nothing is downloaded just to look at it.
- **Reading menu details.** To fill in an app's menu entry, AppGlider runs the AppImage's own runtime with `--appimage-extract '*.desktop'` in a temporary folder. This happens only on a file that already passed the checksum and header checks, and is owned by AppGlider. See [Menu entries](files-and-folders.md#menu-entries).
- **An AppImage you pick yourself is never run** just to read its name, version and icon. AppGlider reads them from inside the file. See [Apps added from a file](files-and-folders.md#apps-added-from-a-file).
- **Your own commands.** AppGlider runs a program of yours only when you typed it under **Settings → Updates → Install commands**. See [Install commands](#install-commands) below.
- **Only AppGlider's own installs are touched.** Each install folder holds a small `.appglider` marker, and AppGlider never adopts, changes or deletes AppImages, documents or settings it did not install. It never downgrades an app by itself.
- **Files come from the app's own release.** A renamed or moved repository is followed only when GitHub redirects the old address to it. See [Renamed and moved repositories](release-checks.md#renamed-and-moved-repositories).
- **Release notes and names are shown as plain, read-only text.** Release notes never load images or local files, and only web links open. Catalog names and descriptions are never shown as formatted text.
- **Failures keep what you have.** A failed or cancelled install or update keeps the working version, and library and settings changes are written atomically.

## Install commands

The commands you can enter under **Settings → Updates** are described in [Install commands](../using/install-commands.md). This is how they are run.

The two commands in **Settings → Updates** (`install/before-command` and
`install/after-command` in the settings file) are split like a shell would split them, but
run without a shell. `{file}` is replaced in each argument, or the path is appended when it
is absent. They run in a clean environment, with the `APPGLIDER_*` variables added.

The before command runs after the download is verified (size and SHA-256 for GitHub
releases, or the copy for a file) and before the file is made executable or the library
changes. A non-zero exit, a start failure or the 10 minute timeout stops the install and
removes what was staged. The after command runs once the new version is active and the
library is saved; a failure or the 2 minute timeout only produces a warning.

Each command starts in its own process group. On **Cancel** or a timeout the whole group is
killed, so child processes do not keep running. Rollback and AppGlider's own updates never
run the commands, and catalog export does not include them.

## Reporting a vulnerability

If you find something that could put users at risk, please do not open a public issue. Email [contact@appglider.org](mailto:contact@appglider.org) instead. See the [security section of Contributing](../../CONTRIBUTING.md#security-problems).
