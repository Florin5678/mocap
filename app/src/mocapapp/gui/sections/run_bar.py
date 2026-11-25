"""The bar at the bottom of the Session section (R15): what the run is working on now, overall progress, Stop,
Pose2Sim's own output, and the time and space estimate. It starts runs and the sync player (§6.6); the trials table
shows each trial's own progress from `activity`."""

from __future__ import annotations

import html
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core import runner
from mocapapp.core.assemble import assemble_trials
from mocapapp.gui.common import Background, confirm, error_box

LOG_LINES = 400


class _Bridge(QObject):
    """Carries runner events from the worker thread to the GUI thread (queued signal)."""

    event = Signal(object)


class RunBar(QWidget):
    def __init__(self, section):
        super().__init__()
        self.section = section  # SessionSection: session, root, win, after_run(), show_status()
        self.cancel = threading.Event()
        self.activity: dict[str, str] = {}  # label -> what happens to it now (its Status while busy)
        self.labels: list[str] = []
        self.bridge = _Bridge()
        self.bridge.event.connect(self._on_event)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        row = QHBoxLayout()
        self.stage = QLabel("Nothing running.")
        self.stage.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setMaximumWidth(260)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setToolTip("Stops after the current step; finished stages are kept for later.")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop)
        self.show_log = QCheckBox("Show Pose2Sim's own output")
        row.addWidget(self.stage, 1)
        row.addWidget(self.bar)
        row.addWidget(self.stop_button)
        row.addWidget(self.show_log)
        layout.addLayout(row)
        self.estimate = QLabel()
        self.estimate.setWordWrap(True)
        self.estimate.setVisible(False)
        layout.addWidget(self.estimate)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(LOG_LINES)
        self.log.setMaximumHeight(160)
        self.log.setVisible(False)
        self.show_log.toggled.connect(self.log.setVisible)
        layout.addWidget(self.log)

    @property
    def session(self):
        return self.section.session

    @property
    def root(self):
        return self.section.root

    @property
    def win(self):
        return self.section.win

    # ---------- running ----------

    def start(self, labels: list[str], resume: bool = True, accept_sync: bool = False, left_out: str = "") -> bool:
        """Run these trials (each on its own: one failure never stops the others). Returns False when nothing
        started (a problem shown, or the researcher said no)."""
        ses, root = self.session, self.root
        try:
            assemble_trials(ses, root, progress=lambda m: None, labels=labels)  # current settings and points
        except (OSError, RuntimeError, ValueError) as exc:
            error_box(self, "Cannot start", str(exc))
            return False
        issues = runner.preflight(ses, root, labels)
        blocking = [i for i in issues if i.level in ("error", "action")]
        if blocking:
            error_box(self, "Cannot run yet", "<br><br>".join(
                f"<b>{html.escape(i.where)}:</b> {html.escape(i.what)}"
                + (f" {html.escape(i.fix)}" if i.fix and not i.fix.startswith("mocapapp") else "")  # no CLI hints
                for i in blocking))
            return False
        est = runner.estimate_seconds(ses, root, labels)
        need = runner.space_needed(ses, root, labels)
        space = f" Working files: up to about {need / 1e9:.1f} GB." if need else ""
        warnings = "".join(f"<br><br><b>Check:</b> {html.escape(i.what)}" for i in issues)
        if not accept_sync and not confirm(
                self, "Run", f"Rough time estimate on this computer: about {est / 60:.0f} minutes for "
                f"{len(labels)} trial(s).{space} You can leave the computer; Stop keeps finished stages for later."
                f"{warnings}{left_out}", "Run"):
            return False
        if not resume:  # Start over (R19): finished stages are done again
            for lb in labels:
                for table in (ses.run_state.stage_done, ses.run_state.trial_status, ses.run_state.sync):
                    table.pop(lb, None)
        self.cancel.clear()
        self.labels = list(labels)
        self.log.clear()
        self.bar.setValue(0)
        self.estimate.setText(f"About {est / 60:.0f} minutes for {len(labels)} trial(s) (rough).{space}")
        self.estimate.setVisible(True)
        self.activity = {lb: "waiting for its turn" for lb in labels}
        self._busy(True)
        Background(self, runner.run_session, ses, root, labels, self.bridge.event.emit, self.cancel, resume,
                   accept_sync, on_done=self._finished, on_error=self._failed)
        return True

    def fix_sync(self, label: str) -> None:
        """Pose2Sim's synchronisation player for one trial; the run then continues with its offsets."""
        self.labels = [label]
        self.bar.setValue(0)
        self.activity = {label: "sync player open in a separate window: close it when the offsets look right"}
        self._busy(True, stop=False)
        self.stage.setText(f"Trial {label}: the synchronisation player opens in a separate window. Close it when "
                           "the offsets look right; the run then continues.")
        Background(self, runner.fix_sync, self.session, self.root, label, self.bridge.event.emit,
                   on_done=self._finished, on_error=self._failed)

    def stop(self) -> None:
        self.cancel.set()
        self.stage.setText("Stopping after the current step ...")
        self.stop_button.setEnabled(False)

    def _busy(self, on: bool, stop: bool = True) -> None:
        self.win.set_busy(on)
        self.stop_button.setEnabled(on and stop)
        self.section.show_status()

    def _on_event(self, e: runner.Event) -> None:
        if e.kind == "line":
            self.log.appendPlainText(e.message)
            return
        k = self.labels.index(e.trial) if e.trial in self.labels else 0
        n = max(1, len(self.labels))
        if e.fraction is not None:
            self.bar.setValue(max(self.bar.value(), int(1000 * (k + e.fraction) / n)))
        if e.kind == "stage_start":
            code = self.session.trial(e.trial).code if e.trial else ""
            self.stage.setText(f"Trial {e.trial} ({code}), {k + 1} of {n}: {e.message} ...")
            self.activity[e.trial] = f"{e.message} ..."
            self.section.show_status()
        elif e.kind == "stage_end" and not e.ok:
            self.log.appendPlainText(f"PROBLEM: {e.message}")
        elif e.kind in ("trial_end", "needs_sync_fix"):
            self.activity.pop(e.trial, None)
            self.section.show_status()
            results = self.win.section("results")
            if self.win.current_page() is results:  # trials appear there as they finish
                results.refresh()

    def _finished(self, _results) -> None:
        self.activity = {}
        self.bar.setValue(1000)
        states = self.session.run_state.trial_status
        done = [lb for lb in self.labels if states.get(lb) == "done"]
        self.stage.setText(f"Finished: {len(done)} of {len(self.labels)} trial(s) done. See each trial's status "
                           "above; the results are in Results.")
        self.estimate.setVisible(False)
        self._busy(False)
        self.win.save()
        self.section.after_run()

    def _failed(self, msg: str, details: str) -> None:
        self.activity = {}
        self.estimate.setVisible(False)
        self._busy(False)
        if self.cancel.is_set():
            self.stage.setText("Stopped. Run the trials again to continue where they stopped.")
        else:
            self.stage.setText("The run stopped with a problem.")
            error_box(self, "Run stopped", msg, details)
        self.win.save()
        self.section.after_run()
