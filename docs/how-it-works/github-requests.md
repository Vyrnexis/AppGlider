# GitHub requests

How many requests AppGlider makes to GitHub, and how it keeps them within GitHub's free allowance. You do not need a GitHub account or token. Release checks themselves are described in [Release checks](release-checks.md).

## The request allowance

AppGlider needs no GitHub account or token. It uses GitHub's public API, which allows
60 requests per hour per network address without signing in.

- A full API check takes two metadata requests per repository (the latest stable release
  and a listing of recent releases). Checksums can need more. Cheap checks (above) avoid
  these for apps that have not changed.
- The listing asks for the newest 20 releases. A repository that attaches many files to
  every release (VSCodium has 180) makes that answer larger than AppGlider reads (4 MB), so
  it asks again for 5, then 1. The size that fitted is remembered until AppGlider closes, so
  later checks of that repository cost the usual two requests.
- Answers are stored with their ETag, so repeat requests are conditional and an unchanged
  answer (HTTP 304) is reused. For anonymous requests a 304 still counts against the
  allowance, so a check costs about one request per installed app whether or not anything
  changed.
- A scheduled check is skipped while GitHub last reported fewer than 30 requests left,
  while a download or another check is running, and when nothing is installed.
- When the allowance runs out, AppGlider falls back to GitHub's public release pages for
  up to three pages of ten releases, applying the same selection rules.
- The **GitHub** line in the sidebar's status block shows the last reported allowance. Its
  tooltip adds the reset time, active retry countdowns and the most recent successful result
  with its repository, source (GitHub API, public pages or cached) and time; clicking the
  block opens **Settings → Updates**. None of this makes network requests. (Versions up to
  0.9.4 also showed these details in a Settings → GitHub section.) Counts come from earlier GitHub responses and
  can also be affected by other programs on your network. After a request window expires the
  count becomes unknown until the next response; AppGlider does not assume the full
  allowance is available. Retry times survive restarts.

## Icon search

- While adding a repository, AppGlider looks for likely icon files in it and previews the
  candidates. You can also choose a local PNG or a self-contained SVG. Selected icons are
  saved locally and keep working offline.
- Images are limited to 2 MiB and 4096 pixels per side and are converted to a PNG of at
  most 512 pixels per side. SVGs that reference external resources, or declare anything
  beyond a standard `<!DOCTYPE svg …>` line, are rejected; that standard line is accepted.
- Only a bounded set of likely repository files is examined, so some projects need a manual
  choice. A missing icon or a quota error never stops a valid repository from being added.
  Icons inside the AppImage itself are not extracted.
- Icon searches run only when you ask, never in the background and never for many apps at
  once. The images themselves are always read from `raw.githubusercontent.com`, which does
  not use the API allowance. To find *which* files to read, AppGlider uses one of two ways:
  with plenty of allowance left (30 or more requests) it asks the API for the repository's
  file list, which is **one API request** and takes about a second. When the allowance is low
  or unknown, the API is rate limited, or the repository is too big for that listing, it
  **browses the repository's web pages** instead (`github.com/<owner>/<repo>/tree/HEAD/<folder>`,
  which returns a folder's contents as JSON), using **no API requests**.
- The web walk opens every top-level and second-level folder, then deeper folders only when
  their path looks like artwork (whole words such as `icons`, `brand`, `assets`, `hicolor`,
  `packaging`), most promising first. It skips dependency, test, documentation and
  platform-specific folders, and looks at only the two largest sizes inside `hicolor`-style
  folders. It stops early: once a strong candidate (a file named after the project, or one in
  a `hicolor` folder) has been seen on page N, it opens at least six more pages (or N more,
  if that is larger) and then finishes. Otherwise it stops after 40 pages or 60 seconds.
  Typically that is 5 to 10 seconds; a project whose icons sit deep in its source tree can
  take up to about 20 seconds.
  GitHub's folder JSON is an undocumented part of its website, so if it ever changes the walk
  simply finds nothing and you can still choose an image yourself.
- A PNG or SVG is a candidate if its path contains `icon`, `logo` or `brand/`, if it is the
  icon named in the project's `.desktop` file, or if its file name contains the project's name
  (for example `OrcaSlicer_192px.png`; names under four characters do not count). Files whose
  names carry a variant word (`mac`, `win`, `about`, `title`, `gradient`, `grayscale`,
  `splash`, `badge`, `banner`, `screenshot`...) are skipped.
- Candidates are ranked: the icon named in the project's `.desktop` file first (including
  names like `com.example.app`), then files named after the project, then PNGs under
  `linux` or `hicolor`, then larger sizes before smaller (read from folders like `512x512`
  or names like `icon_192px`). Candidates that look like a preview or nightly build (a path word such as `insiders`, `nightly`,
  `beta` or `preview`) come after the real icon, and so does artwork that is far from square
  (page corners, wide logos). Up to four are offered.
- Repositories that were renamed or moved are followed: redirects are accepted only to
  GitHub's own hosts, and the new name is used for the remaining pages. A file list too
  large for the API (some big projects) simply uses the web walk instead.
- The apps in the ArtCraft example catalog use icons bundled with AppGlider.
