"""Session section, the project's hub (PROJECT.md §16.3): with nothing open, start a project (new, open, sample data);
with a project open, the first steps still to do, the trials table (R9) with its add row (R10), actions on the
selected trials, the trial panel and the run bar (R15)."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core import readiness, workflow
from mocapapp.core.texts import s
from mocapapp.core.validate import suggest_trials
from mocapapp.core.workflow import MAX_TRIALS, create_project
from mocapapp.gui.common import (
    Background,
    NoWheelCombo,
    clear_table,
    confirm,
    error_box,
    fit_rows,
    for_each,
    last_output_root,
    note,
    readonly,
    scroll,
    selected_rows,
    status_badge,
)
from mocapapp.gui.main_window import Section
from mocapapp.gui.sections.run_bar import RunBar
from mocapapp.gui.sections.trial_panel import TrialPanel

TITLES = {"session": "Session", "participants": "Participants", "cameras": "Cameras", "layouts": "Layouts"}
COLUMNS = ["Trial", "Name / note", "Participant", "Layout", "Date", "Status", "", "", ""]
STATUS = 5
RUNS_AGAIN = ("ready", "failed", "out of date")  # Run with nothing selected: every trial in one of these states
ERRORS = (ValueError, KeyError, OSError)
HIGHLIGHT = "QComboBox { background: #fff3cd; font-weight: bold; }"


def choose_layout(parent, session) -> str | None:
    """The layout for Change layout: its id, or None (cancelled)."""
    names = [f"{x.name} ({x.date})" if x.date else x.name for x in session.layouts]
    if not names:
        error_box(parent, "No layouts yet", "Make a layout in Layouts first.")
        return None
    name, ok = QInputDialog.getItem(parent, "Change layout", "Layout the selected trials were recorded with:", names,
                                    0, False)
    return session.layouts[names.index(name)].id if ok else None


def clean(exc: Exception) -> str:
    return str(exc).strip("'\"")


class SessionSection(Section):
    key, title = "session", "Session"
    purpose = "The project's trials: add them, choose their videos, check and copy them, run them."

    def __init__(self, win):
        super().__init__(win)
        self.rows: list[str] = []
        self.cells: dict[str, tuple] = {}
        self.suggested: dict = {}  # (camera, trial) -> video named with the trial's number
        self.checks: dict[str, tuple] = {}  # trial -> (report, plan, error) of its last Check and copy
        self._keep: list[str] = []
        self.modes = QStackedWidget()
        self.modes.addWidget(scroll(self._start_panel()))
        hub = QWidget()
        hl = QVBoxLayout(hub)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(scroll(self._overview()), 1)
        self.bar = RunBar(self)  # R15: at the bottom, outside the scrolled area
        hl.addWidget(self.bar)
        self.modes.addWidget(hub)
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

    # ---------- a project open: its hub ----------

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
        tl.addWidget(note(s("sync_reminder"), "important"))  # R18: a reminder, never a tick
        run_row = QHBoxLayout()
        self.run_button = QPushButton("Run ready trials")
        self.run_button.setMinimumHeight(34)
        self.run_button.setToolTip("Runs the selected trials; with none selected, every trial that is ready (or "
                                   "failed, or out of date). Trials that are not ready are left out.")
        self.run_button.clicked.connect(self.run_clicked)
        self.start_over = QCheckBox("Start over (ignore finished stages)")
        self.start_over.setToolTip("Computes every stage again, also those already finished. Without it, a run "
                                   "continues where the trial stopped.")
        run_row.addWidget(self.run_button)
        run_row.addWidget(self.start_over)
        run_row.addStretch()
        tl.addLayout(run_row)
        actions = QHBoxLayout()
        self.buttons: dict[str, QPushButton] = {}
        for key, text, slot, tip in (
                ("duplicate", "Duplicate", self.duplicate, "A new trial like each selected one: same participant, "
                 "layout and name; its own videos."),
                ("layout", "Change layout ...", self.change_layout, "The layout the selected trials were recorded "
                 "with."),
                ("check", "Check and copy videos", lambda: self.check_and_copy(), "Checks the videos of the selected "
                 "trials and copies them into the project, each trial on its own (none selected: every trial whose "
                 "videos are chosen but not copied yet)."),
                ("raw", "Delete raw footage ...", self.delete_raw, "Deletes the working copies of the selected "
                 "trials' videos; the 3D results are kept. One confirmation per trial."),
                ("remove", "Remove trial ...", self.remove, "Removes the selected trials. A trial with videos or "
                 "results is deleted with its data (you type the pseudonym)."),
                ("forms", "Consent forms ...", self.forms, "One PDF with the consent forms of the selected trials' "
                 "participants (none selected: every participant)."),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            self.buttons[key] = b
            actions.addWidget(b)
        actions.addStretch()
        tl.addLayout(actions)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)  # Ctrl/Shift: several
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        tl.addWidget(self.table)

        add = QHBoxLayout()  # R10: pre-filled from the previous trial, editable before Add
        add.addWidget(QLabel("<b>Add a trial:</b> participant"))
        self.add_who = NoWheelCombo()
        self.add_who.setStyleSheet(HIGHLIGHT)
        self.add_who.setSizeAdjustPolicy(NoWheelCombo.SizeAdjustPolicy.AdjustToContents)
        self.add_who.setToolTip("Who is recorded in the new trial. Pre-filled: the previous trial's participant "
                                "while they have fewer trials than 'trials per participant', otherwise a participant "
                                "without trials, otherwise a new one.")
        add.addWidget(self.add_who)
        add.addWidget(QLabel("layout"))
        self.add_layout = NoWheelCombo()
        self.add_layout.setSizeAdjustPolicy(NoWheelCombo.SizeAdjustPolicy.AdjustToContents)
        add.addWidget(self.add_layout, 1)
        add.addWidget(QLabel("name"))
        self.add_name = QLineEdit()
        self.add_name.setPlaceholderText("e.g. walking")
        add.addWidget(self.add_name, 1)
        add.addWidget(QLabel("date"))
        self.add_date = QLineEdit()
        self.add_date.setPlaceholderText("from the videos")
        self.add_date.setToolTip("Empty: the day the videos were recorded (or today). Otherwise YYYY-MM-DD.")
        self.add_date.setMaximumWidth(110)
        add.addWidget(self.add_date)
        self.add_button = QPushButton("Add")
        self.add_button.clicked.connect(self.add)
        add.addWidget(self.add_button)
        tl.addLayout(add)
        layout.addWidget(trials)
        self.panel = TrialPanel(self)
        self.panel.setVisible(False)
        layout.addWidget(self.panel)
        layout.addStretch()
        return w

    def refresh(self) -> None:
        if self.session is None:
            self.modes.setCurrentIndex(0)
            self.out_edit.setText(self.out_edit.text() or last_output_root())
            self.checks, self.rows, self.cells = {}, [], {}
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
        try:
            self.suggested = suggest_trials(ses)
        except OSError:  # a camera folder that went away
            self.suggested = {}
        self.checks = {k: v for k, v in self.checks.items() if k in {t.label for t in ses.trials}}
        self._fill_table()
        self._fill_add_row()

    # ---------- the table ----------

    def _fill_table(self) -> None:
        ses = self.session
        keep = self._keep or self.selected()
        self._keep = []
        self.table.blockSignals(True)
        clear_table(self.table)
        self.rows = [t.label for t in ses.trials]
        self.cells = {}
        for r, t in enumerate(ses.trials):
            self.table.insertRow(r)
            lay = ses.trial_layout(t.label)
            for c, text in enumerate([t.label, t.name, t.code, lay.name if lay else (t.layout or "-"), t.date]):
                self.table.setItem(r, c, readonly(text))
            state = QLabel()
            state.setWordWrap(True)
            self.table.setCellWidget(r, STATUS, state)
            one = QPushButton("Run this trial")
            one.setToolTip("Runs (or continues) only this trial. The other trials are not touched.")
            one.clicked.connect(lambda _=False, lb=t.label: self._start([lb]))
            fix = QPushButton("Check sync ...")
            fix.setToolTip("Opens Pose2Sim's synchronisation player, where the offsets can be corrected.")
            fix.clicked.connect(lambda _=False, lb=t.label: self.bar.fix_sync(lb))
            accept = QPushButton("Accept as is")
            accept.setToolTip("Continues with the automatic synchronisation.")
            accept.clicked.connect(lambda _=False, lb=t.label: self._start([lb], accept_sync=True))
            for c, b in enumerate((one, fix, accept), STATUS + 1):
                self.table.setCellWidget(r, c, b)
            self.cells[t.label] = (state, one, fix, accept)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(STATUS, QHeaderView.ResizeMode.Stretch)  # the status takes the free width
        model, flags = self.table.selectionModel(), self.table.selectionModel().SelectionFlag
        for label in keep:
            if label in self.rows:
                model.select(self.table.model().index(self.rows.index(label), 0), flags.Select | flags.Rows)
        self.table.blockSignals(False)
        self.show_status()
        fit_rows(self.table, max_rows=12)
        self._selection_changed()

    def _status(self, label: str) -> tuple[str, str]:
        ses, root = self.session, self.root
        if self.win.busy and label in self.bar.activity:
            return "running", self.bar.activity[label]
        status = readiness.trial_status(ses, root, label)
        if status == "not ready":
            return status, " ".join(readiness.trial_missing(ses, root, label))
        rs = ses.run_state
        if status in ("failed", "needs sync fix"):
            return status, rs.errors.get(label) or "; ".join(rs.sync.get(label, {}).get("issues", []))
        return status, ""

    def runnable(self, label: str) -> bool:
        """Ready to run: nothing missing (§16.4), footage still there."""
        trial = self.session.trial(label)
        return trial.raw_present and not trial.withdrawn_at and readiness.trial_ready(self.session, self.root, label)

    def show_status(self) -> None:
        """Status column (with live run progress, R15), row buttons and the action buttons."""
        if self.session is None:
            return
        busy = self.win.busy
        labels = {t.label for t in self.session.trials}
        for label, (state, one, fix, accept) in self.cells.items():
            if label not in labels:
                continue
            status, msg = self._status(label)
            state.setText(status_badge(status) + (f" - {html.escape(msg)}" if msg else ""))
            one.setEnabled(not busy and status != "needs sync fix" and self.runnable(label))
            for b in (fix, accept):
                b.setEnabled(not busy and status == "needs sync fix")
        self._enable()

    def selected(self) -> list[str]:
        if self.session is None:
            return []
        return [self.rows[r] for r in selected_rows(self.table) if r < len(self.rows)]

    def _selection_changed(self) -> None:
        labels = self.selected()
        self._enable()
        self.panel.show_trial(labels[0] if len(labels) == 1 else None, self.suggested)

    def _enable(self) -> None:
        if self.session is None:
            return
        labels, busy = self.selected(), self.win.busy
        n = len(labels)
        open_ = [lb for lb in labels if not self.session.trial(lb).frozen]
        self.run_button.setText(f"Run selected ({n})" if n else "Run ready trials")
        self.run_button.setEnabled(not busy and bool(self.session.trials))
        self.start_over.setEnabled(not busy)
        for key in ("duplicate", "remove"):
            self.buttons[key].setEnabled(not busy and n > 0)
        for key in ("layout", "raw"):
            self.buttons[key].setEnabled(not busy and bool(open_))
        self.buttons["check"].setEnabled(not busy and bool(self.session.trials))
        self.buttons["forms"].setEnabled(bool(self.session.active_participants))
        for key, text in (("duplicate", "Duplicate"), ("remove", "Remove trial"), ("raw", "Delete raw footage"),
                          ("check", "Check and copy videos")):
            dots = " ..." if key in ("remove", "raw") else ""
            self.buttons[key].setText(text + (f" ({n})" if n > 1 else "") + dots)
        self.add_button.setEnabled(not busy)
        shown = self.panel.label
        self.panel.check_button.setEnabled(not busy and shown in self.rows and not self.session.trial(shown).frozen
                                           and self.session.trial_layout(shown) is not None)

    def changed(self, keep: list[str] | None = None) -> None:
        """Something changed the project: this section (keeping these trials selected), the others, the badges."""
        self._keep = list(keep if keep is not None else self.selected())
        self.refresh()
        self.win.changed()

    def after_run(self) -> None:
        self.changed()
        self.completeChanged.emit()

    # ---------- adding a trial (R10) ----------

    def _fill_add_row(self) -> None:
        ses = self.session
        self.add_who.clear()
        self.add_who.addItem("New participant (random pseudonym)", None)
        for p in ses.active_participants:
            n = len(ses.trials_of(p.label))
            self.add_who.addItem(f"{p.code} ({n} trial{'' if n == 1 else 's'})", p.label)
        self.add_who.setCurrentIndex(max(0, self.add_who.findData(workflow.default_participant(ses))))
        self.add_layout.clear()
        self.add_layout.addItem("(none yet)", "")
        for lay in ses.layouts:
            self.add_layout.addItem(f"{lay.name} ({lay.date})" if lay.date else lay.name, lay.id)
        last = ses.trials[-1] if ses.trials else None
        self.add_layout.setCurrentIndex(max(0, self.add_layout.findData(workflow.default_layout(ses))))
        self.add_name.setText(last.name if last else "")
        self.add_date.clear()

    def add(self) -> None:
        layout = self.add_layout.currentData() or ""
        try:
            trial = workflow.add_trial(self.session, self.root, participant=self.add_who.currentData(),
                                       layout=layout, name=self.add_name.text(),
                                       date=self.add_date.text().strip() or None)
        except ERRORS as exc:
            error_box(self, "Trial not added", clean(exc))
            return
        self.changed(keep=[trial.label])  # its panel opens: choose (or accept the suggested) videos there

    # ---------- actions on the selected trials ----------

    def duplicate(self) -> None:
        copies = for_each(self, "Not duplicated", self.selected(),
                          lambda lb: workflow.duplicate_trial(self.session, self.root, lb), ERRORS)
        self.changed(keep=[t.label for t in copies])

    def change_layout(self) -> None:
        labels = self.selected()
        lid = choose_layout(self, self.session) if labels else None
        if lid is None:
            return
        for_each(self, "Layout not changed", labels,
                 lambda lb: workflow.set_trial_layout(self.session, self.root, lb, lid), ERRORS)
        self.changed(keep=labels)

    def waiting_for_copy(self) -> list[str]:
        """Trials whose videos are all chosen but not checked and copied yet."""
        ses = self.session
        out = []
        for t in ses.trials:
            lay = ses.trial_layout(t.label)
            if (not t.frozen and lay is not None and all(t.videos.get(c) for c in lay.camera_ids())
                    and not workflow.trial_inputs_ready(ses, self.root, t.label)):
                out.append(t.label)
        return out

    def check_and_copy(self, labels: list[str] | None = None) -> None:
        labels = [lb for lb in (labels or self.selected()) if lb] or self.waiting_for_copy()
        if not labels:
            error_box(self, "Nothing to check", "Choose the videos of a trial first (select it: its panel opens "
                      "below the table), or select the trials to check again.")
            return
        self.win.set_busy(True)
        self.show_status()
        self.bar.stage.setText(f"Checking and copying the videos of {len(labels)} trial(s) ...")

        def done(result: dict) -> None:
            self.checks.update(result)
            copied = [lb for lb, (_r, plan, _e) in result.items() if plan is not None]
            not_copied = [lb for lb in result if lb not in copied]
            self.bar.stage.setText(f"Checked: {len(copied)} trial(s) copied" + (
                f"; not copied: trial {', '.join(not_copied)} (see its panel)." if not_copied else "."))
            self.win.set_busy(False)
            self.changed(keep=labels)

        def failed(msg: str, details: str) -> None:
            self.win.set_busy(False)
            self.bar.stage.setText("")
            error_box(self, "Check stopped", msg, details)
            self.changed(keep=labels)

        Background(self, workflow.check_each_trial, self.session, self.root, labels, on_done=done, on_error=failed,
                   on_progress=self.bar.stage.setText, progress=True)

    def delete_raw(self) -> None:
        from mocapapp.gui.privacy_dialog import DeleteDialog

        labels = [lb for lb in self.selected() if self.session.trial(lb).raw_present]
        for label in labels:  # each shows its own list of files and asks on its own (§8.4)
            if DeleteDialog(self, self.session, self.root, "raw", label).exec() != QDialog.DialogCode.Accepted:
                break
        self.changed(keep=labels)

    def remove(self) -> None:
        from mocapapp.gui.privacy_dialog import DeleteDialog

        labels = self.selected()
        with_data = [lb for lb in labels if workflow.trial_has_data(self.session, self.root, lb)]
        empty = [lb for lb in labels if lb not in with_data]
        if empty and confirm(self, "Remove trial" + ("s" if len(empty) > 1 else ""),
                             f"Remove trial(s) {', '.join(empty)}? They have no copied videos or results yet; the "
                             "recordings in the camera folders and the participants are not touched.", "Remove"):
            for_each(self, "Not removed", empty,
                     lambda lb: workflow.remove_empty_trial(self.session, self.root, lb), ERRORS)
        for label in with_data:  # deleted with their data: one confirmation each, with the pseudonym typed
            if DeleteDialog(self, self.session, self.root, "trial", label).exec() != QDialog.DialogCode.Accepted:
                break
        self.changed(keep=[])

    def forms(self) -> None:
        codes = list(dict.fromkeys(self.session.trial(lb).code for lb in self.selected()
                                   if not self.session.trial(lb).withdrawn_at))
        if len(codes) == 1:
            self.win.save_consent_pdf(codes[0])
        else:
            self.win.save_all_consent_pdfs(codes or None)

    # ---------- running ----------

    def run_clicked(self) -> None:
        ses = self.session
        chosen = self.selected()
        if chosen:
            labels = [lb for lb in chosen if self.runnable(lb)]
        else:
            labels = [t.label for t in ses.active_trials
                      if readiness.trial_status(ses, self.root, t.label) in RUNS_AGAIN and self.runnable(t.label)]
        left = [lb for lb in chosen if lb not in labels]
        reasons = "<br>".join(f"Trial {lb}: {html.escape(self._status(lb)[1] or self._status(lb)[0])}"
                              for lb in left)
        if not labels:
            error_box(self, "Nothing to run", reasons or "No trial is ready to run yet: each trial's Status says "
                      "what it still needs.")
            return
        self._start(labels, left_out=f"<br><br><b>Not ready, left out:</b><br>{reasons}" if left else "")

    def _start(self, labels: list[str], accept_sync: bool = False, left_out: str = "") -> None:
        if self.bar.start(labels, resume=not self.start_over.isChecked(), accept_sync=accept_sync,
                          left_out=left_out):
            self.start_over.setChecked(False)
