"""Fake Pose2Sim stages for runner/QA/export tests: each writes the outputs and log lines the real stage would."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from mocapapp.core import runner
from mocapapp.core.config_gen import build_config
from mocapapp.core.session import (
    Camera,
    Consent,
    Layout,
    Participant,
    Session,
    Trial,
    atomic_write_text,
    save_session,
)

MARKERS = ["Hip", "RHip", "LHip", "RKnee", "LKnee", "RAnkle", "LAnkle", "RHeel", "LHeel", "RBigToe", "LBigToe",
           "Neck", "Head", "Nose"]


def make_session(root: Path, n_trials: int = 2, n_cams: int = 2, ready: bool = True) -> Session:
    people = [Participant(label=str(i), code=f"T1-ABCDEF{i}", height_m=1.75, consent=Consent(attested=ready))
              for i in range(1, n_trials + 1)]
    cams = [Camera(name=f"cam{c:02d}", source_dir=str(root / "raw")) for c in range(1, n_cams + 1)]
    session = Session(n_trials=n_trials, participants=people, cameras=cams, sync_cue_confirmed=ready,
                      study_code="T1", trials=[Trial(label=p.label, participant=p.label) for p in people])
    trials = session.trials
    layout = Layout(root)
    for t in trials:
        tdir = layout.trial(t.label)
        (tdir / "videos").mkdir(parents=True)
        (tdir / "calibration").mkdir()
        (tdir / "calibration" / "Calib_scene.toml").write_text("[cam01]\n")
        for c in cams:
            (tdir / "videos" / f"{c.name}.mp4").write_bytes(b"not a real video")
        atomic_write_text(tdir / "Config.toml", build_config(session, t))
    save_session(session, root)
    return session


def write_trc(path: Path, frames: int = 20, rate: float = 30.0, flip: bool = False, floor: float = 0.0) -> None:
    rng = np.random.default_rng(0)
    heights = {"Head": 1.7, "Nose": 1.65, "Neck": 1.5, "Hip": 1.0, "RHip": 1.0, "LHip": 1.0, "RKnee": 0.5,
               "LKnee": 0.5, "RAnkle": 0.08, "LAnkle": 0.08, "RHeel": 0.03, "LHeel": 0.03, "RBigToe": 0.02,
               "LBigToe": 0.02}
    lines = [f"PathFileType\t4\t(X/Y/Z)\t{path.name}",
             "DataRate\tCameraRate\tNumFrames\tNumMarkers\tUnits\tOrigDataRate\tOrigDataStartFrame\tOrigNumFrames",
             f"{rate}\t{rate}\t{frames}\t{len(MARKERS)}\tm\t{rate}\t1\t{frames}",
             "Frame#\tTime\t" + "\t\t\t".join(MARKERS),
             "\t\t" + "\t".join(f"X{i}\tY{i}\tZ{i}" for i in range(1, len(MARKERS) + 1)), ""]
    for f in range(frames):
        vals = []
        for i, m in enumerate(MARKERS):
            y = (-heights[m] if flip else heights[m]) + floor
            vals += [0.01 * f + 0.1 * (i % 3), y, 0.2 * (m.startswith("L")) + rng.normal(0, 1e-3)]
        lines.append(f"{f + 1}\t{f / rate:.4f}\t" + "\t".join(f"{v:.5f}" for v in vals))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def write_bvh(path: Path, frames: int = 20, rows: int | None = None) -> None:
    rows = frames if rows is None else rows
    head = ("HIERARCHY\nROOT pelvis\n{\n  OFFSET 0 0 0\n  CHANNELS 6 Xposition Yposition Zposition Zrotation "
            "Xrotation Yrotation\n  JOINT femur_r\n  {\n    OFFSET 0 -0.1 0.08\n    CHANNELS 3 Zrotation Xrotation "
            "Yrotation\n    End Site\n    {\n      OFFSET 0 -0.4 0\n    }\n  }\n}\n")
    body = "\n".join(" ".join("0.0" for _ in range(9)) for _ in range(rows))
    path.write_text(f"{head}MOTION\nFrames: {frames}\nFrame Time: 0.033333\n{body}\n")


SYNC_GOOD = "--> Camera cam01 and cam02: 2 frames offset, correlation 0.86.\n"
SYNC_BAD = "--> Camera cam01 and cam02: -1 frames offset, correlation 0.31.\n"
TRIANG_LOG = """Mean reprojection error for Nose is 9.1 px (~ 0.016 m), reached with 0.2 excluded cameras.
Mean reprojection error for Head is 12.0 px (~ 0.02 m), reached with 0.5 excluded cameras.
  Frames 3:5 were interpolated.
  No frames needed to be interpolated.
--> Mean reprojection error for all points on frames 1 to 20 is 10.4 px, which roughly corresponds to 18.9 mm.
"""


class FakeStages:
    """Drop-in for runner.run_stage. `fail` / `sync_log` / `cancel_at` steer what happens."""

    def __init__(self, fail: set[str] | None = None, sync_log: str = SYNC_GOOD, cancel_at: str | None = None,
                 fail_trials: set[str] | None = None):
        self.calls: list[tuple[str, str]] = []
        self.fail = fail or set()
        self.fail_trials = fail_trials
        self.sync_log = sync_log
        self.cancel_at = cancel_at

    def __call__(self, trial_dir: Path, stage: str, on_line, cancel=None, code=None) -> int:
        label = trial_dir.name.removeprefix("trial_")
        self.calls.append((label, stage))
        if stage == self.cancel_at:
            self.cancel_at = None
            raise runner.Cancelled(stage)
        log = ""
        failing = stage in self.fail and (self.fail_trials is None or label in self.fail_trials)
        if not failing:
            log = self._outputs(trial_dir, stage)
        (trial_dir / runner.LOG_DIR).mkdir(exist_ok=True)
        (trial_dir / runner.LOG_DIR / f"{stage}.log").write_text(log + ("Traceback: boom\n" if failing else ""))
        for line in ["Processing cam01.mp4:  50%|#####     | 5/10", *log.splitlines()]:
            on_line(line)
        return 1 if failing else 0

    def _outputs(self, tdir: Path, stage: str) -> str:
        stem = f"{tdir.name}_1-20"
        folder = {"poseEstimation": "pose", "synchronization": "pose-sync", "personAssociation": "pose-associated"}
        if stage in folder:
            for cam in sorted(v.stem for v in (tdir / "videos").glob("*.mp4")) or ["cam01", "cam02"]:
                d = tdir / folder[stage] / f"{cam}_json"
                d.mkdir(parents=True, exist_ok=True)
                (d / f"{cam}_000001.json").write_text("{}")
            return self.sync_log if stage == "synchronization" else ""
        p3d = tdir / "pose-3d"
        if stage == "triangulation":
            write_trc(p3d / f"{stem}.trc")
            (p3d / f"{stem}.c3d").write_bytes(b"c3d")
            return TRIANG_LOG
        if stage == "filtering":
            write_trc(p3d / f"{stem}_filt_butterworth.trc")
            (p3d / f"{stem}_filt_butterworth.c3d").write_bytes(b"c3d")
        if stage == "markerAugmentation":
            write_trc(p3d / f"{stem}_filt_butterworth_LSTM.trc")
            (p3d / f"{stem}_filt_butterworth_LSTM.c3d").write_bytes(b"c3d")
        if stage == "kinematics":
            kin = tdir / "kinematics"
            kin.mkdir(exist_ok=True)
            name = f"{stem}_filt_butterworth_LSTM"
            (kin / f"{name}.osim").write_text("<OpenSimDocument/>")
            (kin / f"{name}.mot").write_text("Coordinates\nversion=1\nnRows=20\nnColumns=3\nendheader\n")
            write_bvh(kin / f"{name}.bvh")
            (kin / "_ik_marker_errors.sto").write_text(
                "IK errors\nendheader\ntime\tmarker_error_total_squared\tmarker_error_RMS\tmarker_error_max\n"
                "0\t0.01\t0.020\t0.028\n0.033\t0.01\t0.025\t0.030\n")
        return ""
