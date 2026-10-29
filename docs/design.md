# AppGlider interface design

This is the design brief for AppGlider's interface: the layout, the visual tokens and the
rules for new screens. Change it deliberately, in its own commit, before changing the
interface it describes.

AppGlider is a hobbyist Linux tool. The goal is an interface that is calm, obvious and
consistent rather than flashy: people open it to launch an app or apply an update and leave.

## Who it is for and what they do

Linux desktop users who run AppImages from GitHub and want them kept current. In order of
frequency, they:

1. Launch an installed app.
2. See whether anything needs updating, and update it.
3. Add a repository and install its app.
4. Change one app's settings (release channel, build, hold, icon), roll back or uninstall.
5. Change AppGlider's own settings.

Every screen should make the most frequent task the most obvious one.

## Layout

One main window. No modal dialogs for browsing or settings; dialogs are kept for
confirmations, file pickers and choices that block an action (such as choosing a build).

```
┌──────────────┬─────────────────────────────────────────────┐
│ ◢ AppGlider  │ Page title   [Search……………] [⋯] [⟳]           │
│              │ ┌ AppGlider banner (new version, accent)  ┐ │
│              │ │ AppGlider 0.9.5 is available [Update]   │ │
│              │ └─────────────────────────────────────────┘ │
│              │ ┌ updates banner (only when updates wait) ┐ │
│ ▣ Library 12 │ │ 2 updates ready.        [Update all]    │ │
│ ↓ Updates  2 │ └─────────────────────────────────────────┘ │
│ + Add app    │ ┌ row: icon · name/sub · version · chip · ┐ │
│              │ │      primary button · ⋯                 │ │
│              │ └─────────────────────────────────────────┘ │
│ ⚙ Settings   │                                             │
│ Checked 21:56│                                             │
│ GitHub 49/60 │                                             │
└──────────────┴─────────────────────────────────────────────┘
```

- **Sidebar** (fixed width 210 px): logo and name, then Library, Updates and Add app, a
  stretch, Settings, and a status block with the last and next check and the GitHub
  allowance. The Updates entry shows an amber count badge while updates wait.
- **Pages** in a stacked area to the right: Library, Updates, Add app, App, Settings. The
  sidebar selection follows the page; the App page keeps Library (or Updates) selected and
  shows a "‹ Library" back link.
- **Library** lists every app as a row. **Updates** is the same list filtered to apps with
  an update, with Update all. Rows can be dragged into the user's own order, the only order
  (apps without a place, such as new ones, follow A–Z; ⋯ in the toolbar offers Sort A–Z after
  a confirmation). Dragging works in Library with All or Installed and no search text, never
  in Updates; hidden apps keep their places. Pinned apps stay on top whatever the order.
- **App page**: header (large icon, name, repository and description, status chips, Launch
  and the primary action), then two columns. Left: What's new (release notes of the
  offered release: the first five lines fading out, with Show all notes / Show less to
  expand them in place, never a scroll area inside the page) and Versions. Right: Preferences (release channel, hold, pin, build, icon,
  menu shortcut, and the Arguments and Environment launch options), then Manage, styled as a
  danger area: Roll back when a copy is kept, then Uninstall while the app is installed or
  Remove from library once it is not, never both, and Forget missing copy when the
  installed file has gone. A row that does not apply is hidden, not disabled; a running
  task disables what is shown. Hold and Menu shortcut show once the app is installed.
- **Settings page**: a secondary list (General, Updates, Catalogs, Storage, About)
  and panels of setting rows. Changes apply immediately; there is no Save or Cancel.
  Storage also has a read-only Space used panel: the total, one bar split into the versions
  in use and the copies kept for roll back, and every installed app largest first with its
  own bar. It has no actions.

The AppGlider banner shows when a new version of AppGlider is out. It sits above the amber
updates banner on Library and Updates, tinted in the accent colour, with a "What's new ↗"
link and one normal-style button (Update AppGlider, Restart now or Releases on GitHub ↗).

Both banners end with a muted **×** (32 px, no border until focused) that closes them until
there is something new: a newer AppGlider, or an update that was not on the banner when it
was closed. A closed updates banner stays on the Updates page, without the ×, so Update all
is always reachable; an explicit check brings the AppGlider banner back.
AppGlider is never a row. Settings → About has a row with the version, a hint with the result
of the last check and a Check now button.

Space bars have no track behind them, so no part reads as free space. The version in use
is solid accent; the kept copy is accent stripes, so the two differ by pattern as well as
shade.

Minimum window size 860 × 560. Nothing may require horizontal scrolling at that size.

## Rows

A row is 60 px tall: a drag handle (six dots in `faint`, `muted` on hover, open-hand
cursor, "Drag to reorder" tooltip; hidden but still taking its space whenever reordering is
off, and the only place a drag starts), icon (40 px, 9 px radius), name in semibold with a gold ★ when pinned
and a one-line muted subtitle (the description, else `owner/repo`), installed version in
tabular figures, one status chip, one primary button (84 px wide) and a ⋯ menu.

Status chips, one per row, in priority order:

| State | Chip text | Colour token | Primary button |
| --- | --- | --- | --- |
| Busy (installing, updating) | Installing… / Updating… with progress | accent | Cancel |
| Update available | ↑ 1.4.0 | warn | Update (primary style) |
| Choose build | Choose build | warn | Choose build |
| Held with a newer release | ‖ Held · 2.3.0 out | held | Launch |
| Held, nothing newer | ‖ Held | held | Launch |
| Installed preview | Preview channel | accent | Launch |
| Up to date | ✓ Up to date | ok | Launch |
| Release check failed | Check failed | danger | Launch |
| File missing | ⚠ File missing | danger | Reinstall |
| Not installed | Not installed | neutral | Install |

When GitHub cannot be reached at all, rows keep their last known chip (Check failed is for
real failures) and the sidebar GitHub status reads **GitHub: offline** instead.

Chips always pair colour with a symbol or word; colour is never the only signal. The ⋯ menu
holds quick actions only: Open details, Launch, Open repository, Pin to top, Move up and
Move down, Hold, Create/repair menu shortcut, Roll back, Uninstall; or, for an app that is
not installed, Remove from library and (when its file has gone) Forget missing copy. It
shows only the actions that fit the app's state: Move up and Move down only while the list
can be reordered, Roll back only when a copy is kept. Everything else is on the App page. Clicking anywhere on a row except its
buttons opens the App page.

## Visual tokens

Defined once in Python (one dictionary per theme) and used by the stylesheet. Never write a
colour, radius or spacing value directly in a widget.

| Token | Dark | Light | Use |
| --- | --- | --- | --- |
| `bg` | `#15181e` | `#eef0f4` | Window background |
| `surface` | `#1c2028` | `#ffffff` | Sidebar, panels, list |
| `surface2` | `#232833` | `#f5f6f9` | Inputs, hovered rows, buttons |
| `raised` | `#2a303c` | `#eceef3` | Selected segment, switch track |
| `line` | `#2f3542` | `#dde0e7` | Borders, row dividers |
| `text` | `#e8eaef` | `#1b1f27` | Primary text |
| `muted` | `#9aa1ae` | `#5d6573` | Secondary text |
| `faint` | `#6c7381` | `#8b92a0` | Hints, subtitles, counts |
| `ok` | `#6fcf97` | `#1f8a4c` | Up to date |
| `warn` | `#f0b860` | `#a8670a` | Updates waiting |
| `held` | `#c7a6ff` | `#7044c4` | Held |
| `danger` | `#ff8a8a` | `#c53030` | Errors, destructive actions |

Chip and banner backgrounds are the state colour at 13–14 % opacity.

**Accent** is chosen separately from the theme: Blue `#8fb3ff` / `#3563d8`, Green `#6fcf97` /
`#1f8a4c`, Amber `#d9a35b` / `#9a6512`, Teal `#4fc8c8` / `#0f7c80` (dark / light). Text on an
accent-filled button uses `bg` in dark mode and white in light mode.

**Themes** are System (follows the desktop's light or dark preference), Light
and Dark. Forest, Earthy and Sandstone are retired; a saved retired theme maps to Dark with
the nearest accent (Forest → Green, Earthy → Amber, Sandstone → Amber; the retired Clay accent → Amber).

**Shape and spacing**: radius 10 px for panels and the list, 8 px for buttons, inputs and
segments, 20 px (pill) for chips. Spacing steps are 4, 8, 12, 16 and 22 px; page padding is
22 px, gaps between panels 16 px.

**Type**: the desktop's UI font. Sizes: page title 20 px bold, app name on the App page
22 px bold, row name 14 px semibold, body 14 px, secondary 13 px, hints and chips 12 px.
Section headings in panels are 13 px semibold, upper case, letter-spaced, in `muted`.

## Controls

- Yes/no settings are switches, aligned right in a setting row.
- Two to four exclusive choices are a segmented control; longer lists are a dropdown.
- A setting row has a bold label, a one-line hint in `faint` and the control on the right.
  Hints are one line. Longer explanations belong in the documentation.
- There is one primary (accent-filled) button per area: the action people most likely want.
- Destructive actions use `danger` text and border, sit in a Manage area at the end of the
  page and always confirm.
- Every interactive control is reachable by keyboard with a visible focus ring (2 px
  accent outline) and has an accessible name.

## Words

Short and plain. Sentence case. Name the thing, then the action ("Update all", "Roll back
to v1.3.1"). Say what happened in the status area rather than in a dialog when nothing needs
deciding. Do not mention GitHub's API; say "GitHub's allowance".

## What must not change

The redesign changes presentation only. Install safety, checksums, rollback, holds, release
checks and their request budget, tray and single-instance behaviour, ownership rules and
stored data stay as described in the [technical notes](technical-notes.md). Settings keep
their QSettings keys unless a migration is written and tested.
