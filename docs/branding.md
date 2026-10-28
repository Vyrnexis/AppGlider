# AppGlider branding

This page is for contributors and designers.

AppGlider is the user-facing name. Its icon is a swept wing of three
S-curved feathers in ivory cream and forest greens (`#F4F3EB`, `#8FAE75`, `#486140`) on a transparent
background, so it reads on both light and dark desktops and panels. The brand
palette is `#1C1F1A`, `#5B7F52`, `#8FAE75`, `#D6D1C4` and `#F4F3EB`. The name replaced
AppHarbour, which was already in use.

The themes and accent colours (System, Light and Dark; Blue, Green, Amber or Teal) and their
colour values are in the [design brief](design.md#visual-tokens).

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

The names, file names and paths AppGlider uses are listed in the
[technical notes](technical-notes.md#names-and-identifiers).
