"""Input validation and trial mapping (PROJECT.md §6.2, directory rules 1-7)."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from mocapapp.core.media import MediaError, ProbeInfo, is_video, probe, recording_time
from mocapapp.core.session import Session

ROLES = ("intrinsic", "extrinsic")
CLOUD_MARKERS = ("onedrive", "dropbox", "google drive", "googledrive", "icloud", "icloud drive")
MIN_FPS, MIN_HEIGHT_PX, MIN_CAMERAS = 59.0, 1080, 3
FPS_TOLERANCE = 0.5
CROSS_CAMERA_DURATION_S = 2.0
CHRONO_TOLERANCE_S, CHRONO_TOLERANCE_REL = 3.0, 0.10

Level = Literal["error", "action", "warning", "info"]


@dataclass
class Issue:
    level: Level  # error: blocks Run; action: user must choose something; warning: overridable; info
    where: str
    what: str
    fix: str = ""

    def __str__(self) -> str:
        text = f"[{self.level.upper()}] {self.where}: {self.what}"
        return text + (f"\n    -> {self.fix}" if self.fix else "")


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)
    mapping_method: str | None = None
    low_confidence: set[tuple[str, str]] = field(default_factory=set)  # (camera, trial label)
    probes: dict[str, ProbeInfo] = field(default_factory=dict)  # resolved path -> info

    @property
    def ok(self) -> bool:
        return not any(i.level in ("error", "action") for i in self.issues)

    @property
    def needs_mapping_review(self) -> bool:
        """Tier 3 table must be shown when mapping is not by digit or any cross-check failed."""
        return self.mapping_method not in ("digit", "manual") or bool(self.low_confidence)

    def add(self, level: Level, where: str, what: str, fix: str = "") -> None:
        self.issues.append(Issue(level, where, what, fix))


# ---------- helpers ----------

def natural_key(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def file_fingerprint(path: Path) -> str:
    """Size + hash of the first and last MiB: fast, and enough to spot the same recording twice."""
    size = path.stat().st_size
    h = hashlib.sha256(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(1 << 20))
        if size > 2 << 20:
            f.seek(-(1 << 20), os.SEEK_END)
            h.update(f.read())
    return h.hexdigest()


def is_ascii_path(path: Path) -> bool:
    return str(Path(path).resolve()).isascii()


def in_cloud_folder(path: Path) -> bool:
    return any(m in str(Path(path).resolve()).lower() for m in CLOUD_MARKERS)


def check_output_root(root: Path) -> list[Issue]:
    issues = []
    if not is_ascii_path(root):
        issues.append(Issue("error", str(root),
                            "The session folder path contains letters such as æ, ø, å or accents. "
                            "OpenSim cannot save results in such folders.",
                            "Choose an output folder whose full path uses only plain letters, digits, "
                            "spaces, - and _."))
    if in_cloud_folder(root):
        issues.append(Issue("warning", str(root),
                            "The session folder is inside a cloud-synced folder; copies of participant videos "
                            "may stay in the cloud even after deletion.",
                            "Prefer a local folder that is not synced."))
    return issues


@dataclass
class DirScan:
    calib: dict[str, list[Path]]  # role -> matching files
    trials: list[Path]
    ignored: list[Path]


def scan_dir(directory: Path) -> DirScan:
    calib: dict[str, list[Path]] = {r: [] for r in ROLES}
    trials, ignored = [], []
    for p in sorted(Path(directory).iterdir(), key=lambda p: natural_key(p.name)):
        if p.is_dir() or p.name.startswith("."):
            continue
        if not is_video(p):
            ignored.append(p)
            continue
        stem = p.stem.lower()
        if stem in ROLES:
            calib[stem].append(p)
        else:
            trials.append(p)
    return DirScan(calib, trials, ignored)


# ---------- trial mapping tiers ----------

def map_by_digit(per_cam: dict[str, list[Path]], n_trials: int) -> dict[str, dict[str, Path]] | None:
    """Tier 1: the last character of the stem is the trial digit; same digit set on every camera."""
    result: dict[str, dict[str, Path]] = {}
    digit_sets = set()
    for cam, files in per_cam.items():
        by_digit: dict[str, Path] = {}
        for f in files:
            d = f.stem[-1:]
            if not d.isdigit() or d in by_digit:
                return None
            by_digit[d] = f
        if len(by_digit) != n_trials:
            return None
        result[cam] = by_digit
        digit_sets.add(frozenset(by_digit))
    return result if len(digit_sets) == 1 else None


def _chrono_key(path: Path, info: ProbeInfo | None):
    t = recording_time(info) if info else None
    return (0 if t else 1, t.timestamp() if t else path.stat().st_mtime, natural_key(path.name))


def map_by_time(per_cam: dict[str, list[Path]], probes: dict[str, ProbeInfo], report: Report
                ) -> dict[str, dict[str, Path]]:
    """Tier 2: i-th file by recording time (creation_time -> mtime -> name), cross-checked by duration."""
    ordered = {cam: sorted(files, key=lambda f: _chrono_key(f, probes.get(str(f))))
               for cam, files in per_cam.items()}
    result = {cam: {str(i + 1): f for i, f in enumerate(files)} for cam, files in ordered.items()}
    cams = list(result)
    n = min(len(v) for v in result.values()) if result else 0
    for i in range(n):
        label = str(i + 1)
        durations = {c: probes[str(result[c][label])].duration_s for c in cams if str(result[c][label]) in probes}
        if len(durations) < 2:
            continue
        lo, hi = min(durations.values()), max(durations.values())
        if hi - lo > max(CHRONO_TOLERANCE_S, CHRONO_TOLERANCE_REL * hi):
            report.low_confidence.update((c, label) for c in cams)
            report.add("warning", f"trial {label}",
                       f"Videos matched by recording time have different lengths "
                       f"({', '.join(f'{c} {d:.0f} s' for c, d in durations.items())}). "
                       "A video may be missing or extra on one camera.",
                       "Check the trial mapping table and correct it by hand if needed.")
    return result


# ---------- main entry ----------

def validate(session: Session, session_root: Path | None = None, do_probe: bool = True) -> Report:
    """Check all inputs and resolve calibration videos and trial mapping in `session` (modified in place)."""
    report = Report()
    if session_root is not None:
        report.issues.extend(check_output_root(session_root))

    n_trials = session.n_trials
    if len(session.cameras) < 2:
        report.add("error", "cameras", "At least 2 cameras are needed to compute 3D positions.",
                   "Add another camera directory.")
    elif len(session.cameras) < MIN_CAMERAS:
        report.add("warning", "cameras", f"Only {len(session.cameras)} cameras. Results are much more robust "
                   "with 3 or more.", "Use 3+ cameras if you can.")

    # Rule 5: distinct, not nested
    dirs = [Path(c.source_dir).resolve() for c in session.cameras]
    for i, a in enumerate(dirs):
        if not a.is_dir():
            report.add("error", session.cameras[i].name, f"Folder {a} does not exist.",
                       "Pick the camera's folder again.")
        for j, b in enumerate(dirs[i + 1:], start=i + 1):
            if a == b or a in b.parents or b in a.parents:
                report.add("error", f"{session.cameras[i].name}, {session.cameras[j].name}",
                           "Camera folders must be different and must not be inside each other.",
                           "Give every camera its own separate folder.")
        if in_cloud_folder(a):
            report.add("warning", session.cameras[i].name, "Folder is inside a cloud-synced folder (OneDrive, "
                       "Dropbox, Google Drive or iCloud). Deleting raw footage may not remove the cloud copies.",
                       "Move the videos to a local folder if participants asked for deletion.")
    if not report.ok:
        return report

    scans = {c.name: scan_dir(Path(c.source_dir)) for c in session.cameras}

    # Rules 1-3: calibration videos and trial candidates
    used_calib: dict[str, str] = {}  # fingerprint -> "cam role"
    per_cam_trials: dict[str, list[Path]] = {}
    for cam in session.cameras:
        scan = scans[cam.name]
        for p in scan.ignored:
            report.add("info", cam.name, f"Ignored {p.name} (not a video).")
        for role in ROLES:
            ref = getattr(cam, role)
            if ref is not None and ref.origin == "manual":
                if not Path(ref.file).is_file():
                    report.add("action", f"{cam.name} {role}", f"The chosen file {ref.file} no longer exists.",
                               "Choose the file again.")
                    setattr(cam, role, None)
                continue
            found = scan.calib[role]
            if len(found) == 1:
                setattr(cam, role, _ref(role, str(found[0]), "found"))
            else:
                setattr(cam, role, None)
                why = "No file named" if not found else f"{len(found)} files named"
                report.add("action", f"{cam.name} {role}",
                           f"{why} '{role}.*' in {cam.source_dir}.",
                           f"Choose the {role} calibration video for {cam.name} (any folder).")
        trials = list(scan.trials)
        for p in trials:
            low = p.stem.lower()
            if low.startswith(ROLES) and low[-1:].isdigit():
                report.add("warning", cam.name, f"{p.name} is treated as a trial video, not a calibration video "
                           "(only files named exactly 'intrinsic' or 'extrinsic' count).",
                           f"Rename it to {low.rstrip('0123456789')}{p.suffix} if it is a calibration video.")
        per_cam_trials[cam.name] = trials

        # different files for the two roles, no file shared between cameras
        refs = [(role, getattr(cam, role)) for role in ROLES if getattr(cam, role)]
        if len(refs) == 2 and Path(refs[0][1].file).resolve() == Path(refs[1][1].file).resolve():
            report.add("error", cam.name, "The intrinsic and extrinsic videos are the same file.",
                       "They must be two different recordings: choose another file for one of them.")
        for role, ref in refs:
            try:
                fp = file_fingerprint(Path(ref.file))
            except OSError as exc:
                report.add("error", f"{cam.name} {role}", f"Cannot read {ref.file}: {exc}")
                continue
            if fp in used_calib:
                report.add("error", f"{cam.name} {role}",
                           f"Same recording as {used_calib[fp]} ({Path(ref.file).name}).",
                           "Every camera needs its own calibration videos, and intrinsic/extrinsic must differ.")
            used_calib[fp] = f"{cam.name} {role}"

    # Rule 6: probe everything
    if do_probe:
        files = [Path(getattr(c, r).file) for c in session.cameras for r in ROLES if getattr(c, r)]
        files += [p for ps in per_cam_trials.values() for p in ps]
        for f in files:
            try:
                report.probes[str(f)] = probe(f)
            except MediaError as exc:
                report.add("error", f.name, str(exc), "Replace or remove this file.")

    # Rule 4: trial mapping
    if _keep_mapping(session):
        # Trials already processed (or added later with their own videos) keep their labels and files: an automatic
        # mapping could renumber them and mix up results. Missing videos are asked for in the mapping table.
        report.mapping_method = "manual"
        for trial in session.trials:
            if not trial.raw_present:
                continue
            for cam in session.cameras:
                f = cam.trial_files.get(trial.label)
                if not f:
                    report.add("action", f"{cam.name} trial {trial.label}",
                               f"No video chosen for trial {trial.label} ({trial.participant}).",
                               "Choose it in the mapping table of the Trials step.")
                elif not Path(f).is_file() and session.run_state.trial_status.get(trial.label) == "done":
                    report.add("info", f"{cam.name} trial {trial.label}", f"The original video {Path(f).name} is "
                               "gone. The trial's results are kept, but it can only be run again from the copy in "
                               "the session.")
                elif not Path(f).is_file():
                    report.add("action", f"{cam.name} trial {trial.label}", f"{f} no longer exists.",
                               "Fix the mapping table.")
                elif do_probe and str(Path(f)) not in report.probes:
                    try:
                        report.probes[str(Path(f))] = probe(Path(f))
                    except MediaError as exc:
                        report.add("error", Path(f).name, str(exc), "Replace or remove this file.")
        if do_probe:
            _check_media(session, report, [t.label for t in session.trials])
        return report
    else:
        for cam, files in per_cam_trials.items():
            if len(files) != n_trials:
                report.add("error", cam, f"Found {len(files)} trial videos but the session has {n_trials} trials: "
                           + (", ".join(p.name for p in files) or "none") + ".",
                           "Make sure each camera folder holds exactly one video per trial, plus "
                           "'intrinsic' and 'extrinsic'.")
        if not report.ok:
            return report
        mapping = map_by_digit(per_cam_trials, n_trials)
        if mapping is not None:
            report.mapping_method = "digit"
        else:
            report.mapping_method = "chronological"
            report.add("info", "trial mapping", "File names do not end in a unique trial digit on every camera; "
                       "videos were matched by recording time.", "Confirm the mapping table before running.")
            mapping = map_by_time(per_cam_trials, report.probes, report)

    labels = sorted(next(iter(mapping.values())).keys(), key=natural_key) if mapping else []
    for t, lbl in zip(session.trials, labels, strict=False):
        t.label = lbl
        t.mapping_method = report.mapping_method
    for cam in session.cameras:
        cam.trial_files = {lbl: str(mapping[cam.name][lbl]) for lbl in labels}

    if do_probe:
        _check_media(session, report, labels)
    return report


def _keep_mapping(session: Session) -> bool:
    """Use the trial labels and files as they are (no automatic mapping)?

    Yes when any trial was processed or its footage deleted, when every trial was mapped by hand, or when trials
    were added by hand (0.1.0rc13) and the others already have all their videos."""
    rs = session.run_state
    if any(rs.stage_done.get(t.label) or t.label in rs.trial_status or not t.raw_present for t in session.trials):
        return True
    manual = [t.mapping_method == "manual" for t in session.trials]
    if all(manual):
        return any(c.trial_files for c in session.cameras)
    return any(manual) and all(c.trial_files.get(t.label) for t, m in zip(session.trials, manual, strict=True)
                               if not m for c in session.cameras)


def _ref(role: str, file: str, origin: str):
    from mocapapp.core.session import CalibVideo, VideoRef

    return (CalibVideo if role == "intrinsic" else VideoRef)(file=file, origin=origin)


def _check_media(session: Session, report: Report, labels: list[str]) -> None:
    infos = report.probes
    all_fps = []
    for cam in session.cameras:
        files = [Path(getattr(cam, r).file) for r in ROLES if getattr(cam, r)] + [
            Path(f) for f in cam.trial_files.values()]
        for f in files:
            info = infos.get(str(f))
            if not info:
                continue
            all_fps.append(info.fps)
            where = f"{cam.name} {f.name}"
            if info.fps < MIN_FPS:
                report.add("warning", where, f"{info.fps:g} fps. Fast movements need 60 fps.",
                           "Record at 60 fps if possible.")
            if min(info.display_size) < MIN_HEIGHT_PX:
                report.add("warning", where, f"Resolution {info.display_size[0]}x{info.display_size[1]} is below "
                           "1080p.", "Record at 1080p if possible.")
            if info.vfr:
                report.add("info", where, "Variable frame rate (common on phones); it will be converted to a "
                           "constant frame rate.")
            if info.rotation:
                report.add("info", where, f"Stored rotated by {info.rotation} degrees; it will be turned upright.")
        intr = cam.intrinsic
        if intr and str(Path(intr.file)) in infos:
            isize = infos[str(Path(intr.file))].display_size
            for f in cam.trial_files.values():
                if f in infos and infos[f].display_size != isize:
                    report.add("error", f"{cam.name} {Path(f).name}",
                               f"Resolution {infos[f].display_size} differs from the intrinsic video {isize}. "
                               "The lens calibration only holds for the same resolution and zoom.",
                               "Record the intrinsic video with the same camera settings as the trials.")
    if all_fps and max(all_fps) - min(all_fps) > FPS_TOLERANCE:
        report.add("warning", "frame rate",
                   f"Frame rates differ between videos ({min(all_fps):g}-{max(all_fps):g} fps).",
                   "Use the same fps on every camera; videos will be converted to a common rate.")
    for lbl in labels:
        durs = {c.name: infos[c.trial_files[lbl]].duration_s for c in session.cameras
                if c.trial_files.get(lbl) in infos}
        if len(durs) > 1 and max(durs.values()) - min(durs.values()) > CROSS_CAMERA_DURATION_S:
            report.add("warning", f"trial {lbl}", "Video lengths differ by more than 2 s between cameras ("
                       + ", ".join(f"{c} {d:.1f} s" for c, d in durs.items()) + ").",
                       "Fine if cameras were started at different times; synchronisation will align them.")
