# Files and folders

Where AppGlider keeps its files, what it writes to your menu, and how it names things. AppGlider only ever touches files it installed itself.

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

Each version is installed in its own folder (`<apps>/<app id>/<version>/<file>.AppImage`).
The AppImage runtime treats folders named `<file>.AppImage.home` and `<file>.AppImage.config`
next to the file as the app's home and settings folders (portable mode). On an update and on
**Roll back**, these two folders are moved (renamed, so nothing is copied) to sit next to the
version that becomes active, under its file name. That way the data follows the active
version, as settings in `~/.config` are shared by every version. The move happens just before
the library is saved and is undone if saving fails. A folder that already exists next to the
target is never overwritten: both are then left where they are. If the app is running from
the old version, the update or roll back stops with a request to close it, before anything is
downloaded; apps without these folders update while running as before. Other files next to an
AppImage are left alone, and uninstalling removes only the AppImages, so these folders stay.

**Force close** finds an app's processes in `/proc`: the current user's processes whose
executable is in the app's own folder (`<apps>/<app id>/`, which must carry AppGlider's marker),
or whose environment has `APPIMAGE=` pointing into it. The AppImage runtime sets that variable
and child processes inherit it, so helper processes and copies started from the menu, the tray
or a terminal are included, as is an earlier version still open after an update. AppGlider's
own process is never included. Each process is held with a `pidfd` and checked again before
any signal, so a process number reused by another program is never signalled. The Python in
the AppImage is built without `os.pidfd_open`, so the `pidfd_open` and `pidfd_send_signal`
system calls are made through `ctypes`; on kernels before 5.3, which have neither, a process is
checked again just before it is signalled by number. The packaged smoke test checks this. The processes
get `SIGTERM`, then `SIGKILL` if still running after two seconds; the work runs off the Qt
thread. The ⋯ menu checks when it opens, and the app page checks every three seconds while
it is shown (a scan takes a few milliseconds). An app that removes `APPIMAGE` from its own
environment and runs from outside its mount is not found.
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

## Apps added from a file

**Add app → Choose AppImage…** adds an AppImage from this computer that has no GitHub
repository. AppGlider never checks such an app for updates: it is skipped by release checks,
**Update all**, the update counts and GitHub's allowance accounting, and has no release channel,
hold, build, What's new or Versions.

- **Copy.** `place_appimage` (`install/place.py`) copies the file into
  `<apps>/<id>/<version>/<file>.AppImage` under the usual `.appglider` marker, so Uninstall,
  Force close, launch options, icon, menu shortcut, pin and ordering work as for any install.
  The original is not changed. A file whose bytes (SHA-256) equal an installed file is refused.
- **Name, version, icon** come from inside the AppImage (`read_identity` in
  `install/desktopentry.py`, using the same `--appimage-extract` as menu details): the
  `.desktop` file's `Name` and `X-AppImage-Version`, and its icon. The version falls back to the
  file name (`release_version`), then to unknown (shown as `?`; the folder is
  `0.0+<8 hex digits of the SHA-256>`). A file that is not executable is first copied to a
  temporary executable file for this reading. The name can be edited before adding.
- **Replace with a newer file…** (App page, Preferences) copies another file in, keeps the old
  one as the roll-back copy exactly as an update does (the copy before that is removed) and
  moves portable-mode folders with it. The repository entry's `file` follows the new name.
  A version folder that already exists gets `+<8 hex digits>` appended.
- **Uninstall** (and **Forget missing copy**) removes the app from the library too, since
  there is nothing to install again. The original file is never touched.
- **Id and storage.** The id is `file-` and 12 random hex digits. Repository ids are bundled
  names or begin with `github-`, so they cannot collide. `repositories.json` gets entries
  `{"id": "file-…", "name": "…", "file": "Name.AppImage"}` with no `repo`; entries with `repo`
  are read and written exactly as before, so existing data loads unchanged. (An older AppGlider
  refuses a file containing such an entry and preserves it.) `library.json` records may carry
  an optional `previous_version` (a string, `""` for unknown) giving the version of the kept
  copy when its folder name cannot, which only manual apps use.
- **Catalogs.** Export skips these apps (the file is not portable and a catalog has no field
  for it); import is unaffected.

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

## Catalog files

A catalog is TOML with `format = 1`, an optional `name` and one `[[app]]` table per
repository (`repo`, and optionally `name`, `description`, `stable_only` and `build`). The
[Catalogs page](../catalogs.md#the-file-format) shows the whole format. `stable_only`
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
