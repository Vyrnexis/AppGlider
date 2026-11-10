import unittest

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QWidget

from appglider.ui.pages.icon_picker import IconPickerDialog, choose_icon_candidate


def png(colour: str) -> bytes:
    image = QImage(32, 32, QImage.Format.Format_ARGB32)
    image.fill(QColor(colour))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data.data())


ICONS = [("assets/red.png", png("#ff0000")), ("logo/blue.png", png("#0000ff"))]


class IconPickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)

    def test_each_icon_is_shown_as_a_picture_with_its_file_name(self) -> None:
        dialog = IconPickerDialog(ICONS, self.parent)
        self.addCleanup(dialog.deleteLater)
        items = [dialog.icons.item(row) for row in range(dialog.icons.count())]
        self.assertEqual([item.text() for item in items], ["red.png", "blue.png"])
        self.assertEqual(items[1].toolTip(), "logo/blue.png")
        pixmap = items[1].icon().pixmap(32, 32)
        self.assertEqual(pixmap.toImage().pixelColor(16, 16).name(), "#0000ff")
        self.assertEqual(dialog.chosen(), 0)  # the first icon is selected

    def test_choosing_returns_the_index_and_cancel_returns_none(self) -> None:
        def pick_second() -> None:
            dialog = QApplication.activeModalWidget()
            assert isinstance(dialog, IconPickerDialog)
            dialog.icons.setCurrentRow(1)
            dialog.use_button.click()

        QTimer.singleShot(0, pick_second)
        self.assertEqual(choose_icon_candidate(self.parent, ICONS), 1)

        def cancel() -> None:
            dialog = QApplication.activeModalWidget()
            assert isinstance(dialog, IconPickerDialog)
            dialog.reject()

        QTimer.singleShot(0, cancel)
        self.assertIsNone(choose_icon_candidate(self.parent, ICONS))


if __name__ == "__main__":
    unittest.main()
