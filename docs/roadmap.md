# Roadmap

Ideas for where AppGlider could go next. There are no promised dates or order, and plans
change after discussion. Something being listed here is not a promise that it will be built.

## Ideas

None of these are approved or scheduled.

- **Install queue:** line up several installs or updates, with clear progress and a way to
  cancel.
- **Smaller AppGlider updates:** download only the parts of a new AppGlider that changed,
  using the `.zsync` file published with every release (going from 0.9.5 to 0.9.6 would
  have needed about 2 MB instead of 48 MB). The rebuilt file would be checked against
  GitHub's SHA-256 like any download, blocks would only come from that release's own file,
  and anything unexpected would fall back to a full download. Apps that publish a `.zsync`
  could follow later; for some, such as VSCodium, little can be reused between versions.
  Finding the reusable parts is too slow in pure Python for a 44 MB file, so this would use
  a zsync tool installed on the computer, or a small compiled helper inside the AppImage.
- **AppGlider on a USB stick:** carry AppGlider and its apps on a stick and use them on any
  computer. AppGlider already runs from portable mode (`.home` and `.config` folders next to
  its file), but the library stores each app's full path, and a stick is mounted at a
  different place on each computer. Paths would need to be kept relative to the apps folder,
  menu and login entries skipped in portable mode, and a short guide written (an ext4 stick
  avoids FAT32's missing permissions and 4 GB file limit).
- **Builds that update from their own repository (for forks):** a fork that builds AppGlider
  on GitHub Actions still carries this repository in the AppImage's update information, so
  AppImageUpdate or Gear Lever would replace the fork's build with this one. Reading
  `GITHUB_REPOSITORY` during the build would point each build at the repository that made
  it (local builds keep this one). A nice to have for people who fork AppGlider; idea from
  the zonaro/AppGlider fork.
- **Linking a manual app to a repository:** an app added from a file could later be given
  the GitHub repository it has started publishing on, and from then on be updated like any
  other app, keeping its launch options, pin and place in the list. The installed file would
  have to be matched to a release build (or simply replaced at the next update). Idea from
  issue #13.
- **Hiding apps:** hiding an app without removing it would need more work and only makes
  sense if people ask for it. Removing an app from the list and adding it again with a
  catalog already covers most of it.

## Done

Roughly newest first. Where shown, the version is the release that first included the work.

- **Catalogs from an address (1.0.0):** **Settings → Catalogs → Import from an address** reads
  a catalog straight from a GitHub link, so a shared list no longer has to be downloaded
  first. Idea from issue #14.
- **Community catalog (1.0.0):** a shared list of apps in this repository that anyone can add
  to with a pull request. A check on each pull request confirms the app is valid, not listed
  twice, and publishes AppImages AppGlider can install. Idea from issue #14.
- **Shorter GitHub addresses (1.0.0):** an address such as `github.com/owner/app`, without
  `https://`, or a link to a page inside the repository is accepted. Reported in issue #15.
- **Commands before and after installing (0.9.9):** **Settings → Updates** can run your own
  command before each install, for example a virus scan that stops the install if it fails,
  and another one after it. Idea from issue #10.
- **Clearer uninstalling (0.9.9):** **Uninstall** and **Forget** ask whether to also remove the
  app from your library; without the tick it stays there to install again. **Roll back** sits
  beside the previous copy and can hold that version.
- **Easier adding and updating (0.9.9):** **Add and install** in one step, and a file copy can be
  cancelled. **Update all** skips apps that are open and says why it stopped. A picked AppImage
  is never run just to read its details.
- **Readable in every theme (0.9.9):** hints, chips and links are easy to read in light and dark
  themes with every accent. Failures that need action open a dialog that names the app, and
  uninstalling no longer pauses while it checks for a running app.
- **AppImages from a file (0.9.8):** **Add app → Choose AppImage…** adds a file that is
  already on the computer, reading its name, version and icon from inside it. These apps are
  never checked for updates; **Replace with a newer file…** keeps the old one for Roll back.
- **Move AppGlider here (0.9.8):** **Settings → Storage** can copy AppGlider into the
  install folder, restart from the copy and remove the old file. When an app quits with an
  error right after starting, the message shows the end of its log and explains a missing FUSE.
- **Update all, one app at a time on screen (0.9.7):** each app's row changes as soon as it is
  updated and leaves the Updates page, instead of all of them changing at the end; the apps
  still in line show **Waiting**.
- **Force close (0.9.7):** a frozen app can be closed from its **⋯** menu or its page, after asking,
  including the extra processes it started. Messages that ask you to close an app offer it too.
- **Portable apps keep their data (0.9.7):** an app's portable-mode folders (`Name.AppImage.home` and
  `Name.AppImage.config`) move along with updates and roll backs.
- **Repositories with many files per release (0.9.6):** update checks of projects such as
  VSCodium, which attach about 180 files to every release, no longer fail after the install.
- **Offline and quieter banners (0.9.5):** with no network, rows keep what was known before
  and the sidebar says **GitHub: offline**; checks at login try again once the network is up.
  Both banners can be closed until something new is out. Release notes open as a short
  preview that expands in place.
- **Renamed and moved repositories (0.9.5):** when a project renames its repository or moves it to
  another owner, AppGlider follows GitHub's redirect, remembers the new address and keeps the
  app's install and settings.
- **Missing files and clearer menus (0.9.4):** an app whose file was moved or deleted outside
  AppGlider shows **⚠ File missing** with **Reinstall** and **Forget missing copy**, and can be
  removed from the library. AppImageLauncher is told to leave AppGlider's apps alone. Row menus
  and the app page offer only the actions that fit the app's state.
- **Better icon search (0.9.2):** SVG icons with a standard `<!DOCTYPE svg>` line are accepted,
  and logos of preview builds and artwork that is far from square are ranked after the real
  icon.
- **Launch options (0.9.1):** **Arguments** and **Environment** fields in an app's
  Preferences, applied to **Launch**, the tray and the menu entry, kept out of exported
  catalogs.
- **Menu entries from the app's own details (0.9.1):** the entry uses the category, file
  types, keywords, description, window class and actions in the `.desktop` file inside the
  AppImage. It is read once, after the download is verified, and entries made by older
  versions are refreshed at startup.
- **AppGlider updating itself (0.9.0):** AppGlider checks for new versions of itself, shows a banner
  with what changed and offers **Update AppGlider**. It downloads and checks the new file,
  points the menu entry at it and offers **Restart now**. The replaced file is removed for
  you. **Settings → About** has **Check now**.
- **Space used (0.9.0):** a read-only panel in Settings → Storage shows how much space your apps use,
  split into the versions in use and the copies kept for roll back (striped), with every
  installed app listed largest first. It has no delete buttons: each app keeps only one
  earlier version and updating replaces it, so the space cannot keep growing.
- **A tidier Manage area (0.9.0):** an app page shows Uninstall while the app is installed and
  Remove from library once it is not, instead of both with one greyed out.
- **Update information for other tools (0.9.0):** the AppImage carries update information and each
  release includes a `.zsync` file, so tools such as AppImageUpdate can update it.
- **A new interface:** a sidebar (Library, Updates, Add app, Settings) with a status block,
  apps as rows with status chips, an updates banner with **Update all**, and a page for each
  app with its release notes, versions, preferences and a Manage area. Settings and Add app
  are pages in the same window, and there are Light and Dark themes with a choice of
  accent colour (Blue, Green, Amber or Teal). The window works from the keyboard.
- **Start with my desktop:** an opt-in switch in Settings → General writes an autostart entry
  that opens AppGlider in the tray at login (`--tray`).
- **Holding an app:** keep an installed app at its current version; it is still checked but
  never offered, counted or updated by **Update all**.
- **Nix flake (community-contributed):** `nix run github:Vyrnexis/AppGlider`, with a package
  and an overlay. It is not tested on aarch64-linux.
- **Icon search without the API allowance:** finding an icon now costs one request (down
  from about six), and none at all when the allowance is low, by browsing the repository's
  web pages. Results are also ranked better (names like `com.example.app`, and files named
  after the project).
- **Update notifications:** an optional desktop message when a scheduled check finds a new
  update, once per new version. It is off unless you turn it on in Settings.
- **Ordering and pinning:** your own order (drag the rows by their handle, use **Move up**
  and **Move down**, or **⋯ → Sort A–Z** in the toolbar) and **Pin to top** on each app,
  remembered between runs.
- **Example catalog built in:** an **Example catalog** button on the welcome screen and in the
  Settings → Catalogs section adds the ArtCraft apps and SolidCraft without downloading anything.
- **Cheaper update checks:** checking an app that has not changed now asks GitHub's ordinary
  release page first (or its public release feed, for apps with previews turned on) and uses
  none of the free request allowance. The update schedule can now run as often as every hour.
- **Scheduled update checks and a tray badge:** choose how often AppGlider checks (every
  3, 6, 12 or 24 hours, or only when you ask), and see an amber badge on the tray icon
  while updates are waiting.
- **A fresh start for new users:** the library is empty at first, and the ArtCraft apps and
  SolidCraft are an example catalog you can import instead of being built in.
- **Catalogs:** import and export plain text lists of apps, with a preview before anything
  is added.
- **GitHub status:** a status block in the sidebar shows how many of GitHub's
  anonymous requests are left and when checks were last made.
