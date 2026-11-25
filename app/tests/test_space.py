"""Disk space (0.1.0rc13): no overlay videos by default, 2D keypoints removed after a finished trial, space check."""

from collections import namedtuple

import pytest
import tomlkit

from mocapapp.core import runner
from mocapapp.core.config_gen import build_config
from mocapapp.core.session import Layout, load_session
from tests.m3_fakes import FakeStages, make_session

KEYPOINTS = ("pose", "pose-sync", "pose-associated")


@pytest.fixture
def fake(monkeypatch):
    f = FakeStages()
    monkeypatch.setattr(runner, "run_stage", f)
    monkeypatch.setattr(runner, "_fps", lambda *a: 30.0)
    return f


def _kept(root, label):
    return [k for k in KEYPOINTS if (Layout(root).trial(label) / k).exists()]


def test_overlay_videos_only_when_asked(tmp_path):
    session = make_session(tmp_path)
    assert tomlkit.parse(build_config(session, session.trials[0]))["pose"]["save_video"] == "none"
    session.settings.save_overlay = True
    assert tomlkit.parse(build_config(session, session.trials[0]))["pose"]["save_video"] == "to_video"


def test_finished_trial_loses_its_keypoints_and_still_resumes(tmp_path, fake):
    session = make_session(tmp_path)
    lines = []
    assert runner.run_session(session, tmp_path, listener=lines.append) == {"1": "done", "2": "done"}
    assert _kept(tmp_path, "1") == [] and _kept(tmp_path, "2") == []
    assert set(load_session(tmp_path).run_state.keypoints_removed) == {"1", "2"}
    assert any("2D keypoint files removed" in e.message for e in lines)
    assert (Layout(tmp_path).trial("1") / "kinematics").is_dir()  # results are kept

    fake.calls.clear()  # resume: nothing to do although the keypoint folders are gone
    assert runner.run_session(session, tmp_path) == {"1": "done", "2": "done"} and fake.calls == []

    session.settings.filter.cutoff_hz = 8.0  # smoothing changed: only the 3D stages from filtering run again
    fake.calls.clear()
    runner.run_trial(session, tmp_path, "1")
    assert [s for _, s in fake.calls] == ["filtering", "markerAugmentation", "kinematics"]


def test_lost_3d_restarts_from_the_videos(tmp_path, fake):
    session = make_session(tmp_path)
    runner.run_session(session, tmp_path)
    for trc in (Layout(tmp_path).trial("1") / "pose-3d").glob("*.trc"):
        trc.unlink()
    fake.calls.clear()
    assert runner.run_trial(session, tmp_path, "1")
    assert [s for _, s in fake.calls] == runner.STAGES  # keypoints are needed again, so they are made again
    assert "1" in session.run_state.keypoints_removed and _kept(tmp_path, "1") == []


def test_new_keypoint_settings_rerun_everything(tmp_path, fake):
    session = make_session(tmp_path)
    runner.run_session(session, tmp_path)
    session.settings.pose_mode = "performance"
    fake.calls.clear()
    runner.run_trial(session, tmp_path, "2")
    assert [s for _, s in fake.calls] == runner.STAGES


def test_keep_keypoints_and_unfinished_trials_keep_them(tmp_path, monkeypatch):
    from tests.m3_fakes import SYNC_BAD

    monkeypatch.setattr(runner, "run_stage", FakeStages(sync_log=SYNC_BAD))
    monkeypatch.setattr(runner, "_fps", lambda *a: 30.0)
    session = make_session(tmp_path)
    runner.run_session(session, tmp_path)  # stops before 3D: needs a sync check, which needs the keypoints
    assert _kept(tmp_path, "1") == ["pose", "pose-sync"] and session.run_state.keypoints_removed == {}

    monkeypatch.setattr(runner, "run_stage", FakeStages())
    session.settings.keep_keypoints = True
    runner.run_session(session, tmp_path, accept_sync=True)
    assert session.run_state.trial_status["1"] == "done" and len(_kept(tmp_path, "1")) == 3


def test_space_check_warns_before_a_full_drive(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    monkeypatch.setattr(runner, "_probe_frames", lambda *a: 15_000)  # 4 min at 60 fps per camera
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(runner.shutil, "disk_usage", lambda p: usage(0, 0, 3_000_000_000))
    need = runner.space_needed(session, tmp_path, ["1", "2"])
    # Body with feet: 2 cameras x 15000 frames x 25 kB, twice per trial, plus one more copy and the results
    assert need == 2 * (2 * 750_000_000 + runner.RESULTS_BYTES) + 750_000_000
    issues = [i for i in runner.preflight(session, tmp_path, ["1", "2"]) if i.where == "disk space"]
    assert issues and issues[0].level == "warning"
    assert "about 4 GB" in issues[0].what and "3 GB are free" in issues[0].what
    assert "Finished trials give most of it back" in issues[0].what

    without = runner.space_needed(session, tmp_path, ["1"])
    session.settings.save_overlay = True  # overlays: 1.5 x the two trial videos (16 bytes each in the fake)
    assert runner.space_needed(session, tmp_path, ["1"]) == without + int(2 * 16 * runner.OVERLAY_FACTOR)

    session.run_state.stage_done["1"] = ["poseEstimation"]  # keypoints already found: nothing big left to write
    assert runner.space_needed(session, tmp_path, ["1"]) == 0
    monkeypatch.setattr(runner.shutil, "disk_usage", lambda p: usage(0, 0, 10**12))
    assert not [i for i in runner.preflight(session, tmp_path, ["1", "2"]) if i.where == "disk space"]


def test_space_options_in_session_settings(qtbot, tmp_path, monkeypatch):
    from mocapapp.gui.main_window import MainWindow
    from mocapapp.gui.session_settings import SessionSettingsDialog
    from tests.test_viewer_projects import _wizard_done

    root = tmp_path / "S"
    _wizard_done(root, monkeypatch)
    win = MainWindow()
    qtbot.addWidget(win)
    win.open_session(root)
    dlg = SessionSettingsDialog(win, win.session, root)
    qtbot.addWidget(dlg)
    assert not dlg.overlay.isChecked() and dlg.keep.isChecked()  # the helper session keeps its keypoints
    dlg.overlay.setChecked(True)
    dlg.keep.setChecked(False)
    dlg._accept()
    saved = load_session(root).settings
    assert saved.save_overlay and not saved.keep_keypoints
    assert win.session.run_state.trial_status["1"] == "done"  # these options never make results stale
