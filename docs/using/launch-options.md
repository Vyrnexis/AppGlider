# Launch options

Extra arguments and environment variables change how an app is started. They are set for each app, on its page.

## Arguments and environment

**Arguments** and **Environment** on an app's page change how that app is started. Both are optional, and are saved when you press **Enter** or leave the box.

- **Arguments**: extra options for the app, written as you would in a terminal, for example `--profile "work files" --safe-mode`.
- **Environment**: variables as `NAME=value` pairs separated by spaces, for example `GTK_THEME=Adwaita:dark LANG="de_DE.UTF-8"`. Put quotes around a value with spaces in it.

## Where they are used

They are used by **Launch** and by the app's menu entry. The app's extra menu actions (such as "New window") get the variables but not the arguments.

If AppGlider cannot read what you typed, it does not save it and says why. The options are kept when you uninstall the app.

> [!NOTE]
> Launch options are never included when you [export a catalog](../catalogs.md#export-your-list), because a variable can hold a password.
