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
        self.running = False  # a run's thread is working (win.busy also covers an open sync player)
        self.players: list[str] = []  # trials whose sync player opens next, one after another
        self.player: str | None = None  # whose player is open now
        self.pending: list[str] = []  # sync checked or accepted, waiting for the run to take them (_take)
        self.left: list[str] = []  # what a Stop or a failing player left undone, said when all is over
        self.stopped = False
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
        self.stopped = False
        self.running = True
        self.labels = list(labels)
        self.log.clear()
        self.bar.setValue(0)
        self.estimate.setText(f"About {est / 60:.0f} minutes for {len(labels)} trial(s) (rough).{space}")
        self.estimate.setVisible(True)
        self.activity = {lb: "waiting for its turn" for lb in labels}
        self._busy(True)
        Background(self, runner.run_session, ses, root, labels, self.bridge.event.emit, self.cancel, resume,
                   accept_sync, self._take, on_done=self._finished, on_error=self._failed)
        return True

    # ---------- checking the sync of several trials (§6.6) ----------

    def sync_eligible(self, label: str) -> bool:
        """Needs a sync fix and is not already waiting for its player or its turn."""
        ses = self.session
        trial = next((t for t in ses.trials if t.label == label), None)
        return (trial is not None and label not in self.activity and trial.raw_present and not trial.withdrawn_at
                and ses.run_state.trial_status.get(label) == "needs_sync_fix")

    def check_sync(self, labels: list[str]) -> None:
        """Pose2Sim's synchronisation player for each trial, one after another; each trial whose player was closed
        runs in the background straight away (after the current run, if one is going), so the next player can be
        handled meanwhile."""
        new = [lb for lb in labels if self.sync_eligible(lb)]
        for lb in new:
            self.activity[lb] = "waiting for its sync player (they open one after another)"
        self.players += new
        if new and self.player is None:
            self.stopped = False
            if not self.running:
                self._busy(True, stop=True)
            self._next_player()
        else:
            self.section.show_status()

    def accept_sync(self, labels: list[str]) -> None:
        """Continue these trials with the automatic synchronisation, in the background."""
        self.stopped = False
        for lb in [lb for lb in labels if self.sync_eligible(lb)]:
            self._queue(lb)

    def _next_player(self) -> None:
        if not self.players or self.stopped:
            for lb in self.players:
                self.activity.pop(lb, None)
            self.players, self.player = [], None
            if not self.running:
                self._idle()
            return
        label = self.player = self.players.pop(0)
        self.activity[label] = "sync player open in a separate window: close it when the offsets look right"
        if not self.running:
            self.stage.setText(f"Trial {label}: the synchronisation player is open in a separate window. Close it "
                               "when the offsets look right; the trial then runs in the background.")
        self.section.show_status()
        Background(self, runner.sync_player, self.session, self.root, label,
                   on_done=lambda ok, lb=label: self._player_closed(lb, ok),
                   on_error=lambda msg, _d, lb=label: self._player_closed(lb, False, msg))

    def _player_closed(self, label: str, ok: bool, problem: str = "") -> None:
        self.player = None
        self.activity.pop(label, None)
        if not ok:
            self.log.appendPlainText(f"Trial {label}: the synchronisation player stopped with an error {problem}")
            self.left.append(f"Trial {label}: the player stopped with an error; check its sync again.")
        elif self.stopped:
            self.left.append(f"Trial {label}: its sync was checked but it did not run; press Accept as is to "
                             "continue with the checked sync.")
        else:
            self._queue(label)
        self._next_player()

    def _queue(self, label: str) -> None:
        """Run this trial (its sync checked or accepted): now, or as soon as the current run gets to it."""
        self.activity[label] = "sync checked: waits for its turn"
        self.labels.append(label)
        self.pending.append(label)
        if self.running:
            self.section.show_status()
            return
        self.running = True
        self.cancel.clear()
        self.labels = [label]
        self.left = []
        self.bar.setValue(0)
        self._busy(True)
        Background(self, runner.run_queue, self.session, self.root, self._take, self.bridge.event.emit, self.cancel,
                   on_done=self._finished, on_error=self._failed)

    def _take(self) -> str | None:  # called by the runner's thread; list.pop is atomic
        try:
            return self.pending.pop(0)
        except IndexError:
            return None

    def _idle(self) -> None:
        """No run and no player left: the window is free again."""
        self.estimate.setVisible(False)
        if self.left:
            self.stage.setText("<br>".join(html.escape(x) for x in self.left))
            self.left = []
        self._busy(False)
        self.win.save()
        self.section.after_run()

    def stop(self) -> None:
        """Stops the run after the current step; sync players not opened yet are dropped (an open one stays until
        it is closed)."""
        self.cancel.set()
        self.stopped = True
        for lb in self.pending:
            self.activity.pop(lb, None)
            self.left.append(f"Trial {lb}: its sync was checked but it did not run; press Accept as is to continue "
                             "with the checked sync.")
        self.pending.clear()
        for lb in self.players:
            self.activity.pop(lb, None)
        self.players = []
        self.stage.setText("Stopping after the current step ..." if self.running else
                           "Stopping: close the open synchronisation player.")
        self.stop_button.setEnabled(False)
        self.section.show_status()

    def _busy(self, on: bool, stop: bool = True) -> None:
        if on != self.win.busy:  # a run can start while a sync player keeps the window busy already
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
        self.running = False
        if self.pending and not self.stopped:  # queued just after the run's last look at the queue
            label = self.pending.pop(0)
            self._queue(label)
            return
        for lb in list(self.activity):
            if lb not in self.players and lb != self.player:
                self.activity.pop(lb)
        self.bar.setValue(1000)
        states = self.session.run_state.trial_status
        done = [lb for lb in self.labels if states.get(lb) == "done"]
        self.stage.setText(f"Finished: {len(done)} of {len(self.labels)} trial(s) done. See each trial's status "
                           "above; the results are in Results.")
        if self.left:
            self.left.insert(0, self.stage.text())
        if self.player is not None:  # the run is over, but a sync player is still open: wait for it
            self.stop_button.setEnabled(not self.stopped)
            self.win.save()
            self.section.show_status()
            return
        self._idle()

    def _failed(self, msg: str, details: str) -> None:
        self.running = False
        for lb in list(self.activity):
            if lb != self.player:
                self.activity.pop(lb)
        self.players = []
        self.pending.clear()
        if self.cancel.is_set():
            self.left.insert(0, "Stopped. Run the trials again to continue where they stopped.")
        else:
            self.left.insert(0, "The run stopped with a problem.")
            error_box(self, "Run stopped", msg, details)
        if self.player is not None:
            self.stopped = True
            self.stage.setText("Stopped: close the open synchronisation player.")
            self.section.show_status()
            return
        self._idle()
