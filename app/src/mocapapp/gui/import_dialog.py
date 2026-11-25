"""Import cameras, reference-point presets and layouts from another project (R3, PROJECT.md §16.5). The other
project is only read; participants, trials and results are never copied."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core import importing
from mocapapp.core.session import Session
from mocapapp.gui.common import error_box, note, para


class ImportDialog(QDialog):
    def __init__(self, parent: QWidget | None, session: Session, root: Path):
        super().__init__(parent)
        self.session, self.root = session, root
        self.source: importing.Source | None = None
        self.imported: importing.Imported | None = None
        self.setWindowTitle("Import from another project")
        self.resize(640, 520)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setPlaceholderText("the other project's file (.mocapproj) or folder")
        self.path.editingFinished.connect(lambda: self.load(Path(self.path.text().strip())))
        browse = QPushButton("Choose project ...")
        browse.clicked.connect(self._browse)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        layout.addLayout(row)
        layout.addWidget(para("Tick what to copy into this project. A layout brings its cameras, reference points, "
                              "camera positions and videos along; a camera its lens calibration. Copies are new "
                              "items here: nothing is merged with what this project already has."))
        self.items = QListWidget()
        layout.addWidget(self.items, 1)
        layout.addWidget(note("Participants, trials and results are never copied."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText("Import")
        self.ok.setEnabled(False)
        buttons.accepted.connect(self._import)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        from mocapapp.core.project import SUFFIX

        name, _ = QFileDialog.getOpenFileName(self, "The other project", str(Path(self.root).parent),
                                              f"mocapapp project (*{SUFFIX});;Project file (session.json)")
        if name:
            path = Path(name)
            self.load(path.parent if path.name == "session.json" else path)

    def load(self, path: Path) -> bool:
        self.items.clear()
        self.ok.setEnabled(False)
        if not str(path).strip() or str(path) == ".":
            return False
        self.path.setText(str(path))
        try:
            src = importing.open_source(path)
        except (importing.ImportError_, OSError, ValueError) as exc:
            error_box(self, "Cannot import from there", str(exc))
            return False
        if Path(src.root).resolve() == Path(self.root).resolve():
            error_box(self, "Cannot import from there", "That is this project.")
            return False
        self.source = src
        o = src.session
        for kind, things in (("layout", [(x.id, f"Layout {x.name} ({len(x.cameras)} cameras)") for x in o.layouts]),
                             ("camera", [(c.id, f"Camera {c.name} ({c.id})") for c in o.cameras]),
                             ("preset", [(p.id, f"Reference points {p.name}") for p in o.presets])):
            for ident, text in things:
                item = QListWidgetItem(text)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                item.setData(Qt.ItemDataRole.UserRole, (kind, ident))
                self.items.addItem(item)
        if not self.items.count():
            self.items.addItem("That project has no cameras, layouts or reference points.")
        self.ok.setEnabled(self.items.count() > 0)
        return True

    def ticked(self, kind: str) -> list[str]:
        out = []
        for i in range(self.items.count()):
            item = self.items.item(i)
            data = item.data(Qt.ItemDataRole.UserRole)
            if data and data[0] == kind and item.checkState() == Qt.CheckState.Checked:
                out.append(data[1])
        return out

    def _import(self) -> None:
        if self.source is None:
            return
        cams, presets, layouts = self.ticked("camera"), self.ticked("preset"), self.ticked("layout")
        if not (cams or presets or layouts):
            error_box(self, "Nothing ticked", "Tick the cameras, layouts or reference points to import.")
            return
        try:
            self.imported = importing.import_project(self.session, self.root, self.source.root, cams, presets,
                                                     layouts)
        except (importing.ImportError_, OSError, ValueError) as exc:
            error_box(self, "Not imported", str(exc))
            return
        QMessageBox.information(self, "Imported", "\n".join(self.imported.notes) or "Imported.")
        self.accept()
