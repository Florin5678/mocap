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

def test_columns_dialog_and_participants_table(qtbot, tmp_path):
    """Optional participant columns are chosen in a dialog; their cells and the consent drop-down ignore the mouse
    wheel (0.1.0rc17)."""
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication

    from mocapapp.gui.columns_dialog import ColumnsDialog
    from mocapapp.gui.main_window import MainWindow

    session = make_session(tmp_path)
    save_session(session, tmp_path)
    win = MainWindow()
    qtbot.addWidget(win)
    win.set_session(tmp_path, session)
    win.go("participants")
    people = win.section("participants")
    dlg = ColumnsDialog(people, session, tmp_path)
    qtbot.addWidget(dlg)
    assert dlg.keys == ["height", "weight", "handedness", "age", "sex"]  # one row per detail
    name, unit, kind, options, shown = dlg.widgets(dlg.keys.index("sex"))
    assert name.text() == "Gender" and not name.isEnabled() and kind.currentData() == "choice"
    assert not dlg.widgets(0)[4].isEnabled()  # height is always asked
    dlg.widgets(dlg.keys.index("weight"))[4].setChecked(True)
    assert [d.key for d in load_session(tmp_path).asked_details] == ["height"]  # nothing changes before Save
    dlg.save()
    assert [d.key for d in load_session(tmp_path).asked_details] == ["height", "weight"]
    people.refresh()
    headers = [people.table.horizontalHeaderItem(c).text() for c in range(people.table.columnCount())]
    assert "Weight (kg)" in headers
    consent = people.table.cellWidget(0, headers.index("Consent"))
    before = consent.currentIndex()
    event = QWheelEvent(QPointF(5, 5), QPointF(consent.mapToGlobal(QPoint(5, 5))), QPoint(0, 0), QPoint(0, -120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(consent, event)
    assert consent.currentIndex() == before


def test_details_table_adds_changes_and_removes(qtbot, tmp_path, monkeypatch):
    """Participants > Columns: details as rows (name, unit, type, options, shown), saved together; a changed type
    must fit the values entered; removing a detail removes its values (after asking)."""
    from mocapapp.core.workflow import SetupError, set_details
    from mocapapp.gui import columns_dialog
    from mocapapp.gui.columns_dialog import ColumnsDialog

    session = make_session(tmp_path)
    save_session(session, tmp_path)
    dlg = ColumnsDialog(None, session, tmp_path)
    qtbot.addWidget(dlg)
    r = dlg.add_row()
    name, unit, kind, options, shown = dlg.widgets(r)
    name.setText("Dominant leg")
    kind.setCurrentIndex(kind.findData("choice"))
    assert options.isEnabled() and not unit.isEnabled()
    assert options.placeholderText() and not unit.placeholderText()  # an example only where one can type
    options.setText("left, right")
    dlg.add_row()  # left empty: ignored
    dlg.save()
    leg = session.detail("dominant_leg")
    assert leg.choices == ["left", "right"] and leg.enabled and not leg.builtin
    session.trials[0].details["dominant_leg"] = "left"
    rows = [{"key": d.key, "label": d.label, "kind": d.kind, "unit": d.unit, "choices": d.choices,
             "enabled": d.enabled} for d in session.details]
    rows[-1]["kind"] = "number"
    with pytest.raises(SetupError, match="do not fit"):
        set_details(session, tmp_path, rows)
    assert session.detail("dominant_leg").kind == "choice"
    rows[-1].update(kind="choice", label="Leading leg", choices=["Left", "Right"])
    set_details(session, tmp_path, rows)
    assert session.detail("dominant_leg").label == "Leading leg" and session.trials[0].details["dominant_leg"] == "Left"
    with pytest.raises(SetupError, match="built in"):
        set_details(session, tmp_path, rows[1:])

    dlg = ColumnsDialog(None, session, tmp_path)
    qtbot.addWidget(dlg)
    monkeypatch.setattr(columns_dialog, "confirm", lambda *a: True)
    dlg.table.selectRow(dlg.keys.index("weight"))
    assert not dlg.remove_button.isEnabled()  # built in: only unticked
    dlg.table.clearSelection()
    dlg.table.selectRow(dlg.keys.index("dominant_leg"))
    assert dlg.remove_button.isEnabled()
    dlg.remove_rows()
    first, second = dlg.add_row(), dlg.add_row()  # a row removed above another: the type still changes its own row
    dlg.table.clearSelection()
    dlg.table.selectRow(first)
    dlg.remove_rows()
    kind, options = dlg.widgets(second - 1)[2], dlg.widgets(second - 1)[3]
    kind.setCurrentIndex(kind.findData("choice"))
    assert options.isEnabled()
    dlg.save()
    assert "dominant_leg" not in [d.key for d in load_session(tmp_path).details]
    assert "dominant_leg" not in load_session(tmp_path).trials[0].details


def test_import_participants_from_csv(tmp_path):
    from mocapapp.core.participants_csv import import_participants
    from mocapapp.core.workflow import SetupError, set_detail_enabled

    session = make_session(tmp_path / "S")
    save_session(session, tmp_path / "S")
    root = tmp_path / "S"
    set_detail_enabled(session, root, "weight", True)
    first = session.participants[0].code
    csv_file = tmp_path / "people.csv"
    csv_file.write_text(f"Pseudonym;Height (m);Weight (kg);Shoe size\n{first};1,82;70;42\nNEWONE;175 cm;;\n;1.6;55;\n",
                        encoding="utf-8-sig")
    n = len(session.participants)
    result = import_participants(session, root, csv_file)
    assert result.updated == [first] and len(result.added) == 2 and "NEWONE" in result.added
    assert result.ignored_columns == ["Shoe size"]
    assert session.participants[0].height_m == pytest.approx(1.82)
    assert all(t.details.get("weight") == "70" for t in session.trials_of(session.participants[0].label))
    new = next(p for p in session.participants if p.code == "NEWONE")
    assert new.height_m == pytest.approx(1.75) and session.trials_of(new.label)  # with trials for their details
    assert len(session.participants) == n + 2 and len(load_session(root).participants) == n + 2

    bad = tmp_path / "bad.csv"
    bad.write_text("Pseudonym,Height,Weight\nAB,1.7,heavy\nJohn Smith,1.8,80\n", encoding="utf-8")
    with pytest.raises(SetupError, match="Nothing was imported") as err:
        import_participants(session, root, bad)
    assert "row 2" in str(err.value) and "row 3" in str(err.value)
    assert len(session.participants) == n + 2

    from mocapapp.cli.main import main  # the same from the command line

    again = tmp_path / "again.csv"
    again.write_text("Pseudonym,Height\nNEWONE,1.77\n", encoding="utf-8")
    assert main(["session", "import", "--session", str(root), str(again)]) == 0
    assert next(p for p in load_session(root).participants if p.code == "NEWONE").height_m == pytest.approx(1.77)
    assert main(["session", "import", "--session", str(root), str(bad)]) == 1


def test_mouse_wheel_never_changes_values(qtbot, tmp_path):
    """Scrolling over the calibration board's number boxes (or any number box or drop-down) leaves them as they
    were (0.2.0rc3)."""
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication, QLineEdit

    from mocapapp.gui.main_window import MainWindow

    session = make_session(tmp_path)
    save_session(session, tmp_path)
    win = MainWindow()
    qtbot.addWidget(win)
    win.set_session(tmp_path, session)
    win.go("cameras")
    cams = win.section("cameras")
    for box in (cams.cols, cams.rows, cams.square, cams.board_type, cams.every):
        before = box.currentIndex() if hasattr(box, "currentIndex") else box.value()
        for target in [box] + box.findChildren(QLineEdit):  # also over the box's own text field
            event = QWheelEvent(QPointF(5, 5), QPointF(target.mapToGlobal(QPoint(5, 5))), QPoint(0, 0),
                                QPoint(0, 120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(target, event)
        after = box.currentIndex() if hasattr(box, "currentIndex") else box.value()
        assert after == before, box
