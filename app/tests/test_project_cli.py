"""P2 (PROJECT.md §16.5): a whole project from raw videos by CLI only, and import into another project."""

import json

import numpy as np

from mocapapp.cli.main import main
from mocapapp.core import runner
from mocapapp.core.calibration.scene_points import to_csv
from mocapapp.core.session import Layout, load_session
from tests.conftest import make_video
from tests.m3_fakes import FakeStages
from tests.test_calibration import board_views, good_scene, look_at, project, write_video

CAMS = {"cam01": [-2.0, -1.5, 1.6], "cam02": [5.0, -1.5, 1.6], "cam03": [1.5, 4.0, 2.0]}


def _clicks(tmp_path, scene, cam, pos):
    r, t = look_at(np.array(pos), [1.5, 1.0, 0.6])
    uv = project(scene.array(), r, t)
    csv = tmp_path / f"{cam}_clicks.csv"
    csv.write_text("\n".join(["label,u,v"] + [f"{p.label},{u:.2f},{v:.2f}"
                                              for p, (u, v) in zip(scene.points, uv, strict=True)]))
    return str(csv)


def _raw(tmp_path):
    raw = tmp_path / "raw"
    for i, cam in enumerate(CAMS, 1):
        d = raw / cam
        d.mkdir(parents=True)
        frames = board_views(n=24)
        write_video(frames[i:] + frames[:i], d / "intrinsic.mp4")
        make_video(d / "extrinsic.mp4", seconds=0.3 + 0.05 * i, size="1280x720", pattern="smptebars")
        for n in (1, 2):
            make_video(d / f"trial{n}.mp4", seconds=0.4 + 0.05 * i + 0.1 * n, size="1280x720")
    return raw


def test_whole_project_by_cli_then_import(tmp_path, capsys, monkeypatch):
    raw = _raw(tmp_path)
    out = tmp_path / "projects"
    pfile = tmp_path / "hall study.mocapproj"
    assert main(["project", "new", "--out", str(out), "--study", "T1", "--file", str(pfile)]) == 0
    root = next(out.iterdir())
    s = str(pfile)  # every command accepts the project file as well as the folder

    # cameras: the 'intrinsic' video in each folder is found by name; lens calibration
    for cam in CAMS:
        assert main(["camera", "add", "--session", s, "--folder", str(raw / cam), "--zoom", "wide"]) == 0
    assert "intrinsic video intrinsic.mp4" in capsys.readouterr().out
    assert main(["camera", "calibrate", "--session", s, "--every", "0.5"]) == 0
    text = capsys.readouterr().out
    assert text.count("-> OK") + text.count("-> WARN") == 3, text
    assert main(["camera", "list", "--session", s]) == 0
    assert capsys.readouterr().out.count("ready") == 3

    # reference points as a preset, a layout using it, its extrinsic videos and clicks
    scene = good_scene()
    (tmp_path / "points.csv").write_text(to_csv(scene.points))
    assert main(["points", "preset", "save", "--session", s, "--name", "Hall", "--csv", str(tmp_path / "points.csv"),
                 "--origin", "tape cross", "--x-axis", "the window"]) == 0
    assert main(["points", "preset", "check", "--session", s, "--preset", "P1"]) == 0
    assert main(["layout", "new", "--session", s, "--name", "Hall", "--cameras", "cam01,cam02,cam03",
                 "--preset", "P1"]) == 0
    assert main(["layout", "edit", "--session", s, "--layout", "L1"]
                + [a for cam in CAMS for a in ("--video", f"{cam}={raw / cam / 'extrinsic.mp4'}")]) == 0
    capsys.readouterr()
    assert main(["layout", "click", "--session", s, "--layout", "L1", "--camera", "cam01"]) == 0
    assert "extrinsic_cam01_f0.png" in capsys.readouterr().out
    for cam, pos in CAMS.items():
        assert main(["layout", "click", "--session", s, "--layout", "L1", "--camera", cam,
                     "--points", _clicks(tmp_path, scene, cam, pos)]) == 0
    assert main(["layout", "status", "--session", s]) == 0
    assert capsys.readouterr().out.count(": ready") == 3

    # a trial: a new participant (the first trial), its videos by name, consent and height, check, run
    assert main(["trial", "add", "--session", s, "--name", "walk"]) == 0
    assert "Trial 1 added: participant 1" in capsys.readouterr().out
    assert main(["trial", "videos", "--session", s, "--trial", "1", "--suggest"]) == 0
    assert capsys.readouterr().out.count("trial1.mp4") == 3
    assert main(["trial", "status", "--session", s]) == 1
    out_text = capsys.readouterr().out
    assert "consent" in out_text and "height" in out_text and "not checked and copied" in out_text
    assert main(["participant", "set", "--session", s, "--participant", "1", "--height", "175 cm"]) == 0
    assert main(["participant", "consent", "--session", s, "--participant", "1", "--state", "delete"]) == 0
    assert main(["trial", "check", "--session", s, "--trial", "1"]) == 0
    assert main(["trial", "status", "--session", s]) == 0
    assert "trial 1: ready" in capsys.readouterr().out
    monkeypatch.setattr(runner, "run_stage", FakeStages())
    monkeypatch.setattr(runner, "_fps", lambda *a: 30.0)
    monkeypatch.setattr(runner, "estimate_seconds", lambda *a: 60)
    assert main(["run", "--session", str(root), "--trial", "1"]) == 0
    assert main(["trial", "list", "--session", s]) == 0
    assert "walk" in capsys.readouterr().out.splitlines()[-1]

    # R6 by CLI: the camera ran, so its lens stays; duplicate it instead
    assert main(["camera", "edit", "--session", s, "--camera", "cam01", "--intrinsic",
                 str(raw / "cam02" / "intrinsic.mp4")]) == 1
    assert "Duplicate the camera" in capsys.readouterr().out
    assert main(["camera", "delete", "--session", s, "--camera", "cam01"]) == 1
    assert main(["camera", "duplicate", "--session", s, "--camera", "cam01"]) == 0
    assert "as cam04" in capsys.readouterr().out

    # a second trial on another day: a new participant (1 trial each), the layout warning and its override
    assert main(["trial", "add", "--session", s, "--date", "2026-01-05"]) == 0
    text = capsys.readouterr().out
    assert "participant 2" in text and "Were the cameras set up again?" in text
    assert main(["trial", "status", "--session", s, "--trial", "2"]) == 1
    assert "Were the cameras set up again?" in capsys.readouterr().out
    assert main(["trial", "edit", "--session", s, "--trial", "2", "--accept-layout"]) == 0
    assert main(["participant", "list", "--session", s]) == 0
    text = capsys.readouterr().out
    assert "Signed, delete footage" in text and "Not signed yet" in text and "height missing" in text
    assert main(["project", "info", "--session", s]) == 0
    assert "trials: 2 (1 done, 1 not ready)" in capsys.readouterr().out

    # import into another project: cameras, presets and layouts with their videos and results, no people
    assert main(["project", "new", "--out", str(tmp_path / "other")]) == 0
    other = next((tmp_path / "other").iterdir())
    assert main(["project", "import", "--session", str(other), "--from", str(pfile), "--list"]) == 0
    assert "layout L1: Hall" in capsys.readouterr().out
    assert main(["project", "import", "--session", str(other), "--from", str(pfile), "--layouts"]) == 0
    text = capsys.readouterr().out
    assert "Layout Hall" in text and "with its lens calibration" in text
    imported = load_session(other)
    assert [c.id for c in imported.cameras] == ["cam01", "cam02", "cam03"]  # only the layout's cameras
    assert imported.participants == [] and imported.trials == [] and imported.presets == []
    assert imported.layouts[0].imported_from == root.name and imported.layouts[0].points["points"]
    assert Layout(other).layout_input("L1", "cam02").is_file() and Layout(other).camera_input("cam03").is_file()
    assert main(["layout", "status", "--session", str(other)]) == 0
    assert capsys.readouterr().out.count(": ready") == 3  # nothing to calibrate again
    assert main(["project", "import", "--session", str(other), "--from", str(pfile), "--points", "--cameras"]) == 0
    imported = load_session(other)
    assert [p.id for p in imported.presets] == ["P1"] and len(imported.cameras) == 7  # copies, never merged
    data = json.loads((other / "session.json").read_text(encoding="utf-8"))
    assert "T1-" not in json.dumps(data)  # no pseudonym of the other project


def test_cli_errors_are_plain(tmp_path, capsys):
    assert main(["project", "new", "--out", str(tmp_path / "p")]) == 0
    s = str(next((tmp_path / "p").iterdir()))
    capsys.readouterr()
    assert main(["layout", "new", "--session", s, "--name", "x", "--cameras", "cam09"]) == 1
    assert capsys.readouterr().out.strip() == "No camera cam09 in this project."
    assert main(["trial", "videos", "--session", s, "--trial", "7"]) == 1
    assert main(["project", "import", "--session", s, "--from", str(tmp_path / "nowhere")]) == 1
    assert "is not a project" in capsys.readouterr().out
    assert main(["trial", "add", "--session", s]) == 0  # no layout yet: the trial waits for one
    assert main(["trial", "status", "--session", s]) == 1
    assert "No layout chosen." in capsys.readouterr().out
