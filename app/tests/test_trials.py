"""Participant details per trial, adding and deleting trials, running one trial at a time (0.1.0rc13)."""

import json
from pathlib import Path

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

    assert detail_value(session.detail("height"), "180 cm") == "1.80"  # two decimals at least (0.1.0rc17)
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
    assert add_trial(session, tmp_path, "2").label == "3"  # R20: a deletion locks only its own trial


def test_delete_one_trial_with_its_data(tmp_path, monkeypatch):
    root = tmp_path / "S"
    session = _finished(root, monkeypatch)
    layout = Layout(root)
    exported = sorted(p.name for p in layout.export.glob("*_trial1_*"))
    assert exported and (layout.export / "trials.csv").is_file()

    plan = deletion.plan_trial(session, root, "1")
    paths = {i.path for i in plan.items}
    assert layout.trial("1") in paths and layout.trial_input("1", "cam01") in paths
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
        layout.trial_input(later.label, cam.id).parent.mkdir(parents=True, exist_ok=True)
        layout.trial_input(later.label, cam.id).write_bytes(b"video")
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
        cam.source_dir = str(tmp_path / "raw" / cam.id)
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
    set_mapping(session, {(c.id, new.label): tmp_path / "raw" / c.id / "trial3.mp4" for c in session.cameras})
    (tmp_path / "raw" / "cam01" / "trial1.mp4").unlink()  # the original of the finished trial was removed later
    report = validate(session, do_probe=False)
    assert report.ok, report.issues
    assert any(i.level == "info" and "trial 1" in i.where for i in report.issues)
    assert session.trial("1").videos["cam01"].endswith("trial1.mp4")  # never renumbered


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

def _wheel(widget) -> None:
    """Scroll the mouse wheel over a widget (one notch down)."""
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication

    pos = QPointF(5, 5)
    event = QWheelEvent(pos, QPointF(widget.mapToGlobal(QPoint(5, 5))), QPoint(0, 0), QPoint(0, -120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


def test_gui_trials_table(qtbot, tmp_path, monkeypatch):
    """0.1.0rc17: trials and participants in one table; columns chosen in a dialog; no change by mouse wheel."""
    from PySide6.QtWidgets import QLineEdit

    from mocapapp.gui.columns_dialog import ColumnsDialog
    from mocapapp.gui.common import NoWheelCombo
    from mocapapp.gui.main_window import MainWindow

    session = make_session(tmp_path)
    save_session(session, tmp_path)
    win = MainWindow()
    qtbot.addWidget(win)
    win.set_session(tmp_path, session)
    assert not hasattr(win.pages[0], "people") and not hasattr(win.pages[0], "detail_boxes")
    videos = win.pages[1]
    monkeypatch.setattr(videos, "check", lambda: None)
    import mocapapp.gui.pages.videos as videos_module

    asked = []
    monkeypatch.setattr(videos_module, "confirm", lambda _p, _t, text, _ok: asked.append(text) or True)

    def no_error(_parent, title, text, *_a):
        raise AssertionError(f"{title}: {text}")

    monkeypatch.setattr(videos_module, "error_box", no_error)
    videos.refresh()

    dlg = ColumnsDialog(videos, session, tmp_path)
    qtbot.addWidget(dlg)
    assert set(dlg.boxes) == {"weight", "handedness", "age", "sex"}  # height is always a column
    assert dlg.boxes["sex"].text() == "Gender"
    dlg.boxes["weight"].setChecked(True)
    assert [d.key for d in load_session(tmp_path).asked_details] == ["height", "weight"]
    videos._fill_people()
    table = videos.table
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    assert headers[:3] == ["Participant ID", "Height (m)", "Weight (kg)"] and "Consent\nform" in headers
    assert table.verticalHeaderItem(0).text() == "Trial 1"
    cell = table.cellWidget(0, 2)
    assert isinstance(cell, QLineEdit)
    cell.setText("70,5")
    cell.editingFinished.emit()
    assert load_session(tmp_path).trial("1").details == {"weight": "70.5"} and cell.text() == "70.5"
    height = table.cellWidget(0, 1)
    assert height.placeholderText() == "1.75"  # the participant's height, two decimals
    height.setText("1.7")
    height.editingFinished.emit()
    assert height.text() == "1.70" and load_session(tmp_path).trial("1").details["height"] == "1.70"

    # a new trial gets a new participant with a random pseudonym
    videos.add_trial()
    videos = win.pages[1]
    s = win.session
    assert [t.label for t in s.trials] == ["1", "2", "3"] and len(s.participants) == 3
    new = s.trial("3")
    assert new.participant == "3" and new.code not in ("T1-ABCDEF1", "T1-ABCDEF2") and new.details == {}
    box = videos.table.cellWidget(2, 0)
    assert isinstance(box, NoWheelCombo) and box.isEditable() and box.currentText() == new.code

    # the arrow: the trial belongs to participant 1 after all; the participant made for it is gone
    box.activated.emit(box.findData("1"))
    qtbot.wait(10)
    s = win.session
    assert s.trial("3").participant == "1" and [p.label for p in s.participants] == ["1", "2"]
    assert s.trial("3").details == {"weight": "70.5", "height": "1.70"}  # copied from their first trial
    assert load_session(tmp_path).trial("3").participant == "1"

    # typing: a new pseudonym renames the participant (both of their trials)
    box = videos.table.cellWidget(0, 0)
    box.lineEdit().setText("Q07")
    box.lineEdit().editingFinished.emit()
    qtbot.wait(10)
    assert win.session.trial("1").code == win.session.trial("3").code == "Q07"
    assert "signed consent form" in asked[0]  # consent was received: the change was confirmed first
    assert videos.table.cellWidget(2, 0).currentText() == "Q07"

    # consent ticked in one row shows in the participant's other rows
    first = 2 + len([d for d in win.session.asked_details if d.key != "height"])
    videos.table.cellWidget(0, first).setChecked(False)
    qtbot.wait(10)
    assert not videos.table.cellWidget(2, first).isChecked()

    # the mouse wheel never changes a drop-down in the tables
    box = videos.table.cellWidget(1, 0)
    before = box.currentIndex()
    _wheel(box)
    assert box.currentIndex() == before and win.session.trial("2").participant == "2"
    assert all(isinstance(videos.grid.cellWidget(0, c), NoWheelCombo) for c in range(videos.grid.columnCount()))

    # the Run step: tick trials to run only those
    win.go(4)
    run = win.pages[4]
    assert run.trials.horizontalHeaderItem(1).text() == "Trial" and run.trials.verticalHeader().isHidden()
    assert run.trials.cellWidget(0, 6).text() == "Run this trial" and run.run_button.text() == "Run all"
    from PySide6.QtWidgets import QCheckBox

    tick = run.trials.cellWidget(1, 0).findChild(QCheckBox)
    tick.setChecked(True)
    assert run.run_button.text() == "Run selected" and run.selected_labels() == ["2"]
    started = []
    monkeypatch.setattr(run, "start", lambda labels=None, accept_sync=False: started.append(labels))
    run.run_clicked()
    assert started == [["2"]]
    for r in (0, 2):
        run.trials.cellWidget(r, 0).findChild(QCheckBox).setChecked(True)
    assert run.run_button.text() == "Run all" and run.selected_labels() == []


def test_trial_videos_suggested_by_name(qtbot, tmp_path, monkeypatch):
    """0.1.0rc17: trial videos named 01, trial02 are filled in the mapping table, saved only with the button."""
    from mocapapp.gui.main_window import MainWindow

    session = make_session(tmp_path)
    for cam in session.cameras:
        cam.source_dir = str(tmp_path / cam.name)
        Path(cam.source_dir).mkdir()
        for name in ("01.mp4", "trial02.mp4", "intrinsic.mp4", "extrinsic.mp4"):
            (Path(cam.source_dir) / name).write_bytes(b"x")
    save_session(session, tmp_path)
    win = MainWindow()
    qtbot.addWidget(win)
    win.set_session(tmp_path, session)
    videos = win.pages[1]
    videos.refresh()
    grid = videos.grid
    assert [grid.cellWidget(r, 0).currentText() for r in range(2)] == ["01.mp4", "trial02.mp4"]
    assert "Save this mapping" in videos.mapping_note.text() and not videos.mapping_note.isHidden()
    assert load_session(tmp_path).camera("cam01").trial_files == {}  # not saved until the button
    assert load_session(tmp_path).camera("cam01").intrinsic.file.endswith("intrinsic.mp4")  # calibration: saved
    checked = []
    monkeypatch.setattr(videos, "check", lambda: checked.append(True))
    videos.save_mapping()
    files = load_session(tmp_path).camera("cam02").trial_files
    assert checked and Path(files["1"]).name == "01.mp4" and Path(files["2"]).name == "trial02.mp4"


def test_gui_calibration_videos(qtbot, tmp_path, monkeypatch):
    """0.1.0rc17: several calibration videos per camera, assigned per trial in a dialog."""
    from mocapapp.core.session import CameraPosition, VideoRef
    from mocapapp.gui.calib_dialog import MULTIPLE_TIP, CalibDialog
    from mocapapp.gui.main_window import MainWindow
    from mocapapp.gui.pages.calibration import ExtrinsicsPage, IntrinsicsPage

    session = make_session(tmp_path)
    for name in ("extrinsic.mp4", "moved.mp4", "intrinsic.mp4"):
        (tmp_path / name).write_bytes(b"x")
    cam = session.camera("cam01")
    cam.extrinsic = VideoRef(file=str(tmp_path / "extrinsic.mp4"))
    cam.positions.append(CameraPosition(key="2", file=str(tmp_path / "moved.mp4"), trials=["2"]))
    save_session(session, tmp_path)
    win = MainWindow()
    qtbot.addWidget(win)
    win.set_session(tmp_path, session)
    assert [p.title for p in win.pages[2:4]] == ["Intrinsic calibration", "Extrinsic calibration"]
    page = next(p for p in win.pages if isinstance(p, ExtrinsicsPage))
    page.refresh()
    assert page.table.rowCount() == 3 and "moved.mp4" in page.table.item(1, 0).text()
    assert "trials 2" in page.table.item(1, 0).text() and not hasattr(page, "moved_button")
    lens = next(p for p in win.pages if isinstance(p, IntrinsicsPage))
    assert lens.choose.text() == "Change intrinsic video(s) ..."
    assert not [b for b in lens.findChildren(type(lens.choose)) if "PDF" in b.text()]

    videos = win.pages[1]
    monkeypatch.setattr(videos, "check", lambda: None)
    videos.refresh()
    warn = videos.warn_buttons[("cam01", "extrinsic")]
    assert warn.toolTip() == MULTIPLE_TIP and ("cam01", "intrinsic") not in videos.warn_buttons

    dlg = CalibDialog(videos, session, tmp_path, "cam01", "extrinsic")
    qtbot.addWidget(dlg)
    assert dlg.windowTitle() == "Extrinsic calibration for cam01"
    assert (dlg.table.rowCount(), dlg.table.columnCount()) == (2, 2)
    assert dlg.choice() == {"1": "", "2": "2"}
    dlg.groups["1"].button(1).setChecked(True)  # exactly one per trial: ticking another unticks the first
    assert not dlg.groups["1"].button(0).isChecked() and dlg.choice() == {"1": "2", "2": "2"}
    dlg.save()
    assert load_session(tmp_path).camera("cam01").position("2").trials == ["1", "2"]

    from mocapapp.gui import calib_dialog

    more = tmp_path / "zoomed.mp4"
    more.write_bytes(b"x")
    monkeypatch.setattr(calib_dialog, "pick_videos", lambda *a, **k: [more])
    monkeypatch.setattr(calib_dialog, "confirm", lambda *a, **k: True)
    dlg = CalibDialog(videos, session, tmp_path, "cam01", "intrinsic")
    qtbot.addWidget(dlg)
    assert dlg.windowTitle() == "Intrinsic calibration for cam01" and dlg.table.columnCount() == 0
    dlg.add_videos()  # no intrinsic video yet: the first one added is the main one
    assert dlg.files_changed and dlg.table.columnCount() == 1
    assert Path(session.camera("cam01").intrinsic.file).name == "zoomed.mp4"
    more = tmp_path / "intrinsic.mp4"
    monkeypatch.setattr(calib_dialog, "pick_videos", lambda *a, **k: [more])
    dlg.add_videos()
    assert dlg.table.columnCount() == 2 and session.camera("cam01").lenses[0].trials == []
    dlg.groups["2"].button(1).setChecked(True)
    dlg.save()
    assert load_session(tmp_path).camera("cam01").lens_for("2") == "2"
    dlg = CalibDialog(videos, session, tmp_path, "cam01", "intrinsic")
    qtbot.addWidget(dlg)
    dlg.remove_video("2")
    assert session.camera("cam01").lenses == [] and dlg.table.columnCount() == 1
