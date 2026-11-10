# Updating AppGlider

AppGlider also keeps itself up to date. It checks for new versions of itself, and can download and switch to the new version for you.

## How it finds out

AppGlider checks for new versions of itself at the same times it checks your apps, and when you press **Check now** under **Settings → About**. It only looks for stable versions. When one is out, a banner in your accent colour appears above the list: "**AppGlider 0.9.8** is available", with a **What's new ↗** link.

## Update AppGlider

**Update AppGlider** on the banner:

1. downloads the new version into the same folder as the one you are running, and checks it the same way as an app you install (a failed or cancelled download leaves nothing behind),
2. points AppGlider's menu entry, and its login entry if you use one, at the new file,
3. changes the banner to say the new version is ready, with a **Restart now** button.

You do not have to restart straight away: the next time you open AppGlider, you get the new version. The first time it starts, it deletes the old file it replaced. Other AppGlider files you keep in that folder are never touched.

## If the button says Releases on GitHub

If AppGlider cannot write to its folder, or was not started from its AppImage, the button says **Releases on GitHub ↗** instead and opens the download page in your browser. Download the new file as described in [Install AppGlider](install.md).

## Closing the banner

The banner's **×** closes it until a newer AppGlider comes out. To bring it back sooner, press **Check now** under **Settings → About**.

The exact rules are in [Release checks](../how-it-works/release-checks.md#appgliders-own-updates).
