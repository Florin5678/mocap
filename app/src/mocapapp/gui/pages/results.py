"""Step 7: completion - QA traffic lights, 3D preview, export (§6.7, FR-4.8)."""

from __future__ import annotations

import html

from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core.export import export, kinds
from mocapapp.core.qa import LIGHT_COLOR, LIGHT_TEXT, write_report
from mocapapp.core.session import Layout
from mocapapp.gui.common import Background, error_box, open_path
from mocapapp.gui.main_window import Page

GROUPS = [("Settings", ["config"]), ("3D markers (.trc)", ["trc.raw", "trc.filt", "trc.filt_lstm"]),
          ("3D markers (.c3d)", ["c3d.raw", "c3d.filt", "c3d.filt_lstm"]),
          ("Kinematics", ["osim", "mot", "bvh"])]
LABELS = {"config": "Config.toml", "trc.raw": "raw", "trc.filt": "smoothed", "trc.filt_lstm": "smoothed + LSTM",
          "c3d.raw": "raw", "c3d.filt": "smoothed", "c3d.filt_lstm": "smoothed + LSTM",
          "osim": "scaled model (.osim)", "mot": "joint angles (.mot)", "bvh": "animation (.bvh)"}


class ResultsPage(Page):
    key, title = "results", "Results"

    def __init__(self, win):
        super().__init__(win)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        self.qa = QTextBrowser()
        self.qa.setOpenLinks(False)
        self.qa.anchorClicked.connect(lambda url: open_path(url.toLocalFile()))
        self.qa.setMinimumHeight(260)
        layout.addWidget(self.qa)
        row = QHBoxLayout()
        self.report_button = QPushButton("Open the quality report")
        self.report_button.clicked.connect(lambda: open_path(Layout(self.root).qa / "qa_report.html"))
        row.addWidget(self.report_button)
        row.addStretch()
        layout.addLayout(row)

        self.export_box = QGroupBox("Export (click to collapse)")
        self.export_box.setCheckable(True)
        self.export_box.setChecked(True)
        body = QWidget()
        el = QVBoxLayout(body)
        self.source = QLabel()
        self.source.setWordWrap(True)
        el.addWidget(self.source)
        grid = QGridLayout()
        self.boxes: dict[str, QCheckBox] = {}
        for r, (title, keys) in enumerate(GROUPS):
            grid.addWidget(QLabel(f"<b>{title}</b>"), r, 0)
            for c, key in enumerate(keys, 1):
                box = QCheckBox(LABELS[key])
                self.boxes[key] = box
                grid.addWidget(box, r, c)
        el.addLayout(grid)
        buttons = QHBoxLayout()
        self.export_button = QPushButton("Export")
        self.open_button = QPushButton("Open containing folder")
        self.close_button = QPushButton("Close")
        self.export_button.clicked.connect(self.do_export)
        self.open_button.clicked.connect(lambda: open_path(Layout(self.root).export))
        self.close_button.clicked.connect(self.win.close)
        for b in (self.export_button, self.open_button, self.close_button):
            buttons.addWidget(b)
        buttons.addStretch()
        el.addLayout(buttons)
        self.export_state = QLabel()
        self.export_state.setWordWrap(True)
        el.addWidget(self.export_state)
        self.export_box.toggled.connect(body.setVisible)
        QVBoxLayout(self.export_box).addWidget(body)
        layout.addWidget(self.export_box)
        data_box = QGroupBox("Raw footage and participant data")
        dl = QVBoxLayout(data_box)
        self.deletion = QLabel()
        self.deletion.setWordWrap(True)
        dl.addWidget(self.deletion)
        drow = QHBoxLayout()
        self.delete_now = QPushButton("Delete raw footage now ...")
        self.delete_now.setToolTip("For the trials marked 'delete' at setup. Shows the exact files first.")
        self.delete_now.clicked.connect(self.delete_marked)
        self.data_button = QPushButton("Participant data and deletion ...")
        self.data_button.clicked.connect(self.win.show_participant_data)
        drow.addWidget(self.delete_now)
        drow.addWidget(self.data_button)
        drow.addStretch()
        dl.addLayout(drow)
        layout.addWidget(data_box)
        layout.addStretch()
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(area)
        self.results = []

    def is_complete(self) -> bool:
        return self.session is not None and (Layout(self.root).export / "manifest.json").is_file()

    def refresh(self) -> None:
        if self.session is None or not self.win.reachable(self.win.pages.index(self)):
            return
        try:
            _path, self.results = write_report(self.session, self.root)
        except (OSError, ValueError) as exc:
            self.qa.setHtml(f"<p>The quality report could not be made: {html.escape(str(exc))}</p>")
            self.results = []
        parts = []
        for r in self.results:
            color, word = LIGHT_COLOR[r.light], LIGHT_TEXT[r.light]
            parts.append(f"<h3>Trial {html.escape(r.label)} ({html.escape(r.code)}): "
                         f"<span style='color:{color}'>{word}</span></h3><table cellspacing=4>")
            for m in r.metrics:
                val = "n/a" if m.value is None else f"{m.value:.1%}" if m.unit == "share" else f"{m.value} {m.unit}"
                parts.append(f"<tr><td>{html.escape(m.name)}</td><td>{html.escape(val)}</td>"
                             f"<td style='color:{LIGHT_COLOR[m.light]}'><b>{LIGHT_TEXT[m.light]}</b></td></tr>")
            parts.append("</table>")
            if r.warnings:
                parts.append("<ul>" + "".join(f"<li>{html.escape(w)}</li>" for w in r.warnings) + "</ul>")
            if r.preview:
                from pathlib import Path

                parts.append(f"<p><a href='{Path(r.preview).as_uri()}'>Open the 3D preview of trial "
                             f"{html.escape(r.label)}</a> - watch for limbs that jump or swap sides.</p>")
        self.qa.setHtml("".join(parts) or "<p>No results yet.</p>")
        st = self.session.settings
        for k in kinds(st.filter.type):
            self.boxes[k.key].setChecked(k.default if k.key != "bvh" else self.boxes["bvh"].isChecked())
            self.boxes[k.key].setToolTip(k.describe)
        self.source.setText("Joint angles, the scaled model and the BVH animation are computed from "
                            f"<b>{'LSTM-augmented markers' if st.use_augmentation else 'filtered keypoints'}</b>. "
                            "File names start with the trial code (a pseudonym).")
        self._show_deletion_state()

    def _due(self) -> list:
        """Trials marked for deletion at setup whose footage is still there."""
        return [t for t in self.session.active_trials if t.delete_raw and t.raw_present]

    def _show_deletion_state(self) -> None:
        lines = []
        for t in self.session.trials:
            if t.withdrawn_at:
                state = f"all data deleted on {t.withdrawn_at[:10]} (withdrawn)"
            elif t.raw_deleted_at:
                state = f"deleted on {t.raw_deleted_at[:10]}"
            elif t.delete_raw:
                state = "<b>still stored</b>, marked for deletion after export"
            else:
                state = "kept with consent until the analysis is finished - delete it then"
            lines.append(f"Trial {html.escape(t.label)} ({html.escape(t.code)}): {state}")
        self.deletion.setText("<br>".join(lines))
        self.delete_now.setEnabled(bool(self._due()) and not self.win.busy)

    def delete_marked(self) -> None:
        """§8.4 safeguard 1: the deletion chosen at setup runs only once results are complete and exported."""
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
        self.close_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.delete_now.setEnabled(False)
        self.data_button.setEnabled(False)
        self.export_state.setText("Exporting ...")

        def done(result):
            out, warnings = result
            self.win.set_busy(False)
            self.close_button.setEnabled(True)
            self.export_button.setEnabled(True)
            self.data_button.setEnabled(True)
            self._show_deletion_state()
            self.completeChanged.emit()
            self.export_state.setText(f"Exported to {out}" + ("<br>" + "<br>".join(html.escape(w) for w in warnings)
                                                                if warnings else ""))

        def failed(msg, details):
            self.win.set_busy(False)
            self.close_button.setEnabled(True)
            self.export_button.setEnabled(True)
            self.data_button.setEnabled(True)
            self._show_deletion_state()
            self.export_state.setText("")
            error_box(self, "Export failed", msg, details)

        Background(self, export, self.session, self.root, selection, None, qa, on_done=done, on_error=failed)
