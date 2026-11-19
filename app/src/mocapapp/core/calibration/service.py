"""Calibration steps on a session: intrinsics per camera, scene, extrinsics per camera, Calib_scene.toml."""

from __future__ import annotations

import shutil
from pathlib import Path

import cv2

from mocapapp.core.calibration import calibfile, extrinsics, intrinsics
from mocapapp.core.calibration.scene_points import Scene, check_scene, load_scene, save_scene
from mocapapp.core.session import Layout, Session

Progress = intrinsics.Progress


class CalibrationStepError(Exception):
    pass


def input_video(root: Path, camera: str, role: str, position: str = "") -> Path:
    """Normalised copy (upright, constant frame rate) - the same orientation the trials will have."""
    path = Layout(root).inputs(camera) / (f"{role}_{position}.mp4" if position else f"{role}.mp4")
    if not path.is_file():
        raise CalibrationStepError(f"{path} is missing. Prepare the videos first: mocapapp prepare --session {root}")
    return path


# ---------- intrinsics ----------

def run_intrinsics(session: Session, root: Path, cameras: list[str] | None = None, every_n_sec: float = 1.0,
                   force: bool = False, progress: Progress = print) -> list[intrinsics.IntrinsicsResult]:
    layout = Layout(root)
    spec = intrinsics.BoardSpec.from_settings(session.settings)
    results = []
    for cam in session.cameras:
        if cameras and cam.name not in cameras:
            continue
        video = input_video(root, cam.name, "intrinsic")
        result = None if force else intrinsics.load_result(layout.calibration_work, cam.name, video)
        if result is None:
            progress(f"{cam.name}: detecting the board in {video.name} ...")
            result = intrinsics.calibrate_video(cam.name, video, spec, every_n_sec,
                                                debug_dir=layout.calibration_work / "debug", progress=lambda _m: None)
            intrinsics.save_result(result, layout.calibration_work)
            _invalidate_calib(root)  # new lens model: the camera position must be recomputed with it
            extrinsics.result_path(layout.calibration_work, cam.name).unlink(missing_ok=True)
            for pos in cam.positions:
                extrinsics.result_path(layout.calibration_work, cam.name, pos.key).unlink(missing_ok=True)
        if cam.intrinsic is not None:
            cam.intrinsic.rms_px = result.rms_px
            cam.intrinsic.status = result.status
        results.append(result)
    return results


# ---------- scene ----------

def set_scene(root: Path, scene: Scene) -> list[str]:
    """Save the reference points. Changing points invalidates computed extrinsics (returns what was removed)."""
    layout = Layout(root)
    removed = []
    if layout.scene_json.is_file():
        old = load_scene(layout.scene_json)
        if [p.model_dump() for p in old.points] != [p.model_dump() for p in scene.points]:
            for f in sorted(layout.calibration_work.glob("extrinsics_*.json")):
                f.unlink()
                removed.append(f.name)
            if _invalidate_calib(root):
                removed.append(layout.calib_file.name)
    save_scene(scene, layout.scene_json)
    return removed


def get_scene(root: Path) -> Scene:
    path = Layout(root).scene_json
    if not path.is_file():
        raise CalibrationStepError(f"No reference points yet. Set them with: mocapapp scene set --session {root} ...")
    return load_scene(path)


# ---------- extrinsics ----------

def export_frame(root: Path, camera: str, frame_index: int = 0, position: str = "") -> Path:
    """Save the extrinsic frame as PNG so the points can be clicked (GUI) or measured in any image tool (CLI)."""
    frame = extrinsics.read_frame(input_video(root, camera, "extrinsic", position), frame_index)
    out = Layout(root).calibration_work / f"extrinsic_{camera}{_pos(position)}_f{frame_index}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), frame)
    return out


def _pos(position: str) -> str:
    return f"_pos{position}" if position else ""


def review_image(root: Path, camera: str, position: str = "") -> Path:
    return Layout(root).calibration_work / f"extrinsic_{camera}{_pos(position)}_review.jpg"


def run_extrinsics(session: Session, root: Path, camera: str, clicks: list[extrinsics.Click], frame_index: int = 0,
                   override: bool = False, position: str = "") -> extrinsics.ExtrinsicsResult:
    layout = Layout(root)
    scene = get_scene(root)
    blocking = [i for i in check_scene(scene) if i.level == "error"]
    if blocking:
        raise CalibrationStepError("Fix the reference points first:\n" + "\n".join(str(i) for i in blocking))
    intr = intrinsics.load_result(layout.calibration_work, camera, input_video(root, camera, "intrinsic"))
    if intr is None:
        raise CalibrationStepError(f"{camera}: compute the intrinsics first (mocapapp calibrate intrinsics).")
    result = extrinsics.solve_camera(camera, scene, clicks, intr.matrix, intr.distortions, frame_index)
    result.overridden = override and result.status == "fail"
    if position:
        session.camera(camera).position(position)  # KeyError for an unknown position
    extrinsics.save_result(result, layout.calibration_work, position)
    frame = extrinsics.read_frame(input_video(root, camera, "extrinsic", position), frame_index)
    cv2.imwrite(str(review_image(root, camera, position)), extrinsics.draw_review(frame, result))
    entry = {"camera": camera, "frame_index": frame_index, "n_points": result.n_points, "error_cm": result.rms_cm,
             "error_px": result.rms_px, "status": result.status, "overridden": result.overridden}
    if position:
        entry["position"] = position
    session.extrinsics = [e for e in session.extrinsics
                          if (e.get("camera"), e.get("position", "")) != (camera, position)] + [entry]
    session.extrinsics.sort(key=lambda e: (e["camera"], e.get("position", "")))
    _invalidate_calib(root)
    return result


# ---------- calibration file ----------

def _extrinsic_state(work: Path, camera: str, position: str = "") -> str:
    e = extrinsics.load_result(work, camera, position)
    if e is None:
        return "extrinsics missing"
    if e.status == "fail" and not e.overridden:
        return f"extrinsics error {e.rms_cm:.1f} cm (> {extrinsics.WARN_CM} cm, blocked)"
    return "ready"


def calibration_state(session: Session, root: Path) -> dict[str, str]:
    """Per camera (and per further camera position): 'ready', or what is still missing/blocking."""
    work = Layout(root).calibration_work
    if session.calibration_source == "imported":
        ok = Layout(root).calib_file.is_file()
        return {c.name: "ready" if ok else "imported calibration file missing" for c in session.cameras}
    state = {}
    for cam in session.cameras:
        i = intrinsics.load_result(work, cam.name)
        if i is None:
            state[cam.name] = "intrinsics missing"
        elif i.status == "fail":
            state[cam.name] = f"intrinsics failed ({i.rms_px:.2f} px)"
        else:
            state[cam.name] = _extrinsic_state(work, cam.name)
        for pos in cam.positions:
            state[f"{cam.name} position {pos.key}"] = (_extrinsic_state(work, cam.name, pos.key)
                                                       if state[cam.name] == "ready" else state[cam.name])
    return state


def write_calibration(session: Session, root: Path) -> Path:
    state = calibration_state(session, root)
    not_ready = {c: s for c, s in state.items() if s != "ready"}
    if not_ready:
        raise CalibrationStepError("Not every camera is calibrated: "
                                   + "; ".join(f"{c}: {s}" for c, s in not_ready.items()))
    layout = Layout(root)
    work = layout.calibration_work
    intr = [intrinsics.load_result(work, c.name) for c in session.cameras]
    extr = [extrinsics.load_result(work, c.name) for c in session.cameras]
    path = calibfile.write_calib(layout.calib_file, intr, extr)
    for old in work.glob("Calib_scene_trial_*.toml"):
        old.unlink()
    for trial in session.trials:  # trials recorded after a camera moved: that camera's later position
        keys = [c.position_for(trial.label) for c in session.cameras]
        if any(keys):
            moved = [extrinsics.load_result(work, c.name, k) for c, k in zip(session.cameras, keys, strict=True)]
            calibfile.write_calib(layout.trial_calib(trial.label), intr, moved)
    # keep already assembled trials in step: exactly one .toml per trial calibration folder
    for tdir in sorted(layout.trials.glob("trial_*")) if layout.trials.is_dir() else []:
        cdir = tdir / "calibration"
        cdir.mkdir(exist_ok=True)
        for old in cdir.glob("*.toml"):
            old.unlink()
        own = layout.trial_calib(tdir.name.removeprefix("trial_"))
        shutil.copy2(own if own.is_file() else path, cdir / "Calib_scene.toml")
    return path


def _invalidate_calib(root: Path) -> bool:
    """Remove the session calibration file and its copies in the trials (a stale copy would be used silently)."""
    layout = Layout(root)
    existed = layout.calib_file.is_file()
    layout.calib_file.unlink(missing_ok=True)
    if layout.calibration_work.is_dir():
        for own in layout.calibration_work.glob("Calib_scene_trial_*.toml"):
            own.unlink()
    if layout.trials.is_dir():
        for copy in layout.trials.glob("trial_*/calibration/*.toml"):
            copy.unlink()
    return existed
