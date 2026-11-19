"""Participant details per trial, adding and deleting trials, running one trial at a time (0.1.0rc13)."""

import json

import pytest
import tomlkit

from mocapapp.cli.main import main
from mocapapp.core import deletion, runner
from mocapapp.core.assemble import assemble_trials
from mocapapp.core.config_gen import build_config
from mocapapp.core.export import default_selection, export
from mocapapp.core.session import Layout, load_session, save_session
from mocapapp.core.validate import validate
from mocapapp.core.workflow import (
    SetupError,
    add_detail,
    add_trial,
    detail_value,
    remove_detail,
    remove_empty_trial,
    set_detail_enabled,
    set_mapping,
    set_trial_detail,
    set_trial_participants,
)
from tests.m3_fakes import FakeStages, make_session
from tests.test_deletion import _finished


@pytest.fixture
def fake(monkeypatch):
    f = FakeStages()
    monkeypatch.setattr(runner, "run_stage", f)
    monkeypatch.setattr(runner, "_fps", lambda *a: 30.0)
    return f


# ---------- participant details ----------

def test_details_asked_per_trial(tmp_path):
    session = make_session(tmp_path)
    assert [d.key for d in session.details] == ["height", "weight", "handedness", "age", "sex"]
    assert [d.key for d in session.asked_details] == ["height"]  # only height by default
    set_detail_enabled(session, tmp_path, "weight", True)
    leg = add_detail(session, tmp_path, "Leg length", "number", "cm")
    side = add_detail(session, tmp_path, "Dominant leg", "choice", choices=["left", "right"])
    assert (leg.key, side.key) == ("leg_length", "dominant_leg")
    assert [d.key for d in load_session(tmp_path).asked_details] == ["height", "weight", "leg_length",
                                                                       "dominant_leg"]
    with pytest.raises(SetupError, match="already"):
        add_detail(session, tmp_path, "leg length")
    with pytest.raises(SetupError, match="two different options"):
        add_detail(session, tmp_path, "Side", "choice", choices=["left"])
    with pytest.raises(SetupError, match="built in"):
        remove_detail(session, tmp_path, "weight")

    assert detail_value(session.detail("height"), "180 cm") == "1.8"
    assert detail_value(session.detail("weight"), "70,5") == "70.5"
    assert detail_value(session.detail("handedness"), "LEFT") == "left"
    assert detail_value(session.detail("leg_length"), "92 cm") == "92"
    for key, bad in (("weight", "heavy"), ("weight", "-3"), ("handedness", "up")):
        with pytest.raises(SetupError):
            detail_value(session.detail(key), bad)

    set_trial_detail(session, tmp_path, "1", "leg_length", "92")
    remove_detail(session, tmp_path, "leg_length")
    assert "leg_length" not in load_session(tmp_path).trial("1").details


def test_details_follow_the_participant(tmp_path):
    session = make_session(tmp_path)
    set_detail_enabled(session, tmp_path, "weight", True)
    set_trial_detail(session, tmp_path, "1", "weight", "70")
    later = add_trial(session, tmp_path, "1")
    assert later.label == "3" and later.details == {"weight": "70"}  # copied from the first trial

    set_trial_detail(session, tmp_path, "1", "height", "1.82")  # filled into their later trial while still empty
    set_trial_detail(session, tmp_path, "3", "weight", "71")  # a later trial can differ
    s = load_session(tmp_path)
    assert s.trial("3").details == {"weight": "71", "height": "1.82"} and s.trial("1").details["weight"] == "70"
    assert s.trial("2").details == {}  # another participant
    height = tomlkit.parse(build_config(s, s.trial("3")))["project"]["participant_height"]
    assert height == pytest.approx(1.82)
    assert s.trial("2").height_m == 1.75  # no trial height: the participant's (step 1)

    set_trial_participants(session, {"2": "1"})  # trial 2 is participant 1's after all: their details
    assert session.trial("2").details == session.trial("1").details


# ---------- adding, removing and deleting trials ----------

def test_add_and_remove_trials(tmp_path):
    session = make_session(tmp_path)
    trial = add_trial(session, tmp_path, "2")
    assert (trial.label, trial.participant, trial.mapping_method) == ("3", "2", "manual")
    with pytest.raises(SetupError, match="already has videos or results"):
        remove_empty_trial(session, tmp_path, "1")  # make_session built its trial folder
    remove_empty_trial(session, tmp_path, "3")
    assert [t.label for t in load_session(tmp_path).trials] == ["1", "2"]
    session.trials[0].raw_deleted_at = "2026-11-19T10:00:00"
    with pytest.raises(SetupError, match="deleted"):
        add_trial(session, tmp_path, "2")


def test_delete_one_trial_with_its_data(tmp_path, monkeypatch):
    root = tmp_path / "S"
    session = _finished(root, monkeypatch)
    layout = Layout(root)
    exported = sorted(p.name for p in layout.export.glob("*_trial1_*"))
    assert exported and (layout.export / "trials.csv").is_file()

    plan = deletion.plan_trial(session, root, "1")
    paths = {i.path for i in plan.items}
    assert layout.trial("1") in paths and layout.inputs("cam01") / "trial_1.mp4" in paths
    assert not any("trial2" in p.name or p == layout.trial("2") for p in paths)
    assert plan.confirm_text == session.trial("1").code and not plan.blockers
    with pytest.raises(deletion.DeletionError, match="type the pseudonym"):
        deletion.execute(plan, session, "wrong")
    deletion.execute(plan, session, session.trial("1").code)

    s = load_session(root)
    assert [t.label for t in s.trials] == ["2"] and "1" not in s.run_state.trial_status
    assert not layout.trial("1").exists() and layout.trial("2").is_dir()
    assert not list(layout.export.glob("*_trial1_*")) and list(layout.export.glob("*_trial2_*"))
    manifest = json.loads((layout.export / "manifest.json").read_text(encoding="utf-8"))
    assert {f["trial"] for f in manifest["files"]} == {"2"} and [t["trial"] for t in manifest["trials"]] == ["2"]
    assert (layout.export / "trials.csv").read_text(encoding="utf-8").splitlines()[1:] == [
        f"2,{s.trial('2').code},1.75"]  # the participant's height when no trial height was entered
    assert deletion.deletion_log(root)[-1]["scope"] == "trial"
    only = deletion.plan_trial(s, root, "2")
    assert only.blockers  # the last trial: delete the session instead


# ---------- saved and reopened between trials ----------

def test_trials_run_one_at_a_time_across_reopening(tmp_path, fake):
    session = make_session(tmp_path)
    assert runner.run_session(session, tmp_path, ["1"]) == {"1": "done"}
    reopened = load_session(tmp_path)  # the app closed and opened again
    assert reopened.run_state.trial_status == {"1": "done"}
    fake.calls.clear()
    assert runner.run_session(reopened, tmp_path, ["2"]) == {"2": "done"}
    assert {label for label, _ in fake.calls} == {"2"}  # trial 1 is not touched

    later = add_trial(reopened, tmp_path, "1")  # recorded on another day
    layout = Layout(tmp_path)
    for cam in reopened.cameras:
        layout.inputs(cam.name).mkdir(parents=True, exist_ok=True)
        (layout.inputs(cam.name) / f"trial_{later.label}.mp4").write_bytes(b"video")
    # finished trials without copies (removed to save space, or never made in this fake) are left as they are
    assert assemble_trials(reopened, tmp_path, progress=lambda m: None) == [layout.trial(later.label)]
    assert assemble_trials(reopened, tmp_path, progress=lambda m: None, labels=[later.label]) == [
        layout.trial(later.label)]
    fake.calls.clear()
    assert runner.run_session(reopened, tmp_path, [later.label]) == {later.label: "done"}
    assert {label for label, _ in fake.calls} == {"3"}
    assert load_session(tmp_path).run_state.trial_status == {"1": "done", "2": "done", "3": "done"}


def _camera_folders(root, labels):
    for cam in ("cam01", "cam02"):
        d = root / "raw" / cam
        d.mkdir(parents=True, exist_ok=True)
        for name in ["intrinsic", "extrinsic"] + [f"trial{lb}" for lb in labels]:
            (d / f"{name}.mp4").write_bytes(f"{cam} {name}".encode())


def test_processed_trials_keep_their_labels(tmp_path):
    session = make_session(tmp_path)
    for cam in session.cameras:
        cam.source_dir = str(tmp_path / "raw" / cam.name)
    _camera_folders(tmp_path, ["1", "2"])
    report = validate(session, do_probe=False)
    assert report.ok and report.mapping_method == "digit"
    session.run_state.trial_status["1"] = "done"
    session.run_state.stage_done["1"] = list(runner.STAGES)

    new = add_trial(session, tmp_path, "1")
    report = validate(session, do_probe=False)
    assert [t.label for t in session.trials] == ["1", "2", "3"]
    assert {i.where for i in report.issues if i.level == "action"} == {"cam01 trial 3", "cam02 trial 3"}

    _camera_folders(tmp_path, ["3"])
    set_mapping(session, {(c.name, new.label): tmp_path / "raw" / c.name / "trial3.mp4" for c in session.cameras})
    (tmp_path / "raw" / "cam01" / "trial1.mp4").unlink()  # the original of the finished trial was removed later
    report = validate(session, do_probe=False)
    assert report.ok, report.issues
    assert any(i.level == "info" and "trial 1" in i.where for i in report.issues)
    assert session.camera("cam01").trial_files["1"].endswith("trial1.mp4")  # never renumbered


# ---------- CLI ----------

def test_cli_details_and_trials(tmp_path, capsys, monkeypatch):
    root = tmp_path / "S"
    _finished(root, monkeypatch)
    s = str(root)
    assert main(["session", "details", "--session", s, "--enable", "weight",
                 "--add", "Dominant leg:choice:left/right", "--add", "Leg length:number:cm"]) == 0
    out = capsys.readouterr().out
    assert "[x] weight" in out and "[x] dominant_leg: Dominant leg - choice: left / right (custom)" in out
    assert main(["session", "trial-details", "--session", s, "--trial", "1", "--set", "weight=70",
                 "--set", "dominant_leg=Left"]) == 0
    out = capsys.readouterr().out
    assert "Weight: 70 kg" in out and "Dominant leg: left" in out and "Height: 1.75 (participant's height)" in out
    assert main(["session", "trial-details", "--session", s, "--trial", "1", "--set", "weight=lots"]) == 1

    assert main(["session", "add-trial", "--session", s, "--participant", "1"]) == 0
    assert "Trial 3 added" in capsys.readouterr().out
    assert load_session(root).trial("3").details == {"weight": "70", "dominant_leg": "left"}
    assert main(["session", "remove-trial", "--session", s, "--trial", "3"]) == 0
    assert main(["session", "remove-trial", "--session", s, "--trial", "2"]) == 1
    assert "mocapapp delete-trial" in capsys.readouterr().out

    assert main(["delete-trial", "--session", s, "--trial", "2"]) == 0  # dry run
    assert "Nothing was deleted" in capsys.readouterr().out and Layout(root).trial("2").is_dir()
    code = load_session(root).trial("2").code
    assert main(["delete-trial", "--session", s, "--trial", "2", "--yes", "--confirm", code]) == 0
    assert [t.label for t in load_session(root).trials] == ["1"]
    export(load_session(root), root, default_selection(), None, {})
    rows = (Layout(root).export / "trials.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0] == "trial,pseudonym,Height (m),Weight (kg),Dominant leg,Leg length (cm)"
    assert rows[1:] == [f"1,{load_session(root).trial('1').code},1.75,70,left,"]


def test_export_of_one_trial_keeps_the_others(tmp_path, monkeypatch):
    root = tmp_path / "S"
    session = _finished(root, monkeypatch)  # exported both trials
    export(session, root, default_selection(), ["2"], {})  # trial 2 run again and exported alone
    manifest = json.loads((Layout(root).export / "manifest.json").read_text(encoding="utf-8"))
    assert {f["trial"] for f in manifest["files"]} == {"1", "2"}
    save_session(session, root)


# ---------- GUI ----------

def test_gui_details_and_trials(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QLineEdit

    from mocapapp.gui.main_window import MainWindow

    session = make_session(tmp_path)
    save_session(session, tmp_path)
    win = MainWindow()
    qtbot.addWidget(win)
    win.set_session(tmp_path, session)
    setup = win.pages[0]
    assert set(setup.detail_boxes) == {"height", "weight", "handedness", "age", "sex"}
    setup.detail_boxes["weight"].setChecked(True)
    assert [d.key for d in load_session(tmp_path).asked_details] == ["height", "weight"]

    videos = win.pages[1]
    monkeypatch.setattr(videos, "check", lambda: None)
    videos.refresh()
    headers = [videos.grid.horizontalHeaderItem(c).text() for c in range(videos.grid.columnCount())]
    assert headers == ["Height (m)", "Weight (kg)", "cam01", "cam02"]
    cell = videos.grid.cellWidget(0, 1)
    assert isinstance(cell, QLineEdit)
    cell.setText("70,5")
    cell.editingFinished.emit()
    assert load_session(tmp_path).trial("1").details == {"weight": "70.5"} and cell.text() == "70.5"
    assert videos.grid.cellWidget(0, 0).placeholderText() == "1.75"

    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: (a[3][0], True))
    videos.add_trial()
    assert [t.label for t in win.session.trials] == ["1", "2", "3"]
    assert win.session.trial("3").details == {"weight": "70.5"}
    videos = win.pages[1]
    assert videos.grid.horizontalHeaderItem(0).text() == "Participant"  # two trials of participant 1 now

    win.go(5)
    run = win.pages[5]
    assert run.trials.cellWidget(0, 5).text() == "Run this trial"


def test_gui_camera_position_rows(qtbot, tmp_path):
    from mocapapp.core.session import CameraPosition
    from mocapapp.gui.main_window import MainWindow
    from mocapapp.gui.pages.calibration import ExtrinsicsPage, PositionDialog

    session = make_session(tmp_path)
    session.camera("cam01").positions.append(CameraPosition(key="2", file=str(tmp_path / "moved.mp4"),
                                                            trials=["2"]))
    save_session(session, tmp_path)
    win = MainWindow()
    qtbot.addWidget(win)
    win.set_session(tmp_path, session)
    page = next(p for p in win.pages if isinstance(p, ExtrinsicsPage))
    page.refresh()
    assert page.table.rowCount() == 3 and "moved" in page.table.item(1, 0).text()
    assert page.moved_button.isVisibleTo(page) and page.moved_button.isFlat()
    dlg = PositionDialog(page, session, "cam01", ["2"])
    assert dlg.values() == ("cam01", "", ["2"])
