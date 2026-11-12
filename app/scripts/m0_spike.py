"""Milestone 0 spike: run upstream Pose2Sim headless on Demo_SinglePerson in the per-trial layout.

Builds  <out>/session spike/trials/trial_1  (path with a space, parent without Config.toml),
converts the demo's Qualisys calibration, then runs every stage in a subprocess with cwd = trial folder
and all interactive flags off. Usage:  uv run python scripts/m0_spike.py <out_dir> [--stages a,b,...]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

import rtoml
from Pose2Sim.Pose2Sim import recursive_update  # same merge as upstream read_config_files

DEMO = Path(__import__("Pose2Sim").__file__).parent / "Demo_SinglePerson"

HEADLESS = {
    "project": {"participant_height": 1.72},
    "pose": {"display_detection": False, "save_video": "none"},
    "synchronization": {"synchronization_gui": False, "display_sync_plots": False, "save_sync_plots": True},
    "calibration": {
        "calibration_type": "convert",
        "convert": {"convert_from": "qualisys"},
        "calculate": {"intrinsics": {"show_detection_intrinsics": False},
                      "extrinsics": {"show_reprojection_error": False}},
    },
    "filtering": {"display_figures": False},
    "kinematics": {"make_bvh": True},
}
STAGES = ["calibration", "poseEstimation", "synchronization", "personAssociation",
          "triangulation", "filtering", "markerAugmentation", "kinematics"]


def build_trial(trial: Path) -> None:
    (trial / "calibration").mkdir(parents=True, exist_ok=True)
    shutil.copy2(DEMO / "calibration" / "Calib.qca.txt", trial / "calibration")
    shutil.copytree(DEMO / "videos", trial / "videos", dirs_exist_ok=True)
    config = recursive_update(rtoml.load(DEMO / "Config.toml"), HEADLESS)
    rtoml.dump(config, trial / "Config.toml")


def run_stage(trial: Path, stage: str) -> float:
    code = f"from Pose2Sim.Pose2Sim import Pose2SimPipeline; Pose2SimPipeline().{stage}()"
    t0 = time.perf_counter()
    proc = subprocess.run([sys.executable, "-c", code], cwd=trial, env={**__import__("os").environ,
                          "MPLBACKEND": "Agg", "PYTHONIOENCODING": "utf-8"})
    if proc.returncode:
        raise SystemExit(f"stage {stage} failed with exit code {proc.returncode}")
    return time.perf_counter() - t0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--stages", default=",".join(STAGES))
    args = ap.parse_args()
    # ASCII only: non-ASCII paths break OpenSim (upstream-notes §8)
    trial = args.out / "session spike" / "trials" / "trial_1"
    if not (trial / "Config.toml").exists():
        build_trial(trial)
    for stage in args.stages.split(","):
        print(f"=== {stage}: {run_stage(trial, stage):.1f} s", flush=True)


if __name__ == "__main__":
    main()
