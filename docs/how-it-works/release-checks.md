# Release checks

How AppGlider looks for new versions, decides which release and which file to offer, and updates itself. You do not need any of this to use AppGlider; it is for the curious and for contributors. The everyday view is in [Updates and versions](../using/updates-and-versions.md).

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
- An automatic check that could not reach GitHub at all (no network yet at login, or just
  after a resume) tries again after 15 s, 30 s, 1, 2 and 5 minutes, and stops once a check
  gets through. AppGlider's own background check does the same. Other failures wait for the
  next scheduled check. Update notifications wait until the retries are over.
- Being offline is not a failed check. Rows keep their last known chip, their tooltip says
  the information is from before, and the sidebar shows **GitHub: offline** until a check
  gets through.
- Background checks run one app at a time, or up to three at once while GitHub reports at
  least 30 requests left. Launching apps and changing preferences stay available meanwhile.
- Installing re-checks the release using your saved preference and the same five-minute
  cache, confirms there is enough free disk space (the download size plus 64 MiB),
  downloads the file, and rejects it if the server's reported size differs from the
  published size. A failed or cancelled download leaves the installed version untouched.
- AppGlider itself is updated by downloading a newer AppImage next to the running one
  (see [AppGlider's own updates](#appgliders-own-updates)).

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
