# Bundled assets

AppGlider's own generated icon and the individual applications' icons are bundled.
No ArtCraft launcher marks are used.
The source projects' MIT notices are distributed in `src/appglider/assets/licenses/`.

| Files | Author / source | Licence |
| --- | --- | --- |
| `assets/icons/appglider-update.png` | The AppGlider icon with an update badge, drawn in VectorCraft; source in [`docs/branding/appglider-update-icon.svg`](docs/branding/appglider-update-icon.svg). | Original project branding. |
| `assets/icons/appglider-taskbar.png` | The AppGlider wing on a dark rounded square, drawn in VectorCraft; source in [`docs/branding/appglider-taskbar-icon.svg`](docs/branding/appglider-taskbar-icon.svg). | Original project branding. |
| `assets/icons/appglider.png` | Drawn for AppGlider in VectorCraft; source in [`docs/branding/appglider-icon.svg`](docs/branding/appglider-icon.svg), details in [branding](docs/branding.md). | Original project branding; no upstream artwork used. |
| `assets/icons/{photocraft,vectorcraft,filmcraft,lightcraft,pdfcraft,effectcraft,designcraft,figcraft,wordcraft,soundcraft,deckcraft,cadcraft,gridcraft}.png` | ArtCraft Team and each application's contributors. Copied unmodified from [ArtCraft Launcher's app icons](https://github.com/storytold/craft-launcher/tree/main/assets/app-icons); original per-app source paths and notices are in its [ATTRIBUTION.md](https://github.com/storytold/craft-launcher/blob/main/ATTRIBUTION.md). | MIT, selected from MIT OR Apache-2.0; `assets/licenses/artcraft-MIT.txt` |
| `assets/icons/solidcraft.png` | SolidCraft contributors; copied unmodified from [the SolidCraft repository](https://github.com/tangency-labs/solidcraft/blob/main/assets/icons/app/linux/exports/hicolor/128x128/apps/ai.storyteller.solidcraft.png). | MIT, selected from MIT OR Apache-2.0; `assets/licenses/solidcraft-MIT.txt` |

Paths in this table are relative to `src/appglider/`. App names identify the managed
projects and do not imply endorsement. The example catalog's factual names and repository locations
were checked against the upstream repositories; GUI implementation is original to this project.

## Nix flake

`flake.nix` was originally contributed by NAX LAB under the MIT licence; the permission
notice is in `src/appglider/assets/licenses/nax-lab-MIT.txt`. It is distributed with AppGlider, which is
otherwise GPL-3.0-or-later.
