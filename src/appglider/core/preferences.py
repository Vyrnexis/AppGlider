"""Appearance, update-check and saved library settings."""

import json

from PySide6.QtCore import QSettings

SYSTEM_THEME = "System"
THEME_CHOICES = [SYSTEM_THEME, "Light", "Dark"]
# Accent -> (dark value, light value).
ACCENTS: dict[str, tuple[str, str]] = {
    "Blue": ("#8fb3ff", "#3563d8"),
    "Green": ("#6fcf97", "#1f8a4c"),
    "Amber": ("#d9a35b", "#9a6512"),
    "Teal": ("#4fc8c8", "#0f7c80"),
}
DEFAULT_ACCENT = "Blue"
# Retired accents -> the accent they are read as.
RETIRED_ACCENTS = {"Clay": "Amber"}
# Retired themes -> (theme, accent) they are read as.
RETIRED_THEMES = {
    "Forest": ("Dark", "Green"),
    "Earthy": ("Dark", "Amber"),
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
MAX_PINNED = 100


def appearance_choice(settings: QSettings) -> tuple[str, str]:
    """Return the saved (theme, accent), mapping retired themes. Never writes settings."""
    theme = str(settings.value("appearance/theme", SYSTEM_THEME))
    accent = str(settings.value("appearance/accent", ""))
    accent = RETIRED_ACCENTS.get(accent, accent)
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


def pinned_apps(settings: QSettings) -> set[str]:
    """Ids of the apps the user pinned to the top; anything unreadable counts as none."""
    try:
        data = json.loads(str(settings.value("library/pinned", "[]")))
    except ValueError:
        return set()
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        return set()
    return {item for item in data if len(item) <= 256}


def custom_order(settings: QSettings) -> list[str]:
    """Ids in the order the user dragged them into; anything unreadable counts as none."""
    try:
        data = json.loads(str(settings.value("library/order", "[]")))
    except ValueError:
        return []
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        return []
    return list(dict.fromkeys(item for item in data if len(item) <= 256))


def notified_versions(settings: QSettings) -> dict[str, str]:
    """Update versions the user was already told about, keyed by app id."""
    return _versions(settings, "notifications/seen")


def dismissed_versions(settings: QSettings) -> dict[str, str]:
    """Update versions whose banner the user closed, keyed by app id."""
    return _versions(settings, "banners/dismissed")


def _versions(settings: QSettings, key: str) -> dict[str, str]:
    try:
        data = json.loads(str(settings.value(key, "{}")))
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key: value
        for key, value in data.items()
        if isinstance(key, str) and isinstance(value, str) and len(key) <= 256 and len(value) <= 128
    }
