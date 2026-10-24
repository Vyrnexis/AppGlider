# Roadmap

Ideas for where AppGlider could go next. There are no promised dates or order, and plans
change after discussion. Something being listed here is not a promise that it will be built.

## Planned

- **Install queue:** line up several installs or updates, with clear progress and a way to
  cancel.
- **AppGlider updating itself:** check for new versions of AppGlider, show what changed and
  offer an update button. Today you update AppGlider by downloading the new AppImage.
- **Storage overview:** show how much space your apps use, including the old versions kept
  for roll back, and let you delete an old copy you no longer need.

## Under discussion

- **Hiding apps:** hiding an app without removing it would need more work and only makes
  sense if people ask for it. Removing an app from the list and adding it again with a
  catalog already covers most of it.

## Done

- **A new interface:** a sidebar (Library, Updates, Add app, Settings) with a status block,
  apps as rows with status chips, an updates banner with **Update all**, and a page for each
  app with its release notes, versions, preferences and a Manage area. Settings and Add app
  are pages in the same window, and there are Light and Dark themes with a choice of
  accent colour (Blue, Green, Amber or Clay). The window works from the keyboard.
- **Your own order:** drag rows into a custom order (or use **Move up** and **Move down**);
  pinned apps stay on top.
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
- **Sorting and pinning:** a **Sort** box (name, installed first, updates first, order added)
  and **Pin to top** on each app, remembered between runs.
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
- **GitHub status:** a status block in the sidebar and a Settings → GitHub page show how many of GitHub's
  anonymous requests are left and when checks were last made.
