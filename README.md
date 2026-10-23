# AppGlider

<img src="src/appglider/assets/icons/appglider.png" alt="AppGlider icon" width="96">

**AppGlider installs, updates and launches AppImage apps on Linux, from one window.**

An AppImage is an app packed into a single file that runs on most Linux distributions
without being installed the usual way. They are handy, but they do not update themselves
or appear in your menu. AppGlider takes care of that: it downloads apps from their GitHub
pages, checks the download is intact, adds them to your application menu and lets you know
when a new version is out.

![AppGlider showing 12 installed apps, one with an update waiting](docs/previews/appglider-library.png)

*An example library using the default look. The versions shown are made up.*

## What it does

- Installs apps from public GitHub repositories and launches them from one searchable list.
- Checks your installed apps for updates when it starts and then on a schedule you choose.
  A badge appears on its tray icon when something is waiting.
- Checks each download against the checksum the project published before installing.
- Keeps the previous version so you can roll back if an update misbehaves.
- Lets you choose stable releases only, or try previews, for each app.
- Adds a menu entry for each app it installs and can sit in your system tray.
- Sorts your apps and lets you pin favourites to the top.
- Can show a desktop notification when new updates are found (off unless you turn it on).
- Shares lists of apps as plain text files you can read, edit and import
  (see [catalogs](docs/usage.md#catalogs-sharing-a-list-of-apps)).
- Follows your desktop's colours, or pick Light, Dark or one of three coloured themes.

AppGlider only manages the apps it installed itself. AppImages you already have, and apps
from other launchers, are left alone.

AppGlider was called AppHarbour until version 0.3.0. The name had to change because it was
already taken. Version 0.4.0 and later start with a fresh library and settings.

## Get AppGlider

1. Download the latest `AppGlider-…-linux-x86_64.AppImage` from the
   [Releases page](https://github.com/Vyrnexis/AppGlider/releases/latest).
2. Make it runnable: right-click it, open **Properties → Permissions** and tick **Allow
   executing as a program**, or run `chmod +x AppGlider-*.AppImage` in a terminal.
3. Double-click it, or run it from a terminal.

Keep the file somewhere permanent, such as `~/Applications`. AppGlider adds a menu entry
that points at it. If you move the file, start it once from its new place and the entry is
repaired.

**What you need:** a 64-bit (x86_64) Linux desktop that is about as new as Ubuntu 22.04 or
newer (technically, glibc 2.35 or newer). That covers current Ubuntu, Linux Mint, Fedora,
Debian 12 and Arch. It includes everything else it needs. If it will not start and the
message mentions FUSE, add `--appimage-extract-and-run` after the file name.

**Nix:** the repository has a flake (community-contributed, not tested on aarch64-linux).
Run `nix run github:Vyrnexis/AppGlider`, or add `packages.default` or `overlays.default`
to your configuration. AppGlider installs and launches ordinary AppImages, so on NixOS
also enable `programs.appimage.enable = true;` with `binfmt = true;`.

## First steps

AppGlider starts empty. Add apps in either of two ways:

- **+ Add app**: paste a GitHub repository address, choose **Inspect repository**, then
  **Add to library**.
- **Import catalog…** (in the **Catalog** menu under **Settings → Applications**): add a whole
  list at once from a plain-text file. **Example catalog** adds a ready-made list of the
  ArtCraft apps and SolidCraft that is built into AppGlider
  ([`docs/catalogs/artcraft.toml`](docs/catalogs/artcraft.toml)).

Adding an app only puts a card in your library. Press **Install** on the card to download
it. The project needs a published release with an AppImage and a checksum.

## Learn more

- [User guide](docs/usage.md): everything AppGlider can do, and what to try when something
  goes wrong.
- [Technical notes](docs/technical-notes.md): how it works behind the scenes, for the
  curious.
- [Roadmap](docs/roadmap.md): what may come next.

## For developers

AppGlider is written in Python with PySide6 and needs Python 3.11 or newer.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -O -m appglider
```

See [development and packaging](docs/development.md) for the checks and building the
AppImage, and [GitHub Actions](docs/builds.md) for automated builds and releases.

## Licence and credits

AppGlider's own code is licensed under [MIT](LICENSE).

The interface is original to this project. Application icons belong to their projects; see
[ATTRIBUTION.md](ATTRIBUTION.md) for their sources and licences. The libraries bundled in
the AppImage have their own [licences and source links](THIRD_PARTY_NOTICES.md).
