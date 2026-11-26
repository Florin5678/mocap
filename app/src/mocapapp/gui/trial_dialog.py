"""A trial's details in a dialog (0.2.0rc3), for Edit and Add trial in Session: participant, layout, date, name, the
video of each camera of the layout (suggested ones highlighted), the layout-from-another-day warning (R8), and, for an
existing trial, its check and copy results and its state. Nothing changes until Save; a change that the trial's
results were computed from (participant, layout, videos) deletes them, after a warning and a confirmation."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
)

from mocapapp.core import readiness, workflow
from mocapapp.core.session import Trial, format_height
from mocapapp.core.validate import suggest_trials
from mocapapp.gui.common import (
    IssueView,
    NoWheelCombo,
    clear_table,
    confirm,
    error_box,
    fit_rows,
    note,
    readonly,
    status_badge,
)

SUGGESTED = QColor("#e3f2fd")
ERRORS = (ValueError, KeyError, OSError)
NEW = "__new__"  # the participant choice "New participant (random pseudonym)"


def size(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"


def choose_video(parent, title: str, start: str = "") -> Path | None:
    from mocapapp.gui.sections.cameras import choose_video as pick

    return pick(parent, title, start)


class TrialDialog(QDialog):
    """label None: a new trial, pre-filled like the previous one (R10)."""

    def __init__(self, section, label: str | None = None):
        super().__init__(section)
        self.section = section
        self.label = label
        self.saved: str | None = None  # the trial saved (its label), for the caller
        self.check_after = False  # Save and check videos
        ses = self.session
        self.trial = ses.trial(label) if label else None
        self.videos: dict[str, str] = dict(self.trial.videos) if self.trial else {}  # camera id -> file (staged)
        self.setWindowTitle(f"Trial {label} ({self.trial.code})" if label else "Add a trial")
        self.resize(820, 600 if label else 460)
        layout = QVBoxLayout(self)

        open_ = self.trial is None or not self.trial.frozen
        form = QFormLayout()
        self.participant = NoWheelCombo()
        self.participant.setToolTip("Who is recorded in this trial (participants are also added in Participants).")
        if self.trial is None:
            self.participant.addItem("New participant (random pseudonym)", NEW)
        for p in ses.active_participants:
            n = len(ses.trials_of(p.label))
            self.participant.addItem(f"{p.code} ({n} trial{'' if n == 1 else 's'})", p.label)
        if self.trial is not None and self.trial.withdrawn_at:
            self.participant.addItem(self.trial.code, self.trial.participant)
        who = self.trial.participant if self.trial else (workflow.default_participant(ses) or NEW)
        self.participant.setCurrentIndex(max(0, self.participant.findData(who)))
        self.participant.setEnabled(open_)
        self.layout_box = NoWheelCombo()
        self.layout_box.setToolTip("The camera layout the trial was recorded with. Its cameras decide which videos "
                                   "the trial needs.")
        self.layout_box.addItem("(none)", "")
        for lay in ses.layouts:
            self.layout_box.addItem(f"{lay.name} ({lay.date})" if lay.date else lay.name, lay.id)
        lid = self.trial.layout if self.trial else workflow.default_layout(ses)
        self.layout_box.setCurrentIndex(max(0, self.layout_box.findData(lid)))
        self.layout_box.setEnabled(open_)
        self.layout_box.currentIndexChanged.connect(lambda _: self._layout_changed())
        self.date = QLineEdit(self.trial.date if self.trial else "")
        self.date.setPlaceholderText("YYYY-MM-DD (empty: the day the videos were recorded)")
        self.date.editingFinished.connect(self._layout_changed)  # the layout-from-another-day warning follows
        last = ses.trials[-1] if ses.trials else None
        self.name = QLineEdit(self.trial.name if self.trial else (last.name if last else ""))
        self.name.setPlaceholderText("free text, e.g. walking, fast")
        form.addRow("Participant", self.participant)
        form.addRow("Layout", self.layout_box)
        form.addRow("Date", self.date)
        form.addRow("Name / note", self.name)
        layout.addLayout(form)

        warn_row = QHBoxLayout()
        self.warning = note("", "warning")
        self.accept_layout = QCheckBox("Use this layout anyway")
        self.accept_layout.setToolTip("The cameras were not moved since the layout was made: its positions still "
                                      "hold. Recorded with the trial.")
        self.accept_layout.setChecked(bool(self.trial and self.trial.layout_override))
        warn_row.addWidget(self.warning, 1)
        warn_row.addWidget(self.accept_layout)
        layout.addLayout(warn_row)

        layout.addWidget(QLabel("<b>Videos</b> (one per camera of the layout)"))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Camera", "Video", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        self.use_suggested = QPushButton("Use the suggested videos")
        self.use_suggested.setToolTip("Videos named with the trial's number in each camera's folder (highlighted).")
        self.use_suggested.clicked.connect(self._use_suggested)
        row = QHBoxLayout()
        row.addWidget(self.use_suggested)
        row.addStretch()
        layout.addLayout(row)
        self.open_ = open_

        self.copy = QLabel()
        self.copy.setWordWrap(True)
        layout.addWidget(self.copy)
        self.report = IssueView(min_height=60)
        self.report.setMaximumHeight(140)
        layout.addWidget(self.report)
        self.state = QLabel()
        self.state.setWordWrap(True)
        self.state.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.state)
        layout.addStretch()

        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton("Add" if label is None else "Save",
                                             QDialogButtonBox.ButtonRole.AcceptRole)
        self.check_button = buttons.addButton("Save and check videos" if label else "Add and check videos",
                                              QDialogButtonBox.ButtonRole.ApplyRole)
        self.check_button.setToolTip("Saves, then checks the videos (frame rate, size against the camera's "
                                     "calibration, length) and copies them into the project, in the background.")
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.save_button.clicked.connect(lambda: self._save(check=False))
        self.check_button.clicked.connect(lambda: self._save(check=True))
        buttons.rejected.connect(self.reject)
        busy = self.section.win.busy
        self.save_button.setEnabled(not busy)
        self.check_button.setEnabled(not busy and open_)
        if busy:
            self.save_button.setToolTip("The app is working: change trials when it is done.")
        layout.addWidget(buttons)
        self._layout_changed()
        self._show_state()

    @property
    def session(self):
        return self.section.session

    @property
    def root(self):
        return self.section.root

    # ---------- what is shown ----------

    def _preview(self) -> tuple:
        """A copy of the project with the trial as chosen in the dialog (for the suggested videos and the cameras)."""
        ses = self.session.model_copy(deep=True)
        lid = self.layout_box.currentData() or ""
        if self.trial is not None:
            trial = ses.trial(self.label)
        else:
            labels = [t.label for t in ses.trials]
            trial = Trial(label=workflow._next_label(labels), participant=ses.participants[0].label
                          if ses.participants else "P1", mapping_method="manual", layout=lid)
            ses.trials.append(trial)
        trial.layout = lid
        trial.videos = {c: f for c, f in self.videos.items() if f}
        trial.date = self.date.text().strip() or (self.trial.date if self.trial else workflow.today())
        trial.layout_override = False  # the warning is shown; the check box says whether it is accepted
        return ses, trial.label

    def _layout_changed(self) -> None:
        lid = self.layout_box.currentData() or ""
        try:
            ses, label = self._preview()
            lay = ses.layout(lid) if lid else None
            cams = ses.trial_cameras(label) if lay else []
            suggested = {cam: f for (cam, lb), f in suggest_trials(ses).items() if lb == label}
            warning = ses.layout_warning(label)
        except Exception:  # a camera folder that went away, an unusual project: no suggestions
            lay, suggested, warning = None, {}, ""
            if lid:
                lay = self.session.layout(lid)
                cams = [(lay.slot(c.id), c, None) for c in self.session.cameras if c.id in lay.camera_ids()]
        self.suggested = suggested
        clear_table(self.table)
        for slot, cam, _lc in cams:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, readonly(f"{cam.name} ({slot})" if cam.name != cam.id else cam.id))
            chosen = self.videos.get(cam.id)
            hint = suggested.get(cam.id)
            item = readonly(Path(chosen).name if chosen else f"suggested: {hint.name}" if hint else "not chosen")
            item.setToolTip(chosen or (str(hint) if hint else ""))
            if hint is not None and not chosen:
                item.setBackground(QBrush(SUGGESTED))
            self.table.setItem(r, 1, item)
            button = QPushButton("Choose ...")
            button.setEnabled(self.open_)
            button.clicked.connect(lambda _=False, c=cam.id: self._choose(c))
            self.table.setCellWidget(r, 2, button)
        fit_rows(self.table)
        self.table.setVisible(lay is not None)
        self.use_suggested.setVisible(bool(suggested) and self.open_)
        self.warning.setText(html.escape(warning))
        self.warning.setVisible(bool(warning))
        self.accept_layout.setVisible(bool(warning))

    def _show_state(self) -> None:
        """For an existing trial: its check and copy results and its state (as saved)."""
        if self.trial is None:
            for w in (self.copy, self.report, self.state):
                w.setVisible(False)
            return
        ses, root, label, trial = self.session, self.root, self.label, self.trial
        report, plan, error = self.section.checks.get(label, (None, None, ""))
        if error:
            self.copy.setText(f"<b>Not copied:</b> {html.escape(error)}")
        elif plan is not None:
            converted = sum(how == "transcode" for _, _, how in plan.jobs)
            n = len(ses.trial_cameras(label))
            self.copy.setText(f"Checked and copied: the trial's {n} video(s) and its cameras' calibration videos "
                              f"({converted} converted; copies already made are kept). Space for them: about "
                              f"{size(plan.bytes_needed)}; {size(plan.bytes_free)} free."
                              + (f" Trial videos at {plan.target_fps:g} fps." if plan.target_fps else ""))
        elif report is not None:
            self.copy.setText("<b>Not copied:</b> fix the problems below, then check again.")
        elif trial.raw_present and workflow.trial_inputs_ready(ses, root, label):
            self.copy.setText("The videos are checked and copied into the project.")
        else:
            self.copy.setText("")
        self.report.setVisible(report is not None)
        if report is not None:
            self.report.show_issues(report.issues, "The videos look fine.")

        from mocapapp.gui.sections.participants import footage_text

        status = readiness.trial_status(ses, root, label)
        lines = [f"<b>Status:</b> {status_badge(status)}"]
        rs = ses.run_state
        if status == "not ready":
            lines += [f"&#9675; {html.escape(m)}" for m in readiness.trial_missing(ses, root, label)]
        elif msg := readiness.status_message(ses, root, label, status):
            lines.append(f"<b>Message:</b> {html.escape(msg)}")
        if status != "needs sync fix" and rs.sync.get(label, {}).get("accepted"):
            lines += [f"<b>Sync accepted although:</b> {html.escape(m)}" for m in rs.sync[label].get("issues", [])]
        if not trial.withdrawn_at:
            height = format_height(trial.height_m) if trial.height_m else "missing"
            consent = "signed" if trial.consent.attested else "not signed yet"
            lines.append(f"Height {height} m; consent {consent}; raw footage "
                         f"{footage_text(ses, ses.participant(trial.participant))}.")
        if trial.frozen:
            lines.append("Its videos and layout can no longer change (R20): add a new trial for new recordings.")
        self.state.setText("<br>".join(lines))

    # ---------- videos ----------

    def _choose(self, camera: str) -> None:
        cam = self.session.camera(camera)
        title = f"Video of trial {self.label} from {cam.name}" if self.label else f"Video from {cam.name}"
        file = choose_video(self, title, cam.source_dir)
        if file is not None:
            self.videos[camera] = str(Path(file).resolve())
            self._layout_changed()

    def _use_suggested(self) -> None:
        for cam, file in self.suggested.items():
            self.videos.setdefault(cam, str(Path(file).resolve()))
        self._layout_changed()

    # ---------- saving ----------

    def changes(self) -> dict:
        """What Save would change: participant, layout, videos (camera -> file), name, date, accept."""
        lid = self.layout_box.currentData() or ""
        lay = self.session.layout(lid) if lid else None
        cams = lay.camera_ids() if lay else []
        videos = {c: f for c, f in self.videos.items() if c in cams and f}
        out = {}
        t = self.trial
        who = self.participant.currentData()
        if t is None or who != t.participant:
            out["participant"] = who
        if t is None or lid != t.layout:
            out["layout"] = lid
        old = {c: f for c, f in (t.videos.items() if t else []) if c in cams}
        if {c: str(Path(f).resolve()) for c, f in videos.items()} != \
                {c: str(Path(f).resolve()) for c, f in old.items()} or (t and "layout" in out):
            out["videos"] = videos
        if t is None or self.name.text().strip() != t.name:
            out["name"] = self.name.text()
        if t is None or self.date.text().strip() != t.date:
            out["date"] = self.date.text().strip()
        accept = not self.accept_layout.isHidden() and self.accept_layout.isChecked()
        if t is None or accept != t.layout_override:
            out["accept"] = accept
        return out

    def resets(self, changes: dict) -> list[str]:
        """The changes that the trial's results were computed from (empty: the results stay)."""
        if self.trial is None or not workflow.trial_has_results(self.session, self.label):
            return []
        return [what for key, what in (("participant", "participant"), ("layout", "layout"), ("videos", "videos"))
                if key in changes]

    def _ask_reset(self, what: list[str]) -> bool:
        label, code = self.label, self.trial.code
        if not confirm(self, "The trial's results will be deleted",
                       f"Trial {label} ({html.escape(code)}) has results computed from its {' and '.join(what)}. "
                       f"Changing {'it' if len(what) == 1 else 'them'} means those results no longer fit: they are "
                       "deleted (2D keypoints, synchronisation, 3D markers, joint angles and the logs), and the trial "
                       "runs again from the start. Its copied videos, calibration and details are kept; exported "
                       "copies stay until you export again.", "Delete results and continue ..."):
            return False
        return confirm(self, "Delete the results?",
                       f"Delete the results of trial {label} ({html.escape(code)})? This cannot be undone.",
                       "Delete results")

    def _save(self, check: bool) -> None:
        changes = self.changes()
        what = self.resets(changes)
        if what and not self._ask_reset(what):
            return
        ses, root = self.session, self.root
        try:
            if self.trial is None:
                who = changes["participant"]
                trial = workflow.add_trial(ses, root, participant=None if who == NEW else who,
                                           layout=changes["layout"], name=changes["name"],
                                           date=changes["date"] or None)
                label = trial.label
            else:
                label = self.label
                if what:
                    workflow.clear_results(ses, root, label)
                if "participant" in changes:
                    workflow.assign_trial(ses, root, label, changes["participant"])
                if "layout" in changes and changes["layout"]:
                    workflow.set_trial_layout(ses, root, label, changes["layout"])
                if "name" in changes or "date" in changes:
                    workflow.set_trial_info(ses, root, label, name=changes.get("name"), date=changes.get("date"))
            self.saved = label
            videos = {(c, label): Path(f) for c, f in changes.get("videos", {}).items()
                      if Path(f).resolve() != Path(ses.trial(label).videos.get(c, "") or ".").resolve()}
            if videos:
                workflow.set_mapping(ses, videos, root)
                self.section.checks.pop(label, None)  # a new video: checked again
            if changes.get("accept") or (self.trial is not None and "accept" in changes):
                workflow.accept_layout(ses, root, label, bool(changes.get("accept")))
            self.section.win.save()
        except ERRORS as exc:
            error_box(self, "Trial not saved" if self.trial else "Trial not added", str(exc).strip("'\""))
            if self.saved is not None:  # added, but a video or the layout choice could not be set: show it
                self.accept()
            return
        self.check_after = check
        self.accept()
