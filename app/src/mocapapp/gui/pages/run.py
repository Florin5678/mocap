"""Step 6: pre-Run summary (§6.2 last paragraph) and the run itself (§6.6): progress, cancel, resume, Fix sync."""

from __future__ import annotations

import html
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core import runner
from mocapapp.core.assemble import assemble_trials
from mocapapp.core.calibration import intrinsics as intr
from mocapapp.core.session import Layout
from mocapapp.core.workflow import run_ready
from mocapapp.gui.common import (
    Background,
    IssueView,
    clear_table,
    confirm,
    error_box,
    fit_rows,
    note,
    para,
    readonly,
    status_badge,
)
from mocapapp.gui.main_window import Page

LOG_LINES = 400


class _Bridge(QObject):
    """Carries runner events from the worker thread to the GUI thread (queued signal)."""

    event = Signal(object)


class RunPage(Page):
    key, title = "run", "Run"

    def __init__(self, win):
        super().__init__(win)
        self.cancel = threading.Event()
        self.bridge = _Bridge()
        self.bridge.event.connect(self._on_event)
        self.n_labels = 1
        self.done_labels: list[str] = []

        inner = QWidget()
        layout = QVBoxLayout(inner)
        self.summary = QTextBrowser()
        self.summary.setMinimumHeight(220)
        layout.addWidget(self.summary)
        self.sync_box = QCheckBox("I recorded a sync cue at the start of every trial (required).")
        self.sync_box.toggled.connect(self._sync_toggled)
        layout.addWidget(self.sync_box)
        self.issues = IssueView(min_height=70)
        self.issues.setMaximumHeight(150)
        layout.addWidget(self.issues)

        adv = QGroupBox("Advanced settings (click to show)")
        adv.setCheckable(True)
        adv.setChecked(False)
        self.adv_inner = QWidget()
        form = QFormLayout(self.adv_inner)
        self.mode = QComboBox()
        self.mode.addItems(["lightweight", "balanced", "performance"])
        self.cutoff = QDoubleSpinBox()
        self.cutoff.setRange(1, 30)
        self.cutoff.setSuffix(" Hz")
        self.no_aug = QCheckBox("Compute kinematics from the filtered keypoints instead of the LSTM-augmented markers")
        self.bvh = QCheckBox("Also make a BVH animation file")
        form.addRow("Pose detection", self.mode)
        form.addRow("Smoothing cut-off", self.cutoff)
        form.addRow(para("6 Hz suits walking and other slow movement; fast movements need 6-15 Hz."))
        form.addRow(self.no_aug)
        form.addRow(self.bvh)
        form.addRow(para("Seated, crouching or lying movements: model scaling is less reliable (Pose2Sim)."))
        self.adv_inner.setVisible(False)
        adv.toggled.connect(self.adv_inner.setVisible)
        al = QVBoxLayout(adv)
        al.addWidget(self.adv_inner)
        for w in (self.mode, self.cutoff, self.no_aug, self.bvh):
            sig = (w.currentIndexChanged if isinstance(w, QComboBox) else
                   w.valueChanged if isinstance(w, QDoubleSpinBox) else w.toggled)
            sig.connect(self._settings_changed)
        layout.addWidget(adv)

        row = QHBoxLayout()
        self.run_button = QPushButton("Run")
        self.run_button.setMinimumHeight(40)
        self.cancel_button = QPushButton("Stop")
        self.restart_box = QCheckBox("Start over (ignore finished stages)")
        self.run_button.clicked.connect(self.start)
        self.cancel_button.clicked.connect(self.stop)
        self.cancel_button.setEnabled(False)
        row.addWidget(self.run_button)
        row.addWidget(self.cancel_button)
        row.addWidget(self.restart_box)
        row.addStretch()
        layout.addLayout(row)
        self.stage = QLabel()
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        layout.addWidget(self.stage)
        layout.addWidget(self.bar)
        self.show_log = QCheckBox("Show Pose2Sim's own output")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(LOG_LINES)
        self.log.setVisible(False)
        self.show_log.toggled.connect(self.log.setVisible)
        layout.addWidget(self.show_log)
        layout.addWidget(self.log)

        res = QGroupBox("Trials")
        rl = QVBoxLayout(res)
        self.trials = QTableWidget(0, 5)
        self.trials.setHorizontalHeaderLabels(["Trial", "Code", "State", "", ""])
        self.trials.horizontalHeader().setStretchLastSection(False)
        rl.addWidget(self.trials)
        rl.addWidget(note("If a trial's synchronisation looks unreliable it stops before 3D positions are computed. "
                          "'Check sync' opens Pose2Sim's player where you can correct the offsets; 'Accept as is' "
                          "continues with the automatic result."))
        layout.addWidget(res)
        layout.addStretch()
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(area)

    # ---------- summary ----------

    def refresh(self) -> None:
        if self.session is None:
            return
        ses, root = self.session, self.root
        self._loading = True
        st = ses.settings
        self.mode.setCurrentText(st.pose_mode)
        self.cutoff.setValue(st.filter.cutoff_hz)
        self.no_aug.setChecked(not st.use_augmentation)
        self.bvh.setChecked(st.make_bvh)
        self.sync_box.setChecked(ses.sync_cue_confirmed)
        self._loading = False
        locked = any(ses.run_state.stage_done.values())
        self.adv_inner.setEnabled(not locked or self.restart_box.isChecked())
        self.adv_inner.setToolTip("Tick 'Start over' to change settings after a run." if locked else "")

        work = Layout(root).calibration_work
        rows = []
        for t in ses.trials:
            if not t.raw_present:
                gone = "all data deleted (withdrawn)" if t.withdrawn_at else "raw footage deleted"
                rows.append(f"<tr><td>{t.label}</td><td>{html.escape(t.code)}</td><td colspan=3>{gone}</td></tr>")
                continue
            h = f"{t.height_m:.2f} m" if t.height_m else "estimated"
            rows.append(f"<tr><td>{t.label}</td><td>{html.escape(t.code)}</td><td>{h}</td>"
                        f"<td>{'yes' if t.consent.attested else '<b>no</b>'}</td>"
                        f"<td>{'delete' if t.delete_raw else 'keep'}</td></tr>")
        cams = []
        for c in ses.cameras:
            if ses.calibration_source == "imported":
                cal = "imported (sample)"
            else:
                i = intr.load_result(work, c.name)
                e = next((x for x in ses.extrinsics if x.get("camera") == c.name), None)
                cal = (f"lens {i.rms_px:.2f} px" if i else "lens ?") + (
                    f", position {e['error_cm']:.2f} cm" + (" (accepted above limit)" if e.get("overridden") else "")
                    if e else ", position ?")
            files = ", ".join(Path(f).name for f in c.trial_files.values())
            cams.append(f"<tr><td>{c.name}</td><td>{html.escape(c.source_dir)}</td><td>{cal}</td>"
                        f"<td>{html.escape(files)}</td></tr>")
        method = ses.trials[0].mapping_method if ses.trials else "?"
        self.summary.setHtml(
            "<h3>Before you run</h3><table cellspacing=6><tr><th>Trial</th><th>Code</th><th>Height</th>"
            f"<th>Consent</th><th>Raw footage</th></tr>{''.join(rows)}</table>"
            f"<p>Trial mapping: {html.escape(method or '?')}</p><table cellspacing=6><tr><th>Camera</th>"
            f"<th>Folder</th><th>Calibration</th><th>Trial files</th></tr>{''.join(cams)}</table>"
            "<p>Kinematics (.mot/.osim/.bvh) are computed from "
            f"{'LSTM-augmented markers' if st.use_augmentation else 'filtered keypoints'}.</p>")
        self._check()
        self._fill_trials()

    _loading = False

    def _check(self) -> list:
        issues = run_ready(self.session, self.root)
        # a missing trial folder is fixed by Run itself (it rebuilds the trial configs first)
        issues = [i for i in issues if "assemble" not in (i.fix or "")]
        gui_fix = {"sync cue": "Tick the box above.", "consent": "Tick 'Signed consent received' in step 1."}
        for i in issues:
            if (i.fix or "").startswith("mocapapp "):  # CLI hints mean nothing in the window
                i.fix = gui_fix["sync cue"] if i.where == "sync cue" else gui_fix["consent"]
        self.issues.show_issues(issues, "Ready to run.")
        blocking = any(i.level in ("error", "action") for i in issues)
        self.run_button.setEnabled(not blocking and not self.win.busy)
        return issues

    def _sync_toggled(self, on: bool) -> None:
        if self.session is None or self._loading:
            return
        self.session.sync_cue_confirmed = on
        self.win.save()
        self._check()

    def _settings_changed(self) -> None:
        if self.session is None or self._loading:
            return
        st = self.session.settings
        st.pose_mode = self.mode.currentText()
        st.filter.cutoff_hz = self.cutoff.value()
        st.use_augmentation = not self.no_aug.isChecked()
        st.make_bvh = self.bvh.isChecked()
        self.win.save()

    def _fill_trials(self) -> None:
        rs = self.session.run_state
        clear_table(self.trials)
        self.trials.setRowCount(len(self.session.trials))
        for r, t in enumerate(self.session.trials):
            status = rs.trial_status.get(t.label, "pending")
            if t.withdrawn_at:
                status = "withdrawn"
            elif not t.raw_present and status != "done":
                status = "raw footage deleted"
            msg = rs.errors.get(t.label) or "; ".join(rs.sync.get(t.label, {}).get("issues", []))
            self.trials.setItem(r, 0, readonly(t.label))
            self.trials.setItem(r, 1, readonly(t.code))
            label = QLabel(status_badge(status) + (f" - {html.escape(msg)}" if msg and status != "done" else ""))
            label.setWordWrap(True)
            self.trials.setCellWidget(r, 2, label)
            fix = QPushButton("Check sync ...")
            accept = QPushButton("Accept as is")
            fix.clicked.connect(lambda _=False, lb=t.label: self.fix_sync(lb))
            accept.clicked.connect(lambda _=False, lb=t.label: self.start([lb], accept_sync=True))
            for col, b in ((3, fix), (4, accept)):
                b.setEnabled(status == "needs_sync_fix" and not self.win.busy)
                self.trials.setCellWidget(r, col, b)
        self.trials.resizeColumnsToContents()
        fit_rows(self.trials)
        self.trials.horizontalHeader().setStretchLastSection(False)

    def is_complete(self) -> bool:
        if self.session is None:
            return False
        st = self.session.run_state.trial_status
        # trials whose footage was deleted can never run again: they do not hold the wizard back
        return bool(self.session.trials) and all(st.get(t.label) == "done" or not t.raw_present
                                                 for t in self.session.trials)

    # ---------- running ----------

    def start(self, labels: list[str] | None = None, accept_sync: bool = False) -> None:
        ses, root = self.session, self.root
        labels = labels or [t.label for t in ses.trials]
        resume = not self.restart_box.isChecked()
        try:
            assemble_trials(ses, root, progress=lambda m: None)  # current settings, heights and points
            if not resume:
                for lb in labels:
                    ses.run_state.stage_done.pop(lb, None)
                    ses.run_state.trial_status.pop(lb, None)
                    ses.run_state.sync.pop(lb, None)
        except (OSError, RuntimeError) as exc:
            error_box(self, "Cannot start", str(exc))
            return
        issues = [i for i in runner.preflight(ses, root, labels) if i.level in ("error", "action")]
        if issues:
            self.issues.show_issues(issues)
            return
        est = runner.estimate_seconds(ses, root, labels)
        if not accept_sync and not confirm(
                self, "Run", f"Rough time estimate on this computer: about {est / 60:.0f} minutes for "
                f"{len(labels)} trial(s). You can leave the computer; Stop keeps finished stages for later.",
                "Run"):
            return
        self.restart_box.setChecked(False)
        self.cancel.clear()
        self.n_labels, self.done_labels = len(labels), []
        self.labels = labels
        self.log.clear()
        self.bar.setValue(0)
        self.win.set_busy(True)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._fill_trials()
        Background(self, runner.run_session, ses, root, labels, self.bridge.event.emit, self.cancel, resume,
                   accept_sync, on_done=self._finished, on_error=self._failed)

    def stop(self) -> None:
        self.cancel.set()
        self.stage.setText("Stopping ...")
        self.cancel_button.setEnabled(False)

    def _on_event(self, e: runner.Event) -> None:
        if e.kind == "line":
            self.log.appendPlainText(e.message)
            return
        k = self.labels.index(e.trial) if e.trial in getattr(self, "labels", []) else 0
        if e.fraction is not None:
            self.bar.setValue(int(1000 * (k + e.fraction) / max(1, self.n_labels)))
        code = self.session.trial(e.trial).code if e.trial else ""
        if e.kind == "stage_start":
            self.stage.setText(f"Trial {e.trial} ({code}), {k + 1} of {self.n_labels}: {e.message} ...")
        elif e.kind == "stage_end" and not e.ok:
            self.log.appendPlainText(f"PROBLEM: {e.message}")
        elif e.kind in ("trial_end", "needs_sync_fix"):
            self._fill_trials()

    def _finished(self, _results) -> None:
        self.win.set_busy(False)
        self.cancel_button.setEnabled(False)
        self.bar.setValue(1000)
        self.stage.setText("Finished. See each trial's state below." if not self.is_complete() else
                           "All trials finished. Continue to the results.")
        self.win.save()
        self.refresh()
        self.completeChanged.emit()

    def _failed(self, msg: str, details: str) -> None:
        self.win.set_busy(False)
        self.cancel_button.setEnabled(False)
        if self.cancel.is_set():
            self.stage.setText("Stopped. Press Run to continue where it stopped.")
        else:
            error_box(self, "Run stopped", msg, details)
        self.win.save()
        self.refresh()

    def fix_sync(self, label: str) -> None:
        self.labels, self.n_labels = [label], 1
        self.win.set_busy(True)
        self.stage.setText(f"Trial {label}: the synchronisation player opens in a separate window. Close it when "
                           "the offsets look right; the run then continues.")
        Background(self, runner.fix_sync, self.session, self.root, label, self.bridge.event.emit,
                   on_done=self._finished, on_error=self._failed)
