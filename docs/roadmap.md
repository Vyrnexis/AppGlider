# Roadmap

Ideas for where AppGlider could go next. There are no promised dates or order, and plans
change after discussion. Something being listed here is not a promise that it will be built.

## Ideas

None of these are approved or scheduled.

- **Install queue:** line up several installs or updates, with clear progress and a way to
  cancel.
- **Smaller AppGlider updates:** download only the parts of a new AppGlider that changed,
  using the `.zsync` file published with every release (going from 0.9.5 to 0.9.6 would
  have needed about 2 MB instead of 48 MB). The rebuilt file would be checked against
  GitHub's SHA-256 like any download, blocks would only come from that release's own file,
  and anything unexpected would fall back to a full download. Apps that publish a `.zsync`
  could follow later; for some, such as VSCodium, little can be reused between versions.
  Finding the reusable parts is too slow in pure Python for a 44 MB file, so this would use
  a zsync tool installed on the computer, or a small compiled helper inside the AppImage.
- **AppGlider on a USB stick:** carry AppGlider and its apps on a stick and use them on any
  computer. AppGlider already runs from portable mode (`.home` and `.config` folders next to
  its file), but the library stores each app's full path, and a stick is mounted at a
  different place on each computer. Paths would need to be kept relative to the apps folder,
  menu and login entries skipped in portable mode, and a short guide written (an ext4 stick
  avoids FAT32's missing permissions and 4 GB file limit).
- **Builds that update from their own repository (for forks):** a fork that builds AppGlider
  on GitHub Actions still carries this repository in the AppImage's update information, so
  AppImageUpdate or Gear Lever would replace the fork's build with this one. Reading
  `GITHUB_REPOSITORY` during the build would point each build at the repository that made
  it (local builds keep this one). A nice to have for people who fork AppGlider; idea from
  the zonaro/AppGlider fork.
- **Linking a manual app to a repository:** an app added from a file could later be given
  the GitHub repository it has started publishing on, and from then on be updated like any
  other app, keeping its launch options, pin and place in the list. The installed file would
  have to be matched to a release build (or simply replaced at the next update). Idea from
  issue #13.
- **Hiding apps:** hiding an app without removing it would need more work and only makes
  sense if people ask for it. Removing an app from the list and adding it again with a
  catalog already covers most of it.

## Done

What has been built is listed in the [changelog](changelog.md).
