# AppGlider user guide

AppGlider keeps your AppImage apps in one place. It downloads them from their GitHub
pages, checks they arrived intact, adds them to your application menu and tells you when a
newer version is out. If you want the "how does it actually work" details, see the
[technical notes](technical-notes.md).

## Contents

- [The window](#the-window)
- [Adding apps](#adding-apps)
- [Installing and launching](#installing-and-launching)
- [Updates](#updates)
- [Stable versions and previews](#stable-versions-and-previews)
- [Choosing a build](#choosing-a-build)
- [Icons](#icons)
- [Catalogs: sharing a list of apps](#catalogs-sharing-a-list-of-apps)
- [Settings](#settings)
- [The tray icon](#the-tray-icon)
- [Menu shortcuts](#menu-shortcuts)
- [Uninstalling](#uninstalling)
- [If something goes wrong](#if-something-goes-wrong)

## The window

A new library is empty. You will see a welcome message with **+ Add app**, **Import
catalog…** and **Example catalog**.

Once you have apps, the tabs along the top filter them. The **cog** at the top right opens
Settings; **Sort** and **Refresh releases** sit above the cards.

![The AppGlider window showing 12 installed apps, one with an update waiting](previews/appglider-library.png)


- **All apps**: everything in your library, installed or not.
- **Installed (N)**: the apps AppGlider has installed.
- **Updates (N)**: installed apps with a newer version waiting.

The numbers always show your whole library, even while you are searching. The search box
matches names, descriptions and repository names.

Each app is a card. Click an app's name to open its details, where you can see its other
releases. The **⋯** menu on a card has Pin to top, Application preferences, Hold at current
version, Roll back to previous version, Uninstall and the menu-shortcut repair.

**Holding a version.** **⋯ → Hold at current version** keeps an installed app where it is.
AppGlider still checks a held app, but it never offers an update, counts it, notifies you or
includes it in **Update all**; the card says "Held" and shows which version is waiting.
Choose **Release hold** to update again. The hold is removed when you uninstall the app.

**Sorting and pinning.** The **Sort** box above the cards orders them by name (the default),
installed apps first, apps with updates first, or the order you added them. **⋯ → Pin to
top** puts an app at the front whatever the sort (it gets a gold ★ beside its name), and
**Unpin** puts it back. AppGlider remembers both. The tray menu's list of installed apps
follows the same order.

## Adding apps

AppGlider installs apps from public GitHub repositories. There are two ways to fill your
library.

**One at a time**

1. Choose **+ Add app**.
2. Paste the repository's address, such as `https://github.com/owner/project`, and give it
   a name if you like.
3. Leave **Stable releases only** selected unless you want to try previews.
4. Choose **Inspect repository** (or press Enter). AppGlider looks at the latest release and shows what it
   found.
5. Choose **Add to library**. This only creates a card. It does not install anything yet.

![The Add an application page after inspecting a repository](previews/appglider-add-app.png)

The project needs a published release with an AppImage file and a published checksum. If
something is missing, AppGlider tells you what.

**From a catalog**: see [Catalogs](#catalogs-sharing-a-list-of-apps) below.

## Installing and launching

Press **Install** on an app's card. AppGlider downloads the version that matches your
processor, checks it against the checksum the project published, makes sure it is a proper
AppImage, and adds an entry to your application menu such as **PhotoCraft (AppGlider)**.
After that the card shows **Launch**.

Installed apps work offline. If an app fails to start, AppGlider shows where its log file
is.

AppGlider only manages apps it installed itself. AppImages you downloaded some other way,
or installed with another launcher, are left alone.

## Updates

**Refresh releases** checks the apps you have installed to see whether newer versions
exist. It does not download anything. When an update is available, the card shows the new
version with an **Update** button, and **Update all** appears at the top.

AppGlider also checks by itself:

- when it starts, and
- on a schedule while it is running, including while it sits in the tray. Choose how often
  under **Settings → General → Check for updates**: every hour, 3, 6 (the default), 12 or 24
  hours, or **Only when I choose** if you would rather do it yourself.

Checks only happen while AppGlider is running. When at least one update is waiting, the
tray icon gets an amber badge with an up arrow, and its tooltip says how many ("AppGlider ·
Updates: 2").

**Desktop notifications (optional).** Tick **Settings → General → Show a desktop
notification when new updates are found** and AppGlider also pops up a message when a
scheduled check finds an update you have not been told about yet, such as "PhotoCraft 1.1.0
is available." Several updates are combined into one message, you are told once for each new
version, and nothing appears while you are looking at the AppGlider window or when you press
**Refresh releases** yourself. Clicking the message opens AppGlider. It is off by default,
and it needs a system tray; whether and how your desktop shows the message is up to your
desktop.

GitHub lets programs make about 60 anonymous requests an hour. AppGlider is careful with
them: to find out whether an app you have installed has a newer version, it first looks at
the app's ordinary GitHub release page (or, for apps with previews turned on, its public
release feed), which does not use up that allowance. It only uses the allowance when a new
version has really appeared, or when you install, add or inspect an app. AppGlider skips scheduled checks when GitHub says few requests are left. The
**GitHub** label at the bottom of the window shows how many remain. No GitHub account or
token is needed.

**Roll back.** AppGlider normally keeps the previous version of each app. If a new version
misbehaves, use **⋯ → Roll back to previous version**. A download that fails or is
cancelled never replaces the version you already have.

## Stable versions and previews

Projects often publish test versions ("beta", "preview") ahead of the real thing. By
default AppGlider only offers **stable** releases. To include previews for an app, open
**⋯ → Application preferences…** on its card, or **Settings → Applications** and choose the
app, then under **Updates** choose **Stable and prerelease versions**. **Check now** shows what that would give you without installing anything.

![An app's page in Settings: Updates and Application icon](previews/appglider-app-preferences.png)

- Green **Latest** badges mark the project's latest stable release. Amber **Pre-release**
  badges mark previews.
- If you turn previews off again, an installed preview keeps working, and AppGlider waits
  for a newer stable release. It never silently downgrades you. **Roll back** is the way
  to go back on purpose.

## Choosing a build

Some projects publish several AppImages for the same version, for example one for Ubuntu
22.04 and one for 24.04. When AppGlider cannot tell which one you need, a **Build variant**
choice appears when you add the app. Pick the one that suits your system, following that
project's own advice. AppGlider cannot tell which distribution you run. Your choice is
remembered for later updates. To change it, use **⋯ → Application preferences… → Build
variant → Choose another build…** (or open the app from **Settings → Applications**).

If the project later renames its files so AppGlider can no longer tell which build you
meant, the card shows **Choose build**. Your installed copy keeps working, and nothing
changes until you choose.

## Icons

AppGlider looks for an icon in the project's repository when you add it. This normally takes
a second or two. If GitHub's request allowance is low it browses the project's web pages
instead, which uses none of the allowance and usually takes 5 to 10 seconds (up to about 20 for
some big projects). While it works you will see a moving bar and a message such as "Looking
through OrcaSlicer's folders… 7 pages so far", so you can tell it has not stopped. To stop it,
go back or close Settings. You can pick one
of the candidates it finds, or **Choose file…** to use your own PNG or SVG file. To change
an app's icon later, open **Settings → Applications**, choose the app and
use **Find online** or **Choose file…** under **Application icon**. Icons are saved on your computer and work offline.
If there is no icon, AppGlider's own is used. Apps from the example catalog come with their
icons.

## Catalogs: sharing a list of apps

A catalog is a plain text file that lists apps. Open **Settings → Applications**, then use **Catalog → Import catalog…** to add everyone on a list at once, or
**Export catalog…** to save your own list, perhaps to back it up or share it.

A ready-made example list of the ArtCraft apps and SolidCraft is built in: choose **Example
catalog** on the welcome screen, or **Catalog → Example catalog…** in **Settings → Applications**. The same file is [`catalogs/artcraft.toml`](catalogs/artcraft.toml), also
attached to each release, if you want to read it or use it as a model for your own.

- **You see a preview first.** Importing only adds apps to your library as cards. Nothing
  is installed or downloaded until you press **Install**.
- **Your existing apps are never changed.** If a catalog lists something you already have,
  it is left exactly as it is.
- **It is just a text file.** You can open one in any text editor to see what it does, or
  write your own. Here is the whole format:

```toml
# lines starting with # are notes for humans
format = 1
name = "My catalog"

[[app]]
repo = "owner/project"            # required: owner/project or the full GitHub address
name = "Project"                  # optional: the name shown on the card
description = "What it does"      # optional: up to 200 characters
stable_only = true                # optional: false also offers previews
build = "Project-x86_64.AppImage" # optional: the exact AppImage file to prefer
```

If a catalog has a problem, AppGlider tells you which entries it skipped and why, and
imports the rest. A file that is not a proper catalog adds nothing at all.

## Settings

Open **Settings** (the cog at the top right). The sections are:

- **General**: Appearance, whether closing the window keeps AppGlider in the tray, how often
  to check for updates, and whether to show update notifications.
- **Applications**: a list of your apps with their update channel and build. Choose one to
  change its **Updates** (stable only or with previews), **Build variant** and icon, or to
  remove it; use **‹ Applications** to go back. **+ Add repository** opens a page where you
  paste a GitHub address and choose **Inspect repository** (or press Enter) before
  **Add to library**. The **Catalog** menu imports and exports lists. These pages save
  immediately, so they show **Close** instead of Save and Cancel.
- **Storage**: the folder new apps are installed into.
- **GitHub**: how many requests GitHub reports as remaining (no requests are made).
- **About**: the version and project links (the version number at the bottom right opens
  it).

![Settings, General page](previews/appglider-settings.png)

![Settings, Applications page](previews/appglider-applications.png)

**Appearance** offers System (the default, which follows your desktop's colours), Light,
Dark, and three coloured themes: Forest, Earthy and Sandstone. The window changes as you
pick, so you can try them out. **Save** keeps your choice, and **Cancel** goes back to the
one you had.

Changing the install folder (under **Storage**) only affects new installs. Apps you already installed stay where
they are and keep updating there.

## The tray icon

If your desktop has a system tray, AppGlider sits in it. Click the icon to bring back the
window. Right-click for **Open AppGlider**, **Refresh releases**, your installed apps and
**Quit AppGlider**.

Closing the window hides it to the tray and downloads carry on in the background. If you
would rather closing quit the program, turn that off in **Settings → General**. Without a
working tray, closing the window just quits. To really exit while it is in the tray, use
**Quit AppGlider**.

Only one AppGlider window runs at a time. Starting it again just shows the one that is
already open.

## Menu shortcuts

AppGlider adds an entry to your application menu for itself and for every app it installs,
named like **PhotoCraft (AppGlider)**. If one goes missing, use **⋯ → Create/repair menu
shortcut**.

Some apps add their own menu entry the first time you run them, without "(AppGlider)" in the
name. That is the app's doing, not AppGlider's, so such an app can appear twice in your
menu. They both start the same program. When you uninstall the app through AppGlider, its
own entry is removed too, so no dead entry is left behind.

## Uninstalling

**⋯ → Uninstall…** removes the app and its menu entries. Your documents and the app's
own settings stay. You cannot uninstall an app while it is running. To remove an app from
the list as well, uninstall it first, then remove it in **Settings → Applications**.

## If something goes wrong

**AppGlider will not start, and the message mentions FUSE.** Run it with
`--appimage-extract-and-run` added, for example
`./AppGlider-*.AppImage --appimage-extract-and-run`.

**The menu entry stopped working.** You probably moved the AppImage file. Start AppGlider
from its new location once and it repairs the entry.

**There is no tray icon.** Some desktops need an extension for tray icons (GNOME is the
usual example). AppGlider still works without one; closing the window then just quits it.

**"Review N unrecognised entries…" appears at the bottom.** AppGlider found installed apps
that are not in your repository list, for example apps installed before you imported the
example catalog. Importing the catalog usually makes them match again. You can also tick the
ones you want AppGlider to forget. Nothing on your disk is deleted.

**A project cannot be added.** AppGlider needs a published release with an AppImage file
and a published checksum, and it will say which is missing. Projects that only publish
test versions need previews turned on first.

**The GitHub label says few requests remain.** You have used up most of GitHub's anonymous
allowance for the hour. It resets on its own. Scheduled checks pause meanwhile.

**An app will not run.** Apps installed through AppGlider are the project's own AppImages,
and they may need libraries your system lacks. Check the project's page for its
requirements.
