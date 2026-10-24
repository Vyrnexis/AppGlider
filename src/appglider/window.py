"""Library rows, installed applications and background installation controls."""

import html
import json
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import Event

from PySide6.QtCore import (
    QByteArray,
    QEvent,
    QMimeData,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSettings,
    QSize,
    QStandardPaths,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QDesktopServices,
    QDrag,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QIcon,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QPen,
    QPixmap,
    QResizeEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from appglider import (
    APP_ICON,
    APP_NAME,
    APP_TASKBAR_ICON,
    APP_UPDATE_ICON,
    APP_VERSION,
    github_status,
)
from appglider.add_page import AddPage
from appglider.app_page import AppPage
from appglider.app_status import app_status
from appglider.catalog import App
from appglider.controls import repolish
from appglider.icons import icon_pixmap
from appglider.jobs import Task, TaskResult
from appglider.library import Installed, Library, Orphan
from appglider.preferences import (
    ACCENTS,
    DEFAULT_ACCENT,
    appearance_choice,
    check_interval_hours,
)
from appglider.release_checks import ReleaseChecks
from appglider.release_info import (
    ReleaseInfo,
    cached_info,
    discover_releases,
    info_path,
    remember_release,
)
from appglider.releases import (
    BuildChoiceRequired,
    Progress,
    Release,
    cached_release,
    check_cancel,
    file_stamp,
    github_request_status,
    latest_release,
    release_cache_path,
    restore_rate_limits,
    version_key,
)
from appglider.settings_page import SettingsPage

SIDEBAR_WIDTH = 210
LEGACY_SECTIONS = {0: "General", 1: "Catalogs", 2: "Storage", 3: "GitHub", 4: "About"}
CHECK_TOOLTIP = "Check for updates"


def icon_label(app: App, size: int, path: Path | None = None) -> QLabel:
    label = QLabel()
    label.setFixedSize(size, size)
    label.setPixmap(icon_pixmap(path or app.icon, size))
    label.setAccessibleName(f"{app.name} icon")
    return label


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
MAX_PINNED = 100


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
    try:
        data = json.loads(str(settings.value("notifications/seen", "{}")))
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key: value
        for key, value in data.items()
        if isinstance(key, str) and isinstance(value, str) and len(key) <= 256 and len(value) <= 128
    }


CHIP_STATES = ("busy", "update", "build", "held", "preview", "ok", "failed", "none")
ROW_HEIGHT = 60
ORDER_MIME = "application/x-appglider-app"
DRAG_SCROLL_MARGIN = 28


class ElidedLabel(QLabel):
    """One line of plain text that is cut with an ellipsis instead of widening its row."""

    def __init__(self) -> None:
        super().__init__()
        self.full_text = ""
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_full_text(self, text: str) -> None:
        self.full_text = text
        self.setToolTip(text)
        self.elide()

    def elide(self) -> None:
        shown = self.fontMetrics().elidedText(
            self.full_text, Qt.TextElideMode.ElideRight, max(0, self.width())
        )
        if shown != self.text():
            self.setText(shown)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.elide()


class StatusMessage(QLabel):
    """The footer message: a transient note that expires and tells the window when it changes."""

    changed = Signal()
    expired = Signal()
    LIFETIME_MS = 30_000

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("footer")
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.setAccessibleName("Status message")
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.expired)

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        if text:
            self.timer.start(self.LIFETIME_MS)
        else:
            self.timer.stop()
        self.changed.emit()


class DragHandle(QWidget):
    """Six dots at the start of a row that show it can be dragged, and start the drag."""

    colours = ("#6c7381", "#9aa1ae")  # idle and hover; apply_theme sets them from the tokens

    def __init__(self, row: "AppRow") -> None:
        super().__init__()
        self.row = row
        self.press_pos: QPoint | None = None
        self.setObjectName("dragHandle")
        self.setFixedSize(14, 28)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag to reorder")
        policy = self.sizePolicy()
        policy.setRetainSizeWhenHidden(True)  # rows keep their alignment while reordering is off
        self.setSizePolicy(policy)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        hot = self.underMouse() or self.press_pos is not None
        painter.setBrush(QColor(self.colours[1] if hot else self.colours[0]))
        for x in (4.0, 10.0):
            for y in (8.0, 14.0, 20.0):
                painter.drawEllipse(QPointF(x, y), 1.6, 1.6)

    def event(self, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.HoverEnter, QEvent.Type.HoverLeave):
            self.update()
        return super().event(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.press_pos = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            self.press_pos is not None
            and self.row.draggable
            and event.buttons() & Qt.MouseButton.LeftButton
            and (event.position().toPoint() - self.press_pos).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            hot_spot = self.mapTo(self.row, self.press_pos)
            self.press_pos = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.row.start_drag(hot_spot)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        # Releasing on the handle never opens the app page.
        self.press_pos = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        event.accept()


class AppRow(QFrame):
    details_requested = Signal(str)
    launch_requested = Signal(str)
    install_requested = Signal(str)
    shortcut_requested = Signal(str)
    rollback_requested = Signal(str)
    hold_requested = Signal(str)
    remove_requested = Signal(str)
    pin_requested = Signal(str)
    build_requested = Signal(str)
    cancel_requested = Signal(str)
    move_requested = Signal(str, int)

    def __init__(self, app: App, icon_path: Path | None = None) -> None:
        super().__init__()
        self.app = app
        self.installed: Installed | None = None
        self.build_required = False
        self.pinned = False
        self.mode = "install"
        self.press_pos: QPoint | None = None
        self.setObjectName("appRow")
        self.setProperty("edge", "mid")
        self.setFixedHeight(ROW_HEIGHT)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        content = QHBoxLayout()
        content.setContentsMargins(6, 0, 10, 0)
        content.setSpacing(12)
        outer.addLayout(content, stretch=1)
        self.handle = DragHandle(self)
        self.handle.hide()
        content.addWidget(self.handle)
        content.addSpacing(-6)
        self.divider = QFrame()
        self.divider.setObjectName("rowDivider")
        self.divider.setFixedHeight(1)
        outer.addWidget(self.divider)
        self.icon = icon_label(app, 40, icon_path)
        content.addWidget(self.icon)
        identity = QVBoxLayout()
        identity.setSpacing(1)
        identity.addStretch()
        name = QPushButton(app.name)
        self.name_button = name
        name.setObjectName("appName")
        name.setAccessibleName(f"View {app.name} details")
        name.setToolTip(app.name)
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        name.setCursor(Qt.CursorShape.PointingHandCursor)
        name.clicked.connect(lambda: self.details_requested.emit(app.id))
        name_row = QHBoxLayout()
        name_row.setSpacing(4)
        self.pin_star = QLabel("★")
        self.pin_star.setObjectName("pinStar")
        self.pin_star.setToolTip("Pinned to the top")
        self.pin_star.setAccessibleName("Pinned to the top")
        self.pin_star.hide()
        name_row.addWidget(self.pin_star)
        name_row.addWidget(name, stretch=1)
        identity.addLayout(name_row)
        self.subtitle = ElidedLabel()
        self.subtitle.setObjectName("rowSubtitle")
        self.subtitle.set_full_text(app.description or app.repo)
        identity.addWidget(self.subtitle)
        identity.addStretch()
        content.addLayout(identity, stretch=1)
        self.installed_version = QLabel("—")
        self.installed_version.setObjectName("rowVersion")
        self.installed_version.setFixedWidth(88)
        font = self.installed_version.font()
        font.setFeature(QFont.Tag("tnum"), 1)
        self.installed_version.setFont(font)
        content.addWidget(self.installed_version)
        chip_slot = QWidget()
        chip_slot.setFixedWidth(140)
        chip_layout = QHBoxLayout(chip_slot)
        chip_layout.setContentsMargins(0, 0, 0, 0)
        self.chip = QLabel()
        self.chip.setObjectName("chip")
        self.chip.setProperty("state", "none")
        self.chip.setFixedHeight(22)
        self.chip.setMaximumWidth(140)
        chip_layout.addWidget(self.chip)
        chip_layout.addStretch()
        content.addWidget(chip_slot)
        self.action_button = QPushButton()
        self.action_button.setProperty("rowAction", True)
        self.action_button.setFixedSize(84, 32)
        self.action_button.clicked.connect(self.activate)
        content.addWidget(self.action_button)
        more = QToolButton()
        self.more_button = more
        more.setText("⋯")
        more.setObjectName("rowMenu")
        more.setFixedSize(32, 32)
        more.setAccessibleName(f"More actions for {app.name}")
        more.setToolTip(f"More actions for {app.name}")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        menu.addAction("Open details").triggered.connect(
            lambda: self.details_requested.emit(app.id)
        )
        self.launch_action = menu.addAction("Launch")
        self.launch_action.triggered.connect(lambda: self.launch_requested.emit(app.id))
        menu.addAction("Open repository").triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl(app.source_url))
        )
        self.pin_action = menu.addAction("Pin to top")
        self.pin_action.triggered.connect(lambda: self.pin_requested.emit(app.id))
        self.move_up_action = menu.addAction("Move up")
        self.move_up_action.triggered.connect(lambda: self.move_requested.emit(app.id, -1))
        self.move_down_action = menu.addAction("Move down")
        self.move_down_action.triggered.connect(lambda: self.move_requested.emit(app.id, 1))
        self.move_up_action.setEnabled(False)
        self.move_down_action.setEnabled(False)
        self.hold_action = menu.addAction("Hold at current version")
        self.hold_action.triggered.connect(lambda: self.hold_requested.emit(app.id))
        menu.addSeparator()
        self.shortcut_action = menu.addAction("Create/repair menu shortcut")
        self.shortcut_action.triggered.connect(lambda: self.shortcut_requested.emit(app.id))
        self.rollback_action = menu.addAction("Roll back to previous version")
        self.rollback_action.triggered.connect(lambda: self.rollback_requested.emit(app.id))
        self.remove_action = menu.addAction("Uninstall…")
        self.remove_action.triggered.connect(lambda: self.remove_requested.emit(app.id))
        more.setMenu(menu)
        content.addWidget(more)
        self.set_state(None, None, False)

    def activate(self) -> None:
        signal = {
            "launch": self.launch_requested,
            "install": self.install_requested,
            "update": self.install_requested,
            "build": self.build_requested,
            "cancel": self.cancel_requested,
        }[self.mode]
        signal.emit(self.app.id)

    def set_pinned(self, pinned: bool) -> None:
        if pinned == self.pinned:
            return
        self.pinned = pinned
        self.pin_star.setVisible(pinned)
        self.name_button.setAccessibleName(
            f"View {self.app.name} details" + (", pinned to the top" if pinned else "")
        )
        self.pin_action.setText("Unpin" if pinned else "Pin to top")

    def set_edge(self, edge: str) -> None:
        """Mark the row as first, last, only or middle so the list can round its hover."""
        if self.property("edge") != edge:
            self.setProperty("edge", edge)
            repolish(self)
        self.divider.setVisible(edge in ("first", "mid"))

    def set_chip(self, state: str, text: str, tooltip: str) -> None:
        if state not in CHIP_STATES:
            raise ValueError(f"Unknown chip state: {state}")
        self.chip.setText(text)
        self.chip.setToolTip(tooltip)
        if self.chip.property("state") != state:
            self.chip.setProperty("state", state)
            repolish(self.chip)

    def set_state(
        self,
        installed: Installed | None,
        release: Release | None,
        busy: bool,
        error: str = "",
        info: ReleaseInfo | None = None,
        refreshing: bool = False,
        refresh_error: str = "",
        working: str = "",
    ) -> None:
        self.installed = installed
        status = app_status(
            self.app, installed, release, error, info, refreshing, refresh_error, working
        )
        self.build_required = status.build_required
        self.mode = status.mode
        self.rollback_action.setEnabled(
            bool(
                not busy
                and installed
                and installed.managed
                and installed.previous
                and installed.previous.is_file()
            )
        )
        self.remove_action.setEnabled(bool(not busy and installed and installed.managed))
        self.hold_action.setText(
            "Release hold" if self.app.hold_updates else "Hold at current version"
        )
        self.hold_action.setEnabled(
            bool(not busy and (self.app.hold_updates or (installed and installed.managed)))
        )
        self.shortcut_action.setEnabled(bool(not busy and installed and installed.managed))
        self.launch_action.setEnabled(installed is not None)
        self.set_action(release)
        self.action_button.setEnabled(self.mode in ("cancel", "launch") or not busy)
        self.set_chip(status.state, status.text, status.tooltip)
        if installed:
            self.installed_version.setText(f"v{installed.version}" if installed.version else "?")
            self.installed_version.setToolTip(
                f"Installed with AppGlider\n{installed.path}"
                + ("\nPre-release" if installed.prerelease else "")
            )
        else:
            self.installed_version.setText("—")
            self.installed_version.setToolTip("Not installed")

    def set_action(self, release: Release | None) -> None:
        name = self.app.name
        text, accessible, tooltip = {
            "cancel": ("Cancel", f"Cancel {name}", "Cancel the running operation"),
            "build": ("Choose build", f"Choose build for {name}", "Choose which build to install"),
            "update": (
                "Update",
                f"Update {name}",
                f"Update to v{release.version}; keep the previous version"
                if release
                else "Update; keep the previous version",
            ),
            "launch": (
                "Launch",
                f"Launch {name}",
                f"Launch {self.installed.path}" if self.installed else "Launch",
            ),
            "install": (
                "Install",
                f"Install {name}",
                "Check the eligible release, then download and verify its AppImage",
            ),
        }[self.mode]
        self.action_button.setText(text)
        self.action_button.setAccessibleName(accessible)
        self.action_button.setToolTip(tooltip)
        object_name = "primary" if self.mode in ("update", "build") else ""
        if self.action_button.objectName() != object_name:
            self.action_button.setObjectName(object_name)
            repolish(self.action_button)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.press_pos = event.position().toPoint()
            event.accept()
        else:
            super().mousePressEvent(event)

    @property
    def draggable(self) -> bool:
        return not self.handle.isHidden()

    @draggable.setter
    def draggable(self, value: bool) -> None:
        self.handle.setVisible(value)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        pressed = self.press_pos is not None
        self.press_pos = None
        if (
            pressed
            and event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.details_requested.emit(self.app.id)
        else:
            super().mouseReleaseEvent(event)

    def start_drag(self, hot_spot: QPoint) -> None:
        self.press_pos = None
        mime = QMimeData()
        mime.setData(ORDER_MIME, QByteArray(self.app.id.encode()))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.setHotSpot(hot_spot)
        drag.exec(Qt.DropAction.MoveAction)


class AppList(QFrame):
    """The bordered list of rows; accepts a dragged row and reports where it was dropped."""

    row_dropped = Signal(str, int)  # app id, index in the shown list to insert before

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("appList")
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.rows: list[AppRow] = []
        self.reorderable = False
        self.scroll_area: QScrollArea | None = None
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(1, 1, 1, 1)
        self.box.setSpacing(0)
        self.drop_line = QFrame(self)
        self.drop_line.setObjectName("dropLine")
        self.drop_line.setFixedHeight(2)
        self.drop_line.hide()

    def set_rows(self, rows: list[AppRow]) -> None:
        if rows != self.rows:
            while self.box.count():
                self.box.takeAt(0)
            for row in rows:
                self.box.addWidget(row)
            self.rows = rows
        for index, row in enumerate(rows):
            edge = "only" if len(rows) == 1 else "first" if index == 0 else "mid"
            row.set_edge("last" if len(rows) > 1 and index == len(rows) - 1 else edge)

    def dragged(self, event: QDragMoveEvent | QDropEvent) -> AppRow | None:
        mime = event.mimeData()
        if not self.reorderable or not mime.hasFormat(ORDER_MIME):
            return None
        app_id = bytes(mime.data(ORDER_MIME).data()).decode(errors="replace")
        return next((row for row in self.rows if row.app.id == app_id), None)

    def drop_index(self, row: AppRow, position: QPoint) -> int:
        """Index to insert before, kept inside the dragged row's pinned or unpinned group."""
        before = sum(1 for other in self.rows if other.geometry().center().y() < position.y())
        group = [index for index, other in enumerate(self.rows) if other.pinned == row.pinned]
        return min(max(before, group[0]), group[-1] + 1)

    def show_drop_line(self, before: int) -> None:
        if not self.rows:
            return
        y = self.rows[before].y() if before < len(self.rows) else self.rows[-1].geometry().bottom()
        self.drop_line.setGeometry(1, max(0, y - 1), max(0, self.width() - 2), 2)
        self.drop_line.show()
        self.drop_line.raise_()

    def scroll_for(self, position: QPoint) -> None:
        if self.scroll_area is None:
            return
        viewport = self.scroll_area.viewport()
        y = self.mapTo(viewport, position).y()
        bar = self.scroll_area.verticalScrollBar()
        if y < DRAG_SCROLL_MARGIN:
            bar.setValue(bar.value() - 18)
        elif y > viewport.height() - DRAG_SCROLL_MARGIN:
            bar.setValue(bar.value() + 18)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self.dragged(event) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        row = self.dragged(event)
        if row is None:
            self.drop_line.hide()
            event.ignore()
            return
        position = event.position().toPoint()
        self.show_drop_line(self.drop_index(row, position))
        self.scroll_for(position)
        event.acceptProposedAction()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self.drop_line.hide()
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:
        self.drop_line.hide()
        row = self.dragged(event)
        if row is None:
            event.ignore()
            return
        event.acceptProposedAction()
        self.row_dropped.emit(row.app.id, self.drop_index(row, event.position().toPoint()))


class OrphansDialog(QDialog):
    """Offer to forget library entries for unknown apps; keeping them changes nothing."""

    def __init__(self, orphans: list[Orphan], parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Unrecognised library entries")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "These entries are in your AppGlider library but match no application in the "
            "current catalog, for example a repository that was removed. They are kept "
            "untouched unless you tick them. Forgetting an entry never deletes files."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.entries = QListWidget()
        self.entries.setAccessibleName("Unrecognised entries")
        for orphan in orphans:
            version = f" v{orphan.version}" if orphan.version else ""
            item = QListWidgetItem(f"{orphan.app_id}{version} · {orphan.path}")
            item.setData(Qt.ItemDataRole.UserRole, orphan.app_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.entries.addItem(item)
        layout.addWidget(self.entries)
        buttons = QDialogButtonBox()
        self.forget_button = buttons.addButton(
            "Forget ticked entries", QDialogButtonBox.ButtonRole.AcceptRole
        )
        buttons.addButton("Keep all", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected(self) -> set[str]:
        return {
            str(item.data(Qt.ItemDataRole.UserRole))
            for row in range(self.entries.count())
            if (item := self.entries.item(row)).checkState() == Qt.CheckState.Checked
        }


def fit_size(width: int, height: int, available: QRect) -> QSize:
    """Limit a preferred window size to 90% of the screen's usable area."""
    return QSize(min(width, available.width() * 9 // 10), min(height, available.height() * 9 // 10))


def screen_area(widget: QWidget | None) -> QRect:
    screen = widget.screen() if widget is not None else QApplication.primaryScreen()
    return screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1080)


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


def nav_entry(text: str, *, badge: bool = False) -> tuple[QPushButton, QLabel]:
    """Create a sidebar entry with a trailing count label (hidden until it has text)."""
    button = QPushButton(text)
    button.setObjectName("nav")
    button.setIconSize(QSize(NAV_ICON_SIZE, NAV_ICON_SIZE))
    button.setFixedHeight(38)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    layout = QHBoxLayout(button)
    layout.setContentsMargins(12, 0, 10, 0)
    layout.addStretch()
    count = QLabel()
    count.setObjectName("navBadge" if badge else "navCount")
    count.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    count.setAlignment(Qt.AlignmentFlag.AlignCenter)
    if badge:
        count.setFixedHeight(18)
        count.setMinimumWidth(18)
    layout.addWidget(count, alignment=Qt.AlignmentFlag.AlignVCenter)
    return button, count


class LauncherWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None, library: Library | None = None) -> None:
        super().__init__()
        self.settings = settings if settings is not None else QSettings()
        data_dir = (
            Path(
                QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)
            )
            / "appglider"
        )
        self.library = (
            library
            if library is not None
            else Library(
                data_dir,
                Path(str(self.settings.value("installation/folder", str(data_dir / "apps")))),
            )
        )
        self.installed: dict[str, Installed] = {}
        self.apps: tuple[App, ...] = ()
        self.pinned = pinned_apps(self.settings)
        self.releases: dict[str, Release] = {}
        self.check_errors: dict[str, str] = {}
        self.release_infos: dict[str, ReleaseInfo] = {}
        self.cache_stamps: dict[str, object] = {}
        self.tray_key: list[tuple[str, str, Path, tuple[int, int, int] | None]] | None = None
        self.release_errors: dict[str, str] = {}
        self.checks = ReleaseChecks(
            self.checkable_app,
            self.release_operation,
            lambda: 3 if github_status.quota_plentiful() else 1,
        )
        self.checks.started.connect(self.release_check_started)
        self.checks.completed.connect(self.release_check_done)
        self.checks.drained.connect(self.release_checks_drained)
        self.background_enabled = False
        self.close_after_release = False
        self.task: Task | None = None
        self.working: dict[str, str] = {}
        self.close_after_task = False
        self.quitting = False
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(str(APP_TASKBAR_ICON)))
        self.resize(fit_size(1040, 816, screen_area(None)))
        saved = self.settings.value("window/geometry")
        if isinstance(saved, QByteArray | bytes):
            self.restoreGeometry(QByteArray(saved))
        self.setMinimumSize(860, 560)
        self.view = "library"
        shell = QWidget()
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        self.setCentralWidget(shell)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 18, 12, 12)
        side.setSpacing(4)
        brand = QHBoxLayout()
        brand.setContentsMargins(6, 0, 0, 14)
        brand.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(QIcon(str(APP_ICON)).pixmap(32, 32))
        logo.setFixedSize(32, 32)
        logo.setAccessibleName(APP_NAME)
        brand.addWidget(logo)
        brand_name = QLabel(APP_NAME)
        brand_name.setObjectName("brand")
        brand.addWidget(brand_name)
        brand.addStretch()
        side.addLayout(brand)
        self.nav_library, self.library_count = nav_entry("Library")
        self.nav_updates, self.updates_count = nav_entry("Updates", badge=True)
        self.nav_add, _ = nav_entry("Add app")
        self.nav_settings, _ = nav_entry("Settings")
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for button in (self.nav_library, self.nav_updates, self.nav_add, self.nav_settings):
            button.setCheckable(True)
            self.nav_group.addButton(button)
        self.nav_library.setChecked(True)
        self.nav_library.clicked.connect(lambda: self.show_view("library"))
        self.nav_updates.clicked.connect(lambda: self.show_view("updates"))
        self.nav_add.setAccessibleName("Add app")
        self.nav_add.clicked.connect(self.open_add_app)
        self.nav_settings.setAccessibleName("Settings")
        self.nav_settings.clicked.connect(lambda: self.open_settings("General"))
        for button in (self.nav_library, self.nav_updates, self.nav_add):
            side.addWidget(button)
        side.addStretch()
        side.addWidget(self.nav_settings)
        side.addSpacing(6)
        self.status_block = QPushButton()
        self.status_block.setObjectName("statusBlock")
        self.status_block.setAccessibleName("GitHub request status")
        self.status_block.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status_block.setFixedHeight(66)
        self.status_block.clicked.connect(lambda: self.open_settings("GitHub"))
        status_layout = QVBoxLayout(self.status_block)
        status_layout.setContentsMargins(8, 8, 8, 6)
        status_layout.setSpacing(4)
        self.status_label = QLabel()
        self.github_label = QLabel()
        self.github_meter = QProgressBar()
        self.github_meter.setObjectName("meter")
        self.github_meter.setTextVisible(False)
        self.github_meter.setFixedHeight(4)
        for status_text in (self.status_label, self.github_label):
            status_text.setObjectName("sideStatus")
        for widget in (self.status_label, self.github_label, self.github_meter):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            status_layout.addWidget(widget)
        side.addWidget(self.status_block)
        self.version_button = QPushButton(f"v{APP_VERSION}")
        self.version_button.setObjectName("versionButton")
        self.version_button.setAccessibleName(f"About AppGlider {APP_VERSION}")
        self.version_button.setToolTip("About AppGlider")
        self.version_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.version_button.clicked.connect(lambda: self.open_settings("About"))
        side.addWidget(self.version_button, alignment=Qt.AlignmentFlag.AlignLeft)
        shell_layout.addWidget(sidebar)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        shell_layout.addWidget(body, stretch=1)
        self.pages = QStackedWidget()
        body_layout.addWidget(self.pages, stretch=1)
        list_widget = self.list_page = QWidget()
        layout = QVBoxLayout(list_widget)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(14)
        self.pages.addWidget(list_widget)
        self.app_page = AppPage(self)
        self.app_page.back_requested.connect(self.close_app)
        self.pages.addWidget(self.app_page)
        self.settings_page = SettingsPage(self)
        self.pages.addWidget(self.settings_page)
        self.add_page = AddPage(self)
        self.add_page.added.connect(self.app_added)
        self.pages.addWidget(self.add_page)
        self.pages.currentChanged.connect(self.page_changed)
        self.list_scroll = 0
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        self.page_title = QLabel("Library")
        self.page_title.setObjectName("pageTitle")
        toolbar.addWidget(self.page_title)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search apps")
        self.search.setAccessibleName("Search apps")
        self.search.setClearButtonEnabled(True)
        toolbar.addWidget(self.search, stretch=1)
        self.filters = QWidget()
        filters = QHBoxLayout(self.filters)
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setSpacing(0)
        self.filter_all = QPushButton("All")
        self.filter_installed = QPushButton("Installed")
        filter_group = QButtonGroup(self)
        filter_group.setExclusive(True)
        for position, button in (("first", self.filter_all), ("last", self.filter_installed)):
            button.setCheckable(True)
            button.setObjectName("segment")
            button.setProperty("segment", position)
            filter_group.addButton(button)
            filters.addWidget(button)
        self.filter_all.setChecked(True)
        self.filters.setAccessibleName("Library filter")
        toolbar.addWidget(self.filters)
        self.options_button = QToolButton()
        self.options_button.setObjectName("libraryOptions")
        self.options_button.setText("⋯")
        self.options_button.setAccessibleName("Library options")
        self.options_button.setToolTip("Library options")
        self.options_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        options_menu = QMenu(self.options_button)
        self.sort_action = options_menu.addAction("Sort A–Z")
        self.sort_action.triggered.connect(self.sort_alphabetically)
        self.options_button.setMenu(options_menu)
        toolbar.addWidget(self.options_button)
        self.check_button = QToolButton()
        self.check_button.setObjectName("checkNow")
        self.check_button.setAccessibleName("Check for updates")
        self.check_button.setToolTip(CHECK_TOOLTIP)
        self.check_button.setIconSize(QSize(18, 18))
        self.check_button.clicked.connect(lambda: self.refresh_releases(force=True))
        toolbar.addWidget(self.check_button)
        layout.addLayout(toolbar)
        self.banner = QFrame()
        self.banner.setObjectName("updatesBanner")
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(14, 10, 10, 10)
        banner_layout.setSpacing(8)
        self.banner_label = QLabel()
        self.banner_label.setTextFormat(Qt.TextFormat.RichText)
        self.banner_label.setWordWrap(True)
        banner_layout.addWidget(self.banner_label, stretch=1)
        self.update_all_button = QPushButton("Update all")
        self.update_all_button.clicked.connect(self.update_all)
        self.update_all_button.setObjectName("primary")
        banner_layout.addWidget(self.update_all_button)
        self.review_button = QPushButton("Review")
        self.review_button.setToolTip("Show only the apps that have updates")
        self.review_button.clicked.connect(lambda: self.show_view("updates"))
        banner_layout.addWidget(self.review_button)
        self.banner.hide()
        layout.addWidget(self.banner)
        self.search.setFocus()
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        list_page = QWidget()
        list_layout = QVBoxLayout(list_page)
        list_layout.setContentsMargins(0, 0, 8, 0)
        self.app_list = AppList()
        self.app_list.scroll_area = self.scroll_area
        self.app_list.row_dropped.connect(self.drop_app)
        list_layout.addWidget(self.app_list)
        list_layout.addStretch()
        self.scroll_area.setWidget(list_page)
        layout.addWidget(self.scroll_area, stretch=1)
        self.empty = QLabel()
        self.empty.setObjectName("emptyState")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty, stretch=1)
        self.welcome = QWidget()
        welcome_layout = QVBoxLayout(self.welcome)
        welcome_layout.setSpacing(16)
        welcome_layout.setContentsMargins(24, 0, 24, 0)
        welcome_layout.addStretch()
        welcome_title = QLabel("Welcome to AppGlider")
        welcome_title.setObjectName("welcomeTitle")
        welcome_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_layout.addWidget(welcome_title)
        welcome_text = QLabel(
            "Your library is empty. Add an app from a public GitHub repository, import a "
            "catalog (a plain-text list of repositories you can read before adding it), or "
            "start from the ready-made example list of ArtCraft apps and SolidCraft."
        )
        welcome_text.setObjectName("emptyState")
        welcome_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_text.setWordWrap(True)
        welcome_layout.addWidget(welcome_text)
        welcome_buttons = QHBoxLayout()
        welcome_buttons.addStretch()
        self.welcome_add_button = QPushButton("+ Add app")
        self.welcome_add_button.setObjectName("primary")
        self.welcome_add_button.clicked.connect(self.open_add_app)
        welcome_buttons.addWidget(self.welcome_add_button)
        self.welcome_import_button = QPushButton("Import catalog…")
        self.welcome_import_button.clicked.connect(self.settings_page.import_catalog)
        welcome_buttons.addWidget(self.welcome_import_button)
        self.welcome_example_button = QPushButton("Example catalog")
        self.welcome_example_button.setToolTip(
            "Add the ArtCraft apps and SolidCraft, a ready-made example list."
        )
        self.welcome_example_button.clicked.connect(self.settings_page.import_example_catalog)
        welcome_buttons.addWidget(self.welcome_example_button)
        welcome_buttons.addStretch()
        welcome_layout.addLayout(welcome_buttons)
        welcome_layout.addStretch()
        layout.addWidget(self.welcome, stretch=1)
        self.footer = StatusMessage()
        self.footer.changed.connect(self.update_status_line)
        self.footer.expired.connect(self.expire_footer)
        self.status_line = QWidget()
        progress_row = QHBoxLayout(self.status_line)
        progress_row.setContentsMargins(22, 0, 22, 12)
        progress_row.addWidget(self.footer, stretch=1)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(170)
        self.progress_bar.hide()
        progress_row.addWidget(self.progress_bar)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_task)
        self.cancel_button.hide()
        progress_row.addWidget(self.cancel_button)
        self.orphans: list[Orphan] = []
        self.orphans_button = QPushButton()
        self.orphans_button.clicked.connect(self.review_orphans)
        self.orphans_button.hide()
        progress_row.addWidget(self.orphans_button)
        body_layout.addWidget(self.status_line)
        self.update_status_line()
        self.cards: dict[str, AppRow] = {}
        self.search.textChanged.connect(self.refresh)
        self.filter_installed.toggled.connect(self.refresh)
        QApplication.styleHints().colorSchemeChanged.connect(self.system_theme_changed)
        self.tray_icons = (QIcon(str(APP_ICON)), QIcon(str(APP_UPDATE_ICON)))
        self.tray = QSystemTrayIcon(self.tray_icons[0], self)
        self.tray_has_updates = False
        self.tray_menu = QMenu(self)
        self.tray_menu.addAction("Open AppGlider").triggered.connect(self.show_launcher)
        self.tray_check_action = self.tray_menu.addAction("Check for updates")
        self.tray_check_action.triggered.connect(lambda: self.refresh_releases(force=True))
        self.tray_apps_menu = self.tray_menu.addMenu("Installed apps")
        self.tray_menu.addSeparator()
        self.tray_menu.addAction("Quit AppGlider").triggered.connect(self.quit_launcher)
        self.tray_menu.aboutToShow.connect(self.scan_installed)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(self.tray_activated)
        self.tray.messageClicked.connect(self.show_launcher)
        self.notify_pending = False
        self.notified = notified_versions(self.settings)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.apply_theme()
        restore_rate_limits(self.library.data_dir / "release-cache")
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.scheduled_check)
        self.github_timer = QTimer(self)
        self.github_timer.timeout.connect(self.update_github_status)
        self.github_timer.start(1000)
        self.update_github_status()
        self.scan_installed()
        self.schedule_update_checks()
        self.setup_keyboard()

    def setup_keyboard(self) -> None:
        """Shortcuts, focus behaviour and the order Tab walks through the controls."""
        for sequence, handler in (("Ctrl+F", self.focus_search), ("Esc", self.go_back)):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(handler)
        for button in self.findChildren(QAbstractButton):
            self.keyboard_button(button)
        QWidget.setTabOrder(self.version_button, self.search)

    @staticmethod
    def keyboard_button(button: QAbstractButton) -> None:
        """Focus a button with Tab only, so a mouse click leaves no ring; Enter presses it."""
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        if isinstance(button, QPushButton):
            button.setAutoDefault(True)

    def focus_search(self) -> None:
        if self.pages.currentIndex() != 0:
            self.show_view(self.view)
        self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search.selectAll()

    def go_back(self) -> None:
        """Escape leaves the App or Add page for the list."""
        if self.pages.currentWidget() in (self.app_page, self.add_page):
            self.show_view(self.view)

    def update_status_line(self) -> None:
        """Show the bottom row only while it has a message, progress, Cancel or orphans."""
        self.footer.setVisible(bool(self.footer.text()))
        self.status_line.setVisible(
            any(
                not widget.isHidden()
                for widget in (
                    self.footer,
                    self.progress_bar,
                    self.cancel_button,
                    self.orphans_button,
                )
            )
        )

    def expire_footer(self) -> None:
        if self.task is None and not self.checks.busy:
            self.footer.setText("")
        else:
            self.footer.timer.start(self.footer.LIFETIME_MS)

    def schedule_update_checks(self) -> None:
        hours = check_interval_hours(self.settings)
        if hours:
            self.update_timer.start(hours * 60 * 60 * 1000)
        else:
            self.update_timer.stop()

    def scheduled_check(self) -> None:
        """Check installed apps on the chosen schedule, without disturbing a running task."""
        if (
            self.task is None
            and not self.checks.busy
            and not self.close_after_release
            and not self.quitting
            and self.installed
            and not github_status.quota_low()
        ):
            self.notify_pending = True
            self.refresh_releases()
            self.notify_pending = self.notify_pending and self.checks.busy

    def notify_updates(self) -> None:
        """Tell the user, once per new version, that updates are waiting (opt-in)."""
        waiting = {
            app.id: release.version
            for app in self.apps
            if app.id in self.installed
            and self.has_update(app.id)
            and (release := self.releases.get(app.id)) is not None
        }
        new = [
            app for app in self.apps if waiting.get(app.id) not in (None, self.notified.get(app.id))
        ]
        self.notified = dict(waiting)
        self.settings.setValue("notifications/seen", json.dumps(self.notified))
        if (
            not new
            or not self.settings.value("notifications/updates", False, type=bool)
            or not QSystemTrayIcon.isSystemTrayAvailable()
            or not self.tray.isVisible()
            or (self.isVisible() and self.isActiveWindow())
        ):
            return
        names = [f"{app.name} {waiting[app.id]}" for app in new]
        if len(names) == 1:
            text = f"{names[0]} is available."
        else:
            more = f", and {len(names) - 3} more" if len(names) > 3 else ""
            text = f"{len(names)} updates are waiting: {', '.join(names[:3])}{more}."
        self.tray.showMessage("AppGlider: updates available", text, self.tray_icons[1], 15000)

    def update_github_status(self) -> None:
        label, details, limited = github_request_status()
        allowance = github_status.allowance()
        self.github_label.setText(
            "GitHub: limited"
            if limited
            else f"GitHub {allowance[0]} of {allowance[1]} left"
            if allowance
            else label
        )
        self.github_meter.setVisible(allowance is not None)
        if allowance:
            self.github_meter.setRange(0, max(1, allowance[1]))
            self.github_meter.setValue(allowance[0])
        self.status_label.setText(self.check_summary())
        if self.settings_page.isVisible():
            self.settings_page.update_github_status()
        self.status_block.setToolTip(details + "\nClick for details in Settings → GitHub.")
        if self.status_block.property("limited") != limited:
            self.status_block.setProperty("limited", limited)
            for widget in (self.status_block, self.github_label, self.github_meter):
                widget.style().unpolish(widget)
                widget.style().polish(widget)

    def check_summary(self) -> str:
        """Describe the last release check and, when scheduled, the next one."""
        last = max((info.checked_at for info in self.release_infos.values()), default=0)
        scheduled = self.update_timer.isActive()
        if not last:
            return "Not checked yet" if scheduled else "Automatic checks off"
        text = f"Checked {datetime.fromtimestamp(last):%H:%M}"
        if scheduled:
            upcoming = datetime.now().timestamp() + self.update_timer.remainingTime() / 1000
            text += f" · next {datetime.fromtimestamp(upcoming):%H:%M}"
        return text

    def create_row(self, app: App) -> AppRow:
        card = AppRow(app, self.library.icon_path(app))
        card.setParent(self.app_list)
        card.details_requested.connect(self.open_app)
        card.launch_requested.connect(self.launch_app)
        card.install_requested.connect(self.install_app)
        card.shortcut_requested.connect(self.create_shortcut)
        card.rollback_requested.connect(self.rollback_app)
        card.remove_requested.connect(self.remove_app)
        card.pin_requested.connect(self.toggle_pin)
        card.hold_requested.connect(self.toggle_hold)
        card.build_requested.connect(self.choose_build)
        card.cancel_requested.connect(self.cancel_task)
        card.move_requested.connect(self.move_app)
        for button in card.findChildren(QAbstractButton):
            self.keyboard_button(button)
        return card

    def sorted_apps(self, apps: Iterable[App]) -> list[App]:
        """Pinned apps first, then the user's order; apps without a place follow, A-Z."""
        custom = {app_id: index for index, app_id in enumerate(custom_order(self.settings))}
        catalog = {app.id: index for index, app in enumerate(self.apps)}

        def key(app: App) -> tuple[object, ...]:
            return (
                app.id not in self.pinned,
                custom.get(app.id, len(custom)),
                app.name.casefold(),
                catalog.get(app.id, 0),
            )

        return sorted(apps, key=key)

    def legacy_order(self, mode: str) -> list[str]:
        """The order the retired Sort box showed for `mode`, for one-time migration."""
        catalog = {app.id: index for index, app in enumerate(self.apps)}

        def key(app: App) -> tuple[object, ...]:
            installed = app.id in self.installed
            name = app.name.casefold()
            if mode == "installed":
                return (not installed, name)
            if mode == "updates":
                return (not self.has_update(app.id), not installed, name)
            if mode == "added":
                return ()
            return (name,)

        return [app.id for app in sorted(self.apps, key=lambda app: (key(app), catalog[app.id]))]

    def migrate_legacy_sort(self) -> None:
        """Turn a saved Sort choice into the equivalent custom order, then forget the choice."""
        if not self.apps or not self.settings.contains("library/sort"):
            return
        mode = str(self.settings.value("library/sort", "name"))
        if not custom_order(self.settings):
            self.settings.setValue("library/order", json.dumps(self.legacy_order(mode)))
        self.settings.remove("library/sort")

    def sort_alphabetically(self) -> None:
        if len(self.apps) < 2:
            return
        answer = QMessageBox.question(
            self,
            "Sort the library A–Z?",
            "This replaces your own order. Pinned apps stay on top.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        ids = [app.id for app in sorted(self.apps, key=lambda app: app.name.casefold())]
        self.settings.setValue("library/order", json.dumps(ids))
        self.footer.setText("Library sorted A–Z.")
        self.refresh()

    def reorder_enabled(self) -> bool:
        """Dragging works in the Library, with All or Installed, but not while searching."""
        return self.view == "library" and not self.search.text().strip()

    def move_app(self, app_id: str, delta: int) -> None:
        ids = [row.app.id for row in self.app_list.rows]
        if app_id in ids:
            source = ids.index(app_id)
            if delta > 0:
                self.reorder_app(app_id, source + delta, after=True)
            else:
                self.reorder_app(app_id, source + delta)

    def drop_app(self, app_id: str, before: int) -> None:
        ids = [row.app.id for row in self.app_list.rows]
        if app_id in ids:
            source = ids.index(app_id)
            self.reorder_app(app_id, before if before <= source else before - 1)

    def reorder_app(self, app_id: str, index: int, after: bool = False) -> None:
        """Move an app within the shown apps, leaving the hidden ones where they are.

        `index` is a position among the shown apps without the moved one. The app goes
        directly before the app there (or directly after the one before it when `after`
        is set, or after the last shown app at the end), within its pinned group.
        """
        if not self.reorder_enabled():
            return
        shown = [row.app.id for row in self.app_list.rows]
        if app_id not in shown:
            return
        source = shown.index(app_id)
        ids = [item for item in shown if item != app_id]
        pinned = sum(item in self.pinned for item in ids)
        if app_id in self.pinned:
            index = min(max(index, 0), pinned)
        else:
            index = min(max(index, pinned), len(ids))
        if index == source or not ids:
            return
        full = [app.id for app in self.sorted_apps(self.apps)]
        full.remove(app_id)
        if after or index >= len(ids):
            full.insert(full.index(ids[index - 1]) + 1, app_id)
        else:
            full.insert(full.index(ids[index]), app_id)
        if full == [app.id for app in self.sorted_apps(self.apps)]:
            return
        self.settings.setValue("library/order", json.dumps(full))
        self.footer.setText("Custom order saved.")
        self.refresh()

    def toggle_pin(self, app_id: str) -> None:
        if app_id in self.pinned:
            self.pinned.discard(app_id)
        elif len(self.pinned) < MAX_PINNED:
            self.pinned.add(app_id)
        self.settings.setValue("library/pinned", json.dumps(sorted(self.pinned)))
        self.refresh()

    def toggle_hold(self, app_id: str) -> None:
        app = next((app for app in self.apps if app.id == app_id), None)
        if app is None or self.task is not None:
            return
        held = not app.hold_updates
        try:
            self.library.set_update_hold(app_id, held)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Cannot change update hold", str(error))
            return
        self.scan_installed()
        self.footer.setText(
            f"{app.name} is held at its current version."
            if held
            else f"{app.name} is no longer held."
        )

    def show_launcher(self) -> None:
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_launcher()

    def quit_launcher(self) -> None:
        self.show_launcher()
        self.quitting = True
        if not self.close() and not self.close_after_task and not self.close_after_release:
            self.quitting = False

    def refresh(self) -> None:
        update_count = sum(self.has_update(app.id) for app in self.apps)
        self.library_count.setText(str(len(self.apps)))
        self.updates_count.setText(str(update_count))
        self.updates_count.setVisible(update_count > 0)
        self.nav_updates.setAccessibleName(
            f"Updates, {update_count} waiting" if update_count else "Updates"
        )
        self.filter_installed.setText(f"Installed ({len(self.installed)})")
        updates_view = self.view == "updates"
        self.tray.setToolTip(f"AppGlider · Updates: {update_count}")
        if (update_count > 0) != self.tray_has_updates:
            self.tray_has_updates = update_count > 0
            self.tray.setIcon(self.tray_icons[int(self.tray_has_updates)])
        self.tray_check_action.setEnabled(not self.checks.busy and bool(self.installed))
        tray_apps = [
            (app, self.library.icon_path(app))
            for app in self.sorted_apps(app for app in self.apps if app.id in self.installed)
        ]
        tray_key = [(app.id, app.name, path, file_stamp(path)) for app, path in tray_apps]
        if tray_key != self.tray_key:
            self.tray_key = tray_key
            self.tray_apps_menu.clear()
            self.tray_apps_menu.setEnabled(bool(tray_apps))
            for app, path in tray_apps:
                action = self.tray_apps_menu.addAction(QIcon(icon_pixmap(path, 64)), app.name)
                action.triggered.connect(
                    lambda checked=False, app_id=app.id: self.launch_app(app_id)
                )
        query = self.search.text().strip().casefold()
        visible = [
            app
            for app in self.apps
            if (
                self.has_update(app.id)
                if updates_view
                else app.id in self.installed or not self.installed_only
            )
            and query in f"{app.name} {app.description} {app.repo}".casefold()
        ]
        visible = self.sorted_apps(visible)
        ids = {app.id for app in visible}
        for app_id, card in self.cards.items():
            card.icon.setPixmap(icon_pixmap(self.library.icon_path(card.app), 40))
            card.set_pinned(app_id in self.pinned)
            card.set_state(
                self.installed.get(app_id),
                self.releases.get(app_id),
                self.task is not None,
                self.check_errors.get(app_id, ""),
                self.release_infos.get(app_id),
                self.checks.is_checking(app_id),
                self.release_errors.get(app_id, ""),
                self.working.get(app_id, ""),
            )
            card.setVisible(app_id in ids)
            card.draggable = False
            card.move_up_action.setEnabled(False)
            card.move_down_action.setEnabled(False)
        shown = [self.cards[app.id] for app in visible]
        reorder = self.reorder_enabled()
        self.app_list.reorderable = reorder
        for index, row in enumerate(shown):
            row.draggable = reorder and len(shown) > 1
            row.move_up_action.setEnabled(
                reorder and index > 0 and shown[index - 1].pinned == row.pinned
            )
            row.move_down_action.setEnabled(
                reorder and index + 1 < len(shown) and shown[index + 1].pinned == row.pinned
            )
        self.nav_add.setEnabled(self.task is None)
        self.settings_page.update_state()
        self.add_page.update_state()
        self.search.setPlaceholderText(
            f"Search {update_count if updates_view else len(self.apps)} apps"
        )
        self.check_button.setToolTip("Checking for updates…" if self.checks.busy else CHECK_TOOLTIP)
        self.check_button.setEnabled(not self.checks.busy and bool(self.installed))
        self.options_button.setEnabled(len(self.apps) > 1)
        self.update_all_button.setVisible(
            any(
                item.managed and app_id in self.releases and self.has_update(app_id)
                for app_id, item in self.installed.items()
            )
        )
        self.update_all_button.setEnabled(self.task is None)
        self.review_button.setVisible(not updates_view)
        self.banner.setVisible(update_count > 0)
        if update_count > 0:
            self.banner_label.setText(self.banner_text())
        self.app_list.set_rows(shown)
        order: list[QWidget] = [
            self.search,
            self.filter_all,
            self.filter_installed,
            self.options_button,
            self.check_button,
            self.update_all_button,
            self.review_button,
        ]
        for row in shown:
            order += [row.name_button, row.action_button, row.more_button]
        order += [self.cancel_button, self.orphans_button]
        for before, after in zip(order, order[1:], strict=False):
            QWidget.setTabOrder(before, after)
        self.update_status_line()
        self.scroll_area.setVisible(bool(visible))
        self.empty.setVisible(not visible and bool(self.apps))
        self.welcome.setVisible(not self.apps)
        self.welcome_add_button.setEnabled(self.task is None)
        self.welcome_import_button.setEnabled(self.task is None)
        self.welcome_example_button.setEnabled(self.task is None)
        if self.app_page.app_id is not None:
            if any(app.id == self.app_page.app_id for app in self.apps):
                self.app_page.refresh()
            else:
                self.close_app()
        if query:
            self.empty.setText(
                "No apps match your search in this view.\nTry another name or clear the search."
            )
        elif updates_view:
            self.empty.setText(
                "No known updates.\nUse Check for updates to look for new releases."
                if self.installed
                else "No installed apps to check.\nInstall an app from Library to get started."
            )
        elif self.installed_only:
            self.empty.setText("No apps are installed.\nChoose Install from All to get started.")
        else:
            self.empty.setText("No apps match your search.\nTry another name or clear the search.")

    @property
    def installed_only(self) -> bool:
        return self.filter_installed.isChecked()

    def show_view(self, view: str) -> None:
        """Show the whole library or only the apps with updates."""
        if view not in ("library", "updates"):
            raise ValueError(f"Unknown view: {view}")
        self.view = view
        self.show_list()
        (self.nav_updates if view == "updates" else self.nav_library).setChecked(True)
        self.page_title.setText("Updates" if view == "updates" else "Library")
        self.filters.setVisible(view == "library")
        self.refresh()

    def banner_text(self) -> str:
        """Summarise the waiting updates, shortened the way the tray notification is."""
        names = []
        for app in self.apps:
            if self.has_update(app.id):
                release = self.releases.get(app.id)
                version = (
                    f" {'v' if release.version[:1].isdigit() else ''}{release.version}"
                    if release
                    else ""
                )
                names.append(html.escape(f"{app.name}{version}"))
        count = len(names)
        lead = f"<b>{count} update{'s' if count != 1 else ''} ready.</b>"
        if count > 3:
            tail = f"{', '.join(names[:3])}, and {count - 3} more"
        elif count > 1:
            tail = f"{', '.join(names[:-1])} and {names[-1]}"
        else:
            tail = names[0]
        return f"{lead} {tail}."

    def open_app(self, app_id: str) -> None:
        """Show the App page; the sidebar keeps the view it was opened from."""
        if not any(app.id == app_id for app in self.apps):
            return
        if self.pages.currentWidget() is not self.app_page:
            self.list_scroll = self.scroll_area.verticalScrollBar().value()
        self.app_page.show_app(app_id, "Updates" if self.view == "updates" else "Library")
        self.show_page(self.app_page)
        (self.nav_updates if self.view == "updates" else self.nav_library).setChecked(True)
        if isinstance(QApplication.focusWidget(), QAbstractButton):
            # A keyboard user stays oriented; a mouse click leaves no focus ring behind.
            self.app_page.back_button.setFocus()

    open_details = open_app

    def close_app(self) -> None:
        """Return to the list with the view, filter, search and scroll position as they were."""
        if self.pages.currentWidget() is not self.app_page:
            return
        self.app_page.app_id = None
        self.show_page(self.list_page)
        QTimer.singleShot(
            0, self, lambda: self.scroll_area.verticalScrollBar().setValue(self.list_scroll)
        )
        self.scroll_area.verticalScrollBar().setValue(self.list_scroll)

    def show_list(self) -> None:
        """Return to the Library or Updates list from whichever page is showing."""
        if self.pages.currentWidget() is self.app_page:
            self.close_app()
        else:
            self.show_page(self.list_page)

    def show_page(self, page: QWidget) -> None:
        """Switch pages; a mouse user is not left with a focus ring on the new page."""
        keyboard = isinstance(QApplication.focusWidget(), QAbstractButton)
        self.pages.setCurrentWidget(page)
        if not keyboard:
            # Parks focus on the page itself: Tab still enters it, and no button shows a ring.
            page.setFocus(Qt.FocusReason.OtherFocusReason)

    def page_changed(self) -> None:
        if self.pages.currentWidget() is not self.add_page:
            self.add_page.leave()

    def open_settings(self, section: int | str = "General") -> None:
        """Show the Settings page at a section; the old dialog's numbers still work."""
        name = LEGACY_SECTIONS[section] if isinstance(section, int) else section
        self.settings_page.show_section(name)
        self.show_page(self.settings_page)
        self.nav_settings.setChecked(True)
        if isinstance(QApplication.focusWidget(), QAbstractButton):
            self.settings_page.focus_section()

    def open_add_app(self) -> None:
        if self.task is not None:
            return
        self.add_page.start()
        self.show_page(self.add_page)
        self.add_page.url.setFocus()
        self.nav_add.setChecked(True)

    def app_added(self, app_id: str, warning: str) -> None:
        self.catalog_changed()
        app = next((app for app in self.apps if app.id == app_id), None)
        self.open_app(app_id)
        if app is not None:
            self.footer.setText(f"Added {app.name}. Install it from its page.{warning}")

    def choose_build(self, app_id: str) -> None:
        """Open the App page and ask which build of the app to use."""
        self.open_app(app_id)
        self.app_page.choose_build()

    def review_orphans(self) -> None:
        if self.task is not None:
            return
        dialog = OrphansDialog(self.orphans, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected():
            try:
                self.library.discard_orphans(dialog.selected())
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "Cannot update the library", str(error))
            self.scan_installed()
        dialog.deleteLater()

    def has_update(self, app_id: str) -> bool:
        app = next((app for app in self.apps if app.id == app_id), None)
        if app is not None and app.hold_updates:
            return False
        installed, release = self.installed.get(app_id), self.releases.get(app_id)
        if installed and release:
            return release.newer_than(installed.version)
        info = self.release_infos.get(app_id)
        choice = info.choice(app.include_prereleases) if info and app else None
        return bool(
            installed
            and choice
            and choice.build_required
            and version_key(choice.version) > version_key(installed.version)
        )

    def scan_installed(self) -> None:
        try:
            apps = self.library.apps
            installed = self.library.scan()
            orphans = self.library.orphans()
            self.library.reap_processes()
        except (OSError, ValueError) as error:
            self.footer.setText(str(error))
        else:
            self.orphans = orphans
            self.orphans_button.setVisible(bool(orphans))
            self.orphans_button.setText(
                f"Review {len(orphans)} unrecognised {'entry' if len(orphans) == 1 else 'entries'}…"
            )
            if apps != self.apps:
                for app_id in set(self.cards) - {app.id for app in apps}:
                    self.cards.pop(app_id).deleteLater()
                    self.releases.pop(app_id, None)
                    self.check_errors.pop(app_id, None)
                    self.release_infos.pop(app_id, None)
                    self.release_errors.pop(app_id, None)
                for app in apps:
                    kept = self.cards.get(app.id)
                    if (
                        kept is not None
                        and kept.app != app
                        and replace(kept.app, hold_updates=app.hold_updates) == app
                    ):
                        # Only the hold changed: keep the card and the release it knows.
                        kept.app = app
                        continue
                    if app.id not in self.cards or self.cards[app.id].app != app:
                        old = self.cards.pop(app.id, None)
                        if old is not None:
                            if old.app.build_variant != app.build_variant:
                                self.release_infos.pop(app.id, None)
                                self.release_errors.pop(app.id, None)
                                self.checks.requeue_active(app.id)
                            old.deleteLater()
                        self.releases.pop(app.id, None)
                        self.check_errors.pop(app.id, None)
                        self.cards[app.id] = self.create_row(app)
                self.apps = apps
            cache_dir = self.library.data_dir / "release-cache"
            for app in apps:
                paths = (info_path(app, cache_dir), release_cache_path(app, cache_dir))
                stamps = tuple(file_stamp(path) for path in paths)
                fresh = self.cache_stamps.get(app.id) != (paths, stamps)
                self.cache_stamps[app.id] = (paths, stamps)
                info = cached_info(app, cache_dir) if fresh else None
                if info is not None and (
                    app.id not in self.release_infos
                    or info.checked_at > self.release_infos[app.id].checked_at
                ):
                    self.release_infos[app.id] = info
                    github_status.release_result("Cached information", app.repo, info.checked_at)
                if app.id in self.release_infos:
                    self.apply_release_info(app)
                elif fresh:
                    release = cached_release(app, cache_dir)
                    if release is not None:
                        self.releases[app.id] = release
            self.installed = installed
            self.migrate_legacy_sort()
        self.refresh()

    def launch_app(self, app_id: str) -> None:
        app = next(app for app in self.apps if app.id == app_id)
        installed = self.installed.get(app_id)
        if installed is None:
            self.scan_installed()
            return
        try:
            log = self.library.launch(app, installed)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, f"Cannot launch {app.name}", str(error))
            return
        process = self.library.processes[app_id]
        self.footer.setText(f"Launched {app.name} · Log: {log}")

        def check_startup() -> None:
            code = process.poll()
            if code is not None and code != 0:
                self.footer.setText(f"{app.name} exited with code {code}. See {log}")
                QMessageBox.warning(
                    self,
                    f"{app.name} could not start",
                    f"The application exited with code {code}.\nIts diagnostic log is:\n{log}",
                )

        QTimer.singleShot(1800, self, check_startup)

    def start_task(
        self,
        message: str,
        operation: Callable[[Event, Progress], TaskResult],
        *,
        page: AppPage | None = None,
        working: dict[str, str] | None = None,
    ) -> None:
        if self.task is not None:
            return
        self.working = working or {}
        self.task = Task(operation)
        self.task.signals.progress.connect(self.task_progress)
        self.task.signals.finished.connect(self.task_finished)
        self.task.signals.failed.connect(self.task_failed)
        if page is not None:
            # After the window's own handlers, so the page sees the settled state.
            self.task.signals.finished.connect(page.task_done)
            self.task.signals.failed.connect(page.task_error)
        self.footer.setText(message)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()
        self.cancel_button.setText("Cancel")
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()
        self.refresh()
        QThreadPool.globalInstance().start(self.task)

    def check_releases(self, app_id: str, page: AppPage | None = None) -> None:
        """A full release check of one app, even an uninstalled one (never downloads the app)."""
        app = next((app for app in self.apps if app.id == app_id), None)
        if app is None or self.task is not None:
            return

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            info = discover_releases(
                app, cancel, self.library.data_dir / "release-cache", force=True
            )
            return TaskResult("Release checked.", release_infos={app.id: info})

        self.start_task(f"Checking releases for {app.name}…", operation, page=page)

    def create_shortcut(self, app_id: str) -> None:
        app = next(app for app in self.apps if app.id == app_id)
        try:
            self.library.create_shortcut(app)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Cannot create shortcut", str(error))
        else:
            self.footer.setText(f"Created menu shortcut: {app.name} (AppGlider).")

    def task_progress(self, message: str, received: int, total: int) -> None:
        self.footer.setText(message)
        self.progress_bar.setRange(0, 100 if total > 0 else 0)
        if total > 0:
            self.progress_bar.setValue(min(100, int(received * 100 / total)))

    def reset_task(self) -> None:
        self.task = None
        self.working = {}
        self.progress_bar.hide()
        self.cancel_button.hide()
        self.scan_installed()

    def task_finished(self, result: TaskResult) -> None:
        self.releases.update(result.releases)
        for app in self.apps:
            checked_info = result.release_infos.get(app.id)
            if checked_info is not None and checked_info.build_variant == app.build_variant:
                self.release_infos[app.id] = checked_info
                self.release_errors.pop(app.id, None)
                self.apply_release_info(app)
            release = result.releases.get(app.id)
            if release is not None:
                info = remember_release(app, release, self.library.data_dir / "release-cache")
                if info is not None:
                    self.release_infos[app.id] = info
        self.check_errors.update(result.errors)
        for app_id in result.releases:
            self.check_errors.pop(app_id, None)
        self.reset_task()
        self.footer.setText(result.message)
        if result.build_choices and not self.close_after_task:
            self.choose_build(next(iter(result.build_choices)))
        if result.errors and not self.close_after_task:
            errors = "\n\n".join(f"{key}: {value}" for key, value in result.errors.items())
            QMessageBox.warning(self, "Some operations could not finish", errors)
        if self.close_after_task:
            self.close()

    def task_failed(self, error: str) -> None:
        self.reset_task()
        self.footer.setText(error)
        if self.close_after_task:
            self.close()
        else:
            QMessageBox.warning(self, "Operation could not finish", error)

    def cancel_task(self) -> None:
        if self.task is not None:
            self.task.cancel.set()
            self.cancel_button.setText("Cancelling…")
            self.cancel_button.setEnabled(False)

    def apply_release_info(self, app: App) -> None:
        info = self.release_infos[app.id]
        choice = info.choice(app.include_prereleases)
        if choice and choice.release:
            self.releases[app.id] = choice.release
        else:
            self.releases.pop(app.id, None)
        error = info.error(app.include_prereleases)
        if error:
            self.check_errors[app.id] = error
        else:
            self.check_errors.pop(app.id, None)

    def catalog_changed(self) -> None:
        self.scan_installed()
        if self.background_enabled:
            self.checks.add(
                [
                    app.id
                    for app in self.apps
                    if app.id in self.installed and app.id not in self.release_infos
                ]
            )

    def refresh_releases(self, *, force: bool = False) -> None:
        if self.checks.busy or self.close_after_release:
            return
        self.background_enabled = True
        self.scan_installed()
        if not self.installed:
            self.footer.setText("Install an application to enable release checks.")
            return
        self.checks.force = force
        self.checks.add([app.id for app in self.apps if app.id in self.installed])

    def release_operation(self, app: App, force: bool) -> Callable[[Event, Progress], TaskResult]:
        def operation(cancel: Event, progress: Progress) -> TaskResult:
            info = discover_releases(
                app, cancel, self.library.data_dir / "release-cache", force=force, cheap=True
            )
            return TaskResult("Release checked.", release_infos={app.id: info})

        return operation

    def checkable_app(self, app_id: str) -> App | None:
        if app_id not in self.installed:
            return None
        return next((app for app in self.apps if app.id == app_id), None)

    def release_check_started(self, app_id: str) -> None:
        if self.task is None:
            app = next(app for app in self.apps if app.id == app_id)
            self.footer.setText(f"Checking releases for {app.name}…")
        self.refresh()

    def release_check_done(
        self, app_id: str, variant: str, result: TaskResult | None, error: str
    ) -> None:
        if result is None:
            app = next((app for app in self.apps if app.id == app_id), None)
            if app and app.build_variant == variant:
                self.release_errors[app.id] = error
            return
        for app in self.apps:
            info = result.release_infos.get(app.id)
            if info is not None:
                if info.build_variant != app.build_variant:
                    self.checks.add([app.id])
                    continue
                self.release_infos[app.id] = info
                self.release_errors.pop(app.id, None)
                self.apply_release_info(app)

    def release_checks_drained(self) -> None:
        if self.close_after_release:
            self.close()
            return
        self.refresh()
        if self.notify_pending:
            self.notify_pending = False
            self.notify_updates()
        if self.task is None:
            self.footer.setText(
                "Update check finished."
                + (
                    " Some checks failed; rows keep previously known information."
                    if any(app_id in self.installed for app_id in self.release_errors)
                    else ""
                )
            )

    def install_app(self, app_id: str) -> None:
        app = next(app for app in self.apps if app.id == app_id)
        installed = self.installed.get(app_id)

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            progress(f"Checking {app.name} release…", 0, 0)
            release = latest_release(app, cancel, self.library.data_dir / "release-cache")
            if installed and not release.newer_than(installed.version):
                return TaskResult(f"{app.name} is already up to date.", {app_id: release})
            self.library.install(
                app,
                release,
                cancel,
                lambda message, done, total: progress(f"{app.name} · {message}", done, total),
            )
            return TaskResult(
                f"{app.name} v{release.version} is ready to launch.", {app_id: release}
            )

        self.start_task(
            f"Preparing {app.name}…",
            operation,
            working={app_id: "Updating" if installed else "Installing"},
        )

    def update_all(self) -> None:
        apps = [
            app
            for app in self.apps
            if self.has_update(app.id)
            and app.id in self.releases
            and self.installed[app.id].managed
        ]

        versions = {app.id: self.installed[app.id].version for app in apps}

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            result = TaskResult("Updates finished.")
            updated: list[str] = []
            for app in apps:
                check_cancel(cancel)
                progress(f"Checking {app.name}…", 0, 0)
                try:
                    release = latest_release(app, cancel, self.library.data_dir / "release-cache")
                    if not release.newer_than(versions[app.id]):
                        result.releases[app.id] = release
                        continue
                    self.library.install(
                        app,
                        release,
                        cancel,
                        lambda message, done, total: progress(
                            f"{app.name} · {message}", done, total
                        ),
                    )
                    result.releases[app.id] = release
                    updated.append(app.id)
                except BuildChoiceRequired as error:
                    result.build_choices[app.id] = error
                except (OSError, ValueError) as error:
                    result.errors[app.id] = str(error)
            result.message = f"Updated {len(updated)} apps."
            return result

        if apps:
            self.start_task(
                "Updating installed apps…",
                operation,
                working={app.id: "Updating" for app in apps},
            )

    def rollback_app(self, app_id: str) -> None:
        app = next(app for app in self.apps if app.id == app_id)

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            check_cancel(cancel)
            self.library.rollback(app)
            return TaskResult(f"Restored the previous {app.name} version.")

        self.start_task(f"Restoring {app.name}…", operation)

    def remove_app(self, app_id: str) -> None:
        app = next(app for app in self.apps if app.id == app_id)
        answer = QMessageBox.question(
            self,
            f"Remove {app.name}?",
            "Remove this app and its previous version from AppGlider?\n"
            "Your documents and application settings will be kept.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            check_cancel(cancel)
            self.library.remove(app)
            return TaskResult(f"Removed the managed {app.name} installation.")

        self.start_task(f"Removing {app.name}…", operation)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.settings.setValue("window/geometry", self.saveGeometry())
        if (
            not self.quitting
            and self.settings.value("General/close-to-tray", True, type=bool)
            and self.tray.isVisible()
            and QSystemTrayIcon.isSystemTrayAvailable()
        ):
            self.hide()
            event.ignore()
            return
        if self.task is not None:
            answer = QMessageBox.question(
                self,
                "Operation in progress",
                "Cancel the current operation and close when it has stopped?",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.quitting = True
                self.close_after_task = True
                self.cancel_task()
            event.ignore()
            return
        if self.checks.busy:
            self.quitting = True
            self.close_after_release = True
            self.checks.stop()
            self.add_page.stop()
            self.tray.hide()
            self.hide()
            event.ignore()
            return
        self.add_page.stop()
        self.tray.hide()
        super().closeEvent(event)

    def system_theme_changed(self, scheme: object = None) -> None:
        self.apply_theme()

    def apply_theme(self) -> None:
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        theme, accent_name = appearance_choice(self.settings)
        if theme not in THEMES:
            scheme = app.styleHints().colorScheme()
            if scheme == Qt.ColorScheme.Unknown:
                # Our own palette has replaced the desktop's, so ask the style for its default.
                window = app.style().standardPalette().color(QPalette.ColorRole.Window)
                scheme = Qt.ColorScheme.Dark if window.lightness() < 128 else Qt.ColorScheme.Light
            theme = "Dark" if scheme == Qt.ColorScheme.Dark else "Light"
        tokens = THEMES[theme]
        dark = theme == "Dark"
        accents = ACCENTS.get(accent_name, ACCENTS[DEFAULT_ACCENT])
        accent = accents[0] if dark else accents[1]
        background, surface = tokens["bg"], tokens["surface"]
        foreground, muted, border = tokens["text"], tokens["muted"], tokens["line"]
        ok, warn, danger = tokens["ok"], tokens["warn"], tokens["danger"]
        raised, faint = tokens["raised"], tokens["faint"]
        surface2, held = tokens["surface2"], tokens["held"]
        DragHandle.colours = (faint, muted)
        for row in self.cards.values():
            row.handle.update()
        swatches = "\n".join(
            f'QPushButton#swatch[accent="{name}"] {{ background: {pair[0] if dark else pair[1]}; }}'
            for name, pair in ACCENTS.items()
        )
        on_accent = background if dark else "#ffffff"
        on_warn = on_accent
        palette = QPalette()
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
            palette.setColor(role, QColor(color))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(muted))
        app.setPalette(palette)
        self.check_button.setIcon(refresh_icon(foreground))
        for button, kind in (
            (self.nav_library, "library"),
            (self.nav_updates, "updates"),
            (self.nav_add, "add"),
            (self.nav_settings, "settings"),
        ):
            button.setIcon(nav_icon(kind, muted, foreground, faint))
        app.setStyleSheet(f"""
            QWidget {{ font-size: 14px; }}
            QMainWindow, QDialog {{ background: {background}; }}
            QLabel {{ background: transparent; }}
            QLabel#pageTitle {{ font-size: 20px; font-weight: 700; }}
            QLabel#welcomeTitle {{ font-size: 27px; font-weight: 650; }}
            QLabel#sectionTitle {{ font-size: 18px; font-weight: 650; }}
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
            QLabel#chip[state="failed"] {{ color: {danger}; background: {tint(danger, 0.14)}; }}
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
            QLabel#pinStar {{ color: {PIN_COLOUR}; font-size: 15px; }}
            QPushButton#appName {{ border: none; padding: 0; text-align: left;
                font-size: 14px; font-weight: 600; background: transparent; }}
            QPushButton#appName:focus {{ border: 2px solid {accent}; border-radius: 6px; }}
            QToolButton#rowMenu {{ border: 2px solid transparent; background: transparent;
                font-size: 20px; padding: 0; }}
            QToolButton#rowMenu:focus {{ border: 2px solid {accent}; }}
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
            QComboBox::down-arrow {{ image: url({chevron_path(foreground)});
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
        """)
