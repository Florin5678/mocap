"""Trials table columns (0.1.0rc17): which participant details are asked for every trial, and custom ones."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core.workflow import SetupError, add_detail, remove_detail, set_detail_enabled
from mocapapp.gui.common import confirm, error_box, para


def detail_title(d) -> str:
    return f"{d.label} ({d.unit})" if d.unit else d.label


class DetailDialog(QDialog):
    """A new participant detail: a name, and whether it is a number (with a unit), a choice or free text."""

    KINDS = (("number", "Number"), ("choice", "Choice"), ("text", "Text"))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add a custom column")
        form = QFormLayout(self)
        self.name = QLineEdit()
        self.name.setMaxLength(40)
        self.name.setPlaceholderText("for example Leg length")
        self.kind = QComboBox()
        for key, title in self.KINDS:
            self.kind.addItem(title, key)
        self.unit = QLineEdit()
        self.unit.setMaxLength(12)
        self.unit.setPlaceholderText("for example cm")
        self.options = QLineEdit()
        self.options.setPlaceholderText("for example left, right")
        form.addRow("Name", self.name)
        form.addRow("Kind", self.kind)
        form.addRow("Unit", self.unit)
        form.addRow("Options", self.options)
        form.addRow(para("<i>Never ask for names or other details that identify the person.</i>"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()

    def _kind_changed(self) -> None:
        kind = self.kind.currentData()
        self.unit.setEnabled(kind == "number")
        self.options.setEnabled(kind == "choice")

    def values(self) -> tuple[str, str, str, list[str]]:
        return (self.name.text(), self.kind.currentData(), self.unit.text(),
                [o.strip() for o in self.options.text().split(",") if o.strip()])


class ColumnsDialog(QDialog):
    """Edit columns: tick the details asked for every trial; add or remove custom ones. Applied straight away."""

    def __init__(self, parent, session, root):
        super().__init__(parent)
        self.session, self.root = session, root
        self.setWindowTitle("Edit columns")
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        layout.addWidget(para("Pseudonym and height are always shown. Tick the other details to ask for every trial; "
                              "unticking hides a column and keeps the values already entered."))
        self.list = QWidget()
        self.list_layout = QVBoxLayout(self.list)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.list)
        row = QHBoxLayout()
        self.add_button = QPushButton("Add custom column ...")
        self.add_button.setToolTip("A detail of your own, for example leg length or dominant leg.")
        self.add_button.clicked.connect(self.add_custom)
        row.addWidget(self.add_button)
        row.addStretch()
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)
        self.boxes: dict[str, QCheckBox] = {}
        self._fill()

    def _fill(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.boxes = {}
        for d in self.session.details:
            if d.key == "height":
                continue
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            box = QCheckBox(detail_title(d) + ("" if d.builtin else "  (custom)"))
            box.setChecked(d.enabled)
            kind = {"number": "a number", "choice": " / ".join(d.choices), "text": "text"}[d.kind]
            box.setToolTip(kind)
            box.toggled.connect(lambda on, key=d.key: self._toggled(key, on))
            rl.addWidget(box)
            if not d.builtin:
                remove = QToolButton()
                remove.setText("Remove")
                remove.setToolTip("Removes this column and the values entered in it.")
                remove.clicked.connect(lambda _=False, key=d.key: self.remove_custom(key))
                rl.addWidget(remove)
            rl.addStretch()
            self.list_layout.addWidget(row)
            self.boxes[d.key] = box

    def _toggled(self, key: str, on: bool) -> None:
        set_detail_enabled(self.session, self.root, key, on)

    def add_custom(self) -> None:
        dialog = DetailDialog(self)
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                add_detail(self.session, self.root, *dialog.values())
            except SetupError as exc:
                error_box(self, "Column not added", str(exc))
                continue
            self._fill()
            return

    def remove_custom(self, key: str) -> None:
        d = self.session.detail(key)
        filled = sum(1 for t in self.session.trials if t.details.get(key))
        if filled and not confirm(self, "Remove column", f"Remove '{d.label}' and the values entered for {filled} "
                                  "trial(s)?", "Remove"):
            return
        remove_detail(self.session, self.root, key)
        self._fill()
