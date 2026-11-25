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


def _status(hub, label):
    return hub.cells[label][0].text()


def test_full_project_on_the_hub(win, tmp_path, qtbot, monkeypatch):
    _fake_run(monkeypatch)
    win.section("session").trials_spin.setValue(2)  # two trials per participant
    raw, root, ses = calibrate_in_window(win, tmp_path, qtbot, monkeypatch, trials=3)

    # Session: the add row, pre-filled (R10): a new participant first, then theirs until they have two trials
    win.go("session")
    hub = win.section("session")
    assert hub.add_who.currentData() is None and hub.add_layout.currentData() == "L1"
    assert "font-weight: bold" in hub.add_who.styleSheet()  # the participant field is highlighted
    hub.add_name.setText("walking")
    hub.add_button.click()
    assert [(t.label, t.participant, t.layout, t.name) for t in ses.trials] == [("1", "1", "L1", "walking")]
    assert hub.selected() == ["1"] and hub.panel.isVisibleTo(hub) and hub.panel.label == "1"
    assert hub.add_who.currentData() == "1" and hub.add_name.text() == "walking"
    hub.add_button.click()
    assert hub.add_who.currentData() is None  # participant 1 has their two trials
    hub.add_button.click()
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

    # Session: the trial panel suggests each trial's videos by number; accept them, then check and copy
    win.go("session")
    for label in ("1", "2", "3"):
        _select(hub.table, [int(label) - 1])
        assert hub.panel.label == label and hub.panel.use_suggested.isVisibleTo(hub.panel)
        assert f"suggested: walk{label}.mp4" in hub.panel.videos.item(0, 1).text()
        hub.panel.use_suggested.click()
        assert Path(ses.trial(label).videos["cam01"]).name == f"walk{label}.mp4"
    assert "not checked and copied" in _status(hub, "1")
    _select(hub.table, [0, 1])
    assert not hub.panel.isVisibleTo(hub)  # two trials selected: no panel
    assert hub.buttons["check"].text() == "Check and copy videos (2)" and hub.run_button.text() == "Run selected (2)"
    hub.buttons["check"].click()
    assert win.busy
    _idle(qtbot, win)
    assert all(workflow.trial_inputs_ready(ses, root, lb) for lb in ("1", "2"))
    assert not workflow.trial_inputs_ready(ses, root, "3") and hub.selected() == ["1", "2"]
    assert readiness.trial_status(ses, root, "1") == "ready" and "ready" in _status(hub, "2")
    _select(hub.table, [0])
    copied = hub.panel.copy.text()
    assert "Checked and copied: the trial's 2 video(s)" in copied and " free." in copied

    # R8 in the panel: a trial recorded on another day than its layout
    _select(hub.table, [2])
    hub.panel.date.setText("2020-01-01")
    hub.panel.date.editingFinished.emit()
    assert ses.trial("3").date == "2020-01-01" and hub.panel.accept_button.isVisibleTo(hub.panel)
    assert "recorded on 2020-01-01" in hub.panel.warning.text()
    hub.panel.accept_button.click()
    assert ses.trial("3").layout_override and not hub.panel.accept_button.isVisibleTo(hub.panel)

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
    assert not one.isEnabled()  # Check sync or Accept as is, not a plain re-run

    # while the sync player is open, the trial says so and no other player or run can start
    started, background = [], run_bar.Background
    monkeypatch.setattr(run_bar, "Background", lambda *a, **k: started.append(a[1]))
    fix.click()
    assert started == [runner.fix_sync]
    assert "running" in _status(hub, "1") and "sync player open" in _status(hub, "1")
    assert not any(b.isEnabled() for b in hub.cells["2"][1:]) and not hub.run_button.isEnabled()
    hub.bar._finished(None)
    assert "needs sync fix" in _status(hub, "1") and hub.cells["1"][2].isEnabled()
    monkeypatch.setattr(run_bar, "Background", background)

    hub.cells["1"][3].click()  # Accept as is
    _idle(qtbot, win)
    assert ses.run_state.trial_status["1"] == "done" and "done" in _status(hub, "1")


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
    """Duplicate, change layout, remove for several selected trials; Run leaves out what is not ready."""
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

    monkeypatch.setattr(session_mod, "choose_layout", lambda *a: "L2")
    hub.buttons["layout"].click()
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
    assert hub.buttons["forms"].isEnabled() and hub.add_who.currentData() is None


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
    hub.add_name.setText("walking")
    hub.add_button.click()
    hub.add_button.click()
    win.go("participants")
    people = win.section("participants")
    box = people.table.cellWidget(0, _col(people.table, "Consent"))
    box.setCurrentIndex(box.findData("keep"))
    height = people.table.cellWidget(0, _col(people.table, "Height"))
    height.setText("172")
    height.editingFinished.emit()
    win.go("session")
    for label in ("1", "2"):
        _select(hub.table, [int(label) - 1])
        hub.panel.use_suggested.click()
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

    # Session: participant 1 has their two trials, so the add row offers a new participant, with today's layout
    win2.go("session")
    assert hub.add_who.currentData() is None and hub.add_layout.currentData() == "L2"
    hub.add_button.click()
    hub.add_button.click()
    assert [(t.label, t.participant, t.layout) for t in ses.trials[2:]] == [("3", "2", "L2"), ("4", "2", "L2")]
    win2.go("participants")
    people = win2.section("participants")
    box = people.table.cellWidget(1, _col(people.table, "Consent"))
    box.setCurrentIndex(box.findData("delete"))
    height = people.table.cellWidget(1, _col(people.table, "Height"))
    height.setText("165")
    height.editingFinished.emit()
    win2.go("session")
    for label in ("3", "4"):
        _select(hub.table, [int(label) - 1])
        hub.panel.use_suggested.click()
        assert ses.trial(label).date == day2 and not hub.panel.accept_button.isVisibleTo(hub.panel)

    # R8: a day-2 trial put on yesterday's layout warns; back on today's layout it does not
    _select(hub.table, [3])
    monkeypatch.setattr(session_mod, "choose_layout", lambda *a: "L1")
    hub.buttons["layout"].click()
    assert hub.panel.accept_button.isVisibleTo(hub.panel) and "set up again" in hub.panel.warning.text()
    assert "not ready" in _status(hub, "4")
    monkeypatch.setattr(session_mod, "choose_layout", lambda *a: "L2")
    hub.buttons["layout"].click()
    assert not hub.panel.accept_button.isVisibleTo(hub.panel)
    _shot(win2, "day2_panel")

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
