---
name: appglider-design
description: AppGlider's interface rules (layout, tokens, rows, controls, wording). Use before designing, mocking up, reviewing or changing any AppGlider window, page, widget, stylesheet or icon.
---

1. Read `docs/design.md` in full before changing or reviewing anything visible. It is the
   source of truth; do not restate or override it here.
2. Use the tokens from the theme dictionary; never hard-code colours, radii or spacing in a
   widget.
3. Check every new or changed screen against: one primary action per area, a status chip
   with a symbol as well as a colour, setting rows with one-line hints, destructive actions
   in a Manage area with confirmation, keyboard reachability and accessible names.
4. Presentation only: do not change install, release-check, rollback, hold, tray or
   storage behaviour, or QSettings keys, unless the task explicitly says so. `AGENTS.md`
   still applies.
5. If the brief and the request disagree, follow the request and say which rule in
   `docs/design.md` should change.
