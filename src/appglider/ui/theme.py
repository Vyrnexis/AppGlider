"""Colour themes: tokens, palette, stylesheet and the painted icons that depend on them."""

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QStandardPaths, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap

from appglider.core.preferences import ACCENTS

# Colour tokens per theme (see docs/design.md). "System" picks Light or Dark from the desktop.
THEMES: dict[str, dict[str, str]] = {
    "Dark": {
        "bg": "#15181e",
        "surface": "#1c2028",
        "surface2": "#232833",
        "raised": "#2a303c",
        "line": "#2f3542",
        "text": "#e8eaef",
        "muted": "#9aa1ae",
        "faint": "#6c7381",
        "ok": "#6fcf97",
        "warn": "#f0b860",
        "held": "#c7a6ff",
        "danger": "#ff8a8a",
    },
    "Light": {
        "bg": "#eef0f4",
        "surface": "#ffffff",
        "surface2": "#f5f6f9",
        "raised": "#eceef3",
        "line": "#dde0e7",
        "text": "#1b1f27",
        "muted": "#5d6573",
        "faint": "#8b92a0",
        "ok": "#1f8a4c",
        "warn": "#a8670a",
        "held": "#7044c4",
        "danger": "#c53030",
    },
}

PIN_COLOUR = "#e6a817"  # gold, readable on both light and dark themes


def glyph_path(name: str, colour: str, points: list[QPointF]) -> str:
    """Return a cached stroked-line glyph in the given colour, for stylesheet images."""
    folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation))
    path = folder / f"{name}-{colour.lstrip('#')}.png"
    if not path.exists():
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(colour), 2.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline(points)
        painter.end()
        folder.mkdir(parents=True, exist_ok=True)
        pixmap.save(str(path))
    return path.as_posix()


def css_path(path: str) -> str:
    """Escape a file path for a quoted stylesheet url(); an unquoted odd path voids the sheet."""
    return path.replace("\\", "\\\\").replace('"', '\\"')


def chevron_path(colour: str) -> str:
    return glyph_path("chevron", colour, [QPointF(7, 9.5), QPointF(12, 14.5), QPointF(17, 9.5)])


def refresh_icon(colour: str) -> QIcon:
    """Draw a circular arrow in the given colour."""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(colour), 6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.drawArc(QRectF(14, 14, 36, 36), 50 * 16, 270 * 16)
    tip = QPointF(47.6, 23)
    head = QPainterPath()
    head.moveTo(tip + QPointF(-1, -14))
    head.lineTo(tip + QPointF(-1, 8))
    head.lineTo(tip + QPointF(-18, -2))
    head.closeSubpath()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.fillPath(head, QColor(colour))
    painter.end()
    return QIcon(pixmap)


NAV_ICON_SIZE = 18


def nav_icon(kind: str, normal: str, active: str, disabled: str) -> QIcon:
    """Draw a sidebar glyph (grid, download, plus or cog) in three colours; no image assets."""
    icon = QIcon()
    scale = 3
    side = NAV_ICON_SIZE * scale
    for colour, mode, state in (
        (normal, QIcon.Mode.Normal, QIcon.State.Off),
        (active, QIcon.Mode.Normal, QIcon.State.On),
        (active, QIcon.Mode.Active, QIcon.State.Off),
        (active, QIcon.Mode.Active, QIcon.State.On),
        (disabled, QIcon.Mode.Disabled, QIcon.State.Off),
        (disabled, QIcon.Mode.Disabled, QIcon.State.On),
    ):
        pixmap = QPixmap(side, side)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(scale, scale)
        pen = QPen(QColor(colour), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if kind == "library":
            for x in (2.5, 10.5):
                for y in (2.5, 10.5):
                    painter.drawRoundedRect(QRectF(x, y, 5, 5), 1.2, 1.2)
        elif kind == "updates":
            painter.drawPolyline([QPointF(9, 2.5), QPointF(9, 11.5)])
            painter.drawPolyline([QPointF(5, 7.8), QPointF(9, 11.8), QPointF(13, 7.8)])
            painter.drawLine(QPointF(3.5, 15), QPointF(14.5, 15))
        elif kind == "add":
            painter.drawLine(QPointF(9, 3), QPointF(9, 15))
            painter.drawLine(QPointF(3, 9), QPointF(15, 9))
        else:
            painter.drawEllipse(QPointF(9, 9), 2.4, 2.4)
            painter.drawEllipse(QPointF(9, 9), 5.2, 5.2)
            for step in range(8):
                painter.save()
                painter.translate(9, 9)
                painter.rotate(step * 45)
                painter.drawLine(QPointF(0, -5.2), QPointF(0, -7.6))
                painter.restore()
        painter.end()
        icon.addPixmap(pixmap, mode, state)
    return icon


def tint(colour: str, alpha: float) -> str:
    """Return a stylesheet rgba() value for a hex token at the given opacity."""
    value = QColor(colour)
    return f"rgba({value.red()}, {value.green()}, {value.blue()}, {round(alpha * 255)})"


def palette(tokens: dict[str, str], accent: str, dark: bool) -> QPalette:
    """Build the application palette from a theme's tokens and the accent colour."""
    background, surface = tokens["bg"], tokens["surface"]
    foreground, muted = tokens["text"], tokens["muted"]
    on_accent = background if dark else "#ffffff"
    result = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, background),
        (QPalette.ColorRole.WindowText, foreground),
        (QPalette.ColorRole.Base, surface),
        (QPalette.ColorRole.AlternateBase, background),
        (QPalette.ColorRole.Text, foreground),
        (QPalette.ColorRole.Button, surface),
        (QPalette.ColorRole.ButtonText, foreground),
        (QPalette.ColorRole.ToolTipBase, surface),
        (QPalette.ColorRole.ToolTipText, foreground),
        (QPalette.ColorRole.Highlight, accent),
        (QPalette.ColorRole.HighlightedText, on_accent),
        (QPalette.ColorRole.PlaceholderText, muted),
        (QPalette.ColorRole.Link, accent),
        (QPalette.ColorRole.LinkVisited, accent),
    ):
        result.setColor(role, QColor(color))
    result.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(muted))
    return result


def stylesheet(tokens: dict[str, str], accent: str, dark: bool) -> str:
    """Build the application stylesheet from a theme's tokens and the accent colour."""
    background, surface = tokens["bg"], tokens["surface"]
    foreground, muted, border = tokens["text"], tokens["muted"], tokens["line"]
    ok, warn, danger = tokens["ok"], tokens["warn"], tokens["danger"]
    raised, faint = tokens["raised"], tokens["faint"]
    surface2 = tokens["surface2"]
    held = tokens["held"]
    swatches = "\n".join(
        f'QPushButton#swatch[accent="{name}"] {{ background: {pair[0] if dark else pair[1]}; }}'
        for name, pair in ACCENTS.items()
    )
    on_accent = background if dark else "#ffffff"
    on_warn = on_accent
    return f"""
            QWidget {{ font-size: 14px; }}
            QMainWindow, QDialog {{ background: {background}; }}
            QLabel {{ background: transparent; }}
            QLabel#pageTitle {{ font-size: 20px; font-weight: 700; }}
            QLabel#welcomeTitle {{ font-size: 27px; font-weight: 650; }}
            QLabel#sectionTitle {{ font-size: 18px; font-weight: 650; }}
            QLabel#spaceTotal {{ font-size: 20px; font-weight: 700; }}
            QPushButton#compact {{ padding: 6px 12px; }}
            QToolButton#checkNow, QToolButton#libraryOptions {{ padding: 0;
                min-width: 36px; max-width: 36px; min-height: 36px; max-height: 36px; }}
            QToolButton#libraryOptions {{ font-size: 16px; }}
            QFrame#sidebar {{ background: {surface}; border: none;
                border-right: 1px solid {border}; }}
            QLabel#brand {{ font-size: 16px; font-weight: 700; }}
            QPushButton#nav {{ background: transparent; border: 2px solid transparent;
                border-radius: 8px; color: {muted}; text-align: left;
                padding: 0 10px; font-weight: 500; }}
            QPushButton#nav:hover {{ color: {foreground}; border-color: transparent; }}
            QPushButton#nav:checked {{ background: {tint(accent, 0.14)}; color: {foreground}; }}
            QPushButton#nav:focus {{ border: 2px solid {accent}; }}
            QPushButton#nav:disabled {{ background: transparent; color: {faint}; }}
            QLabel#navCount {{ color: {faint}; font-size: 12px; }}
            QLabel#navBadge {{ background: {warn}; color: {on_warn}; border-radius: 9px;
                padding: 0 6px; font-size: 11px; font-weight: 700; }}
            QPushButton#statusBlock {{ background: transparent; border: 1px solid transparent;
                border-radius: 8px; padding: 0; text-align: left; }}
            QPushButton#statusBlock:hover {{ border-color: {accent}; }}
            QPushButton#statusBlock:focus {{ border: 2px solid {accent}; }}
            QLabel#sideStatus {{ color: {muted}; font-size: 12px; }}
            QPushButton#statusBlock[limited="true"] QLabel#sideStatus {{ color: {warn}; }}
            QProgressBar#meter {{ background: {border}; border: none; border-radius: 2px; }}
            QProgressBar#meter::chunk {{ background: {accent}; border-radius: 2px; }}
            QPushButton#versionButton {{ background: transparent; border: 1px solid transparent;
                color: {faint}; font-size: 12px; padding: 3px 8px; }}
            QPushButton#versionButton:hover {{ border-color: {accent}; color: {accent}; }}
            QPushButton#versionButton:focus {{ border: 2px solid {accent}; color: {accent}; }}
            QPushButton#segment {{ background: {surface}; color: {muted};
                border-radius: 0; padding: 8px 12px; }}
            QPushButton#segment[segment="first"] {{ border-top-left-radius: 7px;
                border-bottom-left-radius: 7px; }}
            QPushButton#segment[segment="last"] {{ border-top-right-radius: 7px;
                border-bottom-right-radius: 7px; border-left: none; }}
            QPushButton#segment:checked {{ background: {raised}; color: {foreground}; }}
            QFrame#updatesBanner {{ background: {tint(warn, 0.14)};
                border: 1px solid {tint(warn, 0.45)}; border-radius: 10px; }}
            QFrame#updatesBanner QLabel {{ color: {foreground}; }}
            QFrame#updatesBanner QPushButton {{ padding: 7px 14px; }}
            QFrame#selfUpdateBanner {{ background: {tint(accent, 0.13)};
                border: 1px solid {tint(accent, 0.45)}; border-radius: 10px; }}
            QFrame#selfUpdateBanner QLabel {{ color: {foreground}; }}
            QFrame#selfUpdateBanner QPushButton {{ padding: 7px 14px; }}
            QFrame#selfUpdateBanner QPushButton#linkButton {{ padding: 7px 4px; }}
            QProgressBar {{ background: {background}; border: none; border-radius: 3px; }}
            QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}
            QPushButton#backLink {{ background: transparent; border: none; color: {accent};
                padding: 4px 2px; font-weight: 600; }}
            QPushButton#backLink:hover {{ text-decoration: underline; }}
            QPushButton#backLink:focus {{ border: 2px solid {accent}; border-radius: 6px; }}
            QPushButton#backLink:disabled {{ color: {muted}; }}
            QLabel#muted, QLabel#footer {{ color: {muted}; }}
            QLabel#footer {{ font-size: 12px; padding-top: 8px; }}
            QLabel#emptyState {{ font-size: 17px; color: {muted}; }}
            QLabel#versionCaption {{ color: {muted}; font-size: 12px; }}
            QFrame#appList {{ background: {surface}; border: 1px solid {border};
                border-radius: 10px; }}
            QFrame#appRow {{ background: transparent; border: none; }}
            QFrame#appRow:hover {{ background: {surface2}; }}
            QFrame#appRow[edge="first"]:hover, QFrame#appRow[edge="only"]:hover {{
                border-top-left-radius: 9px; border-top-right-radius: 9px; }}
            QFrame#appRow[edge="last"]:hover, QFrame#appRow[edge="only"]:hover {{
                border-bottom-left-radius: 9px; border-bottom-right-radius: 9px; }}
            QFrame#rowDivider {{ border: none; background: {border}; }}
            QFrame#dropLine {{ border: none; background: {accent}; border-radius: 1px; }}
            QLabel#rowSubtitle {{ color: {muted}; font-size: 13px; }}
            QLabel#rowVersion {{ color: {muted}; font-size: 13px; }}
            QLabel#chip {{ color: {muted}; background: {tint(muted, 0.14)};
                border-radius: 11px; padding: 0 10px; font-size: 12px; font-weight: 600; }}
            QLabel#chip[state="busy"], QLabel#chip[state="preview"] {{
                color: {accent}; background: {tint(accent, 0.14)}; }}
            QLabel#chip[state="update"], QLabel#chip[state="build"] {{
                color: {warn}; background: {tint(warn, 0.14)}; }}
            QLabel#chip[state="held"] {{ color: {held}; background: {tint(held, 0.14)}; }}
            QLabel#chip[state="ok"] {{ color: {ok}; background: {tint(ok, 0.14)}; }}
            QLabel#chip[state="failed"], QLabel#chip[state="missing"] {{
                color: {danger}; background: {tint(danger, 0.14)}; }}
            QPushButton[rowAction="true"] {{ padding: 0; font-size: 13px; }}
            QPushButton, QToolButton {{ background: {surface}; color: {foreground};
                border: 1px solid {border}; border-radius: 7px; padding: 9px 14px; }}
            QPushButton:hover, QToolButton:hover {{ border-color: {accent}; color: {accent}; }}
            QPushButton:focus, QToolButton:focus, QLineEdit:focus, QComboBox:focus {{
                border: 2px solid {accent}; }}
            QPushButton:disabled {{ color: {muted}; background: {background}; }}
            QPushButton#primary {{ background: {accent}; color: {on_accent};
                border-color: {accent}; font-weight: 600; }}
            QPushButton#primary:hover {{ border-color: {foreground}; }}
            QPushButton#primary:disabled {{ background: {surface}; color: {muted};
                border-color: {border}; }}
            QPushButton#danger {{ color: {danger}; border-color: {tint(danger, 0.45)}; }}
            QPushButton#danger:hover {{ color: {danger}; border-color: {danger}; }}
            QFrame#panel {{ background: {surface}; border: 1px solid {border};
                border-radius: 10px; }}
            QFrame#dangerPanel {{ background: {tint(danger, 0.06)};
                border: 1px solid {tint(danger, 0.35)}; border-radius: 10px; }}
            QLabel#panelTitle {{ color: {muted}; font-size: 13px; font-weight: 600; }}
            QLabel#dangerTitle {{ color: {danger}; font-size: 13px; font-weight: 600; }}
            QLabel#appTitle {{ font-size: 22px; font-weight: 700; }}
            QLabel#settingLabel {{ font-weight: 600; }}
            QLabel#settingHint {{ color: {faint}; font-size: 12px; }}
            QLabel#captionLabel {{ color: {muted}; font-size: 13px; }}
            QLabel#versionValue {{ color: {foreground}; font-size: 13px; }}
            QLabel#releaseTitle {{ font-weight: 600; }}
            QPushButton#linkButton {{ background: transparent; border: 2px solid transparent;
                color: {accent}; padding: 2px 2px; text-align: left; }}
            QPushButton#linkButton:hover {{ text-decoration: underline;
                border-color: transparent; }}
            QPushButton#linkButton:focus {{ border: 2px solid {accent}; }}
            QPushButton#linkButton:disabled {{ background: transparent; color: {faint}; }}
            QTextBrowser#notes {{ background: transparent; border: none; color: {foreground}; }}
            QFrame#segmented {{ background: {surface2}; border: 1px solid {border};
                border-radius: 9px; }}
            QPushButton#segmentedButton {{ background: transparent; color: {muted};
                border: 2px solid transparent; border-radius: 7px; padding: 4px 8px; }}
            QPushButton#segmentedButton:hover {{ color: {foreground}; border-color: transparent; }}
            QPushButton#segmentedButton:checked {{ background: {raised}; color: {foreground}; }}
            QPushButton#segmentedButton:focus {{ border: 2px solid {accent}; }}
            QPushButton#segmentedButton:disabled {{ background: transparent; color: {faint}; }}
            QPushButton#segmentedButton:checked:disabled {{ background: {raised}; }}
            QAbstractButton#switch {{ qproperty-trackOn: {accent}; qproperty-trackOff: {raised};
                qproperty-knob: {on_accent}; qproperty-ring: {accent}; }}
            SpaceBar, SpaceSwatch, QWidget#spaceBar, QWidget#spaceSwatch {{
                qproperty-accent: {accent}; }}
            QLabel#pinStar {{ color: {PIN_COLOUR}; font-size: 15px; }}
            QPushButton#appName {{ border: none; padding: 0; text-align: left;
                font-size: 14px; font-weight: 600; background: transparent; }}
            QPushButton#appName:focus {{ border: 2px solid {accent}; border-radius: 6px; }}
            QToolButton#rowMenu {{ border: 2px solid transparent; background: transparent;
                font-size: 20px; padding: 0; }}
            QToolButton#rowMenu:focus {{ border: 2px solid {accent}; }}
            QToolButton#bannerClose {{ border: 2px solid transparent; background: transparent;
                color: {muted}; font-size: 20px; padding: 0; }}
            QToolButton#bannerClose:hover {{ color: {foreground}; }}
            QToolButton#bannerClose:focus {{ border: 2px solid {accent}; }}
            QToolButton::menu-indicator {{ image: none; }}
            QLabel#status {{ color: {muted}; font-size: 12px; }}
            QLabel#releaseBadge {{ border: 1px solid; border-radius: 9px;
                padding: 2px 7px; font-size: 11px; font-weight: 600; }}
            QLabel#releaseBadge[release="latest"] {{ color: {ok};
                border-color: {ok}; }}
            QLabel#releaseBadge[release="prerelease"] {{ color: {warn};
                border-color: {warn}; }}
            QPushButton#sectionNav {{ background: transparent; border: 2px solid transparent;
                border-radius: 8px; color: {muted}; text-align: left; padding: 8px 12px; }}
            QPushButton#sectionNav:hover {{ color: {foreground}; border-color: transparent; }}
            QPushButton#sectionNav:checked {{ background: {surface2}; color: {foreground};
                font-weight: 600; }}
            QPushButton#sectionNav:focus {{ border: 2px solid {accent}; }}
            QPushButton#swatch {{ border: 2px solid transparent; border-radius: 14px;
                padding: 0; }}
            QPushButton#swatch:hover, QPushButton#swatch:checked, QPushButton#swatch:focus {{
                border: 2px solid {foreground}; }}
            {swatches}
            QLabel#savedNote {{ color: {ok}; font-size: 13px; }}
            QLabel#settingError {{ color: {danger}; font-size: 12px; }}
            QLineEdit, QComboBox {{ background: {surface}; color: {foreground};
                border: 1px solid {border}; border-radius: 7px; padding: 9px; }}
            QComboBox {{ min-height: 20px; }}
            QComboBox::drop-down {{ border: none; width: 28px; }}
            QComboBox::down-arrow {{ image: url("{css_path(chevron_path(foreground))}");
                width: 20px; height: 20px; }}
            QComboBox QAbstractItemView {{ background: {surface}; color: {foreground};
                border: 1px solid {border}; padding: 4px; outline: 0;
                selection-background-color: {accent}; selection-color: {on_accent}; }}
            QComboBox QAbstractItemView::item {{ min-height: 32px; padding: 4px 10px; }}
            QComboBox QAbstractItemView::item:selected {{ background: {accent};
                color: {on_accent}; }}
            QAbstractItemView QLineEdit, QAbstractItemView QLineEdit:focus {{
                padding: 0; border: 1px solid {accent}; border-radius: 2px; }}
            QScrollArea, QScrollArea > QWidget > QWidget {{ background: {background}; }}
            QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
            QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: {faint}; border-radius: 4px;
                min-height: 32px; margin: 2px; }}
            QScrollBar::handle:horizontal {{ background: {faint}; border-radius: 4px;
                min-width: 32px; margin: 2px; }}
            QScrollBar::handle:hover, QScrollBar::handle:pressed {{ background: {muted}; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0;
                background: none; border: none; }}
            QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
            QScrollBar::up-arrow, QScrollBar::down-arrow, QScrollBar::left-arrow,
            QScrollBar::right-arrow {{ background: none; image: none; }}
            QMenu {{ background: {surface}; color: {foreground};
                border: 1px solid {border}; padding: 6px; }}
            QMenu::item {{ padding: 8px 16px; }}
            QMenu::item:selected {{ background: {background}; color: {accent}; }}
        """
