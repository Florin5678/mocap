"""Calibration through the CLI on a synthetic 3-camera session with known cameras."""

import numpy as np
import rtoml

from mocapapp.cli.main import main
from mocapapp.core.calibration.scene_points import to_csv
from mocapapp.core.session import Layout, load_session
from tests.conftest import make_video
from tests.test_calibration import board_views, good_scene, look_at, project, write_video

CAMS = {"cam01": [-2.0, -1.5, 1.6], "cam02": [5.0, -1.5, 1.6], "cam03": [1.5, 4.0, 2.0]}


def test_calibration_flow(tmp_path, capsys):
    raw = tmp_path / "raw"
    for i, cam in enumerate(CAMS, 1):
        d = raw / cam
        d.mkdir(parents=True)
        frames = board_views(n=24)
        frames = frames[i:] + frames[:i]  # different recording per camera
        write_video(frames, d / "intrinsic.mp4")
        make_video(d / "extrinsic.mp4", seconds=0.3 + 0.05 * i, size="1280x720", pattern="smptebars")
        make_video(d / "trial1.mp4", seconds=0.5 + 0.05 * i, size="1280x720")
    out = tmp_path / "sessions"
    args = ["session", "new", "--trials", "1", "--heights", "1.75", "--out", str(out)]
    for cam in CAMS:
        args += ["--cam", f"{cam}={raw / cam}"]
    assert main(args) == 0
    root = next(out.iterdir())
    s = str(root)
    assert main(["prepare", "--session", s]) == 0

    assert main(["calibrate", "intrinsics", "--session", s, "--every", "0.5"]) == 0
    text = capsys.readouterr().out
    assert text.count("-> OK") + text.count("-> WARN") == 3, text

    scene = good_scene()
    (tmp_path / "points.csv").write_text(to_csv(scene.points))
    assert main(["scene", "set", "--session", s, "--csv", str(tmp_path / "points.csv"),
                 "--origin", "tape cross by the door", "--x-axis", "the window"]) == 0
    assert main(["scene", "check", "--session", s]) == 0

    # click the projections of the true cameras, using each camera's own intrinsics
    from mocapapp.core.calibration.intrinsics import load_result

    work, lwork = Layout(root).cameras_work, Layout(root).layout_work("L1")
    for cam, pos in CAMS.items():
        r, t = look_at(np.array(pos), [1.5, 1.0, 0.6])
        uv = project(scene.array(), r, t)
        lines = ["label,u,v"] + [f"{p.label},{u:.2f},{v:.2f}" for p, (u, v) in zip(scene.points, uv, strict=True)]
        lines[3] = f"{scene.points[2].label},hidden,"
        csv = tmp_path / f"{cam}_clicks.csv"
        csv.write_text("\n".join(lines))
        assert main(["calibrate", "frame", "--session", s, "--camera", cam]) == 0
        assert main(["calibrate", "extrinsics", "--session", s, "--camera", cam, "--points", str(csv)]) == 0
        assert load_result(work, cam) is not None
    text = capsys.readouterr().out
    assert "Calibration written for 1 trial(s)" in text

    calib = Layout(root).trial_calib("1")
    data = rtoml.load(calib)
    assert list(data)[:3] == ["cam01", "cam02", "cam03"]
    session = load_session(root)
    lay = session.layout("L1")
    assert lay.camera_ids() == list(CAMS)
    for lc in lay.cameras:
        assert lc.result["status"] in ("ok", "warn") and lc.result["error_cm"] < 2.5
    assert all(c.intrinsic.status in ("ok", "warn") for c in session.cameras)
    # recovered camera position close to the truth (intrinsics come from the synthetic board video)
    from mocapapp.core.calibration.extrinsics import load_result as load_ext

    np.testing.assert_allclose(load_ext(lwork, "cam01").camera_position, CAMS["cam01"], atol=0.1)

    # trials get the calibration; a corrected reference point recomputes the camera positions from the clicks
    # already made and rewrites the calibration everywhere (0.1.0rc15; before, every camera was clicked again)
    assert main(["assemble", "--session", s]) == 0
    trial_calib = Layout(root).trial("1") / "calibration" / "Calib_scene.toml"
    assert trial_calib.read_text() == calib.read_text()
    before = calib.read_text()
    scene.points[0].x += 0.1
    (tmp_path / "points.csv").write_text(to_csv(scene.points))
    capsys.readouterr()
    assert main(["scene", "set", "--session", s, "--csv", str(tmp_path / "points.csv")]) == 0
    assert capsys.readouterr().out.count("recomputed from your clicks") == len(CAMS)
    assert calib.read_text() != before and trial_calib.read_text() == calib.read_text()

    # a removed point's clicks are dropped; too few left and that camera is clicked again
    scene.points = scene.points[:4]
    (tmp_path / "points.csv").write_text(to_csv(scene.points))
    assert main(["scene", "set", "--session", s, "--csv", str(tmp_path / "points.csv")]) == 1  # too few points
    assert "Click the points again" in capsys.readouterr().out
    assert not calib.exists() and not trial_calib.exists()
    assert main(["calibrate", "status", "--session", s]) == 0
    assert "extrinsics missing" in capsys.readouterr().out
