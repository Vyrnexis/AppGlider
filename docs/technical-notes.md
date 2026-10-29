# Technical notes

How AppGlider works under the hood. You do not need any of this to use it; the
[user guide](usage.md) covers everyday use. These notes are for the curious, for people
who want to know exactly what AppGlider does with their system, and for contributors.

## Contents

- [How release checks work](#how-release-checks-work)
- [GitHub's request allowance](#githubs-request-allowance)
- [Stable and preview versions](#stable-and-preview-versions)
- [Which file is downloaded](#which-file-is-downloaded)
- [Checksums](#checksums)
- [Release notes](#release-notes)
- [Update notifications](#update-notifications)
- [AppGlider's own updates](#appgliders-own-updates)
- [Icons](#icons)
- [Catalog files](#catalog-files)
- [Files AppGlider keeps](#files-appglider-keeps)
- [Menu entries](#menu-entries)
- [Tray and single instance](#tray-and-single-instance)
- [Names and identifiers](#names-and-identifiers)
- [Renamed and moved repositories](#renamed-and-moved-repositories)
- [Library recovery](#library-recovery)

## How release checks work

- Startup, scheduled and **Check for updates** checks cover installed apps only. Checking
  an app you have not installed happens only when you inspect a repository or choose
  **Check now** on its page. With no installed apps, startup makes no app release
  requests (AppGlider's own check, below, still runs).
- Release information is read from GitHub's metadata. AppGlider never downloads an app
  just to look at it or to check for updates.
- Results less than five minutes old are reused; older results are refreshed. **Check for
  updates** and the app page's **Check now** ignore that window.
- **Cheap checks.** For a stable-only app whose release information is already saved,
  background, startup, scheduled and **Check for updates** checks first send a `HEAD` request
  to `github.com/<owner>/<repo>/releases/latest`. GitHub answers with a redirect to the
  tag of its designated latest stable release, which AppGlider reads without following it.
  If that version equals the saved one, the saved information is reused and only its
  "checked" time is updated, with no API request. If the tag differs, the page gives no
  clear answer (no release, a renamed repository, an unreadable tag, a network error, HTTP
  429), AppGlider goes on to the API exactly as before. The app page's **Check
  now**, installing and adding a repository always do a full check. The status tooltip
  reports this source as "GitHub release page".
- **Apps with previews turned on** cannot rely on the "Latest" page, which ignores previews.
  Their cheap check reads the repository's public Atom release feed
  (`github.com/<owner>/<repo>/releases.atom`) with a saved ETag, so an unchanged feed is a
  small `304`. If the newest version in the feed is no newer than the newest version already
  known, the saved information is reused (source "GitHub release feed"); otherwise, or if
  the feed cannot be read, the API is used. The feed's first download is large because it
  carries the release notes (a few megabytes for some projects) and is limited to 8 MiB;
  AppGlider keeps only its ETag and tag list, in `<app>-feed.json` in the release cache.
- Checked information is kept across restarts and shown first, so a row can show what was
  known before the new check finishes. Rows and app pages show when the information was
  checked and whether a check failed. A failed check keeps the last known versions and badges.
- Background checks run one app at a time, or up to three at once while GitHub reports at
  least 30 requests left. Launching apps and changing preferences stay available meanwhile.
- Installing re-checks the release using your saved preference and the same five-minute
  cache, confirms there is enough free disk space (the download size plus 64 MiB),
  downloads the file, and rejects it if the server's reported size differs from the
  published size. A failed or cancelled download leaves the installed version untouched.
- AppGlider itself is updated by downloading a newer AppImage next to the running one
  (see [AppGlider's own updates](#appgliders-own-updates)).

## GitHub's request allowance

AppGlider needs no GitHub account or token. It uses GitHub's public API, which allows
60 requests per hour per network address without signing in.

- A full API check takes two metadata requests per repository (the latest stable release
  and a listing of recent releases). Checksums can need more. Cheap checks (above) avoid
  these for apps that have not changed.
- Answers are stored with their ETag, so repeat requests are conditional and an unchanged
  answer (HTTP 304) is reused. For anonymous requests a 304 still counts against the
  allowance, so a check costs about one request per installed app whether or not anything
  changed.
- A scheduled check is skipped while GitHub last reported fewer than 30 requests left,
  while a download or another check is running, and when nothing is installed.
- When the allowance runs out, AppGlider falls back to GitHub's public release pages for
  up to three pages of ten releases, applying the same selection rules.
- The **GitHub** line in the sidebar's status block shows the last reported allowance. Its
  tooltip adds the reset time, active retry countdowns and the most recent successful result
  with its repository, source (GitHub API, public pages or cached) and time; clicking the
  block opens **Settings → Updates**. None of this makes network requests. (Versions up to
  0.9.4 also showed these details in a Settings → GitHub section.) Counts come from earlier GitHub responses and
  can also be affected by other programs on your network. After a request window expires the
  count becomes unknown until the next response; AppGlider does not assume the full
  allowance is available. Retry times survive restarts.

## Stable and preview versions

- GitHub's own flags decide whether a release is stable or a preview. Draft releases are
  excluded, and tags AppGlider cannot read as versions are ignored.
- **Stable and prerelease versions** picks the highest supported version among up to 100
  recent releases, plus GitHub's designated latest stable release. For example
  `1.3.0-beta.10` follows `1.3.0-beta.2`, and `1.3.0` follows both, so a final release
  replaces a beta once it is newer.
- Turning previews off keeps an installed preview launchable and waits for a newer stable
  release. AppGlider never downgrades automatically; **Roll back** is the explicit way to
  go back.
- Supported tags include `v1.2.3`, `1.2.3`, `v1.2.3-beta.1`, short versions such as `v0.6`
  and four-part versions such as `v02.08.02.61`. Project prefixes such as `studio-v0.6.0`,
  `release-1.2.3` and `app/v1.2.3` also work. The original tag is kept in download URLs;
  only its numeric version is used for comparing. A missing patch number counts as zero
  (`0.6` equals `0.6.0`), and numbers are compared as numbers.
- If the newest eligible version has no compatible AppImage or checksum, AppGlider says so
  instead of quietly choosing an older download. A project that publishes only previews
  must have previews enabled before it can be added.

## Which file is downloaded

- AppGlider looks at the release's real file list for names ending in `.AppImage`,
  ignoring letter case. The repository name, where the version sits, the separators and
  the word `linux` are not required. Companion files such as `.AppImage.zsync` and
  `.AppImage.asc` are ignored.
- CPU labels are matched to your machine: `x86_64`, `x86-64`, `x64` and `amd64` mean
  x86_64; `aarch64` and `arm64` mean ARM64. An explicit match beats an unlabelled file. A
  single unlabelled file is allowed. Before anything is activated, the downloaded file's
  own CPU type and AppImage header are checked. The AppGlider package itself is built for
  Linux x86_64 only.
- When several files fit, familiar filenames keep their existing preference. If that does
  not settle it, a **Build variant** choice appears (for example Bambu Studio's Ubuntu
  22.04 or 24.04 builds). The choice is remembered separately from the stable/preview
  choice and follows that build across releases, ignoring version numbers and build dates.
  Change it on the app's page under **Preferences → Build → Change…**.
- AppGlider does not detect which distribution a build suits. Follow the project's own
  guidance.
- If a saved variant disappears or becomes ambiguous, the installed row shows **Choose
  build**. AppGlider keeps the installed copy and waits for your choice rather than
  switching builds. Choosing a build never downloads anything, and a changed preference
  affects future installs and updates, not the installed copy. Upstream filename changes can
  need confirmation too: Bambu Studio's `ubuntu22.04` and `ubu22` names count as different
  variants until you pick the new one.

## Checksums

A published SHA-256 checksum is required before anything is installed. AppGlider uses
GitHub's own asset digest when available, otherwise a matching entry in the release's
`SHA256SUMS.txt`. When the API allowance is exhausted, the public-page fallback reads
the asset links and filename-labelled digests from GitHub's public release listing. If
GitHub changes that page or no checksum can be found, AppGlider stops and explains why.

## Release notes

The app page shows the description GitHub holds for the offered release. It is read from
the metadata of the check that found the release (the API or the public release page), so
showing it costs no extra request, and it is kept bounded in length. The text is rendered
as Markdown, read-only: images and local files are never loaded and only web links open.
A project that published no description shows a link to its releases instead.

## Update notifications

The optional notification is sent through the system tray's message support, only after a
scheduled check (never at startup or for **Check for updates**), only if the tray is available
and visible, and not while the AppGlider window is shown and active. AppGlider remembers the
version it announced for each app, so an update is announced once; when the update is
installed or goes away, the memory is cleared. Updates that are already waiting when the
option is switched on are not announced, only new ones.

## AppGlider's own updates

AppGlider looks for a newer stable release of itself (`Vyrnexis/AppGlider`) with the same
`HEAD` request to `releases/latest` that the cheap app checks use. There is no fallback to
the API: if the redirect gives no clear answer the check is reported as failed (when the user
asked for it) or ignored (in the background), so it never uses GitHub's allowance. The tag's
version is compared with the running version using the usual version ordering, and a newer
stable version is offered; previews and unreadable tags never are. The check runs on a
separate background worker, not in the window's single task slot, so it never blocks an
install, and only one runs at a time. It runs at startup, on the schedule, from the toolbar
and tray **Check for updates**, and from **Settings → About → Check now**, even with no apps
installed. With **Only when I choose** the startup and scheduled runs are skipped, leaving the
toolbar and tray command and **Check now**. The result is held in memory only. A newer version shows the tray
badge and, if notifications are on, one scheduled-check message per version, remembered under
the reserved key `AppGlider:self` in `notifications/seen` (app identifiers never contain a
colon). The running AppImage is recognised by `APPIMAGE` and `APPDIR` together with the Python
prefix lying inside `APPDIR`; the same test builds the menu shortcut's command.

**Updating in place.** The **Update AppGlider** button works only when the running AppImage is
recognised (as above) and is a regular file in a folder the user can write. Otherwise it opens
the release page. The update runs in the window's single task slot, so it never overlaps an
app install, and its banner button is disabled while any task runs.

- The release comes from the same full check app installs use (`latest_release` in `github/download.py` for
  `Vyrnexis/AppGlider`, stable only, so unlike the quick check it can use the API allowance). It must be the version the quick check found, stable and
  still newer than the running version; otherwise nothing is downloaded and the footer says
  so. The file must have a published SHA-256 (GitHub's asset digest or `SHA256SUMS.txt`), and
  the download is bounded and verified by `download` in `github/download.py` (size and SHA-256), then by
  `check_appimage` in `install/appimage.py` (ELF, AppImage type 2 and the CPU), with the same free-space check
  as installs (download size plus 64 MiB).
- The download goes to a hidden `.AppGlider-update-*` file in the running image's folder,
  is made executable and is renamed onto `AppGlider-<version>-x86_64.AppImage` (the asset's
  own name when it matches `AppGlider-*.AppImage`, ignoring case) only after verification. A
  failure or cancel deletes the temporary file. An existing file of that name is replaced
  only at that last step; a symbolic link, a folder or the running image itself is refused.
  The running image is never written to.
- Afterwards the menu entry (`create_launcher_shortcut` in `install/shortcuts.py`) and, only if it already
  exists and carries our marker, the login entry (`enable_autostart`, same file) are pointed at
  the new file, with the usual rules: only entries marked `X-AppGlider=true`, never a symbolic
  link. A refusal is reported in the footer and does not undo the update.
- Settings keys: `selfupdate/ready` (the new file), `selfupdate/replaced` (the file it
  replaced, for removal) and `selfupdate/version` (the new file's version, so the "ready"
  banner can come back). On startup a still-valid `ready` file that is newer than the running
  version shows the ready banner instead of "available"; if it vanished or is no longer a
  valid newer AppImage the keys are cleared.
- **Restart now** starts the new file with the flag `--after-update` the way apps are
  launched: `subprocess.Popen` in a new session with no inherited file descriptors (an
  inherited handle on this image's FUSE mount would keep the old file in use), in an
  environment cleaned of the running image's `APPIMAGE`, `APPDIR`, library and certificate
  variables (the same cleaning used to launch apps), then quits through the normal
  quit path. It refuses while a task or release check is running, and stays open with an error
  if the new file cannot start.
- `--after-update` makes the new instance retry the single-instance lock quietly (every 150 ms
  for up to 15 s) while the old window finishes quitting, instead of waking it and exiting.
  If the lock never frees it behaves like any second start.
- **Cleanup.** After taking the lock (never in `--smoke-test`) the new instance reads
  `selfupdate/replaced` and deletes that file only when it runs from an AppImage, the path is
  not the running image, it is in the running image's folder, it is a regular file and not a
  symbolic link, its name matches `AppGlider-*.AppImage` (ignoring case), it passes
  `check_appimage` and `running` (in `install/library.py`) finds no process using it. `ready` and
  `version` are cleared once the running image is the `ready` file. `replaced` is kept only
  while that file passes every other rule and is still running (the old version closing after
  a restart); an instance started with `--after-update` tries again after 10 seconds, and later
  starts try again too. A `ready` file that vanished clears everything without deleting
  anything. Nothing else is deleted.

## Icons

- While adding a repository, AppGlider looks for likely icon files in it and previews the
  candidates. You can also choose a local PNG or a self-contained SVG. Selected icons are
  saved locally and keep working offline.
- Images are limited to 2 MiB and 4096 pixels per side and are converted to a PNG of at
  most 512 pixels per side. SVGs that reference external resources, or declare anything
  beyond a standard `<!DOCTYPE svg …>` line, are rejected; that standard line is accepted.
- Only a bounded set of likely repository files is examined, so some projects need a manual
  choice. A missing icon or a quota error never stops a valid repository from being added.
  Icons inside the AppImage itself are not extracted.
- Icon searches run only when you ask, never in the background and never for many apps at
  once. The images themselves are always read from `raw.githubusercontent.com`, which does
  not use the API allowance. To find *which* files to read, AppGlider uses one of two ways:
  with plenty of allowance left (30 or more requests) it asks the API for the repository's
  file list, which is **one API request** and takes about a second. When the allowance is low
  or unknown, the API is rate limited, or the repository is too big for that listing, it
  **browses the repository's web pages** instead (`github.com/<owner>/<repo>/tree/HEAD/<folder>`,
  which returns a folder's contents as JSON), using **no API requests**.
- The web walk opens every top-level and second-level folder, then deeper folders only when
  their path looks like artwork (whole words such as `icons`, `brand`, `assets`, `hicolor`,
  `packaging`), most promising first. It skips dependency, test, documentation and
  platform-specific folders, and looks at only the two largest sizes inside `hicolor`-style
  folders. It stops early: once a strong candidate (a file named after the project, or one in
  a `hicolor` folder) has been seen on page N, it opens at least six more pages (or N more,
  if that is larger) and then finishes. Otherwise it stops after 40 pages or 60 seconds.
  Typically that is 5 to 10 seconds; a project whose icons sit deep in its source tree can
  take up to about 20 seconds.
  GitHub's folder JSON is an undocumented part of its website, so if it ever changes the walk
  simply finds nothing and you can still choose an image yourself.
- A PNG or SVG is a candidate if its path contains `icon`, `logo` or `brand/`, if it is the
  icon named in the project's `.desktop` file, or if its file name contains the project's name
  (for example `OrcaSlicer_192px.png`; names under four characters do not count). Files whose
  names carry a variant word (`mac`, `win`, `about`, `title`, `gradient`, `grayscale`,
  `splash`, `badge`, `banner`, `screenshot`...) are skipped.
- Candidates are ranked: the icon named in the project's `.desktop` file first (including
  names like `com.example.app`), then files named after the project, then PNGs under
  `linux` or `hicolor`, then larger sizes before smaller (read from folders like `512x512`
  or names like `icon_192px`). Candidates that look like a preview or nightly build (a path word such as `insiders`, `nightly`,
  `beta` or `preview`) come after the real icon, and so does artwork that is far from square
  (page corners, wide logos). Up to four are offered.
- Repositories that were renamed or moved are followed: redirects are accepted only to
  GitHub's own hosts, and the new name is used for the remaining pages. A file list too
  large for the API (some big projects) simply uses the web walk instead.
- The apps in the ArtCraft example catalog use icons bundled with AppGlider.

## Catalog files

A catalog is TOML with `format = 1`, an optional `name` and one `[[app]]` table per
repository (`repo`, and optionally `name`, `description`, `stable_only` and `build`). The
[user guide](usage.md#catalogs-sharing-a-list-of-apps) shows the whole format. `stable_only`
defaults to `true`, and `build` is the exact AppImage filename to prefer.

- Each entry goes through the same checks as adding a repository by hand (GitHub only, no
  query strings or odd characters). Bad entries are listed and skipped; keys AppGlider does
  not know are ignored and listed.
- A file that is not valid TOML, lacks `format = 1`, is larger than 256 KiB or lists more
  than 100 apps imports nothing. The library holds at most 100 repositories in total.
- Import never changes an app you already have, makes no network requests and installs
  nothing. Names and descriptions are shown as plain text, never as formatted text.
- The example catalog is also bundled in the package (`src/appglider/assets/catalogs/artcraft.toml`) and a
  test keeps it identical to `docs/catalogs/artcraft.toml`; the release workflow attaches
  that file to every release.
- The 14 apps in the example catalog keep the identifiers they always had (for example
  `photocraft`), which is how apps installed before the catalog existed match again when it
  is imported.

## Files AppGlider keeps

| What | Where |
| --- | --- |
| Library, repository list, release cache (`release-cache/`, including `etags.json`), icons, locks (`library.lock`, `appglider.lock`) | `~/.local/share/appglider/` (follows `XDG_DATA_HOME`) |
| Installed apps | `~/.local/share/appglider/apps/` unless you chose another folder |
| Settings | `~/.config/AppGlider/AppGlider.conf` (follows `XDG_CONFIG_HOME`) |
| Menu entries | `~/.local/share/applications/appglider*.desktop` |
| Menu details read from each app (`desktop/<app>.json`), per-app launch options (`launch-options.json`) | `~/.local/share/appglider/` |
| Login entry (only if you turn on Start with my desktop) | `~/.config/autostart/appglider.desktop` (follows `XDG_CONFIG_HOME`) |
| Logs of apps that failed to start | `~/.local/share/appglider/logs/` |

Each install folder holds a small `.appglider` marker. That marker is how AppGlider knows
which files it owns; it never touches AppImages, documents or settings it did not install.
Library and settings changes are written atomically, so a crash cannot leave a half-written
file. A corrupt file is preserved and reported, never overwritten with an empty library.

Choices such as the theme and accent (`appearance/theme`, `appearance/accent`), update schedule, your own order (`library/order`, a JSON list of app identifiers; apps not in it follow, A–Z by name) and pinned apps
(`library/pinned`, a JSON list of app identifiers), the notification choice
(`notifications/updates`) and the versions already announced (`notifications/seen`, a JSON
object) live in the settings file, as do the three `selfupdate/…` keys that track an
AppGlider update waiting for a restart. Unreadable or
unknown values fall back to the defaults instead of causing an error.

The retired `library/sort` key (name, installed, updates, added or custom) is migrated once at
startup: if `library/order` is absent, the order that mode showed is saved as `library/order`;
either way `library/sort` is then removed.

## Menu entries

- AppGlider writes `appglider.desktop` for itself and `appglider-<app>.desktop` for each
  managed app, marked `X-AppGlider=true` so it only ever edits its own.
- An app's entry takes its descriptive keys from the `.desktop` file inside the AppImage
  (`install/desktopentry.py`): Categories, MimeType, Keywords, Comment, StartupWMClass, StartupNotify,
  Terminal and the `[Desktop Action]` groups. Nothing else is copied; Name, Exec, Icon and the
  `X-AppGlider` markers are AppGlider's. Without usable details the entry has `Categories=Utility;`.
- The file is read by running the AppImage's own type 2 runtime as
  `--appimage-extract '*.desktop'` (and once more for the target of a symbolic link, which must
  stay inside the extracted tree) in a temporary directory, with the clean environment, no
  input or output, its own session and a 20 second limit. It runs only on a file that passed
  the checksum and header checks and is owned by AppGlider, and never while the library lock
  is held: install reads the verified download before taking the lock, and roll back reads the
  kept copy first. The sanitised result is cached in `desktop/<app>.json`, tied to the file's
  path, size and modification time, and checked again when used. Writing an entry only uses the
  cache.
- Entries carry `X-AppGlider-Metadata=2` (raised from `=1` when entries gained the
  AppImageLauncher variable below). At startup, one background pass rewrites owned entries of
  installed apps that lack the current marker, never creating a missing entry, then
  `update-desktop-database` runs once on the folder if it is installed.
- Launch options (`launch-options.json`, app id to `args` and `env`) are separate from the
  catalog and its export. Launching adds the arguments after the AppImage path and merges the
  variables over the clean environment. The entry's `Exec` is
  `"env" "APPIMAGELAUNCHER_DISABLE=1" "NAME=value" "/path/app.AppImage" "arg" %F` (`%U` if the
  app's own entry took URLs), each word quoted as the desktop entry specification requires.
  Actions get the variables but not the arguments. Saving the options rewrites an existing
  owned entry. When removing entries, the command is read past an `env NAME=value` prefix.
- `APPIMAGELAUNCHER_DISABLE=1` is set in the clean environment (so for Launch and for reading
  the `.desktop` file) and in every entry. AppImageLauncher's binfmt handler and launcher both
  check it and then run the AppImage directly, instead of offering to integrate it, which
  would move the file out of AppGlider's folder. A launch option can override it. AppGlider's
  own menu and login entries run `env APPIMAGELAUNCHER_DISABLE=1` too; an own entry that runs
  the same image in an older format is rewritten at startup.
- Settings → Storage warns when the install folder is inside AppImageLauncher's folder. It
  counts as installed when `AppImageLauncher` or `appimagelauncherd` is on `PATH` or a
  binfmt_misc entry names it; the folder is `destination` in
  `$XDG_CONFIG_HOME/appimagelauncher.cfg`, else `~/Applications`.
- Its own entry is rewritten only when it is missing, when its target no longer exists, or
  when a newer packaged AppImage takes over. A development run never repoints a working
  entry. The entry's icon copy is refreshed whenever a new release ships a different icon.
- Some apps add their own entry the first time they run, such as
  `ai.storyteller.solidcraft.desktop`. AppGlider does not create or edit these. When you
  uninstall the app, any entry in your applications folder whose command is one of the
  removed AppImages is deleted; entries for other programs are untouched.

## Tray and single instance

- Clicking the tray icon restores the window. Its menu has **Open AppGlider**, **Check for
  updates**, an **Installed apps** submenu that launches managed apps, and **Quit AppGlider**.
- Closing the window hides it only while a tray is available (and **Keep running in the tray**
  is on); otherwise the normal close applies. Downloads continue while hidden, and Quit waits
  for a confirmed cancellation to finish before exiting. Tray messages are limited to update
  alerts.
- Only one AppGlider runs per data folder. `appglider.lock` in the data folder is the guard;
  starting again wakes the running window through a socket in `$XDG_RUNTIME_DIR`.
- Qt's tray support follows the
  [QSystemTrayIcon documentation](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QSystemTrayIcon.html).

## Names and identifiers

- Display name and Qt organisation/application IDs: `AppGlider`. Python module and command:
  `appglider`.
- Installation ownership marker: `.appglider` in each install folder, whose first line is
  `AppGlider`.
- Managed-app menu entries: `appglider-<id>.desktop`, marked `X-AppGlider=true`. Launcher entry
  and Qt desktop-file ID: `appglider.desktop` / `appglider`.
- Optional login entry: `$XDG_CONFIG_HOME/autostart/appglider.desktop` (default
  `~/.config/autostart`), marked `X-AppGlider=true`, running the launcher command with
  `--tray`. Its existence is the setting; a symbolic link or unmarked file is never touched.
- Paths are listed under [Files AppGlider keeps](#files-appglider-keeps).

## Renamed and moved repositories

A release whose download links belong to another repository is accepted only when
`github.com/<old owner>/<old name>` redirects to exactly that repository (`http.moved_to`, a
HEAD request outside the API allowance). Only someone with admin rights can rename or transfer
a repository, so the redirect is the owner's own say-so. Files from any other repository are
still refused.

The move is then recorded (`Library.move_repository`): the new address replaces the old one in
`repositories.json` together with the app's original `id`, so the install folder, its
`.appglider` marker (rewritten for the new address), preferences, holds, launch options, icon,
menu entry and release caches stay attached. The operation runs again with the new address
(`Library.following_moves`), and the window reports the move. AppGlider never goes back to the
old address: GitHub keeps redirecting only until someone takes the old name, and that someone
could publish anything there. Add app with an old address adds the new one. The old name is
still offered as a file-name stem, since moved projects often keep their file names.

## Library recovery

If the library contains an entry for an app that is not in your repository list (a removed
repository, or apps installed before you imported the example catalog), the entry is kept
untouched and a **Review N unrecognised entries…** button appears at the bottom of the window. Tick entries to
forget them. Nothing is forgotten without confirmation, and files on disk are never deleted.
Unreadable or malformed library files are preserved and reported.

A managed entry whose AppImage is no longer a file (moved or deleted outside AppGlider) is
not counted as installed: the row shows **⚠ File missing** and offers **Reinstall**, which
installs normally and reuses the entry's ownership-marked folder. **Forget missing copy**
drops only that entry, AppGlider's own `appglider-<app>.desktop` and the cached menu details,
plus any entry whose command is the missing path. **Remove from library** does the same as
part of removing the repository; it still refuses while the managed file exists. Neither
touches files elsewhere, such as a copy moved to `~/Applications`.
