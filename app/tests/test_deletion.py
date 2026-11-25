"""M5: consent records, deletion plans and safeguards (PROJECT.md §8.3-8.4), on temp folders."""

import json
from pathlib import Path

import pytest

from mocapapp.cli.main import main
from mocapapp.core import deletion, qa, runner
from mocapapp.core.export import default_selection, export
from mocapapp.core.session import CONSENT_FILE, Layout, load_session, save_session
from mocapapp.core.workflow import SetupError, inputs_ready, set_mapping, set_trial_layout
from tests.m3_fakes import FakeStages, make_session


def _finished(root: Path, monkeypatch, exported: bool = True):
    """Two trials run with fake stages, plus inputs/, originals, overlays and (optionally) an export."""
    monkeypatch.setattr(runner, "run_stage", FakeStages())
    monkeypatch.setattr(runner, "_fps", lambda *a: 30.0)
    session = make_session(root)
    layout = Layout(root)
    for cam in session.cameras:
        raw = root.parent / f"{root.name}_raw" / cam.name
        raw.mkdir(parents=True, exist_ok=True)
        for t in session.trials:
            original = raw / f"take{t.label}.mp4"
            original.write_bytes(b"original video " * 10)
            t.videos[cam.id] = str(original)
            copy = layout.trial_input(t.label, cam.id)
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_bytes(b"working copy")
            copy.with_name(copy.name + ".source").write_text("{}")
        for calib in (layout.camera_input(cam.id), layout.layout_input("L1", cam.id)):
            calib.parent.mkdir(parents=True, exist_ok=True)
            calib.write_bytes(b"calibration")
    session.settings.keep_keypoints = True  # these tests delete the 2D keypoints themselves
    runner.run_session(session, root)
    for t in session.trials:
        pose = layout.trial(t.label) / "pose"
        (pose / "cam01_pose.mp4").write_bytes(b"overlay with blurred face")
        (layout.trial(t.label) / "pose-sync" / "sync_cam01_vs_cam02.png").write_bytes(b"correlation plot")
    if exported:
        qa.write_report(session, root)
        export(session, root, default_selection(), None, {})
    save_session(session, root)
    return session


def test_consent_json_and_retention(tmp_path):
    session = make_session(tmp_path)
    t = session.trials[0]
    t.consent.raw_footage_storage, t.delete_raw = True, False
    save_session(session, tmp_path)
    rec = json.loads((Layout(tmp_path).trial("1") / CONSENT_FILE).read_text())
    assert rec["code"] == t.code and rec["attested"] is True and rec["template_version"] == "1"
    assert rec["raw_footage_storage"] is True and rec["retention_until"] == "study finished"
    assert load_session(tmp_path).trials[1].retention_until is None  # no storage consent: delete, no retention
    t.delete_raw = True
    save_session(session, tmp_path)
    assert load_session(tmp_path).trials[0].retention_until is None


def test_raw_plan_groups_and_execute(tmp_path, monkeypatch):
    root = tmp_path / "S"
    session = _finished(root, monkeypatch)
    layout = Layout(root)
    plan = deletion.plan_raw(session, root, "1", after_run=True)
    assert not plan.blockers and plan.confirm_text is None
    groups = {i.group for i in plan.items}
    assert groups == {"working", "derived"}  # originals only when asked
    paths = {i.path for i in plan.items}
    assert layout.trial_input("1", "cam01") in paths and layout.trial("1") / "videos" / "cam02.mp4" in paths
    assert layout.trial("1") / "pose" / "cam01_json" in paths and layout.trial("1") / "pose" / "cam01_pose.mp4" in paths
    assert layout.trial("1") / "pose-associated" / "cam02_json" in paths
    assert not any("sync_cam01" in str(p) for p in paths)
    text = plan.describe()
    assert "(a) Working copies" in text and "(b) Material derived" in text and "not moved to the Recycle Bin" in text
    assert plan.size() > 0

    result = deletion.execute(plan, session)
    assert not result.failures and result.deleted == len(plan.items)
    assert any("SD cards" in c for c in result.checklist)
    tdir = layout.trial("1")
    assert not (tdir / "pose").exists() and not (layout.trial_input("1", "cam01")).exists()
    # kept: results, configs, QA, sync plot, calibration inputs, other trial, originals
    assert list((tdir / "pose-3d").glob("*_LSTM.trc")) and list((tdir / "kinematics").glob("*.mot"))
    assert (tdir / "Config.toml").is_file() and (tdir / CONSENT_FILE).is_file()
    assert (tdir / "pose-sync" / "sync_cam01_vs_cam02.png").is_file()
    assert layout.camera_input("cam01").is_file() and layout.trial_input("2", "cam01").is_file()
    assert Path(session.trial("1").videos["cam01"]).is_file()

    s2 = load_session(root)
    assert s2.trial("1").raw_deleted_at and s2.trial("1").frozen and not s2.trial("1").raw_present
    assert not s2.trial("2").frozen  # R20: the lock is per trial
    log = deletion.deletion_log(root)
    assert log[-1]["code"] == "T1-ABCDEF1" and log[-1]["scope"] == "raw" and log[-1]["groups"]["working"]["items"]
    assert json.loads((layout.export / "manifest.json").read_text())["deletions"][-1]["at"] == log[-1]["at"]

    # the pipeline leaves the trial alone from now on
    assert not [i for i in runner.preflight(s2, root, ["1", "2"]) if i.level == "error"]
    assert runner.run_session(s2, root, ["1"]) == {"1": "done"}
    with pytest.raises(ValueError, match="cannot be processed"):
        runner.run_trial(s2, root, "1")
    assert inputs_ready(s2, root)
    with pytest.raises(SetupError, match="raw footage was deleted"):
        set_mapping(s2, {("cam01", "1"): Path(s2.trial("2").videos["cam01"])})
    with pytest.raises(SetupError, match="raw footage was deleted"):
        set_trial_layout(s2, None, "1", "L1-other")
    assert deletion.plan_raw(s2, root, "1").items == []  # nothing left


def test_planned_deletion_waits_for_complete_exported_results(tmp_path, monkeypatch):
    root = tmp_path / "S"
    session = _finished(root, monkeypatch, exported=False)
    plan = deletion.plan_raw(session, root, "1", after_run=True)
    assert any("not been exported" in b for b in plan.blockers)
    with pytest.raises(deletion.DeletionError, match="Not deleted yet"):
        deletion.execute(plan, session)
    assert Layout(root).trial_input("1", "cam01").is_file()
    # a broken result file also blocks
    export(session, root, default_selection(), None, {})
    mot = next((Layout(root).trial("1") / "kinematics").glob("*.mot"))
    mot.write_text("")
    assert any("empty or unreadable" in b for b in deletion.plan_raw(session, root, "1", after_run=True).blockers)
    # on request (participant asks) it is allowed, with a warning, even before the run
    fresh = make_session(tmp_path / "F")
    req = deletion.plan_raw(fresh, tmp_path / "F", "1")
    assert not req.blockers and any("never be processed again" in w for w in req.warnings)


def test_originals_need_the_typed_code_and_are_logged_without_paths(tmp_path, monkeypatch):
    root = tmp_path / "S"
    session = _finished(root, monkeypatch)
    plan = deletion.plan_raw(session, root, "2", include_originals=True)
    originals = [i for i in plan.items if i.group == "originals"]
    assert len(originals) == 2 and plan.confirm_text == "T1-ABCDEF2"
    for wrong in (None, "", "T1-ABCDEF1"):
        with pytest.raises(deletion.DeletionError, match="type the pseudonym"):
            deletion.execute(plan, session, wrong)
    assert all(i.path.is_file() for i in originals)
    deletion.execute(plan, session, " t1-abcdef2 ")
    assert not any(i.path.exists() for i in originals)
    entry = deletion.deletion_log(root)[-1]
    assert entry["originals_deleted_for"] == ["cam01", "cam02"]
    assert not any("take2" in p for p in entry["paths"]) and "_raw" not in json.dumps(entry)


def test_withdrawal_removes_everything_of_that_participant(tmp_path, monkeypatch):
    root = tmp_path / "S"
    session = _finished(root, monkeypatch)
    layout = Layout(root)
    assert list(layout.export.glob("T1-ABCDEF1_*")) and (layout.qa / "trial_1_preview.html").is_file()
    plan = deletion.plan_participant(session, root, "1")
    assert plan.confirm_text == "T1-ABCDEF1" and "results" in plan.groups()
    with pytest.raises(deletion.DeletionError):
        deletion.execute(plan, session, "nope")
    deletion.execute(plan, session, "T1-ABCDEF1")
    assert not layout.trial("1").exists() and not list(layout.export.glob("T1-ABCDEF1_*"))
    assert not list(layout.qa.glob("trial_1_*")) and list(layout.export.glob("T1-ABCDEF2_*"))
    manifest = json.loads((layout.export / "manifest.json").read_text())
    assert {f["code"] for f in manifest["files"]} == {"T1-ABCDEF2"}
    assert manifest["deletions"][-1]["scope"] == "participant"
    s2 = load_session(root)
    t = s2.trial("1")
    assert t.withdrawn_at and t.height_m is None and [x.label for x in s2.active_trials] == ["2"]
    assert [r["label"] for r in json.loads((layout.qa / "qa.json").read_text())["trials"]] == ["2"]
    save_session(s2, root)
    assert not layout.trial("1").exists()  # no consent.json resurrects the folder
    assert runner.run_session(s2, root) == {"1": "withdrawn", "2": "done"}
    with pytest.raises(deletion.DeletionError, match="already deleted"):
        deletion.plan_raw(s2, root, "1")


def test_whole_session(tmp_path, monkeypatch):
    root = tmp_path / "S"
    session = _finished(root, monkeypatch)
    (root / "my notes.txt").write_text("not the app's")
    plan = deletion.plan_session(session, root, include_originals=True)
    assert plan.blockers and "my notes.txt" in plan.blockers[0]
    (root / "my notes.txt").unlink()
    plan = deletion.plan_session(session, root, include_originals=True)
    assert plan.confirm_text == "S" and not plan.blockers
    with pytest.raises(deletion.DeletionError, match="session folder name"):
        deletion.execute(plan, session, "T1-ABCDEF1")
    originals = [i.path for i in plan.items if i.group == "originals"]
    deletion.execute(plan, session, "S")
    assert not root.exists() and not any(p.exists() for p in originals)


def test_sample_originals_are_never_listed(tmp_path, monkeypatch):
    root = tmp_path / "S"
    session = _finished(root, monkeypatch)
    session.calibration_source = "imported"
    assert not [i for i in deletion.plan_raw(session, root, "1", include_originals=True).items
                if i.group == "originals"]


def test_cloud_warning_and_reminders(tmp_path, monkeypatch):
    root = tmp_path / "S"
    session = _finished(root, monkeypatch)
    monkeypatch.setattr(deletion, "in_cloud_folder", lambda p: True)
    assert any("cloud-synced" in w for w in deletion.plan_raw(session, root, "1").warnings)
    t1, t2 = session.trials
    t1.consent.raw_footage_storage, t1.delete_raw = True, False
    save_session(session, root)
    notes = deletion.reminders(load_session(root))
    assert any("kept with consent for T1-ABCDEF1" in n for n in notes)
    assert any("marked for deletion but not deleted yet: T1-ABCDEF2" in n for n in notes)
    assert deletion.session_reminders(tmp_path / "missing") == []


def test_cli_dry_run_then_delete(tmp_path, monkeypatch, capsys):
    root = tmp_path / "S"
    _finished(root, monkeypatch)
    video = Layout(root).trial_input("1", "cam01")
    assert main(["delete-raw", "--session", str(root), "--trial", "1"]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out and str(video) in out and video.is_file()
    assert main(["delete-raw", "--session", str(root), "--trial", "1", "--include-originals", "--yes"]) == 1
    assert "type the pseudonym" in capsys.readouterr().out and video.is_file()
    assert main(["delete-raw", "--session", str(root), "--trial", "1", "--include-originals", "--yes",
                 "--confirm", "T1-ABCDEF1", "--after-run"]) == 0
    out = capsys.readouterr().out
    assert "Deleted" in out and "[ ]" in out and not video.exists()
    with pytest.raises(SystemExit, match="raw footage was deleted"):
        main(["map", "--session", str(root), "--set", f"cam01:1={root.parent / 'S_raw' / 'cam01' / 'take2.mp4'}"])
    assert main(["reminders", "--root", str(tmp_path)]) == 0
    assert "T1-ABCDEF2" in capsys.readouterr().out
    assert main(["delete-session", "--session", str(root), "--trial", "2", "--yes", "--confirm", "T1-ABCDEF2"]) == 0
    assert not Layout(root).trial("2").exists()
    assert main(["delete-session", "--session", str(root), "--yes", "--confirm", "S"]) == 0
    assert not root.exists()
