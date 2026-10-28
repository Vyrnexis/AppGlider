# AppGlider

<img src="src/appglider/assets/icons/appglider.png" alt="AppGlider icon" width="96">

**AppGlider installs, updates and launches AppImage apps on Linux, from one window.**

Website: [appglider.org](https://appglider.org/)

An AppImage is an app packed into a single file that runs on most Linux distributions
without being installed the usual way. They are handy, but they do not update themselves
or appear in your menu. AppGlider takes care of that: it downloads apps from their GitHub
pages, checks the download is intact, adds them to your application menu and lets you know
when a new version is out.

![AppGlider's Library page with two updates waiting](docs/previews/appglider-library.png)

*An example library in the Dark theme with the default blue accent. The versions shown are
made up.*

## What it does

- Installs apps from public GitHub repositories and launches them from one searchable list
  of rows, with a sidebar for Library, Updates, Add app and Settings.
- Checks your installed apps for updates when it starts and then on a schedule you choose.
  A banner offers **Update all**, and a badge appears on its tray icon when something is
  waiting.
- Checks each download against the checksum the project published before installing.
- Keeps the previous version so you can roll back if an update misbehaves, and shows how
  much space your apps and those kept copies use.
- Lets you choose stable releases only, or try previews, for each app, and read the release
  notes on the app's own page before you update.
- Can hold an app at its current version so it is never offered an update.
- Adds a menu entry for each app it installs, in the category and with the file types the
  app asks for, and can pass each app arguments and environment variables you choose.
- Can sit in your system tray, start quietly there when you log in and, if you turn it on,
  show a desktop notification when updates are found.
- Keeps your apps in your own order (drag the rows, or sort A–Z) and lets you pin favourites
  to the top.
- Shares lists of apps as plain text files you can read, edit and import
  (see [catalogs](docs/usage.md#catalogs-sharing-a-list-of-apps)).
- Looks for new versions of AppGlider itself and, when you agree, downloads and checks the
  new file for you.
- Follows your desktop's light or dark setting, or choose Light or Dark and one of four
  accent colours (Blue, Green, Amber or Teal).

AppGlider only manages the apps it installed itself. AppImages you already have, and apps
from other launchers, are left alone.

AppGlider was called AppHarbour until version 0.3.0. The name had to change because it was
already taken. Version 0.4.0 and later start with a fresh library and settings.

## Get AppGlider

1. Download the latest `AppGlider-…-x86_64.AppImage` from the
   [Releases page](https://github.com/Vyrnexis/AppGlider/releases/latest).
2. Make it runnable: right-click it, open **Properties → Permissions** and tick **Allow
   executing as a program**, or run `chmod +x AppGlider-*.AppImage` in a terminal.
3. Double-click it, or run it from a terminal.

Keep the file somewhere permanent, such as `~/Applications`. AppGlider adds a menu entry
that points at it. If you move the file, start it once from its new place and the entry is
repaired. AppGlider updates itself by downloading the new file next to the old one (see
[Updating AppGlider](docs/usage.md#updating-appglider)).

**What you need:** a 64-bit (x86_64) Linux desktop that is about as new as Ubuntu 22.04 or
newer (technically, glibc 2.35 or newer). That covers current Ubuntu, Linux Mint, Fedora,
Debian 12 and Arch. It includes everything else it needs. If it will not start and the
message mentions FUSE, see [if something goes wrong](docs/usage.md#if-something-goes-wrong).

**Nix:** the repository has a flake (community-contributed, not tested on aarch64-linux).
Run `nix run github:Vyrnexis/AppGlider`, or add `packages.default` or `overlays.default`
to your configuration. AppGlider installs and launches ordinary AppImages, so on NixOS
also enable `programs.appimage.enable = true;` with `binfmt = true;`.

## First steps

AppGlider starts empty. Add apps in either of two ways:

- **Add app** (in the sidebar): paste a GitHub repository address, choose **Inspect
  repository**, then **Add to library**.
- **Import catalog…** (on the welcome screen, or under **Settings → Catalogs**): add a whole
  list at once from a plain-text file. **Example catalog** adds a ready-made list of the
  ArtCraft apps and SolidCraft that is built into AppGlider
  ([`docs/catalogs/artcraft.toml`](docs/catalogs/artcraft.toml)).

Adding an app only puts a row in your library. Press **Install** on the row to download it,
and click the app's name any time to open its page. The project needs a published release
with an AppImage and a checksum.

## Learn more

- [User guide](docs/usage.md): everything AppGlider can do, and what to try when something
  goes wrong.
- [Technical notes](docs/technical-notes.md): how it works behind the scenes, for the
  curious.
- [Roadmap](docs/roadmap.md): what may come next.

## For developers

AppGlider is written in Python with PySide6 and needs Python 3.11 or newer.
[Development and packaging](docs/development.md) covers setup, checks and building the
AppImage, [GitHub Actions](docs/builds.md) covers automated builds and releases, and the
[design brief](docs/design.md) covers the interface.

## Licence and credits

Copyright (C) 2026 Vyrnexis

AppGlider is free software: you can redistribute and/or modify it under the terms of the
GNU General Public License version 3, or (at your option) any later version. It is
distributed WITHOUT ANY WARRANTY. See [LICENSE](LICENSE) for details.

Releases up to and including 0.9.2 were published under the MIT licence and remain so.
Later releases are GPL-3.0-or-later.

The interface is original to this project. Application icons belong to their projects; see
[ATTRIBUTION.md](ATTRIBUTION.md) for their sources and licences. The libraries bundled in
the AppImage have their own [licences and source links](THIRD_PARTY_NOTICES.md).
