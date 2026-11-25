"""Calibration steps on a session: intrinsics per camera, scene, extrinsics per camera, Calib_scene.toml.

A camera can have several intrinsic and extrinsic videos (0.1.0rc13: a camera moved between trials; 0.1.0rc17: any
of the two, assigned per trial). Each trial uses one of each per camera; its camera position is computed with the
lens calibration that trial uses, so an extrinsic video used with two intrinsic videos gives two results (the same
clicks, solved with each lens).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import cv2

from mocapapp.core.calibration import calibfile, extrinsics, intrinsics
from mocapapp.core.calibration.scene_points import Scene, check_scene, load_scene, save_scene
from mocapapp.core.session import Camera, Layout, Session

Progress = intrinsics.Progress
EXTRINSICS_FILE_RE = re.compile(r"extrinsics_(?P<cam>.+?)(?:_pos(?P<pos>\d+))?(?:_lens(?P<lens>\d+))?")


class CalibrationStepError(Exception):
    pass


def input_video(root: Path, camera: str, role: str, key: str = "") -> Path:
    """Normalised copy (upright, constant frame rate) - the same orientation the trials will have."""
    path = Layout(root).inputs(camera) / input_name(role, key)
    if not path.is_file():
        raise CalibrationStepError(f"{path} is missing. Prepare the videos first: mocapapp prepare --session {root}")
    return path


def input_name(role: str, key: str = "") -> str:
    return f"{role}_{key}.mp4" if key else f"{role}.mp4"


# ---------- which videos are used together ----------

def video_title(cam: Camera, role: str, key: str) -> str:
    """'cam01' for the main videos, 'cam01 extrinsic video 2' for a further one."""
    return cam.name + (f" {role} video {key}" if key else "")


def pair_title(cam: Camera, lens: str, position: str) -> str:
    return video_title(cam, "extrinsic", position) + (f" (with intrinsic video {lens})" if lens else "")


def needed_pairs(session: Session, cam: Camera) -> list[tuple[str, str]]:
    """(intrinsic key, extrinsic key) used together by some trial that can still run, the main pair always."""
    pairs = {("", "")} | {(cam.lens_for(t.label), cam.position_for(t.label)) for t in session.trials
                          if t.raw_present}
    return sorted(pairs, key=lambda p: (p[0] != "", p[0], p[1] != "", p[1]))


def needed_lenses(session: Session, cam: Camera) -> list[str]:
    return sorted({lens for lens, _ in needed_pairs(session, cam)}, key=lambda k: (k != "", k))


def lenses_for_position(session: Session, cam: Camera, position: str) -> list[str]:
    """The intrinsic videos an extrinsic video is solved with (the main one when no trial uses it yet)."""
    return [lens for lens, pos in needed_pairs(session, cam) if pos == position] or [""]


# ---------- intrinsics ----------

def run_intrinsics(session: Session, root: Path, cameras: list[str] | None = None, every_n_sec: float = 1.0,
                   force: bool = False, progress: Progress = print) -> list[intrinsics.IntrinsicsResult]:
    layout = Layout(root)
    spec = intrinsics.BoardSpec.from_settings(session.settings)
    results = []
    for cam in session.cameras:
        if cameras and cam.name not in cameras:
            continue
        for key, _file in cam.calib_videos("intrinsic"):
            video = input_video(root, cam.name, "intrinsic", key)
            result = None if force else intrinsics.load_result(layout.calibration_work, cam.name, video, key)
            if result is None:
                progress(f"{video_title(cam, 'intrinsic', key)}: detecting the board in {video.name} ...")
                result = intrinsics.calibrate_video(cam.name, video, spec, every_n_sec,
                                                    debug_dir=layout.calibration_work / "debug",
                                                    progress=lambda _m: None)
                intrinsics.save_result(result, layout.calibration_work, key)
                # new lens model: the camera positions computed with it are solved again from their clicks
                resolve_extrinsics(session, root, cam.name, lens=key)
            if not key and cam.intrinsic is not None:
                cam.intrinsic.rms_px = result.rms_px
                cam.intrinsic.status = result.status
            results.append(result)
    return results


# ---------- scene ----------

def set_scene(root: Path, scene: Scene, session: Session | None = None) -> list[str]:
    """Save the reference points. Returns plain-language notes on what happened to the camera positions.

    When the points changed, every camera position is recomputed from its saved clicks: the clicked spots did not
    move, only their coordinates changed (a corrected measurement, another unit). Clicks of removed points are
    dropped. A position that cannot be recomputed (no session given, too few clicks left, invalid points) is removed
    and has to be clicked again. The session's extrinsics entries are updated; the caller saves the session."""
    layout = Layout(root)
    old = load_scene(layout.scene_json) if layout.scene_json.is_file() else None
    save_scene(scene, layout.scene_json)
    if old is None or [p.model_dump() for p in old.points] == [p.model_dump() for p in scene.points]:
        return []
    _invalidate_calib(root)
    notes = []
    labels = set(scene.labels())
    for f in sorted(layout.calibration_work.glob("extrinsics_*.json")):
        result = extrinsics.ExtrinsicsResult.from_json(f.read_text(encoding="utf-8"))
        m = EXTRINSICS_FILE_RE.fullmatch(f.stem)
        position, lens = (m.group("pos") or "", m.group("lens") or "") if m else ("", "")
        where = result.camera + (f" extrinsic video {position}" if position else "") + (
            f" (with intrinsic video {lens})" if lens else "")
        if session is not None:
            clicks = [extrinsics.Click(**c) for c in result.clicks if c.get("label") in labels]
            try:
                new = _solve(session, root, result.camera, clicks, result.frame_index, result.overridden, position,
                             lens)
                notes.append(f"{where}: camera position recomputed from your clicks (error {new.rms_cm:.1f} cm, "
                             f"{new.status}).")
                continue
            except Exception as exc:  # any reason: fall back to clicking again, as before 0.1.0rc15
                notes.append(f"{where}: could not be recomputed ({exc}). Click the points again.")
        else:
            notes.append(f"{where}: removed. Click the points again.")
        f.unlink()
        if session is not None:
            _forget_entry(session, result.camera, position, lens)
    if session is not None and session.calibration_source == "app":
        try:
            write_calibration(session, root)
        except CalibrationStepError:
            pass  # not every camera is ready: written once the extrinsic step is complete
    return notes


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


def lens_result(root: Path, camera: str, lens: str = "") -> intrinsics.IntrinsicsResult | None:
    """The lens calibration of an intrinsic video, if computed from the video now in the session."""
    work = Layout(root).calibration_work
    try:
        video = input_video(root, camera, "intrinsic", lens)
    except CalibrationStepError:
        return intrinsics.load_result(work, camera, lens=lens)
    return intrinsics.load_result(work, camera, video, lens)


def _forget_entry(session: Session, camera: str, position: str, lens: str) -> None:
    session.extrinsics = [e for e in session.extrinsics
                          if (e.get("camera"), e.get("position", ""), e.get("lens", "")) != (camera, position, lens)]


def _solve(session: Session, root: Path, camera: str, clicks: list[extrinsics.Click], frame_index: int,
           override: bool, position: str, lens: str) -> extrinsics.ExtrinsicsResult:
    """One camera position from clicks, with one lens calibration; saved with its session entry."""
    layout = Layout(root)
    scene = get_scene(root)
    blocking = [i for i in check_scene(scene) if i.level == "error"]
    if blocking:
        raise CalibrationStepError("Fix the reference points first:\n" + "\n".join(str(i) for i in blocking))
    cam = session.camera(camera)
    if position:
        cam.position(position)  # KeyError for an unknown video
    intr = lens_result(root, camera, lens)
    if intr is None:
        raise CalibrationStepError(f"{video_title(cam, 'intrinsic', lens)}: compute the intrinsics first "
                                   "(mocapapp calibrate intrinsics).")
    result = extrinsics.solve_camera(camera, scene, clicks, intr.matrix, intr.distortions, frame_index)
    result.overridden = override and result.status == "fail"
    extrinsics.save_result(result, layout.calibration_work, position, lens)
    entry = {"camera": camera, "frame_index": frame_index, "n_points": result.n_points, "error_cm": result.rms_cm,
             "error_px": result.rms_px, "status": result.status, "overridden": result.overridden}
    if position:
        entry["position"] = position
    if lens:
        entry["lens"] = lens
    _forget_entry(session, camera, position, lens)
    session.extrinsics.append(entry)
    session.extrinsics.sort(key=lambda e: (e["camera"], e.get("position", ""), e.get("lens", "")))
    return result


def run_extrinsics(session: Session, root: Path, camera: str, clicks: list[extrinsics.Click], frame_index: int = 0,
                   override: bool = False, position: str = "") -> extrinsics.ExtrinsicsResult:
    """The camera position seen in an extrinsic video, solved with every lens calibration used together with it.
    Returns the result for the first of them (shown to the researcher)."""
    cam = session.camera(camera)
    results = [_solve(session, root, camera, clicks, frame_index, override, position, lens)
               for lens in lenses_for_position(session, cam, position)]
    frame = extrinsics.read_frame(input_video(root, camera, "extrinsic", position), frame_index)
    cv2.imwrite(str(review_image(root, camera, position)), extrinsics.draw_review(frame, results[0]))
    _invalidate_calib(root)
    return results[0]


def position_result(root: Path, session: Session, camera: str, position: str = "") -> \
        extrinsics.ExtrinsicsResult | None:
    """The result shown for an extrinsic video: with the first lens calibration it is used with."""
    lens = lenses_for_position(session, session.camera(camera), position)[0]
    work = Layout(root).calibration_work
    return extrinsics.load_result(work, camera, position, lens) or extrinsics.load_result(work, camera, position)


def saved_clicks(root: Path, camera: str, position: str) -> extrinsics.ExtrinsicsResult | None:
    """Any saved result of this extrinsic video (they all hold the same clicks)."""
    work = Layout(root).calibration_work
    for f in sorted(work.glob(f"extrinsics_{camera}*.json")):
        m = EXTRINSICS_FILE_RE.fullmatch(f.stem)
        if m and m.group("cam") == camera and (m.group("pos") or "") == position:
            return extrinsics.ExtrinsicsResult.from_json(f.read_text(encoding="utf-8"))
    return None


def resolve_extrinsics(session: Session, root: Path, camera: str, lens: str | None = None,
                       position: str | None = None) -> None:
    """Solve camera positions again from their saved clicks (after a new lens calibration, or when trials now use
    an extrinsic video with another lens). Results no longer needed are removed; a position without clicks stays
    to be clicked."""
    work = Layout(root).calibration_work
    cam = session.camera(camera)
    clicks = {pos: saved_clicks(root, camera, pos) for pos in [""] + [p.key for p in cam.positions]}
    needed = set()
    for pos in clicks:
        for ln in lenses_for_position(session, cam, pos):
            needed.add((ln, pos))
    for f in sorted(work.glob(f"extrinsics_{camera}*.json")):  # results with a lens or video no longer used
        m = EXTRINSICS_FILE_RE.fullmatch(f.stem)
        if m and m.group("cam") == camera and ((m.group("lens") or ""), (m.group("pos") or "")) not in needed:
            f.unlink()
            _forget_entry(session, camera, m.group("pos") or "", m.group("lens") or "")
    for ln, pos in sorted(needed):
        if (lens is not None and ln != lens) or (position is not None and pos != position):
            if extrinsics.result_path(work, camera, pos, ln).is_file():
                continue
        src = clicks.get(pos)
        if src is None:
            continue
        try:
            _solve(session, root, camera, [extrinsics.Click(**c) for c in src.clicks], src.frame_index,
                   src.overridden, pos, ln)
        except Exception:  # anything: this position has to be clicked again
            extrinsics.result_path(work, camera, pos, ln).unlink(missing_ok=True)
            _forget_entry(session, camera, pos, ln)
    _invalidate_calib(root)


# ---------- calibration file ----------

def _extrinsic_state(work: Path, camera: str, position: str = "", lens: str = "") -> str:
    e = extrinsics.load_result(work, camera, position, lens)
    if e is None:
        return "extrinsics missing"
    if e.status == "fail" and not e.overridden:
        return f"extrinsics error {e.rms_cm:.1f} cm (> {extrinsics.WARN_CM} cm, blocked)"
    return "ready"


def _intrinsic_state(work: Path, camera: str, lens: str = "") -> str:
    i = intrinsics.load_result(work, camera, lens=lens)
    if i is None:
        return "intrinsics missing"
    if i.status == "fail":
        return f"intrinsics failed ({i.rms_px:.2f} px)"
    return "ready"


def calibration_state(session: Session, root: Path) -> dict[str, str]:
    """Per camera (and per further calibration video in use): 'ready', or what is still missing/blocking."""
    work = Layout(root).calibration_work
    if session.calibration_source == "imported":
        ok = Layout(root).calib_file.is_file()
        return {c.name: "ready" if ok else "imported calibration file missing" for c in session.cameras}
    state = {}
    for cam in session.cameras:
        lens_state = {lens: _intrinsic_state(work, cam.name, lens) for lens in needed_lenses(session, cam)}
        for lens, value in lens_state.items():
            if lens and value != "ready":
                state[video_title(cam, "intrinsic", lens)] = value
        for lens, pos in needed_pairs(session, cam):
            title = pair_title(cam, lens, pos)
            if lens_state[lens] != "ready":
                if not lens:
                    state[title] = lens_state[lens]
                continue
            state[title] = _extrinsic_state(work, cam.name, pos, lens)
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
    for trial in session.trials:  # trials using further calibration videos of some camera
        keys = [(c.lens_for(trial.label), c.position_for(trial.label)) for c in session.cameras]
        if trial.raw_present and any(lens or pos for lens, pos in keys):
            own_intr = [intrinsics.load_result(work, c.name, lens=lens)
                        for c, (lens, _) in zip(session.cameras, keys, strict=True)]
            own_extr = [extrinsics.load_result(work, c.name, pos, lens)
                        for c, (lens, pos) in zip(session.cameras, keys, strict=True)]
            calibfile.write_calib(layout.trial_calib(trial.label), own_intr, own_extr)
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
