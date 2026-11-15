from pathlib import Path

import pytest

from mocapapp.core.session import CalibVideo, Camera, Session, VideoRef
from mocapapp.core.validate import check_output_root, map_by_digit, validate


def make_session(dirs, n_trials=2):
    return Session.model_validate({
        "n_trials": n_trials, "trials": [{"label": str(i + 1), "code": f"C{i}"} for i in range(n_trials)],
        "cameras": [Camera(name=f"cam{i + 1:02d}", source_dir=str(d)) for i, d in enumerate(dirs)]})


def levels(report, level):
    return [i for i in report.issues if i.level == level]


def test_digit_mapping_happy_path(camera_dirs):
    s = make_session(camera_dirs(trial_names=("take_1", "take_2")))
    r = validate(s)
    assert r.ok, [str(i) for i in r.issues]
    assert r.mapping_method == "digit" and not r.needs_mapping_review
    assert [t.label for t in s.trials] == ["1", "2"]
    assert Path(s.cameras[0].trial_files["2"]).name == "take_2.mp4"
    assert Path(s.cameras[1].intrinsic.file).name == "intrinsic.mp4"


def test_gopro_names_fall_back_to_time(camera_dirs):
    s = make_session(camera_dirs(trial_names=("GX010012", "GX010022")))
    r = validate(s)
    assert r.mapping_method == "chronological" and r.needs_mapping_review
    assert [t.label for t in s.trials] == ["1", "2"]


def test_chronological_uses_creation_time(camera_dirs, tmp_path):
    dirs = camera_dirs(trial_names=("GX010012", "GX010022"))
    # make the alphabetically later file the earlier recording on every camera
    from tests.conftest import make_video

    for d in dirs:
        make_video(d / "GX010022.mp4", creation_time="2026-01-01T10:00:00Z")
        make_video(d / "GX010012.mp4", creation_time="2026-01-01T11:00:00Z")
    s = make_session(dirs)
    validate(s)
    assert Path(s.cameras[0].trial_files["1"]).name == "GX010022.mp4"


def test_map_by_digit_requires_same_digits_everywhere():
    per_cam = {"cam01": [Path("a1.mp4"), Path("a2.mp4")], "cam02": [Path("b1.mp4"), Path("b3.mp4")]}
    assert map_by_digit(per_cam, 2) is None


def test_missing_extrinsic_needs_manual_pick(camera_dirs, video, tmp_path):
    dirs = camera_dirs()
    (dirs[1] / "extrinsic.mp4").unlink()
    s = make_session(dirs)
    r = validate(s)
    assert not r.ok and any(i.where == "cam02 extrinsic" for i in levels(r, "action"))
    other = video(tmp_path / "elsewhere" / "cam2_ext.mp4", seconds=0.77)
    s.cameras[1].extrinsic = VideoRef(file=str(other), origin="manual")
    assert validate(s).ok


def test_same_file_for_both_roles_rejected(camera_dirs):
    dirs = camera_dirs()
    s = make_session(dirs)
    s.cameras[0].intrinsic = CalibVideo(file=str(dirs[0] / "extrinsic.mp4"), origin="manual")
    assert any("same file" in i.what for i in levels(validate(s), "error"))


def test_calibration_video_shared_between_cameras_rejected(camera_dirs):
    dirs = camera_dirs()
    s = make_session(dirs)
    s.cameras[1].intrinsic = CalibVideo(file=str(dirs[0] / "intrinsic.mp4"), origin="manual")
    assert any("Same recording" in i.what for i in levels(validate(s), "error"))


def test_intrinsic2_is_a_trial_with_warning(camera_dirs):
    r = validate(make_session(camera_dirs(trial_names=("intrinsic2", "trial1"))))
    assert any("treated as a trial video" in i.what for i in levels(r, "warning"))


def test_wrong_trial_count(camera_dirs):
    r = validate(make_session(camera_dirs(trial_names=("t1", "t2", "t3"))))
    assert any("3 trial videos" in i.what for i in levels(r, "error"))


def test_nested_dirs_rejected(camera_dirs):
    dirs = camera_dirs(n_cams=2)
    nested = dirs[0] / "sub"
    nested.mkdir()
    assert any("inside each other" in i.what for i in levels(validate(make_session([dirs[0], nested])), "error"))


def test_low_res_and_two_cameras_warn(camera_dirs):
    r = validate(make_session(camera_dirs(n_cams=2)))
    whats = " ".join(i.what for i in levels(r, "warning"))
    assert "below 1080p" in whats and "Only 2 cameras" in whats


@pytest.mark.parametrize("name,ok", [("session spike", True), ("sessiøn", False)])
def test_output_root_ascii(tmp_path, name, ok):
    errors = [i for i in check_output_root(tmp_path / name) if i.level == "error"]
    assert (not errors) == ok
