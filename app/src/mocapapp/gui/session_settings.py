"""Session settings (PROJECT.md §16.3): output folder, study code, trials per participant, notes, processing settings
and deletion of raw footage (R13). Changes go through core functions when OK is pressed."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core import runner, workflow
from mocapapp.core.session import Session
from mocapapp.core.workflow import MAX_TRIALS
from mocapapp.gui.common import error_box, note, para

WHEN = workflow.DELETE_WHEN


class SessionSettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None, session: Session, root: Path):
        super().__init__(parent)
        self.session, self.root = session, root
        self.move_requested = False
        self.setWindowTitle("Session settings")
        self.resize(760, 720)
        inner = QWidget()
        layout = QVBoxLayout(inner)

        project = QGroupBox("Project")
        form = QFormLayout(project)
        folder_row = QHBoxLayout()
        folder = QLabel(html.escape(str(root)))
        folder.setWordWrap(True)
        move = QPushButton("Change output folder ...")
        move.setToolTip("Moves this project folder, with everything in it, into another output folder.")
        move.clicked.connect(self._move)
        folder_row.addWidget(folder, 1)
        folder_row.addWidget(move)
        form.addRow("Project folder", folder_row)
        self.study = QLineEdit(session.study_code)
        self.study.setMaxLength(8)
        self.study.setPlaceholderText("optional, e.g. PA26")
        form.addRow("Study code", self.study)
        form.addRow(para("Used in the pseudonyms of new participants only: existing pseudonyms never change."))
        self.trials = QSpinBox()
        self.trials.setRange(1, MAX_TRIALS)
        self.trials.setValue(max(1, session.trials_per_participant))
        form.addRow("Trials per participant", self.trials)
        self.notes = QPlainTextEdit(session.notes)
        self.notes.setPlaceholderText("Your notes on this project (kept in the project file)")
        self.notes.setFixedHeight(80)
        form.addRow("Notes", self.notes)
        layout.addWidget(project)

        proc = QGroupBox("Processing")
        pf = QFormLayout(proc)
        st = session.settings
        self.mode = QComboBox()
        self.mode.addItems(["lightweight", "balanced", "performance"])
        self.mode.setCurrentText(st.pose_mode)
        self.keypoints = QComboBox()
        for key, title in runner.KEYPOINT_TITLES.items():
            self.keypoints.addItem(title, key)
        self.keypoints.setCurrentIndex(max(0, self.keypoints.findData(st.keypoints)))
        self.cutoff = QDoubleSpinBox()
        self.cutoff.setRange(1, 30)
        self.cutoff.setSuffix(" Hz")
        self.cutoff.setValue(st.filter.cutoff_hz)
        self.no_aug = QCheckBox("Compute kinematics from the filtered keypoints instead of the LSTM-augmented markers")
        self.no_aug.setChecked(not st.use_augmentation)
        self.body_model = QComboBox()
        self.body_model.addItem("Full (muscles, joint constraints)", False)
        self.body_model.addItem("Simple (over 10x faster, no muscles, stiffer spine and shoulders)", True)
        self.body_model.setCurrentIndex(1 if st.simple_model else 0)
        self.overlay = QCheckBox("Save overlay videos (the video with the detected skeleton drawn on it)")
        self.overlay.setChecked(st.save_overlay)
        self.keep = QCheckBox("Keep the 2D keypoint files of finished trials")
        self.keep.setChecked(st.keep_keypoints)
        pf.addRow("Pose detection", self.mode)
        pf.addRow("Keypoints", self.keypoints)
        pf.addRow(para("Body with feet (26 points) suits most studies. Whole body adds hands and face (133 points; "
                       "slower, a larger download); 'with wrists' keeps only two points per hand."))
        pf.addRow("Smoothing cut-off", self.cutoff)
        pf.addRow(para("6 Hz suits walking and other slow movement; fast movements need 6-15 Hz. It must stay "
                       "below half the video frame rate (below 15 Hz for 30 fps video)."))
        pf.addRow(self.no_aug)
        pf.addRow("OpenSim body model", self.body_model)
        pf.addRow(self.overlay)
        pf.addRow(self.keep)
        pf.addRow(note("Trials already finished with other settings become 'out of date'; running them again "
                       "repeats only the stages these settings change."))
        layout.addWidget(proc)

        raw = QGroupBox("Raw footage")
        rf = QVBoxLayout(raw)
        self.delete_all = QCheckBox("Delete raw footage for all participants")
        self.delete_all.setChecked(st.delete_raw_all)
        self.delete_all.setToolTip("Also for participants who agreed to its storage. Turned off again, each "
                                   "participant's consent decides.")
        rf.addWidget(self.delete_all)
        when = QHBoxLayout()
        when.addWidget(QLabel("When"))
        self.when = QComboBox()
        for key, text in WHEN.items():
            self.when.addItem(text, key)
        self.when.setCurrentIndex(max(0, self.when.findData(st.delete_raw_when)))
        when.addWidget(self.when)
        when.addStretch()
        rf.addLayout(when)
        rf.addWidget(note("Deletion is offered (Results, and File > Participant data and deletion) once each trial's "
                          "results are complete and readable, and with 'after the results are exported' once they "
                          "are also exported. Results stay in the project and can be exported later. Nothing is "
                          "deleted without your confirmation."))
        layout.addWidget(raw)
        layout.addStretch()

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(inner)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        outer = QVBoxLayout(self)
        outer.addWidget(area, 1)
        outer.addWidget(buttons)

    def _move(self) -> None:
        """The move needs this dialog closed (it reopens the project from its new place): unsaved changes first."""
        if self._apply():
            self.move_requested = True
            self.accept()

    def processing(self) -> dict:
        return {"pose_mode": self.mode.currentText(), "keypoints": self.keypoints.currentData(),
                "cutoff_hz": self.cutoff.value(), "use_augmentation": not self.no_aug.isChecked(),
                "simple_model": bool(self.body_model.currentData()), "save_overlay": self.overlay.isChecked(),
                "keep_keypoints": self.keep.isChecked()}

    def _apply(self) -> bool:
        st = self.session.settings
        current = {"pose_mode": st.pose_mode, "keypoints": st.keypoints, "cutoff_hz": st.filter.cutoff_hz,
                   "use_augmentation": st.use_augmentation, "simple_model": st.simple_model,
                   "save_overlay": st.save_overlay, "keep_keypoints": st.keep_keypoints}
        changed = {k: v for k, v in self.processing().items() if v != current[k]}
        try:
            workflow.set_project_info(self.session, self.root, self.study.text(), self.trials.value(),
                                      self.notes.toPlainText())
            stale = workflow.set_settings(self.session, self.root, **changed) if changed else []
            when = self.when.currentData()
            if self.delete_all.isChecked() != st.delete_raw_all or when != st.delete_raw_when:
                workflow.set_delete_raw_all(self.session, self.root, self.delete_all.isChecked(), when)
        except ValueError as exc:  # SetupError, or a study code that is not letters and digits
            error_box(self, "Settings not saved", str(exc))
            return False
        if stale:
            QMessageBox.information(self, "Trials out of date", f"Trial(s) {', '.join(stale)} ran with the old "
                                    "settings and are now out of date. Run them again to update their results.")
        return True

    def _accept(self) -> None:
        if self._apply():
            self.accept()
