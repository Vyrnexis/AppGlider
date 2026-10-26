"""Library row widgets: status chips, drag reordering and compact application cards."""

from pathlib import Path

from PySide6.QtCore import (
    QByteArray,
    QEvent,
    QMimeData,
    QPoint,
    QPointF,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QDrag,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from appglider.app_status import app_status
from appglider.catalog import App
from appglider.controls import repolish
from appglider.icons import icon_pixmap
from appglider.library import Installed
from appglider.release_info import ReleaseInfo
from appglider.releases import Release


def icon_label(app: App, size: int, path: Path | None = None) -> QLabel:
    label = QLabel()
    label.setFixedSize(size, size)
    label.setPixmap(icon_pixmap(path or app.icon, size))
    label.setAccessibleName(f"{app.name} icon")
    return label


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
