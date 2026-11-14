"""Runner: resume, invalidation, failure isolation, cancel, sync gating, preflight; real subprocess plumbing."""

import threading
import time

import pytest

from mocapapp.core import runner
from mocapapp.core.session import Layout, load_session
from tests.m3_fakes import SYNC_BAD, FakeStages, make_session


@pytest.fixture
def fake(monkeypatch):
    def install(**kwargs):
        f = FakeStages(**kwargs)
        monkeypatch.setattr(runner, "run_stage", f)
        monkeypatch.setattr(runner, "_fps", lambda *a: 30.0)
        return f

    return install


def test_full_run_then_resume_skips_everything(tmp_path, fake):
    f = fake()
    session = make_session(tmp_path)
    events = []
    assert runner.run_session(session, tmp_path, listener=events.append) == {"1": "done", "2": "done"}
    assert len(f.calls) == 2 * len(runner.STAGES)
    assert any(e.kind == "progress" and 0 < e.fraction < 1 for e in events)
    saved = load_session(tmp_path)
    assert saved.run_state.stage_done["1"] == runner.STAGES and saved.run_state.trial_status["2"] == "done"

    f.calls.clear()
    assert runner.run_session(saved, tmp_path) == {"1": "done", "2": "done"}
    assert f.calls == []


def test_missing_output_reruns_that_stage_and_later_ones(tmp_path, fake):
    f = fake()
    session = make_session(tmp_path, n_trials=1)
    runner.run_session(session, tmp_path)
    for p in (Layout(tmp_path).trial("1") / "pose-3d").glob("*_filt_butterworth.trc"):
        p.unlink()
    f.calls.clear()
    runner.run_session(session, tmp_path)
    assert [s for _, s in f.calls] == ["filtering", "markerAugmentation", "kinematics"]


def test_one_failed_trial_does_not_stop_the_others(tmp_path, fake):
    fake(fail={"triangulation"}, fail_trials={"1"})
    session = make_session(tmp_path)
    assert runner.run_session(session, tmp_path) == {"1": "failed", "2": "done"}
    err = load_session(tmp_path).run_state.errors["1"]
    assert err.startswith("Computing 3D positions") and "calibration" in err


def test_nonzero_exit_with_outputs_is_still_a_failure(tmp_path, fake, monkeypatch):
    f = fake()
    session = make_session(tmp_path, n_trials=1)

    def exit_1(trial_dir, stage, on_line, cancel=None, code=None):
        f(trial_dir, stage, on_line)
        return 1 if stage == "filtering" else 0

    monkeypatch.setattr(runner, "run_stage", exit_1)
    assert runner.run_session(session, tmp_path) == {"1": "failed"}
    assert "filtering.log" in session.run_state.errors["1"]


def test_cancel_then_resume(tmp_path, fake):
    f = fake(cancel_at="triangulation")
    session = make_session(tmp_path, n_trials=1)
    with pytest.raises(runner.Cancelled):
        runner.run_session(session, tmp_path)
    assert load_session(tmp_path).run_state.trial_status["1"] == "cancelled"
    f.calls.clear()
    assert runner.run_session(load_session(tmp_path), tmp_path) == {"1": "done"}
    assert f.calls[0] == ("1", "triangulation")


def test_unreliable_sync_stops_before_triangulation_even_on_resume(tmp_path, fake):
    f = fake(sync_log=SYNC_BAD)
    session = make_session(tmp_path, n_trials=1)
    events = []
    assert runner.run_session(session, tmp_path, listener=events.append) == {"1": "needs_sync_fix"}
    assert [s for _, s in f.calls] == ["poseEstimation", "synchronization"]
    assert any(e.kind == "needs_sync_fix" and "0.31" in e.message for e in events)

    f.calls.clear()
    assert runner.run_session(load_session(tmp_path), tmp_path) == {"1": "needs_sync_fix"}
    assert f.calls == []  # resume does not slip past the gate

    assert runner.run_session(load_session(tmp_path), tmp_path, accept_sync=True) == {"1": "done"}
    s = load_session(tmp_path)
    assert s.run_state.sync["1"]["accepted"] is True
    assert s.run_state.sync["1"]["offsets"]["cam02"]["offset_frames"] == -1


def test_parse_sync_and_issues():
    log = ("--> Camera cam01 and cam02: 0 frames offset, correlation 0.86.\n"
           "--> Camera cam01 and cam03: -200 frames offset (some note), correlation 0.7.\n"
           "--> Camera cam01 and cam04: 3 frames offset, correlation nan.\n")
    offsets = runner.parse_sync(log)
    assert offsets["cam03"]["offset_frames"] == -200 and offsets["cam02"]["correlation"] == 0.86
    issues = runner.sync_issues(offsets, fps=30)
    assert len(issues) == 2
    assert any("cam03" in i and "implausible" in i for i in issues)
    assert any("cam04" in i and "low confidence" in i for i in issues)
    assert runner.sync_issues({}, 30) == ["No synchronisation result was found in the log."]


def test_preflight(tmp_path):
    session = make_session(tmp_path, ready=False)
    issues = runner.preflight(session, tmp_path, ["1", "2"])
    assert sum(i.level == "action" for i in issues) == 3  # sync cue + consent for each trial
    assert not [i for i in issues if i.level == "error"]

    tdir = Layout(tmp_path).trial("1")
    cfg = tdir / "Config.toml"
    cfg.write_text(cfg.read_text().replace("synchronization_gui = false", "synchronization_gui = true"))
    (tdir / "calibration" / "other.toml").write_text("")
    (tdir / "videos" / "cam02.mp4").unlink()
    errors = [i.what for i in runner.preflight(session, tmp_path, ["1"]) if i.level == "error"]
    assert any("synchronization_gui" in e for e in errors)
    assert any("one calibration file, found 2" in e for e in errors)
    assert any("cam02.mp4" in e for e in errors)


def test_run_stage_streams_lines_logs_and_exit_code(tmp_path):
    seen = []
    code = ("import sys; print('start'); sys.stdout.write('a 10%|#\\r a 50%|#####\\r'); sys.stdout.flush(); "
            "print('caf\\u00e9 \\u2713'); sys.exit(3)")
    assert runner.run_stage(tmp_path, "filtering", seen.append, code=code) == 3
    assert seen[0] == "start" and any("50%|" in s for s in seen) and "café ✓" in seen
    assert "café ✓" in (tmp_path / runner.LOG_DIR / "filtering.log").read_text(encoding="utf-8")


def test_run_stage_cancel_kills_a_silent_process(tmp_path):
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    t0 = time.perf_counter()
    with pytest.raises(runner.Cancelled):
        runner.run_stage(tmp_path, "poseEstimation", lambda s: None, cancel, code="import time; time.sleep(60)")
    assert time.perf_counter() - t0 < 10


def test_preflight_rejects_a_cutoff_at_half_the_frame_rate(tmp_path, monkeypatch):
    session = make_session(tmp_path, n_trials=1)
    monkeypatch.setattr(runner, "_fps", lambda *a: 30.0)
    session.settings.filter.cutoff_hz = 15.0  # = fps / 2: the Butterworth filter refuses it
    errors = [i for i in runner.preflight(session, tmp_path, ["1"]) if i.level == "error"]
    assert len(errors) == 1 and "below half the frame rate (15 Hz)" in errors[0].what and "14 Hz" in errors[0].fix
    session.settings.filter.cutoff_hz = 14.0
    assert not [i for i in runner.preflight(session, tmp_path, ["1"]) if i.level == "error"]


def test_filter_failure_is_explained(tmp_path):
    session = make_session(tmp_path, n_trials=1)
    tdir = Layout(tmp_path).trial("1")
    (tdir / runner.LOG_DIR).mkdir()
    (tdir / runner.LOG_DIR / "filtering.log").write_text(
        "ValueError: Digital filter critical frequencies must be 0 < Wn < 1\n")
    assert "too high for this video's frame rate" in runner.check_outputs(tdir, "filtering", ["cam01"], session)


@pytest.mark.parametrize("change, rerun", [
    (lambda s: setattr(s.settings.filter, "cutoff_hz", 8.0), ["filtering", "markerAugmentation", "kinematics"]),
    (lambda s: setattr(s.settings, "simple_model", True), ["kinematics"]),
    (lambda s: setattr(s.settings, "keypoints", "Whole_body"), runner.STAGES),
    (lambda s: setattr(s.settings, "pose_mode", "lightweight"), runner.STAGES),
])
def test_changed_settings_rerun_only_the_affected_stages(tmp_path, fake, change, rerun):
    f = fake()
    session = make_session(tmp_path, n_trials=1)
    runner.run_session(session, tmp_path)
    change(session)
    assert runner.stale_stages(session, "1") == rerun[:1]  # the changed stage; later ones follow
    f.calls.clear()
    assert runner.run_session(session, tmp_path) == {"1": "done"}
    assert [s for _, s in f.calls] == rerun
    assert runner.stale_stages(session, "1") == []


def test_sessions_without_recorded_settings_are_not_rerun(tmp_path, fake):
    f = fake()
    session = make_session(tmp_path, n_trials=1)
    runner.run_session(session, tmp_path)
    session.run_state.stage_settings.clear()  # as in sessions made before 0.1.0rc6
    session.settings.filter.cutoff_hz = 8.0
    f.calls.clear()
    runner.run_session(session, tmp_path)
    assert f.calls == []


def test_new_settings_at_their_defaults_keep_older_records_valid(tmp_path, fake):
    """A record written before keypoints / simple_model existed still matches the defaults."""
    import json

    session = make_session(tmp_path, n_trials=1)
    assert json.loads(runner.stage_inputs(session, "poseEstimation")) == ["balanced"]
    assert json.loads(runner.stage_inputs(session, "kinematics")) == [True, True]


def test_preflight_warns_when_pose_models_must_be_downloaded(tmp_path, monkeypatch):
    session = make_session(tmp_path, n_trials=1)
    monkeypatch.setattr(runner, "_missing_models", lambda mode, kp: ["x"] if kp == "Whole_body" else [])
    assert not [i for i in runner.preflight(session, tmp_path, ["1"]) if i.where == "pose models"]
    session.settings.keypoints = "Whole_body"
    warn = [i for i in runner.preflight(session, tmp_path, ["1"]) if i.where == "pose models"]
    assert len(warn) == 1 and warn[0].level == "warning" and "internet" in warn[0].what
