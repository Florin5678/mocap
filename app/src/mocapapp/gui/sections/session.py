"""Session section (PROJECT.md §16.3): with nothing open, start a project (new, open, sample data); with a project
open, its overview: the first steps still to do, its trials and the Session settings."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core import readiness
from mocapapp.core.texts import s
from mocapapp.core.workflow import MAX_TRIALS, create_project
from mocapapp.gui.common import clear_table, error_box, fit_rows, last_output_root, note, para, readonly
from mocapapp.gui.main_window import Section

TITLES = {"session": "Session", "participants": "Participants", "cameras": "Cameras", "layouts": "Layouts"}


def scroll(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    return area


class SessionSection(Section):
    key, title = "session", "Session"
    purpose = "The project: what is still to do, its trials and its settings."

    def __init__(self, win):
        super().__init__(win)
        self.modes = QStackedWidget()
        self.modes.addWidget(scroll(self._start_panel()))
        self.modes.addWidget(scroll(self._overview()))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.modes)

    # ---------- nothing open: start a project ----------

    def _start_panel(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        new = QGroupBox("New project")
        nl = QVBoxLayout(new)
        form = QFormLayout()
        out_row = QHBoxLayout()
        self.out_edit = QLineEdit(last_output_root())
        self.out_edit.setPlaceholderText("Folder where project folders are created (no letters like æ ø å)")
        self.out_edit.setToolTip("Starts as the output folder chosen in Help > Settings.")
        browse = QPushButton("Browse ...")
        browse.clicked.connect(self._browse)
        out_row.addWidget(self.out_edit, 1)
        out_row.addWidget(browse)
        form.addRow("Output folder", out_row)
        self.study_edit = QLineEdit()
        self.study_edit.setMaxLength(8)
        self.study_edit.setPlaceholderText("optional, e.g. PA26 (letters and digits)")
        form.addRow("Study code", self.study_edit)
        self.trials_spin = QSpinBox()
        self.trials_spin.setRange(1, MAX_TRIALS)
        self.trials_spin.setToolTip("How many trials a new participant usually has. Each participant can have more "
                                    "or fewer.")
        form.addRow("Trials per participant", self.trials_spin)
        nl.addLayout(form)
        nl.addWidget(note("The project starts empty: add its cameras, layouts, participants and trials in their "
                          "sections, in any order. Cameras and layouts can also be imported from another project."))
        self.create_button = QPushButton("Create project")
        self.create_button.clicked.connect(self._create)
        row = QHBoxLayout()
        row.addWidget(self.create_button)
        row.addStretch()
        nl.addLayout(row)
        layout.addWidget(new)
        other = QHBoxLayout()
        open_button = QPushButton("Open a project ...")
        open_button.clicked.connect(lambda: self.win.open_project_dialog())
        sample = QPushButton("Try with sample data ...")
        sample.clicked.connect(lambda: self.win.make_sample())
        other.addWidget(open_button)
        other.addWidget(sample)
        other.addStretch()
        layout.addLayout(other)
        layout.addStretch()
        return w

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Output folder", self.out_edit.text() or str(Path.home()))
        if folder:
            self.out_edit.setText(folder)

    def _create(self) -> None:
        out = self.out_edit.text().strip()
        if not out:
            error_box(self, "Project not created", "Choose the output folder where project folders are created.")
            return
        try:
            root, session = create_project(Path(out), self.study_edit.text(), self.trials_spin.value())
        except ValueError as exc:  # SetupError, or a study code that is not letters and digits
            error_box(self, "Project not created", str(exc))
            return
        self.win.set_session(root, session)
        self.win.go("session")

    # ---------- a project open: its overview ----------

    def _overview(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        top = QHBoxLayout()
        self.info = QLabel()
        self.info.setWordWrap(True)
        top.addWidget(self.info, 1)
        settings_button = QPushButton("Session settings ...")
        settings_button.setToolTip("Output folder, study code, trials per participant, notes, processing settings "
                                   "and deletion of raw footage.")
        settings_button.clicked.connect(lambda: self.win.show_session_settings())
        top.addWidget(settings_button)
        layout.addLayout(top)
        layout.addWidget(note(s("sync_reminder"), "important"))
        self.first_steps = QGroupBox("First steps")
        fl = QVBoxLayout(self.first_steps)
        self.checklist = QLabel()
        self.checklist.setWordWrap(True)
        self.checklist.setTextFormat(Qt.TextFormat.RichText)
        self.checklist.linkActivated.connect(lambda key: self.win.go(key))
        fl.addWidget(self.checklist)
        layout.addWidget(self.first_steps)
        trials = QGroupBox("Trials")
        tl = QVBoxLayout(trials)
        self.trials = QTableWidget(0, 6)
        self.trials.setHorizontalHeaderLabels(["Trial", "Participant", "Name", "Layout", "Status", "Still needed"])
        self.trials.horizontalHeader().setStretchLastSection(True)
        self.trials.verticalHeader().setVisible(False)
        self.trials.setWordWrap(True)
        tl.addWidget(self.trials)
        tl.addWidget(para("Trials are added, checked and run with the command line for now "
                          "(<code>mocapapp trial add</code>, <code>mocapapp trial check</code>, <code>mocapapp "
                          "run</code>); this list follows them."))
        layout.addWidget(trials)
        layout.addStretch()
        return w

    def refresh(self) -> None:
        if self.session is None:
            self.modes.setCurrentIndex(0)
            self.out_edit.setText(self.out_edit.text() or last_output_root())
            return
        self.modes.setCurrentIndex(1)
        ses = self.session
        sample = " <b>(sample data)</b>" if ses.calibration_source == "imported" else ""
        notes = f"<br><b>Notes:</b> {html.escape(ses.notes)}" if ses.notes.strip() else ""
        self.info.setText(f"<b>Project folder:</b> {html.escape(str(self.root))}{sample}<br><b>Study code:</b> "
                          f"{html.escape(ses.study_code or '-')} &nbsp; <b>Trials per participant:</b> "
                          f"{ses.trials_per_participant}{notes}")
        steps = readiness.checklist(ses, self.root)
        self.first_steps.setVisible(not all(done for _, _, done in steps))
        self.checklist.setText("<br>".join(
            f"✓ {html.escape(text)}" if done else
            f"&#9675; <a href='{key}'>{html.escape(text)}</a> ({TITLES[key]})" for key, text, done in steps))
        clear_table(self.trials)
        for t in ses.trials:
            r = self.trials.rowCount()
            self.trials.insertRow(r)
            lay = ses.trial_layout(t.label)
            status = readiness.trial_status(ses, self.root, t.label)
            missing = readiness.trial_missing(ses, self.root, t.label) if status == "not ready" else []
            for c, text in enumerate([t.label, t.code, t.name, lay.name if lay else (t.layout or "-"), status,
                                      " ".join(missing)]):
                self.trials.setItem(r, c, readonly(text))
        self.trials.resizeColumnsToContents()
        fit_rows(self.trials, max_rows=12)
