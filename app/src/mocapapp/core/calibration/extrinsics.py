"""Extrinsic (camera position) calibration from clicked reference points (PROJECT.md §6.5).

Same maths as upstream `calibrate_extrinsics` ('scene'): solvePnP(object_points * 1000, image_points, K, D),
t / 1000, RMS of the clicked points' reprojection distances. Adds diagnostics for the mistakes seen in practice:
mirrored/left-handed axes, mistyped or misclicked single points, and impossible camera positions.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

from mocapapp.core.calibration.scene_points import MIN_CLICKED, Scene
from mocapapp.core.session import atomic_write_text

PASS_CM, WARN_CM = 1.0, 2.5
OUTLIER_FACTOR = 3.0  # a point whose error is > 3x the median of the others is reported
FLIP_IMPROVEMENT = 3.0  # a flip must cut the error at least 3x to be suggested


class ExtrinsicsError(Exception):
    pass


@dataclass
class Click:
    label: str
    u: float | None  # None = not visible in this camera
    v: float | None

    @property
    def visible(self) -> bool:
        return self.u is not None and self.v is not None


@dataclass
class ExtrinsicsResult:
    camera: str
    frame_index: int
    rotation: list[float]  # Rodrigues, world -> camera
    translation: list[float]  # metres
    n_points: int
    rms_px: float
    rms_cm: float
    camera_position: list[float]  # metres, world frame
    per_point_px: dict[str, float]
    reprojected: dict[str, list[float]]  # every scene point, hidden ones included
    status: str  # ok | warn | fail
    warnings: list[str] = field(default_factory=list)
    overridden: bool = False
    clicks: list[dict] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, text: str) -> ExtrinsicsResult:
        return cls(**json.loads(text))


def read_frame(video: Path, index: int = 0) -> np.ndarray:
    cap = cv2.VideoCapture(str(video))
    try:
        if index:
            cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = cap.read()
    finally:
        cap.release()
    if not ok:
        raise ExtrinsicsError(f"Cannot read frame {index} of {video}.")
    return frame


def _solve(objp_m: np.ndarray, imgp: np.ndarray, K: np.ndarray, D: np.ndarray):
    ok, r, t = cv2.solvePnP(objp_m * 1000, imgp, K, D)
    if not ok:
        raise ExtrinsicsError("The camera position could not be computed from these points.")
    r, t = r.flatten(), t.flatten() / 1000
    proj = cv2.projectPoints(objp_m, r, t, K, D)[0].reshape(-1, 2)
    dists = np.linalg.norm(proj - imgp.reshape(-1, 2), axis=1)
    rms = float(np.sqrt(np.mean(dists ** 2)))
    return r, t, dists, rms


def depths(objp_m: np.ndarray, r, t) -> np.ndarray:
    """Distance of each point in front of the camera; negative = behind it."""
    R = cv2.Rodrigues(np.asarray(r, float))[0]
    return (objp_m @ R.T + np.asarray(t, float))[:, 2]


def _rms_cm(rms_px: float, objp_m: np.ndarray, r, t, K) -> float:
    """Pixel error converted to centimetres at the mean distance of the clicked points from the camera."""
    R = cv2.Rodrigues(r)[0]
    depth = float(np.mean((objp_m @ R.T + t)[:, 2]))
    f = float((K[0, 0] + K[1, 1]) / 2)
    return rms_px * abs(depth) / f * 100


def camera_position(r, t) -> np.ndarray:
    R = cv2.Rodrigues(np.asarray(r, float))[0]
    return -R.T @ np.asarray(t, float)


def solve_camera(camera: str, scene: Scene, clicks: list[Click], K, D, frame_index: int = 0) -> ExtrinsicsResult:
    K, D = np.asarray(K, float), np.asarray(D, float)
    pts = {p.label: p for p in scene.points}
    visible = [c for c in clicks if c.visible]
    unknown = [c.label for c in visible if c.label not in pts]
    if unknown:
        raise ExtrinsicsError(f"{camera}: clicked labels not in the reference points: {', '.join(unknown)}.")
    if len(visible) < MIN_CLICKED:
        raise ExtrinsicsError(f"{camera}: {len(visible)} points clicked; at least {MIN_CLICKED} are needed "
                              "(10 or more, spread over the picture, recommended).")
    objp = np.array([pts[c.label].xyz for c in visible], dtype=np.float64)
    imgp = np.array([[c.u, c.v] for c in visible], dtype=np.float64)
    r, t, dists, rms = _solve(objp, imgp, K, D)
    rms_cm = _rms_cm(rms, objp, r, t, K)
    warnings: list[str] = []

    # single bad points: leave-one-out
    if len(visible) > MIN_CLICKED:
        med = float(np.median(dists))
        for i, c in enumerate(visible):
            if dists[i] > OUTLIER_FACTOR * max(med, 1.0):
                keep = [j for j in range(len(visible)) if j != i]
                _, _, _, rms_wo = _solve(objp[keep], imgp[keep], K, D)
                if rms_wo < rms / 2:
                    warnings.append(f"Point '{c.label}' is off by {dists[i]:.0f} px; without it the error drops from "
                                    f"{rms:.1f} to {rms_wo:.1f} px. Check that it was clicked on the right spot and "
                                    "that its measured coordinates are right.")

    # Mirrored (left-handed) coordinates fit perfectly with the points *behind* the camera: projection cannot
    # tell X from -X. Upstream does not check this; the error alone would look excellent.
    behind = int(np.sum(depths(objp, r, t) <= 0))
    if behind:
        warnings.append(f"{behind} of {len(visible)} points end up behind the camera. "
                        + (mirror_hint(objp, imgp, K, D, None) or "Check the signs of the coordinates and the "
                           "click order."))
    elif rms_cm > WARN_CM:
        hint = mirror_hint(objp, imgp, K, D, rms)
        if hint:
            warnings.append(hint)

    pos = camera_position(r, t)
    if pos[2] <= 0:
        warnings.append(f"The computed camera position is at Z = {pos[2]:.2f} m, below the floor. Check that Z is up "
                        "(positive heights) and that points were clicked in the right order.")
    if len(visible) < 10:
        warnings.append(f"Only {len(visible)} points visible; 10 or more spread over the picture give a more "
                        "reliable result.")

    status = "fail" if behind else "ok" if rms_cm < PASS_CM else "warn" if rms_cm <= WARN_CM else "fail"
    all_proj = cv2.projectPoints(scene.array(), r, t, K, D)[0].reshape(-1, 2)
    return ExtrinsicsResult(
        camera=camera, frame_index=frame_index, rotation=[float(v) for v in r], translation=[float(v) for v in t],
        n_points=len(visible), rms_px=round(rms, 3), rms_cm=round(rms_cm, 3),
        camera_position=[round(float(v), 4) for v in pos],
        per_point_px={c.label: round(float(d), 2) for c, d in zip(visible, dists, strict=True)},
        reprojected={p.label: [round(float(x), 1), round(float(y), 1)] for p, (x, y) in zip(scene.points, all_proj,
                                                                                            strict=True)},
        status=status, warnings=warnings, clicks=[asdict(c) for c in clicks])


def mirror_hint(objp, imgp, K, D, rms: float | None) -> str | None:
    """Try sign flips and the X/Y swap of the world axes; suggest one if it removes most of the error.

    With rms=None (points behind the camera) any variant that puts all points in front with a small error wins.
    """
    candidates = []
    for flips in itertools.product((1, -1), repeat=3):
        for swap in (False, True):
            if flips == (1, 1, 1) and not swap:
                continue
            o = objp[:, [1, 0, 2]] if swap else objp.copy()
            o = o * np.array(flips)
            try:
                rv, tv, _, r2 = _solve(o, imgp, K, D)
            except (ExtrinsicsError, cv2.error):
                continue
            if np.any(depths(o, rv, tv) <= 0):
                continue
            candidates.append((r2, flips, swap))
    if not candidates:
        return None
    best_err = min(c[0] for c in candidates)
    if rms is not None and best_err * FLIP_IMPROVEMENT > rms:
        return None

    # Several variants fit equally well (for a mirror, flipping any one axis does). Suggest the simplest and the
    # most common mistake: +Y on the wrong side, then X; Z last because heights are rarely negated.
    def cost(c):
        _, flips, swap = c
        return (sum(f < 0 for f in flips) + swap, {(1, -1, 1): 0, (-1, 1, 1): 1}.get(flips, 2), flips[2] < 0)

    near = sorted((c for c in candidates if c[0] <= best_err * 1.1 + 0.5), key=cost)
    err, flips, swap = near[0]
    parts = [f"flip {axis}" for axis, f in zip("XYZ", flips, strict=True) if f < 0]
    if swap:
        parts.insert(0, "swap X and Y")
    also = " (flipping X instead works equally well)" if flips == (1, -1, 1) and not swap and any(
        c[1] == (-1, 1, 1) and not c[2] for c in near) else ""
    before = f"from {rms:.1f} " if rms is not None else ""
    return (f"The error drops {before}to {err:.1f} px with all points in front of the camera if you "
            f"{' and '.join(parts)}{also}. The axes you measured with are probably mirrored (left-handed) or "
            "swapped: with Z up, +Y must be 90 degrees counter-clockwise from +X seen from above. "
            "Re-check the reference point coordinates.")


def result_path(work_dir: Path, camera: str, position: str = "") -> Path:
    """position "" = the camera's main extrinsic video; "2", "3", ... = a later position (camera moved)."""
    return work_dir / (f"extrinsics_{camera}_pos{position}.json" if position else f"extrinsics_{camera}.json")


def save_result(result: ExtrinsicsResult, work_dir: Path, position: str = "") -> Path:
    path = result_path(work_dir, result.camera, position)
    atomic_write_text(path, result.to_json() + "\n")
    return path


def load_result(work_dir: Path, camera: str, position: str = "") -> ExtrinsicsResult | None:
    path = result_path(work_dir, camera, position)
    return ExtrinsicsResult.from_json(path.read_text(encoding="utf-8")) if path.is_file() else None


def read_clicks_csv(text: str) -> list[Click]:
    """CSV label,u,v (pixels); an empty u/v or 'hidden' marks a point not visible in this camera."""
    import csv
    import io

    clicks = []
    for n, row in enumerate(csv.reader(io.StringIO(text)), 1):
        if not row or not row[0].strip() or row[0].strip().lower() == "label":
            continue
        label = row[0].strip()
        vals = [v.strip() for v in row[1:3]] + ["", ""]
        if vals[0].lower() in ("", "hidden", "-") or vals[1] == "":
            clicks.append(Click(label, None, None))
            continue
        try:
            clicks.append(Click(label, float(vals[0]), float(vals[1])))
        except ValueError:
            raise ExtrinsicsError(f"Row {n} ({label}): u and v must be pixel numbers or 'hidden'.") from None
    return clicks


def draw_review(frame: np.ndarray, result: ExtrinsicsResult) -> np.ndarray:
    """Clicked points (green crosses) and reprojected reference points (blue dots), for the review screen."""
    img = frame.copy()
    for label, (x, y) in result.reprojected.items():
        cv2.circle(img, (int(x), int(y)), 7, (255, 80, 0), -1)
        cv2.putText(img, label, (int(x) + 9, int(y) - 9), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 80, 0), 2)
    for c in result.clicks:
        if c["u"] is not None:
            x, y = int(c["u"]), int(c["v"])
            cv2.drawMarker(img, (x, y), (0, 220, 0), cv2.MARKER_CROSS, 18, 2)
    return img
