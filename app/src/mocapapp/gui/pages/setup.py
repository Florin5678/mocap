"""Step 1: session setup (§6.2) - folders, cameras, participant table; sync reminder always visible (D2)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
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
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core.session import parse_height
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
from mocapapp.gui.common import IssueView, clear_table, error_box, fit_rows, last_output_root, note, para, readonly
from mocapapp.gui.main_window import Page


def _scroll(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    return area


def height_text(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}"


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
        self.trials_spin.valueChanged.connect(self._resize_participants)
        form.addRow("Number of trials", self.trials_spin)
        layout.addLayout(form)

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

        people = QGroupBox("Participants: one row per trial (each trial is a different participant)")
        pl = QVBoxLayout(people)
        pl.addWidget(para("Height in metres (cm is converted). Leave blank to estimate it - not recommended. "
                            "Codes are generated when the session is created."))
        self.height_table = QTableWidget(0, 2)
        self.height_table.setHorizontalHeaderLabels(["Height", "Used as"])
        self.height_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.height_table.itemChanged.connect(self._height_edited)
        pl.addWidget(self.height_table)
        layout.addWidget(people)

        self.new_issues = IssueView()
        layout.addWidget(self.new_issues)
        self.create_button = QPushButton("Create session")
        self.create_button.clicked.connect(self._create)
        layout.addWidget(self.create_button)
        for _ in range(3):
            self._add_camera("")
        self._resize_participants(1)
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

    def _resize_participants(self, n: int) -> None:
        self.height_table.blockSignals(True)
        self.height_table.setRowCount(n)
        self.height_table.setVerticalHeaderLabels([f"Trial {i + 1}" for i in range(n)])
        for r in range(n):
            if self.height_table.item(r, 0) is None:
                self.height_table.setItem(r, 0, QTableWidgetItem(""))
                self.height_table.setItem(r, 1, readonly("estimated (not recommended)"))
        self.height_table.blockSignals(False)
        fit_rows(self.height_table)
        self._check_new()

    def _height_edited(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        try:
            h = parse_height(item.text())
            text = "estimated (not recommended)" if h is None else f"{h:.2f} m"
        except ValueError:
            text = "invalid: 1.00-2.30 m"
        self.height_table.blockSignals(True)
        self.height_table.item(item.row(), 1).setText(text)
        self.height_table.blockSignals(False)
        self._check_new()

    def spec(self) -> NewSession:
        dirs = [Path(self.cam_table.cellWidget(r, 1).text().strip()) for r in range(self.cam_table.rowCount())]
        heights = [self.height_table.item(r, 0).text().strip() for r in range(self.height_table.rowCount())]
        return NewSession(out_root=Path(self.out_edit.text().strip() or "."), n_trials=self.trials_spin.value(),
                          camera_dirs=dirs, study=self.study_edit.text(), heights=heights)

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
        layout.addWidget(note(s("sync_reminder"), "important"))
        layout.addWidget(note(s("cameras_stay_put")))
        box = QGroupBox("Participants (one per trial)")
        bl = QVBoxLayout(box)
        bl.addWidget(para("Tick <i>Signed consent received</i> once you hold the signed form for that code. "
                            "<i>Delete raw footage</i> follows the storage consent by default; deletion is done after "
                            "a successful run and export, and is always possible later."))
        self.people = QTableWidget(0, 6)
        self.people.setHorizontalHeaderLabels(["Code", "Height (m)", "Signed consent received",
                                               "Consented to storing raw footage", "Delete raw footage",
                                               "Consent form"])
        self.people.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.people.horizontalHeader().setStretchLastSection(True)
        bl.addWidget(self.people)
        data_row = QHBoxLayout()
        self.data_button = QPushButton("Participant data and deletion ...")
        self.data_button.setToolTip("Delete raw footage or all data of a participant, at any time.")
        self.data_button.clicked.connect(self.win.show_participant_data)
        data_row.addWidget(self.data_button)
        data_row.addStretch()
        bl.addLayout(data_row)
        layout.addWidget(box)
        self.existing_issues = IssueView()
        self.existing_issues.setMaximumHeight(160)
        layout.addWidget(self.existing_issues)
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
        clear_table(self.people)
        self.people.setRowCount(len(self.session.trials))
        self.people.setVerticalHeaderLabels([f"Trial {t.label}" for t in self.session.trials])
        for r, t in enumerate(self.session.trials):
            self.people.setItem(r, 0, readonly(t.code))
            if not t.raw_present:  # nothing left to set up for this participant
                gone = (f"all data deleted {t.withdrawn_at[:10]}" if t.withdrawn_at
                        else f"raw footage deleted {t.raw_deleted_at[:10]}")
                self.people.setItem(r, 1, readonly(gone))
                continue
            h = QLineEdit(height_text(t.height_m))
            h.setPlaceholderText("estimate")
            h.editingFinished.connect(lambda t=t, h=h: self._set_height(t, h))
            self.people.setCellWidget(r, 1, h)
            attest = QCheckBox("received")
            attest.setChecked(t.consent.attested)
            attest.toggled.connect(lambda on, t=t: self._attest(t, on))
            storage = QCheckBox("yes")
            storage.setChecked(t.consent.raw_footage_storage)
            delete = QCheckBox("delete")
            delete.setChecked(t.delete_raw)
            storage.toggled.connect(lambda on, t=t, d=delete: self._storage(t, on, d))
            delete.toggled.connect(lambda on, t=t: self._delete(t, on))
            for col, cb in ((2, attest), (3, storage), (4, delete)):
                self.people.setCellWidget(r, col, cb)
            form = QPushButton("Generate ...")
            form.clicked.connect(lambda _=False, c=t.code: self.win.save_consent_pdf(c))
            self.people.setCellWidget(r, 5, form)
        fit_rows(self.people)
        self._check_existing()

    def _set_height(self, trial, edit: QLineEdit) -> None:
        try:
            trial.height_m = parse_height(edit.text())
        except ValueError as exc:
            error_box(self, "Height not changed", str(exc))
        edit.setText(height_text(trial.height_m))
        self.win.save()
        self._check_existing()

    def _attest(self, trial, on: bool) -> None:
        trial.consent.attested = on
        trial.consent.attested_at = datetime.now().isoformat(timespec="seconds") if on else None
        self.win.save()
        self._check_existing()

    def _storage(self, trial, on: bool, delete_box: QCheckBox) -> None:
        trial.consent.raw_footage_storage = on
        delete_box.setChecked(not on)  # default follows consent (§6.2); still changeable
        self.win.save()
        self._check_existing()

    def _delete(self, trial, on: bool) -> None:
        trial.delete_raw = on
        self.win.save()
        self._check_existing()

    def _check_existing(self) -> None:
        issues = []
        for t in self.session.trials:
            if not t.raw_present:
                continue
            where = f"trial {t.label} ({t.code})"
            if not t.consent.attested:
                issues.append(Issue("action", where, "Signed consent not yet confirmed (needed before Run).", ""))
            if not t.consent.raw_footage_storage and not t.delete_raw:
                issues.append(Issue("warning", where, "No consent to store raw footage, but it will not be deleted: "
                                    "the consent form promises deletion.", "Tick 'Delete raw footage'."))
            if t.height_m is None:
                issues.append(Issue("warning", where, "No height: it will be estimated (not recommended).", ""))
        self.existing_issues.show_issues(issues, "All participants are complete.")

    def is_complete(self) -> bool:
        return self.session is not None
