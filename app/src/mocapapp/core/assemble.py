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
    target_fps: float | None = None
    bytes_needed: int = 0
    bytes_free: int = 0

    @property
    def enough_space(self) -> bool:
        return self.bytes_free >= self.bytes_needed

    def describe(self) -> str:
        lines = [f"{'transcode' if how == 'transcode' else 'copy/link':9}  {src}  ->  {dst}"
                 for src, dst, how in self.jobs]
        lines.append(f"Space needed: about {self.bytes_needed / 1e9:.2f} GB; free: {self.bytes_free / 1e9:.1f} GB.")
        if self.target_fps:
            lines.append(f"All videos will be converted to {self.target_fps:g} fps.")
        return "\n".join(lines)


def _common_fps(report: Report, files: list[Path]) -> float | None:
    """None if all videos share a frame rate, else the most common one (videos are converted to it)."""
    rates = [round(report.probes[str(f)].fps, 2) for f in files if str(f) in report.probes]
    if not rates or max(rates) - min(rates) <= 0.5:
        return None
    return Counter(rates).most_common(1)[0][0]


def plan_inputs(session: Session, root: Path, report: Report) -> Plan:
    if not report.ok:
        raise ValueError("Fix the validation errors before preparing the videos.")
    layout = Layout(root)
    sources: list[tuple[Path, Path]] = []
    for cam in session.cameras:
        sources.append((Path(cam.intrinsic.file), layout.inputs(cam.name) / "intrinsic.mp4"))
        sources.append((Path(cam.extrinsic.file), layout.inputs(cam.name) / "extrinsic.mp4"))
        for label, f in cam.trial_files.items():
            if not session.trial(label).raw_present:
                continue
            sources.append((Path(f), layout.inputs(cam.name) / f"trial_{label}.mp4"))
    trial_files = [Path(f) for c in session.cameras for f in c.trial_files.values()]
    plan = Plan(target_fps=_common_fps(report, trial_files))
    root.mkdir(parents=True, exist_ok=True)
    plan.bytes_free = shutil.disk_usage(root).free
    for src, dst in sources:
        info = report.probes.get(str(src)) or media.probe(src)
        fps_change = plan.target_fps is not None and abs(info.fps - plan.target_fps) > 0.01
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
        stamp = _source_stamp(src, plan.target_fps)
        stamp_file = dst.with_name(dst.name + ".source")
        if dst.exists() and stamp_file.is_file() and stamp_file.read_text(encoding="utf-8") == stamp:
            done[str(dst)] = "existing"
            continue
        dst.unlink(missing_ok=True)  # another source was chosen since (new mapping or calibration video)
        progress(f"[{i}/{len(plan.jobs)}] {src.name} -> {dst.parent.name}/{dst.name}")
        info = report.probes.get(str(src))
        done[str(dst)] = media.normalise(src, dst, info, plan.target_fps)
        stamp_file.write_text(stamp, encoding="utf-8")
    return done


def _source_stamp(src: Path, fps: float | None) -> str:
    st = Path(src).stat()
    return json.dumps({"source": str(Path(src).resolve()), "size": st.st_size, "mtime": int(st.st_mtime),
                       "fps": fps})


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.unlink(missing_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _scene_points(root: Path) -> list[list[float]] | None:
    scene = Layout(root).scene_json
    if not scene.is_file():
        return None
    data = json.loads(scene.read_text(encoding="utf-8"))
    return [[p["x"], p["y"], p["z"]] for p in data.get("points", [])] or None


def assemble_trials(session: Session, root: Path, progress: Progress = print) -> list[Path]:
    """Create trials/trial_<label>/ with Config.toml, videos/camNN.mp4 and (if computed) calibration."""
    layout = Layout(root)
    if (layout.trials / "Config.toml").exists():
        raise RuntimeError(f"{layout.trials / 'Config.toml'} must not exist: Pose2Sim would treat all trials as one "
                           "batch and look for a shared calibration. Delete that file.")
    points = _scene_points(root)
    made = []
    for trial in session.trials:
        if not trial.raw_present:
            continue
        tdir = layout.trial(trial.label)
        (tdir / "videos").mkdir(parents=True, exist_ok=True)
        # Upstream picks the first folder with 'calib' in its name: never create another one here.
        for cam in session.cameras:  # cam01, cam02, ... same order as Calib_scene.toml (upstream-notes §2)
            src = layout.inputs(cam.name) / f"trial_{trial.label}.mp4"
            if not src.is_file():
                raise FileNotFoundError(f"{src} is missing. Prepare the videos first (mocapapp assemble).")
            _link_or_copy(src, tdir / "videos" / f"{cam.name}.mp4")
        atomic_write_text(tdir / "Config.toml", build_config(session, trial, points))
        if layout.calib_file.is_file():
            calib_dir = tdir / "calibration"
            calib_dir.mkdir(exist_ok=True)
            for old in calib_dir.glob("*.toml"):  # upstream uses the newest .toml: keep exactly one
                old.unlink()
            shutil.copy2(layout.calib_file, calib_dir / "Calib_scene.toml")
        progress(f"trial {trial.label} ({trial.code}) -> {tdir}")
        made.append(tdir)
    return made
