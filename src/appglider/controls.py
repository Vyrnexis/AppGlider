"""Small reusable controls: switch, segmented choice and space bars."""

from PySide6.QtCore import Property, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPaintEvent, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QWidget,
)


def repolish(widget: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property or object name changed."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class Switch(QAbstractButton):
    """A yes/no control. Space toggles it; the caller sets the accessible name.

    The colours come from the stylesheet (`qproperty-trackOn` and friends) and fall back to
    the palette, so no colour is written here.
    """

    TRACK = QSize(40, 22)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("switch")
        self.setFixedSize(self.TRACK + QSize(6, 6))
        self._track_on = QColor()
        self._track_off = QColor()
        self._knob = QColor()
        self._ring = QColor()

    def sizeHint(self) -> QSize:
        return self.TRACK + QSize(6, 6)

    def get_track_on(self) -> QColor:
        return self._track_on

    def set_track_on(self, colour: QColor) -> None:
        self._track_on = colour
        self.update()

    def get_track_off(self) -> QColor:
        return self._track_off

    def set_track_off(self, colour: QColor) -> None:
        self._track_off = colour
        self.update()

    def get_knob(self) -> QColor:
        return self._knob

    def set_knob(self, colour: QColor) -> None:
        self._knob = colour
        self.update()

    def get_ring(self) -> QColor:
        return self._ring

    def set_ring(self, colour: QColor) -> None:
        self._ring = colour
        self.update()

    trackOn = Property(QColor, get_track_on, set_track_on)  # noqa: N815
    trackOff = Property(QColor, get_track_off, set_track_off)  # noqa: N815
    knob = Property(QColor, get_knob, set_knob)
    ring = Property(QColor, get_ring, set_ring)

    def paintEvent(self, event: QPaintEvent) -> None:
        palette = self.palette()
        on = (
            self._track_on
            if self._track_on.isValid()
            else palette.color(QPalette.ColorRole.Highlight)
        )
        off = (
            self._track_off if self._track_off.isValid() else palette.color(QPalette.ColorRole.Mid)
        )
        knob = self._knob if self._knob.isValid() else palette.color(QPalette.ColorRole.Window)
        ring = self._ring if self._ring.isValid() else palette.color(QPalette.ColorRole.Highlight)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if not self.isEnabled():
            painter.setOpacity(0.5)
        track = QRectF(3, 3, self.TRACK.width(), self.TRACK.height())
        painter.setBrush(on if self.isChecked() else off)
        radius = track.height() / 2
        painter.drawRoundedRect(track, radius, radius)
        diameter = track.height() - 6
        left = track.right() - diameter - 3 if self.isChecked() else track.left() + 3
        painter.setBrush(
            knob if self.isChecked() else palette.color(QPalette.ColorRole.PlaceholderText)
        )
        painter.drawEllipse(QRectF(left, track.top() + 3, diameter, diameter))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(ring, 2))
            outline = track.adjusted(-2, -2, 2, 2)
            painter.drawRoundedRect(outline, outline.height() / 2, outline.height() / 2)


def paint_space_segments(
    painter: QPainter,
    rect: QRectF,
    in_use: int,
    kept: int,
    scale: int,
    accent: QColor,
) -> None:
    """Draw in-use (solid accent) and kept (accent stripes with 1px outline) segments."""
    total_bytes = in_use + kept
    if scale <= 0 or total_bytes <= 0:
        return
    width = rect.width()
    height = rect.height()
    radius = 3.0
    total_w = width * min(1.0, total_bytes / scale)
    if total_w <= 0:
        return

    in_use_rect: QRectF | None = None
    kept_rect: QRectF | None = None

    if in_use > 0 and kept > 0:
        bar_w = max(0.0, total_w - 2.0)
        w_in_use = bar_w * (in_use / total_bytes)
        if bar_w >= 4.0:
            w_in_use = max(2.0, min(bar_w - 2.0, w_in_use))
        w_kept = bar_w - w_in_use
        in_use_rect = QRectF(rect.x(), rect.y(), w_in_use, height)
        kept_rect = QRectF(rect.x() + w_in_use + 2.0, rect.y(), w_kept, height)
    elif in_use > 0:
        in_use_rect = QRectF(rect.x(), rect.y(), total_w, height)
    elif kept > 0:
        kept_rect = QRectF(rect.x(), rect.y(), total_w, height)

    if in_use_rect is not None and in_use_rect.width() > 0:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(accent))
        r = min(radius, in_use_rect.width() / 2, in_use_rect.height() / 2)
        painter.drawRoundedRect(in_use_rect, r, r)

    if kept_rect is not None and kept_rect.width() > 0:
        outline = QColor(accent)
        outline.setAlphaF(0.4)
        pen = QPen(outline, 1.0)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        brush = QBrush(accent, Qt.BrushStyle.BDiagPattern)
        painter.setPen(pen)
        painter.setBrush(brush)
        adjusted_rect = kept_rect.adjusted(0.5, 0.5, -0.5, -0.5)
        r = min(radius, adjusted_rect.width() / 2, adjusted_rect.height() / 2)
        painter.drawRoundedRect(adjusted_rect, r, r)


class SpaceBar(QWidget):
    """A bar split into in-use and roll-back kept bytes, painted without a background track.

    The colours come from the stylesheet (`qproperty-accent`) and fall back to the
    palette highlight colour.
    """

    def __init__(
        self,
        in_use: int = 0,
        kept: int = 0,
        scale: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("spaceBar")
        self._in_use = max(0, in_use)
        self._kept = max(0, kept)
        self._scale = max(0, scale)
        self._accent = QColor()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(100, 8)

    def get_accent(self) -> QColor:
        return self._accent

    def set_accent(self, colour: QColor) -> None:
        self._accent = colour
        self.update()

    accent = Property(QColor, get_accent, set_accent)

    @property
    def in_use(self) -> int:
        return self._in_use

    @property
    def kept(self) -> int:
        return self._kept

    @property
    def scale(self) -> int:
        return self._scale

    def set_values(self, in_use: int, kept: int, scale: int) -> None:
        self._in_use = max(0, in_use)
        self._kept = max(0, kept)
        self._scale = max(0, scale)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        accent = (
            self._accent
            if self._accent.isValid()
            else self.palette().color(QPalette.ColorRole.Highlight)
        )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, float(self.width()), float(self.height()))
        paint_space_segments(painter, rect, self._in_use, self._kept, self._scale, accent)


class SpaceSwatch(QWidget):
    """A legend swatch showing one segment style (in-use solid or kept stripes)."""

    def __init__(self, kind: str = "in_use", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("spaceSwatch")
        self.kind = kind
        self._accent = QColor()
        self.setFixedSize(12, 12)

    def sizeHint(self) -> QSize:
        return QSize(12, 12)

    def get_accent(self) -> QColor:
        return self._accent

    def set_accent(self, colour: QColor) -> None:
        self._accent = colour
        self.update()

    accent = Property(QColor, get_accent, set_accent)

    def paintEvent(self, event: QPaintEvent) -> None:
        accent = (
            self._accent
            if self._accent.isValid()
            else self.palette().color(QPalette.ColorRole.Highlight)
        )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, float(self.width()), float(self.height()))
        in_use = 1 if self.kind == "in_use" else 0
        kept = 1 if self.kind == "kept" else 0
        paint_space_segments(painter, rect, in_use, kept, 1, accent)


class Segmented(QFrame):
    """Two to four exclusive choices side by side. Emits `currentIndexChanged` on user choice."""

    currentIndexChanged = Signal(int)  # noqa: N815

    def __init__(self, labels: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("segmented")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: list[QPushButton] = []
        for index, label in enumerate(labels):
            button = QPushButton(label)
            button.setObjectName("segmentedButton")
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.group.addButton(button, index)
            layout.addWidget(button)
            self.buttons.append(button)
        self.buttons[0].setChecked(True)
        self.group.idToggled.connect(self.toggled)

    def toggled(self, index: int, checked: bool) -> None:
        if checked:
            self.currentIndexChanged.emit(index)

    def currentIndex(self) -> int:  # noqa: N802
        return max(0, self.group.checkedId() if self.group.checkedId() != -1 else 0)

    def setCurrentIndex(self, index: int, *, notify: bool = False) -> None:  # noqa: N802
        """Choose a segment. Programmatic changes are silent unless `notify` is set."""
        was = self.blockSignals(not notify)
        try:
            self.buttons[index].setChecked(True)
        finally:
            self.blockSignals(was)
