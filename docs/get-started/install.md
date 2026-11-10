# Install AppGlider

AppGlider is a single AppImage file. Download it, make it runnable and start it. There is nothing else to install.

## What you need

A 64-bit (x86_64) Linux desktop about as new as Ubuntu 22.04 or newer (technically, glibc 2.35 or newer). That covers current Ubuntu, Linux Mint, Fedora, Debian 12 and Arch. Everything else AppGlider needs is included.

## Download and start it

1. **Get AppGlider.** Download `AppGlider-…-x86_64.AppImage` from the [Releases page](https://github.com/Vyrnexis/AppGlider/releases/latest) and keep it somewhere permanent. Moving it into `~/Applications` is optional; it works from any folder you keep it in, and **Settings → Storage** can move it for you (see [Settings](../using/settings.md#move-appglider-here)).
2. **Make it runnable.** Right-click the file, open **Properties → Permissions** and tick **Allow executing as a program**, or run `chmod +x AppGlider-*.AppImage` in a terminal.
3. **Start it.** Double-click the file, or run it from a terminal.

AppGlider adds a menu entry that points at the file. If you move the file later, start it once from its new place and the entry is repaired.

Next, [add your first app](first-app.md).

## Nix and NixOS

The repository has a flake (community-contributed, not tested on aarch64-linux). Run `nix run github:Vyrnexis/AppGlider`, or add `packages.default` or `overlays.default` to your configuration.

AppGlider installs and launches ordinary AppImages, so on NixOS also enable `programs.appimage.enable = true;` with `binfmt = true;`. See [On NixOS, no app will start](../troubleshooting.md#on-nixos-no-app-will-start).

## If it will not start

If the message mentions FUSE, see [AppGlider will not start, and the message mentions FUSE](../troubleshooting.md#appglider-will-not-start-and-the-message-mentions-fuse). Other problems are covered in [Troubleshooting](../troubleshooting.md).
