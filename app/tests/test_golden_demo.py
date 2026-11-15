"""Golden end-to-end run on upstream's Demo_SinglePerson through the app's own layout, runner, QA and export.

Slow (~8 min on CPU). Run with:  uv run pytest -m slow tests/test_golden_demo.py -s
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import rtoml

from mocapapp.core import qa, runner
from mocapapp.core.assemble import assemble_trials
from mocapapp.core.export import default_selection, export
from mocapapp.core.session import Camera, Consent, Layout, Participant, Session, Trial, save_session

DEMO = Path(__import__("Pose2Sim").__file__).parent / "Demo_SinglePerson"


def convert_demo_calibration(work: Path) -> Path:
    """Headless upstream conversion of the demo's Qualisys file to a Pose2Sim .toml."""
    (work / "calibration").mkdir(parents=True)
    shutil.copy2(DEMO / "calibration" / "Calib.qca.txt", work / "calibration")
    cfg = rtoml.load(DEMO / "Config.toml")
    cfg["calibration"]["calibration_type"] = "convert"
    cfg["calibration"]["convert"]["convert_from"] = "qualisys"
    rtoml.dump(cfg, work / "Config.toml")
    subprocess.run([sys.executable, "-c", "from Pose2Sim.Pose2Sim import Pose2SimPipeline; "
                    "Pose2SimPipeline().calibration()"], cwd=work, env=runner.stage_env(), check=True,
                   capture_output=True)
    return next((work / "calibration").glob("*.toml"))


@pytest.mark.slow
def test_demo_end_to_end(tmp_path):
    calib = convert_demo_calibration(tmp_path / "convert")
    root = tmp_path / "golden session"
    cams = [Camera(name=f"cam{i:02d}", source_dir=str(DEMO / "videos")) for i in range(1, 5)]
    person = Participant(label="1", code="DEMO-2345672", height_m=1.72, consent=Consent(attested=True))
    session = Session(n_trials=1, participants=[person], trials=[Trial(label="1", participant="1")], cameras=cams,
                      sync_cue_confirmed=True, study_code="DEMO")
    layout = Layout(root)
    for cam in cams:
        layout.inputs(cam.name).mkdir(parents=True)
        shutil.copy2(DEMO / "videos" / f"{cam.name}.mp4", layout.inputs(cam.name) / "trial_1.mp4")
    layout.calibration_work.mkdir(parents=True)
    shutil.copy2(calib, layout.calib_file)
    save_session(session, root)
    assemble_trials(session, root, progress=lambda m: None)

    assert [i for i in runner.preflight(session, root, ["1"]) if i.level in ("error", "action")] == []

    def show(e):
        if e.kind in ("stage_start", "stage_end", "needs_sync_fix", "trial_end"):
            print(f"[{e.kind}] {e.stage}: {e.message}", flush=True)

    assert runner.run_session(session, root, listener=show) == {"1": "done"}, session.run_state.errors

    result = qa.trial_qa(session, root, "1")
    m = {x.name: x.value for x in result.metrics}
    print(m, result.warnings)
    assert 10 < m["3D reprojection error"] < 30  # spike: 18.9 mm
    assert m["Model fit (IK marker RMS, mean)"] < 40  # spike: 22.5 mm
    assert m["Sync confidence (lowest camera)"] > 0.5
    assert not any("head is below" in w for w in result.warnings)
    report, _ = qa.write_report(session, root)
    assert report.is_file()

    out, warnings = export(session, root, default_selection() + ["bvh"])
    assert warnings == [], warnings
    names = {p.name for p in out.iterdir()}
    assert {"DEMO-2345672_trial1_joint_angles.mot", "DEMO-2345672_trial1_skeleton_animation.bvh",
            "DEMO-2345672_trial1_markers_butterworth-lstm.trc", "manifest.json", "README.txt"} <= names
