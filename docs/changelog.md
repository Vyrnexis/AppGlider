# Changelog

What changed in each release of AppGlider, newest first. Releases up to 0.3.0 were published as AppHarbour. Full notes for each release are on the [Releases page](https://github.com/Vyrnexis/AppGlider/releases). Ideas that may come next are on the [roadmap](roadmap.md).

## 1.0.0 — 10 November 2026

- **Import a catalog from an address** ([#14](https://github.com/Vyrnexis/AppGlider/issues/14)). **Settings → Catalogs → Import from an address** reads a catalog straight from a GitHub link (a `github.com/…/blob/…` or `raw.githubusercontent.com` address), so a shared list no longer has to be downloaded first. You see a preview before anything is added.
- **A community catalog** ([#14](https://github.com/Vyrnexis/AppGlider/issues/14)). [`catalogs/community.toml`](catalogs/community.toml) is a shared list of open-source apps that publish AppImages, including VSCodium, Obsidian, FreeCAD, Joplin and KeePassXC. Import it by address. Anyone can suggest an app with a pull request; a check confirms the entry is valid, not a duplicate, and installable by AppGlider. See [Contributing](../CONTRIBUTING.md#add-an-app-to-the-community-catalog).
- **Shorter GitHub addresses work** ([#15](https://github.com/Vyrnexis/AppGlider/issues/15)). **Add app** accepts `github.com/owner/app` without `https://`, as well as `http://`, `www.` and links to pages inside a repository, such as its releases.

## 0.9.9 — 7 November 2026

- **Run your own commands before and after installing** ([#10](https://github.com/Vyrnexis/AppGlider/issues/10)). **Settings → Updates** has a new **Install commands** panel, for example `clamscan --no-summary {file}` to scan a download for viruses before it is used. If the command fails, the install stops and the current version stays. See [Install commands](using/install-commands.md).
- **Choose whether an app stays in your library.** **Uninstall** and **Forget** ask whether to also remove the app from your library. The box starts unticked, so the app stays listed and can be installed again. **Remove from library** is offered only when nothing is installed.
- **Add and install in one step.** After looking up a repository, **Add and install** adds the app and installs it straight away. A file copy on the Add page can be cancelled.
- **Roll back beside the previous copy.** **Roll back** sits next to the earlier copy under **Versions** and can hold that version, so the next update check does not offer the newer one again.
- Hints, faint text, status chips and links are easy to read in the light and dark themes with every accent colour.
- **Update all** skips apps that are open instead of stopping, names any app that failed, and says why it stopped when it hits GitHub's rate limit or goes offline. Cancelling keeps the summary of what was done.
- Failures that need you to act open a dialog that names the app and the action, and full-disk and permission errors are explained in plain words. Moving AppGlider and rolling back ask before they start.
- Moving AppGlider into its folder replaces an older AppGlider file of the same name that is already there.
- Uninstalling no longer pauses while it checks whether the app is running. Version numbers with a `+` in them work, and long app names no longer go over the file name limit.
- An AppImage you pick from your computer is never run just to read its details.

## 0.9.8 — 4 November 2026

- **Add an AppImage you already have** ([#13](https://github.com/Vyrnexis/AppGlider/issues/13)). **Add app** has a section for an AppImage file on your computer. AppGlider reads its name, version and icon from inside the file, and copies it into your install folder. These apps show a **Manual** chip and are never checked for updates; **Replace with a newer file…** updates them.
- **Move AppGlider into its folder** ([#11](https://github.com/Vyrnexis/AppGlider/issues/11)). If AppGlider runs from somewhere else, such as Downloads, **Settings → Storage** offers **Move AppGlider here**.
- **"(AppGlider)" only when needed** ([#12](https://github.com/Vyrnexis/AppGlider/issues/12)). Menu entries are named just after the app. AppGlider adds "(AppGlider)" only when another menu entry has the same name.
- When an app quits with an error right after starting, the message shows the end of its log, and explains when FUSE is missing.
- When a release attaches identical files under different names, AppGlider treats them as one build instead of asking you to choose ([#9](https://github.com/Vyrnexis/AppGlider/issues/9)).
- A repository that moved to a new address is recognised as already added, and AppGlider's own repository can no longer be added as an app. An asset named only `.AppImage` is refused.
- Safer handling of content from releases: app names and errors in dialogs are plain text, release notes can no longer load images from your computer, and processing instructions are removed from SVG icons.

## 0.9.7 — 2 November 2026

- **Force close** ([#8](https://github.com/Vyrnexis/AppGlider/issues/8)). When an app freezes, **⋯ → Force close…** on its row, or **Force close** under **Manage** on its page, closes it after asking, including the extra processes it started.
- **Update all shows each app as it finishes.** The app being updated shows **Updating…**, the others **Waiting**, and each row turns **✓ Up to date** and leaves the Updates page as soon as it is updated. Apps are updated in the order of your list.
- **Portable apps keep their data.** An app's portable-mode folders (`Name.AppImage.home` and `Name.AppImage.config`) move along with updates and roll backs.
- The AppImage is about 3.5 MB smaller.

## 0.9.6 — 1 November 2026

- **Update checks no longer fail for apps with many files per release** ([#7](https://github.com/Vyrnexis/AppGlider/issues/7)). Some projects, such as VSCodium, attach about 180 files to every release, and every update check showed **Check failed**. AppGlider now asks GitHub for fewer releases at a time when that happens.
- The release build uses a fixed version of the AppImage packaging tool.

## 0.9.5 — 29 October 2026

- **No more "Check failed" on every app after you log in.** AppGlider now tries again after 15 seconds, then 30 seconds, 1, 2 and 5 minutes, and stops as soon as a check gets through. The same happens after waking from suspend.
- **Offline, rows keep what they knew.** When GitHub cannot be reached at all, rows keep their last known status and the sidebar says **GitHub: offline**. **Check failed** is kept for real problems, such as a repository that is gone.
- **Banners you can close.** The updates banner and the AppGlider banner each have a **×**, and stay closed until there is something new.
- **Renamed and moved repositories.** When a project renames its repository or moves it to another owner, AppGlider follows GitHub's redirect and keeps the app's install, preferences and icon.
- Release notes on an app's page open as a short preview with **Show all notes**. **Settings → About** links to the notes of the version you are running. The **Settings → GitHub** section is gone; the sidebar status keeps those details.

## 0.9.4 — 29 October 2026

- **Apps no longer seem to uninstall themselves** ([#6](https://github.com/Vyrnexis/AppGlider/issues/6)). AppGlider tells AppImageLauncher to leave its apps, and AppGlider itself, alone.
- **⚠ File missing.** If an app's file is moved or deleted outside AppGlider anyway, its row says so, shows where the file used to be, and offers **Reinstall** or **Forget missing copy…**.
- **Remove from library works again** for such apps ([#5](https://github.com/Vyrnexis/AppGlider/issues/5)).
- **Settings → Storage** warns you if you pick AppImageLauncher's folder as AppGlider's install folder.
- The **⋯** menu, **Manage** and **Preferences** show only what fits the app's state.
- A rewritten user guide with a quick start, a tour of the window and annotated screenshots. New issue forms on GitHub.

## 0.9.3 — 28 October 2026

- From this release AppGlider is **GPL-3.0-or-later**. Releases up to and including 0.9.2 were published under MIT and remain so.
- AppGlider could appear completely unstyled if the path to your cache folder contained a space or an apostrophe. Fixed.
- No more pauses when repairing a menu entry or saving launch options.
- A failed install keeps the current version's menu details. Faster update checks, and more robust handling of a damaged cache file.
- AppGlider has a home at [appglider.org](https://appglider.org/), and **Settings → About** has a **Visit appglider.org** button.

## 0.9.2 — 28 October 2026

- SVG icons from common editors (with the standard SVG 1.1 declaration line) are accepted ([#4](https://github.com/Vyrnexis/AppGlider/issues/4)).
- Better first icon suggestions: a project's own logo comes ahead of logos for its preview builds and ahead of artwork that is far from square.

## 0.9.1 — 28 October 2026

- **Menu entries that fit each app.** Entries use the details each app ships in its own `.desktop` file: menu category, description, file types, keywords, window class and extra actions.
- **Launch options.** **Arguments** and **Environment** on each app's page, kept out of exported catalogs. See [Launch options](using/launch-options.md).

## 0.9.0 — 26 October 2026

- **AppGlider updates itself.** A banner offers **Update AppGlider**, which downloads and checks the new AppImage, points your menu and login entries at it and starts it when you press **Restart now**. See [Updating AppGlider](get-started/updating-appglider.md).
- **Space used.** **Settings → Storage** shows how much space your apps take, with the copies kept for roll back.
- **A simpler Manage area.** **Uninstall** while the app is installed, **Remove from library** once it is not.
- On NixOS without AppImage support, launching an app explains which setting to add.
- The AppImage carries update information and each release has a `.zsync` file, so tools such as AppImageUpdate and Gear Lever can update AppGlider too. The download is named `AppGlider-0.9.0-x86_64.AppImage`.

## 0.8.1 — 25 October 2026

- AppGlider carries its own app information (name, description, screenshots) for software catalogs such as [appimage.github.io](https://appimage.github.io/). No changes to the app itself.

## 0.8.0 — 25 October 2026

- **A new interface** with one window and a sidebar: **Library**, **Updates**, **Add app** and **Settings**, plus a status block. Apps are rows with a status chip and one main button, a banner offers **Update all**, and each app has its own page. Themes are **System**, **Light** and **Dark** with a choice of accent colour.
- **Your own order:** drag rows by the handle, or use **Move up** and **Move down**. **Pin to top** keeps apps first.
- **Hold an app at its current version.**
- **Start with my desktop**, an opt-in switch that opens AppGlider in the tray at login.
- A community-contributed Nix flake.

## 0.7.1 — 22 October 2026

- Settings for the check interval and close to tray are saved where Qt reads them, so they no longer reset on every start.
- A new AppGlider wing icon.

## 0.7.0 — 22 October 2026

- Settings were reworked, with the main window getting a settings cog, a remembered size and clearer checkboxes in every theme.
- Release listings ask GitHub for the newest 20 releases instead of 100, so repositories with large nightly releases no longer exceed the metadata limit.

## 0.6.0 — 21 October 2026

- **Much cheaper update checks.** Checking an app that has not changed asks GitHub's ordinary release page first, or its public release feed for apps with previews turned on, and uses none of the free request allowance. The schedule can run as often as every hour.
- **Icon search that no longer eats your allowance:** one request instead of about six, and none when the allowance is low.
- An optional update notification, and ways to sort and pin your apps.

## 0.5.0 — 21 October 2026

- AppGlider starts empty. If you installed apps from the old built-in list with 0.4.0, they appear as unrecognised entries until you import the example catalog.
- **Catalogs.** Import or export a plain text list of apps, with a preview first.
- Scheduled update checks (every 3, 6, 12 or 24 hours, or only when you ask) and an amber badge on the tray icon while updates are waiting.

## 0.4.0 — 21 October 2026

- AppHarbour was renamed **AppGlider** because the old name was already in use. It starts with a fresh library and settings and does not import an AppHarbour library.
- A new icon, and one Appearance setting.

## 0.3.0 — 21 October 2026

- Starting AppGlider again raises the running window. Entries for apps that are not in the current list no longer stop the library from loading.
- Fewer GitHub requests, faster checks, and safer installs (a free-space check, and downloads whose size disagrees with the published size are rejected).

## 0.2.3 — 20 October 2026

- A GitHub status indicator beside the version number.
- Support for project-prefixed tags such as `studio-v0.6.0`, short tags such as `v0.6` and uppercase `V` prefixes.

## 0.2.2 — 20 October 2026

- Choose a build variant when several AppImages match your CPU, remembered separately from the stable or preview choice. Four-part versions such as `v02.08.02.61` are supported.

## 0.2.1 — 20 October 2026

- Fewer background requests and safer installs and shortcut repair; AppImage headers are validated before installation.

## 0.2.0 — 20 October 2026

- Opt-in preview releases for each app, green **Latest** and amber **Pre-release** badges, and release checks in the background.
- Compact app cards and a close-to-tray preference.

## 0.1.3 — 20 October 2026

- AppImages are found from the actual release files, so projects such as Ultimaker Cura with different file naming work ([#1](https://github.com/Vyrnexis/AppGlider/issues/1)).

## 0.1.2 — 19 October 2026

- Recognises `x64` and `amd64` as x86_64 and rejects wrong-CPU files and `.zsync` files.

## 0.1.1 — 19 October 2026

- Finds AppImage files whose names use capital letters.

## 0.1.0 — 19 October 2026

- The first release, as AppHarbour.
