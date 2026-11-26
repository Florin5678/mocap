"""Participants > Columns (0.2.0rc3): the participant details as an editable table, one row per detail (Height,
Weight, ...): its name, unit, type (number, text or a choice with its options) and whether it is shown and asked for
every participant. Rows are added and removed here; nothing changes until Save (`workflow.set_details`)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core.workflow import SetupError, set_details
from mocapapp.gui.common import NoWheelCombo, confirm, error_box, para, selected_rows

KINDS = (("number", "Number"), ("text", "Text"), ("choice", "Choice"))
NAME, UNIT, KIND, OPTIONS, SHOWN = range(5)
HEADERS = ["Name", "Unit", "Type", "Options (for a choice)", "Shown"]


def detail_title(d) -> str:
    return f"{d.label} ({d.unit})" if d.unit else d.label


def _centered(widget: QWidget) -> QWidget:
    holder = QWidget()
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(widget)
    return holder


class ColumnsDialog(QDialog):
    def __init__(self, parent, session, root):
        super().__init__(parent)
        self.session, self.root = session, root
        self.setWindowTitle("Participant details")
        self.resize(760, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(para("One row per detail asked for every participant. Built-in details keep their name and "
                              "type; untick <b>Shown</b> to stop asking one (the values already entered are kept). "
                              "<i>Never ask for names or other details that identify the person.</i>"))
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(OPTIONS, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._enable)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.add_button = QPushButton("Add detail")
        self.add_button.setToolTip("A detail of your own, for example leg length or dominant leg.")
        self.add_button.clicked.connect(lambda: self.add_row())
        self.remove_button = QPushButton("Remove")
        self.remove_button.setToolTip("Removes the selected details and the values entered in them (built-in ones "
                                      "can only be unticked).")
        self.remove_button.clicked.connect(self.remove_rows)
        row.addWidget(self.add_button)
        row.addWidget(self.remove_button)
        row.addStretch()
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        layout.addWidget(buttons)
        self.keys: list[str | None] = []  # per row: the detail's key (None: added here)
        for d in session.details:
            self.add_row(d)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(NAME, max(self.table.columnWidth(NAME), 160))
        self._enable()

    # ---------- rows ----------

    def add_row(self, d=None) -> int:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.keys.append(d.key if d else None)
        fixed = bool(d and d.builtin)
        name = QLineEdit(d.label if d else "")
        name.setMaxLength(40)
        name.setPlaceholderText("for example Leg length")
        unit = QLineEdit(d.unit if d else "")
        unit.setMaxLength(12)
        kind = NoWheelCombo()
        for key, title in KINDS:
            kind.addItem(title, key)
        kind.setCurrentIndex(max(0, kind.findData(d.kind if d else "number")))
        # the row is looked up when the type changes: rows above it may have been removed since
        kind.currentIndexChanged.connect(lambda _i, k=kind: self._kind_changed(self._row_of(k)))
        options = QLineEdit(", ".join(d.choices) if d else "")
        shown = QCheckBox()
        shown.setChecked(d.enabled if d else True)
        for w in (name, unit, kind, options):
            w.setEnabled(not fixed)
        if d is not None and d.key == "height":
            shown.setEnabled(False)
            shown.setToolTip("Height is always asked: it scales the body model.")
        if fixed:
            name.setToolTip("Built in: its name and type stay.")
        self.table.setCellWidget(r, NAME, name)
        self.table.setCellWidget(r, UNIT, unit)
        self.table.setCellWidget(r, KIND, kind)
        self.table.setCellWidget(r, OPTIONS, options)
        self.table.setCellWidget(r, SHOWN, _centered(shown))
        self._kind_changed(r)
        if d is None:
            name.setFocus()
            self.table.scrollToBottom()
        return r

    def widgets(self, r: int) -> tuple:
        name, unit, kind, options = (self.table.cellWidget(r, c) for c in (NAME, UNIT, KIND, OPTIONS))
        shown = self.table.cellWidget(r, SHOWN).findChild(QCheckBox)
        return name, unit, kind, options, shown

    def _row_of(self, kind) -> int:
        return next((r for r in range(self.table.rowCount()) if self.table.cellWidget(r, KIND) is kind), -1)

    def _kind_changed(self, r: int) -> None:
        if not 0 <= r < self.table.rowCount():
            return
        name, unit, kind, options, _shown = self.widgets(r)
        editable = name.isEnabled()
        unit.setEnabled(editable and kind.currentData() == "number")
        options.setEnabled(editable and kind.currentData() == "choice")
        # an example only where something can be typed: a number's unit, a choice's options
        unit.setPlaceholderText("e.g. cm" if unit.isEnabled() else "")
        options.setPlaceholderText("for example left, right" if options.isEnabled() else "")

    def _enable(self) -> None:
        rows = selected_rows(self.table)
        self.remove_button.setEnabled(bool(rows) and all(self.removable(r) for r in rows))

    def removable(self, r: int) -> bool:
        key = self.keys[r]
        return key is None or not self.session.detail(key).builtin

    def remove_rows(self) -> None:
        rows = sorted(r for r in selected_rows(self.table) if self.removable(r))
        filled = sum(1 for r in rows if self.keys[r] for t in self.session.trials if t.details.get(self.keys[r]))
        names = ", ".join(self.widgets(r)[0].text() or "(new)" for r in rows)
        if filled and not confirm(self, "Remove details", f"Remove {names} and the {filled} value(s) entered in them "
                                  "(when you save)?", "Remove"):
            return
        for r in reversed(rows):
            self.table.removeRow(r)
            del self.keys[r]
        self._enable()

    # ---------- saving ----------

    def rows(self) -> list[dict]:
        out = []
        for r in range(self.table.rowCount()):
            name, unit, kind, options, shown = self.widgets(r)
            if self.keys[r] is None and not name.text().strip():
                continue  # an added row left empty
            out.append({"key": self.keys[r], "label": name.text(), "kind": kind.currentData(), "unit": unit.text(),
                        "choices": [o.strip() for o in options.text().split(",") if o.strip()],
                        "enabled": shown.isChecked()})
        return out

    def save(self) -> None:
        try:
            set_details(self.session, self.root, self.rows())
        except SetupError as exc:
            error_box(self, "Details not saved", str(exc))
            return
        self.accept()
