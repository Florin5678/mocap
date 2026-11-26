"""The trial panel (PROJECT.md §16.3), shown under the trials table for the selected trial: participant, layout,
date and name; the video of each camera of its layout (suggested ones highlighted); the check and copy results; what
it still needs; the layout-from-another-day warning (R8); its state and messages."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
)

from mocapapp.core import readiness, workflow
from mocapapp.core.session import format_height
from mocapapp.gui.common import IssueView, NoWheelCombo, clear_table, error_box, fit_rows, note, readonly, status_badge
from mocapapp.gui.sections.participants import footage_text

SUGGESTED = QColor("#e3f2fd")
ERRORS = (ValueError, KeyError, OSError)


def size(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"


def choose_video(parent, title: str, start: str = "") -> Path | None:
    from mocapapp.gui.sections.cameras import choose_video as pick

    return pick(parent, title, start)


class TrialPanel(QGroupBox):
    def __init__(self, section):
        super().__init__("Trial")
        self.section = section
        self.label: str | None = None
        self._loading = False
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.participant = NoWheelCombo()
        self.participant.setToolTip("The participant recorded in this trial (add participants in Participants).")
        self.participant.currentIndexChanged.connect(self._participant)
        self.layout_box = NoWheelCombo()
        self.layout_box.setToolTip("The camera layout the trial was recorded with. Its cameras decide which videos "
                                   "the trial needs.")
        self.layout_box.currentIndexChanged.connect(self._layout)
        self.date = QLineEdit()
        self.date.setPlaceholderText("YYYY-MM-DD (empty: the day the videos were recorded)")
        self.date.editingFinished.connect(self._date)
        self.name = QLineEdit()
        self.name.setPlaceholderText("free text, e.g. walking, fast")
        self.name.editingFinished.connect(self._name)
        form.addRow("Participant", self.participant)
        form.addRow("Layout", self.layout_box)
        form.addRow("Date", self.date)
        form.addRow("Name / note", self.name)
        layout.addLayout(form)

        warn_row = QHBoxLayout()
        self.warning = note("", "warning")
        self.accept_button = QPushButton("Use this layout anyway")
        self.accept_button.setToolTip("The cameras were not moved since the layout was made: its positions still "
                                      "hold. Recorded with the trial.")
        self.accept_button.clicked.connect(self._accept)
        warn_row.addWidget(self.warning, 1)
        warn_row.addWidget(self.accept_button)
        layout.addLayout(warn_row)

        layout.addWidget(QLabel("<b>Videos</b> (one per camera of the layout)"))
        self.videos = QTableWidget(0, 3)
        self.videos.setHorizontalHeaderLabels(["Camera", "Video", ""])
        self.videos.verticalHeader().setVisible(False)
        self.videos.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.videos.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.videos)
        row = QHBoxLayout()
        self.use_suggested = QPushButton("Use the suggested videos")
        self.use_suggested.setToolTip("Videos named with the trial's number in each camera's folder (highlighted).")
        self.use_suggested.clicked.connect(self._use_suggested)
        self.check_button = QPushButton("Check and copy videos")
        self.check_button.setToolTip("Checks the videos (frame rate, size against the camera's calibration, length) "
                                     "and copies them into the project.")
        self.check_button.clicked.connect(lambda: self.section.check_and_copy([self.label]))
        row.addWidget(self.use_suggested)
        row.addWidget(self.check_button)
        row.addStretch()
        layout.addLayout(row)
        self.copy = QLabel()
        self.copy.setWordWrap(True)
        layout.addWidget(self.copy)
        self.report = IssueView(min_height=60)
        self.report.setMaximumHeight(140)
        layout.addWidget(self.report)
        self.state = QLabel()
        self.state.setWordWrap(True)
        layout.addWidget(self.state)

    @property
    def session(self):
        return self.section.session

    @property
    def root(self):
        return self.section.root

    # ---------- showing a trial ----------

    def show_trial(self, label: str | None, suggested: dict) -> None:
        self.label = label
        self.setVisible(label is not None and self.session is not None)
        if label is None or self.session is None:
            return
        ses, root = self.session, self.root
        trial = ses.trial(label)
        self._loading = True
        self.setTitle(f"Trial {label} ({trial.code})")
        open_ = not trial.frozen
        self.participant.clear()
        for p in ses.active_participants:
            self.participant.addItem(p.code, p.label)
        if trial.withdrawn_at:
            self.participant.addItem(trial.code, trial.participant)
        self.participant.setCurrentIndex(max(0, self.participant.findData(trial.participant)))
        self.participant.setEnabled(open_ and not ses.run_state.stage_done.get(label))
        self.layout_box.clear()
        self.layout_box.addItem("(none)", "")
        for lay in ses.layouts:
            self.layout_box.addItem(f"{lay.name} ({lay.date})" if lay.date else lay.name, lay.id)
        self.layout_box.setCurrentIndex(max(0, self.layout_box.findData(trial.layout)))
        self.layout_box.setEnabled(open_)
        self.date.setText(trial.date)
        self.name.setText(trial.name)
        self._loading = False

        warning = ses.layout_warning(label)
        self.warning.setText(html.escape(warning))
        self.warning.setVisible(bool(warning))
        self.accept_button.setVisible(bool(warning))

        clear_table(self.videos)
        lay = ses.trial_layout(label)
        mine = {cam: f for (cam, lb), f in suggested.items() if lb == label}
        for slot, cam, _lc in ses.trial_cameras(label):
            r = self.videos.rowCount()
            self.videos.insertRow(r)
            self.videos.setItem(r, 0, readonly(f"{cam.name} ({slot})" if cam.name != cam.id else cam.id))
            chosen = trial.videos.get(cam.id)
            hint = mine.get(cam.id)
            item = readonly(Path(chosen).name if chosen else f"suggested: {hint.name}" if hint else "not chosen")
            item.setToolTip(chosen or (str(hint) if hint else ""))
            if hint is not None and not chosen:
                item.setBackground(QBrush(SUGGESTED))
            self.videos.setItem(r, 1, item)
            button = QPushButton("Choose ...")
            button.setEnabled(open_)
            button.clicked.connect(lambda _=False, c=cam.id: self._choose(c))
            self.videos.setCellWidget(r, 2, button)
        fit_rows(self.videos)
        self.videos.setVisible(lay is not None)
        self.use_suggested.setVisible(bool(mine) and open_)

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

    # ---------- changes ----------

    def _apply(self, title: str, action) -> None:
        if self._loading or self.label is None:
            return
        try:
            action()
        except ERRORS as exc:
            error_box(self, title, str(exc).strip("'\""))
        self.section.changed(keep=[self.label])

    def _participant(self) -> None:
        who = self.participant.currentData()
        if who and who != self.session.trial(self.label).participant:
            self._apply("Participant not changed",
                        lambda: workflow.assign_trial(self.session, self.root, self.label, who))

    def _layout(self) -> None:
        lid = self.layout_box.currentData()
        if lid is not None and lid != self.session.trial(self.label).layout and lid:
            self._apply("Layout not changed",
                        lambda: workflow.set_trial_layout(self.session, self.root, self.label, lid))

    def _date(self) -> None:
        if self.date.text().strip() != self.session.trial(self.label).date:
            self._apply("Date not changed",
                        lambda: workflow.set_trial_info(self.session, self.root, self.label,
                                                        date=self.date.text().strip()))

    def _name(self) -> None:
        if self.name.text().strip() != self.session.trial(self.label).name:
            self._apply("Name not changed",
                        lambda: workflow.set_trial_info(self.session, self.root, self.label, name=self.name.text()))

    def _accept(self) -> None:
        self._apply("Layout not accepted", lambda: workflow.accept_layout(self.session, self.root, self.label))

    def _choose(self, camera: str) -> None:
        cam = self.session.camera(camera)
        file = choose_video(self, f"Video of trial {self.label} from {cam.name}", cam.source_dir)
        if file is not None:
            self._set_videos({(camera, self.label): file})

    def _use_suggested(self) -> None:
        mine = {k: f for k, f in self.section.suggested.items() if k[1] == self.label}
        self._set_videos(mine)

    def _set_videos(self, table: dict) -> None:
        def save():
            workflow.set_mapping(self.session, table, self.root)
            self.section.win.save()
            self.section.checks.pop(self.label, None)  # a new video: checked again

        self._apply("Video not chosen", save)
