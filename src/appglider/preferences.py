"""Appearance and update-check choices shared by the window and the Settings page."""

from PySide6.QtCore import QSettings

SYSTEM_THEME = "System"
THEME_CHOICES = [SYSTEM_THEME, "Light", "Dark"]
# Accent -> (dark value, light value).
ACCENTS: dict[str, tuple[str, str]] = {
    "Blue": ("#8fb3ff", "#3563d8"),
    "Green": ("#6fcf97", "#1f8a4c"),
    "Amber": ("#d9a35b", "#9a6512"),
    "Clay": ("#c58b6a", "#9c5434"),
}
DEFAULT_ACCENT = "Blue"
# Retired themes -> (theme, accent) they are read as.
RETIRED_THEMES = {
    "Forest": ("Dark", "Green"),
    "Earthy": ("Dark", "Clay"),
    "Sandstone": ("Dark", "Amber"),
}
CHECK_INTERVALS = (
    ("Every hour", 1),
    ("Every 3 hours", 3),
    ("Every 6 hours", 6),
    ("Every 12 hours", 12),
    ("Every 24 hours", 24),
    ("Only when I choose", 0),
)
DEFAULT_CHECK_HOURS = 6


def appearance_choice(settings: QSettings) -> tuple[str, str]:
    """Return the saved (theme, accent), mapping retired themes. Never writes settings."""
    theme = str(settings.value("appearance/theme", SYSTEM_THEME))
    accent = str(settings.value("appearance/accent", ""))
    if theme in RETIRED_THEMES:
        theme, retired_accent = RETIRED_THEMES[theme]
        accent = accent if accent in ACCENTS else retired_accent
    if theme not in THEME_CHOICES:
        theme = SYSTEM_THEME
    return theme, accent if accent in ACCENTS else DEFAULT_ACCENT


def check_interval_hours(settings: QSettings) -> int:
    """Hours between scheduled update checks; 0 means only when asked."""
    try:
        hours = int(str(settings.value("General/check-interval-hours", DEFAULT_CHECK_HOURS)))
    except ValueError:
        return DEFAULT_CHECK_HOURS
    return hours if hours in {value for _, value in CHECK_INTERVALS} else DEFAULT_CHECK_HOURS
