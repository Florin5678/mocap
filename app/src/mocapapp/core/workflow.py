"""Session-level steps shared by the CLI and the GUI (the GUI must not hold logic of its own)."""

from __future__ import annotations

import errno
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from mocapapp.core.privacy import make_code, normalise_study
from mocapapp.core.session import (
    CalibVideo,
    Camera,
    Layout,
    Participant,
    Session,
    Trial,
    VideoRef,
    existing_codes,
    parse_height,
    save_session,
    session_dir_name,
)
from mocapapp.core.validate import Issue, check_output_root

MAX_TRIALS = 10
CAMERA_DIR_RE = re.compile(r"cam0*(\d+)", re.IGNORECASE)


def camera_number(name: str) -> int | None:
    """1 for a folder named cam01, cam1 or CAM01; None for other names."""
    m = CAMERA_DIR_RE.fullmatch(name.strip())
    return int(m.group(1)) if m and int(m.group(1)) > 0 else None


def sibling_camera_dirs(folder: Path) -> dict[int, Path]:
    """Camera folders next to `folder` (cam01, cam02, ...), by camera number."""
    try:
        entries = [d for d in Path(folder).parent.iterdir() if d.is_dir()]
    except OSError:
        return {}
    found = {}
    for d in sorted(entries):
        n = camera_number(d.name)
        if n is not None and n not in found:
            found[n] = d
    return found
SOFT_MAX_CAMERAS = 16


class SetupError(ValueError):
    pass


@dataclass
class NewSession:
    out_root: Path
    n_participants: int
    camera_dirs: list[Path]  # cam01, cam02, ... in this order
    study: str = ""
    heights: list[str | float | None] = field(default_factory=list)  # one per participant, '' / None = auto
    picks: dict[tuple[str, str], Path] = field(default_factory=dict)  # (camNN, intrinsic|extrinsic) -> file
    trials_per_participant: int = 1

    @property
    def n_trials(self) -> int:
        return self.n_participants * self.trials_per_participant


def check_new_session(spec: NewSession) -> list[Issue]:
    """Problems with the form before anything is written (errors block)."""
    issues = list(check_output_root(spec.out_root))
    if spec.n_participants < 1 or spec.trials_per_participant < 1:
        issues.append(Issue("error", "participants", "At least one participant with one trial is needed.",
                            "Add a participant."))
    elif spec.n_trials > MAX_TRIALS:
        issues.append(Issue("error", "trials", f"{spec.n_trials} trials in total; at most {MAX_TRIALS} per session.",
                            "Use fewer participants or trials per participant, or a second session."))
    if len(spec.camera_dirs) < 2:
        issues.append(Issue("error", "cameras", "At least two cameras are needed.", "Add a folder per camera."))
    elif len(spec.camera_dirs) == 2:
        issues.append(Issue("warning", "cameras", "Only two cameras: 3 or more give more reliable results.", ""))
    if len(spec.camera_dirs) > SOFT_MAX_CAMERAS:
        issues.append(Issue("warning", "cameras", f"More than {SOFT_MAX_CAMERAS} cameras: processing will be slow.",
                            ""))
    for i, d in enumerate(spec.camera_dirs, 1):
        if not Path(d).is_dir():
            issues.append(Issue("error", f"cam{i:02d}", f"{d} is not a folder.", "Choose the camera's folder."))
    if spec.heights and len(spec.heights) != spec.n_participants:
        issues.append(Issue("error", "heights", f"{len(spec.heights)} heights for {spec.n_participants} participants.",
                            "Give one height per participant (blank = estimate)."))
    for i, h in enumerate(spec.heights, 1):
        try:
            if parse_height(h) is None:
                issues.append(Issue("warning", f"participant {i}", "No height: it will be estimated (not "
                                    "recommended).", "Enter the participant's measured height."))
        except ValueError as exc:
            issues.append(Issue("error", f"participant {i}", str(exc), "Enter the height in metres or centimetres."))
    try:
        normalise_study(spec.study)
    except ValueError as exc:
        issues.append(Issue("error", "study code", str(exc), "Use up to 8 letters/digits."))
    return issues


def create_session(spec: NewSession) -> tuple[Path, Session]:
    """Write a new session folder with session.json. Raises SetupError on blocking problems."""
    errors = [i for i in check_new_session(spec) if i.level == "error"]
    if errors:
        raise SetupError("\n".join(str(i) for i in errors))
    study = normalise_study(spec.study)
    root = Path(spec.out_root) / session_dir_name(study)
    if root.exists():
        raise SetupError(f"{root} already exists; wait a minute or choose another output folder.")
    taken = existing_codes(Path(spec.out_root))
    codes: list[str] = []
    for _ in range(spec.n_participants):
        codes.append(make_code(study, taken | set(codes)))
    heights = [parse_height(h) for h in spec.heights] if spec.heights else [None] * spec.n_participants
    k = spec.trials_per_participant
    cameras = []
    for i, d in enumerate(spec.camera_dirs, 1):
        name = f"cam{i:02d}"
        intr, extr = spec.picks.get((name, "intrinsic")), spec.picks.get((name, "extrinsic"))
        cameras.append(Camera(
            name=name, source_dir=str(Path(d).resolve()),
            intrinsic=CalibVideo(file=str(Path(intr).resolve()), origin="manual") if intr else None,
            extrinsic=VideoRef(file=str(Path(extr).resolve()), origin="manual") if extr else None))
    session = Session(study_code=study, n_trials=spec.n_trials, cameras=cameras, trials_per_participant=k,
                      participants=[Participant(label=str(i + 1), code=c, height_m=h)
                                    for i, (c, h) in enumerate(zip(codes, heights, strict=True))],
                      trials=[Trial(label=str(i + 1), participant=str(i // k + 1)) for i in range(spec.n_trials)])
    fill_versions(session)
    save_session(session, root)
    return root, session


def fill_versions(session: Session) -> None:
    import Pose2Sim

    repo = Path(Pose2Sim.__file__).parents[1]
    try:
        proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, check=False)
        session.pose2sim_commit = proc.stdout.strip() or None
    except OSError:  # git not installed
        session.pose2sim_commit = None
    try:
        import opensim

        session.opensim_version = opensim.GetVersion()
    except Exception:  # OpenSim missing is reported by `doctor`
        session.opensim_version = None


def _next_label(labels) -> str:
    return str(max((int(x) for x in labels if str(x).isdigit()), default=0) + 1)


def add_participant(session: Session, root: Path, height: str | float | None = None) -> Participant:
    """A participant added after the session was created: a new pseudonym and trials_per_participant trials."""
    ensure_not_frozen(session)
    if len(session.trials) + session.trials_per_participant > MAX_TRIALS:
        raise SetupError(f"A session holds at most {MAX_TRIALS} trials. Start a second session for more "
                         "participants.")
    taken = existing_codes(Path(root).parent) | {p.code for p in session.participants}
    person = Participant(label=_next_label(p.label for p in session.participants),
                         code=make_code(session.study_code, taken), height_m=parse_height(height))
    session.participants.append(person)
    for _ in range(session.trials_per_participant):
        session.trials.append(Trial(label=_next_label(t.label for t in session.trials), participant=person.label))
    session.link()
    save_session(session, root)
    return person


def remove_participant(session: Session, root: Path, label: str) -> None:
    """Remove a participant who has no data in the session yet (for example, one who did not come)."""
    person = session.participant(label)
    if len(session.participants) == 1:
        raise SetupError("A session needs at least one participant.")
    layout = Layout(root)
    for t in session.trials_of(label):
        if layout.trial(t.label).exists() or t.label in session.run_state.trial_status:
            raise SetupError(f"Participant {label} ({person.code}) already has videos or results in this session. "
                             "Use 'Participant data and deletion' on the Results step to delete their data instead.")
    gone = {t.label for t in session.trials_of(label)}
    session.trials = [t for t in session.trials if t.label not in gone]
    session.participants = [p for p in session.participants if p.label != label]
    for cam in session.cameras:
        cam.trial_files = {k: v for k, v in cam.trial_files.items() if k not in gone}
        for k in gone:
            _drop_input(layout.inputs(cam.name) / f"trial_{k}.mp4")
    session.link()
    save_session(session, root)


PSEUDONYM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,23}$")


def check_renamable(session: Session, label: str) -> Participant:
    person = session.participant(label)
    if person.withdrawn_at:
        raise SetupError(f"Participant {label} withdrew; their pseudonym stays as it was.")
    if person.consent.attested:
        raise SetupError(f"The signed consent form of participant {label} carries the pseudonym {person.code}. To "
                         "change it anyway, untick 'Signed consent received' first and correct the paper form.")
    done = session.run_state
    if any(t.label in done.trial_status or done.stage_done.get(t.label) for t in session.trials_of(label)):
        raise SetupError(f"Participant {label} ({person.code}) already has results under this pseudonym, so it can "
                         "no longer be changed.")
    return person


def rename_participant(session: Session, root: Path, label: str, code: str) -> Participant:
    """Give a participant another pseudonym (typed by the researcher), before consent and results refer to it."""
    code = code.strip()
    person = session.participant(label)
    if code == person.code:
        return person
    check_renamable(session, label)
    if not PSEUDONYM_RE.match(code):
        raise SetupError("A pseudonym is 1-24 letters, digits, '-' or '_' with no spaces, starting with a letter or "
                         "digit. Do not use the participant's real name.")
    taken = {c.upper() for c in existing_codes(Path(root).parent)} - {person.code.upper()}
    taken |= {p.code.upper() for p in session.participants if p is not person}
    if code.upper() in taken:
        raise SetupError(f"The pseudonym {code} is already used in this output folder; choose another one.")
    person.code = code
    save_session(session, root)
    return person


def new_pseudonym(session: Session, root: Path, label: str) -> Participant:
    """A fresh random pseudonym for a participant (the dice button)."""
    check_renamable(session, label)
    taken = existing_codes(Path(root).parent) | {p.code for p in session.participants}
    return rename_participant(session, root, label, make_code(session.study_code, taken))


# ---------- moving a session to another output folder ----------

COPY_MARGIN = 1.05  # free space needed when the session is copied to another drive


@dataclass
class MovePlan:
    source: Path
    dest: Path
    copy: bool  # another drive: copy, check the copy, then remove the old folder
    size: int = 0  # bytes to copy (0 when the folder is only renamed)
    free: int = 0
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        how = (f"The session is on another drive: its {self.size / 1e9:.1f} GB are copied, the copy is checked, "
               "and then the old folder is removed." if self.copy
               else "The folder is moved on the same drive, which takes a moment.")
        return "\n\n".join([f"From: {self.source}\nTo: {self.dest}", how, *self.warnings])


def _nearest_existing(path: Path) -> Path:
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


def _tree(root: Path) -> dict[str, int]:
    """Relative path -> size of every file below root."""
    out = {}
    for path in Path(root).rglob("*"):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = path.stat().st_size
    return out


def plan_move(session: Session, root: Path, new_out: Path) -> MovePlan:
    """Check that the session folder can move into another output folder. Raises SetupError with the reason."""
    root, out = Path(root).resolve(), Path(new_out).expanduser().resolve()
    issues = check_output_root(out)
    errors = [i for i in issues if i.level == "error"]
    if errors:
        raise SetupError("\n".join(f"{i.what} {i.fix}" for i in errors))
    if out == root.parent:
        raise SetupError(f"The session is already in {out}.")
    if out == root or root in out.parents:
        raise SetupError("Choose a folder outside the session folder itself.")
    dest = out / root.name
    if dest.exists():
        raise SetupError(f"{dest} already exists. Choose another output folder, or rename or remove that folder "
                         "first.")
    taken = {c.upper() for c in existing_codes(out)}
    clash = sorted(p.code for p in session.participants if p.code.upper() in taken)
    if clash:
        raise SetupError(f"The pseudonym{'s' if len(clash) > 1 else ''} {', '.join(clash)} "
                         f"{'are' if len(clash) > 1 else 'is'} already used by another session in {out}. Pseudonyms "
                         "must be unique within an output folder; choose another one.")
    existing = _nearest_existing(out)
    copy = os.stat(existing).st_dev != os.stat(root).st_dev
    plan = MovePlan(root, dest, copy, warnings=[f"{i.what} {i.fix}" for i in issues])
    if copy:
        plan.size = sum(_tree(root).values())
        plan.free = shutil.disk_usage(existing).free
        if plan.size * COPY_MARGIN > plan.free:
            raise SetupError(f"Not enough space on that drive: the session needs about {plan.size / 1e9:.1f} GB, "
                             f"{plan.free / 1e9:.1f} GB are free. Free some space or choose another folder.")
    return plan


def _in_use(exc: OSError, plan: MovePlan) -> SetupError:
    return SetupError(f"The session could not be moved ({exc.strerror or exc}). A file in it is probably open in "
                      "another program (a video player, Excel, File Explorer showing the folder): close it and try "
                      f"again. Nothing was changed; the session is still at {plan.source}.")


def move_session(plan: MovePlan, progress=print) -> tuple[Path, list[str]]:
    """Move the session folder as planned. Returns the new folder and warnings for the researcher.

    Nothing inside the folder refers to where it is, so a rename or a checked copy is all it takes. A copy is only
    removed from the old place once every file arrived with its size; if that removal fails, the warning says so,
    because the old folder still holds participant data."""
    src, dest = plan.source, plan.dest
    if dest.exists():
        raise SetupError(f"{dest} already exists. Choose another output folder.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not plan.copy:
        try:
            src.rename(dest)
            return dest, []
        except OSError as exc:
            if exc.errno != errno.EXDEV and getattr(exc, "winerror", None) != 17:  # 17: not the same device
                raise _in_use(exc, plan) from exc
    from mocapapp.core.deletion import _remove

    files = _tree(src)
    progress(f"Copying {len(files)} files ({sum(files.values()) / 1e9:.1f} GB) to {dest} ...")
    copied = 0

    def copy(a, b):
        nonlocal copied
        copied += 1
        if copied % 20 == 0:
            progress(f"Copying file {copied} of {len(files)} ...")
        return shutil.copy2(a, b)

    try:
        shutil.copytree(src, dest, copy_function=copy)
        if _tree(dest) != files:
            raise OSError("the copy does not match the original")
    except OSError as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise SetupError(f"Copying the session to {dest} failed ({exc}). Nothing was changed; the session is still "
                         f"at {src}.") from exc
    progress("Removing the old folder ...")
    try:
        _remove(src)
    except OSError as exc:
        return dest, [f"The session was copied to {dest}, but the old folder {src} could not be removed completely "
                      f"({exc}). It still holds participant data: delete it yourself once you have checked the new "
                      "folder."]
    return dest, []


def set_trial_participants(session: Session, table: dict[str, str]) -> None:
    """Which participant each trial belongs to (trial label -> participant label), from the Videos step."""
    ensure_not_frozen(session)
    known = {p.label for p in session.active_participants}
    for trial_label, person in table.items():
        trial = session.trial(trial_label)
        if person not in known:
            raise SetupError(f"There is no participant {person} for trial {trial_label}.")
        if trial.participant != person and session.run_state.stage_done.get(trial_label):
            raise SetupError(f"Trial {trial_label} was already processed for participant {trial.participant}; "
                             "it cannot be moved to another participant.")
    for trial_label, person in table.items():
        session.trial(trial_label).participant = person
    session.link()


def ensure_not_frozen(session: Session) -> None:
    """After any raw-footage deletion the session's videos are fixed: no re-validation, re-mapping or re-copying."""
    if session.frozen:
        gone = ", ".join(f"trial {t.label} ({t.code})" for t in session.trials if not t.raw_present)
        raise SetupError(f"Raw footage was deleted in this session ({gone}), so its videos can no longer be checked, "
                         "changed or prepared again. Start a new session for new recordings.")


def pick_video(session: Session, camera: str, role: str, file: Path, root: Path | None = None) -> None:
    """D3: choose a calibration video from any folder. With `root`, results computed from the old video are
    removed (a new intrinsic video also means a new camera position)."""
    from mocapapp.core.calibration import extrinsics
    from mocapapp.core.calibration.service import _invalidate_calib
    from mocapapp.core.session import Layout

    ensure_not_frozen(session)
    path = Path(file).resolve()
    if not path.is_file():
        raise SetupError(f"{path} does not exist.")
    cls = CalibVideo if role == "intrinsic" else VideoRef
    old = getattr(session.camera(camera), role)
    setattr(session.camera(camera), role, cls(file=str(path), origin="manual"))
    if root is not None and (old is None or Path(old.file).resolve() != path):
        work = Layout(root).calibration_work
        _drop_input(Layout(root).inputs(camera) / f"{role}.mp4")
        extrinsics.result_path(work, camera).unlink(missing_ok=True)
        session.extrinsics = [e for e in session.extrinsics if e.get("camera") != camera]
        _invalidate_calib(root)


def _drop_input(path: Path) -> None:
    """Remove a prepared copy (and its source stamp) so it is prepared again from the new source."""
    path.unlink(missing_ok=True)
    path.with_name(path.name + ".source").unlink(missing_ok=True)


def set_mapping(session: Session, table: dict[tuple[str, str], Path], root: Path | None = None) -> None:
    """Manual trial mapping (tier 3): (camera, trial label) -> file. With `root`, copies of files that changed are
    removed from inputs/ and the trial folder, so they are prepared again."""
    from mocapapp.core.session import Layout

    ensure_not_frozen(session)
    for (cam, label), file in table.items():
        if not Path(file).is_file():
            raise SetupError(f"{file} does not exist.")
        new = str(Path(file).resolve())
        old = session.camera(cam).trial_files.get(label)
        session.camera(cam).trial_files[label] = new
        if root is not None and (old is None or Path(old).resolve() != Path(new)):
            _drop_input(Layout(root).inputs(cam) / f"trial_{label}.mp4")
            (Layout(root).trial(label) / "videos" / f"{cam}.mp4").unlink(missing_ok=True)
    for t in session.trials:
        t.mapping_method = "manual"


def run_ready(session: Session, root: Path) -> list[Issue]:
    """Everything the pre-Run summary must show as done (calibration, assembly, confirmations)."""
    from mocapapp.core import runner
    from mocapapp.core.calibration.service import calibration_state
    from mocapapp.core.session import Layout

    issues = []
    if session.calibration_source == "app":
        for cam, state in calibration_state(session, root).items():
            if state != "ready":
                issues.append(Issue("error", cam, f"Calibration: {state}.", "Finish the calibration steps."))
    elif not Layout(root).calib_file.is_file():
        issues.append(Issue("error", "calibration", "The imported calibration file is missing.", ""))
    return issues + runner.preflight(session, root, [t.label for t in session.active_trials])


def inputs_ready(session: Session, root: Path) -> bool:
    """Every video has been copied/normalised into inputs/ and the trial folders exist."""
    from mocapapp.core.session import Layout

    layout = Layout(root)
    trials = [t for t in session.trials if t.raw_present]  # deleted footage is gone on purpose
    for cam in session.cameras:
        names = [f"trial_{t.label}.mp4" for t in trials]
        if session.calibration_source == "app":
            names += ["intrinsic.mp4", "extrinsic.mp4"]
        if not all((layout.inputs(cam.name) / n).is_file() for n in names):
            return False
    return all((layout.trial(t.label) / "Config.toml").is_file() and
               all((layout.trial(t.label) / "videos" / f"{c.name}.mp4").is_file() for c in session.cameras)
               for t in trials)


def prepare_session(session: Session, root: Path, progress=print, dry_run: bool = False):
    """Validate, then copy/convert every video into inputs/ and build the trial folders.
    Returns (report, plan); the plan is None when validation failed."""
    from mocapapp.core.assemble import assemble_trials, plan_inputs, prepare_inputs
    from mocapapp.core.validate import validate

    ensure_not_frozen(session)
    report = validate(session, root)
    save_session(session, root)
    if not report.ok:
        return report, None
    plan = plan_inputs(session, root, report)
    if not dry_run:
        prepare_inputs(plan, report, progress)
        assemble_trials(session, root, progress)
        save_session(session, root)
    return report, plan
