"""P5 (PROJECT.md §16.6): the Session hub. Exit criterion: the full wizard test rewritten on the hub (cameras and
layout calibrated in their sections, participants, trials added and checked in Session, a run, results and viewer).

Modal dialogs are replaced by stand-ins that answer like a researcher would; Pose2Sim's stages are faked."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from mocapapp.core import readiness, runner, workflow
from mocapapp.core.calibration import service
from mocapapp.core.session import Layout, load_session, save_session
from mocapapp.gui.main_window import MainWindow
from mocapapp.gui.sections import layouts as layouts_mod
from mocapapp.gui.sections import run_bar
from mocapapp.gui.sections import session as session_mod
from tests.conftest import make_video
from tests.m3_fakes import SYNC_BAD, FakeStages, make_session
from tests.test_window_privacy import _col
from tests.test_window_projects import CAMS, _select, calibrate_in_window

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def win(qtbot, monkeypatch):
    def no_modal(box):  # a message box nobody expected means something went wrong: fail instead of hanging
        raise AssertionError(f"message box: {box.text()} {box.detailedText()}")

    monkeypatch.setattr(QMessageBox, "exec", no_modal)
    w = MainWindow()
    qtbot.addWidget(w)
    return w


def _idle(qtbot, win, timeout=120000):
    qtbot.waitUntil(lambda: not win.busy, timeout=timeout)


def _fake_run(monkeypatch, fake=None):
    fake = fake or FakeStages()
    monkeypatch.setattr(run_bar, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(runner, "run_stage", fake)
    monkeypatch.setattr(runner, "_fps", lambda *a: 30.0)
    monkeypatch.setattr(runner, "estimate_seconds", lambda *a: 60)
    return fake


def _dialogs(monkeypatch, *steps):
    """Each trial dialog that opens runs the next step instead of waiting for a person: the step looks at the
    dialog, fills it in and presses one of its buttons."""
    from mocapapp.gui import trial_dialog

    queue = list(steps)

    def run(dlg):
        queue.pop(0)(dlg)
        return dlg.result()

    monkeypatch.setattr(trial_dialog.TrialDialog, "exec", run)
    return queue


def _save(dlg):
    dlg.save_button.click()


def _use_suggested(dlg):
    dlg.use_suggested.click()
    dlg.save_button.click()


def _status(hub, label):
    return hub.cells[label][0].text()


def test_full_project_on_the_hub(win, tmp_path, qtbot, monkeypatch):
    _fake_run(monkeypatch)
    win.section("session").trials_spin.setValue(2)  # two trials per participant
    raw, root, ses = calibrate_in_window(win, tmp_path, qtbot, monkeypatch, trials=3)

    # Session: Add trial, pre-filled (R10): a new participant first, then theirs until they have two trials
    from mocapapp.gui.trial_dialog import NEW

    win.go("session")
    hub = win.section("session")

    def first(dlg):
        assert dlg.participant.currentData() == NEW and dlg.layout_box.currentData() == "L1"
        assert dlg.windowTitle() == "Add a trial" and not dlg.state.isVisibleTo(dlg)
        dlg.name.setText("walking")
        dlg.save_button.click()

    def like_before(who):
        def step(dlg):
            assert dlg.participant.currentData() == who and dlg.name.text() == "walking"
            dlg.save_button.click()
        return step

    _dialogs(monkeypatch, first, like_before("1"), like_before(NEW))
    hub.buttons["add"].click()
    assert [(t.label, t.participant, t.layout, t.name) for t in ses.trials] == [("1", "1", "L1", "walking")]
    assert hub.selected() == ["1"]
    hub.buttons["add"].click()
    hub.buttons["add"].click()  # participant 1 has their two trials: a new participant
    assert [t.participant for t in ses.trials] == ["1", "1", "2"]
    assert "No height" in _status(hub, "1") and "not ready" in _status(hub, "1")
    assert win.badges()["session"] == "3 to do"

    # Participants: heights and consent
    win.go("participants")
    people = win.section("participants")
    for row in (0, 1):
        box = people.table.cellWidget(row, _col(people.table, "Consent"))
        box.setCurrentIndex(box.findData("keep"))
        qtbot.waitUntil(lambda r=row, b=box: people.table.cellWidget(r, _col(people.table, "Consent")) is not b)
        height = people.table.cellWidget(row, _col(people.table, "Height"))
        height.setText("178")
        height.editingFinished.emit()
        qtbot.waitUntil(lambda r=row, h=height: people.table.cellWidget(r, _col(people.table, "Height")) is not h)
    assert all(p.consent.attested and p.height_m == 1.78 for p in ses.participants)

    # Session: Edit suggests each trial's videos by number; accept them, then check and copy
    win.go("session")
    for label in ("1", "2", "3"):
        def suggested(dlg, label=label):
            assert dlg.windowTitle().startswith(f"Trial {label} ") and dlg.use_suggested.isVisibleTo(dlg)
            assert f"suggested: walk{label}.mp4" in dlg.table.item(0, 1).text()
            _use_suggested(dlg)
        _dialogs(monkeypatch, suggested)
        _select(hub.table, [int(label) - 1])
        hub.buttons["edit"].click()
        assert Path(ses.trial(label).videos["cam01"]).name == f"walk{label}.mp4"
    assert "not checked and copied" in _status(hub, "1")
    _select(hub.table, [0, 1])
    assert not hub.buttons["edit"].isEnabled()  # one trial at a time
    assert hub.buttons["check"].text() == "Check and copy videos (2)" and hub.run_button.text() == "Run selected (2)"
    hub.buttons["check"].click()
    assert win.busy
    _idle(qtbot, win)
    assert all(workflow.trial_inputs_ready(ses, root, lb) for lb in ("1", "2"))
    assert not workflow.trial_inputs_ready(ses, root, "3") and hub.selected() == ["1", "2"]
    assert readiness.trial_status(ses, root, "1") == "ready" and "ready" in _status(hub, "2")
    def copied(dlg):
        text = dlg.copy.text()
        assert "Checked and copied: the trial's 2 video(s)" in text and " free." in text
        assert "Status:" in dlg.state.text() and "ready" in dlg.state.text()
        dlg.reject()

    _dialogs(monkeypatch, copied)
    _select(hub.table, [0])
    hub.buttons["edit"].click()

    # R8 in the dialog: a trial recorded on another day than its layout
    def other_day(dlg):
        assert not dlg.accept_layout.isVisibleTo(dlg)
        dlg.date.setText("2020-01-01")
        dlg.date.editingFinished.emit()
        assert dlg.accept_layout.isVisibleTo(dlg) and "recorded on 2020-01-01" in dlg.warning.text()
        dlg.accept_layout.setChecked(True)
        _save(dlg)

    _dialogs(monkeypatch, other_day)
    hub.table.cellDoubleClicked.emit(2, 0)  # a double-click opens it too
    assert ses.trial("3").date == "2020-01-01" and ses.trial("3").layout_override

    # Run with nothing selected: every ready trial (3 is not copied yet, so it waits)
    hub.table.clearSelection()
    assert hub.run_button.text() == "Run ready trials"
    hub.run_button.click()
    assert win.busy and not hub.run_button.isEnabled() and hub.bar.stop_button.isEnabled()
    _idle(qtbot, win)
    states = load_session(root).run_state.trial_status
    assert states == {"1": "done", "2": "done"}, states
    assert "done" in _status(hub, "1") and "Finished: 2 of 2" in hub.bar.stage.text()
    assert "3" not in states and "not ready" in _status(hub, "3")

    # Results: report, export, viewer
    win.go("results")
    res = win.section("results")
    assert [r.label for r in res.results if r.status == "done"] == ["1", "2"] and "Trial 1" in res.qa.toPlainText()
    assert res.results[2].light == "grey"  # trial 3 has not run yet
    res.boxes["bvh"].setChecked(True)
    res.do_export()
    _idle(qtbot, win)
    names = {p.name for p in Layout(root).export.iterdir()}
    code = ses.participant("1").code
    assert f"{code}_trial1_skeleton_animation.bvh" in names and "manifest.json" in names
    res.tabs.setCurrentIndex(1)  # 3D
    assert res.viewer.trial_box.count() == 2 and res.viewer.markers is not None


def _ready_project(root, n_trials):
    """make_session's trials, ready to run: imported calibration, copied inputs."""
    ses = make_session(root, n_trials=n_trials)
    ses.calibration_source = "imported"
    Layout(root).calibration_work.mkdir(exist_ok=True)
    Layout(root).calib_file.write_text("[cam_1]\n")
    for t in ses.trials:
        for c in ses.cameras:
            p = Layout(root).trial_input(t.label, c.id)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"fake")
            t.videos[c.id] = str(p)
    save_session(ses, root)
    return ses


def _hub(win, root, ses):
    win.set_session(root, ses)
    win.go("session")
    return win.section("session")


def test_sync_buttons_in_the_trials_table(win, tmp_path, qtbot, monkeypatch):
    _fake_run(monkeypatch, FakeStages(sync_log=SYNC_BAD))
    ses = _ready_project(tmp_path, 2)
    hub = _hub(win, tmp_path, ses)
    assert all(readiness.trial_ready(ses, tmp_path, lb) for lb in ("1", "2"))
    hub.run_button.click()
    _idle(qtbot, win)
    assert ses.run_state.trial_status["1"] == "needs_sync_fix"
    _state, one, fix, accept = hub.cells["1"]
    assert "needs sync fix" in _status(hub, "1") and fix.isEnabled() and accept.isEnabled()
    assert not one.isEnabled()  # Fix sync or Accept as is, not a plain re-run

    hub.cells["1"][3].click()  # Accept as is
    _idle(qtbot, win)
    assert ses.run_state.trial_status["1"] == "done" and "done" in _status(hub, "1")


def test_check_sync_of_a_trial_that_failed_after_its_sync(win, tmp_path, qtbot, monkeypatch):
    """A trial whose 3D step failed (sync passed the automatic check): its sync can be checked, not accepted as is;
    it then runs again from its synchronisation."""
    fake = _fake_run(monkeypatch, FakeStages(fail={"triangulation"}, fail_trials={"1"}))
    ses = _ready_project(tmp_path, 2)
    hub = _hub(win, tmp_path, ses)
    hub.run_button.click()
    _idle(qtbot, win)
    assert ses.run_state.trial_status == {"1": "failed", "2": "done"}
    _state, one, fix, accept = hub.cells["1"]
    assert "failed" in _status(hub, "1") and fix.isEnabled() and one.isEnabled() and not accept.isEnabled()
    assert "Failed in step 4 of 7 (Computing 3D positions): " in _status(hub, "1")
    _shot(win, "failed_trial")
    assert not hub.cells["2"][2].isEnabled()
    # the toolbar takes a failed trial only when it is selected (it may have failed for another reason)
    assert not hub.buttons["sync"].isEnabled() and not hub.buttons["accept"].isEnabled()
    _select(hub.table, [0])
    assert hub.buttons["sync"].isEnabled() and not hub.buttons["accept"].isEnabled()
    opened = []
    monkeypatch.setattr(runner, "sync_player", lambda _s, _r, label: opened.append(label) or True)
    fake.fail, fake.calls = set(), []
    fix.click()
    _idle(qtbot, win)
    assert opened == ["1"] and ses.run_state.trial_status["1"] == "done"
    assert ("1", "triangulation") in fake.calls and ("1", "poseEstimation") not in fake.calls
    assert ("1", "synchronization") not in fake.calls  # continues from the checked sync


def test_fix_sync_of_a_finished_trial(win, tmp_path, qtbot, monkeypatch):
    """A finished trial whose sync was accepted but wrong: Fix sync asks first, then runs its later steps again."""
    fake = _fake_run(monkeypatch, FakeStages(sync_log=SYNC_BAD))
    ses = _ready_project(tmp_path, 2)
    ses.settings.keep_keypoints = True  # the player needs the 2D keypoints
    save_session(ses, tmp_path)
    hub = _hub(win, tmp_path, ses)
    hub.run_button.click()
    _idle(qtbot, win)
    hub.buttons["accept"].click()  # both accepted as is
    _idle(qtbot, win)
    assert ses.run_state.trial_status == {"1": "done", "2": "done"}
    assert hub.cells["1"][2].isEnabled() and not hub.cells["1"][3].isEnabled()
    assert not hub.buttons["sync"].isEnabled()  # none selected: only trials that need a sync fix
    asked = []
    monkeypatch.setattr(run_bar, "confirm", lambda _w, title, text, *a: asked.append(text) or True)
    monkeypatch.setattr(runner, "sync_player", lambda _s, _r, label: True)
    fake.calls = []
    hub.cells["1"][2].click()
    _idle(qtbot, win)
    assert "Trial(s) 1 finished already" in asked[0]
    assert [s for lb, s in fake.calls if lb == "1"] == runner.STAGES[2:] and ses.run_state.trial_status["1"] == "done"


def test_check_sync_of_several_trials_in_the_background(win, tmp_path, qtbot, monkeypatch):
    """Several trials need a sync fix: their players open one after another, and each trial runs as soon as its
    player is closed, while the next player is still open (no waiting for one run before the next player)."""
    import threading

    _fake_run(monkeypatch, FakeStages(sync_log=SYNC_BAD))
    ses = _ready_project(tmp_path, 3)
    hub = _hub(win, tmp_path, ses)
    hub.run_button.click()
    _idle(qtbot, win)
    assert set(ses.run_state.trial_status.values()) == {"needs_sync_fix"}
    opened, release = [], threading.Event()

    def player(_ses, _root, label):  # trial 1's player is closed at once, trial 2's stays open until released
        opened.append(label)
        if label == "2":
            assert release.wait(60)
        return True

    monkeypatch.setattr(runner, "sync_player", player)
    _select(hub.table, [0, 1])
    assert hub.buttons["sync"].text() == "Fix sync (2) ..." and hub.buttons["accept"].isEnabled()
    hub.buttons["sync"].click()
    assert win.busy
    qtbot.waitUntil(lambda: ses.run_state.trial_status["1"] == "done", timeout=60000)
    assert opened == ["1", "2"] and win.busy  # trial 1 ran while trial 2's player is still open
    assert "sync player open" in _status(hub, "2")
    # trial 3 can still be handled meanwhile: accepted as is, it joins in the background
    assert hub.cells["3"][3].isEnabled() and not hub.cells["2"][2].isEnabled()
    hub.cells["3"][3].click()
    qtbot.waitUntil(lambda: ses.run_state.trial_status["3"] == "done", timeout=60000)
    assert win.busy
    release.set()
    _idle(qtbot, win)
    assert load_session(tmp_path).run_state.trial_status == {"1": "done", "2": "done", "3": "done"}
    assert "Finished" in hub.bar.stage.text() and not hub.buttons["sync"].isEnabled()


def test_stop_while_a_sync_player_is_open(win, tmp_path, qtbot, monkeypatch):
    """Stop drops the players not opened yet; the open one's trial keeps its checked sync for Accept as is."""
    import threading

    _fake_run(monkeypatch, FakeStages(sync_log=SYNC_BAD))
    ses = _ready_project(tmp_path, 2)
    hub = _hub(win, tmp_path, ses)
    hub.run_button.click()
    _idle(qtbot, win)
    opened, release = [], threading.Event()

    def player(_ses, _root, label):
        opened.append(label)
        assert release.wait(60)
        return True

    monkeypatch.setattr(runner, "sync_player", player)
    hub.table.clearSelection()
    assert hub.buttons["sync"].text() == "Fix sync (2) ..."  # none selected: every trial that needs it
    hub.buttons["sync"].click()
    qtbot.waitUntil(lambda: opened == ["1"], timeout=10000)
    assert hub.bar.stop_button.isEnabled()
    hub.bar.stop()
    assert "close the open synchronisation player" in hub.bar.stage.text()
    release.set()
    _idle(qtbot, win)
    assert opened == ["1"] and set(ses.run_state.trial_status.values()) == {"needs_sync_fix"}
    assert "Trial 1: its sync was fixed but it did not run" in hub.bar.stage.text()
    hub.cells["1"][3].click()  # Accept as is continues with the checked sync
    _idle(qtbot, win)
    assert ses.run_state.trial_status["1"] == "done"


def test_changing_a_setting_after_a_run_repeats_only_later_stages(win, tmp_path, qtbot, monkeypatch):
    fake = _fake_run(monkeypatch)
    shown = []
    monkeypatch.setattr(run_bar, "error_box", lambda _p, title, text, *a: shown.append((title, text)))
    ses = _ready_project(tmp_path, 1)
    hub = _hub(win, tmp_path, ses)
    hub.cells["1"][1].click()  # Run this trial
    _idle(qtbot, win)
    assert ses.run_state.trial_status["1"] == "done"

    workflow.set_settings(ses, tmp_path, cutoff_hz=15)  # what Session settings does; too high for 30 fps
    hub.changed()
    assert "out of date" in _status(hub, "1")
    hub.run_button.click()  # out of date: run again with nothing selected
    assert shown and shown[-1][0] == "Cannot run yet" and "below half the frame rate" in shown[-1][1]
    assert not win.busy
    workflow.set_settings(ses, tmp_path, cutoff_hz=8)
    hub.changed()
    fake.calls.clear()
    hub.run_button.click()
    _idle(qtbot, win)
    assert [s for _, s in fake.calls] == ["filtering", "markerAugmentation", "kinematics"]
    assert ses.run_state.trial_status["1"] == "done" and "done" in _status(hub, "1")

    # Start over (R19): every stage again, and the box unticks itself
    fake.calls.clear()
    hub.start_over.setChecked(True)
    _select(hub.table, [0])
    hub.run_button.click()
    _idle(qtbot, win)
    assert [s for _, s in fake.calls][0] == "poseEstimation" and not hub.start_over.isChecked()


def test_results_during_a_run(win, tmp_path, qtbot, monkeypatch):
    """While a trial runs, Session and Results can be opened (the other sections change what the run uses); Results
    shows trials as they finish and exports once the run is over."""
    _fake_run(monkeypatch)
    ses = _ready_project(tmp_path, 2)
    hub = _hub(win, tmp_path, ses)
    hub.run_button.click()
    assert win.busy
    assert win.reachable(4) and not win.reachable(1) and not win.reachable(2)
    win.go("participants")
    assert win.current_page() is hub
    win.go("results")
    res = win.section("results")
    assert win.current_page() is res and not res.export_button.isEnabled()
    _idle(qtbot, win)
    assert [r.status for r in res.results] == ["done", "done"] and res.export_button.isEnabled()
    assert res.list.count() == 2 and res.list.item(0).text().startswith("● Trial 1")
    assert "n/a" not in res.list.item(0).text()  # a traffic light, not grey
    assert win.reachable(1)


def test_actions_on_several_trials(win, tmp_path, qtbot, monkeypatch):
    """Duplicate, edit, remove selected trials; Run leaves out what is not ready."""
    _fake_run(monkeypatch)
    shown = []
    monkeypatch.setattr(session_mod, "error_box", lambda _p, title, text, *a: shown.append((title, text)))
    monkeypatch.setattr("mocapapp.gui.common.error_box", lambda _p, title, text, *a: shown.append((title, text)))
    monkeypatch.setattr(session_mod, "confirm", lambda *a: True)
    ses = _ready_project(tmp_path, 2)
    ses.trials[0].name = "walk"
    workflow.add_layout(ses, tmp_path, "Gym", ["cam01"])
    hub = _hub(win, tmp_path, ses)

    _select(hub.table, [0, 1])
    hub.buttons["duplicate"].click()
    assert [(t.label, t.participant, t.name) for t in ses.trials[2:]] == [("3", "1", "walk"), ("4", "2", "")]
    assert hub.selected() == ["3", "4"]  # the copies are selected
    assert "No video chosen" in _status(hub, "3")

    def gym(dlg):
        dlg.layout_box.setCurrentIndex(dlg.layout_box.findData("L2"))
        _save(dlg)

    _dialogs(monkeypatch, gym, gym)
    for row in (2, 3):
        _select(hub.table, [row])
        hub.buttons["edit"].click()
    assert [t.layout for t in ses.trials] == ["L1", "L1", "L2", "L2"]

    # Run on a selection: the ready trial runs, the other is left out and named in the confirmation
    asked = []
    monkeypatch.setattr(run_bar, "confirm", lambda _p, title, text, ok: asked.append(text) or True)
    _select(hub.table, [0, 2])
    hub.run_button.click()
    _idle(qtbot, win)
    assert ses.run_state.trial_status == {"1": "done"}
    assert "Not ready, left out" in asked[-1] and "Trial 3" in asked[-1]

    _select(hub.table, [2, 3])
    hub.buttons["remove"].click()  # no videos or results: removed after one confirmation
    assert [t.label for t in ses.trials] == ["1", "2"] and len(ses.participants) == 2  # participants stay
    _select(hub.table, [0])
    hub.run_button.click()  # done already: runs again, nothing left to do
    _idle(qtbot, win)
    hub.table.clearSelection()
    _select(hub.table, [])
    assert not shown, shown
    assert hub.buttons["forms"].isEnabled() and hub.buttons["add"].isEnabled()


def _shot(win, name):  # MOCAPAPP_SHOTS=<folder>: screenshots for the usability review
    import os

    folder = os.environ.get("MOCAPAPP_SHOTS")
    if folder:
        win.resize(1280, 860)
        win.grab().save(str(Path(folder) / f"{name}.png"))


def test_two_days(win, tmp_path, qtbot, monkeypatch):
    """P6 usability check (PROJECT.md §16.6): a new project used over two days. Day 1: cameras, a layout, one
    participant's two trials, run. Day 2, the project opened again: the cameras were set up again, so a new layout
    (Duplicate, new extrinsic videos); a second participant's trials, recorded that day, run without touching day 1."""
    import datetime as dt

    _fake_run(monkeypatch)
    win.section("session").trials_spin.setValue(2)
    raw, root, ses = calibrate_in_window(win, tmp_path, qtbot, monkeypatch, trials=2)
    win.go("session")
    hub = win.section("session")

    def walking(dlg):
        dlg.name.setText("walking")
        _save(dlg)

    _dialogs(monkeypatch, walking, _save)
    hub.buttons["add"].click()
    hub.buttons["add"].click()
    win.go("participants")
    people = win.section("participants")
    box = people.table.cellWidget(0, _col(people.table, "Consent"))
    box.setCurrentIndex(box.findData("keep"))
    height = people.table.cellWidget(0, _col(people.table, "Height"))
    height.setText("172")
    height.editingFinished.emit()
    win.go("session")
    _dialogs(monkeypatch, _use_suggested, _use_suggested)
    for label in ("1", "2"):
        _select(hub.table, [int(label) - 1])
        hub.buttons["edit"].click()
    hub.table.clearSelection()
    hub.buttons["check"].click()  # nothing selected: every trial whose videos are chosen
    _idle(qtbot, win)
    hub.run_button.click()
    _idle(qtbot, win)
    assert load_session(root).run_state.trial_status == {"1": "done", "2": "done"}
    _shot(win, "day1_hub")
    day1 = ses.layout("L1").date
    win.close()

    # Day 2: the SD cards are copied into the same camera folders; the cameras were moved since yesterday
    day2 = (dt.date.fromisoformat(day1) + dt.timedelta(days=1)).isoformat()
    stamp = f"{day2}T10:00:00Z"
    for i, cam in enumerate(CAMS, 1):
        for n in (3, 4):
            make_video(raw / cam / f"walk{n}.mp4", seconds=0.5 + 0.05 * i, size="1280x720", creation_time=stamp)
        make_video(tmp_path / "day2" / cam / "extrinsic.mp4", seconds=0.3 + 0.05 * i, size="1280x720",
                   pattern="smptebars", creation_time=stamp)
    win2 = MainWindow()
    qtbot.addWidget(win2)
    assert win2.open_session(root)
    ses = win2.session
    assert win2.current_page().key == "session" and win2.badges()["cameras"] == win2.badges()["layouts"]  # both done
    hub = win2.section("session")
    assert "done" in _status(hub, "1") and "done" in _status(hub, "2")

    # Layouts: duplicate yesterday's layout (cameras and points kept), new extrinsic videos, click again
    win2.go("layouts")
    lays = win2.section("layouts")
    _select(lays.table, [0])
    lays.buttons["duplicate"].click()
    assert [x.id for x in ses.layouts] == ["L1", "L2"] and lays.current == "L2" and ses.layout("L2").points
    for cam in CAMS:
        monkeypatch.setattr(layouts_mod, "choose_video", lambda *a, c=cam: tmp_path / "day2" / c / "extrinsic.mp4")
        lays.choose(cam)
    assert ses.layout("L2").date == day2  # the day its extrinsic videos were recorded
    for cam in CAMS:
        lays.click(cam)
        qtbot.waitUntil(lambda c=cam: service.position_result(root, "L2", c) is not None, timeout=120000)
        qtbot.waitUntil(lambda: not win2.busy, timeout=10000)
    assert service.layout_state(ses, root, "L2") == {"Hall (copy): cam01": "ready", "Hall (copy): cam02": "ready"}

    # Session: participant 1 has their two trials, so Add trial offers a new participant, with today's layout
    from mocapapp.gui.trial_dialog import NEW

    win2.go("session")

    def new_today(dlg):
        assert dlg.participant.currentData() == NEW and dlg.layout_box.currentData() == "L2"
        _save(dlg)

    _dialogs(monkeypatch, new_today, _save)
    hub.buttons["add"].click()
    hub.buttons["add"].click()
    assert [(t.label, t.participant, t.layout) for t in ses.trials[2:]] == [("3", "2", "L2"), ("4", "2", "L2")]
    win2.go("participants")
    people = win2.section("participants")
    box = people.table.cellWidget(1, _col(people.table, "Consent"))
    box.setCurrentIndex(box.findData("delete"))
    height = people.table.cellWidget(1, _col(people.table, "Height"))
    height.setText("165")
    height.editingFinished.emit()
    win2.go("session")
    _dialogs(monkeypatch, _use_suggested, _use_suggested)
    for label in ("3", "4"):
        _select(hub.table, [int(label) - 1])
        hub.buttons["edit"].click()
        assert ses.trial(label).date == day2 and not ses.layout_warning(label)

    # R8: a day-2 trial put on yesterday's layout warns; back on today's layout it does not
    def layouts(dlg):
        dlg.layout_box.setCurrentIndex(dlg.layout_box.findData("L1"))
        assert dlg.accept_layout.isVisibleTo(dlg) and "set up again" in dlg.warning.text()
        dlg.layout_box.setCurrentIndex(dlg.layout_box.findData("L2"))
        assert not dlg.accept_layout.isVisibleTo(dlg)
        dlg.show()
        _shot(dlg, "day2_trial_dialog")
        dlg.reject()

    _dialogs(monkeypatch, layouts)
    _select(hub.table, [3])
    hub.buttons["edit"].click()
    assert ses.trial("4").layout == "L2"

    hub.table.clearSelection()
    hub.buttons["check"].click()
    _idle(qtbot, win2)
    assert hub.selected() == ["3", "4"] and hub.run_button.text() == "Run selected (2)"  # what was checked
    fake = _fake_run(monkeypatch)
    hub.run_button.click()
    _idle(qtbot, win2)
    assert {label for label, _stage in fake.calls} == {"3", "4"}  # day 1 stays as it was
    assert load_session(root).run_state.trial_status == {"1": "done", "2": "done", "3": "done", "4": "done"}
    _shot(win2, "day2_hub")

    # Results: all four trials; export everything, then delete participant 2's raw footage (no storage consent)
    win2.go("results")
    res = win2.section("results")
    assert res.list.count() == 4 and all(r.status == "done" for r in res.results)
    res.tabs.setCurrentIndex(3)
    res.do_export()
    _idle(qtbot, win2)
    assert res.delete_now.isEnabled() and "to be deleted now" in res.deletion.text()
    _shot(win2, "day2_results")
    res.tabs.setCurrentIndex(1)
    assert res.viewer.trial_box.count() == 4


def test_participant_edits_do_not_rebuild_other_sections(win, tmp_path, qtbot, monkeypatch):
    """A height typed in Participants saved straight away, without the Results section remaking the quality report
    (that made every edit wait seconds); Results is up to date when it is opened."""
    ses = _ready_project(tmp_path / "P", 2)
    win.set_session(tmp_path / "P", ses)
    win.go("participants")
    part, res = win.section("participants"), win.section("results")
    calls = []
    monkeypatch.setattr(res, "refresh", lambda: calls.append(1))
    height = part.cells[(part.rows[0], "Height")]
    assert height.placeholderText() == ""
    height.setText("1.81")
    height.editingFinished.emit()
    qtbot.waitUntil(lambda: part.cells[(part.rows[0], "Height")] is not height)  # the table was rebuilt
    assert ses.participant(part.rows[0]).height_m == pytest.approx(1.81) and calls == []
    win.go("results")
    assert calls == [1]


def test_sidebar_width_can_be_dragged_and_is_remembered(win, qtbot):
    from mocapapp.gui.common import settings
    from mocapapp.gui.main_window import SIDEBAR_MIN, MainWindow

    win.show()
    win.splitter.moveSplitter(320, 1)
    assert win.sidebar.width() > 300 and settings().value("sidebar_width", type=int) == win.sidebar.width()
    other = MainWindow()
    qtbot.addWidget(other)
    other.show()
    assert abs(other.sidebar.width() - win.sidebar.width()) <= 2
    win.splitter.moveSplitter(10, 1)
    assert win.sidebar.width() >= SIDEBAR_MIN


def test_editing_a_trial_that_ran_deletes_its_results_after_asking(win, tmp_path, qtbot, monkeypatch):
    """Edit: a new name keeps the results; a new layout deletes them, after a warning and a confirmation (no to
    either: nothing changes), and the trial runs again from the start."""
    from mocapapp.gui import trial_dialog

    _fake_run(monkeypatch)
    ses = _ready_project(tmp_path, 1)
    workflow.add_layout(ses, tmp_path, "Gym", ["cam01"])
    hub = _hub(win, tmp_path, ses)
    hub.run_button.click()
    _idle(qtbot, win)
    tdir = Layout(tmp_path).trial("1")
    assert ses.run_state.trial_status == {"1": "done"} and any((tdir / "pose-3d").glob("*.trc"))
    asked = []
    answers = []
    monkeypatch.setattr(trial_dialog, "confirm", lambda _p, title, text, ok: asked.append(title) or answers.pop(0))

    def rename(dlg):
        dlg.name.setText("jumping")
        _save(dlg)

    def to_gym(dlg):
        dlg.layout_box.setCurrentIndex(dlg.layout_box.findData("L2"))
        _save(dlg)

    _dialogs(monkeypatch, rename, to_gym, to_gym, to_gym)
    _select(hub.table, [0])
    hub.buttons["edit"].click()
    assert ses.trial("1").name == "jumping" and asked == [] and ses.run_state.trial_status == {"1": "done"}
    for no in ([False], [True, False]):  # the warning, then the confirmation: either can say no
        answers[:] = no
        hub.buttons["edit"].click()
        assert ses.trial("1").layout == "L1" and ses.run_state.trial_status == {"1": "done"}
    answers[:] = [True, True]
    asked.clear()
    hub.buttons["edit"].click()
    assert asked == ["The trial's results will be deleted", "Delete the results?"]
    assert ses.trial("1").layout == "L2" and "1" not in ses.run_state.trial_status
    assert not (tdir / "pose-3d").exists() and not (tdir / "kinematics").exists()
    assert load_session(tmp_path).trial("1").layout == "L2"
    assert readiness.trial_status(ses, tmp_path, "1") in ("ready", "not ready")
