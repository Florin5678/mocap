"""Copy/normalise inputs into the session and build one Pose2Sim project per trial (PROJECT.md §5)."""

from __future__ import annotations

import json
import os
import shutil
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from mocapapp.core import media
from mocapapp.core.config_gen import build_config
from mocapapp.core.session import Layout, Session, atomic_write_text
from mocapapp.core.validate import Report

DISK_MARGIN = 1.2  # transcoded copies are about the size of the originals; keep 20 % spare

Progress = Callable[[str], None]


@dataclass
class Plan:
    """What normalisation will do, shown to the user before anything is written."""
    jobs: list[tuple[Path, Path, str]] = field(default_factory=list)  # (source, destination, 'link'|'transcode')
    target_fps: float | None = None  # the frame rate trials are converted to (most common one), for display
    # destination -> frame rate it is converted to. Only trial videos: the cameras of one trial must share a frame
    # rate, each trial on its own; calibration videos keep theirs (a later trial never re-copies them)
    fps: dict[Path, float] = field(default_factory=dict)
    bytes_needed: int = 0
    bytes_free: int = 0

    @property
    def enough_space(self) -> bool:
        return self.bytes_free >= self.bytes_needed

    def describe(self) -> str:
        lines = [f"{'transcode' if how == 'transcode' else 'copy/link':9}  {src}  ->  {dst}"
                 for src, dst, how in self.jobs]
        lines.append(f"Space needed: about {self.bytes_needed / 1e9:.2f} GB; free: {self.bytes_free / 1e9:.1f} GB.")
        if self.fps:
            rates = ", ".join(f"{r:g}" for r in sorted(set(self.fps.values())))
            lines.append(f"Trial videos recorded at different frame rates will be converted to {rates} fps.")
        return "\n".join(lines)


def _common_fps(report: Report, files: list[Path]) -> float | None:
    """None if all videos share a frame rate, else the most common one (videos are converted to it)."""
    rates = [round(report.probes[str(f)].fps, 2) for f in files if str(f) in report.probes]
    if not rates or max(rates) - min(rates) <= 0.5:
        return None
    return Counter(rates).most_common(1)[0][0]


def input_sources(session: Session, root: Path, labels: list[str] | None = None) -> list[tuple[Path, Path]]:
    """(source video, normalised copy) for every camera's intrinsic video, every layout's extrinsic videos and the
    trials' videos (only these trials when labels are given; their layouts and cameras only)."""
    layout = Layout(root)
    trials = [t for t in session.trials if t.raw_present and (labels is None or t.label in labels)]
    used_layouts = {t.layout for t in trials} if labels is not None else {lay.id for lay in session.layouts}
    used_cams = {c for lay in session.layouts if lay.id in used_layouts for c in lay.camera_ids()}
    if labels is None:
        used_cams |= {c.id for c in session.cameras}
    sources: list[tuple[Path, Path]] = []
    for cam in session.cameras:
        if cam.intrinsic is not None and cam.id in used_cams:
            sources.append((Path(cam.intrinsic.file), layout.camera_input(cam.id)))
    for lay in session.layouts:
        if lay.id not in used_layouts:
            continue
        for lc in lay.cameras:
            if lc.extrinsic is not None:
                sources.append((Path(lc.extrinsic.file), layout.layout_input(lay.id, lc.camera)))
    for trial in trials:
        for _slot, cam, _lc in session.trial_cameras(trial.label):
            f = trial.videos.get(cam.id)
            if not f:
                continue
            if not Path(f).is_file() and session.run_state.trial_status.get(trial.label) == "done":
                continue  # finished trial whose original was removed later: its copy (if any) stays as it is
            sources.append((Path(f), layout.trial_input(trial.label, cam.id)))
    return sources


def plan_inputs(session: Session, root: Path, report: Report, labels: list[str] | None = None) -> Plan:
    if not report.ok:
        raise ValueError("Fix the validation errors before preparing the videos.")
    sources = input_sources(session, root, labels)
    plan = Plan()
    layout = Layout(root)
    for t in session.trials:
        if labels is not None and t.label not in labels:
            continue
        files = {cid: Path(f) for cid, f in t.videos.items() if Path(f).is_file()}
        rate = _common_fps(report, list(files.values()))
        if rate is not None:
            plan.fps.update({layout.trial_input(t.label, cid): rate for cid in files})
    if plan.fps:
        plan.target_fps = Counter(plan.fps.values()).most_common(1)[0][0]
    root.mkdir(parents=True, exist_ok=True)
    plan.bytes_free = shutil.disk_usage(root).free
    for src, dst in sources:
        info = report.probes.get(str(src)) or media.probe(src)
        rate = plan.fps.get(dst)
        fps_change = rate is not None and abs(info.fps - rate) > 0.01
        how = "transcode" if media.needs_transcode(info) or fps_change else "link"
        same_drive = Path(src).resolve().anchor.lower() == Path(root).resolve().anchor.lower()
        if how == "transcode" or not same_drive:
            plan.bytes_needed += Path(src).stat().st_size
        plan.jobs.append((src, dst, how))
    plan.bytes_needed = int(plan.bytes_needed * DISK_MARGIN)
    return plan


def prepare_inputs(plan: Plan, report: Report, progress: Progress = print) -> dict[str, str]:
    """Normalise every video into inputs/. Skips destinations that already exist (resume)."""
    if not plan.enough_space:
        raise OSError(f"Not enough disk space: need about {plan.bytes_needed / 1e9:.1f} GB, "
                      f"{plan.bytes_free / 1e9:.1f} GB free. Free up space or choose another output folder.")
    done = {}
    for i, (src, dst, _how) in enumerate(plan.jobs, 1):
        rate = plan.fps.get(dst)
        stamp = _source_stamp(src, rate)
        stamp_file = dst.with_name(dst.name + ".source")
        if dst.exists() and stamp_file.is_file() and _same_source(stamp_file.read_text(encoding="utf-8"), stamp,
                                                                  frame_rate=dst in plan.fps):
            done[str(dst)] = "existing"
            continue
        dst.unlink(missing_ok=True)  # another source was chosen since (new mapping or calibration video)
        dst.parent.mkdir(parents=True, exist_ok=True)
        progress(f"[{i}/{len(plan.jobs)}] {src.name} -> {dst.parent.name}/{dst.name}")
        info = report.probes.get(str(src))
        done[str(dst)] = media.normalise(src, dst, info, rate)
        stamp_file.write_text(stamp, encoding="utf-8")
    return done


def _source_stamp(src: Path, fps: float | None) -> str:
    st = Path(src).stat()
    return json.dumps({"source": str(Path(src).resolve()), "size": st.st_size, "mtime": int(st.st_mtime),
                       "fps": fps})


def _same_source(old: str, new: str, frame_rate: bool) -> bool:
    """The copy was made from this file. A calibration video's copy counts whatever frame rate it was made at
    (0.1.x converted them to the trials' rate), so its calibration stays valid."""
    if frame_rate:
        return old == new
    try:
        a, b = json.loads(old), json.loads(new)
    except json.JSONDecodeError:
        return False
    return {k: v for k, v in a.items() if k != "fps"} == {k: v for k, v in b.items() if k != "fps"}


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.unlink(missing_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _scene_points(session: Session, label: str) -> list[list[float]] | None:
    lay = session.trial_layout(label)
    data = lay.points if lay is not None else None
    return [[p["x"], p["y"], p["z"]] for p in (data or {}).get("points", [])] or None


def assemble_trials(session: Session, root: Path, progress: Progress = print,
                    labels: list[str] | None = None) -> list[Path]:
    """Create trials/trial_<label>/ with Config.toml, videos/camNN.mp4 and (if computed) calibration.

    labels: only these trials (a single trial run); None = all."""
    layout = Layout(root)
    if (layout.trials / "Config.toml").exists():
        raise RuntimeError(f"{layout.trials / 'Config.toml'} must not exist: Pose2Sim would treat all trials as one "
                           "batch and look for a shared calibration. Delete that file.")
    made = []
    for trial in session.trials:
        if not trial.raw_present or (labels is not None and trial.label not in labels):
            continue
        tdir = layout.trial(trial.label)
        cams = session.trial_cameras(trial.label)
        if not cams:
            raise ValueError(f"Trial {trial.label} has no layout yet: choose one first.")
        missing = [c.id for _s, c, _lc in cams if not layout.trial_input(trial.label, c.id).is_file()]
        if missing and session.run_state.trial_status.get(trial.label) == "done":
            continue  # finished, and its copies were removed later: leave the trial folder as it is
        (tdir / "videos").mkdir(parents=True, exist_ok=True)
        # Upstream picks the first folder with 'calib' in its name: never create another one here.
        slots = {f"{slot}.mp4" for slot, _c, _lc in cams}
        for old in (tdir / "videos").glob("*.mp4"):  # a camera no longer in the trial's layout
            if old.name not in slots:
                old.unlink()
        for slot, cam, _lc in cams:  # cam01, cam02, ... same order as the trial's Calib_scene.toml (upstream-notes §2)
            src = layout.trial_input(trial.label, cam.id)
            if not src.is_file():
                raise FileNotFoundError(f"{src} is missing. Prepare the videos first (mocapapp assemble).")
            _link_or_copy(src, tdir / "videos" / f"{slot}.mp4")
        atomic_write_text(tdir / "Config.toml", build_config(session, trial, _scene_points(session, trial.label)))
        calib = layout.calib_file if session.calibration_source == "imported" else layout.trial_calib(trial.label)
        if not calib.is_file() and session.calibration_source == "app":
            # a trial added after its layout was calibrated: its own calibration file, from the layout
            from mocapapp.core.calibration import service

            if service.trial_ready(session, root, trial.label):
                service.write_calibration(session, root, [trial.label])
        if calib.is_file():
            calib_dir = tdir / "calibration"
            calib_dir.mkdir(exist_ok=True)
            for old in calib_dir.glob("*.toml"):  # upstream uses the newest .toml: keep exactly one
                old.unlink()
            shutil.copy2(calib, calib_dir / "Calib_scene.toml")
        progress(f"trial {trial.label} ({trial.code}) -> {tdir}")
        made.append(tdir)
    return made
