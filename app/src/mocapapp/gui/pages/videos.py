"""Step 2: check the videos, choose missing calibration videos (D3), confirm the trial mapping (D7), and copy the
videos into the session (§6.2 rules 1-7)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core.assemble import plan_inputs
from mocapapp.core.session import save_session
from mocapapp.core.validate import scan_dir, validate
from mocapapp.core.workflow import SetupError, inputs_ready, pick_video, prepare_session, set_mapping
from mocapapp.gui.common import Background, IssueView, clear_table, error_box, fit_rows, note, readonly
from mocapapp.gui.main_window import Page

LOW = QColor("#fff3cd")


class VideosPage(Page):
    key, title = "videos", "Videos"

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

        mapping = QGroupBox("Trial mapping: which file is which trial, per camera")
        ml = QVBoxLayout(mapping)
        self.mapping_note = QLabel()
        self.mapping_note.setWordWrap(True)
        ml.addWidget(self.mapping_note)
        self.grid = QTableWidget(0, 0)
        ml.addWidget(self.grid)
        self.save_mapping_button = QPushButton("Save this mapping")
        self.save_mapping_button.clicked.connect(self.save_mapping)
        ml.addWidget(self.save_mapping_button)
        layout.addWidget(mapping)

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
        done = self.is_complete()
        self.status.setText("Videos are in the session. Check again after changing anything." if done else
                            "Press <b>Check videos</b> to inspect every file (this reads each video once).")
        self._fill_calib()
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

    def _fill_grid(self) -> None:
        trials, cams = self.session.trials, self.session.cameras
        self.grid.clear()
        self.grid.setRowCount(len(trials))
        self.grid.setColumnCount(len(cams))
        self.grid.setHorizontalHeaderLabels([c.name for c in cams])
        self.grid.setVerticalHeaderLabels([f"Trial {t.label} ({t.code})" for t in trials])
        self.grid.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        low = self.report.low_confidence if self.report else set()
        for c, cam in enumerate(cams):
            try:
                candidates = scan_dir(Path(cam.source_dir)).trials
            except OSError:
                candidates = []
            for r, t in enumerate(trials):
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

    def save_mapping(self) -> None:
        table = {}
        for c, cam in enumerate(self.session.cameras):
            for r, t in enumerate(self.session.trials):
                data = self.grid.cellWidget(r, c).currentData()
                if not data:
                    error_box(self, "Mapping incomplete", f"Choose a file for {cam.name}, trial {t.label}.")
                    return
                table[(cam.name, t.label)] = Path(data)
        for cam in self.session.cameras:
            files = [table[(cam.name, t.label)] for t in self.session.trials]
            if len(set(files)) != len(files):
                error_box(self, "Mapping not saved", f"{cam.name}: the same file is used for two trials.")
                return
        set_mapping(self.session, table, self.root)
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
