"""P3 (PROJECT.md §16.6): the five-section window. Exit criterion: calibrate cameras and a layout in the GUI.

Modal dialogs are replaced by stand-ins that answer like a researcher would; everything else is the real window."""

from pathlib import Path

import numpy as np
import pytest

from mocapapp.core import workflow
from mocapapp.core.calibration import service
from mocapapp.core.calibration.extrinsics import Click
from mocapapp.core.session import load_session
from mocapapp.gui import extrinsic_dialog, scene_dialog, session_settings
from mocapapp.gui.main_window import MainWindow
from mocapapp.gui.sections import cameras as cameras_mod
from mocapapp.gui.sections import layouts as layouts_mod
from mocapapp.gui.session_settings import SessionSettingsDialog
from tests.conftest import make_video
from tests.test_calibration import board_views, good_scene, look_at, project, write_video

CAMS = {"cam01": [-2.0, -1.5, 1.6], "cam02": [5.0, -1.5, 1.6]}
TICK = "✓"


class _Text:
    def __init__(self, text):
        self._text = text

    def text(self):
        return self._text

    def currentData(self):  # noqa: N802 (Qt name)
        return self._text


@pytest.fixture
def win(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    return w


def _new_project(win, tmp_path):
    sec = win.section("session")
    sec.out_edit.setText(str(tmp_path / "projects"))
    sec.study_edit.setText("T1")
    sec.create_button.click()
    assert win.session is not None and win.session.study_code == "T1"
    return win.root, win.session


def _raw(tmp_path, trials: int = 0):
    """Each camera's folder: board video, extrinsic video and `trials` trial videos named walk1, walk2, ..."""
    raw = tmp_path / "raw"
    for i, cam in enumerate(CAMS, 1):
        d = raw / cam
        d.mkdir(parents=True)
        frames = board_views(n=24)
        write_video(frames[i:] + frames[:i], d / "intrinsic.mp4")
        make_video(d / "extrinsic.mp4", seconds=0.3 + 0.05 * i, size="1280x720", pattern="smptebars")
        for n in range(1, trials + 1):
            make_video(d / f"walk{n}.mp4", seconds=0.5 + 0.05 * i, size="1280x720")
    return raw


def test_sections_and_badges(win, tmp_path):
    # nothing open: only the Session section, with the new-project form
    assert [win.sidebar.item(i).text() for i in range(5)] == ["Session", "Participants", "Cameras", "Layouts",
                                                              "Results"]
    assert not win.reachable(2) and win.current_page().key == "session"
    root, ses = _new_project(win, tmp_path)
    assert win.reachable(4) and (root / "session.json").is_file()
    workflow.add_camera(ses, root, name="left")
    workflow.add_layout(ses, root, "Hall", ["cam01"])
    win.changed()
    assert win.badges()["cameras"] == "1 to do" and win.badges()["layouts"] == "1 to do"
    assert "Cameras    1 to do" in win.sidebar.item(2).text()
    for key in ("participants", "cameras", "layouts", "results"):
        win.go(key)
        assert win.current_page().key == key and win.page_title.text() == win.current_page().title
    # the first-steps checklist links to the sections
    win.go("session")
    text = win.section("session").checklist.text()
    assert f"{TICK} Add the cameras" in text and "href='layouts'" in text
    win.section("session").checklist.linkActivated.emit("cameras")
    assert win.current_page().key == "cameras"


def test_session_settings(win, tmp_path, monkeypatch):
    root, ses = _new_project(win, tmp_path)
    keep = workflow.add_participant(ses, root)
    workflow.set_consent(ses, root, keep.label, "keep")
    assert not keep.delete_raw
    dlg = SessionSettingsDialog(win, ses, root)
    shown = []
    monkeypatch.setattr(session_settings, "error_box", lambda _p, title, text, *a: shown.append(text))
    dlg.study.setText("pa 26")
    assert not dlg._apply() and "no spaces" in shown[0]  # a plain message, the dialog stays open
    dlg.study.setText("pa26")
    dlg.trials.setValue(3)
    dlg.notes.setPlainText("hall, autumn")
    dlg.cutoff.setValue(8)
    dlg.overlay.setChecked(True)
    dlg.delete_all.setChecked(True)
    assert [dlg.when.itemText(i) for i in range(dlg.when.count())] == [
        "after the trial is processed", "after the results are exported"]
    assert dlg.when.currentData() == "after_export"
    dlg.when.setCurrentIndex(0)
    dlg._accept()
    saved = load_session(root)
    assert (saved.study_code, saved.trials_per_participant, saved.notes) == ("PA26", 3, "hall, autumn")
    assert saved.settings.filter.cutoff_hz == 8 and saved.settings.save_overlay
    assert saved.settings.delete_raw_all and saved.participants[0].delete_raw  # R13: even with storage consent
    assert saved.settings.delete_raw_when == "after_processing"
    assert saved.participants[0].code == keep.code  # existing pseudonyms never change
    dlg = SessionSettingsDialog(win, ses, root)
    dlg.delete_all.setChecked(False)
    dlg._accept()
    assert not load_session(root).participants[0].delete_raw  # consent decides again


def calibrate_in_window(win, tmp_path, qtbot, monkeypatch, trials: int = 0):
    """A new project; two cameras added and calibrated in Cameras, layout 'Hall' made and calibrated in Layouts.
    Returns (raw folder, project folder, session)."""
    raw = _raw(tmp_path, trials)
    root, ses = _new_project(win, tmp_path)

    # Cameras: add two from their folders (the 'intrinsic' video is found by name), then calibrate them
    class CameraDialog:
        folder = ""

        def __init__(self, parent, cam=None, locked=""):
            pass

        def exec(self):
            return 1

        def values(self):
            return {"name": "", "source_dir": CameraDialog.folder}

        def intrinsic(self):
            return None

    monkeypatch.setattr(cameras_mod, "CameraDialog", CameraDialog)
    win.go("cameras")
    cams = win.section("cameras")
    for cam in CAMS:
        CameraDialog.folder = str(raw / cam)
        cams.buttons["add"].click()
    assert [Path(c.intrinsic.file).name for c in ses.cameras] == ["intrinsic.mp4"] * 2
    assert cams.calibrate_button.isEnabled() and cams.calibrate_button.text() == "Calibrate all cameras"
    cams.every.setValue(0.5)  # the synthetic board videos are short (as in the CLI test)
    cams.calibrate_button.click()
    assert win.busy
    qtbot.waitUntil(lambda: not win.busy, timeout=300000)
    assert all(service.lens_result(root, c) for c in CAMS)
    assert cams.table.item(0, 3).text() in ("PASS", "CHECK (usable)")
    assert win.badges()["cameras"] == TICK
    assert cams.calibrate_button.text() == "Recalibrate all cameras"
    _select(cams.table, [0])
    assert cams.calibrate_button.text() == "Recalibrate selected" and cams.calibration_plan()[1] == [next(iter(CAMS))]
    cams.table.clearSelection()

    # Layouts: a new layout, its reference points, each camera's extrinsic video and clicks
    class LayoutDialog:
        def __init__(self, parent, session, lay=None):
            self.name, self.date, self.preset = _Text("Hall"), _Text(""), _Text("")

        def exec(self):
            return 1

        def cameras(self):
            return list(CAMS)

    class SceneDialog:
        def __init__(self, parent, scene, has_extrinsics=False, presets=None, on_save_preset=None, title=""):
            pass

        def exec(self):
            return 1

        def scene(self):
            return good_scene()

    class ExtrinsicDialog:
        def __init__(self, parent, camera, video, scene, intr, previous=None):
            self.camera, self.scene = camera, scene
            self.frame_index, self.override = 0, False

        def exec(self):
            return 1

        def clicks(self):
            r, t = look_at(np.array(CAMS[self.camera]), [1.5, 1.0, 0.6])
            uv = project(self.scene.array(), r, t)
            return [Click(p.label, float(u), float(v)) for p, (u, v) in zip(self.scene.points, uv, strict=True)]

    monkeypatch.setattr(layouts_mod, "LayoutDialog", LayoutDialog)
    monkeypatch.setattr(scene_dialog, "SceneDialog", SceneDialog)
    monkeypatch.setattr(extrinsic_dialog, "ExtrinsicDialog", ExtrinsicDialog)
    win.go("layouts")
    lays = win.section("layouts")
    lays.buttons["new"].click()
    assert [x.name for x in ses.layouts] == ["Hall"] and lays.current == "L1"
    lays.edit_points.click()
    assert ses.layout("L1").points["points"]
    assert lays.save_preset.isEnabled()
    for cam in CAMS:
        monkeypatch.setattr(layouts_mod, "choose_video", lambda *a, c=cam: raw / c / "extrinsic.mp4")
        lays.choose(cam)
        assert Path(ses.layout("L1").entry(cam).extrinsic.file).name == "extrinsic.mp4"
    for cam in CAMS:
        lays.click(cam)
        qtbot.waitUntil(lambda c=cam: service.position_result(root, "L1", c) is not None, timeout=120000)
        qtbot.waitUntil(lambda: not win.busy, timeout=10000)
    assert service.layout_state(ses, root, "L1") == {"Hall: cam01": "ready", "Hall: cam02": "ready"}
    return raw, root, ses


def test_calibrate_cameras_and_layout_in_the_window(win, tmp_path, qtbot, monkeypatch):
    _raw_dir, root, ses = calibrate_in_window(win, tmp_path, qtbot, monkeypatch)
    lays = win.section("layouts")
    assert lays.table.item(0, 5).text() in ("PASS", "CHECK (usable)")
    assert "Ready" in lays.state.text()
    assert win.badges()["layouts"] == TICK and "Layouts    " + TICK in win.sidebar.item(3).text()
    saved = load_session(root)  # everything was saved by the core functions
    assert saved.layout("L1").entry("cam02").result["status"] in ("ok", "warn")


def _select(view, rows):
    from PySide6.QtCore import QItemSelectionModel

    view.clearSelection()
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    for r in rows:
        view.selectionModel().select(view.model().index(r, 0), flags)


def test_several_rows_at_once(win, tmp_path, qtbot, monkeypatch):
    """Duplicate, delete, remove and new pseudonyms for several selected rows; one message lists what could not be
    done, the rest is done."""
    root, ses = _new_project(win, tmp_path)
    for name in ("left", "right", "top"):
        workflow.add_camera(ses, root, name=name)
    workflow.add_layout(ses, root, "Hall", ["cam01", "cam02"])
    workflow.add_layout(ses, root, "Gym", ["cam03"])
    monkeypatch.setattr(cameras_mod, "confirm", lambda *a: True)
    monkeypatch.setattr(layouts_mod, "confirm", lambda *a: True)
    shown = []
    monkeypatch.setattr("mocapapp.gui.common.error_box", lambda _p, title, text, *a: shown.append((title, text)))

    win.go("cameras")
    cams = win.section("cameras")
    _select(cams.table, [0, 2])
    assert cams.selected() == ["cam01", "cam03"] and not cams.buttons["edit"].isEnabled()
    cams.buttons["duplicate"].click()
    assert [c.name for c in ses.cameras[3:]] == ["left (copy)", "top (copy)"]
    assert cams.selected() == ["cam04", "cam05"]  # the copies are selected
    _select(cams.table, [1, 3, 4])  # cam02 is in a layout: not deleted, the copies are
    cams.buttons["delete"].click()
    assert [c.id for c in ses.cameras] == ["cam01", "cam02", "cam03"]
    assert shown[-1][0] == "Not deleted" and "Hall" in shown[-1][1]

    win.go("layouts")
    lays = win.section("layouts")
    _select(lays.list, [0, 1])
    assert lays.selected() == ["L1", "L2"] and lays.buttons["delete"].text() == "Delete (2)"
    lays.buttons["duplicate"].click()
    assert [x.name for x in ses.layouts] == ["Hall", "Gym", "Hall (copy)", "Gym (copy)"]
    assert lays.selected() == ["L3", "L4"]
    lays.buttons["delete"].click()
    assert [x.id for x in ses.layouts] == ["L1", "L2"]

    from mocapapp.gui.sections import participants as participants_mod

    monkeypatch.setattr(participants_mod, "confirm", lambda *a: True)
    for _ in range(3):
        workflow.add_participant(ses, root)
    win.go("participants")
    people = win.section("participants")
    _select(people.table, [0, 2])
    assert people.buttons["forms"].text() == "Consent forms (2) ..."
    before = [p.code for p in ses.participants]
    people.buttons["random"].click()
    qtbot.waitUntil(lambda: [p.code for p in ses.participants] != before)
    after = [p.code for p in ses.participants]
    assert after[1] == before[1] and after[0] != before[0] and after[2] != before[2]
    qtbot.waitUntil(lambda: people.selected() == ["1", "3"])  # still selected after the table is rebuilt
    people.buttons["remove"].click()
    qtbot.waitUntil(lambda: people.table.rowCount() == 1)
    assert [p.label for p in ses.participants] == ["2"] and [t.participant for t in ses.trials] == ["2"]
