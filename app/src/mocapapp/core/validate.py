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

NUMBER_RE = re.compile(r"\d+")


def name_number(path: Path) -> int | None:
    """The trial number in a file name: the last number in it (01, trial01, trial_01, Trial 1 -> 1)."""
    found = NUMBER_RE.findall(Path(path).stem)
    return int(found[-1]) if found else None


def _role_like(path: Path) -> bool:
    """'extrinsic 2.mp4', 'intrinsic_old.mp4': a calibration video under another name, never a trial."""
    return Path(path).stem.lower().startswith(ROLES)


def match_by_number(files: list[Path], label: str, taken: set[str] = frozenset()) -> Path | None:
    """The one video whose name carries this trial's number (None: no such video, or more than one)."""
    if not label.isdigit():
        return None
    hits = [f for f in files if not _role_like(f) and name_number(f) == int(label) and str(f) not in taken]
    return hits[0] if len(hits) == 1 else None


def map_by_number(per_cam: dict[str, list[Path]], labels: list[str]) -> dict[str, dict[str, Path]] | None:
    """Tier 2: every trial of the session finds the video named with its number, on every camera. Other videos in
    the folders (more takes than trials) are left out."""
    result: dict[str, dict[str, Path]] = {}
    for cam, files in per_cam.items():
        result[cam] = {}
        for label in labels:
            hit = match_by_number(files, label)
            if hit is None:
                return None
            result[cam][label] = hit
    return result if labels else None


def _scan(cam) -> DirScan | None:
    try:
        return scan_dir(Path(cam.source_dir)) if cam.source_dir else None
    except OSError:
        return None


def _layout_trials(session: Session, layout: str) -> list:
    return [t for t in session.trials_with_layout(layout) if t.raw_present and not t.withdrawn_at]


def suggest_trials(session: Session) -> dict[tuple[str, str], Path]:
    """(camera id, trial label) -> the video named with the trial's number in the camera's folder, for trials without
    a video yet on that camera (exactly one such file; shown until 'Save this mapping', 0.1.0rc17)."""
    found = {}
    taken_by_dir: dict[str, set[str]] = {}
    for t in session.trials:  # a duplicated camera shares its folder: videos taken by either count
        for _slot, cam, _lc in session.trial_cameras(t.label):
            f = t.videos.get(cam.id)
            if f:
                taken_by_dir.setdefault(cam.source_dir, set()).add(str(Path(f)))
    scans = {}
    for trial in session.trials:
        if not trial.raw_present or trial.withdrawn_at:
            continue
        for _slot, cam, _lc in session.trial_cameras(trial.label):
            if trial.videos.get(cam.id) or not cam.source_dir:
                continue
            if cam.id not in scans:
                scans[cam.id] = _scan(cam)
            if scans[cam.id] is None:
                continue
            taken = taken_by_dir.setdefault(cam.source_dir, set())
            hit = match_by_number(scans[cam.id].trials, trial.label, taken)
            if hit is not None:
                found[(cam.id, trial.label)] = hit
                taken.add(str(hit))
    return found


def autofill(session: Session, trials: bool = True) -> list[str]:
    """Choose what the names make obvious, without reading any video: a camera's missing intrinsic video when its
    folder has exactly one file named 'intrinsic', a layout camera's missing extrinsic video when its folder has
    exactly one file named 'extrinsic' and the camera is in no other layout, and a trial's missing video when exactly
    one file is named with its number (trials=False: calibration videos only). Nothing chosen before is changed."""
    chosen = []
    scans = {cam.id: _scan(cam) for cam in session.cameras}
    for cam in session.cameras:
        scan = scans[cam.id]
        if scan is not None and cam.intrinsic is None and len(scan.calib["intrinsic"]) == 1:
            cam.intrinsic = _ref("intrinsic", str(scan.calib["intrinsic"][0]), "found")
            chosen.append(f"{cam.name} intrinsic: {scan.calib['intrinsic'][0].name}")
    for lay in session.layouts:
        for lc in lay.cameras:
            scan = scans.get(lc.camera)
            if (scan is None or lc.extrinsic is not None or len(scan.calib["extrinsic"]) != 1
                    or len(session.layouts_with_camera(lc.camera)) != 1):
                continue
            lc.extrinsic = _ref("extrinsic", str(scan.calib["extrinsic"][0]), "found")
            chosen.append(f"{lay.name} {session.camera(lc.camera).name} extrinsic: {scan.calib['extrinsic'][0].name}")
    for (camera, label), hit in (suggest_trials(session).items() if trials else []):
        session.trial(label).videos[camera] = str(hit)
        chosen.append(f"{session.camera(camera).name} trial {label}: {hit.name}")
    return chosen


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
    """Tier 3: i-th file by recording time (creation_time -> mtime -> name), cross-checked by duration."""
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

def _check_layouts(session: Session, report: Report) -> None:
    for lay in session.layouts:
        if not _layout_trials(session, lay.id):
            continue
        n = len(lay.cameras)
        if n < 2:
            report.add("error", lay.name, "At least 2 cameras are needed to compute 3D positions.",
                       "Add another camera to the layout.")
        elif n < MIN_CAMERAS:
            report.add("warning", lay.name, f"Only {n} cameras. Results are much more robust with 3 or more.",
                       "Use 3+ cameras if you can.")
        cams = [session.camera(c) for c in lay.camera_ids()]
        dirs = [(c, Path(c.source_dir).resolve()) for c in cams if c.source_dir]
        for i, (ca, a) in enumerate(dirs):  # cameras filmed together keep their videos apart (Rule 5)
            for cb, b in dirs[i + 1:]:
                if a == b or a in b.parents or b in a.parents:
                    report.add("error", f"{lay.name}: {ca.name}, {cb.name}",
                               "Camera folders must be different and must not be inside each other.",
                               "Give every camera its own separate folder.")


def _used_cameras(session: Session) -> list:
    used = {c for t in session.trials if t.raw_present for c in
            (session.trial_layout(t.label).camera_ids() if session.trial_layout(t.label) else [])}
    return [c for c in session.cameras if c.id in used]


def validate(session: Session, session_root: Path | None = None, do_probe: bool = True) -> Report:
    """Check all inputs and resolve calibration videos and trial mapping in `session` (modified in place)."""
    report = Report()
    if session_root is not None:
        report.issues.extend(check_output_root(session_root))
    for t in session.trials:
        if t.raw_present and not t.withdrawn_at and session.trial_layout(t.label) is None:
            report.add("action", f"trial {t.label}", "No layout chosen for this trial.",
                       "Choose the layout it was recorded with.")
    _check_layouts(session, report)
    used = _used_cameras(session)
    for cam in used:
        if not cam.source_dir:
            continue
        a = Path(cam.source_dir).resolve()
        if not a.is_dir():
            report.add("error", cam.name, f"Folder {a} does not exist.", "Pick the camera's folder again.")
        elif in_cloud_folder(a):
            report.add("warning", cam.name, "Folder is inside a cloud-synced folder (OneDrive, Dropbox, Google Drive "
                       "or iCloud). Deleting raw footage may not remove the cloud copies.",
                       "Move the videos to a local folder if participants asked for deletion.")
    if not report.ok:
        return report
    scans = {c.id: _scan(c) for c in used}

    # Rules 1-3: calibration videos and trial candidates
    for cam in used:
        scan = scans[cam.id]
        ref = cam.intrinsic
        if ref is not None and ref.origin == "manual":
            if not Path(ref.file).is_file():
                report.add("action", f"{cam.name} intrinsic", f"The chosen file {ref.file} no longer exists.",
                           "Choose the file again.")
                cam.intrinsic = None
        elif scan is not None and len(scan.calib["intrinsic"]) == 1:
            cam.intrinsic = _ref("intrinsic", str(scan.calib["intrinsic"][0]), "found")
        elif ref is None or not Path(ref.file).is_file():
            cam.intrinsic = None
            found = scan.calib["intrinsic"] if scan else []
            why = "No file named" if not found else f"{len(found)} files named"
            report.add("action", f"{cam.name} intrinsic", f"{why} 'intrinsic.*' in {cam.source_dir or 'its folder'}.",
                       f"Choose the intrinsic calibration video for {cam.name} (any folder).")
        if scan is not None:
            for p in scan.ignored:
                report.add("info", cam.name, f"Ignored {p.name} (not a video).")
            for p in scan.trials:
                low = p.stem.lower()
                if low.startswith(ROLES) and low[-1:].isdigit():
                    report.add("warning", cam.name, f"{p.name} is treated as a trial video, not a calibration video "
                               "(only files named exactly 'intrinsic' or 'extrinsic' count).",
                               f"Rename it to {low.rstrip('0123456789')}{p.suffix} if it is a calibration video.")
    for lay in session.layouts:
        if not _layout_trials(session, lay.id):
            continue
        for lc in lay.cameras:
            cam = session.camera(lc.camera)
            if lc.extrinsic is not None and not Path(lc.extrinsic.file).is_file():
                report.add("action", f"{lay.name} {cam.name} extrinsic",
                           f"The chosen file {lc.extrinsic.file} no longer exists.", "Choose the file again.")
                lc.extrinsic = None
            if lc.extrinsic is None:
                scan = scans.get(cam.id)
                found = scan.calib["extrinsic"] if scan else []
                if len(found) == 1 and len(session.layouts_with_camera(cam.id)) == 1:
                    lc.extrinsic = _ref("extrinsic", str(found[0]), "found")
                else:
                    report.add("action", f"{lay.name} {cam.name} extrinsic",
                               f"No extrinsic video chosen for {cam.name} in {lay.name}.",
                               f"Choose the extrinsic calibration video of {cam.name} for this layout (any folder).")

    # different files for the two roles, no file shared between cameras
    used_calib: dict[str, tuple[str, str, str]] = {}  # fingerprint -> (where, camera folder, kind)
    refs = [(cam, "intrinsic", cam.intrinsic) for cam in used if cam.intrinsic]
    refs += [(session.camera(lc.camera), f"extrinsic in {lay.name}", lc.extrinsic) for lay in session.layouts
             if _layout_trials(session, lay.id) for lc in lay.cameras if lc.extrinsic]
    seen_pairs = set()
    for cam, role, ref in refs:
        try:
            fp = file_fingerprint(Path(ref.file))
        except OSError as exc:
            report.add("error", f"{cam.name} {role}", f"Cannot read {ref.file}: {exc}")
            continue
        key = (fp, cam.id, role.split()[0])
        if key in seen_pairs:
            continue  # the same camera's video used again (e.g. a duplicated layout)
        seen_pairs.add(key)
        kind = role.split()[0]
        if fp in used_calib and (used_calib[fp][1] != cam.source_dir or used_calib[fp][2] != kind):
            # a duplicated camera (same folder) may share its videos; nothing else may
            report.add("error", f"{cam.name} {role}",
                       f"Same recording as {used_calib[fp][0]} ({Path(ref.file).name}).",
                       "Every camera needs its own calibration videos, and intrinsic/extrinsic must differ.")
        used_calib.setdefault(fp, (f"{cam.name} {role}", cam.source_dir, kind))

    # Rule 6: probe everything
    per_cam_trials = {c.id: list(scans[c.id].trials) if scans.get(c.id) else [] for c in used}
    if do_probe:
        files = {Path(r.file) for _c, _r, r in refs if Path(r.file).is_file()}
        files |= {p for ps in per_cam_trials.values() for p in ps}
        for f in sorted(files):
            try:
                report.probes[str(f)] = probe(f)
            except MediaError as exc:
                report.add("error", f.name, str(exc), "Replace or remove this file.")

    # Rule 4: trial mapping
    layouts_used = {t.layout for t in session.trials if t.raw_present and t.layout}
    if _keep_mapping(session) or len(layouts_used) != 1:
        # Trials already processed (or added later with their own videos) keep their labels and files: an automatic
        # mapping could renumber them and mix up results. Missing videos are asked for in the trial's videos.
        report.mapping_method = "manual"
        for what in autofill(session):
            report.add("info", "trial mapping", f"Chosen by its name: {what}.", "Check it in the trial's videos.")
        for trial in session.trials:
            if not trial.raw_present:
                continue
            for _slot, cam, _lc in session.trial_cameras(trial.label):
                f = trial.videos.get(cam.id)
                where = f"{cam.name} trial {trial.label}"
                if not f:
                    report.add("action", where, f"No video chosen for trial {trial.label} ({trial.code}).",
                               "Choose it in the trial's videos.")
                elif not Path(f).is_file() and session.run_state.trial_status.get(trial.label) == "done":
                    report.add("info", where, f"The original video {Path(f).name} is gone. The trial's results are "
                               "kept, but it can only be run again from the copy in the project.")
                elif not Path(f).is_file():
                    report.add("action", where, f"{f} no longer exists.", "Choose the trial's video again.")
                elif do_probe and str(Path(f)) not in report.probes:
                    try:
                        report.probes[str(Path(f))] = probe(Path(f))
                    except MediaError as exc:
                        report.add("error", Path(f).name, str(exc), "Replace or remove this file.")
        if do_probe:
            _check_media(session, report, [t.label for t in session.trials])
        return report
    if not report.ok:
        return report
    lay = session.layout(next(iter(layouts_used)))
    trials = [t for t in session.trials if t.raw_present]
    per_cam = {c: per_cam_trials.get(c, []) for c in lay.camera_ids()}
    n_trials = len(trials)
    counts_ok = all(len(files) == n_trials for files in per_cam.values())
    mapping = map_by_digit(per_cam, n_trials) if counts_ok else None
    by_name = None if mapping else map_by_number(per_cam, [t.label for t in trials])
    if mapping is not None:
        report.mapping_method = "digit"
    elif by_name is not None:
        mapping = by_name
        report.mapping_method = "name"
        for cam, files in per_cam.items():
            unused = [f.name for f in files if f not in by_name[cam].values()]
            if unused:
                report.add("info", session.camera(cam).name, "Trials matched by the number in the file name. Not "
                           "used: " + ", ".join(unused) + ".", "")
    elif not counts_ok:
        for cam, files in per_cam.items():
            if len(files) != n_trials:
                report.add("error", session.camera(cam).name, f"Found {len(files)} trial videos but the project has "
                           f"{n_trials} trials: " + (", ".join(p.name for p in files) or "none") + ".",
                           "Name each trial's video with its number (01, trial01 or trial_01), or keep exactly "
                           "one video per trial in each camera folder, plus 'intrinsic' and 'extrinsic'.")
        return report
    else:
        report.mapping_method = "chronological"
        report.add("info", "trial mapping", "File names do not end in a unique trial digit on every camera; "
                   "videos were matched by recording time.", "Confirm the trial videos before running.")
        mapping = map_by_time(per_cam, report.probes, report)

    labels = sorted(next(iter(mapping.values())).keys(), key=natural_key) if mapping else []
    for t, lbl in zip(trials, labels, strict=False):
        t.label = lbl
        t.mapping_method = report.mapping_method
        t.videos = {cam: str(mapping[cam][lbl]) for cam in lay.camera_ids()}
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

    def complete(t) -> bool:
        return all(t.videos.get(c.id) for _s, c, _lc in session.trial_cameras(t.label))

    manual = [t.mapping_method == "manual" for t in session.trials]
    if all(manual):
        return any(t.videos for t in session.trials)
    return any(manual) and all(complete(t) for t, m in zip(session.trials, manual, strict=True) if not m)


def _ref(role: str, file: str, origin: str):
    from mocapapp.core.session import CalibVideo, VideoRef

    return (CalibVideo if role == "intrinsic" else VideoRef)(file=file, origin=origin)


def _check_media(session: Session, report: Report, labels: list[str]) -> None:
    infos = report.probes
    all_fps = []
    checked = set()

    def look(where: str, f: Path) -> None:
        info = infos.get(str(f))
        if not info or str(f) in checked:
            return
        checked.add(str(f))
        all_fps.append(info.fps)
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

    for cam in _used_cameras(session):
        if cam.intrinsic:
            look(f"{cam.name} {Path(cam.intrinsic.file).name}", Path(cam.intrinsic.file))
    for lay in session.layouts:
        for lc in lay.cameras:
            if lc.extrinsic and _layout_trials(session, lay.id):
                look(f"{session.camera(lc.camera).name} {Path(lc.extrinsic.file).name}", Path(lc.extrinsic.file))
    for t in session.trials:
        for _slot, cam, _lc in session.trial_cameras(t.label):
            f = t.videos.get(cam.id)
            if not f:
                continue
            look(f"{cam.name} {Path(f).name}", Path(f))
            intr = cam.intrinsic.file if cam.intrinsic else None
            if intr and str(Path(intr)) in infos and str(Path(f)) in infos:
                isize = infos[str(Path(intr))].display_size
                if infos[str(Path(f))].display_size != isize:
                    report.add("error", f"{cam.name} {Path(f).name}",
                               f"Resolution {infos[str(Path(f))].display_size} differs from the camera's intrinsic "
                               f"video {isize}. The lens calibration only holds for the same resolution and zoom.",
                               "Use a camera whose intrinsic video was recorded with the same settings (or duplicate "
                               "the camera and calibrate it with a new intrinsic video).")
    if all_fps and max(all_fps) - min(all_fps) > FPS_TOLERANCE:
        report.add("warning", "frame rate",
                   f"Frame rates differ between videos ({min(all_fps):g}-{max(all_fps):g} fps).",
                   "Use the same fps on every camera; videos will be converted to a common rate.")
    for lbl in labels:
        try:
            t = session.trial(lbl)
        except KeyError:
            continue
        durs = {cam.name: infos[str(Path(t.videos[cam.id]))].duration_s for _s, cam, _lc in session.trial_cameras(lbl)
                if t.videos.get(cam.id) and str(Path(t.videos[cam.id])) in infos}
        if len(durs) > 1 and max(durs.values()) - min(durs.values()) > CROSS_CAMERA_DURATION_S:
            report.add("warning", f"trial {lbl}", "Video lengths differ by more than 2 s between cameras ("
                       + ", ".join(f"{c} {d:.1f} s" for c, d in durs.items()) + ").",
                       "Fine if cameras were started at different times; synchronisation will align them.")
