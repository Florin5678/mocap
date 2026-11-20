"""A camera moved between trials (0.1.0rc13): a second extrinsic video for the trials recorded after the move."""

import numpy as np

from mocapapp.cli.main import main
from mocapapp.core import runner
from mocapapp.core.calibration import service
from mocapapp.core.calibration.extrinsics import load_result
from mocapapp.core.calibration.scene_points import to_csv
from mocapapp.core.session import Layout, load_session
from tests.conftest import make_video
from tests.test_calibration import board_views, good_scene, look_at, project, write_video

CAMS = {"cam01": [-2.0, -1.5, 1.6], "cam02": [5.0, -1.5, 1.6], "cam03": [1.5, 4.0, 2.0]}
MOVED = [-2.4, -0.6, 1.8]  # cam01 after it was knocked, before trial 2


def _clicks(tmp_path, scene, name, pos):
    r, t = look_at(np.array(pos), [1.5, 1.0, 0.6])
    uv = project(scene.array(), r, t)
    csv = tmp_path / f"{name}.csv"
    csv.write_text("\n".join(["label,u,v"] + [f"{p.label},{u:.2f},{v:.2f}"
                                              for p, (u, v) in zip(scene.points, uv, strict=True)]))
    return str(csv)


def test_camera_moved_between_trials(tmp_path, capsys):
    raw = tmp_path / "raw"
    for i, cam in enumerate(CAMS, 1):
        d = raw / cam
        d.mkdir(parents=True)
        frames = board_views(n=24)
        write_video(frames[i:] + frames[:i], d / "intrinsic.mp4")
        make_video(d / "extrinsic.mp4", seconds=0.3 + 0.05 * i, size="1280x720", pattern="smptebars")
        for n in (1, 2):
            make_video(d / f"trial{n}.mp4", seconds=0.4 + 0.05 * i + 0.1 * n, size="1280x720")
    moved_video = tmp_path / "moved" / "cam01_after.mp4"  # anywhere, not in the camera folder
    moved_video.parent.mkdir()
    make_video(moved_video, seconds=0.3, size="1280x720", pattern="smptebars")
    out = tmp_path / "sessions"
    args = ["session", "new", "--trials", "2", "--heights", "1.75,1.80", "--out", str(out)]
    for cam in CAMS:
        args += ["--cam", f"{cam}={raw / cam}"]
    assert main(args) == 0
    root = next(out.iterdir())
    s = str(root)
    assert main(["prepare", "--session", s]) == 0
    assert main(["calibrate", "intrinsics", "--session", s, "--every", "0.5"]) == 0
    scene = good_scene()
    (tmp_path / "points.csv").write_text(to_csv(scene.points))
    assert main(["scene", "set", "--session", s, "--csv", str(tmp_path / "points.csv"),
                 "--origin", "tape cross", "--x-axis", "the window"]) == 0
    for cam, pos in CAMS.items():
        assert main(["calibrate", "extrinsics", "--session", s, "--camera", cam,
                     "--points", _clicks(tmp_path, scene, cam, pos)]) == 0
    calib = Layout(root).calib_file
    assert calib.is_file()
    capsys.readouterr()

    # the exceptional case: cam01 was moved before trial 2
    assert main(["calibrate", "position", "--session", s, "--camera", "cam01", "--add", str(moved_video),
                 "--trials", "2"]) == 0
    assert "cam01 position 2 added for trial(s) 2" in capsys.readouterr().out
    assert (Layout(root).inputs("cam01") / "extrinsic_2.mp4").is_file()
    assert main(["calibrate", "status", "--session", s]) == 0
    assert "cam01 position 2: extrinsics missing" in capsys.readouterr().out
    assert not calib.exists()  # not every position is calibrated: no run until it is

    assert main(["calibrate", "frame", "--session", s, "--camera", "cam01", "--position", "2"]) == 0
    assert "extrinsic_cam01_pos2_f0.png" in capsys.readouterr().out
    assert main(["calibrate", "extrinsics", "--session", s, "--camera", "cam01", "--position", "2",
                 "--points", _clicks(tmp_path, scene, "cam01_moved", MOVED)]) == 0
    assert "All cameras calibrated" in capsys.readouterr().out
    work = Layout(root).calibration_work
    np.testing.assert_allclose(load_result(work, "cam01", "2").camera_position, MOVED, atol=0.1)
    np.testing.assert_allclose(load_result(work, "cam01").camera_position, CAMS["cam01"], atol=0.1)

    # trial 1 gets the main calibration, trial 2 its own
    assert main(["assemble", "--session", s]) == 0
    t1 = (Layout(root).trial("1") / "calibration" / "Calib_scene.toml").read_text()
    t2 = (Layout(root).trial("2") / "calibration" / "Calib_scene.toml").read_text()
    assert t1 == calib.read_text() and t2 == Layout(root).trial_calib("2").read_text() and t1 != t2
    assert [p.name for p in (Layout(root).trial("2") / "calibration").iterdir()] == ["Calib_scene.toml"]

    session = load_session(root)
    assert runner.stage_inputs(session, "triangulation", "1") == "[]"
    assert "cam01:2:" in runner.stage_inputs(session, "triangulation", "2")
    session.run_state.stage_done["2"] = list(runner.STAGES)  # pretend trial 2 finished, then the trials change
    session.run_state.stage_settings["2"] = {st: runner.stage_inputs(session, st, "2") for st in runner.STAGES}
    session.run_state.trial_status["2"] = "done"
    from mocapapp.core.session import save_session

    save_session(session, root)
    assert main(["calibrate", "position", "--session", s, "--camera", "cam01", "--position", "2", "--remove"]) == 0
    session = load_session(root)
    assert session.camera("cam01").positions == [] and session.run_state.trial_status["2"] == "partial"
    assert runner.stale_stages(session, "2")[0] == "triangulation"  # its 3D is recomputed with the main position
    assert not Layout(root).trial_calib("2").exists() and service.calibration_state(session, root) == {
        c: "ready" for c in CAMS}
    assert (Layout(root).trial("2") / "calibration" / "Calib_scene.toml").read_text() == calib.read_text()

    # reference points typed in centimetres but read as metres (0.1.0rc15): choosing the unit recomputes every
    # camera position from the clicks already made, so nothing is clicked again
    cm = tmp_path / "points_cm.csv"
    cm.write_text(to_csv(scene.points, "cm"))
    assert main(["scene", "set", "--session", s, "--csv", str(cm)]) == 0  # read as metres: 100 times too large
    np.testing.assert_allclose(load_result(work, "cam01").camera_position, np.array(CAMS["cam01"]) * 100, rtol=0.05)
    capsys.readouterr()
    assert main(["scene", "set", "--session", s, "--csv", str(cm), "--unit", "cm"]) == 0
    assert capsys.readouterr().out.count("recomputed from your clicks") == len(CAMS)
    for cam, pos in CAMS.items():
        np.testing.assert_allclose(load_result(work, cam).camera_position, pos, atol=0.1)
    session = load_session(root)
    assert service.get_scene(root).unit == "cm" and len(session.extrinsics) == len(CAMS)
    assert calib.is_file() and service.calibration_state(session, root) == {c: "ready" for c in CAMS}
