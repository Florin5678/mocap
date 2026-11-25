"""Session-level steps shared by the CLI and the GUI (the GUI must not hold logic of its own)."""

from __future__ import annotations

import errno
import json
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
    CameraPosition,
    DetailField,
    Layout,
    Participant,
    Session,
    Trial,
    VideoRef,
    atomic_write_text,
    existing_codes,
    format_height,
    load_session,
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
    """Only a withdrawn participant keeps their pseudonym for good (their data is gone; the logs name it)."""
    person = session.participant(label)
    if person.withdrawn_at:
        raise SetupError(f"Participant {label} withdrew; their pseudonym stays as it was.")
    return person


def rename_consequences(session: Session, label: str) -> list[str]:
    """What a new pseudonym affects beyond session.json; the researcher confirms these first (empty: nothing)."""
    person = session.participant(label)
    out = []
    if person.consent.attested:
        out.append(f"The signed consent form carries {person.code}: write the new pseudonym on it, so that the form "
                   "still links to the data.")
    rs = session.run_state
    if any(t.label in rs.trial_status or rs.stage_done.get(t.label) for t in session.trials_of(label)):
        out.append("Results exist under the old pseudonym: the quality report and the exported files (file names, "
                   "manifest.json, trials.csv) are updated to the new one. Copies made elsewhere keep the old one.")
    return out


def rename_participant(session: Session, root: Path, label: str, code: str, confirmed: bool = False) -> Participant:
    """Give a participant another pseudonym (typed by the researcher). After consent or results this needs
    `confirmed` (see rename_consequences); the old pseudonym is kept in `former_codes` and exports follow."""
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
    consequences = rename_consequences(session, label)
    if consequences and not confirmed:
        raise SetupError("Changing this pseudonym needs confirming: " + " ".join(consequences))
    old = person.code
    person.code = code
    if consequences:
        person.former_codes.append(old)
    save_session(session, root)
    if consequences:
        _rename_exports(session, root, old, code, [t.label for t in session.trials_of(label)])
    return person


def _rename_exports(session: Session, root: Path, old: str, new: str, labels: list[str]) -> None:
    """Exported files, manifest.json and trials.csv follow a new pseudonym; the quality report is rewritten."""
    import json

    layout = Layout(root)
    out = layout.export
    manifest = out / "manifest.json"
    if manifest.is_file():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for f in data.get("files", []):
            if f.get("code") == old and f.get("trial") in labels:
                name = f.get("file", "")
                if name.startswith(f"{old}_"):
                    renamed = new + name[len(old):]
                    if (out / name).is_file():
                        (out / name).replace(out / renamed)
                    f["file"] = renamed
                f["code"] = new
        for row in data.get("trials", []):
            if row.get("code") == old and row.get("trial") in labels:
                row["code"] = new
        atomic_write_text(manifest, json.dumps(data, indent=2) + "\n")
    table = out / "trials.csv"
    if table.is_file():
        lines = table.read_text(encoding="utf-8").splitlines(keepends=True)
        fixed = [ln.replace(f",{old}", f",{new}", 1) if ln.split(",")[1:2] == [old] and ln.split(",")[0] in labels
                 else ln for ln in lines[1:]]
        atomic_write_text(table, "".join(lines[:1] + fixed))
    if layout.qa.is_dir():
        from mocapapp.core.qa import write_report

        try:
            write_report(session, root)
        except (OSError, ValueError):
            pass


def new_pseudonym(session: Session, root: Path, label: str, confirmed: bool = False) -> Participant:
    """A fresh random pseudonym for a participant (the dice button)."""
    check_renamable(session, label)
    taken = existing_codes(Path(root).parent) | {p.code for p in session.participants}
    return rename_participant(session, root, label, make_code(session.study_code, taken), confirmed)


# ---------- participant details, asked per trial (0.1.0rc13) ----------

DETAIL_KEY_RE = re.compile(r"[^a-z0-9]+")
MAX_TEXT = 80


def add_detail(session: Session, root: Path, label: str, kind: str = "text", unit: str = "",
               choices: list[str] | None = None) -> DetailField:
    """A custom detail (asked for every trial from now on)."""
    label = " ".join(label.split())
    if not 1 <= len(label) <= 40:
        raise SetupError("Give the detail a name of 1-40 characters, for example 'Leg length'.")
    if kind not in ("number", "text", "choice"):
        raise SetupError("A detail is a number, a text or a choice.")
    key = DETAIL_KEY_RE.sub("_", label.lower()).strip("_") or "detail"
    if any(d.label.casefold() == label.casefold() for d in session.details):
        raise SetupError(f"There is already a detail called '{label}'.")
    base, n = key, 2
    while any(d.key == key for d in session.details):
        key, n = f"{base}_{n}", n + 1
    options = [" ".join(c.split()) for c in (choices or []) if c.strip()]
    if kind == "choice" and len(set(o.casefold() for o in options)) < 2:
        raise SetupError("A choice needs at least two different options, for example 'left, right'.")
    field_ = DetailField(key=key, label=label, kind=kind, unit=unit.strip() if kind == "number" else "",
                         choices=options if kind == "choice" else [], enabled=True)
    session.details.append(field_)
    save_session(session, root)
    return field_


def set_detail_enabled(session: Session, root: Path, key: str, enabled: bool) -> None:
    """Ask a detail for every trial, or stop asking it (values already entered are kept)."""
    try:
        session.detail(key).enabled = enabled
    except KeyError as exc:
        raise SetupError(str(exc).strip("'\"")) from None
    save_session(session, root)


def remove_detail(session: Session, root: Path, key: str) -> None:
    """Remove a custom detail and its values (built-in details can only be switched off)."""
    field_ = session.detail(key)
    if field_.builtin:
        raise SetupError(f"'{field_.label}' is built in: untick it to stop asking it.")
    session.details = [d for d in session.details if d.key != key]
    for t in session.trials:
        t.details.pop(key, None)
    save_session(session, root)


def detail_value(field_: DetailField, value: str | float | None) -> str:
    """The value as stored ('' = not given). Raises SetupError with what to enter instead."""
    text = "" if value is None else " ".join(str(value).split())
    if not text:
        return ""
    if field_.key == "height":
        try:
            return format_height(parse_height(text))
        except ValueError as exc:
            raise SetupError(str(exc)) from None
    if field_.kind == "number":
        try:
            number = float(text.replace(",", ".").removesuffix(field_.unit).strip())
        except ValueError:
            raise SetupError(f"{field_.label}: '{text}' is not a number.") from None
        if number < 0:
            raise SetupError(f"{field_.label} cannot be negative.")
        return f"{number:g}"
    if field_.kind == "choice":
        for option in field_.choices:
            if option.casefold() == text.casefold():
                return option
        raise SetupError(f"{field_.label}: choose one of {', '.join(field_.choices)}.")
    if len(text) > MAX_TEXT:
        raise SetupError(f"{field_.label}: at most {MAX_TEXT} characters.")
    return text


def set_trial_detail(session: Session, root: Path, label: str, key: str, value: str | float | None) -> str:
    trial = session.trial(label)
    if trial.withdrawn_at:
        raise SetupError(f"Trial {label}: the participant withdrew, so no details are kept.")
    stored = detail_value(session.detail(key), value)
    if stored:
        trial.details[key] = stored
        for other in session.trials_of(trial.participant):  # the same person's other trials, where still empty
            if other is not trial and not other.withdrawn_at and not other.details.get(key):
                other.details[key] = stored
    else:
        trial.details.pop(key, None)
    save_session(session, root)
    return stored


def copy_details(session: Session, trial: Trial) -> None:
    """Fill a trial's details from the same participant's earliest other trial that has any (none: empty)."""
    others = [t for t in session.trials_of(trial.participant) if t is not trial and t.details]
    others.sort(key=lambda t: int(t.label) if t.label.isdigit() else 0)
    trial.details = dict(others[0].details) if others else {}


# ---------- adding and removing trials (0.1.0rc13) ----------

def add_trial(session: Session, root: Path, participant: str | None = None) -> Trial:
    """Another trial, for a new participant with a random pseudonym (participant None, the Trials step) or for an
    existing one, for example recorded later: its details are then copied from their earlier trial. Its videos are
    chosen in the mapping table of the Trials step."""
    ensure_not_frozen(session)
    if participant is None:
        if len(session.trials) >= MAX_TRIALS:
            raise SetupError(f"A session holds at most {MAX_TRIALS} trials. Start a second session for more.")
        taken = existing_codes(Path(root).parent) | {p.code for p in session.participants}
        person = Participant(label=_next_label(p.label for p in session.participants),
                             code=make_code(session.study_code, taken))
        session.participants.append(person)
        participant = person.label
    person = session.participant(participant)
    if person.withdrawn_at:
        raise SetupError(f"Participant {participant} withdrew; no trials can be added for them.")
    if len(session.trials) >= MAX_TRIALS:
        raise SetupError(f"A session holds at most {MAX_TRIALS} trials. Start a second session for more.")
    trial = Trial(label=_next_label(t.label for t in session.trials), participant=participant,
                  mapping_method="manual")
    session.trials.append(trial)
    session.link()
    copy_details(session, trial)
    save_session(session, root)
    return trial


def trial_has_data(session: Session, root: Path, label: str) -> bool:
    from mocapapp.core.session import Layout

    layout = Layout(root)
    return (layout.trial(label).exists() or label in session.run_state.trial_status
            or any((layout.inputs(c.name) / f"trial_{label}.mp4").exists() for c in session.cameras))


def drop_trial(session: Session, root: Path, label: str) -> None:
    """Forget a trial in session.json (its files are handled by the caller)."""
    from mocapapp.core.session import Layout

    rs = session.run_state
    for table in (rs.stage_done, rs.trial_status, rs.sync, rs.errors, rs.stage_settings, rs.keypoints_removed):
        table.pop(label, None)
    for cam in session.cameras:
        cam.trial_files.pop(label, None)
        for pos in cam.positions + cam.lenses:
            pos.trials = [t for t in pos.trials if t != label]
        _drop_input(Layout(root).inputs(cam.name) / f"trial_{label}.mp4")
    Layout(root).trial_calib(label).unlink(missing_ok=True)
    session.trials = [t for t in session.trials if t.label != label]
    session.link()
    save_session(session, root)


def remove_empty_trial(session: Session, root: Path, label: str) -> None:
    """Remove a trial that has no videos or results yet (for example added by mistake)."""
    session.trial(label)
    if len(session.trials) == 1:
        raise SetupError("A session needs at least one trial.")
    if trial_has_data(session, root, label):
        raise SetupError(f"Trial {label} already has videos or results. Delete it with its data instead "
                         "(you will be asked to type the pseudonym).")
    drop_trial(session, root, label)
    if drop_unused_participants(session):  # a participant left without trials goes with it
        save_session(session, root)


def drop_unused_participants(session: Session) -> list[str]:
    """Participants exist with their trials (0.1.0rc17): one left without any trial is forgotten. Returns their
    labels."""
    used = {t.participant for t in session.trials}
    gone = [p.label for p in session.participants if p.label not in used and not p.withdrawn_at]
    session.participants = [p for p in session.participants if p.label not in gone]
    return gone


def assign_trial(session: Session, root: Path, label: str, participant: str) -> None:
    """The Trials table: a trial belongs to another (existing) participant. One left without trials is removed."""
    set_trial_participants(session, {label: participant})
    drop_unused_participants(session)
    save_session(session, root)


def set_trial_code(session: Session, root: Path, label: str, code: str, confirmed: bool = False) -> str:
    """The pseudonym typed in a trial's row: another participant's pseudonym moves the trial to them; a new one
    renames the trial's participant (with all their trials). Returns 'assigned', 'renamed' or '' (unchanged)."""
    trial = session.trial(label)
    code = code.strip()
    if code == trial.code:
        return ""
    other = next((p for p in session.active_participants if p.code.upper() == code.upper()), None)
    if other is not None and other.label != trial.participant:
        assign_trial(session, root, label, other.label)
        return "assigned"
    rename_participant(session, root, trial.participant, code, confirmed)
    return "renamed"


# ---------- further calibration videos, assigned per trial (0.1.0rc13 extrinsic, 0.1.0rc17 both) ----------

def _refresh_calibration(session: Session, root: Path) -> None:
    """Rewrite the calibration files when every camera and video in use is ready; otherwise remove stale copies."""
    from mocapapp.core.calibration import service

    if session.calibration_source != "app":
        return
    try:
        service.write_calibration(session, root)
    except service.CalibrationStepError:
        service._invalidate_calib(root)


def _mark_stale(session: Session, labels) -> None:
    from mocapapp.core.runner import stale_stages

    rs = session.run_state
    for label in labels:
        if rs.trial_status.get(label) == "done" and stale_stages(session, label):
            rs.trial_status[label] = "partial"


def _check_role(role: str) -> None:
    if role not in ("intrinsic", "extrinsic"):
        raise SetupError("A calibration video is 'intrinsic' or 'extrinsic'.")


def _new_calib_file(session: Session, camera: str, file: Path) -> Path:
    if session.calibration_source != "app":
        raise SetupError("This session uses an imported calibration; its calibration videos cannot change.")
    ensure_not_frozen(session)
    path = Path(file).resolve()
    if not path.is_file():
        raise SetupError(f"{path} does not exist.")
    cam = session.camera(camera)
    used = [f for role in ("intrinsic", "extrinsic") for _, f in cam.calib_videos(role)]
    if any(Path(u).resolve() == path for u in used):
        raise SetupError(f"{path.name} is already a calibration video of {camera}.")
    return path


def _forget_video(session: Session, root: Path | None, camera: str, role: str, key: str) -> None:
    """Results computed from a calibration video that was replaced or removed; its copy in the session."""
    from mocapapp.core.calibration import intrinsics, service

    if root is None:
        return
    layout = Layout(root)
    work = layout.calibration_work
    _drop_input(layout.inputs(camera) / service.input_name(role, key))
    for f in list(work.glob(f"extrinsics_{camera}*.json")) if work.is_dir() else []:
        m = service.EXTRINSICS_FILE_RE.fullmatch(f.stem)
        if m and m.group("cam") == camera and (m.group("lens" if role == "intrinsic" else "pos") or "") == key:
            f.unlink()
    if role == "intrinsic":
        intrinsics.result_path(work, camera, key).unlink(missing_ok=True)
    else:
        service.review_image(root, camera, key).unlink(missing_ok=True)
    field_ = "lens" if role == "intrinsic" else "position"
    session.extrinsics = [e for e in session.extrinsics
                          if not (e.get("camera") == camera and e.get(field_, "") == key)]
    service._invalidate_calib(root)


def add_calib_videos(session: Session, root: Path | None, camera: str, role: str, files: list[Path]) -> list[str]:
    """More calibration videos for a camera. Without a main video the first becomes it; the others are further
    videos, used by no trial until assigned (set_calib_assignment). Returns their keys ("" = main)."""
    _check_role(role)
    cam = session.camera(camera)
    keys = []
    for file in files:
        path = _new_calib_file(session, camera, file)
        if getattr(cam, role) is None:
            setattr(cam, role, (CalibVideo if role == "intrinsic" else VideoRef)(file=str(path), origin="manual"))
            keys.append("")
            continue
        extra = CameraPosition(key=_next_label(["1"] + [p.key for p in cam.extras(role)]), file=str(path))
        cam.extras(role).append(extra)
        keys.append(extra.key)
    if root is not None:
        save_session(session, root)
    return keys


def replace_calib_video(session: Session, root: Path | None, camera: str, role: str, key: str, file: Path) -> None:
    """Another file for a calibration video; the trials using it stay with it. Its results are computed again."""
    _check_role(role)
    if not key:
        pick_video(session, camera, role, Path(file), root)
    else:
        extra = session.camera(camera).extra(role, key)
        extra.file = str(_new_calib_file(session, camera, file))
        _forget_video(session, root, camera, role, key)
        _mark_stale(session, extra.trials)
    if root is not None:
        save_session(session, root)


def remove_calib_video(session: Session, root: Path, camera: str, role: str, key: str) -> None:
    """Remove a further calibration video; its trials use the camera's main video again."""
    _check_role(role)
    if not key:
        raise SetupError("The main calibration video cannot be removed; choose another file for it instead.")
    cam = session.camera(camera)
    extra = cam.extra(role, key)
    if role == "intrinsic":
        cam.lenses = [p for p in cam.lenses if p.key != key]
    else:
        cam.positions = [p for p in cam.positions if p.key != key]
    _forget_video(session, root, camera, role, key)
    _mark_stale(session, extra.trials)
    _resolve(session, root, camera)
    save_session(session, root)


def set_calib_assignment(session: Session, root: Path, camera: str, role: str, choice: dict[str, str]) -> None:
    """Which calibration video each trial uses with this camera: trial label -> key ("" = the main video)."""
    _check_role(role)
    cam = session.camera(camera)
    before = {t.label: cam.video_for(role, t.label) for t in session.trials}
    keys = {""} | {p.key for p in cam.extras(role)}
    for label, key in choice.items():
        session.trial(label)
        if key not in keys:
            raise SetupError(f"{camera} has no {role} video {key}.")
        if key != before.get(label) and not session.trial(label).raw_present:
            raise SetupError(f"Trial {label}: its raw footage was deleted, so it cannot be computed again with "
                             "another calibration video.")
    for extra in cam.extras(role):
        kept = [t for t in extra.trials if t not in choice]
        extra.trials = sorted(set(kept) | {t for t, k in choice.items() if k == extra.key},
                              key=lambda x: int(x) if x.isdigit() else 0)
    changed = [t.label for t in session.trials if cam.video_for(role, t.label) != before[t.label]]
    _mark_stale(session, changed)
    _resolve(session, root, camera)
    save_session(session, root)


def _resolve(session: Session, root: Path, camera: str) -> None:
    """Camera positions for the pairs of videos now in use, from saved clicks; then the calibration files."""
    from mocapapp.core.calibration import service

    if session.calibration_source != "app":
        return
    service.resolve_extrinsics(session, root, camera)
    _refresh_calibration(session, root)


def prepare_calib_video(session: Session, root: Path, camera: str, role: str, key: str) -> Path:
    """Copy (or convert) a further calibration video into the session, like the other calibration videos."""
    from mocapapp.core import media
    from mocapapp.core.calibration.service import input_name

    src = Path(session.camera(camera).extra(role, key).file)
    dst = Layout(root).inputs(camera) / input_name(role, key)
    stamp = dst.with_name(dst.name + ".source")
    mark = json.dumps({"source": str(src.resolve()), "size": src.stat().st_size, "mtime": int(src.stat().st_mtime)})
    if not (dst.is_file() and stamp.is_file() and stamp.read_text(encoding="utf-8") == mark):
        media.normalise(src, dst)
        stamp.write_text(mark, encoding="utf-8")
    return dst


# older names, kept for the CLI's `calibrate position` (a camera moved between trials)

def add_camera_position(session: Session, root: Path, camera: str, file: Path, trials: list[str]) -> CameraPosition:
    """The camera was moved between trials: a further extrinsic video for it, used by `trials`."""
    if not session.camera(camera).extrinsic:
        raise SetupError(f"{camera} has no extrinsic video yet; choose that first.")
    if not trials:
        raise SetupError("Choose at least one trial for this camera position.")
    key = add_calib_videos(session, root, camera, "extrinsic", [Path(file)])[0]
    set_position_trials(session, root, camera, key, trials)
    return session.camera(camera).position(key)


def set_position_trials(session: Session, root: Path, camera: str, key: str, trials: list[str]) -> None:
    """Which trials use this further extrinsic video (the others keep theirs)."""
    extra = session.camera(camera).position(key)
    if not trials:
        raise SetupError("Choose at least one trial for this camera position.")
    choice = {t: "" for t in extra.trials if t not in trials}
    choice.update({t: key for t in trials})
    set_calib_assignment(session, root, camera, "extrinsic", choice)


def remove_camera_position(session: Session, root: Path, camera: str, key: str) -> None:
    remove_calib_video(session, root, camera, "extrinsic", key)


def prepare_position(session: Session, root: Path, camera: str, key: str) -> Path:
    return prepare_calib_video(session, root, camera, "extrinsic", key)


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

    Paths stored in session.json that point inside the folder (videos kept in it) are updated. A copy is only
    removed from the old place once every file arrived with its size; if that removal fails, the warning says so,
    because the old folder still holds participant data."""
    src, dest = plan.source, plan.dest
    if dest.exists():
        raise SetupError(f"{dest} already exists. Choose another output folder.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not plan.copy:
        try:
            src.rename(dest)
            _rebase_paths(src, dest)
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
    _rebase_paths(src, dest)
    progress("Removing the old folder ...")
    try:
        _remove(src)
    except OSError as exc:
        return dest, [f"The session was copied to {dest}, but the old folder {src} could not be removed completely "
                      f"({exc}). It still holds participant data: delete it yourself once you have checked the new "
                      "folder."]
    return dest, []


def _rebase_paths(src: Path, dest: Path) -> None:
    """Point paths inside the old session folder (camera folders or videos kept in it) to the new place."""
    session = load_session(dest)
    old = Path(src).resolve()

    def moved(path: str) -> str:
        try:
            return str(dest / Path(path).resolve().relative_to(old)) if path else path
        except ValueError:
            return path  # outside the session folder: unchanged

    for cam in session.cameras:
        cam.source_dir = moved(cam.source_dir)
        for ref in (cam.intrinsic, cam.extrinsic, *cam.positions, *cam.lenses):
            if ref is not None:
                ref.file = moved(ref.file)
        cam.trial_files = {label: moved(f) for label, f in cam.trial_files.items()}
    save_session(session, dest)


def set_trial_participants(session: Session, table: dict[str, str]) -> None:
    """Which participant each trial belongs to (trial label -> participant label), from the Trials step."""
    ensure_not_frozen(session)
    known = {p.label for p in session.active_participants}
    for trial_label, person in table.items():
        trial = session.trial(trial_label)
        if person not in known:
            raise SetupError(f"There is no participant {person} for trial {trial_label}.")
        if trial.participant != person and session.run_state.stage_done.get(trial_label):
            raise SetupError(f"Trial {trial_label} was already processed for participant {trial.participant}; "
                             "it cannot be moved to another participant.")
    moved = [lbl for lbl, person in table.items() if session.trial(lbl).participant != person]
    for trial_label, person in table.items():
        session.trial(trial_label).participant = person
    session.link()
    for lbl in moved:  # another person: their details, copied from their earlier trial
        copy_details(session, session.trial(lbl))


def ensure_not_frozen(session: Session) -> None:
    """After any raw-footage deletion the session's videos are fixed: no re-validation, re-mapping or re-copying."""
    if session.frozen:
        gone = ", ".join(f"trial {t.label} ({t.code})" for t in session.trials if not t.raw_present)
        raise SetupError(f"Raw footage was deleted in this session ({gone}), so its videos can no longer be checked, "
                         "changed or prepared again. Start a new session for new recordings.")


def pick_video(session: Session, camera: str, role: str, file: Path, root: Path | None = None) -> None:
    """D3: choose a calibration video from any folder. With `root`, results computed from the old video are
    removed (a new intrinsic video also means a new camera position)."""
    ensure_not_frozen(session)
    path = Path(file).resolve()
    if not path.is_file():
        raise SetupError(f"{path} does not exist.")
    cls = CalibVideo if role == "intrinsic" else VideoRef
    cam = session.camera(camera)
    old = getattr(cam, role)
    setattr(cam, role, cls(file=str(path), origin="manual"))
    if root is not None and (old is None or Path(old.file).resolve() != path):
        _forget_video(session, root, camera, role, "")
        _mark_stale(session, [t.label for t in session.trials if not cam.video_for(role, t.label)])


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
    # deleted footage is gone on purpose; finished trials no longer need their copies
    trials = [t for t in session.trials if t.raw_present and session.run_state.trial_status.get(t.label) != "done"]
    for cam in session.cameras:
        names = [f"trial_{t.label}.mp4" for t in trials]
        if session.calibration_source == "app":
            names += ["intrinsic.mp4", "extrinsic.mp4"] + [f"extrinsic_{p.key}.mp4" for p in cam.positions] + [
                f"intrinsic_{p.key}.mp4" for p in cam.lenses]
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
