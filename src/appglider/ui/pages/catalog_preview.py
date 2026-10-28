"""The confirmation shown before a catalog is imported."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from appglider.core.catalogfile import ParsedCatalog
from appglider.install.library import ImportPlan


class CatalogPreviewDialog(QDialog):
    """Show what importing a catalog would change, as plain text, before anything is saved."""

    def __init__(self, catalog: ParsedCatalog, plan: ImportPlan, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import catalog")
        self.resize(640, 480)
        layout = QVBoxLayout(self)
        title = catalog.name or "Untitled catalog"
        summary = QLabel(
            f"{title}: {len(plan.new)} to add, {len(plan.existing)} already in your library"
            + (f", {len(catalog.skipped)} skipped." if catalog.skipped else ".")
        )
        summary.setTextFormat(Qt.TextFormat.PlainText)
        summary.setWordWrap(True)
        layout.addWidget(summary)
        lines: list[str] = []
        for entry in plan.new:
            channel = "stable releases only" if entry.stable_only else "includes previews"
            lines.append(f"ADD  {entry.name}  ({entry.repo}, {channel})")
            if entry.description:
                lines.append(f"       {entry.description}")
            if entry.build:
                lines.append(f"       build: {entry.build}")
        if plan.existing:
            lines += ["", "ALREADY IN YOUR LIBRARY (left unchanged)"]
            lines += [f"  {entry.name}  ({entry.repo})" for entry in plan.existing]
        if catalog.skipped:
            lines += ["", "SKIPPED (not valid)"] + [f"  {problem}" for problem in catalog.skipped]
        if catalog.ignored:
            lines += ["", "IGNORED KEYS (not used by this version)"]
            lines += [f"  {key}" for key in catalog.ignored]
        self.details = QPlainTextEdit("\n".join(lines))
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Catalog contents")
        layout.addWidget(self.details, stretch=1)
        note = QLabel("Nothing is installed. Apps are added to your library only.")
        note.setObjectName("muted")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.add_button = buttons.addButton(
            f"Add {len(plan.new)} {'app' if len(plan.new) == 1 else 'apps'}",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self.add_button.setObjectName("primary")
        self.add_button.setEnabled(bool(plan.new))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
