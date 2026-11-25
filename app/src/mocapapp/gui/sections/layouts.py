"""Layouts section (PROJECT.md §16.2-16.3, R7/R8): where the cameras stood. Each layout has measured reference points
(typed in or copied from a preset) and one extrinsic video per camera; each camera's position is solved from the
points clicked in its video."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core import workflow
from mocapapp.core.calibration import extrinsics as ex
from mocapapp.core.calibration import service
from mocapapp.core.calibration.scene_points import MIN_POINTS, Scene, check_scene
from mocapapp.core.session import CameraLayout, Session
from mocapapp.core.texts import s
from mocapapp.gui.common import (
    Background,
    IssueView,
    NoWheelCombo,
    clear_table,
    confirm,
    error_box,
    fit_rows,
    for_each,
    note,
    open_path,
    readonly,
    scroll,
    selected_rows,
)
from mocapapp.gui.main_window import Section
from mocapapp.gui.sections.cameras import CAL_ERRORS, choose_video, table

ERRORS = (*CAL_ERRORS, ex.ExtrinsicsError)


class LayoutDialog(QDialog):
    """A new layout, or a layout's name, date and cameras. The cameras' order is their order in the trial folders
    (cam01, cam02, ...)."""

    def __init__(self, parent: QWidget | None, session: Session, lay: CameraLayout | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"Layout {lay.name}" if lay else "New layout")
        self.resize(560, 520)
        form = QFormLayout(self)
        self.name = QLineEdit(lay.name if lay else "")
        self.name.setPlaceholderText(f"e.g. Hall, October (empty: Layout {len(session.layouts) + 1})")
        form.addRow("Name", self.name)
        self.date = QLineEdit(lay.date if lay and lay.date_manual else "")
        auto = f" (now {lay.date})" if lay and lay.date and not lay.date_manual else ""
        self.date.setPlaceholderText(f"YYYY-MM-DD; empty: the day its extrinsic videos were recorded{auto}")
        form.addRow("Date", self.date)
        self.cams = QListWidget()
        chosen = lay.camera_ids() if lay else []
        for cid in chosen + [c.id for c in session.cameras if c.id not in chosen]:
            cam = session.camera(cid)
            item = QListWidgetItem(cam.id if cam.name == cam.id else f"{cam.name} ({cam.id})")
            item.setData(Qt.ItemDataRole.UserRole, cid)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if cid in chosen or lay is None else Qt.CheckState.Unchecked)
            self.cams.addItem(item)
        moves = QHBoxLayout()
        for text, step in (("Up", -1), ("Down", 1)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, d=step: self._move(d))
            moves.addWidget(b)
        moves.addStretch()
        box = QVBoxLayout()
        box.addWidget(self.cams)
        box.addLayout(moves)
        form.addRow("Cameras", box)
        form.addRow(note("Ticked cameras, in this order: the first is cam01 in the trial folders, and so on."))
        self.preset = NoWheelCombo()
        self.preset.addItem("(none: enter them afterwards)", "")
        for p in session.presets:
            self.preset.addItem(f"{p.name} ({p.id})", p.id)
        if lay is None:
            form.addRow("Reference points", self.preset)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _move(self, d: int) -> None:
        r = self.cams.currentRow()
        if r < 0 or not 0 <= r + d < self.cams.count():
            return
        item = self.cams.takeItem(r)
        self.cams.insertItem(r + d, item)
        self.cams.setCurrentRow(r + d)

    def cameras(self) -> list[str]:
        return [self.cams.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.cams.count())
                if self.cams.item(i).checkState() == Qt.CheckState.Checked]


class LayoutsSection(Section):
    key, title = "layouts", "Layouts"
    purpose = ("Where the cameras stood. A trial uses one layout: its reference points and a camera position per "
               "camera, clicked in that camera's extrinsic video.")

    def __init__(self, win):
        super().__init__(win)
        self.current = ""  # the layout shown
        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)  # Ctrl/Shift: several
        self.list.currentRowChanged.connect(self._picked)
        self.list.itemSelectionChanged.connect(self._enable)
        ll.addWidget(self.list, 1)
        self.buttons: dict[str, QPushButton] = {}
        for key, text, slot in (("new", "New layout ...", self.new), ("edit", "Edit ...", self.edit),
                                ("duplicate", "Duplicate", self.duplicate), ("delete", "Delete", self.delete),
                                ("import", "Import from another project ...", lambda: self.win.import_dialog())):
            b = QPushButton(text)
            b.clicked.connect(slot)
            self.buttons[key] = b
            ll.addWidget(b)
        presets = QGroupBox("Reference-point presets")
        pl = QVBoxLayout(presets)
        self.presets = QListWidget()
        self.presets.setMaximumHeight(110)
        self.presets.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        pl.addWidget(self.presets)
        drop = QPushButton("Delete preset")
        drop.setToolTip("Layouts made from it keep their own copy of the points.")
        drop.clicked.connect(self.delete_preset)
        pl.addWidget(drop)
        ll.addWidget(presets)
        split.addWidget(left)

        detail = QWidget()
        dl = QVBoxLayout(detail)
        self.imported_note = note("This project uses an imported calibration (sample data): its layout is not "
                                  "calibrated here.", "important")
        dl.addWidget(self.imported_note)
        self.header = QLabel()
        self.header.setWordWrap(True)
        dl.addWidget(self.header)
        points = QGroupBox("Reference points")
        ptl = QVBoxLayout(points)
        prow = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        prow.addWidget(self.summary, 1)
        self.edit_points = QPushButton("Edit reference points ...")
        self.edit_points.clicked.connect(self.points_dialog)
        self.use_preset = QPushButton("Use a preset ...")
        self.use_preset.clicked.connect(self.points_from_preset)
        self.save_preset = QPushButton("Save as preset ...")
        self.save_preset.clicked.connect(self.points_to_preset)
        for b in (self.edit_points, self.use_preset, self.save_preset):
            prow.addWidget(b)
        ptl.addLayout(prow)
        self.issues = IssueView(min_height=60)
        self.issues.setMaximumHeight(130)
        ptl.addWidget(self.issues)
        ptl.addWidget(note(f"At least {MIN_POINTS} measured points; each camera must see at least 6 of them."))
        dl.addWidget(points)
        cams = QGroupBox("Camera positions")
        cl = QVBoxLayout(cams)
        cl.addWidget(note(s("cameras_stay_put"), "important"))
        self.table = table(["Name in trials", "Camera", "Extrinsic video", "", "", "Position", "Error",
                            "Camera position (m)", "", "Notes"])
        cl.addWidget(self.table)
        self.state = QLabel()
        self.state.setWordWrap(True)
        cl.addWidget(self.state)
        dl.addWidget(cams)
        dl.addStretch()
        self.detail = detail
        split.addWidget(scroll(detail))
        split.setSizes([260, 900])
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(split)

    # ---------- view ----------

    def current_layout(self) -> CameraLayout | None:
        if self.session is None or not self.current:
            return None
        try:
            return self.session.layout(self.current)
        except KeyError:
            return None

    def refresh(self) -> None:
        if self.session is None:
            return
        ses = self.session
        ids = [x.id for x in ses.layouts]
        if self.current not in ids:
            self.current = ids[0] if ids else ""
        self.list.blockSignals(True)
        self.list.clear()
        for lay in ses.layouts:
            waiting = [v for v in service.layout_state(ses, self.root, lay.id).values() if v != "ready"]
            mark = "✓" if not waiting else f"{len(waiting)} to do"
            self.list.addItem(f"{lay.name}  ({lay.id}, {len(lay.cameras)} cameras)    {mark}")
        self.list.setCurrentRow(ids.index(self.current) if self.current else -1)
        for lid in self._keep:  # the layouts selected before (or just made)
            if lid in ids:
                self.list.item(ids.index(lid)).setSelected(True)
        self._keep = []
        self.list.blockSignals(False)
        self.presets.clear()
        for p in ses.presets:
            n = len((p.points or {}).get("points", []))
            item = QListWidgetItem(f"{p.name} ({p.id}, {n} points)")
            item.setData(Qt.ItemDataRole.UserRole, p.id)
            self.presets.addItem(item)
        self._show()

    def _picked(self, row: int) -> None:
        if self.session is not None and 0 <= row < len(self.session.layouts):
            self.current = self.session.layouts[row].id
            self._show()

    _keep: list[str] = []

    def selected(self) -> list[str]:
        """The selected layouts (the one shown when none is)."""
        ids = [x.id for x in self.session.layouts] if self.session else []
        chosen = [ids[r] for r in selected_rows(self.list) if r < len(ids)]
        return chosen or ([self.current] if self.current in ids else [])

    def _enable(self) -> None:
        if self.session is None:
            return
        n = len(self.selected())
        self.buttons["edit"].setEnabled(self.current_layout() is not None)
        for key in ("duplicate", "delete"):
            self.buttons[key].setEnabled(n > 0)
            self.buttons[key].setText(("Duplicate" if key == "duplicate" else "Delete") + (f" ({n})" if n > 1 else ""))

    def _scene(self) -> Scene | None:
        lay = self.current_layout()
        return Scene.model_validate(lay.points) if lay is not None and lay.points else None

    def _show(self) -> None:
        ses, lay = self.session, self.current_layout()
        imported = ses.calibration_source == "imported"
        self.imported_note.setVisible(imported)
        self._enable()
        self.buttons["new"].setEnabled(not imported and bool(ses.cameras))
        self.buttons["new"].setToolTip("" if ses.cameras else "Add the cameras first (Cameras section).")
        self.detail.setEnabled(lay is not None)
        for b in (self.edit_points, self.use_preset):
            b.setEnabled(lay is not None and not imported)
        self.use_preset.setEnabled(self.use_preset.isEnabled() and bool(ses.presets))
        clear_table(self.table)
        if lay is None:
            self.header.setText("No layout yet: make one with <b>New layout</b>, or import one." if not ses.layouts
                                else "")
            self.summary.setText("")
            self.issues.setHtml("")
            self.state.setText("")
            self.save_preset.setEnabled(False)
            return
        trials = [t.label for t in ses.trials_with_layout(lay.id)]
        date = f"{lay.date or 'not known yet'}" + (" (set by hand)" if lay.date_manual else "")
        source = f" Imported from {html.escape(lay.imported_from)}." if lay.imported_from else ""
        self.header.setText(f"<b>{html.escape(lay.name)}</b> ({lay.id}). Date: {date}. Used by "
                            f"{len(trials)} trial(s).{source}")
        scene = self._scene()
        self.save_preset.setEnabled(scene is not None)
        if scene is None:
            self.summary.setText("No reference points yet: enter them, or use a preset.")
            self.issues.setHtml("")
        else:
            preset = f" Copied from preset {lay.preset}." if lay.preset else ""
            self.summary.setText(f"<b>{len(scene.points)} points.</b> Origin: {html.escape(scene.origin_text or '?')}"
                                 f". +X toward: {html.escape(scene.x_axis_text or '?')}. Z is up.{preset}")
            self.issues.show_issues(check_scene(scene), "The reference points look fine.")
        for lc in lay.cameras:
            r = self.table.rowCount()
            self.table.insertRow(r)
            res = service.position_result(self.root, lay.id, lc.camera)
            video = Path(lc.extrinsic.file).name if lc.extrinsic else "-"
            self.table.setItem(r, 0, readonly(lay.slot(lc.camera)))
            self.table.setItem(r, 1, readonly(service.camera_title(ses, lc.camera)))
            item = readonly(video)
            if lc.extrinsic:
                item.setToolTip(lc.extrinsic.file)
            self.table.setItem(r, 2, item)
            choose = QPushButton("Choose video ...")
            choose.setEnabled(not imported)
            choose.clicked.connect(lambda _=False, c=lc.camera: self.choose(c))
            click = QPushButton("Click points ...")
            click.setEnabled(not imported and scene is not None and lc.extrinsic is not None)
            click.setToolTip("" if scene is not None else "Enter the reference points first.")
            click.clicked.connect(lambda _=False, c=lc.camera: self.click(c))
            review = QPushButton("Review image")
            path = service.review_image(self.root, lay.id, lc.camera)
            review.setEnabled(res is not None and path.is_file())
            review.clicked.connect(lambda _=False, p=path: open_path(p))
            for c, w in ((3, choose), (4, click), (8, review)):
                self.table.setCellWidget(r, c, w)
            if res is None:
                lens = service.lens_result(self.root, lc.camera)
                self.table.setItem(r, 5, readonly("not done yet" if lens else "lens not calibrated yet (Cameras)"))
                for c in (6, 7, 9):
                    self.table.setItem(r, c, readonly(""))
                continue
            word = {"ok": "PASS", "warn": "CHECK (usable)",
                    "fail": "FAIL" + (" (accepted)" if res.overridden else " (blocks the run)")}[res.status]
            self.table.setItem(r, 5, readonly(word))
            self.table.setItem(r, 6, readonly(f"{res.rms_cm:.2f} cm ({res.rms_px:.1f} px), {res.n_points} points"))
            self.table.setItem(r, 7, readonly(", ".join(f"{v:.2f}" for v in res.camera_position)))
            self.table.setItem(r, 9, readonly("\n".join(res.warnings)))
        self.table.resizeRowsToContents()
        fit_rows(self.table)
        waiting = {k: v for k, v in service.layout_state(ses, self.root, lay.id).items() if v != "ready"}
        self.state.setText("<b>Ready:</b> every camera of this layout is calibrated." if not waiting else
                           "<b>Still to do:</b> " + html.escape("; ".join(f"{k}: {v}" for k, v in waiting.items())))

    def _changed(self, keep: list[str] | None = None) -> None:
        self._keep = list(keep or [])
        self.refresh()
        self.completeChanged.emit()
        self.win.changed()

    # ---------- layouts ----------

    def new(self) -> None:
        dlg = LayoutDialog(self, self.session)
        if not dlg.exec():
            return
        try:
            lay = workflow.add_layout(self.session, self.root, dlg.name.text(), dlg.cameras(),
                                      dlg.preset.currentData() or "", dlg.date.text().strip() or None)
        except ERRORS as exc:
            error_box(self, "Layout not made", str(exc))
            return
        self.current = lay.id
        self._changed()

    def edit(self) -> None:
        lay = self.current_layout()
        if lay is None:
            return
        dlg = LayoutDialog(self, self.session, lay)
        if not dlg.exec():
            return
        date = dlg.date.text().strip()
        try:
            workflow.edit_layout(self.session, self.root, lay.id, dlg.name.text(),
                                 date if (date or lay.date_manual) else None, dlg.cameras())
        except ERRORS as exc:
            error_box(self, "Layout not saved", str(exc))
        self._changed()

    def duplicate(self) -> None:
        """Each selected layout: same cameras and points, without extrinsic videos (the cameras were set up again)."""
        copies = for_each(self, "Not duplicated", self.selected(),
                          lambda lid: workflow.duplicate_layout(self.session, self.root, lid), ERRORS)
        if copies:
            self.current = copies[-1].id
        self._changed([c.id for c in copies])

    def delete(self) -> None:
        ids = self.selected()
        if not ids:
            return
        names = ", ".join(html.escape(self.session.layout(lid).name) for lid in ids)
        what = "the layout" if len(ids) == 1 else f"{len(ids)} layouts"
        if not confirm(self, f"Delete {what}?", f"Delete {names}, their camera positions and their copies of the "
                       "extrinsic videos? The videos in their own folders are not touched.", "Delete"):
            return
        for_each(self, "Not deleted", ids, lambda lid: workflow.delete_layout(self.session, self.root, lid), ERRORS)
        self._changed()

    def delete_preset(self) -> None:
        ids = [item.data(Qt.ItemDataRole.UserRole) for item in self.presets.selectedItems()]
        if not ids:
            return
        for_each(self, "Not deleted", ids, lambda pid: workflow.delete_preset(self.session, self.root, pid), ERRORS)
        self._changed()

    # ---------- reference points ----------

    def _save_points(self, scene: Scene, preset: str = "") -> None:
        try:
            notes = workflow.set_layout_points(self.session, self.root, self.current, scene, preset)
        except ERRORS as exc:
            error_box(self, "Reference points not saved", str(exc))
            notes = []
        if notes:
            QMessageBox.information(self, "Camera positions", "The reference points changed.\n\n" + "\n".join(notes))
        self._changed()

    def _new_preset(self, name: str, scene: Scene) -> None:
        preset = service.save_preset(self.session, name, scene)
        self.win.save()
        self.win.statusBar().showMessage(f"Preset {preset.name} ({preset.id}) saved.", 6000)

    def points_dialog(self) -> None:
        from mocapapp.gui.scene_dialog import SceneDialog

        lay = self.current_layout()
        if lay is None:
            return
        clicked = any(service.position_result(self.root, lay.id, c) for c in lay.camera_ids())
        dlg = SceneDialog(self, self._scene(), has_extrinsics=clicked, presets=self.session.presets,
                          on_save_preset=self._new_preset, title=f"Reference points - {lay.name}")
        if dlg.exec():
            self._save_points(dlg.scene())
        else:
            self._changed()  # a preset may have been saved

    def points_from_preset(self) -> None:
        names = {f"{p.name} ({p.id})": p for p in self.session.presets}
        if not names:
            return
        name, ok = QInputDialog.getItem(self, "Use a preset", "Copy the reference points of:", list(names), 0, False)
        if ok:
            preset = names[name]
            self._save_points(Scene.model_validate(preset.points), preset.id)

    def points_to_preset(self) -> None:
        scene = self._scene()
        if scene is None:
            return
        name, ok = QInputDialog.getText(self, "Save as preset", "Name of this point set (e.g. Lab room A):")
        if ok and name.strip():
            self._new_preset(name.strip(), scene)
            self._changed()

    # ---------- camera positions ----------

    def choose(self, camera: str) -> None:
        lay = self.current_layout()
        cam = self.session.camera(camera)
        start = lay.entry(camera).extrinsic.file if lay.entry(camera).extrinsic else cam.source_dir
        path = choose_video(self, f"Extrinsic video of {service.camera_title(self.session, camera)} in {lay.name}",
                            start)
        if path is None:
            return
        try:
            workflow.set_extrinsic_video(self.session, self.root, lay.id, camera, path)
        except ERRORS as exc:
            error_box(self, "Video not chosen", str(exc))
        self._changed()

    def click(self, camera: str) -> Background | None:
        """Copy the layout's extrinsic videos into the project (in the background), then click the points."""
        lay = self.current_layout()
        if lay is None:
            return None
        if service.lens_result(self.root, camera) is None:
            error_box(self, "Not ready", f"{service.camera_title(self.session, camera)}: calibrate its lens first "
                      "(Cameras section).")
            return None
        self.win.set_busy(True)
        self.state.setText("Copying the extrinsic videos into the project ...")

        def done(_n):
            self.win.set_busy(False)
            self.open_click(lay.id, camera)

        def failed(msg, details):
            self.win.set_busy(False)
            error_box(self, "Extrinsic video not ready", msg, details)
            self._changed()

        self.job = Background(self, workflow.prepare_calibration, self.session, self.root, None, lay.id,
                              on_done=done, on_error=failed, on_progress=self.state.setText, progress=True)
        return self.job

    def open_click(self, layout: str, camera: str) -> None:
        from mocapapp.gui.extrinsic_dialog import ExtrinsicDialog

        try:
            scene = service.layout_scene(self.session, layout)
            video = service.layout_video(self.root, layout, camera)
        except ERRORS as exc:
            error_box(self, "Not ready", str(exc))
            self._changed()
            return
        intr = service.lens_result(self.root, camera)
        previous = service.position_result(self.root, layout, camera)
        dlg = ExtrinsicDialog(self, service.camera_title(self.session, camera), video, scene, intr, previous)
        if dlg.exec():
            try:
                workflow.place_camera(self.session, self.root, layout, camera, dlg.clicks(), dlg.frame_index,
                                      dlg.override)
            except ERRORS as exc:
                error_box(self, "Camera position not saved", str(exc))
        self._changed()
