"""Small reusable controls: a switch and a segmented choice, coloured by the theme tokens."""

from PySide6.QtCore import Property, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QPushButton,
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
        return max(0, self.group.checkedId())

    def setCurrentIndex(self, index: int, *, notify: bool = False) -> None:  # noqa: N802
        """Choose a segment. Programmatic changes are silent unless `notify` is set."""
        was = self.blockSignals(not notify)
        try:
            self.buttons[index].setChecked(True)
        finally:
            self.blockSignals(was)
