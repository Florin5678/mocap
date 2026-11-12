"""Intrinsic (lens) calibration from the `intrinsic` video of one camera (PROJECT.md §6.3).

Same maths as upstream `calibrate_intrinsics`: cv2.calibrateCamera with CALIB_FIX_K3 | CALIB_USE_LU, 4 distortion
coefficients written to the calibration file. Detection tries both ChArUco layouts (OpenCV >= 4.8 defaults to the
new one; calib.io boards with an even number of rows use the legacy one) and, if nothing is found, a mirrored
picture (front cameras and some phone apps save mirrored video).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

from mocapapp.core.session import Settings, atomic_write_text

PASS_PX = {"charuco": 0.6, "chess": 0.5}
WARN_PX = 1.0
MIN_FRAMES = 20
COVERAGE_GRID = (4, 4)
MIN_COVERAGE = 0.75  # share of image cells touched by at least one detected corner
DICT_SIZES = (50, 100, 250, 1000)
MARKER_RATIO = 0.75  # marker side / square side, as upstream (calib.io boards)

Progress = Callable[[str], None]


class IntrinsicsError(Exception):
    pass


@dataclass
class BoardSpec:
    board_type: str
    corners_nb: tuple[int, int]  # internal corners (rows-1, cols-1)
    square_size_mm: float
    marker_resolution: int = 4

    @classmethod
    def from_settings(cls, s: Settings) -> BoardSpec:
        return cls(s.board_type, tuple(s.corners_nb), s.square_size_mm, s.marker_resolution)

    @property
    def n_corners(self) -> int:
        return self.corners_nb[0] * self.corners_nb[1]

    @property
    def min_corners(self) -> int:
        return max(8, int(0.3 * self.n_corners)) if self.board_type == "charuco" else self.n_corners


def charuco_board(spec: BoardSpec, dict_size: int, legacy: bool):
    dictionary = cv2.aruco.getPredefinedDictionary(
        getattr(cv2.aruco, f"DICT_{spec.marker_resolution}X{spec.marker_resolution}_{dict_size}"))
    board = cv2.aruco.CharucoBoard((spec.corners_nb[1] + 1, spec.corners_nb[0] + 1), spec.square_size_mm,
                                   spec.square_size_mm * MARKER_RATIO, dictionary)
    if hasattr(board, "setLegacyPattern"):
        board.setLegacyPattern(legacy)
    return board


class BoardDetector:
    """Finds board corners; remembers which dictionary/layout/mirroring worked so later frames are fast."""

    def __init__(self, spec: BoardSpec):
        self.spec = spec
        self.variant: tuple[int, bool, bool] | None = None  # (dict size, legacy, mirrored)
        self._detectors: dict[tuple[int, bool], object] = {}

    def _charuco(self, gray, dict_size, legacy):
        key = (dict_size, legacy)
        if key not in self._detectors:
            params = cv2.aruco.DetectorParameters()
            params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
            board = charuco_board(self.spec, dict_size, legacy)
            self._detectors[key] = (board, cv2.aruco.CharucoDetector(board, detectorParams=params))
        board, det = self._detectors[key]
        corners, ids, _, _ = det.detectBoard(gray)
        if ids is None or len(ids) < self.spec.min_corners:
            return None
        objp = board.getChessboardCorners()[ids.flatten()].astype(np.float32)
        return corners.reshape(-1, 1, 2).astype(np.float32), objp

    def _chess(self, gray):
        rows, cols = self.spec.corners_nb
        ok, corners = cv2.findChessboardCorners(gray, (cols, rows), None)
        if not ok:
            return None
        crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
        corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), crit)
        objp = np.zeros((rows * cols, 3), np.float32)
        objp[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * self.spec.square_size_mm
        return corners.astype(np.float32), objp

    def detect(self, gray: np.ndarray):
        """(image points Nx1x2, object points Nx3) or None. Image points are in the original (unmirrored) frame."""
        if self.spec.board_type == "chess":
            variants = [(0, False, False), (0, False, True)]
        else:
            variants = [(d, legacy, mirror) for mirror in (False, True) for legacy in (False, True) for d in DICT_SIZES]
        if self.variant:
            variants = [self.variant]
        for d, legacy, mirror in variants:
            img = cv2.flip(gray, 1) if mirror else gray
            found = self._chess(img) if self.spec.board_type == "chess" else self._charuco(img, d, legacy)
            if found is None:
                continue
            imgp, objp = found
            if mirror:
                imgp = imgp.copy()
                imgp[:, 0, 0] = gray.shape[1] - 1 - imgp[:, 0, 0]
                # mirrored picture: the board seen from the back, flip the board's x to stay right-handed
                objp = objp.copy()
                objp[:, 0] = -objp[:, 0]
            self.variant = (d, legacy, mirror)
            return imgp, objp
        return None


def iter_frames(video: Path, every_n_sec: float) -> Iterator[tuple[int, np.ndarray]]:
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise IntrinsicsError(f"Cannot open {video}.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    step = max(1, round(every_n_sec * fps))
    index = 0
    try:
        while True:
            ok = cap.grab()
            if not ok:
                break
            if index % step == 0:
                ok, frame = cap.retrieve()
                if ok:
                    yield index, frame
            index += 1
    finally:
        cap.release()


@dataclass
class IntrinsicsResult:
    camera: str
    video: str
    video_hash: str
    size: list[int]  # [w, h]
    matrix: list[list[float]]
    distortions: list[float]  # k1, k2, p1, p2 (k3 fixed at 0, as upstream)
    rms_px: float
    n_frames: int
    n_used: int
    coverage: float
    tilt_spread_deg: float
    layout: str
    mirrored: bool
    status: str  # ok | warn | fail
    advice: list[str] = field(default_factory=list)
    used_frames: list[int] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, text: str) -> IntrinsicsResult:
        return cls(**json.loads(text))


def video_hash(path: Path) -> str:
    from mocapapp.core.validate import file_fingerprint

    return file_fingerprint(Path(path))


def _coverage(points: list[np.ndarray], w: int, h: int) -> float:
    gx, gy = COVERAGE_GRID
    hit = np.zeros((gy, gx), bool)
    for p in points:
        xy = p.reshape(-1, 2)
        cx = np.clip((xy[:, 0] / w * gx).astype(int), 0, gx - 1)
        cy = np.clip((xy[:, 1] / h * gy).astype(int), 0, gy - 1)
        hit[cy, cx] = True
    return float(hit.mean())


def _tilt_spread(rvecs) -> float:
    """Spread of board orientations (deg): low values mean the board was always held at the same angle."""
    normals = [cv2.Rodrigues(r)[0][:, 2] for r in rvecs]
    if len(normals) < 2:
        return 0.0
    angles = [np.degrees(np.arccos(np.clip(abs(n[2]), -1, 1))) for n in normals]  # tilt vs. facing the camera
    return float(np.std(angles))


def grade(spec: BoardSpec, rms: float, n_used: int, coverage: float, tilt: float, mirrored: bool,
          min_frames: int = MIN_FRAMES) -> tuple[str, list[str]]:
    advice = []
    if mirrored:
        advice.append("The video looks mirrored (front camera or a mirroring app). Record trials with the same "
                      "camera and settings, or turn mirroring off and record again.")
    if n_used < min_frames:
        advice.append(f"The board was found in only {n_used} frames (need {min_frames}). Record 30-60 s, move the "
                      "board slowly to avoid blur, and hold it close enough to fill about half the picture.")
    if coverage < MIN_COVERAGE:
        advice.append(f"The board covered only {coverage:.0%} of the picture. Move it into every corner and along "
                      "the edges.")
    if tilt < 8:
        advice.append("The board was held at almost the same angle throughout. Tilt it 30-45 degrees in different "
                      "directions.")
    if rms > PASS_PX[spec.board_type]:
        advice.append(f"Reprojection error {rms:.2f} px is above {PASS_PX[spec.board_type]} px. Avoid blur (slow "
                      "movements, good light) and check the board settings and that the print is flat.")
    if n_used < min_frames or rms > WARN_PX:
        return "fail", advice
    if rms > PASS_PX[spec.board_type] or coverage < MIN_COVERAGE or tilt < 8:
        return "warn", advice
    return "ok", advice


def calibrate_video(camera: str, video: Path, spec: BoardSpec, every_n_sec: float = 1.0,
                    min_frames: int = MIN_FRAMES, debug_dir: Path | None = None,
                    progress: Progress = lambda _m: None) -> IntrinsicsResult:
    detector = BoardDetector(spec)
    imgpoints, objpoints, used = [], [], []
    size = None
    n_frames = 0
    for index, frame in iter_frames(video, every_n_sec):
        n_frames += 1
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        size = (gray.shape[1], gray.shape[0])
        found = detector.detect(gray)
        if found is None:
            continue
        imgp, objp = found
        imgpoints.append(imgp)
        objpoints.append(objp)
        used.append(index)
        if debug_dir is not None:
            debug_dir.mkdir(parents=True, exist_ok=True)
            vis = frame.copy()
            for x, y in imgp.reshape(-1, 2):
                cv2.circle(vis, (int(x), int(y)), 4, (0, 255, 0), -1)
            cv2.imwrite(str(debug_dir / f"int_{camera}_{index:05d}.jpg"), vis)
        progress(f"{camera}: frame {index}, {len(imgp)} corners")
    if size is None:
        raise IntrinsicsError(f"No frames could be read from {video}.")
    if len(imgpoints) < 3:
        raise IntrinsicsError(
            f"{camera}: the board was found in {len(imgpoints)} of {n_frames} frames. Check the board settings "
            f"({spec.board_type}, {spec.corners_nb[0]}x{spec.corners_nb[1]} internal corners, "
            f"{spec.marker_resolution}x{spec.marker_resolution} markers) and record the board closer and slower.")
    flags = cv2.CALIB_FIX_K3 + cv2.CALIB_USE_LU
    rms, mtx, dist, rvecs, _ = cv2.calibrateCamera(objpoints, imgpoints, size, None, None, flags=flags)
    coverage = _coverage(imgpoints, *size)
    tilt = _tilt_spread(rvecs)
    d, legacy, mirrored = detector.variant
    status, advice = grade(spec, rms, len(imgpoints), coverage, tilt, mirrored, min_frames)
    layout = "chess" if spec.board_type == "chess" else (
        f"charuco DICT_{spec.marker_resolution}X{spec.marker_resolution}_{d}, {'legacy' if legacy else 'new'} layout")
    return IntrinsicsResult(
        camera=camera, video=str(video), video_hash=video_hash(video), size=[int(size[0]), int(size[1])],
        matrix=mtx.tolist(), distortions=[float(v) for v in dist.ravel()[:4]], rms_px=round(float(rms), 4),
        n_frames=n_frames, n_used=len(imgpoints), coverage=round(coverage, 3), tilt_spread_deg=round(tilt, 1),
        layout=layout, mirrored=mirrored, status=status, advice=advice, used_frames=used)


def result_path(work_dir: Path, camera: str) -> Path:
    return work_dir / f"intrinsics_{camera}.json"


def save_result(result: IntrinsicsResult, work_dir: Path) -> Path:
    path = result_path(work_dir, result.camera)
    atomic_write_text(path, result.to_json() + "\n")
    return path


def load_result(work_dir: Path, camera: str, video: Path | None = None) -> IntrinsicsResult | None:
    """Saved result, or None if missing or computed from a different video (hash check, FR-3.4)."""
    path = result_path(work_dir, camera)
    if not path.is_file():
        return None
    result = IntrinsicsResult.from_json(path.read_text(encoding="utf-8"))
    if video is not None and result.video_hash != video_hash(video):
        return None
    return result


def settings_hash(spec: BoardSpec) -> str:
    return hashlib.sha256(json.dumps(asdict(spec), sort_keys=True).encode()).hexdigest()[:12]
