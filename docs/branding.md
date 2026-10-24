# AppGlider branding and identity

This page is for contributors and designers.

AppGlider is the user-facing name. Its icon is a swept wing of three
S-curved feathers in ivory cream and forest greens (`#F4F3EB`, `#8FAE75`, `#486140`) on a transparent
background, so it reads on both light and dark desktops and panels. The brand
palette is `#1C1F1A`, `#5B7F52`, `#8FAE75`, `#D6D1C4` and `#F4F3EB`. The name replaced
AppHarbour, which was already in use.

Settings → General offers System (follows the desktop's light or dark preference, the
default), Light and Dark, plus an accent colour: Blue (the default), Green, Amber or Clay.
The retired Forest, Earthy and Sandstone themes are read as Dark with Green, Clay or Amber.

While updates are waiting, the tray shows `appglider-update.png` (source
`docs/branding/appglider-update-icon.svg`): the same wing with an amber badge and an up arrow
in the lower right, a transparent ring cut around the badge so it separates from the
feathers. The arrow keeps it from relying on colour alone.

`appglider-taskbar.png` (source `docs/branding/appglider-taskbar-icon.svg`) is the same wing on
a dark rounded square. It is the window, taskbar, desktop shortcut and AppImage icon; the
transparent `appglider.png` is used inside the app and for the tray. PNG sizes from 16 to 512 px
for both are in `docs/branding/icons/`.

The vector source is `docs/branding/appglider-icon.svg`: the feathers have real gaps cut
between them so they render cleanly across light and dark backgrounds.
`src/appglider/assets/icons/appglider.png` is its 1024 px render. Qt renders the same PNG at
the sizes required by the window, sidebar, desktop shortcut and tray; no extra dependency is
needed. The bundled icons for the ArtCraft example catalog's apps remain separate.

## Application identity

- Display name and Qt organisation/application IDs: `AppGlider`.
- Python module and command: `appglider`.
- Library, custom repositories, release cache and icons: `$XDG_DATA_HOME/appglider/`
  (normally `~/.local/share/appglider/`). The release cache holds `etags.json`; the data
  folder also holds `library.lock` and `appglider.lock` (single-instance guard, whose
  wake-up socket lives in `$XDG_RUNTIME_DIR`).
- Preferences: `~/.config/AppGlider/AppGlider.conf`, respecting `XDG_CONFIG_HOME`.
- Installation ownership marker: `.appglider`, with `AppGlider` as its first line.
- Managed-app shortcuts: `appglider-<id>.desktop`, marked `X-AppGlider=true`.
- Launcher shortcut and Qt desktop-file ID: `appglider.desktop` / `appglider`.
- Optional autostart entry: `$XDG_CONFIG_HOME/autostart/appglider.desktop` (default
  `~/.config/autostart`), marked `X-AppGlider=true`, running the launcher command with
  `--tray`. Its existence is the setting; a symlink or unmarked file is never touched.

## Tray behaviour

Click restores the window. The context menu opens AppGlider, checks for updates, launches
managed installed apps or quits. Closing hides only while a tray is available; otherwise
normal close behaviour remains. Downloads continue while hidden, and Quit waits for a
confirmed cancellation to finish before exiting. Notifications are limited to update alerts.

Qt tray integration follows the [QSystemTrayIcon documentation](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QSystemTrayIcon.html).
