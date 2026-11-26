"""Results section (PROJECT.md §16.3): the trials on the left with their traffic light; tabs Quality (metrics,
warnings, 3D preview, the quality report), 3D and Data (the viewer), Export (choices, export, deletion of raw footage
and its notices). It can be opened during a run and shows trials as they finish (§6.7, FR-4.8)."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core import outputs
from mocapapp.core.export import export, kinds
from mocapapp.core.qa import LIGHT_COLOR, LIGHT_TEXT, trial_qa, write_report
from mocapapp.core.session import Layout
from mocapapp.gui.common import Background, error_box, open_path, scroll
from mocapapp.gui.main_window import Section
from mocapapp.gui.sections.viewer import Viewer

GROUPS = [("Settings", ["config"]), ("3D markers (.trc)", ["trc.raw", "trc.filt", "trc.filt_lstm"]),
          ("3D markers (.c3d)", ["c3d.raw", "c3d.filt", "c3d.filt_lstm"]),
          ("Kinematics", ["osim", "mot", "bvh"])]
LABELS = {"config": "Config.toml", "trc.raw": "raw", "trc.filt": "smoothed", "trc.filt_lstm": "smoothed + LSTM",
          "c3d.raw": "raw", "c3d.filt": "smoothed", "c3d.filt_lstm": "smoothed + LSTM",
          "osim": "scaled model (.osim)", "mot": "joint angles (.mot)", "bvh": "animation (.bvh)"}
QUALITY, VIEW, DATA, EXPORT = range(4)
IMPORT_TIP = ("Show Pose2Sim results made elsewhere (a trial folder, or a Pose2Sim project with several) in 3D and "
              "Data, next to this project's trials. Only a link is kept: nothing is copied, and the folder is never "
              "changed.")


def metric_value(m) -> str:
    return "n/a" if m.value is None else f"{m.value:.1%}" if m.unit == "share" else f"{m.value} {m.unit}"


class ResultsSection(Section):
    key, title = "results", "Results"
    purpose = ("Each trial's quality, its 3D skeleton and data, and the export. Trials appear here as they finish, "
               "also during a run.")

    def __init__(self, win):
        super().__init__(win)
        self.results = []  # TrialQA of every trial, in trial order
        self.viewer = Viewer(win)
        self.viewer.setParent(self)
        self.viewer.setVisible(False)

        self.list = QListWidget()
        self.list.setMaximumWidth(260)
        self.list.currentRowChanged.connect(lambda _: self._picked())
        self.empty = QLabel()
        self.empty.setWordWrap(True)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("<b>Trials</b>"))
        ll.addWidget(self.list, 1)
        ll.addWidget(self.empty)
        self.import_button = QPushButton("Import Pose2Sim data ...")
        self.import_button.setToolTip(IMPORT_TIP)
        self.import_button.clicked.connect(self.import_pose2sim)
        self.unlink_button = QPushButton("Remove from the list")
        self.unlink_button.setToolTip("Forgets the chosen imported data. Its folder and files are not touched.")
        self.unlink_button.clicked.connect(self.unlink)
        ll.addWidget(self.import_button)
        ll.addWidget(self.unlink_button)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._quality_tab(), "Quality")
        self.tabs.addTab(self.viewer.view_tab, "3D")
        self.tabs.addTab(self.viewer.data_tab, "Data")
        self.tabs.addTab(scroll(self._export_tab()), "Export")
        self.tabs.currentChanged.connect(self._tab_changed)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split)

    # ---------- tabs ----------

    def _quality_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self.qa = QTextBrowser()
        self.qa.setOpenLinks(False)
        self.qa.anchorClicked.connect(lambda url: open_path(url.toLocalFile()))
        layout.addWidget(self.qa, 1)
        row = QHBoxLayout()
        self.report_button = QPushButton("Open the quality report (all trials)")
        self.report_button.clicked.connect(lambda: open_path(Layout(self.root).qa / "qa_report.html"))
        row.addWidget(self.report_button)
        row.addStretch()
        layout.addLayout(row)
        return w

    def _export_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        box = QGroupBox("Export")
        el = QVBoxLayout(box)
        self.source = QLabel()
        self.source.setWordWrap(True)
        el.addWidget(self.source)
        grid = QGridLayout()
        self.boxes: dict[str, QCheckBox] = {}
        for r, (title, keys) in enumerate(GROUPS):
            grid.addWidget(QLabel(f"<b>{title}</b>"), r, 0)
            for c, key in enumerate(keys, 1):
                self.boxes[key] = QCheckBox(LABELS[key])
                grid.addWidget(self.boxes[key], r, c)
        el.addLayout(grid)
        buttons = QHBoxLayout()
        self.export_button = QPushButton("Export")
        self.export_button.setToolTip("Every finished trial, to the export folder of the project.")
        self.export_button.clicked.connect(self.do_export)
        self.open_button = QPushButton("Open the export folder")
        self.open_button.clicked.connect(lambda: open_path(Layout(self.root).export))
        buttons.addWidget(self.export_button)
        buttons.addWidget(self.open_button)
        buttons.addStretch()
        el.addLayout(buttons)
        self.export_state = QLabel()
        self.export_state.setWordWrap(True)
        el.addWidget(self.export_state)
        layout.addWidget(box)

        data_box = QGroupBox("Raw footage and participant data")
        dl = QVBoxLayout(data_box)
        self.deletion = QLabel()
        self.deletion.setWordWrap(True)
        dl.addWidget(self.deletion)
        drow = QHBoxLayout()
        self.delete_now = QPushButton("Delete raw footage now ...")
        self.delete_now.setToolTip("For the trials whose footage is to be deleted (consent, or Session settings) "
                                   "once their results are complete. Shows the exact files first.")
        self.delete_now.clicked.connect(self.delete_marked)
        self.data_button = QPushButton("Participant data and deletion ...")
        self.data_button.clicked.connect(self.win.show_participant_data)
        drow.addWidget(self.delete_now)
        drow.addWidget(self.data_button)
        drow.addStretch()
        dl.addLayout(drow)
        layout.addWidget(data_box)
        layout.addStretch()
        return w

    def _tab_changed(self, index: int) -> None:
        if index != VIEW:
            self.viewer.stop()

    def hideEvent(self, event) -> None:  # noqa: N802
        try:
            self.viewer.stop()
        except RuntimeError:  # the window is being taken apart as the app closes: nothing plays any more
            pass
        super().hideEvent(event)

    # ---------- state ----------

    def refresh(self) -> None:
        if self.session is None:
            self.viewer.stop()
            return
        current = self.selected()
        try:
            if self.win.busy:  # during a run: read only (the run writes the report itself at its end)
                self.results = [trial_qa(self.session, self.root, t.label) for t in self.session.active_trials]
            else:
                _path, self.results = write_report(self.session, self.root)
        except (OSError, ValueError) as exc:
            self.qa.setHtml(f"<p>The quality report could not be made: {html.escape(str(exc))}</p>")
            self.results = []
        self.list.blockSignals(True)
        self.list.clear()
        for r in self.results:
            item = QListWidgetItem(f"● Trial {r.label} ({r.code}): {LIGHT_TEXT[r.light]}"
                                   + ("" if r.status == "done" else f" - {r.status.replace('_', ' ')}"))
            item.setForeground(QBrush(QColor(LIGHT_COLOR[r.light])))
            item.setData(Qt.ItemDataRole.UserRole, r.label)
            self.list.addItem(item)
        for link in self.session.linked:
            there = outputs.has_results(Path(link.path))
            item = QListWidgetItem(f"◆ Imported: {link.name}" + ("" if there else " - folder not found"))
            item.setForeground(QBrush(QColor("#555" if there else LIGHT_COLOR["red"])))
            item.setToolTip(link.path)
            item.setData(Qt.ItemDataRole.UserRole, link.id)
            self.list.addItem(item)
        labels = [r.label for r in self.results] + [x.id for x in self.session.linked]
        done = [r.label for r in self.results if r.status == "done"]
        pick = current if current in labels else (done[0] if done else (labels[0] if labels else None))
        self.list.setCurrentRow(labels.index(pick) if pick else -1)
        self.list.blockSignals(False)
        self.empty.setText("" if done else "No results yet: run trials in Session. They appear here as they finish.")
        self.viewer.refresh()
        self._picked()
        self._export_settings()
        self._show_deletion_state()

    def selected(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _picked(self) -> None:
        label = self.selected()
        r = next((x for x in self.results if x.label == label), None)
        self.viewer.show_trial(label)
        self._link_buttons()
        link = outputs.find_link(self.session, label) if label and label.startswith("i") else None
        if link is not None:
            self.qa.setHtml(self._link_html(link))
            return
        if r is None:
            self.qa.setHtml("<p>No trials yet.</p>")
            return
        color, word = LIGHT_COLOR[r.light], LIGHT_TEXT[r.light]
        parts = [f"<h3>Trial {html.escape(r.label)} ({html.escape(r.code)}): "
                 f"<span style='color:{color}'>{word}</span></h3>"]
        if r.status != "done":
            parts.append(f"<p>Status: {html.escape(r.status.replace('_', ' '))}. The checks below are filled in as "
                         "the trial's stages finish.</p>")
        if r.metrics:
            parts.append("<table cellspacing=4>")
            for m in r.metrics:
                parts.append(f"<tr><td>{html.escape(m.name)}</td><td>{html.escape(metric_value(m))}</td>"
                             f"<td style='color:{LIGHT_COLOR[m.light]}'><b>{LIGHT_TEXT[m.light]}</b></td>"
                             f"<td>{html.escape(m.note)}</td></tr>")
            parts.append("</table>")
        if r.warnings:
            parts.append("<ul>" + "".join(f"<li>{html.escape(w)}</li>" for w in r.warnings) + "</ul>")
        if r.preview:
            parts.append(f"<p><a href='{Path(r.preview).as_uri()}'>Open the 3D preview of trial "
                         f"{html.escape(r.label)}</a> - watch for limbs that jump or swap sides.</p>")
        self.qa.setHtml("".join(parts))

    def _link_html(self, link) -> str:
        folder = Path(link.path)
        parts = [f"<h3>Imported: {html.escape(link.name)}</h3>",
                 f"<p>Pose2Sim results from <a href='{folder.as_uri()}'>{html.escape(str(folder))}</a>, made outside "
                 "this project. They are read where they are: nothing is copied, and the app never changes or "
                 "deletes them. There are no quality checks or export for them; see them in 3D and Data.</p>"]
        if not outputs.has_results(folder):
            parts.append("<p><b>The folder is no longer there</b> (or has no results any more). Move it back, or "
                         "use <i>Remove from the list</i>.</p>")
            return "".join(parts)
        files = [f"3D markers, {label}: {html.escape(p.name)}" for label, p in outputs.marker_files(folder).items()]
        mot = outputs.angle_file(folder)
        files.append(f"joint angles: {html.escape(mot.name)}" if mot else "no joint angles (no kinematics/*.mot)")
        return "".join(parts) + "<ul>" + "".join(f"<li>{f}</li>" for f in files) + "</ul>"

    def _link_buttons(self) -> None:
        label = self.selected() or ""
        busy = self.win.busy  # a run writes the project file: links wait
        self.import_button.setEnabled(not busy)
        self.unlink_button.setEnabled(not busy and label.startswith("i"))
        self.import_button.setToolTip("Waits for the app to finish what it is doing." if busy else IMPORT_TIP)

    def import_pose2sim(self) -> None:
        if self.win.busy:
            return
        folder = QFileDialog.getExistingDirectory(self, "Import Pose2Sim data: a trial folder or a Pose2Sim "
                                                  "project", str(Path(self.root).parent))
        if not folder:
            return
        self.link_folder(Path(folder))

    def link_folder(self, folder: Path) -> list:
        try:
            new = outputs.link_results(self.session, self.root, folder)
        except outputs.OutputError as exc:
            error_box(self, "Nothing imported", str(exc))
            return []
        if not new:
            self.win.statusBar().showMessage("These Pose2Sim results are in the list already.", 6000)
            return []
        self.win.save()
        self.refresh()
        self.list.setCurrentRow(len(self.results) + self.session.linked.index(new[0]))
        self.win.statusBar().showMessage(f"Imported: {', '.join(x.name for x in new)}", 6000)
        return new

    def unlink(self) -> None:
        label = self.selected() or ""
        if self.win.busy or not label.startswith("i"):
            return
        outputs.unlink_results(self.session, label)
        self.win.save()
        self.refresh()

    def _export_settings(self) -> None:
        st = self.session.settings
        for k in kinds(st.filter.type):
            self.boxes[k.key].setChecked(k.default if k.key != "bvh" else self.boxes["bvh"].isChecked())
            self.boxes[k.key].setToolTip(k.describe)
        self.source.setText("Joint angles, the scaled model and the BVH animation are computed from "
                            f"<b>{'LSTM-augmented markers' if st.use_augmentation else 'filtered keypoints'}</b>. "
                            "File names start with the participant's pseudonym.")
        busy = self.win.busy
        self.export_button.setEnabled(not busy and any(r.status == "done" for r in self.results))
        self.export_button.setToolTip("Waits for the run to finish." if busy else
                                      "Every finished trial, to the export folder of the project.")
        self.data_button.setEnabled(not busy)
        self._link_buttons()

    def _due(self) -> list:
        """Trials whose footage is to be deleted (consent or Session settings) and is still there."""
        return [t for t in self.session.active_trials if t.delete_raw and t.raw_present]

    def _show_deletion_state(self) -> None:
        from mocapapp.core.deletion import export_needed, results_complete

        need_export = export_needed(self.session)
        lines = []
        for t in self.session.trials:
            if t.withdrawn_at:
                state = f"all data deleted on {t.withdrawn_at[:10]} (withdrawn)"
            elif t.raw_deleted_at:
                state = f"deleted on {t.raw_deleted_at[:10]}"
            elif t.delete_raw and results_complete(self.session, self.root, t.label, need_export):  # problems
                when = "after export" if need_export else "once the trial is processed"
                state = f"<b>still stored</b>, to be deleted {when}"
            elif t.delete_raw:
                state = "<b>still stored</b>, to be deleted now (<i>Delete raw footage now ...</i>)"
            else:
                state = "kept with consent until the study is finished - delete it then"
            lines.append(f"Trial {html.escape(t.label)} ({html.escape(t.code)}): {state}")
        self.deletion.setText("<br>".join(lines) or "No trials yet.")
        self.delete_now.setEnabled(bool(self._due()) and not self.win.busy)

    # ---------- actions ----------

    def delete_marked(self) -> None:
        """§8.4 safeguard 1: the deletion runs only once results are complete (and exported, when needed)."""
        from mocapapp.gui.privacy_dialog import DeleteDialog

        for t in self._due():
            DeleteDialog(self, self.session, self.root, "raw", t.label, after_run=True).exec()
        self.win.set_session(self.root, self.session)

    def do_export(self) -> None:
        selection = [k for k, b in self.boxes.items() if b.isChecked()]
        if not selection:
            error_box(self, "Nothing selected", "Tick at least one item to export.")
            return
        qa = {r.label: r.light for r in self.results}
        self.win.set_busy(True)
        self._export_settings()
        self.delete_now.setEnabled(False)
        self.export_state.setText("Exporting ...")

        def finish() -> None:
            self.win.set_busy(False)
            self._export_settings()
            self._show_deletion_state()

        def done(result):
            out, warnings = result
            finish()
            self.completeChanged.emit()
            self.export_state.setText(f"Exported to {out}" + ("<br>" + "<br>".join(html.escape(w) for w in warnings)
                                                                if warnings else ""))

        def failed(msg, details):
            finish()
            self.export_state.setText("")
            error_box(self, "Export failed", msg, details)

        Background(self, export, self.session, self.root, selection, None, qa, on_done=done, on_error=failed)
