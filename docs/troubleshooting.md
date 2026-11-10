# Troubleshooting

Answers to common problems, each under the thing you see. If yours is not here, [ask in Discussions](https://github.com/Vyrnexis/AppGlider/discussions) or [report a problem](https://github.com/Vyrnexis/AppGlider/issues/new/choose).

## AppGlider will not start, and the message mentions FUSE

Start it with `--appimage-extract-and-run` added, for example `./AppGlider-*.AppImage --appimage-extract-and-run`.

## An app quits right after it starts

The message shows the end of the app's log and says where the full log is. If it says AppImages need FUSE, install your distribution's FUSE 2 package (often called `fuse2` or `libfuse2`) and try again.

## An app will not run

Apps installed through AppGlider are the projects' own AppImages, and some need libraries your system does not have. Check the project's page for what it needs, and the log file AppGlider points you to.

## AppGlider's own menu entry stopped working

You probably moved AppGlider's file. Start it once from its new place and the entry is repaired.

## An app shows ⚠ File missing

AppGlider keeps each app's file in its own folder (see **Settings → Storage**). If something else moves or deletes that file, AppGlider can no longer launch or update the app, and its row looks like this:

![A row whose file is missing](previews/guide-file-missing.png)

1. The **⚠ File missing** chip. Hover over it to see where the file used to be. The app's page shows the same under **Versions**.
2. **Reinstall** downloads and checks the app again.

The usual cause is **AppImageLauncher**, a separate program that, the first time an AppImage runs, offers to "integrate" it and moves the file into `~/Applications`. Since version 0.9.4, AppGlider tells AppImageLauncher to leave its apps, and AppGlider itself, alone. A file it moved earlier stays where it went.

You have these choices:

| Choose | If you want to | What happens |
| --- | --- | --- |
| **Reinstall** | keep using the app through AppGlider | A fresh copy is downloaded into AppGlider's folder |
| **⋯ → Forget missing copy…** | look after the moved file yourself | The app stays on your list, as not installed |
| **⋯ → Forget missing copy…**, with **Also remove from my library** ticked | stop using the app | The app is taken off your list too |

None of these touches the moved file. If you no longer need it, delete it yourself, for example from `~/Applications`. More about these actions is in [Uninstalling, forgetting and removing](using/updates-and-versions.md#uninstalling-forgetting-and-removing).

## A project cannot be added

AppGlider needs a published release with an AppImage file and a published checksum, and says which is missing. If a project only publishes test versions, set **Release channel** to **Preview** first. See [Adding apps](using/adding-apps.md).

## A project renamed or moved its repository

Nothing to do. When GitHub sends the old address to a new one, AppGlider follows it the next time it checks or installs the app, remembers the new address and says so at the bottom of the window ("LightCraft Classic moved to emmertex/chromaforge on GitHub"). The app keeps its install, preferences and icon. Adding an app by its old address adds it under the new one.

## There is no tray icon

Some desktops, such as GNOME, need an extension to show tray icons. AppGlider works without one; closing the window then quits it. See [Your desktop](using/desktop.md#the-tray-icon).

## The sidebar says few GitHub requests remain

You have used most of GitHub's hourly allowance. It resets by itself, and scheduled checks wait until then. See [GitHub requests](how-it-works/github-requests.md).

## "Review N unrecognised entries…" appears at the bottom

AppGlider found apps it installed that are no longer on your list, for example from before you imported a catalog. Importing the catalog again usually matches them up. You can also tick the ones AppGlider should forget. Nothing on your disk is deleted. See [Library recovery](how-it-works/files-and-folders.md#library-recovery).

## On NixOS, no app will start

NixOS cannot run AppImages until its AppImage support is turned on; AppGlider says so when you press **Launch**. Add this to your NixOS configuration and run `sudo nixos-rebuild switch`:

```nix
programs.appimage = {
  enable = true;
  binfmt = true;
};
```

Menu entries for your apps start working at the same time.
