# Your desktop

How AppGlider fits into your desktop: the tray icon, starting when you log in, menu shortcuts and icons.

## The tray icon

If your desktop has a system tray, AppGlider sits in it. Click the icon to show the window. Right-click it for **Update all (3)** (only while updates are waiting), **Open AppGlider**, **Check for updates**, an **Installed apps** menu to launch an app (apps with an update say so), and **Quit AppGlider**. **Check for updates** works with no apps installed too; it then checks AppGlider itself. Messages from the tray say what happened, such as "PhotoCraft updated".

Closing the window hides AppGlider in the tray, so update checks and downloads carry on. To make closing quit instead, turn off **Keep running in the tray** in **Settings → General**. The first time you close the window, AppGlider tells you it is still running. To quit while it is in the tray, use **Quit AppGlider**. Without a tray, closing the window quits.

Only one AppGlider runs at a time. Starting it again shows the window that is already open. If that one does not answer, AppGlider says it is already running and to look for its icon in the system tray.

Some desktops, such as GNOME, need an extension to show tray icons. See [There is no tray icon](../troubleshooting.md#there-is-no-tray-icon).

## Starting with your desktop

Turn on **Start with my desktop** in **Settings → General** and AppGlider starts in the tray when you log in, so it can check for updates without a window. (Without a tray, or with **Keep running in the tray** off, it starts minimised.) It is off at first. It works by adding `~/.config/autostart/appglider.desktop`; deleting that file turns the setting off too. If your network isn't connected yet when AppGlider starts, it tries again a few times over the next few minutes.

## Menu shortcuts

AppGlider adds an entry to your application menu for itself and for each app it installs, named after the app (**PhotoCraft**). "(AppGlider)" is added only when another visible menu entry has the same name, and dropped again when that entry goes away. If one goes missing, use **⋯ → Create/repair menu shortcut**, or **Menu shortcut → Create or repair** on the app's page.

Each entry uses what the app says about itself: its menu category, description, the file types it opens and any extra actions (such as "New window"). An app that says nothing goes under **Utility**. Entries you or other programs made are never changed.

Some apps add a menu entry of their own the first time they run. AppGlider then adds "(AppGlider)" to its own entry's name so you can tell them apart, but both start the same program. When you uninstall the app through AppGlider, that extra entry is removed too.

## Icons

When you add an app, AppGlider looks for icons in the project's repository and lets you pick one, or use **Choose file…** to pick your own PNG or SVG file. Looking usually takes a second or two. When GitHub's allowance is low it browses the project's web pages instead, which can take 5 to 20 seconds; a message such as "Looking through OrcaSlicer's folders… 7 pages so far" shows it is still working. Leave the page to stop it.

To change an icon later, use **Icon → Change…** on the app's page, then **Find online**, **Choose file…** or **Use the default icon**. Icons are saved on your computer. Apps from the example catalog come with their icons, and an app without one gets AppGlider's. How the search works is described in [GitHub requests](../how-it-works/github-requests.md#icon-search).
