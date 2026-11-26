"""P1 of the project-based layout (PROJECT.md §16): model, per-trial lock, readiness, R6/R8, migration of 0.1.x."""

import json

import pytest

from mocapapp.core import workflow
from mocapapp.core.calibration import service
from mocapapp.core.migrate import BACKUP, triangulation_deps, upgrade_v1
from mocapapp.core.readiness import trial_missing, trial_ready
from mocapapp.core.session import (
    Camera,
    CameraLayout,
    Consent,
    Layout,
    LayoutCamera,
    Participant,
    Session,
    Trial,
    load_session,
    save_session,
)

SCENE = {"origin_text": "tape cross", "x_axis_text": "window", "unit": "m",
         "points": [{"label": f"P{i}", "xyz": xyz} for i, xyz in enumerate(
             [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0], [0, 0, 1], [1, 0, 1]], 1)]}


def project(n_trials=2, dates=(None, None)) -> Session:
    cams = [Camera(id="cam01"), Camera(id="cam02"), Camera(id="cam03")]
    layouts = [CameraLayout(id="L1", name="Hall", date=dates[0], cameras=[LayoutCamera(camera="cam01"),
                                                                           LayoutCamera(camera="cam02")]),
               CameraLayout(id="L2", name="Lab", date=dates[1], cameras=[LayoutCamera(camera="cam03"),
                                                                          LayoutCamera(camera="cam01")])]
    people = [Participant(label="1", code="T1-AAAAAAA", height_m=1.7, consent=Consent(attested=True))]
    trials = [Trial(label=str(i), participant="1", layout="L1") for i in range(1, n_trials + 1)]
    return Session(study_code="T1", n_trials=n_trials, cameras=cams, layouts=layouts, participants=people,
                   trials=trials)


# ---------- model ----------

def test_new_project_is_schema_2_and_round_trips(tmp_path):
    s = project()
    save_session(s, tmp_path)
    data = json.loads((tmp_path / "session.json").read_text())
    assert data["schema"] == 2 and "extrinsics" not in data and "sync_cue_confirmed" not in data
    loaded = load_session(tmp_path)
    assert [lay.camera_ids() for lay in loaded.layouts] == [["cam01", "cam02"], ["cam03", "cam01"]]
    assert not (tmp_path / BACKUP).exists()


def test_ids_slots_and_users():
    s = project()
    assert (s.new_id("cam"), s.new_id("L"), s.new_id("P")) == ("cam04", "L3", "P1")
    s.trials[1].layout = "L2"
    assert [(slot, cam.id) for slot, cam, _lc in s.trial_cameras("2")] == [("cam01", "cam03"), ("cam02", "cam01")]
    assert [lay.id for lay in s.layouts_with_camera("cam01")] == ["L1", "L2"]
    assert [t.label for t in s.trials_with_layout("L1")] == ["1"]


def test_r6_camera_used_once_a_trial_ran(tmp_path):
    s = project()
    assert not s.camera_used("cam01")
    service.ensure_recalibratable(s, "cam01")
    s.run_state.stage_done["1"] = ["pose"]
    assert s.camera_used("cam01") and s.camera_used("cam02") and not s.camera_used("cam03")
    with pytest.raises(service.CalibrationStepError, match="Duplicate the camera"):
        service.ensure_recalibratable(s, "cam02")
    copy = workflow.duplicate_camera(s, tmp_path, "cam02")
    assert copy.id == "cam04" and not s.camera_used("cam04")
    service.ensure_recalibratable(s, "cam04")


def test_r6_used_camera_cannot_get_a_new_intrinsic_video(tmp_path, video):
    s = project()
    s.run_state.stage_done["1"] = ["pose"]
    with pytest.raises(service.CalibrationStepError):
        workflow.set_intrinsic_video(s, tmp_path, "cam01", video(tmp_path / "new.mp4"))


def test_r8_layout_from_another_day_warns_until_accepted(tmp_path):
    s = project(dates=("2026-11-20", None))
    assert s.layout_warning("1") == ""  # no trial date: nothing to compare
    workflow.set_trial_info(s, None, "1", date="2026-11-20")
    assert s.layout_warning("1") == ""
    workflow.set_trial_info(s, None, "1", date="2026-11-24")
    assert "2026-11-24" in s.layout_warning("1") and "Hall" in s.layout_warning("1")
    workflow.accept_layout(s, None, "1")
    assert s.layout_warning("1") == "" and s.trial("1").layout_override
    workflow.set_trial_layout(s, None, "1", "L2")  # another layout: the acceptance does not carry over
    assert not s.trial("1").layout_override
    with pytest.raises(workflow.SetupError, match="not a date"):
        workflow.set_trial_info(s, None, "1", date="24/11/2026")


def test_r20_lock_is_per_trial(tmp_path, video):
    s = project()
    s.trial("1").raw_deleted_at = "2026-11-25T10:00:00"
    f = video(tmp_path / "t.mp4")
    with pytest.raises(workflow.SetupError, match="raw footage was deleted"):
        workflow.set_mapping(s, {("cam01", "1"): f})
    with pytest.raises(workflow.SetupError):
        workflow.set_trial_layout(s, None, "1", "L2")
    workflow.set_mapping(s, {("cam01", "2"): f})  # the other trial is unaffected
    assert s.trial("2").videos == {"cam01": str(f.resolve())}
    with pytest.raises(workflow.SetupError, match="not in its layout"):
        workflow.set_mapping(s, {("cam03", "2"): f})


def test_layout_and_camera_lifecycle(tmp_path):
    s = project()
    lay = workflow.add_layout(s, None, "Garden", ["cam02", "cam03"], date="2026-11-25")
    assert lay.id == "L3" and lay.date == "2026-11-25"
    with pytest.raises(workflow.SetupError, match="only be once"):
        workflow.add_layout(s, None, "Bad", ["cam01", "cam01"])
    with pytest.raises(workflow.SetupError, match="part of"):
        workflow.delete_camera(s, None, "cam03")
    s.run_state.stage_done["1"] = ["pose"]
    with pytest.raises(workflow.SetupError, match="Duplicate the layout"):
        workflow.edit_layout(s, None, "L1", cameras=["cam01"])
    with pytest.raises(workflow.SetupError, match="use Hall"):
        workflow.delete_layout(s, None, "L1")
    copy = workflow.duplicate_layout(s, None, "L1")
    assert copy.camera_ids() == ["cam01", "cam02"] and all(lc.extrinsic is None for lc in copy.cameras)
    workflow.delete_layout(s, None, "L3")
    assert [x.id for x in s.layouts] == ["L1", "L2", "L4"]


def test_default_participant_and_added_trials(tmp_path):
    s = project(n_trials=1)
    s.trials_per_participant = 2
    save_session(s, tmp_path)
    assert workflow.default_participant(s) == "1"
    t2 = workflow.add_trial(s, tmp_path, participant="previous")
    assert (t2.participant, t2.layout) == ("1", "L1")
    assert workflow.default_participant(s) is None  # participant 1 has their 2 trials
    t3 = workflow.add_trial(s, tmp_path, participant="previous", layout="L2", name="jump")
    assert t3.participant == "2" and t3.layout == "L2" and t3.name == "jump"
    t4 = workflow.duplicate_trial(s, tmp_path, "3")
    assert (t4.participant, t4.layout, t4.name, t4.videos) == ("2", "L2", "jump", {})


# ---------- readiness (§16.4) ----------

def test_readiness_lists_what_is_missing(tmp_path):
    s = project()
    s.participants[0].consent = Consent()
    s.participants[0].height_m = None
    s.trial("2").layout = ""
    missing = trial_missing(s, tmp_path, "1")
    assert any("consent" in m for m in missing) and any("height" in m for m in missing)
    assert any("reference points" in m for m in missing) and any("intrinsic video missing" in m for m in missing)
    assert any("No video chosen" in m for m in missing)
    assert "No layout chosen." in trial_missing(s, tmp_path, "2")
    assert not trial_ready(s, tmp_path, "1")


def test_readiness_ignores_unrelated_trials(tmp_path, monkeypatch):
    s = project()
    s.trial("2").layout = "L2"
    monkeypatch.setattr(service, "layout_state",
                        lambda session, root, layout: {"x": "ready" if layout == "L1" else "extrinsics missing"})
    monkeypatch.setattr(workflow, "trial_inputs_ready", lambda session, root, label: True)
    s.trial("1").videos = {"cam01": "a.mp4", "cam02": "b.mp4"}
    s.trial("2").videos = {"cam03": "c.mp4", "cam01": "d.mp4"}
    assert trial_ready(s, tmp_path, "1")
    assert trial_missing(s, tmp_path, "2") == ["x: extrinsics missing."]
    s.trial("1").date, s.layout("L1").date = "2026-11-25", "2026-11-20"
    assert not trial_ready(s, tmp_path, "1")
    workflow.accept_layout(s, None, "1")
    assert trial_ready(s, tmp_path, "1")


# ---------- migration of 0.1.x sessions ----------

def v1_session(root=None) -> dict:
    """A 0.1.0rc17 session.json: 2 cameras, cam02 moved for trial 2 (position 2), cam01 with a second lens video
    for trial 2."""
    raw = str(root / "raw") if root else "raw"
    ext = {"rms_px": 1.0, "rms_cm": 0.5, "status": "ok"}
    return {
        "schema": 1, "study_code": "T1", "n_trials": 2, "calibration_source": "app",
        "sync_cue_confirmed": True,
        "participants": [{"label": "1", "code": "T1-AAAAAAA", "height_m": 1.7},
                         {"label": "2", "code": "T1-BBBBBBB", "height_m": 1.8}],
        "trials": [{"label": "1", "participant": "1"}, {"label": "2", "participant": "2"}],
        "cameras": [
            {"name": "cam01", "source_dir": raw, "intrinsic": {"file": f"{raw}/c1i.mp4", "origin": "found"},
             "extrinsic": {"file": f"{raw}/c1e.mp4", "origin": "found"},
             "lenses": [{"key": "2", "file": f"{raw}/c1i2.mp4", "trials": ["2"]}],
             "trial_files": {"1": f"{raw}/c1t1.mp4", "2": f"{raw}/c1t2.mp4"}},
            {"name": "cam02", "source_dir": raw, "intrinsic": {"file": f"{raw}/c2i.mp4", "origin": "found"},
             "extrinsic": {"file": f"{raw}/c2e.mp4", "origin": "found"},
             "positions": [{"key": "2", "file": f"{raw}/c2e2.mp4", "trials": ["2"]}],
             "trial_files": {"1": f"{raw}/c2t1.mp4", "2": f"{raw}/c2t2.mp4"}},
        ],
        "extrinsics": [
            {"camera": "cam01", "error_px": 1.1, **ext},
            {"camera": "cam02", "error_px": 1.2, **ext},
            {"camera": "cam01", "lens": "2", "error_px": 2.1, **ext},
            {"camera": "cam02", "position": "2", "error_px": 2.2, **ext},
        ],
        "run_state": {"stage_done": {"1": ["pose", "triangulation"], "2": ["pose", "triangulation"]},
                      "stage_settings": {}},
    }


def test_upgrade_v1_cameras_layouts_and_videos():
    v1 = v1_session()
    from mocapapp.core.migrate import _old_deps

    for label in ("1", "2"):
        v1["run_state"]["stage_settings"][label] = {"triangulation": _old_deps(v1, label)}
    data = upgrade_v1(v1).data
    assert [c["id"] for c in data["cameras"]] == ["cam01", "cam01-2", "cam02"]
    assert [[lc["camera"] for lc in lay["cameras"]] for lay in data["layouts"]] == [["cam01", "cam02"],
                                                                                    ["cam01-2", "cam02"]]
    l2 = data["layouts"][1]["cameras"]
    assert l2[0]["result"]["error_px"] == 2.1 and l2[1]["result"]["error_px"] == 2.2
    assert l2[1]["extrinsic"]["file"].endswith("c2e2.mp4") and l2[0]["extrinsic"]["file"].endswith("c1e.mp4")
    t1, t2 = data["trials"]
    assert (t1["layout"], t2["layout"]) == ("L1", "L2")
    assert t2["videos"] == {"cam01-2": "raw/c1t2.mp4", "cam02": "raw/c2t2.mp4"}
    # finished triangulation stays fresh: its record is rewritten in the new form
    s = Session.model_validate(data)
    for label in ("1", "2"):
        expected = json.dumps(triangulation_deps([(slot, cam.id, lc.result)
                                                  for slot, cam, lc in s.trial_cameras(label)]))
        assert s.run_state.stage_settings[label]["triangulation"] == expected


def test_upgrade_v1_gives_participants_the_height_entered_with_their_trials():
    """0.1.0rc13 to rc17 asked the height with each trial; 0.2 keeps it on the participant (its 'to do' otherwise)."""
    v1 = v1_session()
    v1["participants"] += [{"label": "3", "code": "T1-CCCCCCC", "height_m": None}]
    v1["participants"][0]["height_m"] = None
    v1["trials"][0]["details"] = {"height": "1.83"}
    data = upgrade_v1(v1).data
    s = Session.model_validate(data)
    assert [p.height_m for p in s.participants] == [1.83, 1.8, None]  # the third has no trial
    assert s.trial("1").height_m == 1.83


def _write(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_migrate_folder_moves_files_and_keeps_a_backup(tmp_path):
    from mocapapp.core import runner

    root = tmp_path / "T1_20261120-1000"
    v1 = v1_session(tmp_path)
    _write(root / "session.json", json.dumps(v1))
    for cam in ("cam01", "cam02"):
        _write(root / "inputs" / cam / "intrinsic.mp4")
        _write(root / "inputs" / cam / "extrinsic.mp4")
        _write(root / "inputs" / cam / "extrinsic.mp4.source", "stamp")
        for label in ("1", "2"):
            _write(root / "inputs" / cam / f"trial_{label}.mp4")
        _write(root / "calibration_work" / f"intrinsics_{cam}.json", json.dumps({"camera": cam}))
        _write(root / "calibration_work" / f"extrinsics_{cam}.json", json.dumps({"camera": cam}))
        _write(root / "calibration_work" / f"extrinsic_{cam}_review.jpg")
    _write(root / "inputs" / "cam01" / "intrinsic_2.mp4")
    _write(root / "inputs" / "cam02" / "extrinsic_2.mp4")
    _write(root / "calibration_work" / "intrinsics_cam01_lens2.json", json.dumps({"camera": "cam01"}))
    _write(root / "calibration_work" / "extrinsics_cam01_lens2.json", json.dumps({"camera": "cam01"}))
    _write(root / "calibration_work" / "extrinsics_cam02_pos2.json", json.dumps({"camera": "cam02"}))
    _write(root / "calibration_work" / "Calib_scene.toml", "[cam01]\n")
    _write(root / "scene.json", json.dumps(SCENE))

    s = load_session(root)
    lay = Layout(root)
    assert s.schema_ == 2 and json.loads((root / BACKUP).read_text())["schema"] == 1
    assert (root / "scene.v1.json").is_file() and not (root / "scene.json").exists()
    assert s.presets[0].points == SCENE and all(x.points == SCENE and x.preset == "P1" for x in s.layouts)
    for cid in ("cam01", "cam01-2", "cam02"):
        assert lay.camera_input(cid).is_file()
        assert json.loads((lay.cameras_work / f"intrinsics_{cid}.json").read_text())["camera"] == cid
    assert lay.layout_input("L1", "cam02").is_file() and lay.layout_input("L2", "cam02").is_file()
    assert lay.layout_input("L2", "cam01-2").is_file()  # cam01 did not move: same extrinsic video in both layouts
    assert (lay.layout_input("L1", "cam01").parent / "cam01.mp4.source").read_text() == "stamp"
    assert json.loads((lay.layout_work("L2") / "extrinsics_cam01-2.json").read_text())["camera"] == "cam01-2"
    assert (lay.layout_work("L1") / "extrinsic_cam01_review.jpg").is_file()
    assert lay.trial_input("2", "cam01-2").is_file() and lay.trial_input("1", "cam02").is_file()
    assert lay.trial_calib("1").is_file() and lay.trial_calib("2").is_file()
    assert not (root / "calibration_work" / "Calib_scene.toml").exists()
    assert not (root / "inputs" / "cam01").exists() and not (root / "inputs" / "cam02").exists()
    assert runner.slots(s, "2") == ["cam01", "cam02"]
    # loading again changes nothing
    before = (root / "session.json").read_text()
    load_session(root)
    assert (root / "session.json").read_text() == before


@pytest.mark.parametrize("release", ["rc13", "rc15", "rc17"])
def test_migrate_older_releases(tmp_path, release):
    """rc13 had positions only, rc15 also the unit of the points, rc17 lenses: all load as schema 2."""
    v1 = v1_session(tmp_path)
    if release != "rc17":
        v1["cameras"][0].pop("lenses")
        v1["extrinsics"] = [e for e in v1["extrinsics"] if not e.get("lens")]
    if release == "rc13":
        v1["trials"] = [{"label": "1", "code": "T1-AAAAAAA", "height_m": 1.7},
                        {"label": "2", "code": "T1-BBBBBBB", "height_m": 1.8}]
        v1.pop("participants")
    _write(tmp_path / "s" / "session.json", json.dumps(v1))
    s = load_session(tmp_path / "s")
    assert s.schema_ == 2 and len(s.layouts) == 2 and len(s.participants) == 2
    assert s.trial("2").layout == "L2" and s.trial("1").layout == "L1"


# ---------- P2: dates, imports, copies ----------

def test_dates_follow_the_recording_unless_set(tmp_path, video):
    s = project()
    clip = video(tmp_path / "ext.mp4", creation_time="2026-03-04T10:00:00Z")
    lay = workflow.add_layout(s, None, "Garden", ["cam01", "cam02"])
    assert lay.date == workflow.today() and not lay.date_manual  # the day it was made, until a video says more
    workflow.set_extrinsic_video(s, None, lay.id, "cam01", clip)
    assert lay.date == "2026-03-04"
    fixed = workflow.add_layout(s, None, "Fixed", ["cam01"], date="2026-03-01")
    workflow.set_extrinsic_video(s, None, fixed.id, "cam01", clip)
    assert fixed.date == "2026-03-01" and fixed.date_manual
    workflow.set_mapping(s, {("cam01", "1"): clip})
    assert s.trial("1").date == "2026-03-04"
    workflow.set_trial_info(s, None, "2", date="2026-03-09")
    workflow.set_mapping(s, {("cam01", "2"): clip})
    assert s.trial("2").date == "2026-03-09"  # set by hand: kept


def test_import_refuses_an_old_project_and_itself(tmp_path):
    from mocapapp.core.importing import ImportError_, import_project

    old = tmp_path / "old"
    old.mkdir()
    (old / "session.json").write_text(json.dumps(v1_session()), encoding="utf-8")
    s = project()
    save_session(s, tmp_path / "here")
    with pytest.raises(ImportError_, match="Open it once with this version first"):
        import_project(s, tmp_path / "here", old, cameras=["cam01"])
    assert json.loads((old / "session.json").read_text())["schema"] == 1  # the other project is never changed
    with pytest.raises(ImportError_, match="That is this project"):
        import_project(s, tmp_path / "here", tmp_path / "here", cameras=["cam01"])


def test_calibration_copies_survive_a_new_trial_frame_rate():
    from mocapapp.core.assemble import _same_source

    old = json.dumps({"source": "a.mp4", "size": 1, "mtime": 2, "fps": 30.0})
    new = json.dumps({"source": "a.mp4", "size": 1, "mtime": 2, "fps": None})
    assert _same_source(old, new, frame_rate=False)  # calibration video: still the same copy
    assert not _same_source(old, new, frame_rate=True)  # trial video: converted again
    assert not _same_source(old, json.dumps({"source": "b.mp4", "size": 1, "mtime": 2}), frame_rate=False)
