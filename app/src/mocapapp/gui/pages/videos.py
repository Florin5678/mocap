"""Step 2 (Trials): the trials with their participant and participant details, the calibration videos (D3), the
trial mapping (D7), and copying the videos into the session (§6.2 rules 1-7)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core.assemble import plan_inputs
from mocapapp.core.session import save_session
from mocapapp.core.validate import autofill, scan_dir, validate
from mocapapp.core.workflow import (
    SetupError,
    add_trial,
    inputs_ready,
    pick_video,
    prepare_session,
    remove_empty_trial,
    set_mapping,
    set_trial_detail,
    set_trial_participants,
    trial_has_data,
)
from mocapapp.gui.common import Background, IssueView, clear_table, confirm, error_box, fit_rows, note, readonly
from mocapapp.gui.main_window import Page
from mocapapp.gui.pages.setup import detail_title

LOW = QColor("#fff3cd")


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

        trials = QGroupBox("Trials and participant details")
        tl = QVBoxLayout(trials)
        tl.addWidget(note("One row per trial: whose trial it is and their details on that day. A participant's later "
                          "trials start with the values of their first one. Which details are asked is set in step 1 "
                          "(Session). Changes are saved straight away."))
        self.details_table = QTableWidget(0, 0)
        tl.addWidget(self.details_table)
        self.add_trial_button = QPushButton("Add trial ...")
        self.add_trial_button.clicked.connect(self.add_trial)
        self.remove_trial_button = QPushButton("Remove trial ...")
        self.remove_trial_button.setToolTip("Removes the selected trial (click its row number first; otherwise the "
                                            "last one). A trial with copied videos or results is deleted with them, "
                                            "after you type the pseudonym.")
        self.remove_trial_button.clicked.connect(self.remove_trial)
        trow = QHBoxLayout()
        trow.addWidget(self.add_trial_button)
        trow.addWidget(self.remove_trial_button)
        trow.addStretch()
        tl.addLayout(trow)
        layout.addWidget(trials)

        mapping = QGroupBox("Trial mapping: which video is which trial, per camera")
        ml = QVBoxLayout(mapping)
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
        layout.addWidget(mapping)

        calib = QGroupBox("Calibration videos (one 'intrinsic' and one 'extrinsic' per camera)")
        cl = QVBoxLayout(calib)
        self.calib_table = QTableWidget(0, 5)
        self.calib_table.setHorizontalHeaderLabels(["Camera", "intrinsic", "extrinsic", "", ""])
        self.calib_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.calib_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.calib_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.calib_table.verticalHeader().setVisible(False)
        cl.addWidget(self.calib_table)
        layout.addWidget(calib)

        prep = QGroupBox("Copy into the session")
        pl = QVBoxLayout(prep)
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
        layout.addWidget(prep)
        layout.addStretch()
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(area)

    def skipped(self) -> bool:
        return self.session is not None and self.session.calibration_source == "imported"

    def is_complete(self) -> bool:
        return self.session is not None and inputs_ready(self.session, self.root)

    def refresh(self) -> None:
        if self.session is None:
            return
        # what the file names make obvious is chosen straight away: 'intrinsic'/'extrinsic', and a trial's video named
        # with its number (01, trial01, trial_01); nothing chosen before is changed
        if not self.session.frozen and not self.win.busy and autofill(self.session):
            save_session(self.session, self.root)
        done = self.is_complete()
        self.status.setText("Videos are in the session. Check again after changing anything." if done else
                            "Press <b>Check videos</b> to inspect every file (this reads each video once).")
        self._fill_calib()
        self._fill_details_table()
        self._fill_grid()
        self.plan = self._plan()
        self.prepare_button.setEnabled(self.plan is not None and self.plan.enough_space)
        frozen = self.session.frozen
        if frozen:  # §8.4: after a deletion the session's videos stay as they are
            self.status.setText("Raw footage was deleted in this session, so its videos can no longer be checked "
                                "or changed. Start a new session for new recordings.")
        for b in (self.check_button, self.save_mapping_button):
            b.setEnabled(not frozen)
        if frozen:
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

    def _fill_calib(self) -> None:
        cams = self.session.cameras
        clear_table(self.calib_table)
        self.calib_table.setRowCount(len(cams))
        for r, cam in enumerate(cams):
            self.calib_table.setItem(r, 0, readonly(cam.name))
            for col, role in ((1, "intrinsic"), (2, "extrinsic")):
                ref = getattr(cam, role)
                text = "not found - choose a file" if ref is None else (
                    f"{Path(ref.file).name}{'  (chosen by hand)' if ref.origin == 'manual' else ''}")
                self.calib_table.setItem(r, col, readonly(text))
                button = QPushButton(f"Choose {role} ...")
                button.clicked.connect(lambda _=False, c=cam.name, ro=role: self.choose(c, ro))
                self.calib_table.setCellWidget(r, col + 2, button)
        fit_rows(self.calib_table)

    def choose(self, camera: str, role: str) -> None:
        cam = self.session.camera(camera)
        name, _ = QFileDialog.getOpenFileName(self, f"{role} video for {camera}", cam.source_dir,
                                              "Videos (*.mp4 *.mov *.avi *.mkv *.MP4 *.MOV);;All files (*)")
        if not name:
            return
        try:
            pick_video(self.session, camera, role, Path(name), self.root)
        except SetupError as exc:
            error_box(self, "Not changed", str(exc))
            return
        self.win.save()
        self.check()

    # ---------- mapping ----------

    def _trial_rows(self, table: QTableWidget, columns: list[str]) -> None:
        trials = self.session.trials
        table.clear()
        table.setRowCount(len(trials))
        table.setColumnCount(len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setVerticalHeaderLabels([f"Trial {t.label}" for t in trials])

    def _fill_details_table(self) -> None:
        """Trials with their participant and participant details (one row per trial)."""
        ses = self.session
        details = ses.asked_details
        table = self.details_table
        self._trial_rows(table, ["Participant"] + [detail_title(d) for d in details])
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for col, d in enumerate(details, 1):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            table.setColumnWidth(col, max(90, header.fontMetrics().horizontalAdvance(detail_title(d)) + 24))
        header.setStretchLastSection(True)
        frozen = ses.frozen
        people = ses.active_participants
        self.detail_cells = {}
        for r, t in enumerate(ses.trials):
            box = QComboBox()
            for person in people:
                box.addItem(f"Participant {person.label} ({person.code})", person.label)
            if t.withdrawn_at:
                box.addItem(f"Participant {t.participant} (withdrawn)", t.participant)
            box.setCurrentIndex(max(0, box.findData(t.participant)))
            if ses.run_state.stage_done.get(t.label) or frozen or t.withdrawn_at:
                box.setEnabled(False)
                box.setToolTip("Already processed for this participant." if ses.run_state.stage_done.get(t.label)
                               else "This session can no longer be changed.")
            box.currentIndexChanged.connect(lambda _i, t=t, b=box: self._owner_changed(t, b))
            table.setCellWidget(r, 0, box)
            table.setItem(r, 0, readonly(""))
            for col, d in enumerate(details, 1):
                table.setCellWidget(r, col, self._detail_cell(t, d))
                table.setItem(r, col, readonly(""))
        fit_rows(table)
        self.add_trial_button.setEnabled(not frozen)
        self.add_trial_button.setToolTip("Raw footage was deleted in this session: start a new session for new "
                                         "trials." if frozen else "Another trial for a participant, for example "
                                         "recorded later. Its details start as those of their first trial.")
        self.remove_trial_button.setEnabled(len(ses.trials) > 1)

    def _fill_grid(self) -> None:
        """Trial mapping: the video of every trial, per camera."""
        cams = self.session.cameras
        self._trial_rows(self.grid, [c.name for c in cams])
        self.grid.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        low = self.report.low_confidence if self.report else set()
        for c, cam in enumerate(cams):
            try:
                candidates = scan_dir(Path(cam.source_dir)).trials
            except OSError:
                candidates = []
            for r, t in enumerate(self.session.trials):
                box = QComboBox()
                box.addItem("-", None)
                for f in candidates:
                    box.addItem(f.name, str(f))
                current = cam.trial_files.get(t.label)
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
                self.grid.setCellWidget(r, c, box)
                item = readonly("")
                item.setBackground(QBrush(LOW if (cam.name, t.label) in low else QColor(0, 0, 0, 0)))
                self.grid.setItem(r, c, item)
        fit_rows(self.grid)
        review = self.report is not None and self.report.needs_mapping_review
        self.mapping_note.setText("<b>Please check every row and correct it if needed, then save.</b> Highlighted "
                                  "cells are uncertain." if review else "")
        self.mapping_note.setVisible(review)

    # ---------- participant details per trial (0.1.0rc13) ----------

    def _detail_cell(self, trial, d) -> QWidget:
        if d.kind == "choice":
            w = QComboBox()
            w.addItem("", "")
            for option in d.choices:
                w.addItem(option, option)
            w.setCurrentIndex(max(0, w.findData(trial.details.get(d.key, ""))))
            w.currentIndexChanged.connect(lambda _i, t=trial, d=d, w=w: self._detail_edited(t, d, w.currentData()))
        else:
            w = QLineEdit(trial.details.get(d.key, ""))
            if d.key == "height":
                own = self.session.participant(trial.participant).height_m
                w.setPlaceholderText(f"{own:.2f}" if own else "estimate")
                w.setToolTip("Height in metres (or cm) for this trial. Empty: estimated, which is less accurate.")
            elif d.unit:
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
            w.blockSignals(True)
            if isinstance(w, QComboBox):
                w.setCurrentIndex(max(0, w.findData(value)))
            elif w.text() != value:
                w.setText(value)
            w.blockSignals(False)

    def _owner_changed(self, trial, box: QComboBox) -> None:
        person = box.currentData()
        if person == trial.participant:
            return
        try:
            set_trial_participants(self.session, {trial.label: person})
        except SetupError as exc:
            error_box(self, "Not changed", str(exc))
            box.blockSignals(True)
            box.setCurrentIndex(max(0, box.findData(trial.participant)))
            box.blockSignals(False)
            return
        self.win.save()
        self._show_details()

    # ---------- adding and removing trials (0.1.0rc13) ----------

    def add_trial(self) -> None:
        people = self.session.active_participants
        if not people:
            return
        r = self.details_table.currentRow()
        current = self.session.trials[r].participant if 0 <= r < len(self.session.trials) else people[0].label
        names = [f"Participant {person.label} ({person.code})" for person in people]
        start = next((i for i, person in enumerate(people) if person.label == current), 0)
        name, ok = QInputDialog.getItem(self, "Add trial", "Whose trial is it?", names, start, False)
        if not ok:
            return
        try:
            trial = add_trial(self.session, self.root, people[names.index(name)].label)
        except SetupError as exc:
            error_box(self, "Trial not added", str(exc))
            return
        self.report = None  # the new trial's videos still have to be chosen and checked
        self.win.set_session(self.root, self.session)
        self.status.setText(f"Trial {trial.label} added. Choose its video for every camera, then <b>Save this "
                            "mapping</b>.")

    def remove_trial(self) -> None:
        from mocapapp.gui.privacy_dialog import DeleteDialog

        trials = self.session.trials
        r = self.details_table.currentRow()
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
