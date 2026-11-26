"""QA metrics/report/preview and export naming, manifest and BVH check, on fake stage outputs; CLI flow."""

import json

import pytest

from mocapapp.cli.main import main
from mocapapp.core import qa, runner
from mocapapp.core.export import check_bvh, default_selection, export, sha256
from mocapapp.core.session import Layout, load_session
from tests.m3_fakes import FakeStages, make_session, write_bvh, write_trc


@pytest.fixture
def done_session(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "run_stage", FakeStages())
    monkeypatch.setattr(runner, "_fps", lambda *a: 30.0)
    session = make_session(tmp_path)
    runner.run_session(session, tmp_path)
    return session


def test_lights():
    assert qa.light("reprojection_mm", 18.9) == "green"
    assert qa.light("reprojection_mm", 30) == "amber"
    assert qa.light("reprojection_mm", 41) == "red"
    assert qa.light("sync_min_correlation", 0.86) == "green"
    assert qa.light("sync_min_correlation", 0.3) == "red"
    assert qa.light("ik_rms_mm", float("nan")) == "grey"
    assert qa.worst(["green", "grey", "amber"]) == "amber" and qa.worst(["grey"]) == "grey"


def test_trial_qa_metrics(done_session, tmp_path):
    r = qa.trial_qa(done_session, tmp_path, "1")
    m = {x.name: x for x in r.metrics}
    assert m["3D reprojection error"].value == 18.9
    assert m["Sync confidence (lowest camera)"].value == 0.86
    assert m["Model fit (IK marker RMS, mean)"].value == 22.5
    assert m["Frames without 2+ cameras (interpolated or filled)"].value == pytest.approx(3 / 40)
    assert m["Feet height above floor (median of lowest foot point)"].value == pytest.approx(0.02, abs=1e-3)
    assert r.light == "amber"  # missing fraction 7.5 % is between 5 and 20 %
    assert r.preview and "const D={" in open(r.preview, encoding="utf-8").read()


def test_flipped_axis_and_floating_feet_are_flagged(done_session, tmp_path):
    p3d = Layout(tmp_path).trial("1") / "pose-3d"
    write_trc(next(p3d.glob("*_LSTM.trc")), flip=True)
    r = qa.trial_qa(done_session, tmp_path, "1")
    assert any("head is below the feet" in w for w in r.warnings)
    write_trc(next(p3d.glob("*_LSTM.trc")), floor=0.3)
    r = qa.trial_qa(done_session, tmp_path, "1")
    assert r.light == "red"


def test_failed_trial_is_red(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "run_stage", FakeStages(fail={"poseEstimation"}))
    session = make_session(tmp_path, n_trials=1)
    runner.run_session(session, tmp_path)
    r = qa.trial_qa(session, tmp_path, "1")
    assert r.light == "red" and r.metrics == [] and "Finding body keypoints" in r.warnings[0]


def test_report_files(done_session, tmp_path):
    path, results = qa.write_report(done_session, tmp_path)
    assert len(results) == 2
    text = path.read_text(encoding="utf-8")
    assert "T1-ABCDEF1" in text and "CHECK" in text and "trial_1_preview.html" in text
    data = json.loads((path.parent / "qa.json").read_text())
    assert data["thresholds"]["ik_rms_mm"] == [30.0, 50.0] and data["trials"][1]["label"] == "2"


def test_export_defaults(done_session, tmp_path):
    out, warnings = export(done_session, tmp_path, default_selection(), qa_summary={"1": "amber"})
    assert warnings == []
    names = sorted(p.name for p in out.iterdir())
    assert "T1-ABCDEF1_trial1_markers_butterworth-lstm.trc" in names
    assert "T1-ABCDEF2_trial2_joint_angles.mot" in names
    assert "T1-ABCDEF1_trial1_config.toml" in names
    assert not any(n.endswith(".bvh") for n in names)  # BVH unticked by default (D-decision)
    manifest = json.loads((out / "manifest.json").read_text())
    entry = next(f for f in manifest["files"] if f["file"].endswith("_model_scaled.osim"))
    assert entry["sha256"] == sha256(out / entry["file"])
    assert entry["kinematics_source"] == "LSTM-augmented markers"
    assert manifest["qa"] == {"1": "amber"} and "Pagnon" in manifest["citation"]
    assert "pose2sim" in manifest["versions"]
    readme = (out / "README.txt").read_text()
    assert "pseudonymised" in readme and "Y up" in readme


def test_export_bvh_checked_and_errors(done_session, tmp_path):
    out, warnings = export(done_session, tmp_path, ["bvh"], labels=["1"])
    assert warnings == []
    entry = json.loads((out / "manifest.json").read_text())["files"][0]
    assert entry["bvh_check"] == "ok" and entry["trial"] == "1"
    with pytest.raises(ValueError, match="Unknown export items: bogus"):
        export(done_session, tmp_path, ["bogus"])


def test_check_bvh(tmp_path):
    good = tmp_path / "g.bvh"
    write_bvh(good)
    assert check_bvh(good) == []
    bad = tmp_path / "b.bvh"
    write_bvh(bad, frames=20, rows=18)
    assert any("18 rows" in p for p in check_bvh(bad))
    mot = tmp_path / "x.mot"
    mot.write_text("nRows=40\nendheader\n")
    assert any("40 rows" in p for p in check_bvh(good, mot))
    (tmp_path / "n.bvh").write_text("garbage")
    assert len(check_bvh(tmp_path / "n.bvh")) == 2


def test_cli_run_qa_export(tmp_path, monkeypatch, capsys):
    fake = FakeStages()
    monkeypatch.setattr(runner, "run_stage", fake)
    monkeypatch.setattr(runner, "_fps", lambda *a: 30.0)
    monkeypatch.setattr(runner, "estimate_seconds", lambda *a: 600)
    make_session(tmp_path, n_trials=1, ready=False)
    s = str(tmp_path)
    assert main(["run", "--session", s]) == 1
    out = capsys.readouterr().out
    assert "--attest" in out and fake.calls == []

    assert main(["confirm", "--session", s, "--sync-cue"]) == 0
    assert main(["consent", "--session", s, "--trial", "1", "--attest", "--raw-storage", "yes"]) == 0
    t = load_session(tmp_path).trial("1")
    assert t.consent.attested and t.consent.attested_at and t.delete_raw is False

    assert main(["run", "--session", s]) == 0
    out = capsys.readouterr().out
    assert "Rough time estimate on CPU: 10 min" in out and "trial 1: done" in out and "Quality report" in out
    assert main(["qa", "--session", s]) == 0
    assert "Model fit (IK marker RMS, mean): 22.5 mm -> GOOD" in capsys.readouterr().out
    assert main(["export", "--session", s, "--select", "trc.filt_lstm,bvh"]) == 0
    assert sorted(p.suffix for p in (tmp_path / "export").iterdir()) == [".bvh", ".csv", ".json", ".trc", ".txt"]
    assert main(["export", "--session", s, "--select", "nope"]) == 1


def test_export_again_replaces_a_trials_earlier_files(done_session, tmp_path):
    """An earlier export with two files of a kind (two runs' results) is replaced, not added to."""
    out, _ = export(done_session, tmp_path, ["mot"])
    for n in (1, 2):
        (out / f"T1-ABCDEF1_trial1_joint_angles_{n}.mot").write_text("old")
    (out / "T1-ABCDEF1_trial1_joint_angles.mot").unlink()
    (out / "T1-ABCDEF1_trial1_model_scaled.osim").write_text("not exported now: kept")
    export(done_session, tmp_path, ["mot"])
    names = sorted(p.name for p in out.glob("T1-ABCDEF1_trial1_*"))
    assert names == ["T1-ABCDEF1_trial1_joint_angles.mot", "T1-ABCDEF1_trial1_model_scaled.osim"]
