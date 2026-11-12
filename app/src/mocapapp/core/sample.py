"""'Try with sample data': a ready-to-run session built from upstream's Demo_SinglePerson (BSD-3, ships with
Pose2Sim). The demo has no calibration videos, so its Qualisys calibration is converted with upstream's own
converter and marked as imported; the app's calibration steps are skipped for this session only.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import rtoml

from mocapapp.core.assemble import assemble_trials
from mocapapp.core.runner import stage_env
from mocapapp.core.session import Camera, Layout, Session, Trial, save_session, session_dir_name
from mocapapp.core.workflow import fill_versions

DEMO_HEIGHT_M = 1.72


class SampleError(RuntimeError):
    pass


def demo_dir() -> Path:
    import Pose2Sim

    return Path(Pose2Sim.__file__).parent / "Demo_SinglePerson"


def convert_demo_calibration(work: Path) -> Path:
    """Headless upstream conversion of the demo's Qualisys file to a Pose2Sim .toml (about 30 s)."""
    demo = demo_dir()
    (work / "calibration").mkdir(parents=True, exist_ok=True)
    shutil.copy2(demo / "calibration" / "Calib.qca.txt", work / "calibration")
    cfg = rtoml.load(demo / "Config.toml")
    cfg["calibration"]["calibration_type"] = "convert"
    cfg["calibration"]["convert"]["convert_from"] = "qualisys"
    rtoml.dump(cfg, work / "Config.toml")
    proc = subprocess.run([sys.executable, "-c", "from Pose2Sim.Pose2Sim import Pose2SimPipeline; "
                           "Pose2SimPipeline().calibration()"], cwd=work, env=stage_env(), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", check=False)
    found = sorted((work / "calibration").glob("*.toml"))
    if proc.returncode or not found:
        raise SampleError("Converting the sample calibration failed:\n" + proc.stdout[-2000:])
    return found[0]


def make_sample_session(out_root: Path, progress: Callable[[str], None] = print) -> tuple[Path, Session]:
    demo = demo_dir()
    videos = sorted((demo / "videos").glob("cam*.mp4"))
    if not videos:
        raise SampleError(f"The sample videos are missing from {demo / 'videos'}.")
    root = Path(out_root) / session_dir_name("SAMPLE")
    if root.exists():
        raise SampleError(f"{root} already exists; wait a minute and try again.")
    layout = Layout(root)
    cams = [Camera(name=f"cam{i:02d}", source_dir=str(demo / "videos"), trial_files={"1": str(v)})
            for i, v in enumerate(videos, 1)]
    session = Session(study_code="SAMPLE", n_trials=1, cameras=cams, calibration_source="imported",
                      trials=[Trial(label="1", code="SAMPLE-DEMO", height_m=DEMO_HEIGHT_M, mapping_method="manual")])
    fill_versions(session)
    progress("Copying the sample videos ...")
    for cam, video in zip(cams, videos, strict=True):
        layout.inputs(cam.name).mkdir(parents=True, exist_ok=True)
        shutil.copy2(video, layout.inputs(cam.name) / "trial_1.mp4")
    progress("Converting the sample calibration (about 30 s) ...")
    calib = convert_demo_calibration(layout.calibration_work / "sample_conversion")
    shutil.copy2(calib, layout.calib_file)
    save_session(session, root)
    assemble_trials(session, root, progress=progress)
    save_session(session, root)
    return root, session
