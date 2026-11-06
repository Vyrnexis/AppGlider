"""Every text/background pair the theme produces keeps WCAG AA contrast (4.5:1)."""

import unittest

from appglider.core.preferences import ACCENTS
from appglider.ui.theme import THEMES, accent_roles, blend, contrast

AA = 4.5
NON_TEXT = 3.0


class ThemeContrastTests(unittest.TestCase):
    def check(self, label: str, foreground: str, background: str, minimum: float = AA) -> None:
        ratio = contrast(foreground, background)
        self.assertGreaterEqual(ratio, minimum, f"{label}: {foreground} on {background}")

    def test_token_text_and_chips_pass(self) -> None:
        for theme, tokens in THEMES.items():
            for ground in ("bg", "surface", "surface2"):
                backdrop = tokens[ground]
                for name in ("text", "muted", "faint", "ok", "warn", "danger", "held"):
                    self.check(f"{theme} {name} on {ground}", tokens[name], backdrop)
                for name in ("muted", "ok", "warn", "danger", "held"):
                    self.check(
                        f"{theme} {name} chip on {ground}",
                        tokens[name],
                        blend(tokens[name], backdrop, 0.14),
                    )
            self.check(f"{theme} muted on raised", tokens["muted"], tokens["raised"])

    def test_accents_pass_in_every_theme(self) -> None:
        for theme, tokens in THEMES.items():
            dark = theme == "Dark"
            on_accent = tokens["bg"] if dark else "#ffffff"
            for name, pair in ACCENTS.items():
                accent = pair[0] if dark else pair[1]
                link, fill = accent_roles(tokens, accent, dark)
                label = f"{theme} {name}"
                for ground in ("bg", "surface", "surface2"):
                    backdrop = tokens[ground]
                    self.check(f"{label} link on {ground}", link, backdrop)
                    self.check(f"{label} chip on {ground}", link, blend(link, backdrop, 0.14))
                    self.check(f"{label} focus ring on {ground}", accent, backdrop, NON_TEXT)
                self.check(f"{label} primary button", on_accent, fill)


if __name__ == "__main__":
    unittest.main()
