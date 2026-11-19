"""GUI smoke tests (pytest-qt): greeting and tutorial, and the whole wizard on a synthetic 3-camera session.

Set MOCAP_SCREENSHOTS=<folder> to save a PNG of every step.
"""

import os
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtWidgets import QPushButton

from mocapapp.core import runner
from mocapapp.core.session import Layout, load_session
from tests.conftest import make_video
from tests.m3_fakes import FakeStages
from tests.test_calibration import board_views, good_scene, look_at, project, write_video

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")
CAMS = {"cam01": [-2.0, -1.5, 1.6], "cam02": [5.0, -1.5, 1.6], "cam03": [1.5, 4.0, 2.0]}
SHOTS = os.environ.get("MOCAP_SCREENSHOTS")


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "qsettings"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))

    def no_modal(box):  # a message box in a test means something went wrong: fail instead of hanging
        raise AssertionError(f"message box: {box.text()} {box.detailedText()}")

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "exec", no_modal)


def shot(widget, name: str) -> None:
    if SHOTS:
        from PySide6.QtWidgets import QApplication

        QApplication.processEvents()
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        widget.grab().save(str(Path(SHOTS) / f"{name}.png"))


def test_greeting_has_exactly_two_buttons(qtbot):
    from mocapapp.gui.greeting import GreetingDialog

    dlg = GreetingDialog()
    qtbot.addWidget(dlg)
    buttons = dlg.findChildren(QPushButton)
    assert len(buttons) == 2
    assert "How it works" in buttons[0].text() and "recommended" in buttons[0].text()
    assert buttons[1].text() == "Start session"
    shot(dlg, "00_greeting")
    buttons[1].click()
    assert dlg.choice == "start"


def test_tutorial_pages_and_end_buttons(qtbot):
    from mocapapp.gui.greeting import TutorialDialog

    dlg = TutorialDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    assert len(dlg.pages) == 8 and dlg.title.text().startswith("1.")
    assert not dlg.back_button.isVisible() and not dlg.start_button.isVisible()
    for _ in range(10):
        dlg.next_button.click()
    assert dlg.index == 7 and dlg.back_button.isVisible() and dlg.start_button.isVisible()
    assert dlg.consent_button.isVisible() and not dlg.board_button.isVisible()
    shot(dlg, "01_tutorial_last")
    dlg.show_page(6)
    assert dlg.board_button.isVisible()
    dlg.show_page(5)
    assert "sync" in dlg.body.toPlainText().lower()
    dlg.show_page(7)
    dlg.back_button.click()
    assert dlg.choice == "back"


def _raw_videos(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    for i, cam in enumerate(CAMS, 1):
        d = raw / cam
        d.mkdir(parents=True)
        frames = board_views(n=24)
        write_video(frames[i:] + frames[:i], d / "intrinsic.mp4")
        make_video(d / "extrinsic.mp4", seconds=0.3 + 0.05 * i, size="1280x720", pattern="smptebars")
        make_video(d / "walk1.mp4", seconds=0.5 + 0.05 * i, size="1280x720")
    return raw


def wait_idle(qtbot, win, timeout=120000):
    qtbot.waitUntil(lambda: not win.busy, timeout=timeout)


def test_full_wizard(qtbot, tmp_path, monkeypatch):
    import mocapapp.gui.pages.run
    import mocapapp.gui.pages.videos  # noqa: F401 (loads the module for monkeypatch)
    from mocapapp.gui import pages
    from mocapapp.gui.main_window import MainWindow

    monkeypatch.setattr(pages.run, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(runner, "run_stage", FakeStages())
    monkeypatch.setattr(runner, "_fps", lambda *a: 30.0)
    raw = _raw_videos(tmp_path)
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    # 1. session
    setup = win.pages[0]
    assert not win.next_button.isEnabled() and not setup.create_button.isEnabled()
    setup.out_edit.setText(str(tmp_path / "sessions"))
    setup.study_edit.setText("GUI1")
    # choosing cam02's folder fills cam01 and cam03 from the folders next to it
    setup.cam_table.cellWidget(1, 1).setText(str(raw / "cam02"))
    setup.fill_sibling_cameras(1, raw / "cam02")
    assert [setup.cam_table.cellWidget(r, 1).text() for r in range(3)] == [str(raw / cam) for cam in CAMS]
    setup.height_table.item(0, 0).setText("178")
    assert setup.height_table.item(0, 1).text() == "1.78 m"
    setup._check_new()
    shot(win, "10_setup_new")
    assert setup.create_button.isEnabled()
    setup.create_button.click()
    assert win.session is not None and win.session.trials[0].height_m == 1.78
    root = win.root
    people = setup.people
    people.cellWidget(0, 2).setChecked(True)  # signed consent received
    assert load_session(root).trials[0].consent.attested
    people.cellWidget(0, 3).setChecked(True)  # storage consent -> delete unticked by default
    assert not people.cellWidget(0, 4).isChecked()
    shot(win, "11_setup_existing")
    assert win.next_button.isEnabled()
    win.next_button.click()

    # 2. videos
    videos = win.pages[1]
    assert win.current_page() is videos and not win.next_button.isEnabled()
    videos.check()
    wait_idle(qtbot, win)
    assert videos.report is not None and videos.report.ok, videos.report.issues
    assert videos.prepare_button.isEnabled() and "Space needed" in videos.space.text()
    videos.prepare()  # no confirmation dialog: the plan is already shown on the page
    wait_idle(qtbot, win)
    shot(win, "20_videos")
    assert videos.is_complete() and win.next_button.isEnabled()
    win.next_button.click()

    # 3. lens calibration
    lens = win.pages[2]
    lens.every.setValue(0.5)
    lens.calibrate(None, False)
    wait_idle(qtbot, win, 300000)
    shot(win, "30_intrinsics")
    assert lens.is_complete(), [lens.table.item(r, 1).text() for r in range(3)]
    win.next_button.click()

    # 4. reference points (dialog)
    from mocapapp.gui.scene_dialog import SceneDialog

    scene = good_scene()
    dlg = SceneDialog(win, None)
    qtbot.addWidget(dlg)
    dlg.set_points(scene.points)
    dlg.origin.setText("tape cross by the door")
    dlg.x_text.setText("the window")
    dlg.resize(1150, 760)
    shot(dlg, "40_scene_dialog")
    assert dlg.ok_button.isEnabled()
    assert "Problem" not in dlg.issues.toPlainText() and "To do" not in dlg.issues.toPlainText()
    from mocapapp.core.calibration.service import set_scene

    set_scene(root, dlg.scene())
    win.pages[3].refresh()
    win.pages[3].completeChanged.emit()  # what SceneDialog's OK does via ScenePage.edit
    assert win.pages[3].is_complete()
    win.next_button.click()
    assert win.current_page() is win.pages[4]

    # 5. camera positions: click the true projections in the dialog
    from mocapapp.core.calibration import intrinsics as intr
    from mocapapp.core.calibration.service import input_video
    from mocapapp.gui.extrinsic_dialog import ExtrinsicDialog

    ext_page = win.pages[4]
    work = Layout(root).calibration_work
    for cam, pos in CAMS.items():
        r, t = look_at(np.array(pos), [1.5, 1.0, 0.6])
        uv = project(scene.array(), r, t)
        K = intr.load_result(work, cam)
        d = ExtrinsicDialog(win, cam, input_video(root, cam, "extrinsic"), scene, K)
        qtbot.addWidget(d)
        for k, (u, v) in enumerate(uv):
            d.list.setCurrentRow(k)
            if k == 2:
                d._toggle_hidden()
            else:
                d.view.clicked.emit(float(u), float(v))
        assert d.result is not None and d.result.status in ("ok", "warn"), d.result_label.text()
        if cam == "cam01":
            d.show()
            shot(d, "50_extrinsic_dialog")
            d._undo()
            assert d.n_visible() == len(uv) - 2
            d.view.clicked.emit(float(uv[-1][0]), float(uv[-1][1]))
        from mocapapp.core.calibration.service import run_extrinsics

        run_extrinsics(win.session, root, cam, d.clicks(), d.frame_index)
        d.close()
    ext_page.refresh()
    ext_page.completeChanged.emit()
    shot(win, "51_extrinsics")
    assert ext_page.is_complete() and Layout(root).calib_file.is_file()
    win.next_button.click()

    # 6. run (fake stages)
    run = win.pages[5]
    assert win.current_page() is run
    assert not run.run_button.isEnabled()  # sync cue not confirmed yet
    run.sync_box.setChecked(True)
    assert run.run_button.isEnabled(), run.issues.toPlainText()
    shot(win, "60_run_summary")
    run.start()
    wait_idle(qtbot, win)
    shot(win, "61_run_done")
    assert run.is_complete(), load_session(root).run_state
    win.next_button.click()

    # 7. results and export
    res = win.pages[6]
    assert win.current_page() is res and len(res.results) == 1
    assert "Trial 1" in res.qa.toPlainText()
    assert win.next_button.text() == "Next >" and win.next_button.isEnabled()  # the Viewer needs no export
    assert win.reachable(7)
    assert not res.boxes["bvh"].isChecked() and res.boxes["mot"].isChecked()
    res.boxes["bvh"].setChecked(True)
    res.do_export()
    wait_idle(qtbot, win)
    shot(win, "70_results")
    names = {p.name for p in Layout(root).export.iterdir()}
    code = win.session.trials[0].code
    assert f"{code}_trial1_skeleton_animation.bvh" in names and "manifest.json" in names
    assert "Exported to" in res.export_state.text()
    win.next_button.click()

    # 8. viewer
    viewer = win.pages[7]
    assert win.current_page() is viewer and viewer.trial_box.count() == 1
    assert viewer.source_box.currentText() == "smoothed + LSTM" and viewer.markers is not None
    viewer.tabs.setCurrentIndex(1)
    assert viewer.ticked() == ["arm_flex_r", "elbow_flex_r"] and "max 89.7" in viewer.summary.text()
    shot(win, "80_viewer")
    assert win.next_button.text() == "Finished!" and win.next_button.isEnabled()
    shown = []
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", lambda *a: shown.append(a[2]))
    win.next_button.click()  # back to step 1 with an empty session; reminds about footage still stored
    assert win.session is None and win.stack.currentIndex() == 0 and win.next_button.text() == "Next >"
    assert shown and "Raw footage" in shown[0]


def test_sync_gate_buttons(qtbot, tmp_path, monkeypatch):
    import mocapapp.gui.pages.run
    import mocapapp.gui.pages.videos  # noqa: F401 (loads the module for monkeypatch)
    from mocapapp.gui import pages
    from mocapapp.gui.main_window import MainWindow
    from tests.m3_fakes import SYNC_BAD, make_session

    monkeypatch.setattr(pages.run, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(runner, "run_stage", FakeStages(sync_log=SYNC_BAD))
    monkeypatch.setattr(runner, "_fps", lambda *a: 30.0)
    monkeypatch.setattr(runner, "estimate_seconds", lambda *a: 60)
    session = make_session(tmp_path, n_trials=1)
    for cam in session.cameras:
        Layout(tmp_path).inputs(cam.name).mkdir(parents=True)
        (Layout(tmp_path).inputs(cam.name) / "trial_1.mp4").write_bytes(b"fake")
    win = MainWindow()
    qtbot.addWidget(win)
    win.open_session(tmp_path)
    run = win.pages[5]
    run.refresh()
    run.start()
    wait_idle(qtbot, win)
    assert win.session.run_state.trial_status["1"] == "needs_sync_fix"
    accept = run.trials.cellWidget(0, 4)
    assert accept.isEnabled() and run.trials.cellWidget(0, 3).isEnabled()
    accept.click()
    wait_idle(qtbot, win)
    assert win.session.run_state.trial_status["1"] == "done"


def test_changing_a_setting_after_a_run_repeats_only_later_stages(qtbot, tmp_path, monkeypatch):
    import mocapapp.gui.pages.run  # noqa: F401 (loads the module for monkeypatch)
    from mocapapp.core.session import save_session
    from mocapapp.gui import pages
    from mocapapp.gui.main_window import MainWindow
    from tests.m3_fakes import make_session

    monkeypatch.setattr(pages.run, "confirm", lambda *a, **k: True)
    fake = FakeStages()
    monkeypatch.setattr(runner, "run_stage", fake)
    monkeypatch.setattr(runner, "_fps", lambda *a: 30.0)
    monkeypatch.setattr(runner, "estimate_seconds", lambda *a: 60)
    session = make_session(tmp_path, n_trials=1)
    session.calibration_source = "imported"  # calibration done: nothing else keeps Run disabled
    Layout(tmp_path).calibration_work.mkdir(exist_ok=True)
    Layout(tmp_path).calib_file.write_text("[cam_1]\n")
    save_session(session, tmp_path)
    for cam in session.cameras:
        Layout(tmp_path).inputs(cam.name).mkdir(parents=True)
        (Layout(tmp_path).inputs(cam.name) / "trial_1.mp4").write_bytes(b"fake")
    win = MainWindow()
    qtbot.addWidget(win)
    win.open_session(tmp_path)
    run = win.pages[5]
    run.refresh()
    run.start()
    wait_idle(qtbot, win)
    assert win.session.run_state.trial_status["1"] == "done"
    assert run.adv_inner.isEnabled()  # no longer locked after a run

    assert run.run_button.isEnabled(), run.issues.toPlainText()
    run.cutoff.setValue(15)  # too high for 30 fps: blocked before running
    assert not run.run_button.isEnabled()
    assert "below half the frame rate" in run.issues.toPlainText()
    assert win.session.run_state.trial_status["1"] == "partial" and not run.stale_note.isHidden()
    run.cutoff.setValue(8)
    assert run.run_button.isEnabled(), run.issues.toPlainText()
    fake.calls.clear()
    run.start()
    wait_idle(qtbot, win)
    assert [s for _, s in fake.calls] == ["filtering", "markerAugmentation", "kinematics"]
    assert win.session.run_state.trial_status["1"] == "done" and run.stale_note.isHidden()

    # BVH is always made; the keypoint set and the OpenSim model are choices
    assert not hasattr(run, "bvh")
    run.body_model.setCurrentIndex(1)
    assert win.session.settings.simple_model and "joint angles" in run.stale_note.text()
    run.keypoints.setCurrentIndex(run.keypoints.findData("Whole_body"))
    assert win.session.settings.keypoints == "Whole_body"
    assert "finding body keypoints" in run.stale_note.text().lower()
    assert "whole body" in run.summary.toPlainText().lower()


@pytest.mark.slow
def test_sample_data_in_gui(qtbot, tmp_path, monkeypatch):
    """M4 exit criterion: the GUI flow on the bundled sample data, with the real Pose2Sim run (~4 min)."""
    import mocapapp.gui.pages.run  # noqa: F401 (loads the module for monkeypatch)
    from mocapapp.core.sample import make_sample_session
    from mocapapp.gui import pages
    from mocapapp.gui.main_window import MainWindow

    monkeypatch.setattr(pages.run, "confirm", lambda *a, **k: True)
    root, _ = make_sample_session(tmp_path / "sessions", progress=lambda m: None)
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    win.open_session(root)
    run = win.pages[5]
    assert win.current_page() is run  # videos and calibration steps are skipped for the sample
    assert all(win.pages[i].skipped() for i in (1, 2, 3, 4))
    assert "consent" in run.issues.toPlainText().lower() and not run.run_button.isEnabled()
    win.go(0)
    win.pages[0].people.cellWidget(0, 2).setChecked(True)  # signed consent received
    win.next_button.click()
    assert win.current_page() is run
    run.sync_box.setChecked(True)
    assert run.run_button.isEnabled(), run.issues.toPlainText()
    shot(win, "80_sample_run")
    run.start()
    wait_idle(qtbot, win, 1200000)
    assert run.is_complete(), win.session.run_state
    win.next_button.click()
    res = win.pages[6]
    assert res.results[0].light in ("green", "amber")
    res.do_export()
    wait_idle(qtbot, win)
    shot(win, "81_sample_results")
    assert (Layout(root).export / "manifest.json").is_file()
