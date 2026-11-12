"""Steps 3-5: lens calibration (§6.3), reference points (§6.4), camera positions (§6.5)."""

from __future__ import annotations

import html

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QVBoxLayout,
)

from mocapapp.core.calibration import extrinsics as ex
from mocapapp.core.calibration import intrinsics as intr
from mocapapp.core.calibration import service
from mocapapp.core.calibration.scene_points import MIN_POINTS, check_scene
from mocapapp.core.session import Layout
from mocapapp.core.texts import s
from mocapapp.gui.common import Background, IssueView, clear_table, error_box, note, open_path, readonly, status_badge
from mocapapp.gui.main_window import Page


def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setWordWrap(True)
    t.verticalHeader().setVisible(False)
    return t


class _CalibPage(Page):
    def skipped(self) -> bool:
        return self.session is not None and self.session.calibration_source == "imported"


# ---------- intrinsics ----------

class IntrinsicsPage(_CalibPage):
    key, title = "intrinsics", "Lens calibration"

    def __init__(self, win):
        super().__init__(win)
        layout = QVBoxLayout(self)
        board = QGroupBox("Calibration board (must match the printed board)")
        bl = QHBoxLayout(board)
        form = QFormLayout()
        self.board_type = QComboBox()
        self.board_type.addItems(["charuco", "chess"])
        self.cols = QSpinBox()
        self.rows = QSpinBox()
        for spin in (self.cols, self.rows):
            spin.setRange(3, 30)
        self.square = QDoubleSpinBox()
        self.square.setRange(5, 200)
        self.square.setSuffix(" mm")
        form.addRow("Board type", self.board_type)
        form.addRow("Squares across", self.cols)
        form.addRow("Squares down", self.rows)
        form.addRow("Square size", self.square)
        bl.addLayout(form)
        side = QVBoxLayout()
        pdf = QPushButton("Save the board PDF ...")
        pdf.clicked.connect(self.win.save_board_pdf)
        side.addWidget(pdf)
        side.addWidget(note("Changing these settings means the lens calibration must be recomputed. "
                            "Lens calibration survives moving a camera, but not changing its zoom, resolution, "
                            "focus mode, stabilisation or lens."))
        bl.addLayout(side, 1)
        for w in (self.board_type, self.cols, self.rows, self.square):
            (w.currentIndexChanged if isinstance(w, QComboBox) else w.valueChanged).connect(self._board_changed)
        layout.addWidget(board)

        row = QHBoxLayout()
        self.run_all = QPushButton("Calibrate all cameras")
        self.run_sel = QPushButton("Recalculate selected")
        self.choose = QPushButton("Choose another intrinsic video ...")
        self.every = QDoubleSpinBox()
        self.every.setRange(0.2, 5)
        self.every.setValue(1.0)
        self.every.setSuffix(" s")
        self.every.setToolTip("A frame is examined every this many seconds of video.")
        for b in (self.run_all, self.run_sel, self.choose):
            row.addWidget(b)
        row.addStretch()
        row.addWidget(QLabel("Use a frame every"))
        row.addWidget(self.every)
        layout.addLayout(row)
        self.run_all.clicked.connect(lambda: self.calibrate(None, False))
        self.run_sel.clicked.connect(lambda: self.calibrate(self._selected(), True))
        self.choose.clicked.connect(self._choose_video)
        self.table = _table(["Camera", "Result", "Error", "Frames used", "Coverage", "Tilt spread", "Advice"])
        layout.addWidget(self.table, 1)
        self.progress = QLabel()
        layout.addWidget(self.progress)

    def _selected(self) -> list[str]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.session.cameras[r].name for r in rows]

    def _board_changed(self) -> None:
        if self.session is None or self._loading:
            return
        st = self.session.settings
        st.board_type = self.board_type.currentText()
        st.corners_nb = (self.rows.value() - 1, self.cols.value() - 1)
        st.square_size_mm = self.square.value()
        self.win.save()

    def refresh(self) -> None:
        if self.session is None:
            return
        self._loading = True
        st = self.session.settings
        self.board_type.setCurrentText(st.board_type)
        self.cols.setValue(st.corners_nb[1] + 1)
        self.rows.setValue(st.corners_nb[0] + 1)
        self.square.setValue(st.square_size_mm)
        self._loading = False
        work = Layout(self.root).calibration_work
        cams = self.session.cameras
        self.table.setRowCount(len(cams))
        for r, cam in enumerate(cams):
            res = intr.load_result(work, cam.name)
            self.table.setItem(r, 0, readonly(cam.name))
            if res is None:
                self.table.setItem(r, 1, readonly("not calibrated yet"))
                for c in range(2, 7):
                    self.table.setItem(r, c, readonly(""))
                continue
            word = {"ok": "PASS", "warn": "CHECK (usable)", "fail": "FAIL"}[res.status]
            item = readonly(word)
            item.setToolTip(res.status)
            self.table.setItem(r, 1, item)
            self.table.setItem(r, 2, readonly(f"{res.rms_px:.2f} px"))
            self.table.setItem(r, 3, readonly(f"{res.n_used} of {res.n_frames}"))
            self.table.setItem(r, 4, readonly(f"{res.coverage:.0%}"))
            self.table.setItem(r, 5, readonly(f"{res.tilt_spread_deg:.0f} deg"))
            self.table.setItem(r, 6, readonly("\n".join(res.advice)))
        self.table.resizeRowsToContents()

    def is_complete(self) -> bool:
        if self.session is None:
            return False
        state = service.calibration_state(self.session, self.root)
        return all(not v.startswith("intrinsics") for v in state.values())

    _loading = False

    def calibrate(self, cameras: list[str] | None, force: bool) -> None:
        if cameras == []:
            error_box(self, "No camera selected", "Select a camera row first.")
            return
        self.win.set_busy(True)
        self.progress.setText("Detecting the board ... this takes about a minute per camera.")

        def done(_results):
            self.win.set_busy(False)
            self.win.save()
            self.progress.setText("")
            self.refresh()
            self.completeChanged.emit()

        def failed(msg, details):
            self.win.set_busy(False)
            self.progress.setText("")
            error_box(self, "Lens calibration stopped", msg, details)
            self.refresh()

        Background(self, service.run_intrinsics, self.session, self.root, cameras, self.every.value(), force,
                   on_done=done, on_error=failed, on_progress=self.progress.setText, progress=True)

    def _choose_video(self) -> None:
        cams = self._selected()
        if len(cams) != 1:
            error_box(self, "Select one camera", "Select the row of the camera whose video you want to replace.")
            return
        # the Videos page owns choosing files and copying them into the session
        self.win.go(1)
        self.win.pages[1].choose(cams[0], "intrinsic")


# ---------- reference points ----------

class ScenePage(_CalibPage):
    key, title = "scene", "Reference points"

    def __init__(self, win):
        super().__init__(win)
        layout = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        edit = QPushButton("Edit reference points ...")
        edit.clicked.connect(self.edit)
        layout.addWidget(edit)
        self.issues = IssueView(min_height=160)
        layout.addWidget(self.issues)
        layout.addWidget(note(f"At least {MIN_POINTS} measured points; each camera must later see at least 6 of "
                              "them. These points are shared by all cameras and all trials of this session."))
        layout.addStretch()

    def _scene(self):
        try:
            return service.get_scene(self.root)
        except service.CalibrationStepError:
            return None

    def refresh(self) -> None:
        if self.session is None:
            return
        scene = self._scene()
        if scene is None:
            self.summary.setText("No reference points yet.")
            self.issues.setHtml("")
            return
        self.summary.setText(f"<b>{len(scene.points)} points.</b> Origin: {html.escape(scene.origin_text or '?')}. "
                             f"+X toward: {html.escape(scene.x_axis_text or '?')}. Z is up.")
        self.issues.show_issues(check_scene(scene), "The reference points look fine.")

    def is_complete(self) -> bool:
        scene = self._scene() if self.session is not None else None
        return scene is not None and not any(i.level in ("error", "action") for i in check_scene(scene))

    def edit(self) -> None:
        from mocapapp.gui.scene_dialog import SceneDialog

        dlg = SceneDialog(self, self._scene(), has_extrinsics=bool(self.session.extrinsics))
        if dlg.exec():
            removed = service.set_scene(self.root, dlg.scene())
            if removed:
                self.session.extrinsics = []
                self.win.save()
            self.refresh()
            self.completeChanged.emit()


# ---------- extrinsics ----------

class ExtrinsicsPage(_CalibPage):
    key, title = "extrinsics", "Camera positions"

    def __init__(self, win):
        super().__init__(win)
        layout = QVBoxLayout(self)
        layout.addWidget(note(s("cameras_stay_put") + " The camera positions found here are used for every trial "
                              "of this session.", "important"))
        self.table = _table(["Camera", "Result", "Points", "Error", "Camera position (m)", "", "", "Notes"])
        layout.addWidget(self.table, 1)
        self.state = QLabel()
        self.state.setWordWrap(True)
        layout.addWidget(self.state)

    def refresh(self) -> None:
        if self.session is None:
            return
        work = Layout(self.root).calibration_work
        cams = self.session.cameras
        clear_table(self.table)
        self.table.setRowCount(len(cams))
        for r, cam in enumerate(cams):
            res = ex.load_result(work, cam.name)
            self.table.setItem(r, 0, readonly(cam.name))
            click = QPushButton("Click points ...")
            click.clicked.connect(lambda _=False, c=cam.name: self.click(c))
            self.table.setCellWidget(r, 5, click)
            review = QPushButton("Review image")
            path = work / f"extrinsic_{cam.name}_review.jpg"
            review.setEnabled(res is not None and path.is_file())
            review.clicked.connect(lambda _=False, p=path: open_path(p))
            self.table.setCellWidget(r, 6, review)
            if res is None:
                self.table.setItem(r, 1, readonly("not done yet"))
                for c in (2, 3, 4, 7):
                    self.table.setItem(r, c, readonly(""))
                continue
            word = {"ok": "PASS", "warn": "CHECK (usable)", "fail": "FAIL" + (" (accepted)" if res.overridden
                                                                              else " (blocks the run)")}[res.status]
            self.table.setItem(r, 1, readonly(word))
            self.table.setItem(r, 2, readonly(str(res.n_points)))
            self.table.setItem(r, 3, readonly(f"{res.rms_cm:.2f} cm ({res.rms_px:.1f} px)"))
            self.table.setItem(r, 4, readonly(", ".join(f"{v:.2f}" for v in res.camera_position)))
            self.table.setItem(r, 7, readonly("\n".join(res.warnings)))
        self.table.resizeRowsToContents()
        self._write_if_ready()

    def _write_if_ready(self) -> None:
        state = service.calibration_state(self.session, self.root)
        waiting = {c: v for c, v in state.items() if v != "ready"}
        if waiting:
            self.state.setText("Still to do: " + "; ".join(f"{c}: {v}" for c, v in waiting.items()))
            return
        calib = Layout(self.root).calib_file
        if not calib.is_file():
            try:
                service.write_calibration(self.session, self.root)
            except (service.CalibrationStepError, ValueError) as exc:
                self.state.setText(f"{status_badge('fail')} The calibration file could not be written: "
                                   f"{html.escape(str(exc))}")
                return
        self.state.setText(f"{status_badge('done')} All cameras calibrated. Calibration file: {calib}")

    def is_complete(self) -> bool:
        if self.session is None:
            return False
        state = service.calibration_state(self.session, self.root)
        return all(v == "ready" for v in state.values()) and Layout(self.root).calib_file.is_file()

    def click(self, camera: str) -> None:
        from mocapapp.gui.extrinsic_dialog import ExtrinsicDialog

        try:
            scene = service.get_scene(self.root)
            video = service.input_video(self.root, camera, "extrinsic")
            K = intr.load_result(Layout(self.root).calibration_work, camera)
        except service.CalibrationStepError as exc:
            error_box(self, "Not ready", str(exc))
            return
        if K is None:
            error_box(self, "Not ready", f"{camera}: do the lens calibration first.")
            return
        previous = ex.load_result(Layout(self.root).calibration_work, camera)
        dlg = ExtrinsicDialog(self, camera, video, scene, K, previous)
        if dlg.exec():
            try:
                service.run_extrinsics(self.session, self.root, camera, dlg.clicks(), dlg.frame_index,
                                       override=dlg.override)
            except (ex.ExtrinsicsError, service.CalibrationStepError) as exc:
                error_box(self, "Camera position not saved", str(exc))
            self.win.save()
            self.refresh()
            self.completeChanged.emit()
