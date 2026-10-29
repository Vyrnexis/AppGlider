<p align="center">
  <img src="src/appglider/assets/icons/appglider.png" alt="AppGlider icon" width="96">
</p>

<h1 align="center">AppGlider</h1>

<p align="center">
  <strong>Install, update and launch AppImage apps on Linux, from one window.</strong>
</p>

<p align="center">
  <a href="https://github.com/Vyrnexis/AppGlider/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/Vyrnexis/AppGlider?label=release"></a>
  <a href="LICENSE"><img alt="Licence: GPL-3.0-or-later" src="https://img.shields.io/badge/licence-GPL--3.0--or--later-blue"></a>
  <img alt="Platform: Linux x86_64" src="https://img.shields.io/badge/platform-Linux%20x86__64-lightgrey">
</p>

<p align="center">
  <a href="https://github.com/Vyrnexis/AppGlider/releases/latest"><strong>Download</strong></a> ·
  <a href="docs/usage.md#quick-start">Quick start</a> ·
  <a href="docs/usage.md">User guide</a> ·
  <a href="https://appglider.org/">Website</a>
</p>

![AppGlider's Library page with two updates waiting](docs/previews/appglider-library.png)

<p align="center"><em>The Library in the Dark theme. The versions shown are made up.</em></p>

## Why AppGlider?

An AppImage is an app packed into a single file that runs on most Linux distributions without
being installed the usual way. They are handy, but they do not update themselves or appear in
your menu. AppGlider takes care of that: it downloads apps from their GitHub pages, checks each
download is intact, adds them to your application menu and tells you when a new version is
out.

AppGlider only manages the apps it installed itself. AppImages you already have, and apps from
other launchers, are left alone.

## Highlights

**Install with confidence**

- Add any public GitHub repository that publishes AppImages, and install it with one button.
- Every download is checked against the checksum the project published before it is used.
- The previous version is kept, so you can roll back if an update misbehaves.

**Stay up to date**

- Checks your apps when it starts and on a schedule you choose, with a badge on the tray icon
  and optional desktop notifications.
- Read the release notes on the app's page, then **Update** one app or **Update all**.
- Stable versions by default; previews only for the apps you choose. Hold any app at its
  current version.
- Keeps itself up to date too.

**Fits your desktop**

- Adds a menu entry for each app, with the category, file types and actions the app asks for.
- Per-app launch arguments and environment variables.
- Follows your desktop's light or dark setting, with four accent colours. Can sit in the tray
  and start when you log in.
- Your own order: drag the rows, or pin favourites to the top.

**Share your list**

- Export your apps as a plain text [catalog](docs/usage.md#catalogs-sharing-a-list-of-apps)
  that a friend can import, or that you can keep as a backup.

## Screenshots

<table>
  <tr>
    <td width="50%"><a href="docs/previews/appglider-app-page.png"><img src="docs/previews/appglider-app-page.png" alt="An app's page with release notes, versions, preferences and Manage"></a></td>
    <td width="50%"><a href="docs/previews/appglider-add-app.png"><img src="docs/previews/appglider-add-app.png" alt="The Add app page after inspecting a repository"></a></td>
  </tr>
  <tr>
    <td align="center"><strong>An app's page</strong>: release notes, versions and preferences</td>
    <td align="center"><strong>Add app</strong>: paste a GitHub address and check it first</td>
  </tr>
  <tr>
    <td width="50%"><a href="docs/previews/appglider-updates.png"><img src="docs/previews/appglider-updates.png" alt="The Updates page with two updates waiting"></a></td>
    <td width="50%"><a href="docs/previews/appglider-storage.png"><img src="docs/previews/appglider-storage.png" alt="Settings, Storage with the space used by each app"></a></td>
  </tr>
  <tr>
    <td align="center"><strong>Updates</strong>: everything waiting, in one list</td>
    <td align="center"><strong>Storage</strong>: the space your apps use</td>
  </tr>
</table>

## Get AppGlider

1. Download the latest `AppGlider-…-x86_64.AppImage` from the
   [Releases page](https://github.com/Vyrnexis/AppGlider/releases/latest).
2. Make it runnable: right-click it, open **Properties → Permissions** and tick **Allow
   executing as a program**, or run `chmod +x AppGlider-*.AppImage` in a terminal.
3. Double-click it, or run it from a terminal.

Keep the file somewhere permanent, such as `~/Applications`. AppGlider adds a menu entry that
points at it; if you move the file, start it once from its new place and the entry is
repaired.

**What you need:** a 64-bit (x86_64) Linux desktop about as new as Ubuntu 22.04 or newer
(technically, glibc 2.35 or newer). That covers current Ubuntu, Linux Mint, Fedora, Debian 12
and Arch. Everything else it needs is included. If it will not start and the message mentions
FUSE, see [if something goes wrong](docs/usage.md#if-something-goes-wrong).

<details>
<summary><strong>Nix and NixOS</strong></summary>

The repository has a flake (community-contributed, not tested on aarch64-linux). Run
`nix run github:Vyrnexis/AppGlider`, or add `packages.default` or `overlays.default` to your
configuration. AppGlider installs and launches ordinary AppImages, so on NixOS also enable
`programs.appimage.enable = true;` with `binfmt = true;`.

</details>

## First steps

AppGlider starts empty. Add apps in either of two ways:

- **Add app** (in the sidebar): paste a GitHub repository address, choose **Inspect
  repository**, then **Add to library**.
- **Import catalog…** (on the welcome screen, or under **Settings → Catalogs**): add a whole
  list at once from a plain text file. **Example catalog** adds a ready-made list of the
  ArtCraft apps and SolidCraft ([`docs/catalogs/artcraft.toml`](docs/catalogs/artcraft.toml)).

Adding an app only puts a row in your library. Press **Install** on the row to download it,
and click the row any time to open the app's page. The project needs a published release with
an AppImage and a checksum.

The [quick start](docs/usage.md#quick-start) in the user guide walks through this with
pictures.

## Help and feedback

- [User guide](docs/usage.md): a quick start, a tour of the window, everything AppGlider can
  do, and what to try when something goes wrong.
- [Questions and ideas](https://github.com/Vyrnexis/AppGlider/discussions): ask in
  Discussions.
- [Report a problem](https://github.com/Vyrnexis/AppGlider/issues/new/choose): the form asks
  for what is needed to fix it.
- [Technical notes](docs/technical-notes.md): how it works behind the scenes, for the curious.
- [Roadmap](docs/roadmap.md): what has been done and what may come next.

## For developers

AppGlider is written in Python with PySide6 and needs Python 3.11 or newer.
[Development and packaging](docs/development.md) covers setup, checks and building the
AppImage, [GitHub Actions](docs/builds.md) covers automated builds and releases, and the
[design brief](docs/design.md) covers the interface.

## History

AppGlider was called AppHarbour until version 0.3.0. The name had to change because it was
already taken. Version 0.4.0 and later start with a fresh library and settings.

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
