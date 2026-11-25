"""Cameras section (PROJECT.md §16.3, R5/R6): the project's cameras, their intrinsic videos and lens calibration,
and the calibration board."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core import workflow
from mocapapp.core.calibration import service
from mocapapp.core.session import Camera
from mocapapp.core.texts import s
from mocapapp.core.workflow import SetupError
from mocapapp.gui.common import Background, clear_table, confirm, error_box, for_each, note, readonly, selected_rows
from mocapapp.gui.main_window import Section
from mocapapp.gui.sections.session import scroll

VIDEO_FILTER = "Videos (*.mp4 *.mov *.avi *.mkv *.m4v *.MP4 *.MOV *.AVI *.MKV *.M4V);;All files (*)"
OPTIONAL = (("resolution", "Resolution", "e.g. 1920x1080"), ("zoom", "Zoom", "e.g. wide, 1x"),
            ("focus", "Focus", "e.g. fixed, auto"), ("stabilisation", "Stabilisation", "e.g. off"),
            ("fps", "Frame rate", "e.g. 60"), ("notes", "Notes", ""))
RESULT_WORD = {"ok": "PASS", "warn": "CHECK (usable)", "fail": "FAIL"}

CAL_ERRORS = (SetupError, service.CalibrationStepError, KeyError, ValueError, OSError)


def table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)  # Ctrl/Shift-click: several rows
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setWordWrap(True)
    t.verticalHeader().setVisible(False)
    return t


def choose_video(parent: QWidget, title: str, start: str = "") -> Path | None:
    name, _ = QFileDialog.getOpenFileName(parent, title, start or str(Path.home()), VIDEO_FILTER)
    return Path(name) if name else None


class CameraDialog(QDialog):
    """Add or edit a camera: its name, folder, intrinsic video (when it can still change) and optional fields."""

    def __init__(self, parent: QWidget | None, cam: Camera | None = None, locked: str = ""):
        super().__init__(parent)
        self.setWindowTitle(f"Camera {cam.id}" if cam else "Add a camera")
        self.resize(620, 0)
        form = QFormLayout(self)
        self.name = QLineEdit(cam.name if cam else "")
        self.name.setPlaceholderText("e.g. GoPro left (empty: its id)")
        form.addRow("Name", self.name)
        folder_row = QHBoxLayout()
        self.folder = QLineEdit(cam.source_dir if cam else "")
        self.folder.setPlaceholderText("optional: where this camera's videos are")
        browse = QPushButton("Browse ...")
        browse.clicked.connect(self._browse_folder)
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(browse)
        form.addRow("Folder", folder_row)
        video_row = QHBoxLayout()
        self.video = QLineEdit(cam.intrinsic.file if cam and cam.intrinsic else "")
        self.video.setPlaceholderText("the video of the board (a video named 'intrinsic' in its folder is found)")
        pick = QPushButton("Choose ...")
        pick.clicked.connect(self._browse_video)
        video_row.addWidget(self.video, 1)
        video_row.addWidget(pick)
        form.addRow("Intrinsic video", video_row)
        if locked:
            for w in (self.video, pick):
                w.setEnabled(False)
            form.addRow(note(locked))
        self.fields: dict[str, QLineEdit] = {}
        for key, title, hint in OPTIONAL:
            edit = QLineEdit(getattr(cam, key) if cam else "")
            edit.setPlaceholderText(hint or "optional")
            self.fields[key] = edit
            form.addRow(title, edit)
        form.addRow(note("Zoom, resolution, focus mode and stabilisation change the lens calibration: a camera "
                         "recorded with other settings is another camera here (duplicate it)."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Camera folder", self.folder.text() or str(Path.home()))
        if folder:
            self.folder.setText(folder)

    def _browse_video(self) -> None:
        path = choose_video(self, "Intrinsic video (the board)", self.video.text() or self.folder.text())
        if path:
            self.video.setText(str(path))

    def values(self) -> dict:
        return {"name": self.name.text().strip(), "source_dir": self.folder.text().strip(),
                **{k: e.text().strip() for k, e in self.fields.items()}}

    def intrinsic(self) -> Path | None:
        text = self.video.text().strip()
        return Path(text) if text else None


class CamerasSection(Section):
    key, title = "cameras", "Cameras"
    purpose = "Every camera of the project and its lens (intrinsic) calibration, used by all layouts it is in."

    def __init__(self, win):
        super().__init__(win)
        self._loading = False
        inner = QWidget()
        layout = QVBoxLayout(inner)
        self.imported_note = note("This project uses an imported calibration (sample data): its cameras are not "
                                  "calibrated here.", "important")
        layout.addWidget(self.imported_note)
        layout.addWidget(note(s("intrinsic_content")))

        row = QHBoxLayout()
        self.buttons: dict[str, QPushButton] = {}
        for key, text, slot in (("add", "Add camera ...", self.add), ("edit", "Edit ...", self.edit),
                                ("duplicate", "Duplicate", self.duplicate), ("delete", "Delete", self.delete),
                                ("import", "Import from another project ...", lambda: self.win.import_dialog())):
            b = QPushButton(text)
            b.clicked.connect(slot)
            self.buttons[key] = b
            row.addWidget(b)
        row.addStretch()
        layout.addLayout(row)
        self.table = table(["Camera", "Name", "Intrinsic video", "Lens", "Error", "Frames used", "Coverage",
                            "Tilt spread", "Layouts", "Advice"])
        self.table.itemSelectionChanged.connect(self._enable)
        self.table.doubleClicked.connect(lambda _: self.edit())
        layout.addWidget(self.table, 1)
        layout.addWidget(note("A camera used by a trial that has started keeps its lens calibration. To calibrate "
                              "it again (other zoom or resolution, a new video), duplicate it and calibrate the "
                              "copy."))

        cal = QHBoxLayout()
        self.calibrate_all = QPushButton("Calibrate all cameras")
        self.calibrate_all.setToolTip("Every camera with an intrinsic video and no lens calibration yet.")
        self.calibrate_sel = QPushButton("Calibrate selected again")
        self.calibrate_all.clicked.connect(lambda: self.calibrate(None, False))
        self.calibrate_sel.clicked.connect(lambda: self.calibrate(self.selected(), True))
        self.every = QDoubleSpinBox()
        self.every.setRange(0.2, 5)
        self.every.setValue(1.0)
        self.every.setSuffix(" s")
        self.every.setToolTip("A frame is examined every this many seconds of video.")
        cal.addWidget(self.calibrate_all)
        cal.addWidget(self.calibrate_sel)
        cal.addStretch()
        cal.addWidget(QLabel("Use a frame every"))
        cal.addWidget(self.every)
        layout.addLayout(cal)
        self.progress = QLabel()
        self.progress.setWordWrap(True)
        layout.addWidget(self.progress)

        board = QGroupBox("Calibration board (must match the printed board)")
        bl = QHBoxLayout(board)
        form = QFormLayout()
        self.board_type = QComboBox()
        self.board_type.addItems(["charuco", "chess"])
        self.cols, self.rows = QSpinBox(), QSpinBox()
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
        side.addWidget(note("Lens calibrations made with another board stay until the cameras are calibrated "
                            "again."))
        pdf = QPushButton("Board PDF ...")
        pdf.clicked.connect(lambda: self.win.save_board_pdf())
        side.addWidget(pdf)
        side.addStretch()
        bl.addLayout(side, 1)
        for w in (self.board_type, self.cols, self.rows, self.square):
            (w.currentIndexChanged if isinstance(w, QComboBox) else w.valueChanged).connect(self._board_changed)
        layout.addWidget(board)
        self.board = board
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll(inner))

    # ---------- view ----------

    def refresh(self) -> None:
        if self.session is None:
            return
        ses = self.session
        imported = ses.calibration_source == "imported"
        self.imported_note.setVisible(imported)
        self._loading = True
        st = ses.settings
        self.board_type.setCurrentText(st.board_type)
        self.cols.setValue(st.corners_nb[1] + 1)
        self.rows.setValue(st.corners_nb[0] + 1)
        self.square.setValue(st.square_size_mm)
        self._loading = False
        clear_table(self.table)
        for cam in ses.cameras:
            r = self.table.rowCount()
            self.table.insertRow(r)
            res = service.lens_result(self.root, cam.id)
            layouts = ", ".join(lay.name for lay in ses.layouts_with_camera(cam.id)) or "-"
            if ses.camera_used(cam.id):
                layouts += " (a trial ran: lens locked)"
            if res is None:
                lens = "no intrinsic video" if cam.intrinsic is None else "not calibrated yet"
                cells = [lens, "", "", "", "", layouts, ""]
            else:
                cells = [RESULT_WORD[res.status], f"{res.rms_px:.2f} px", f"{res.n_used} of {res.n_frames}",
                         f"{res.coverage:.0%}", f"{res.tilt_spread_deg:.0f} deg", layouts, "\n".join(res.advice)]
            video = Path(cam.intrinsic.file).name if cam.intrinsic else "-"
            for c, text in enumerate([cam.id, cam.name, video, *cells]):
                item = readonly(text)
                if c == 2 and cam.intrinsic:
                    item.setToolTip(cam.intrinsic.file)
                self.table.setItem(r, c, item)
        self.table.resizeRowsToContents()
        self.board.setEnabled(not imported)
        self._enable()

    def selected(self) -> list[str]:
        rows = selected_rows(self.table)
        return [self.session.cameras[r].id for r in rows if self.session and r < len(self.session.cameras)]

    def _enable(self) -> None:
        ses = self.session
        imported = ses is not None and ses.calibration_source == "imported"
        n = len(self.selected())
        self.buttons["edit"].setEnabled(n == 1 and not self.win.busy)
        for key in ("duplicate", "delete"):
            self.buttons[key].setEnabled(n > 0 and not self.win.busy)
        self.buttons["add"].setEnabled(not imported)
        self.calibrate_all.setEnabled(not imported and ses is not None and any(c.intrinsic for c in ses.cameras))
        self.calibrate_sel.setEnabled(not imported and bool(self.selected()))

    def _changed(self, select: list[str] | None = None) -> None:
        self.refresh()
        ids = [c.id for c in self.session.cameras] if self.session else []
        for cid in select or []:  # keep (or move) the selection to these cameras
            if cid in ids:
                self.table.selectionModel().select(
                    self.table.model().index(ids.index(cid), 0),
                    self.table.selectionModel().SelectionFlag.Select | self.table.selectionModel().SelectionFlag.Rows)
        self.completeChanged.emit()
        self.win.changed()

    # ---------- actions ----------

    def add(self) -> None:
        dlg = CameraDialog(self)
        if not dlg.exec():
            return
        try:
            cam = workflow.add_camera(self.session, self.root, dlg.intrinsic(), **dlg.values())
            if cam.intrinsic is None and cam.source_dir:  # an 'intrinsic' video in its folder is taken as obvious
                from mocapapp.core.validate import autofill

                autofill(self.session, trials=False)
                self.win.save()
        except CAL_ERRORS as exc:
            error_box(self, "Camera not added", str(exc))
        self._changed()

    def edit(self) -> None:
        ids = self.selected()
        if len(ids) != 1:
            return
        cam = self.session.camera(ids[0])
        locked = ""
        if self.session.calibration_source == "imported":
            locked = "The sample data's calibration is imported."
        elif self.session.camera_used(cam.id):
            locked = "A trial with this camera has started: its intrinsic video stays. Duplicate the camera instead."
        dlg = CameraDialog(self, cam, locked)
        if not dlg.exec():
            return
        try:
            workflow.edit_camera(self.session, self.root, cam.id, **dlg.values())
            new = dlg.intrinsic()
            old = Path(cam.intrinsic.file) if cam.intrinsic else None
            if not locked and new is not None and (old is None or new.resolve() != old.resolve()):
                workflow.set_intrinsic_video(self.session, self.root, cam.id, new)
        except CAL_ERRORS as exc:
            error_box(self, "Camera not saved", str(exc))
        self._changed()

    def duplicate(self) -> None:
        """Each selected camera, with its intrinsic video and lens calibration (R5/R6)."""
        ids = self.selected()
        copies = for_each(self, "Not duplicated", ids,
                          lambda cid: workflow.duplicate_camera(self.session, self.root, cid), CAL_ERRORS)
        if copies:
            self.win.statusBar().showMessage(
                "Duplicated: " + ", ".join(f"{a} as {c.id}" for a, c in zip(ids, copies, strict=False)), 6000)
        self._changed([c.id for c in copies])

    def delete(self) -> None:
        ids = self.selected()
        if not ids:
            return
        names = ", ".join(html.escape(service.camera_title(self.session, c)) for c in ids)
        what = "the camera" if len(ids) == 1 else f"{len(ids)} cameras"
        if not confirm(self, f"Delete {what}?", f"Delete {names} and their lens calibration from this project? "
                       "Their videos in their own folders are not touched.", "Delete"):
            return
        for_each(self, "Not deleted", ids, lambda cid: workflow.delete_camera(self.session, self.root, cid),
                 CAL_ERRORS)
        self._changed()

    def _board_changed(self) -> None:
        if self.session is None or self._loading:
            return
        try:
            workflow.set_board(self.session, self.root, self.board_type.currentText(), self.cols.value(),
                               self.rows.value(), self.square.value())
        except SetupError as exc:
            error_box(self, "Board not saved", str(exc))

    def calibrate(self, cameras: list[str] | None, force: bool) -> Background | None:
        """Copy the intrinsic videos into the project and compute the lens calibrations, in the background."""
        if cameras == []:
            error_box(self, "No camera selected", "Select a camera row first.")
            return None
        self.win.set_busy(True)
        self.progress.setText("Detecting the board ... this takes about a minute per camera.")

        def done(results):
            self.win.set_busy(False)
            self.progress.setText(f"{len(results)} camera(s) calibrated." if results else "")
            self._changed()

        def failed(msg, details):
            self.win.set_busy(False)
            self.progress.setText("")
            error_box(self, "Lens calibration stopped", msg, details)
            self._changed()

        self.job = Background(self, workflow.calibrate_cameras, self.session, self.root, cameras, self.every.value(),
                              force, on_done=done, on_error=failed, on_progress=self.progress.setText, progress=True)
        return self.job
