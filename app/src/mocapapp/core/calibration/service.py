"""Calibration on a project (PROJECT.md §16.2): intrinsics per camera, reference points and extrinsics per layout,
one Calib_scene.toml per trial.

A camera's lens calibration is computed from its intrinsic video and reused by every layout that includes it. A
layout holds reference points and one extrinsic video per camera; each camera's position is solved from the points
clicked in its video, with that camera's lens calibration. A trial uses one layout: its calibration file holds the
layout's cameras, named cam01, cam02, ... in the layout's order (the names of the trial's videos).
"""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path

import cv2

from mocapapp.core.calibration import calibfile, extrinsics, intrinsics
from mocapapp.core.calibration.scene_points import Scene, check_scene
from mocapapp.core.session import Camera, CameraLayout, Layout, PointsPreset, Session

Progress = intrinsics.Progress


class CalibrationStepError(Exception):
    pass


# ---------- normalised copies of the calibration videos ----------

def camera_video(root: Path, camera: str) -> Path:
    """Normalised copy (upright, constant frame rate) of a camera's intrinsic video."""
    path = Layout(root).camera_input(camera)
    if not path.is_file():
        raise CalibrationStepError(f"{camera}: its intrinsic video is not in the project yet. Prepare the videos "
                                   "first (mocapapp prepare).")
    return path


def layout_video(root: Path, layout: str, camera: str) -> Path:
    path = Layout(root).layout_input(layout, camera)
    if not path.is_file():
        raise CalibrationStepError(f"{camera} in layout {layout}: its extrinsic video is not in the project yet. "
                                   "Prepare the videos first (mocapapp prepare).")
    return path


def camera_title(session: Session, camera: str) -> str:
    try:
        cam = session.camera(camera)
    except KeyError:
        return camera
    return cam.name if cam.name == cam.id else f"{cam.name} ({cam.id})"


# ---------- intrinsics ----------

def lens_result(root: Path, camera: str) -> intrinsics.IntrinsicsResult | None:
    """The lens calibration of a camera, if computed from the intrinsic video now in the project."""
    work = Layout(root).cameras_work
    video = Layout(root).camera_input(camera)
    return intrinsics.load_result(work, camera, video if video.is_file() else None)


def ensure_recalibratable(session: Session, camera: str) -> None:
    """R6: a camera used by a trial that has started running keeps its calibration."""
    if session.camera_used(camera):
        users = sorted({t.label for lay in session.layouts_with_camera(camera)
                        for t in session.trials_with_layout(lay.id) if session.run_state.stage_done.get(t.label)},
                       key=lambda s: (len(s), s))
        raise CalibrationStepError(
            f"{camera_title(session, camera)} is already used by trial(s) {', '.join(users)}, so its lens "
            "calibration cannot change. Duplicate the camera and calibrate the copy instead.")


def run_intrinsics(session: Session, root: Path, cameras: list[str] | None = None, every_n_sec: float = 1.0,
                   force: bool = False, progress: Progress = print) -> list[intrinsics.IntrinsicsResult]:
    layout = Layout(root)
    spec = intrinsics.BoardSpec.from_settings(session.settings)
    results = []
    for cam in session.cameras:
        if (cameras and cam.id not in cameras) or cam.intrinsic is None:
            continue
        video = camera_video(root, cam.id)
        result = None if force else intrinsics.load_result(layout.cameras_work, cam.id, video)
        if result is None:
            if intrinsics.result_path(layout.cameras_work, cam.id).is_file():
                ensure_recalibratable(session, cam.id)
            progress(f"{camera_title(session, cam.id)}: detecting the board in {video.name} ...")
            result = intrinsics.calibrate_video(cam.id, video, spec, every_n_sec,
                                                debug_dir=layout.cameras_work / "debug", progress=lambda _m: None)
            intrinsics.save_result(result, layout.cameras_work)
            for lay in session.layouts_with_camera(cam.id):  # positions solved again with the new lens model
                resolve_layout(session, root, lay.id, cameras=[cam.id])
        cam.intrinsic.rms_px = result.rms_px
        cam.intrinsic.status = result.status
        results.append(result)
    return results


# ---------- reference points ----------

def layout_scene(session: Session, layout: str) -> Scene:
    lay = session.layout(layout)
    if lay.points is None:
        raise CalibrationStepError(f"Layout {lay.name} has no reference points yet. Enter them first "
                                   "(mocapapp layout points).")
    return Scene.model_validate(lay.points)


def scene_data(scene: Scene) -> dict:
    return scene.model_dump(mode="json")


def set_layout_points(session: Session, root: Path, layout: str, scene: Scene, preset: str = "") -> list[str]:
    """Save a layout's reference points. Returns plain-language notes on what happened to its camera positions.

    When the points changed, every camera position is recomputed from its saved clicks: the clicked spots did not
    move, only their coordinates changed (a corrected measurement, another unit). Clicks of removed points are
    dropped; a position that cannot be recomputed is removed and has to be clicked again. The caller saves."""
    lay = session.layout(layout)
    old = lay.points
    lay.points = scene_data(scene)
    if preset:
        lay.preset = preset
    if old is None or old.get("points") == lay.points.get("points"):
        return []
    _invalidate_trials(session, root, [t.label for t in session.trials_with_layout(layout)])
    notes = []
    labels = set(scene.labels())
    for lc in lay.cameras:
        saved = position_result(root, layout, lc.camera)
        if saved is None:
            continue
        where = f"{camera_title(session, lc.camera)} in {lay.name}"
        clicks = [extrinsics.Click(**c) for c in saved.clicks if c.get("label") in labels]
        try:
            new = _solve(session, root, layout, lc.camera, clicks, saved.frame_index, saved.overridden)
            notes.append(f"{where}: camera position recomputed from your clicks (error {new.rms_cm:.1f} cm, "
                         f"{new.status}).")
        except Exception as exc:  # any reason: fall back to clicking again
            notes.append(f"{where}: could not be recomputed ({exc}). Click the points again.")
            _forget(root, lay, lc.camera)
    return notes


def save_preset(session: Session, name: str, scene: Scene) -> PointsPreset:
    preset = PointsPreset(id=session.new_id("P"), name=name.strip() or "Reference points", points=scene_data(scene))
    session.presets.append(preset)
    return preset


# ---------- extrinsics ----------

def export_frame(root: Path, layout: str, camera: str, frame_index: int = 0) -> Path:
    """Save the extrinsic frame as PNG so the points can be clicked (GUI) or measured in any image tool (CLI)."""
    frame = extrinsics.read_frame(layout_video(root, layout, camera), frame_index)
    out = Layout(root).layout_work(layout) / f"extrinsic_{camera}_f{frame_index}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), frame)
    return out


def review_image(root: Path, layout: str, camera: str) -> Path:
    return Layout(root).layout_work(layout) / f"extrinsic_{camera}_review.jpg"


def position_result(root: Path, layout: str, camera: str) -> extrinsics.ExtrinsicsResult | None:
    """The solved position of a camera in a layout (it also holds the clicks)."""
    return extrinsics.load_result(Layout(root).layout_work(layout), camera)


def _forget(root: Path, lay: CameraLayout, camera: str) -> None:
    extrinsics.result_path(Layout(root).layout_work(lay.id), camera).unlink(missing_ok=True)
    lay.entry(camera).result = None


def _solve(session: Session, root: Path, layout: str, camera: str, clicks: list[extrinsics.Click],
           frame_index: int, override: bool) -> extrinsics.ExtrinsicsResult:
    lay = session.layout(layout)
    entry = lay.entry(camera)  # KeyError for a camera not in the layout
    scene = layout_scene(session, layout)
    blocking = [i for i in check_scene(scene) if i.level == "error"]
    if blocking:
        raise CalibrationStepError("Fix the reference points first:\n" + "\n".join(str(i) for i in blocking))
    intr = lens_result(root, camera)
    if intr is None:
        raise CalibrationStepError(f"{camera_title(session, camera)}: compute its lens calibration first "
                                   "(mocapapp calibrate intrinsics).")
    result = extrinsics.solve_camera(camera, scene, clicks, intr.matrix, intr.distortions, frame_index)
    result.overridden = override and result.status == "fail"
    extrinsics.save_result(result, Layout(root).layout_work(layout))
    entry.result = {"frame_index": frame_index, "n_points": result.n_points, "error_cm": result.rms_cm,
                    "error_px": result.rms_px, "status": result.status, "overridden": result.overridden}
    return result


def run_extrinsics(session: Session, root: Path, layout: str, camera: str, clicks: list[extrinsics.Click],
                   frame_index: int = 0, override: bool = False) -> extrinsics.ExtrinsicsResult:
    """A camera's position in a layout, from the reference points clicked in its extrinsic video."""
    result = _solve(session, root, layout, camera, clicks, frame_index, override)
    frame = extrinsics.read_frame(layout_video(root, layout, camera), frame_index)
    cv2.imwrite(str(review_image(root, layout, camera)), extrinsics.draw_review(frame, result))
    _invalidate_trials(session, root, [t.label for t in session.trials_with_layout(layout)])
    return result


def resolve_layout(session: Session, root: Path, layout: str, cameras: list[str] | None = None) -> None:
    """Solve camera positions again from their saved clicks (after a new lens calibration). A position that cannot
    be solved any more is removed and has to be clicked again."""
    lay = session.layout(layout)
    for lc in lay.cameras:
        if cameras is not None and lc.camera not in cameras:
            continue
        saved = position_result(root, layout, lc.camera)
        if saved is None:
            continue
        try:
            _solve(session, root, layout, lc.camera, [extrinsics.Click(**c) for c in saved.clicks],
                   saved.frame_index, saved.overridden)
        except Exception:  # anything: this position has to be clicked again
            _forget(root, lay, lc.camera)
    _invalidate_trials(session, root, [t.label for t in session.trials_with_layout(layout)])


# ---------- state ----------

def _intrinsic_state(root: Path, cam: Camera) -> str:
    if cam.intrinsic is None:
        return "intrinsic video missing"
    i = lens_result(root, cam.id)
    if i is None:
        return "intrinsics missing"
    if i.status == "fail":
        return f"intrinsics failed ({i.rms_px:.2f} px)"
    return "ready"


def _extrinsic_state(root: Path, layout: str, camera: str) -> str:
    e = position_result(root, layout, camera)
    if e is None:
        return "extrinsics missing"
    if e.status == "fail" and not e.overridden:
        return f"extrinsics error {e.rms_cm:.1f} cm (> {extrinsics.WARN_CM} cm, blocked)"
    return "ready"


def layout_state(session: Session, root: Path, layout: str) -> dict[str, str]:
    """Per camera of a layout (and its reference points): 'ready', or what is still missing/blocking."""
    lay = session.layout(layout)
    state = {}
    if lay.points is None:
        state[f"{lay.name}: reference points"] = "missing"
    elif any(i.level == "error" for i in check_scene(Scene.model_validate(lay.points))):
        state[f"{lay.name}: reference points"] = "need fixing"
    if not lay.cameras:
        state[f"{lay.name}: cameras"] = "none chosen"
    for lc in lay.cameras:
        title = f"{lay.name}: {camera_title(session, lc.camera)}"
        cam = session.camera(lc.camera)
        own = _intrinsic_state(root, cam)
        state[title] = own if own != "ready" else _extrinsic_state(root, layout, lc.camera)
    return state


def trial_calibration_state(session: Session, root: Path, label: str) -> dict[str, str]:
    if session.calibration_source == "imported":
        ok = Layout(root).calib_file.is_file()
        return {"calibration": "ready" if ok else "imported calibration file missing"}
    t = session.trial(label)
    if not t.layout:
        return {f"trial {label}": "no layout chosen"}
    try:
        return layout_state(session, root, t.layout)
    except KeyError:
        return {f"trial {label}": f"layout {t.layout} does not exist"}


def calibration_state(session: Session, root: Path) -> dict[str, str]:
    """Every layout used by a trial that can still run: what is ready, missing or blocking."""
    state: dict[str, str] = {}
    for t in session.trials:
        if t.raw_present:
            state.update(trial_calibration_state(session, root, t.label))
    return state


def trial_ready(session: Session, root: Path, label: str) -> bool:
    return all(v == "ready" for v in trial_calibration_state(session, root, label).values())


# ---------- calibration files ----------

def write_calibration(session: Session, root: Path, labels: list[str] | None = None) -> list[Path]:
    """Write Calib_scene.toml for every trial whose layout is fully calibrated (and copy it into the assembled trial
    folder). labels: only these trials; one of them not ready raises CalibrationStepError."""
    layout = Layout(root)
    written = []
    for t in session.trials:
        if not t.raw_present or (labels is not None and t.label not in labels):
            continue
        if session.calibration_source == "imported":
            src = layout.calib_file
            if not src.is_file():
                raise CalibrationStepError(f"{src} is missing.")
        else:
            state = trial_calibration_state(session, root, t.label)
            not_ready = {c: s for c, s in state.items() if s != "ready"}
            if not_ready:
                if labels is not None:
                    raise CalibrationStepError(f"Trial {t.label} is not calibrated yet: "
                                               + "; ".join(f"{c}: {s}" for c, s in not_ready.items()))
                continue
            intr, extr = [], []
            for slot, cam, lc in session.trial_cameras(t.label):
                intr.append(dataclasses.replace(lens_result(root, cam.id), camera=slot))
                extr.append(dataclasses.replace(position_result(root, t.layout, lc.camera), camera=slot))
            src = calibfile.write_calib(layout.trial_calib(t.label), intr, extr)
        tdir = layout.trial(t.label)
        if tdir.is_dir():  # keep an assembled trial in step: exactly one .toml in its calibration folder
            cdir = tdir / "calibration"
            cdir.mkdir(exist_ok=True)
            for old in cdir.glob("*.toml"):
                old.unlink()
            shutil.copy2(src, cdir / "Calib_scene.toml")
        written.append(src)
    return written


def trial_calib_source(session: Session, root: Path, label: str) -> Path:
    layout = Layout(root)
    return layout.calib_file if session.calibration_source == "imported" else layout.trial_calib(label)


def _invalidate_trials(session: Session, root: Path, labels: list[str]) -> None:
    """Remove the calibration files of these trials and their copies (a stale copy would be used silently).
    Trials frozen after a deletion keep theirs: they cannot run again anyway."""
    layout = Layout(root)
    for label in labels:
        try:
            if session.trial(label).frozen:
                continue
        except KeyError:
            pass
        layout.trial_calib(label).unlink(missing_ok=True)
        cdir = layout.trial(label) / "calibration"
        if cdir.is_dir():
            for copy in cdir.glob("*.toml"):
                copy.unlink()
