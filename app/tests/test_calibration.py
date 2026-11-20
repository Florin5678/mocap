"""Calibration on synthetic data with a known camera."""

import subprocess

import cv2
import numpy as np
import pytest

from mocapapp.core.calibration import extrinsics as ex
from mocapapp.core.calibration import intrinsics as intr
from mocapapp.core.calibration.calibfile import write_calib
from mocapapp.core.calibration.scene_points import Scene, ScenePoint, check_scene, from_csv, from_snippet, to_csv
from mocapapp.core.media import ffmpeg_exe

W, H = 1280, 720
K_TRUE = np.array([[1000.0, 0, 640], [0, 1000.0, 360], [0, 0, 1]])
SPEC = intr.BoardSpec("charuco", (5, 8), 25.0, 4)


# ---------- scene ----------

def room_points():
    """12 points in a 3 x 2 m room, floor crosses plus raised box corners."""
    floor = [(x, y, 0.0) for x in (0.0, 1.5, 3.0) for y in (0.0, 2.0)]
    raised = [(0.5, 0.5, 0.45), (2.5, 0.5, 0.45), (0.5, 1.5, 0.9), (2.5, 1.5, 0.9), (1.5, 1.0, 1.2), (1.0, 1.8, 0.7)]
    return [ScenePoint(label=f"P{i + 1}", x=x, y=y, z=z) for i, (x, y, z) in enumerate(floor + raised)]


def good_scene():
    return Scene(origin_text="tape cross by the door", x_axis_text="the window", points=room_points())


def errors(issues):
    return [i for i in issues if i.level == "error"]


def test_scene_ok():
    issues = check_scene(good_scene())
    assert not errors(issues) and not [i for i in issues if i.level == "warning"]


def test_scene_rules():
    s = good_scene()
    s.points = s.points[:9]
    assert any("at least 10" in i.what for i in errors(check_scene(s)))
    s = good_scene()
    s.points[1].label = s.points[0].label
    assert any("same label" in i.what for i in errors(check_scene(s)))
    s = good_scene()
    s.points = [ScenePoint(label=f"F{i}", x=i * 0.3, y=(i % 3) * 0.5, z=0) for i in range(10)]
    assert any("nearly flat" in i.what for i in check_scene(s))
    s = good_scene()
    s.points = [ScenePoint(label=f"L{i}", x=i * 0.3, y=0, z=0) for i in range(10)]
    assert any("one line" in i.what for i in errors(check_scene(s)))
    s = good_scene()
    s.points[0].x = 150
    assert any("centimetres" in i.fix for i in check_scene(s))
    assert errors(check_scene(Scene(points=room_points())))  # origin and +X text required


def test_scene_import_export():
    pts = room_points()
    assert [p.xyz for p in from_csv(to_csv(pts))] == [p.xyz for p in pts]
    # another unit (0.1.0rc15): the CSV holds that unit, the points stay in metres
    text = to_csv(pts, "cm")
    assert text.startswith("label,x_cm,y_cm,z_cm")
    assert np.allclose([p.xyz for p in from_csv(text, "cm")], [p.xyz for p in pts])
    assert from_csv("a,1,2,12\n", "ft")[0].xyz == pytest.approx([0.3048, 0.6096, 3.6576])
    assert from_csv("a,10,0,0\n", "in")[0].x == pytest.approx(0.254)
    assert Scene.model_validate({"points": []}).unit == "m"  # scene.json from before the unit choice
    snippet = "object_coords_3d = [[0.1, 0.48, 0.0], # closest spot\n [-0.42, 0.31, 0.0]]  # 2nd"
    parsed = from_snippet(snippet)
    assert parsed[0].label == "closest spot" and parsed[1].xyz == [-0.42, 0.31, 0.0]


# ---------- extrinsics ----------

def project(points, r, t, K=K_TRUE, D=None):
    D = np.zeros(4) if D is None else D
    return cv2.projectPoints(np.array(points, float), np.array(r, float), np.array(t, float), K, D)[0].reshape(-1, 2)


def look_at(cam_pos, target):
    """Rodrigues r and t for a camera at cam_pos looking at target, world Z up."""
    z = np.array(target, float) - cam_pos
    z /= np.linalg.norm(z)
    x = np.cross(z, [0, 0, 1.0])
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    R = np.vstack([x, y, z])
    return cv2.Rodrigues(R)[0].ravel(), -R @ cam_pos


def clicks_for(scene, r, t, noise=0.0, seed=0):
    uv = project(scene.array(), r, t) + np.random.default_rng(seed).normal(0, noise, (len(scene.points), 2))
    return [ex.Click(p.label, float(u), float(v)) for p, (u, v) in zip(scene.points, uv, strict=True)]


def test_extrinsics_recovers_known_camera():
    scene = good_scene()
    cam_pos = np.array([-2.0, -1.5, 1.6])
    r, t = look_at(cam_pos, [1.5, 1.0, 0.6])
    res = ex.solve_camera("cam01", scene, clicks_for(scene, r, t, noise=0.3), K_TRUE, np.zeros(4))
    assert res.status == "ok" and res.rms_px < 0.6 and res.rms_cm < 0.2
    np.testing.assert_allclose(res.camera_position, cam_pos, atol=0.02)
    assert not res.warnings


def test_extrinsics_flags_bad_point_and_mirrored_axes():
    scene = good_scene()
    r, t = look_at(np.array([-2.0, -1.5, 1.6]), [1.5, 1.0, 0.6])
    clicks = clicks_for(scene, r, t, noise=0.3)
    clicks[4] = ex.Click(clicks[4].label, clicks[4].u + 60, clicks[4].v - 40)
    res = ex.solve_camera("cam01", scene, clicks, K_TRUE, np.zeros(4))
    assert any(f"'{clicks[4].label}'" in w for w in res.warnings)

    mirrored = good_scene()
    for p in mirrored.points:
        p.y = -p.y  # measured with a left-handed frame
    res = ex.solve_camera("cam01", mirrored, clicks_for(scene, r, t), K_TRUE, np.zeros(4))
    assert res.status == "fail" and any("flip Y" in w for w in res.warnings)


def test_extrinsics_needs_six_points():
    scene = good_scene()
    r, t = look_at(np.array([-2.0, -1.5, 1.6]), [1.5, 1.0, 0.6])
    clicks = clicks_for(scene, r, t)
    for c in clicks[5:]:
        c.u = c.v = None
    with pytest.raises(ex.ExtrinsicsError, match="at least 6"):
        ex.solve_camera("cam01", scene, clicks, K_TRUE, np.zeros(4))


def test_clicks_csv():
    clicks = ex.read_clicks_csv("label,u,v\nP1,10.5,20\nP2,hidden,\nP3,,\n")
    assert clicks[0].visible and not clicks[1].visible and not clicks[2].visible


# ---------- intrinsics on a synthetic ChArUco video ----------

def board_views(legacy=True, n=24, mirror=False):
    board = intr.charuco_board(SPEC, 50, legacy)
    px_per_mm = 4
    bw, bh = 9 * 25 * px_per_mm, 6 * 25 * px_per_mm
    img = board.generateImage((bw, bh), marginSize=40)
    img = cv2.copyMakeBorder(img, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
    h0, w0 = img.shape
    # board plane in mm, centred; image pixel -> board mm
    S = np.array([[1 / px_per_mm, 0, -w0 / 2 / px_per_mm], [0, 1 / px_per_mm, -h0 / 2 / px_per_mm], [0, 0, 1]])
    rng = np.random.default_rng(1)
    frames = []
    for _ in range(n):
        rx, ry = np.radians(rng.uniform(-40, 40, 2))
        rz = np.radians(rng.uniform(-20, 20))
        R = cv2.Rodrigues(np.array([rx, ry, rz]))[0]
        dist = rng.uniform(350, 650)
        u0, v0 = rng.uniform(-0.25, 0.25) * dist, rng.uniform(-0.2, 0.2) * dist
        t = np.array([u0, v0, dist])
        Hm = K_TRUE @ np.column_stack([R[:, 0], R[:, 1], t]) @ S
        frame = cv2.warpPerspective(img, Hm, (W, H), borderValue=200, flags=cv2.INTER_AREA)
        if mirror:
            frame = cv2.flip(frame, 1)
        frames.append(cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR))
    return frames


def write_video(frames, path, fps=2):
    proc = subprocess.Popen([ffmpeg_exe(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s",
                             f"{W}x{H}", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-crf", "8", "-pix_fmt",
                             "yuv420p", str(path)], stdin=subprocess.PIPE)
    for f in frames:
        proc.stdin.write(f.tobytes())
    proc.stdin.close()
    assert proc.wait() == 0
    return path


@pytest.mark.parametrize("legacy", [True, False])
def test_intrinsics_from_synthetic_video(tmp_path, legacy):
    video = write_video(board_views(legacy=legacy), tmp_path / "intrinsic.mp4")
    res = intr.calibrate_video("cam01", video, SPEC, every_n_sec=0.5, min_frames=15)
    K = np.array(res.matrix)
    assert res.rms_px < 0.6, res
    assert abs(K[0, 0] - 1000) < 15 and abs(K[1, 1] - 1000) < 15
    assert abs(K[0, 2] - 640) < 15 and abs(K[1, 2] - 360) < 15
    assert res.n_used >= 15 and not res.mirrored
    assert ("legacy" in res.layout) == legacy


def test_intrinsics_detects_mirrored_video(tmp_path):
    video = write_video(board_views(mirror=True, n=16), tmp_path / "intrinsic.mp4")
    res = intr.calibrate_video("cam01", video, SPEC, every_n_sec=0.5, min_frames=10)
    assert res.mirrored and any("mirrored" in a for a in res.advice)
    assert abs(np.array(res.matrix)[0, 0] - 1000) < 20


def test_intrinsics_fails_with_wrong_board(tmp_path):
    video = write_video(board_views(n=6), tmp_path / "intrinsic.mp4")
    wrong = intr.BoardSpec("charuco", (4, 7), 25.0, 5)
    with pytest.raises(intr.IntrinsicsError, match="board settings"):
        intr.calibrate_video("cam01", video, wrong, every_n_sec=0.5)


def test_grade_thresholds():
    assert intr.grade(SPEC, 0.4, 30, 0.9, 15, False)[0] == "ok"
    assert intr.grade(SPEC, 0.8, 30, 0.9, 15, False)[0] == "warn"
    assert intr.grade(SPEC, 1.2, 30, 0.9, 15, False)[0] == "fail"
    assert intr.grade(SPEC, 0.4, 12, 0.9, 15, False)[0] == "fail"


# ---------- calibration file ----------

def test_calib_file_round_trip_with_upstream_reader(tmp_path):
    scene = good_scene()
    intrs, extrs = [], []
    for i, pos in enumerate(([-2.0, -1.5, 1.6], [5.0, -1.5, 1.6], [1.5, 4.0, 2.0]), 1):
        cam = f"cam{i:02d}"
        r, t = look_at(np.array(pos), [1.5, 1.0, 0.6])
        intrs.append(intr.IntrinsicsResult(cam, "v", "h", [W, H], K_TRUE.tolist(), [0.01, -0.02, 0.001, 0.0], 0.4,
                                           30, 30, 0.9, 15, "charuco", False, "ok"))
        extrs.append(ex.solve_camera(cam, scene, clicks_for(scene, r, t), K_TRUE, np.zeros(4)))
    path = write_calib(tmp_path / "Calib_scene.toml", intrs, extrs)
    text = path.read_text()
    assert "[cam01]" in text and text.index("[cam01]") < text.index("[cam02]") < text.index("[cam03]")

    # upstream triangulation from the written file recovers a 3D point
    from Pose2Sim.common import computeP, weighted_triangulation

    P = computeP(path)
    X = np.array([1.2, 0.8, 1.0])
    uv = [project([X], e.rotation, e.translation, K_TRUE, np.array([0.01, -0.02, 0.001, 0.0]))[0] for e in extrs]
    uv_undist = [cv2.undistortPoints(np.array([[p]], float), K_TRUE, np.array([0.01, -0.02, 0.001, 0.0]),
                                     P=K_TRUE).ravel() for p in uv]
    Q = weighted_triangulation(P, [p[0] for p in uv_undist], [p[1] for p in uv_undist], [1, 1, 1])
    np.testing.assert_allclose(np.array(Q[:3]), X, atol=1e-3)
