"""The dialog that shows icons found in a repository so one can be picked by sight."""

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

ICON_SIZE = 64


class IconPickerDialog(QDialog):
    """Show each candidate as its picture with the file name under it."""

    def __init__(self, icons: list[tuple[str, bytes]], parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose an icon")
        layout = QVBoxLayout(self)
        label = QLabel("Icons found in the repository. Choose the one to use.")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.icons = QListWidget()
        self.icons.setAccessibleName("Icons found in the repository")
        self.icons.setViewMode(QListView.ViewMode.IconMode)
        self.icons.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.icons.setGridSize(QSize(ICON_SIZE + 64, ICON_SIZE + 44))
        self.icons.setResizeMode(QListView.ResizeMode.Adjust)
        self.icons.setMovement(QListView.Movement.Static)
        self.icons.setWordWrap(True)
        self.icons.setSpacing(4)
        for name, png in icons:
            pixmap = QPixmap()
            pixmap.loadFromData(png)
            item = QListWidgetItem(QIcon(pixmap), Path(name).name)
            item.setToolTip(name)
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            self.icons.addItem(item)
        self.icons.setCurrentRow(0)
        self.icons.itemActivated.connect(lambda _item: self.accept())
        layout.addWidget(self.icons, stretch=1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.use_button = buttons.addButton("Use icon", QDialogButtonBox.ButtonRole.AcceptRole)
        self.use_button.setObjectName("primary")
        self.use_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(4 * (ICON_SIZE + 64) + 48, ICON_SIZE + 44 + 130)

    def chosen(self) -> int | None:
        row = self.icons.currentRow()
        return row if row >= 0 else None


def choose_icon_candidate(parent: QWidget, icons: list[tuple[str, bytes]]) -> int | None:
    """Ask which icon to use; return its index, or None when cancelled."""
    dialog = IconPickerDialog(icons, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.chosen()
