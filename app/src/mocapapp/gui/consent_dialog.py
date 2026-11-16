"""Study details for the participation consent forms, asked once per session and reused for every participant."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from mocapapp.core.session import StudyInfo
from mocapapp.gui.common import para


def _text_box(value: str, placeholder: str, height: int = 54) -> QPlainTextEdit:
    box = QPlainTextEdit(value)
    box.setPlaceholderText(placeholder)
    box.setFixedHeight(height)
    return box


class ConsentInfoDialog(QDialog):
    """Fill in or leave blank; blank details get lines on the printed form, to fill in by hand."""

    def __init__(self, parent, info: StudyInfo, code: str):
        super().__init__(parent)
        self.setWindowTitle("Participation consent forms" if " " in code else f"Participation consent form - {code}")
        self.resize(620, 640)
        layout = QVBoxLayout(self)
        layout.addWidget(para("These details are printed on the form. Leave a field blank to get a line to write on "
                              "instead. They are kept for the next forms of this session; only the pseudonym changes."))

        study = QFormLayout()
        self.study_name = QLineEdit(info.study_name)
        self.purpose = _text_box(info.purpose, "What the study investigates and why")
        study.addRow("Study name", self.study_name)
        study.addRow("Purpose", self.purpose)
        layout.addLayout(study)

        people = QGroupBox("Researchers")
        pl = QVBoxLayout(people)
        self.researchers = QTableWidget(0, 2)
        self.researchers.setHorizontalHeaderLabels(["Name", "Contact"])
        self.researchers.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.researchers.verticalHeader().setVisible(False)
        self.researchers.setFixedHeight(110)
        for name, contact in info.researchers or [("", "")]:
            self.add_researcher(name, contact)
        pl.addWidget(self.researchers)
        row = QHBoxLayout()
        add, remove = QPushButton("Add researcher"), QPushButton("Remove last researcher")
        add.clicked.connect(lambda: self.add_researcher())
        remove.clicked.connect(self._remove_researcher)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        pl.addLayout(row)
        layout.addWidget(people)

        handling = QGroupBox("Data handling")
        hl = QFormLayout(handling)
        self.storage_place = QLineEdit(info.storage_place)
        self.storage_place.setPlaceholderText("e.g. the university's research data server")
        self.access = QLineEdit(info.access)
        self.access.setPlaceholderText("e.g. the researchers named above")
        self.usage = QLineEdit(info.usage)
        self.usage.setPlaceholderText("e.g. only for this study and its publications")
        self.retention = QLineEdit(info.retention)
        self.retention.setPlaceholderText("e.g. until 31 December 2031")
        hl.addRow("Place of storage", self.storage_place)
        hl.addRow("Access", self.access)
        hl.addRow("Usage", self.usage)
        hl.addRow("Retention", self.retention)
        layout.addWidget(handling)

        other = QFormLayout()
        self.risks = _text_box(info.risks, "e.g. video recordings carry a privacy risk; the movements may cause "
                                           "physical effort or mild discomfort")
        self.compensation = QLineEdit(info.compensation)
        self.compensation.setPlaceholderText("e.g. none, or a gift card")
        self.pseudonym = QLineEdit(code)
        self.pseudonym.setReadOnly(True)
        self.pseudonym.setToolTip("Filled in automatically for each participant.")
        other.addRow("Risks", self.risks)
        other.addRow("Compensation", self.compensation)
        other.addRow("Pseudonym", self.pseudonym)
        layout.addLayout(other)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Save PDF ...")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def add_researcher(self, name: str = "", contact: str = "") -> None:
        r = self.researchers.rowCount()
        self.researchers.insertRow(r)
        self.researchers.setItem(r, 0, QTableWidgetItem(name))
        self.researchers.setItem(r, 1, QTableWidgetItem(contact))

    def _remove_researcher(self) -> None:
        if self.researchers.rowCount() > 1:
            self.researchers.removeRow(self.researchers.rowCount() - 1)

    def info(self) -> StudyInfo:
        def cell(r: int, c: int) -> str:
            item = self.researchers.item(r, c)
            return item.text().strip() if item else ""

        people = [(cell(r, 0), cell(r, 1)) for r in range(self.researchers.rowCount())]
        return StudyInfo(study_name=self.study_name.text().strip(), purpose=self.purpose.toPlainText().strip(),
                         researchers=[p for p in people if p[0] or p[1]],
                         storage_place=self.storage_place.text().strip(), access=self.access.text().strip(),
                         usage=self.usage.text().strip(), retention=self.retention.text().strip(),
                         risks=self.risks.toPlainText().strip(), compensation=self.compensation.text().strip())
