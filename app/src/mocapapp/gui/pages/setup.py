"""Step 1: session setup (§6.2) - folders and cameras; sync reminder always visible (D2). Participants are
entered with their trials in step 2 (Trials) since 0.1.0rc17."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
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

from mocapapp.core.texts import s
from mocapapp.core.validate import Issue
from mocapapp.core.workflow import (
    MAX_TRIALS,
    NewSession,
    SetupError,
    camera_number,
    check_new_session,
    create_session,
    sibling_camera_dirs,
)
from mocapapp.gui.common import (
    IssueView,
    error_box,
    fit_rows,
    last_output_root,
    note,
    para,
    readonly,
)
from mocapapp.gui.main_window import Page


def _scroll(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    return area


class SetupPage(Page):
    key, title = "setup", "Session"

    def __init__(self, win):
        super().__init__(win)
        self.modes = QStackedWidget()
        self.modes.addWidget(_scroll(self._new_form()))
        self.modes.addWidget(_scroll(self._existing_view()))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.modes)

    # ---------- new session ----------

    def _new_form(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        form = QFormLayout()
        out_row = QHBoxLayout()
        self.out_edit = QLineEdit(last_output_root())
        self.out_edit.setPlaceholderText("Folder where session folders are created (no letters like æ ø å)")
        self.out_edit.setToolTip("Starts as the output folder chosen in Help > Settings; change it here for this "
                                 "session only.")
        browse = QPushButton("Browse ...")
        browse.clicked.connect(lambda: self._browse_into(self.out_edit, "Output folder"))
        out_row.addWidget(self.out_edit, 1)
        out_row.addWidget(browse)
        form.addRow("Output folder", out_row)
        self.study_edit = QLineEdit()
        self.study_edit.setMaxLength(8)
        self.study_edit.setPlaceholderText("optional, e.g. PA26 (letters and digits)")
        form.addRow("Study code", self.study_edit)
        self.trials_spin = QSpinBox()
        self.trials_spin.setRange(1, MAX_TRIALS)
        self.trials_spin.setValue(1)
        self.trials_spin.valueChanged.connect(lambda _: self._check_new())
        form.addRow("Trials per participant", self.trials_spin)
        self.people_spin = QSpinBox()
        self.people_spin.setRange(1, MAX_TRIALS)
        self.people_spin.setValue(1)
        self.people_spin.valueChanged.connect(lambda _: self._check_new())
        form.addRow("Participants", self.people_spin)
        layout.addLayout(form)
        layout.addWidget(note("Pseudonyms are generated when the session is created. Height, the other "
                              "participant details and consent are entered per trial in step 2 (Trials), where "
                              "trials can also be added."))

        cams = QGroupBox("Cameras: one folder per camera")
        cl = QVBoxLayout(cams)
        cl.addWidget(note(s("sync_reminder"), "important"))
        cl.addWidget(para("Each folder holds the camera's <b>intrinsic</b> and <b>extrinsic</b> videos and one "
                            "video per trial. Missing calibration videos can be chosen from any folder later."))
        cl.addWidget(note(f"{s('intrinsic_content')}\n{s('extrinsic_content')}"))
        self.cam_table = QTableWidget(0, 3)
        self.cam_table.setHorizontalHeaderLabels(["Camera", "Folder", ""])
        self.cam_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.cam_table.verticalHeader().setVisible(False)
        cl.addWidget(self.cam_table)
        row = QHBoxLayout()
        add, remove = QPushButton("Add camera"), QPushButton("Remove last camera")
        add.clicked.connect(lambda: self._add_camera(""))
        remove.clicked.connect(self._remove_camera)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        cl.addLayout(row)
        layout.addWidget(cams)

        self.new_issues = IssueView()
        layout.addWidget(self.new_issues)
        self.create_button = QPushButton("Create session")
        self.create_button.clicked.connect(self._create)
        layout.addWidget(self.create_button)
        for _ in range(3):
            self._add_camera("")
        self._check_new()
        return w

    def _browse_into(self, edit: QLineEdit, title: str) -> None:
        folder = QFileDialog.getExistingDirectory(self, title, edit.text() or str(Path.home()))
        if folder:
            edit.setText(folder)
            self._check_new()

    def _add_camera(self, folder: str) -> None:
        r = self.cam_table.rowCount()
        self.cam_table.insertRow(r)
        self.cam_table.setItem(r, 0, readonly(f"cam{r + 1:02d}"))
        edit = QLineEdit(folder)
        edit.editingFinished.connect(self._check_new)
        self.cam_table.setCellWidget(r, 1, edit)
        browse = QPushButton("Browse ...")
        browse.clicked.connect(lambda _=False, row=r: self._browse_camera(row))
        self.cam_table.setCellWidget(r, 2, browse)
        fit_rows(self.cam_table)

    def _browse_camera(self, row: int) -> None:
        edit = self.cam_table.cellWidget(row, 1)
        folder = QFileDialog.getExistingDirectory(self, f"Folder of cam{row + 1:02d}", edit.text() or str(Path.home()))
        if folder:
            edit.setText(folder)
            self.fill_sibling_cameras(row, Path(folder))
            self._check_new()

    def fill_sibling_cameras(self, row: int, folder: Path) -> None:
        """A folder named like its camera (cam02 for cam02): fill the empty rows from cam01, cam03, ... next to it."""
        if camera_number(folder.name) != row + 1:
            return
        for n, path in sorted(sibling_camera_dirs(folder).items()):
            while self.cam_table.rowCount() < n:
                self._add_camera("")
            edit = self.cam_table.cellWidget(n - 1, 1)
            if not edit.text().strip():
                edit.setText(str(path))

    def _remove_camera(self) -> None:
        if self.cam_table.rowCount() > 2:
            self.cam_table.removeRow(self.cam_table.rowCount() - 1)
            fit_rows(self.cam_table)
            self._check_new()

    def spec(self) -> NewSession:
        dirs = [Path(self.cam_table.cellWidget(r, 1).text().strip()) for r in range(self.cam_table.rowCount())]
        return NewSession(out_root=Path(self.out_edit.text().strip() or "."), n_participants=self.people_spin.value(),
                          camera_dirs=dirs, study=self.study_edit.text(),
                          trials_per_participant=self.trials_spin.value())

    def _check_new(self) -> list[Issue]:
        spec = self.spec()
        issues = []
        if not self.out_edit.text().strip():
            issues.append(Issue("action", "output folder", "Choose where session folders are created.", ""))
        else:
            issues += [i for i in check_new_session(spec)]
        empty = [f"cam{i:02d}" for i, d in enumerate(spec.camera_dirs, 1) if str(d) in ("", ".")]
        issues = [i for i in issues if i.where not in empty] + [
            Issue("action", c, "Choose this camera's folder.", "") for c in empty]
        self.new_issues.show_issues(issues, "Ready to create the session.")
        self.create_button.setEnabled(not any(i.level in ("error", "action") for i in issues))
        return issues

    def _create(self) -> None:
        try:
            root, session = create_session(self.spec())
        except SetupError as exc:
            error_box(self, "Session not created", str(exc))
            return
        self.win.set_session(root, session)
        self.completeChanged.emit()

    # ---------- existing session ----------

    def _existing_view(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self.info = QLabel()
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        move_row = QHBoxLayout()
        self.move_button = QPushButton("Change output folder ...")
        self.move_button.setToolTip("Moves this session folder, with everything in it, into another output folder. "
                                    "Also in File > Move session to another output folder.")
        self.move_button.clicked.connect(lambda: self.win.move_session_dialog())
        move_row.addWidget(self.move_button)
        move_row.addStretch()
        layout.addLayout(move_row)
        layout.addWidget(note(s("sync_reminder"), "important"))
        layout.addWidget(note(s("cameras_stay_put")))
        layout.addWidget(para("Participants, their details and their consent are entered with the trials in step 2 "
                              "(Trials)."))
        layout.addStretch()
        return w

    def refresh(self) -> None:
        if self.session is None:
            self.modes.setCurrentIndex(0)
            self.out_edit.setText(self.out_edit.text() or last_output_root())
            self._check_new()
            return
        self.modes.setCurrentIndex(1)
        cams = "".join(f"<br>&nbsp;&nbsp;{c.name}: {c.source_dir}" for c in self.session.cameras)
        sample = " <b>(sample data)</b>" if self.session.calibration_source == "imported" else ""
        self.info.setText(f"<b>Session folder:</b> {self.root}{sample}<br><b>Cameras:</b>{cams}")

    def is_complete(self) -> bool:
        return self.session is not None
