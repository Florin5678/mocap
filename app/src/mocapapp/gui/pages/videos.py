"""Step 2 (Trials): one table of the trials with their participant, height, other details and consent (0.1.0rc17),
the trial mapping (D7), the calibration videos (D3, several per camera since 0.1.0rc17), and copying the videos into
the session (§6.2 rules 1-7)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStyle,
    QTableWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core.assemble import plan_inputs
from mocapapp.core.session import format_height, parse_height, save_session
from mocapapp.core.validate import Issue, autofill, scan_dir, suggest_trials, validate
from mocapapp.core.workflow import (
    SetupError,
    add_calib_videos,
    add_trial,
    inputs_ready,
    prepare_session,
    remove_empty_trial,
    rename_consequences,
    set_mapping,
    set_trial_code,
    set_trial_detail,
    trial_has_data,
)
from mocapapp.gui.calib_dialog import MULTIPLE_TIP, CalibDialog, pick_videos
from mocapapp.gui.columns_dialog import ColumnsDialog, detail_title
from mocapapp.gui.common import (
    Background,
    IssueView,
    NoWheelCombo,
    clear_table,
    confirm,
    error_box,
    fit_rows,
    note,
    readonly,
)
from mocapapp.gui.main_window import Page

LOW = QColor("#fff3cd")
SUGGESTED = QColor("#e3f2fd")
MAX_ROWS = 10  # the trials table scrolls below its heading row beyond this
CONSENT_COLUMNS = ["Signed consent\nreceived", "Consented to storing\nraw footage", "Delete raw\nfootage",
                   "Consent\nform"]


class VideosPage(Page):
    key, title = "videos", "Trials"  # key kept: project files store it

    def __init__(self, win):
        super().__init__(win)
        self.report = None
        inner = QWidget()
        layout = QVBoxLayout(inner)
        row = QHBoxLayout()
        self.check_button = QPushButton("Check videos")
        self.check_button.clicked.connect(self.check)
        self.status = QLabel()
        self.status.setWordWrap(True)
        row.addWidget(self.check_button)
        row.addWidget(self.status, 1)
        layout.addLayout(row)
        self.issues = IssueView(min_height=140)
        layout.addWidget(self.issues)

        trials = QGroupBox("Trials and participants")
        tl = QVBoxLayout(trials)
        tl.addWidget(note("One row per trial. Click a pseudonym to change it (never use a real name), or open its "
                          "arrow to give the trial to a participant who already has one. Height scales the body "
                          "model: without it, it is estimated (not recommended). <i>Generate ...</i> makes the "
                          "participant's consent form; tick <i>Signed consent received</i> once you hold it. "
                          "<i>Delete raw footage</i> follows the storage consent. Changes are saved straight away."))
        self.table = QTableWidget(0, 0)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tl.addWidget(self.table)
        trow = QHBoxLayout()
        self.add_trial_button = QPushButton("Add trial")
        self.add_trial_button.clicked.connect(self.add_trial)
        self.remove_trial_button = QPushButton("Remove trial ...")
        self.remove_trial_button.setToolTip("Removes the selected trial (click its row first; otherwise the last "
                                            "one). A trial with copied videos or results is deleted with them, after "
                                            "you type the pseudonym.")
        self.remove_trial_button.clicked.connect(self.remove_trial)
        self.columns_button = QPushButton("Edit columns ...")
        self.columns_button.setToolTip("Add or remove columns: weight, handedness, age, gender, or your own.")
        self.columns_button.clicked.connect(self.edit_columns)
        self.all_forms_button = QPushButton("Generate all consent forms ...")
        self.all_forms_button.setToolTip("One PDF with every participant's consent form, ready to print.")
        self.all_forms_button.clicked.connect(self.win.save_all_consent_pdfs)
        for b in (self.add_trial_button, self.remove_trial_button, self.columns_button):
            trow.addWidget(b)
        trow.addStretch()
        trow.addWidget(self.all_forms_button)
        tl.addLayout(trow)
        self.people_issues = IssueView(min_height=30)
        self.people_issues.setMaximumHeight(130)
        tl.addWidget(self.people_issues)
        layout.addWidget(trials)

        self.mapping_box = QGroupBox("Trial mapping: which video is which trial, per camera")
        ml = QVBoxLayout(self.mapping_box)
        self.mapping_note = QLabel()
        self.mapping_note.setWordWrap(True)
        ml.addWidget(self.mapping_note)
        self.grid = QTableWidget(0, 0)
        ml.addWidget(self.grid)
        self.save_mapping_button = QPushButton("Save this mapping")
        self.save_mapping_button.clicked.connect(self.save_mapping)
        mrow = QHBoxLayout()
        mrow.addWidget(self.save_mapping_button)
        mrow.addStretch()
        ml.addLayout(mrow)
        layout.addWidget(self.mapping_box)

        self.calib_box = QGroupBox("Calibration videos ('intrinsic' and 'extrinsic', one or more per camera)")
        cl = QVBoxLayout(self.calib_box)
        self.calib_table = QTableWidget(0, 3)
        self.calib_table.setHorizontalHeaderLabels(["Camera", "intrinsic", "extrinsic"])
        self.calib_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.calib_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.calib_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.calib_table.verticalHeader().setVisible(False)
        cl.addWidget(self.calib_table)
        cl.addWidget(note("A camera whose zoom or other settings changed, or that was moved, between trials: add its "
                          "further calibration videos with <i>Change ...</i>, then tick which trials use each."))
        layout.addWidget(self.calib_box)

        self.prep_box = QGroupBox("Copy into the session")
        pl = QVBoxLayout(self.prep_box)
        pl.addWidget(note("The originals are never changed. Videos that are already constant-frame-rate, upright "
                          "H.264 are linked or copied; others are converted (constant frame rate, rotation applied)."))
        self.space = QLabel()
        self.space.setWordWrap(True)
        pl.addWidget(self.space)
        self.prepare_button = QPushButton("Copy videos into the session")
        self.prepare_button.clicked.connect(self.prepare)
        pl.addWidget(self.prepare_button)
        self.progress = QLabel()
        pl.addWidget(self.progress)
        layout.addWidget(self.prep_box)
        layout.addStretch()
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(area)

    @property
    def imported(self) -> bool:
        return self.session is not None and self.session.calibration_source == "imported"

    def is_complete(self) -> bool:
        if self.session is None:
            return False
        return self.imported or inputs_ready(self.session, self.root)

    def refresh(self) -> None:
        if self.session is None:
            return
        # calibration videos named 'intrinsic'/'extrinsic' are chosen straight away; trial videos named with their
        # number are only suggested in the mapping table until 'Save this mapping' (0.1.0rc17)
        if not self.session.frozen and not self.win.busy and not self.imported and autofill(self.session, False):
            save_session(self.session, self.root)
        self._fill_people()
        for w in (self.check_button, self.issues, self.mapping_box, self.calib_box, self.prep_box):
            w.setVisible(not self.imported)  # sample data: its videos and calibration come ready-made
        frozen = self.session.frozen
        for b in (self.check_button, self.save_mapping_button):
            b.setEnabled(not frozen)
        if self.imported:
            self.status.setText("Sample data: the videos and calibration are ready-made.")
            if frozen:
                self.status.setText("Raw footage was deleted in this session, so its videos can no longer be checked "
                                    "or changed. Start a new session for new recordings.")
            return
        done = self.is_complete()
        self.status.setText("Videos are in the session. Check again after changing anything." if done else
                            "Press <b>Check videos</b> to inspect every file (this reads each video once).")
        self._fill_calib()
        self._fill_grid()
        self.plan = self._plan()
        self.prepare_button.setEnabled(self.plan is not None and self.plan.enough_space)
        if frozen:  # §8.4: after a deletion the session's videos stay as they are
            self.status.setText("Raw footage was deleted in this session, so its videos can no longer be checked "
                                "or changed. Start a new session for new recordings.")
            self.prepare_button.setEnabled(False)

    # ---------- check ----------

    def check(self) -> None:
        self.win.set_busy(True)
        self.status.setText("Checking every video ...")

        def done(report):
            self.win.set_busy(False)
            self.report = report
            save_session(self.session, self.root)
            self.issues.show_issues(report.issues, "All videos look fine.")
            self.status.setText("Check finished." + ("" if report.ok else " Fix the problems listed below."))
            self.refresh()
            self.completeChanged.emit()

        def failed(msg, details):
            self.win.set_busy(False)
            error_box(self, "Check failed", msg, details)

        Background(self, validate, self.session, self.root, on_done=done, on_error=failed)

    # ---------- trials and participants (one table since 0.1.0rc17) ----------

    def _columns(self) -> list:
        return [d for d in self.session.asked_details if d.key != "height"]

    def _fill_people(self) -> None:
        ses = self.session
        details = self._columns()
        table = self.table
        clear_table(table)
        headers = ["Participant ID", "Height (m)"] + [detail_title(d) for d in details] + CONSENT_COLUMNS
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(len(ses.trials))
        table.setVerticalHeaderLabels([f"Trial {t.label}" for t in ses.trials])
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.detail_cells: dict[tuple[str, str], QWidget] = {}
        self.code_boxes: dict[str, QComboBox] = {}
        first = len(headers) - len(CONSENT_COLUMNS)
        for r, t in enumerate(ses.trials):
            trials = ses.trials_of(t.participant)
            gone = t.withdrawn_at or (trials and not any(x.raw_present for x in trials))
            if gone:
                table.setItem(r, 0, readonly(t.code))
                when = (f"all data deleted {t.withdrawn_at[:10]}" if t.withdrawn_at
                        else f"raw footage deleted {min(x.raw_deleted_at or '' for x in trials)[:10]}")
                table.setItem(r, first, readonly(when))
            else:
                table.setCellWidget(r, 0, self._code_box(t))
            table.setCellWidget(r, 1, self._height_cell(t))
            for col, d in enumerate(details, 2):
                table.setCellWidget(r, col, self._detail_cell(t, d))
            if not gone:
                self._consent_cells(r, first, t)
        fit_rows(table, max_rows=MAX_ROWS)
        frozen = ses.frozen
        self.add_trial_button.setEnabled(not frozen)
        self.add_trial_button.setToolTip("Raw footage was deleted in this session: start a new session for new "
                                         "trials." if frozen else "A new trial for a new participant with a random "
                                         "pseudonym. Choose another participant with the arrow of its pseudonym.")
        self.remove_trial_button.setEnabled(len(ses.trials) > 1)
        self._check_people()

    def _code_box(self, trial) -> QComboBox:
        """The pseudonym: type to change it, or open the arrow to choose a participant who already has a trial."""
        box = NoWheelCombo()
        box.setEditable(True)
        box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for person in self.session.active_participants:
            box.addItem(person.code, person.label)
        box.setCurrentIndex(max(0, box.findData(trial.participant)))
        box.lineEdit().setMaxLength(24)
        box.setMinimumWidth(box.fontMetrics().horizontalAdvance("M" * 13))
        person = trial.person
        former = f" Earlier pseudonyms: {', '.join(person.former_codes)}." if person.former_codes else ""
        box.setToolTip("Type another pseudonym (letters, digits, - or _; never the participant's real name), or open "
                       "the arrow to choose another participant." + former)
        box.activated.connect(lambda i, t=trial, b=box: self._picked(t, b.itemData(i)))
        box.lineEdit().editingFinished.connect(lambda t=trial, b=box: self._typed(t, b))
        self.code_boxes[trial.label] = box
        return box

    def _later(self) -> None:
        """Rebuild the table once the current signal is handled (its widgets are replaced)."""
        QTimer.singleShot(0, self._after_change)

    def _after_change(self) -> None:
        if self.session is not None:
            self._fill_people()
            if not self.imported:
                self._fill_grid()

    def _picked(self, trial, person: str) -> None:
        if not person or person == trial.participant:
            return
        target = self.session.participant(person)
        self._apply_code(trial, target.code)

    def _typed(self, trial, box: QComboBox) -> None:
        text = box.currentText().strip()
        if text != trial.code:
            self._apply_code(trial, text)

    def _apply_code(self, trial, code: str) -> None:
        other = next((p for p in self.session.active_participants if p.code.upper() == code.upper()), None)
        confirmed = False
        if other is None or other.label == trial.participant:  # a new pseudonym for this trial's participant
            consequences = rename_consequences(self.session, trial.participant)
            if consequences:
                if not confirm(self, "Change the pseudonym?", "\n\n".join(consequences), "Change it"):
                    self._later()
                    return
                confirmed = True
        try:
            set_trial_code(self.session, self.root, trial.label, code, confirmed)
        except SetupError as exc:
            error_box(self, "Pseudonym not changed", str(exc))
        self._later()

    def _height_cell(self, trial) -> QLineEdit:
        own = trial.details.get("height", "")
        w = QLineEdit(format_height(parse_height(own)) if own else "")
        person = self.session.participant(trial.participant).height_m
        w.setPlaceholderText(format_height(person) if person else "estimate")
        w.setToolTip("Height in metres (or cm) on the day of this trial. Empty: estimated, which is less accurate.")
        w.setEnabled(not trial.withdrawn_at)
        w.editingFinished.connect(lambda t=trial, w=w: self._height_edited(t, w))
        self.detail_cells[(trial.label, "height")] = w
        return w

    def _height_edited(self, trial, w: QLineEdit) -> None:
        stored = trial.details.get("height", "")
        if w.text().strip() and stored and parse_height_safe(w.text()) == parse_height_safe(stored):
            w.setText(format_height(parse_height(stored)))
            return
        self._detail_edited(trial, self.session.detail("height"), w.text())

    def _detail_cell(self, trial, d) -> QWidget:
        if d.kind == "choice":
            w = NoWheelCombo()
            w.addItem("", "")
            for option in d.choices:
                w.addItem(option, option)
            w.setCurrentIndex(max(0, w.findData(trial.details.get(d.key, ""))))
            w.currentIndexChanged.connect(lambda _i, t=trial, d=d, w=w: self._detail_edited(t, d, w.currentData()))
        else:
            w = QLineEdit(trial.details.get(d.key, ""))
            if d.unit:
                w.setPlaceholderText(d.unit)
            w.editingFinished.connect(lambda t=trial, d=d, w=w: self._detail_edited(t, d, w.text()))
        w.setEnabled(not trial.withdrawn_at)
        self.detail_cells[(trial.label, d.key)] = w
        return w

    def _detail_edited(self, trial, d, value) -> None:
        if " ".join(str(value or "").split()) == trial.details.get(d.key, ""):
            return
        try:
            set_trial_detail(self.session, self.root, trial.label, d.key, value)
        except SetupError as exc:
            error_box(self, "Not changed", str(exc))
        self._show_details()

    def _show_details(self) -> None:
        """Put the stored values back in the cells (normalised, and copied to the participant's other trials)."""
        for (label, key), w in self.detail_cells.items():
            value = self.session.trial(label).details.get(key, "")
            if key == "height" and value:
                value = format_height(parse_height(value))
            w.blockSignals(True)
            if isinstance(w, QComboBox):
                w.setCurrentIndex(max(0, w.findData(value)))
            elif w.text() != value:
                w.setText(value)
            w.blockSignals(False)

    def _consent_cells(self, r: int, first: int, trial) -> None:
        person = trial.person
        attest = QCheckBox("received")
        attest.setChecked(person.consent.attested)
        attest.toggled.connect(lambda on, p=person: self._attest(p, on))
        storage = QCheckBox("yes")
        storage.setChecked(person.consent.raw_footage_storage)
        storage.toggled.connect(lambda on, p=person: self._storage(p, on))
        delete = QCheckBox("delete")
        delete.setChecked(person.delete_raw)
        delete.toggled.connect(lambda on, p=person: self._delete(p, on))
        for col, cb in ((first, attest), (first + 1, storage), (first + 2, delete)):
            self.table.setCellWidget(r, col, cb)
        form = QPushButton("Generate ...")
        form.clicked.connect(lambda _=False, p=person: self.win.save_consent_pdf(p.code))
        self.table.setCellWidget(r, first + 3, form)

    def _attest(self, person, on: bool) -> None:
        person.consent.attested = on
        person.consent.attested_at = datetime.now().isoformat(timespec="seconds") if on else None
        self.win.save()
        self._later()  # the participant's other rows show it too

    def _storage(self, person, on: bool) -> None:
        person.consent.raw_footage_storage = on
        person.delete_raw = not on  # default follows consent (§6.2); still changeable
        self.win.save()
        self._later()

    def _delete(self, person, on: bool) -> None:
        person.delete_raw = on
        self.win.save()
        self._later()

    def _check_people(self) -> None:
        issues = []
        for p in self.session.active_participants:
            trials = self.session.trials_of(p.label)
            if trials and not any(x.raw_present for x in trials):
                continue
            where = f"{p.code} (trial {', '.join(t.label for t in trials)})"
            if not p.consent.attested:
                issues.append(Issue("action", where, "Signed consent not yet confirmed (needed before Run).", ""))
            if not p.consent.raw_footage_storage and not p.delete_raw:
                issues.append(Issue("warning", where, "No consent to store raw footage, but it will not be deleted: "
                                    "the consent form promises deletion.", "Tick 'Delete raw footage'."))
        self.people_issues.show_issues(issues, "Every participant's consent is recorded.")
        # as tall as its lines (one line when all is well), at most the maximum: then it scrolls
        lines = max(1, sum(1 + bool(i.fix) for i in issues))
        self.people_issues.setFixedHeight(min(130, 14 + lines * self.people_issues.fontMetrics().lineSpacing()))

    def edit_columns(self) -> None:
        ColumnsDialog(self, self.session, self.root).exec()
        self._fill_people()

    # ---------- adding and removing trials ----------

    def add_trial(self) -> None:
        try:
            trial = add_trial(self.session, self.root)
        except SetupError as exc:
            error_box(self, "Trial not added", str(exc))
            return
        self.report = None  # the new trial's videos still have to be chosen and checked
        self.win.set_session(self.root, self.session)
        self.status.setText(f"Trial {trial.label} added with participant {trial.code}. Choose its video for every "
                            "camera, then <b>Save this mapping</b>.")

    def remove_trial(self) -> None:
        from mocapapp.gui.privacy_dialog import DeleteDialog

        trials = self.session.trials
        r = self.table.currentRow()
        trial = trials[r] if 0 <= r < len(trials) else trials[-1]
        if trial_has_data(self.session, self.root, trial.label):
            dlg = DeleteDialog(self, self.session, self.root, "trial", trial.label)
            if dlg.exec() != QDialog.DialogCode.Accepted or dlg.result_ is None:
                return
        else:
            if not confirm(self, "Remove trial", f"Remove trial {trial.label} ({trial.code})? It has no copied "
                           "videos or results yet; the recordings in the camera folders are not touched.", "Remove"):
                return
            try:
                remove_empty_trial(self.session, self.root, trial.label)
            except SetupError as exc:
                error_box(self, "Trial not removed", str(exc))
                return
        self.report = None
        self.win.set_session(self.root, self.session)

    # ---------- calibration videos ----------

    def _fill_calib(self) -> None:
        cams = self.session.cameras
        clear_table(self.calib_table)
        self.calib_table.setRowCount(len(cams))
        self.warn_buttons: dict[tuple[str, str], QToolButton] = {}
        for r, cam in enumerate(cams):
            self.calib_table.setItem(r, 0, readonly(cam.name))
            for col, role in ((1, "intrinsic"), (2, "extrinsic")):
                self.calib_table.setCellWidget(r, col, self._calib_cell(cam, role))
        fit_rows(self.calib_table)

    def _calib_cell(self, cam, role: str) -> QWidget:
        cell = QWidget()
        row = QHBoxLayout(cell)
        row.setContentsMargins(4, 0, 0, 0)
        videos = cam.calib_videos(role)
        main = getattr(cam, role)
        if not videos:
            text = "not found - choose a file"
        else:
            text = ", ".join(Path(f).name for _, f in videos)
            if main is not None and main.origin == "manual" and len(videos) == 1:
                text += "  (chosen by hand)"
        label = QLabel(text)
        label.setToolTip("\n".join(f for _, f in videos))
        row.addWidget(label, 1)
        if len(videos) > 1:
            warn = QToolButton()
            warn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning))
            warn.setAutoRaise(True)
            warn.setToolTip(MULTIPLE_TIP)
            warn.setCursor(Qt.CursorShape.PointingHandCursor)
            warn.clicked.connect(lambda _=False, c=cam.name, ro=role: self.edit_calib(c, ro))
            self.warn_buttons[(cam.name, role)] = warn
            row.addWidget(warn)
        button = QPushButton("Choose ..." if not videos else "Change ...")
        button.setToolTip(f"Choose {cam.name}'s {role} video(s)." if not videos else
                          f"Add, replace or remove {cam.name}'s {role} videos, and tick which trials use each.")
        button.clicked.connect(lambda _=False, c=cam.name, ro=role: self.choose(c, ro))
        button.setEnabled(not self.session.frozen)
        row.addWidget(button)
        return cell

    def choose(self, camera: str, role: str) -> None:
        cam = self.session.camera(camera)
        if cam.calib_videos(role):
            self.edit_calib(camera, role)
            return
        files = pick_videos(self, f"{role} video(s) for {camera}", cam.source_dir)
        if not files:
            return
        try:
            add_calib_videos(self.session, self.root, camera, role, files)
        except SetupError as exc:
            error_box(self, "Not changed", str(exc))
            return
        self.win.save()
        self.check()

    def edit_calib(self, camera: str, role: str) -> bool:
        """The calibration dialog for one camera and role. True if videos were added, replaced or removed."""
        dlg = CalibDialog(self, self.session, self.root, camera, role)
        dlg.exec()
        self.win.save()
        if dlg.files_changed:
            self.check()
        else:
            self.refresh()
            self.completeChanged.emit()
        return dlg.files_changed

    # ---------- mapping ----------

    def _fill_grid(self) -> None:
        """Trial mapping: the video of every trial, per camera. Videos named with a trial's number are filled in as
        suggestions (highlighted) until the mapping is saved (0.1.0rc17)."""
        ses = self.session
        cams = ses.cameras
        self.grid.clear()
        self.grid.setRowCount(len(ses.trials))
        self.grid.setColumnCount(len(cams))
        self.grid.setHorizontalHeaderLabels([c.name for c in cams])
        self.grid.setVerticalHeaderLabels([f"Trial {t.label}" for t in ses.trials])
        self.grid.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        low = self.report.low_confidence if self.report else set()
        self.suggested = {} if ses.frozen else suggest_trials(ses)
        for c, cam in enumerate(cams):
            try:
                candidates = scan_dir(Path(cam.source_dir)).trials
            except OSError:
                candidates = []
            for r, t in enumerate(ses.trials):
                box = NoWheelCombo()
                box.addItem("-", None)
                for f in candidates:
                    box.addItem(f.name, str(f))
                guess = self.suggested.get((cam.name, t.label))
                current = cam.trial_files.get(t.label) or (str(guess) if guess else None)
                if current:
                    idx = box.findData(str(Path(current)))
                    if idx < 0:
                        box.addItem(Path(current).name, current)
                        idx = box.count() - 1
                    box.setCurrentIndex(idx)
                if (cam.name, t.label) in low:
                    box.setToolTip("Low confidence: check this file (durations do not match the other cameras).")
                    box.setItemText(box.currentIndex(), box.currentText() + "  (check?)")
                    box.setStyleSheet("QComboBox { background: #fff3cd; }")
                elif guess is not None:
                    box.setToolTip("Chosen by its name. Press 'Save this mapping' to keep it.")
                    box.setStyleSheet("QComboBox { background: #e3f2fd; }")
                self.grid.setCellWidget(r, c, box)
                item = readonly("")
                item.setBackground(QBrush(LOW if (cam.name, t.label) in low else
                                          SUGGESTED if guess is not None else QColor(0, 0, 0, 0)))
                self.grid.setItem(r, c, item)
        fit_rows(self.grid, max_rows=MAX_ROWS)
        review = self.report is not None and self.report.needs_mapping_review
        text = ""
        if self.suggested:
            text = ("<b>Videos named with the trial numbers were filled in (highlighted).</b> Check them, then press "
                    "<i>Save this mapping</i>.")
        elif review:
            text = ("<b>Please check every row and correct it if needed, then save.</b> Highlighted cells are "
                    "uncertain.")
        self.mapping_note.setText(text)
        self.mapping_note.setVisible(bool(text))

    def save_mapping(self) -> None:
        table = {}
        for c, cam in enumerate(self.session.cameras):
            for r, t in enumerate(self.session.trials):
                if t.withdrawn_at:
                    continue
                data = self.grid.cellWidget(r, c).currentData()
                if not data:
                    error_box(self, "Mapping incomplete", f"Choose a file for {cam.name}, trial {t.label}.")
                    return
                table[(cam.name, t.label)] = Path(data)
        for cam in self.session.cameras:
            files = [f for (c, _), f in table.items() if c == cam.name]
            if len(set(files)) != len(files):
                error_box(self, "Mapping not saved", f"{cam.name}: the same file is used for two trials.")
                return
        try:
            set_mapping(self.session, table, self.root)
        except SetupError as exc:
            error_box(self, "Mapping not saved", str(exc))
            return
        self.win.save()
        self.check()

    # ---------- prepare ----------

    plan = None

    def _plan(self):
        """What copying would do, shown on the page once the check passed (no extra dialog)."""
        self.space.setText("")
        if self.report is None or not self.report.ok or self.session.frozen:
            return None
        try:
            plan = plan_inputs(self.session, self.root, self.report)
        except (OSError, ValueError) as exc:
            self.space.setText(f"Cannot plan the copy: {exc}")
            return None
        converted = sum(how == "transcode" for _, _, how in plan.jobs)
        text = (f"{len(plan.jobs)} videos ({converted} converted). Space needed: about "
                f"{plan.bytes_needed / 1e9:.2f} GB; free: {plan.bytes_free / 1e9:.1f} GB.")
        if plan.target_fps:
            text += f" All videos will be converted to {plan.target_fps:g} fps."
        if not plan.enough_space:
            text = "<b>Not enough disk space.</b> " + text + " Free some space or choose another output folder."
        self.space.setText(text)
        return plan

    def prepare(self) -> None:
        if self.plan is None or not self.plan.enough_space:
            return
        self.win.set_busy(True)

        def done(_):
            self.win.set_busy(False)
            self.progress.setText("Done: the videos are in the session.")
            self.refresh()
            self.completeChanged.emit()

        def failed(msg, details):
            self.win.set_busy(False)
            self.progress.setText("")
            error_box(self, "Copying stopped", msg + "\n\nPress the button again to continue where it stopped.",
                      details)

        Background(self, prepare_session, self.session, self.root, on_done=done, on_error=failed,
                   on_progress=self.progress.setText, progress=True)


def parse_height_safe(text: str) -> float | None:
    try:
        return parse_height(text)
    except ValueError:
        return None
