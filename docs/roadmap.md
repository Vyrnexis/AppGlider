# Roadmap

Ideas for where AppGlider could go next. There are no promised dates or order, and plans
change after discussion. Something being listed here is not a promise that it will be built.

## Ideas

None of these are approved or scheduled.

- **Install queue:** line up several installs or updates, with clear progress and a way to
  cancel.
- **Hiding apps:** hiding an app without removing it would need more work and only makes
  sense if people ask for it. Removing an app from the list and adding it again with a
  catalog already covers most of it.

## Done

Roughly newest first. Where shown, the version is the release that first included the work.

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
