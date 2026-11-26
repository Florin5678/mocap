"""Project files (.mocapproj) and the Viewer step's data (0.1.0rc12)."""

import csv
import json
import shutil

import numpy as np
import pytest

from mocapapp.cli.main import main
from mocapapp.core import outputs
from mocapapp.core.project import ProjectError, load_project, save_project
from mocapapp.core.session import Layout, save_session
from tests.m3_fakes import make_session, write_mot, write_trc


def _results(root, n_trials=2):
    """A session whose trials have 3D markers and joint angles (trial 2 with a higher elbow peak)."""
    session = make_session(root, n_trials=n_trials)
    for t in session.trials:
        tdir = Layout(root).trial(t.label)
        write_trc(tdir / "pose-3d" / "t_filt_butterworth.trc")
        write_trc(tdir / "pose-3d" / "t_filt_butterworth_LSTM.trc")
        (tdir / "kinematics").mkdir()
        write_mot(tdir / "kinematics" / "t.mot", peak=90.0 + 10 * (int(t.label) - 1))
        session.run_state.trial_status[t.label] = "done"
    save_session(session, root)
    return session


# ---------- project files ----------

def test_project_file_round_trip_and_moves(tmp_path, capsys):
    root = tmp_path / "data" / "PA26_session"
    _results(root)
    path = save_project(tmp_path / "data" / "gait", root, "run")  # a 0.1.x step: saved as its section
    assert path.name == "gait.mocapproj"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["session_relative"] == "PA26_session" and data["step"] == "session"
    assert load_project(path).session == root.resolve() and load_project(path).step == "session"

    moved = tmp_path / "moved"
    shutil.move(str(tmp_path / "data"), moved)  # project and session moved together: relative path wins
    assert load_project(moved / "gait.mocapproj").session == (moved / "PA26_session").resolve()
    shutil.move(str(moved / "gait.mocapproj"), tmp_path / "gait.mocapproj")  # file alone: absolute fails too
    with pytest.raises(ProjectError, match="was not found"):
        load_project(tmp_path / "gait.mocapproj")

    (tmp_path / "x.mocapproj").write_text("{}")
    with pytest.raises(ProjectError, match="not a mocapapp project"):
        load_project(tmp_path / "x.mocapproj")
    with pytest.raises(ProjectError, match="not a session folder"):
        save_project(tmp_path / "y", tmp_path)

    assert main(["project", "save", "--session", str(moved / "PA26_session"), "--out", str(tmp_path / "p"),
                 "--step", "viewer"]) == 0
    assert main(["project", "show", str(tmp_path / "p.mocapproj")]) == 0
    assert "Section: results" in capsys.readouterr().out
    assert main(["project", "show", str(tmp_path / "x.mocapproj")]) == 1


# ---------- data ----------

def test_outputs_read_summarise_and_csv(tmp_path, capsys):
    root = tmp_path / "S"
    session = _results(root)
    tdir = Layout(root).trial("1")
    assert list(outputs.marker_files(tdir)) == ["smoothed + LSTM", "smoothed"]
    assert [t.label for t in outputs.trials_with_results(session, root)] == ["1", "2"]

    angles = outputs.read_angles(outputs.angle_file(tdir))
    assert angles.columns == ["pelvis_tx", "arm_flex_r", "elbow_flex_r"]
    assert angles.unit("elbow_flex_r") == "deg" and angles.unit("pelvis_tx") == "m"
    row = outputs.summary(angles, ["elbow_flex_r"])[0]
    assert row["min"] == 0 and 89 < row["max"] <= 90 and row["peak_speed"] > 100

    markers = outputs.read_markers(outputs.marker_files(tdir)["smoothed"])
    assert markers.xyz.shape == (20, 14, 3) and markers.rate == 30 and ("RHip", "RKnee") in markers.bones
    table = markers.table()
    assert table.columns[:3] == ["Hip X", "Hip Y", "Hip Z"] and np.allclose(table.column("Head Y"), 1.7)
    assert abs(outputs.summary(table, ["Hip X"])[0]["peak_speed"] - 0.3) < 1e-3  # 0.01 m per frame at 30 fps

    out = outputs.write_csv(angles, tmp_path / "a.csv", ["elbow_flex_r"])
    rows = list(csv.reader(out.open(encoding="utf-8")))
    assert rows[0] == ["time (s)", "elbow_flex_r (deg)"] and len(rows) == 21
    with pytest.raises(outputs.OutputError, match="Unknown column"):
        outputs.write_csv(angles, tmp_path / "b.csv", ["knee"])
    (tdir / "kinematics" / "t.mot").write_text("no header\n")
    with pytest.raises(outputs.OutputError, match="endheader"):
        outputs.read_angles(tdir / "kinematics" / "t.mot")

    assert main(["data", "--session", str(root), "--trial", "2", "--columns", "elbow_flex_r"]) == 0
    text = capsys.readouterr().out
    assert "Joint angles of trial 2" in text and "deg/s" in text and "99.7" in text
    assert main(["data", "--session", str(root), "--trial", "2", "--markers", "raw", "--csv",
                 str(tmp_path / "m.csv")]) == 0  # 'raw' missing: falls back to the best source
    assert (tmp_path / "m.csv").read_text(encoding="utf-8").startswith("time (s),Hip X (m)")
    assert main(["data", "--session", str(root), "--trial", "9"]) == 1


# ---------- GUI ----------

def _wizard_done(root, monkeypatch):
    """A session that went through the whole wizard (fake stages), trial 2 with a higher elbow peak."""
    from tests.test_deletion import _finished

    session = _finished(root, monkeypatch, exported=False)
    session.calibration_source = "imported"  # skip the calibration steps of the wizard
    Layout(root).calibration_work.mkdir(exist_ok=True)
    Layout(root).calib_file.write_text("[cam_1]\n")
    save_session(session, root)
    write_mot(outputs.angle_file(Layout(root).trial("2")), peak=100.0)
    return session


def test_save_and_open_project_at_its_step(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    from mocapapp.gui.main_window import MainWindow

    root = tmp_path / "S"
    _wizard_done(root, monkeypatch)
    win = MainWindow()
    qtbot.addWidget(win)
    win.open_session(root)
    win.go("results")
    asked = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        lambda *a, **k: (asked.append(a[2]), (str(tmp_path / "study"), ""))[1])
    assert win.save_project() == (tmp_path / "study.mocapproj").resolve()
    assert asked == [str(tmp_path / "S.mocapproj")] and "Project: study" in win.session_label.text()
    win.go("participants")
    win.save_project()  # second time: no dialog, same file
    assert len(asked) == 1 and load_project(tmp_path / "study.mocapproj").step == "participants"

    other = MainWindow()
    qtbot.addWidget(other)
    other.open_path(tmp_path / "study.mocapproj")
    assert other.root == root.resolve() and other.current_page().key == "participants" and other.project
    other.go("results")
    other.close()  # closing keeps the project at the section the researcher was on
    assert load_project(tmp_path / "study.mocapproj").step == "results"
    other.new_session()
    assert other.project is None and not other.save_action.isEnabled()


def test_viewer_shows_and_compares_trials(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    from mocapapp.gui.main_window import MainWindow

    root = tmp_path / "S"
    _wizard_done(root, monkeypatch)
    win = MainWindow()
    qtbot.addWidget(win)
    win.open_session(root)
    win.go("results")
    res = win.section("results")
    v = res.viewer
    assert [res.list.item(i).text() for i in range(2)] == ["\u25cf Trial 1 (T1-ABCDEF1): CHECK",
                                                           "\u25cf Trial 2 (T1-ABCDEF2): CHECK"]
    assert [v.trial_box.itemData(i) for i in range(2)] == ["1", "2"] and res.selected() == "1"
    res.tabs.setCurrentIndex(1)
    assert v.source_box.currentText() == "smoothed + LSTM" and v.slider.maximum() == 19
    v.slider.setValue(10)
    assert v.view.frame == 10 and "frame 11/20" in v.time_label.text()
    v.view.grab()  # paints without errors
    v.toggle_play()
    assert v.timer.isActive() and v.play_button.text() == "Pause"
    v.toggle_play()
    assert not v.timer.isActive()

    res.tabs.setCurrentIndex(2)  # Data
    assert v.ticked() == ["arm_flex_r", "elbow_flex_r"] and v.table_model.columnCount() == 3
    v.compare_box.setChecked(True)
    assert len(v.axes.get_lines()) == 5  # 2 columns x 2 trials + the time cursor
    assert "(trial 2)" in v.summary.text() and "max 99.7" in v.summary.text()
    v.speed_plot_box.setChecked(True)
    assert "deg/s" in v.axes.get_ylabel()

    v.dataset_box.setCurrentIndex(1)  # marker positions: starts with the right wrist's height ticked
    assert v.ticked() == [] or all(c.endswith(" Y") for c in v.ticked())
    v.filter_edit.setText("hip x")
    shown = [v.columns.item(i).text() for i in range(v.columns.count()) if not v.columns.item(i).isHidden()]
    assert shown == ["Hip X (m)", "RHip X (m)", "LHip X (m)"]
    v.columns.item(0).setCheckState(v.columns.item(0).checkState().Checked)
    assert "Hip X" in v.ticked()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "out.csv"), ""))
    v.save_csv()
    assert (tmp_path / "out.csv").read_text(encoding="utf-8").startswith("time (s),Hip X (m)")

    res.list.setCurrentRow(1)
    assert v.trial_box.currentData() == "2"
    assert v.markers is not None and v.source_box.currentText() == "smoothed + LSTM"


# ---------- Pose2Sim data made elsewhere (Results > Import Pose2Sim data) ----------

def _shot(win, name):  # MOCAPAPP_SHOTS=<folder>: screenshots for the review
    import os
    from pathlib import Path

    if os.environ.get("MOCAPAPP_SHOTS"):
        win.resize(1280, 860)
        win.grab().save(str(Path(os.environ["MOCAPAPP_SHOTS"]) / f"{name}.png"))


def _pose2sim_project(folder):
    """A Pose2Sim project made outside the app: trial_a with markers and angles, trial_b with markers only."""
    write_trc(folder / "trial_a" / "pose-3d" / "a_filt_butterworth.trc")
    (folder / "trial_a" / "kinematics").mkdir()
    write_mot(folder / "trial_a" / "kinematics" / "a.mot", peak=120.0)
    write_trc(folder / "trial_b" / "pose-3d" / "b.trc")
    (folder / "notes").mkdir()
    return folder


def test_linking_pose2sim_results(tmp_path, capsys):
    from mocapapp.core.session import load_session

    root = tmp_path / "S"
    session = _results(root)
    p2s = _pose2sim_project(tmp_path / "P2S")
    new = outputs.link_results(session, root, p2s)
    assert [(x.id, x.name) for x in new] == [("i1", "P2S/trial_a"), ("i2", "P2S/trial_b")]
    assert outputs.link_results(session, root, p2s / "trial_a") == []  # linked already
    with pytest.raises(outputs.OutputError, match="No Pose2Sim results"):
        outputs.link_results(session, root, p2s / "notes")
    with pytest.raises(outputs.OutputError, match="part of this project"):
        outputs.link_results(session, root, root)
    assert [k for k, _ in outputs.viewable(session, root)] == ["1", "2", "i1", "i2"]
    assert outputs.result_dir(session, root, "i2") == (p2s / "trial_b").resolve()
    save_session(session, root)
    assert [x.name for x in load_session(root).linked] == ["P2S/trial_a", "P2S/trial_b"]

    assert main(["data", "--session", str(root), "--trial", "i1"]) == 0
    assert "of P2S/trial_a: a.mot" in capsys.readouterr().out
    assert main(["data", "--session", str(root), "--trial", "i2"]) == 1
    assert "P2S/trial_b has no joint angles (use --markers)." in capsys.readouterr().out
    assert main(["project", "pose2sim", "--session", str(root), "--remove", "i2"]) == 0
    assert main(["project", "pose2sim", "--session", str(root)]) == 0
    out = capsys.readouterr().out
    assert "Removed from the list: P2S/trial_b (its folder is not touched)." in out and "i1: P2S/trial_a" in out
    assert (p2s / "trial_b" / "pose-3d" / "b.trc").is_file()
    assert main(["project", "pose2sim", "--session", str(root), "--add", str(p2s)]) == 0
    assert "Imported i2: P2S/trial_b" in capsys.readouterr().out
    assert main(["project", "pose2sim", "--session", str(root), "--add", str(p2s / "notes")]) == 1


def test_import_pose2sim_data_in_results(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    from mocapapp.core.session import load_session
    from mocapapp.gui.main_window import MainWindow

    root = tmp_path / "S"
    _wizard_done(root, monkeypatch)
    p2s = _pose2sim_project(tmp_path / "P2S")
    win = MainWindow()
    qtbot.addWidget(win)
    win.open_session(root)
    win.go("results")
    res = win.section("results")
    v = res.viewer
    assert res.import_button.isEnabled() and not res.unlink_button.isEnabled()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(p2s))
    res.import_button.click()
    texts = [res.list.item(i).text() for i in range(res.list.count())]
    assert texts[2:] == ["◆ Imported: P2S/trial_a", "◆ Imported: P2S/trial_b"]
    assert res.selected() == "i1" and v.trial_box.currentData() == "i1" and res.unlink_button.isEnabled()
    assert "Imported: P2S/trial_a" in res.qa.toPlainText() and "a.mot" in res.qa.toPlainText()
    assert [x.id for x in load_session(root).linked] == ["i1", "i2"]  # saved
    _shot(win, "import_quality")

    res.tabs.setCurrentIndex(2)  # Data: the imported angles, compared with the project's trials
    v.compare_box.setChecked(True)
    assert "(P2S/trial_a)" in v.summary.text() and "(trial 2)" in v.summary.text()
    assert "max 120" in v.summary.text()
    _shot(win, "import_compare")
    asked = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        lambda *a, **k: (asked.append(a[2]), (str(tmp_path / "a.csv"), ""))[1])
    v.save_csv()
    assert asked == [str(p2s.resolve() / "trial_a" / "trial_a_joint_angles.csv")] and (tmp_path / "a.csv").is_file()

    res.list.setCurrentRow(3)  # trial_b: markers only
    res.tabs.setCurrentIndex(0)
    assert "no joint angles" in res.qa.toPlainText()
    res.tabs.setCurrentIndex(1)
    assert v.trial_box.currentData() == "i2" and v.markers is not None
    res.unlink_button.click()
    assert [x.id for x in load_session(root).linked] == ["i1"] and (p2s / "trial_b").is_dir()

    shutil.rmtree(p2s / "trial_a")  # the folder went away: listed, with a note
    res.refresh()
    assert res.list.item(2).text() == "◆ Imported: P2S/trial_a - folder not found"
    res.list.setCurrentRow(2)
    assert "no longer there" in res.qa.toPlainText()
    assert [v.trial_box.itemData(i) for i in range(v.trial_box.count())] == ["1", "2"]
