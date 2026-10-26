# Technical notes

How AppGlider works under the hood. You do not need any of this to use it; the
[user guide](usage.md) covers everyday use. These notes are for the curious, for people
who want to know exactly what AppGlider does with their system, and for contributors.

## Contents

- [How release checks work](#how-release-checks-work)
- [AppGlider's own updates](#appgliders-own-updates)
- [Release notes](#release-notes)
- [GitHub's request allowance](#githubs-request-allowance)
- [Stable and preview versions](#stable-and-preview-versions)
- [Which file is downloaded](#which-file-is-downloaded)
- [Checksums](#checksums)
- [Icons](#icons)
- [Catalog files](#catalog-files)
- [Files AppGlider keeps](#files-appglider-keeps)
- [Menu entries](#menu-entries)
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
- AppGlider itself is updated by replacing its AppImage with a newer one and starting it;
  it does not yet do this for you (see [AppGlider's own updates](#appgliders-own-updates)).

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

## Release notes

The app page shows the description GitHub holds for the offered release. It is read from
the metadata of the check that found the release (the API or the public release page), so
showing it costs no extra request, and it is kept bounded in length. The text is rendered
as Markdown, read-only: images and local files are never loaded and only web links open.
A project that published no description shows a link to its releases instead.

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
- The **GitHub** indicator in the sidebar's status block opens **Settings → GitHub**,
  showing the last reported allowance, active retry countdowns and the most recent
  successful result with its repository, source (GitHub API, public pages or cached) and
  time. Opening it makes no network requests. Counts come from earlier GitHub responses and
  can also be affected by other programs on your network. After a request window expires the
  count becomes unknown until the next response; AppGlider does not assume the full
  allowance is available. Retry times survive restarts.

## Update notifications

The optional notification is sent through the system tray's message support, only after a
scheduled check (never at startup or for **Check for updates**), only if the tray is available
and visible, and not while the AppGlider window is shown and active. AppGlider remembers the
version it announced for each app, so an update is announced once; when the update is
installed or goes away, the memory is cleared. Updates that are already waiting when the
option is switched on are not announced, only new ones.

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

## Icons

- While adding a repository, AppGlider looks for likely icon files in it and previews the
  candidates. You can also choose a local PNG or a self-contained SVG. Selected icons are
  saved locally and keep working offline.
- Images are limited to 2 MiB and 4096 pixels per side and are converted to a PNG of at
  most 512 pixels per side. SVGs that reference external resources or declare a document
  type are rejected.
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
  or names like `icon_192px`). Up to four are offered.
- Repositories that were renamed or moved are followed: redirects are accepted only to
  GitHub's own hosts, and the new name is used for the remaining pages. A file list too
  large for the API (some big projects) simply uses the web walk instead.
- The apps in the ArtCraft example catalog use icons bundled with AppGlider.

## Catalog files

A catalog is TOML with `format = 1`, an optional `name` and one `[[app]]` table per
repository:

| Key | Required | Meaning |
| --- | --- | --- |
| `repo` | yes | `owner/repository` or `https://github.com/owner/repository` |
| `name` | no | Display name; defaults to the repository name |
| `description` | no | Plain text, up to 200 characters |
| `stable_only` | no | Default `true`; `false` includes previews |
| `build` | no | Exact AppImage filename to prefer |

- Each entry goes through the same checks as adding a repository by hand (GitHub only, no
  query strings or odd characters). Bad entries are listed and skipped; keys AppGlider does
  not know are ignored and listed.
- A file that is not valid TOML, lacks `format = 1`, is larger than 256 KiB or lists more
  than 100 apps imports nothing. The library holds at most 100 repositories in total.
- Import never changes an app you already have, makes no network requests and installs
  nothing. Names and descriptions are shown as plain text, never as formatted text.
- The example catalog is also bundled in the package (`assets/catalogs/artcraft.toml`) and a
  test keeps it identical to `docs/catalogs/artcraft.toml`; the release workflow attaches
  that file to every release.
- The 14 apps in the example catalog keep the identifiers they always had (for example
  `photocraft`), which is how apps installed before the catalog existed match again when it
  is imported.

## Files AppGlider keeps

| What | Where |
| --- | --- |
| Library, repository list, release cache, icons, locks | `~/.local/share/appglider/` (follows `XDG_DATA_HOME`) |
| Installed apps | `~/.local/share/appglider/apps/` unless you chose another folder |
| Settings | `~/.config/AppGlider/AppGlider.conf` (follows `XDG_CONFIG_HOME`) |
| Menu entries | `~/.local/share/applications/appglider*.desktop` |
| Login entry (only if you turn on Start with my desktop) | `~/.config/autostart/appglider.desktop` (follows `XDG_CONFIG_HOME`) |
| Logs of apps that failed to start | `~/.local/share/appglider/logs/` |

Each install folder holds a small `.appglider` marker. That marker is how AppGlider knows
which files it owns; it never touches AppImages, documents or settings it did not install.
Library and settings changes are written atomically, so a crash cannot leave a half-written
file. A corrupt file is preserved and reported, never overwritten with an empty library.
Only one AppGlider runs per data folder; starting it again raises the running window.

Choices such as the theme and accent (`appearance/theme`, `appearance/accent`), update schedule, your own order (`library/order`, a JSON list of app identifiers; apps not in it follow, A–Z by name) and pinned apps
(`library/pinned`, a JSON list of app identifiers), the notification choice
(`notifications/updates`) and the versions already announced (`notifications/seen`, a JSON
object) live in the settings file. Unreadable or
unknown values fall back to the defaults instead of causing an error.

The retired `library/sort` key (name, installed, updates, added or custom) is migrated once at
startup: if `library/order` is absent, the order that mode showed is saved as `library/order`;
either way `library/sort` is then removed.

## Menu entries

- AppGlider writes `appglider.desktop` for itself and `appglider-<app>.desktop` for each
  managed app, marked `X-AppGlider=true` so it only ever edits its own.
- Its own entry is rewritten only when it is missing, when its target no longer exists, or
  when a newer packaged AppImage takes over. A development run never repoints a working
  entry. The entry's icon copy is refreshed whenever a new release ships a different icon.
- Some apps add their own entry the first time they run, such as
  `ai.storyteller.solidcraft.desktop`. AppGlider does not create or edit these. When you
  uninstall the app, any entry in your applications folder whose command is one of the
  removed AppImages is deleted; entries for other programs are untouched.

## Library recovery

If the library contains an entry for an app that is not in your repository list (a removed
repository, or apps installed before you imported the example catalog), the entry is kept
untouched and a **Review N unrecognised entries…** button appears at the bottom of the window. Tick entries to
forget them. Nothing is forgotten without confirmation, and files on disk are never deleted.
Unreadable or malformed library files are preserved and reported.
