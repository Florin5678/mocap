"""Two camera layouts with real calibration (PROJECT.md §16): cam01 was moved before trial 2, so trial 2 uses a
second layout. Replaces 0.1.0rc13's 'camera positions' (a second extrinsic video per camera)."""

import numpy as np

from mocapapp.cli.main import main
from mocapapp.core import runner, workflow
from mocapapp.core.calibration import service
from mocapapp.core.calibration.extrinsics import load_result
from mocapapp.core.calibration.scene_points import to_csv
from mocapapp.core.session import Layout, load_session, save_session
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


def test_camera_moved_between_trials_uses_a_second_layout(tmp_path, capsys):
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
    layout = Layout(root)
    assert layout.trial_calib("1").is_file() and layout.trial_calib("2").is_file()

    # cam01 was moved before trial 2: a copy of the layout with cam01's new extrinsic video (the other cameras keep
    # theirs) and trial 2 moved to it
    session = load_session(root)
    l1 = session.layout("L1")
    l2 = workflow.duplicate_layout(session, root, "L1")
    for lc in l1.cameras:
        video = moved_video if lc.camera == "cam01" else lc.extrinsic.file
        workflow.set_extrinsic_video(session, root, l2.id, lc.camera, video)
    workflow.set_trial_layout(session, root, "2", l2.id)
    assert not layout.trial_calib("2").exists() and layout.trial_calib("1").is_file()
    save_session(session, root)
    capsys.readouterr()
    assert main(["calibrate", "frame", "--session", s, "--camera", "cam01"]) == 1  # in two layouts now
    assert "--layout" in capsys.readouterr().out
    assert main(["prepare", "--session", s]) == 0
    assert layout.layout_input("L2", "cam01").is_file()
    assert main(["calibrate", "frame", "--session", s, "--camera", "cam01", "--layout", "L2"]) == 0
    assert "extrinsic_cam01_f0.png" in capsys.readouterr().out
    for cam, pos in {**CAMS, "cam01": MOVED}.items():
        assert main(["calibrate", "extrinsics", "--session", s, "--camera", cam, "--layout", "L2",
                     "--points", _clicks(tmp_path, scene, f"{cam}_L2", pos)]) == 0
    np.testing.assert_allclose(load_result(layout.layout_work("L2"), "cam01").camera_position, MOVED, atol=0.1)
    np.testing.assert_allclose(load_result(layout.layout_work("L1"), "cam01").camera_position, CAMS["cam01"],
                               atol=0.1)

    # each trial gets its own layout's calibration
    assert main(["assemble", "--session", s]) == 0
    t1 = (layout.trial("1") / "calibration" / "Calib_scene.toml").read_text()
    t2 = (layout.trial("2") / "calibration" / "Calib_scene.toml").read_text()
    assert t1 == layout.trial_calib("1").read_text() and t2 == layout.trial_calib("2").read_text() and t1 != t2
    assert [p.name for p in (layout.trial("2") / "calibration").iterdir()] == ["Calib_scene.toml"]

    # trial 2 finished; a new extrinsic video for cam01 in its layout makes its 3D out of date
    session = load_session(root)
    assert "cam01:cam01:" in runner.stage_inputs(session, "triangulation", "2")
    session.run_state.stage_done["2"] = list(runner.STAGES)
    session.run_state.stage_settings["2"] = {st: runner.stage_inputs(session, st, "2") for st in runner.STAGES}
    session.run_state.trial_status["2"] = "done"
    assert not runner.stale_stages(session, "2")
    workflow.set_extrinsic_video(session, root, "L2", "cam01", raw / "cam01" / "extrinsic.mp4")
    assert session.run_state.trial_status["2"] == "partial"
    assert runner.stale_stages(session, "2")[0] == "triangulation"
    assert service.layout_state(session, root, "L2")["Layout 1 (copy): cam01"] == "extrinsics missing"
    assert service.layout_state(session, root, "L1") == {f"Layout 1: {c}": "ready" for c in CAMS}

    # reference points typed in centimetres but read as metres (0.1.0rc15): choosing the unit recomputes every
    # camera position of that layout from the clicks already made, so nothing is clicked again
    save_session(session, root)
    cm = tmp_path / "points_cm.csv"
    cm.write_text(to_csv(scene.points, "cm"))
    work = layout.layout_work("L1")
    assert main(["scene", "set", "--session", s, "--layout", "L1", "--csv", str(cm)]) == 0  # 100 times too large
    np.testing.assert_allclose(load_result(work, "cam01").camera_position, np.array(CAMS["cam01"]) * 100, rtol=0.05)
    capsys.readouterr()
    assert main(["scene", "set", "--session", s, "--layout", "L1", "--csv", str(cm), "--unit", "cm"]) == 0
    assert capsys.readouterr().out.count("recomputed from your clicks") == len(CAMS)
    for cam, pos in CAMS.items():
        np.testing.assert_allclose(load_result(work, cam).camera_position, pos, atol=0.1)
    session = load_session(root)
    assert service.layout_scene(session, "L1").unit == "cm" and service.layout_scene(session, "L2").unit == "m"
    assert layout.trial_calib("1").is_file()
