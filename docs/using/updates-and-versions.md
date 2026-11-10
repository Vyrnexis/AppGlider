# Updates and versions

How AppGlider finds updates, how to install them, how to choose between stable versions and previews, and how to go back to an earlier version. It also covers uninstalling, forgetting and removing apps.

## Finding out about updates

AppGlider checks your installed apps for new versions when it starts, and then every 6 hours while it is running (also while it sits in the tray). You can change how often under **Settings → Updates**, or choose **Only when I choose**. To check right now, press the round arrow in the toolbar, or **Check for updates** in the tray menu. Checking never downloads anything.

When an update is waiting:

- the row shows the new version and an **Update** button,
- the banner at the top offers **Update all**,
- **Updates** in the sidebar and the tray icon get an amber badge.

If you'd rather not see the banner, press its **×**. It stays closed until a newer version or another app's update comes out. The rows and the sidebar count still show what's waiting, and the **Updates** page always keeps the banner with **Update all**.

**Read before you update.** An app's page shows **What's new**, the project's notes for the new version.

## Updating one app or all of them

Press **Update** on a row to update one app. **Update all** updates one app at a time, in the order of your list. The app being updated shows **Updating…** with a percentage while it downloads, the others **Waiting**. As each one finishes, its row shows **✓ Up to date** and leaves the **Updates** page, and the counts go down. At the end a summary says what happened, for example "Updated 3 apps. Skipped because running: Krita, GIMP. Close them and press Update all again."

- An app that is **running** is skipped, and the others carry on. A dialog lists the skipped apps and offers **Force close** for them.
- An app that **needs a build choice** is left alone and named in the summary. If there is only one, its page opens so you can choose.
- If you **cancel**, the apps already updated stay updated, and the summary says so ("Cancelled. Updated 1 app.").
- If GitHub **cannot be reached** or its allowance runs out, **Update all** stops and a dialog says why and when to try again, with how many apps were updated.
- An app that could not be updated keeps its **↑** chip.

## Notifications

Turn on **Settings → Updates → Notify me about new updates** to get a desktop notification when a scheduled check finds something new. You are told once per new version, several updates share one message, and nothing pops up while you are looking at AppGlider. It needs a system tray.

## Stable versions and previews

Projects often publish test versions ("beta", "preview") before the finished one. AppGlider offers only **stable** versions unless you ask for more. To include previews for one app, open its page and set **Release channel** to **Preview**. **Check now** under **Versions** shows what that would offer, without installing anything.

- A green **Latest** badge marks the project's newest stable version. An amber **Pre-release** badge marks a preview.
- If you switch previews off again, an installed preview keeps working, and AppGlider waits for a newer stable version. It never downgrades an app by itself; [rolling back](#rolling-back) is the way to go back on purpose.

## Choosing a build

Some projects publish several files for the same version, for example one for Ubuntu 22.04 and one for 24.04. When AppGlider cannot tell which one you need, it asks you to choose when you add the app. Pick the one that suits your system, following the project's own advice. AppGlider remembers your choice for later updates. To change it, use **Build → Change…** on the app's page.

If the project later renames its files so AppGlider can no longer tell which one you meant, the row shows **Choose build**. Your installed copy keeps working, and nothing changes until you choose.

## Holding a version

To keep an installed app at the version it has, switch on **Hold at this version** on its page, or use **⋯ → Hold at current version**. AppGlider still checks a held app, but never offers to update it, counts it, notifies you about it or includes it in **Update all**. The row says **‖ Held**, and shows which version is waiting. Switch the hold off (or choose **⋯ → Release hold**) to update it again.

## Rolling back

AppGlider keeps the previous version of each app. If a new version misbehaves, use **⋯ → Roll back to previous version**, or **Roll back** beside **Previous copy** under **Versions** on the app's page. The dialog has **Hold at … so the newer version isn't offered again**, ticked at first: the app then stays on the restored version until you release the [hold](#holding-a-version). Untick it to be offered the newer version again. If the app is running, the dialog offers **Force close…** first.

## Portable apps

Some people keep an app's settings in folders next to its AppImage, named after the file with `.home` or `.config` added (AppImage's "portable mode"). AppGlider moves these folders along when it updates the app or rolls it back, so the app keeps its settings. Close the app first; AppGlider asks you to if it is still open. Uninstalling never deletes them.

## Force close

When an app has frozen, **⋯ → Force close…** (or **Force close** under **Manage** on the app's page) closes it, after asking. Both appear only while the app is running, however you started it. AppGlider first asks the app to close; if it is still running two seconds later, it is stopped. Any unsaved work in it is lost. Only apps AppGlider installed are closed, never anything else. When an update, roll back or uninstall cannot go ahead because the app is open, the message offers **Force close…** too.

## Versions on an app's page

The **Versions** area of an app's page (see [the app's page](window.md#the-apps-page)) shows the installed version, the earlier copy kept for rolling back (with its **Roll back** link), the newest stable and preview versions, and when they were last checked. **Check now** looks again. An app that is not installed has no Installed line.

An app that was [added from a file](adding-apps.md#an-appimage-already-on-your-computer) has no Versions list; its **Roll back** is under **Manage**.

## Uninstalling, forgetting and removing

An app has two parts: its **file** (the AppImage AppGlider installed) and its **card** in your library. Uninstalling deals with the file; removing deals with the card. Each action asks first, with its own name on the button and **Cancel** as the default, and none of them touches your documents or the app's own settings.

| Action | Offered when | What it does | The card |
| --- | --- | --- | --- |
| **Uninstall…** | The app is installed | Deletes the app's file, the earlier copy kept for rolling back and its menu entry, and releases a [hold](#holding-a-version) | Stays, as Not installed, unless you tick **Also remove from my library** (not ticked at first) |
| **Forget missing copy…** | The app shows ⚠ File missing | Stops tracking the missing file and removes its menu entry. Deletes no files | Stays, unless you tick **Also remove from my library** |
| **Remove from library…** | The app is not installed and its file is not missing | Takes the card off your list, with its preferences and icon. Deletes no app files | Gone |

An app [added from a file](adding-apps.md#an-appimage-already-on-your-computer) has nothing to download again, so for it **Uninstall…** removes the card too (there is no checkbox, and the dialog says so; the file you added is not touched), and a missing file offers just **Forget…**, which removes the card and the kept earlier copy.

All of these are in the row's **⋯** menu and under **Manage** on the app's page, and the menu offers only the one that fits the app's state.

- **To get rid of an app completely:** **Uninstall…** it and tick **Also remove from my library**.
- **To reinstall later:** leave the box unticked; the card stays, and **Install** is there.

If the app is running, the dialog says so first and offers **Force close…**, rather than failing after you confirmed.

## GitHub's allowance

GitHub lets programs without an account make about 60 requests an hour. AppGlider is careful with them: a routine check reads the project's ordinary release page first, which costs nothing, and only uses the allowance when a new version has really appeared, or when you add, inspect or install an app. If few requests remain, scheduled checks wait until GitHub resets the allowance. The status block in the sidebar shows what is left. You do not need a GitHub account. The details are in [GitHub requests](../how-it-works/github-requests.md).
