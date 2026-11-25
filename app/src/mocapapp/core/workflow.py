"""Project-level steps shared by the CLI and the GUI (the GUI must not hold logic of its own)."""

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
    CameraLayout,
    DetailField,
    Layout,
    LayoutCamera,
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
    cameras, lay_cams = [], []
    for i, d in enumerate(spec.camera_dirs, 1):
        name = f"cam{i:02d}"
        intr, extr = spec.picks.get((name, "intrinsic")), spec.picks.get((name, "extrinsic"))
        cameras.append(Camera(
            id=name, source_dir=str(Path(d).resolve()),
            intrinsic=CalibVideo(file=str(Path(intr).resolve()), origin="manual") if intr else None))
        lay_cams.append(LayoutCamera(
            camera=name, extrinsic=VideoRef(file=str(Path(extr).resolve()), origin="manual") if extr else None))
    lay = CameraLayout(id="L1", name="Layout 1", cameras=lay_cams)
    session = Session(study_code=study, n_trials=spec.n_trials, cameras=cameras, layouts=[lay],
                      trials_per_participant=k,
                      participants=[Participant(label=str(i + 1), code=c, height_m=h)
                                    for i, (c, h) in enumerate(zip(codes, heights, strict=True))],
                      trials=[Trial(label=str(i + 1), participant=str(i // k + 1), layout="L1")
                              for i in range(spec.n_trials)])
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
    """A participant added later: a new pseudonym and trials_per_participant trials (with the last trial's
    layout, or the newest layout)."""
    layout = session.trials[-1].layout if session.trials else (session.layouts[-1].id if session.layouts else "")
    taken = existing_codes(Path(root).parent) | {p.code for p in session.participants}
    person = Participant(label=_next_label(p.label for p in session.participants),
                         code=make_code(session.study_code, taken), height_m=parse_height(height))
    session.participants.append(person)
    for _ in range(session.trials_per_participant):
        session.trials.append(Trial(label=_next_label(t.label for t in session.trials), participant=person.label,
                                    layout=layout))
    session.link()
    save_session(session, root)
    return person


def remove_participant(session: Session, root: Path, label: str) -> None:
    """Remove a participant who has no data in the project yet (for example, one who did not come), with their
    trials. A project may have no participants (§16: it starts empty)."""
    person = session.participant(label)
    layout = Layout(root)
    for t in session.trials_of(label):
        if layout.trial(t.label).exists() or t.label in session.run_state.trial_status:
            raise SetupError(f"Participant {label} ({person.code}) already has videos or results in this project. "
                             "Use 'Participant data and deletion' (File menu, Participants or Results) to delete "
                             "their data instead.")
    gone = {t.label for t in session.trials_of(label)}
    session.trials = [t for t in session.trials if t.label not in gone]
    session.participants = [p for p in session.participants if p.label != label]
    for k in gone:
        shutil.rmtree(layout.trial_input(k, "x").parent, ignore_errors=True)
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

def add_trial(session: Session, root: Path, participant: str | None = None, layout: str | None = None,
              like: str | None = None, name: str = "", date: str | None = None) -> Trial:
    """Another trial (R10). participant None: a new participant with a random pseudonym; 'previous': the default
    of R10 (the previous trial's participant while they have fewer trials than 'trials per participant'). like: a
    trial to copy the layout (and, with participant 'previous', the participant) from; default the last trial.
    Its details are copied from the participant's earlier trial; its videos are chosen afterwards."""
    model = session.trial(like) if like else (session.trials[-1] if session.trials else None)
    if participant == "previous":
        participant = default_participant(session, model.label if model else None)
    if participant is None:
        taken = existing_codes(Path(root).parent) | {p.code for p in session.participants}
        person = Participant(label=_next_label(p.label for p in session.participants),
                             code=make_code(session.study_code, taken))
        session.participants.append(person)
        participant = person.label
    person = session.participant(participant)
    if person.withdrawn_at:
        raise SetupError(f"Participant {participant} withdrew; no trials can be added for them.")
    if layout is None:
        layout = model.layout if model else (session.layouts[0].id if session.layouts else "")
    if layout:
        session.layout(layout)
    trial = Trial(label=_next_label(t.label for t in session.trials), participant=participant,
                  mapping_method="manual", layout=layout, name=name.strip(), date=check_date(date) or today(),
                  date_manual=bool(date))
    session.trials.append(trial)
    session.link()
    copy_details(session, trial)
    save_session(session, root)
    return trial


def duplicate_trial(session: Session, root: Path, label: str) -> Trial:
    """R9: a new trial like this one: same participant, layout, name and details; its own videos."""
    src = session.trial(label)
    trial = add_trial(session, root, participant=src.participant, layout=src.layout, like=label, name=src.name,
                      date=src.date)
    trial.details = dict(src.details)
    save_session(session, root)
    return trial


def trial_has_data(session: Session, root: Path, label: str) -> bool:
    layout = Layout(root)
    folder = layout.trial_input(label, "x").parent
    return (layout.trial(label).exists() or label in session.run_state.trial_status
            or (folder.is_dir() and any(folder.iterdir())))


def drop_trial(session: Session, root: Path, label: str) -> None:
    """Forget a trial in session.json (its files are handled by the caller)."""
    rs = session.run_state
    for table in (rs.stage_done, rs.trial_status, rs.sync, rs.errors, rs.stage_settings, rs.keypoints_removed):
        table.pop(label, None)
    shutil.rmtree(Layout(root).trial_input(label, "x").parent, ignore_errors=True)
    Layout(root).trial_calib(label).unlink(missing_ok=True)
    session.trials = [t for t in session.trials if t.label != label]
    session.link()
    save_session(session, root)


def remove_empty_trial(session: Session, root: Path, label: str) -> None:
    """Remove a trial that has no videos or results yet (for example added by mistake). A project may have none;
    its participant stays (participants have their own section since 0.2.0)."""
    session.trial(label)
    if trial_has_data(session, root, label):
        raise SetupError(f"Trial {label} already has videos or results. Delete it with its data instead "
                         "(you will be asked to type the pseudonym).")
    drop_trial(session, root, label)


def drop_unused_participants(session: Session) -> list[str]:
    """Participants exist with their trials (0.1.0rc17): one left without any trial is forgotten. Returns their
    labels."""
    used = {t.participant for t in session.trials}
    gone = [p.label for p in session.participants if p.label not in used and not p.withdrawn_at]
    session.participants = [p for p in session.participants if p.label not in gone]
    return gone


def assign_trial(session: Session, root: Path, label: str, participant: str) -> None:
    """A trial belongs to another (existing) participant. The first one stays, also without trials."""
    ensure_open(session, label)
    set_trial_participants(session, {label: participant})
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


# ---------- project pieces: cameras, layouts, trial layouts (PROJECT.md §16) ----------

def _refresh_calibration(session: Session, root: Path, labels: list[str] | None = None) -> None:
    """Rewrite the calibration files of trials whose layout is ready (stale copies were removed already)."""
    from mocapapp.core.calibration import service

    if session.calibration_source != "app":
        return
    service.write_calibration(session, root, None)
    if labels:
        _mark_stale(session, labels)


def _mark_stale(session: Session, labels) -> None:
    from mocapapp.core.runner import stale_stages

    rs = session.run_state
    for label in labels:
        if rs.trial_status.get(label) == "done" and stale_stages(session, label):
            rs.trial_status[label] = "partial"


def _video_file(file: Path) -> Path:
    path = Path(file).resolve()
    if not path.is_file():
        raise SetupError(f"{path} does not exist.")
    return path


def _app_calibration(session: Session) -> None:
    if session.calibration_source != "app":
        raise SetupError("This project uses an imported calibration (sample data); its calibration cannot change.")


CAMERA_FIELDS = ("name", "source_dir", "resolution", "zoom", "focus", "stabilisation", "fps", "notes")


def add_camera(session: Session, root: Path | None, intrinsic: Path | None = None, **fields) -> Camera:
    """A new camera (R5): id and name camNN; optional organisation fields; its intrinsic video if known."""
    unknown = set(fields) - set(CAMERA_FIELDS)
    if unknown:
        raise SetupError(f"Unknown camera field(s): {', '.join(sorted(unknown))}.")
    cam = Camera(id=session.new_id("cam"), **{k: str(v) for k, v in fields.items() if v is not None})
    if cam.source_dir:
        cam.source_dir = str(Path(cam.source_dir).resolve())
    if intrinsic is not None:
        cam.intrinsic = CalibVideo(file=str(_video_file(intrinsic)), origin="manual")
    session.cameras.append(cam)
    if root is not None:
        save_session(session, root)
    return cam


def edit_camera(session: Session, root: Path | None, camera: str, **fields) -> Camera:
    """Name, folder and the optional fields of a camera (its intrinsic video: set_intrinsic_video)."""
    cam = session.camera(camera)
    for key, value in fields.items():
        if key not in CAMERA_FIELDS:
            raise SetupError(f"Unknown camera field: {key}.")
        value = "" if value is None else str(value).strip()
        if key == "name" and not value:
            value = cam.id
        if key == "source_dir" and value:
            value = str(Path(value).resolve())
        setattr(cam, key, value)
    if root is not None:
        save_session(session, root)
    return cam


def duplicate_camera(session: Session, root: Path | None, camera: str) -> Camera:
    """A copy of a camera, with its intrinsic video and lens calibration (R5/R6: to recalibrate a camera already
    used, duplicate it and give the copy another intrinsic video)."""
    from mocapapp.core.calibration import intrinsics

    src = session.camera(camera)
    cam = src.model_copy(deep=True)
    cam.id = session.new_id("cam")
    cam.name = f"{src.name} (copy)"
    session.cameras.append(cam)
    if root is not None:
        layout = Layout(root)
        old = intrinsics.result_path(layout.cameras_work, src.id)
        if old.is_file():
            data = json.loads(old.read_text(encoding="utf-8"))
            data["camera"] = cam.id
            atomic_write_text(intrinsics.result_path(layout.cameras_work, cam.id), json.dumps(data, indent=2) + "\n")
        if layout.camera_input(src.id).is_file():
            layout.camera_input(cam.id).parent.mkdir(parents=True, exist_ok=True)
            for a, b in ((layout.camera_input(src.id), layout.camera_input(cam.id)),):
                shutil.copy2(a, b)
                stamp = a.with_name(a.name + ".source")
                if stamp.is_file():
                    shutil.copy2(stamp, b.with_name(b.name + ".source"))
        save_session(session, root)
    return cam


def delete_camera(session: Session, root: Path | None, camera: str) -> None:
    cam = session.camera(camera)
    users = [lay.name for lay in session.layouts_with_camera(camera)]
    if users:
        raise SetupError(f"{cam.name} is part of {', '.join(users)}. Remove it from the layout(s) first.")
    session.cameras = [c for c in session.cameras if c.id != camera]
    if root is not None:
        from mocapapp.core.calibration import intrinsics

        layout = Layout(root)
        _drop_input(layout.camera_input(camera))
        intrinsics.result_path(layout.cameras_work, camera).unlink(missing_ok=True)
        save_session(session, root)


def set_intrinsic_video(session: Session, root: Path | None, camera: str, file: Path) -> None:
    """D3: a camera's intrinsic video, from any folder. Not for a camera already used (R6)."""
    from mocapapp.core.calibration import intrinsics, service

    _app_calibration(session)
    service.ensure_recalibratable(session, camera)
    path = _video_file(file)
    cam = session.camera(camera)
    old = cam.intrinsic
    cam.intrinsic = CalibVideo(file=str(path), origin="manual")
    if root is not None and (old is None or Path(old.file).resolve() != path):
        layout = Layout(root)
        _drop_input(layout.camera_input(camera))
        intrinsics.result_path(layout.cameras_work, camera).unlink(missing_ok=True)
        for lay in session.layouts_with_camera(camera):  # positions are solved again once the lens is calibrated
            service._invalidate_trials(session, root, [t.label for t in session.trials_with_layout(lay.id)])
    if root is not None:
        save_session(session, root)


def add_layout(session: Session, root: Path | None, name: str, cameras: list[str], preset: str = "",
               date: str | None = None) -> CameraLayout:
    """A new layout (R7): its cameras in calibration order, reference points copied from a preset."""
    for c in cameras:
        session.camera(c)
    if len(set(cameras)) != len(cameras):
        raise SetupError("A camera can only be once in a layout.")
    lay = CameraLayout(id=session.new_id("L"), name=name.strip() or f"Layout {len(session.layouts) + 1}",
                       date=check_date(date) or today(), date_manual=bool(date),
                       cameras=[LayoutCamera(camera=c) for c in cameras])
    if preset:
        lay.points, lay.preset = dict(session.preset(preset).points), preset
    session.layouts.append(lay)
    if root is not None:
        save_session(session, root)
    return lay


def duplicate_layout(session: Session, root: Path | None, layout: str) -> CameraLayout:
    """Same cameras and reference points, without extrinsic videos (the cameras were set up again)."""
    src = session.layout(layout)
    lay = add_layout(session, None, f"{src.name} (copy)", src.camera_ids())
    if src.points:
        lay.points, lay.preset = dict(src.points), src.preset
    if root is not None:
        save_session(session, root)
    return lay


def _started(session: Session, layout: str) -> list[str]:
    return [t.label for t in session.trials_with_layout(layout) if session.run_state.stage_done.get(t.label)]


def edit_layout(session: Session, root: Path | None, layout: str, name: str | None = None,
                date: str | None = None, cameras: list[str] | None = None) -> CameraLayout:
    lay = session.layout(layout)
    if name is not None:
        lay.name = name.strip() or lay.name
    if date is not None:
        lay.date = check_date(date) if date else None
        lay.date_manual = bool(date)
    if cameras is not None and cameras != lay.camera_ids():
        started = _started(session, layout)
        if started:
            raise SetupError(f"Trial(s) {', '.join(started)} already ran with {lay.name}; its cameras cannot change. "
                             "Duplicate the layout instead.")
        for c in cameras:
            session.camera(c)
        if len(set(cameras)) != len(cameras):
            raise SetupError("A camera can only be once in a layout.")
        keep = {lc.camera: lc for lc in lay.cameras}
        lay.cameras = [keep.get(c) or LayoutCamera(camera=c) for c in cameras]
        if root is not None:
            from mocapapp.core.calibration import service

            service._invalidate_trials(session, root, [t.label for t in session.trials_with_layout(layout)])
    if root is not None:
        save_session(session, root)
    return lay


def delete_layout(session: Session, root: Path | None, layout: str) -> None:
    lay = session.layout(layout)
    users = [t.label for t in session.trials_with_layout(layout)]
    if users:
        raise SetupError(f"Trial(s) {', '.join(users)} use {lay.name}. Give them another layout first.")
    session.layouts = [x for x in session.layouts if x.id != layout]
    if root is not None:
        shutil.rmtree(Layout(root).layout_work(layout), ignore_errors=True)
        shutil.rmtree(Layout(root).layout_input(layout, "x").parent, ignore_errors=True)
        save_session(session, root)


def set_extrinsic_video(session: Session, root: Path | None, layout: str, camera: str, file: Path) -> None:
    """D3: a camera's extrinsic video in a layout, from any folder. Trials that ran with the old one are marked
    out of date."""
    from mocapapp.core.calibration import extrinsics, service

    _app_calibration(session)
    path = _video_file(file)
    lay = session.layout(layout)
    lc = lay.entry(camera)
    old = lc.extrinsic
    lc.extrinsic = VideoRef(file=str(path), origin="manual")
    if not lay.date_manual:  # §16.2: the day the extrinsic videos were recorded, when the file says so
        lay.date = recording_date(path) or lay.date
    if root is not None and (old is None or Path(old.file).resolve() != path):
        rlayout = Layout(root)
        _drop_input(rlayout.layout_input(layout, camera))
        extrinsics.result_path(rlayout.layout_work(layout), camera).unlink(missing_ok=True)
        service.review_image(root, layout, camera).unlink(missing_ok=True)
        lc.result = None
        labels = [t.label for t in session.trials_with_layout(layout)]
        service._invalidate_trials(session, root, labels)
        _mark_stale(session, labels)
    if root is not None:
        save_session(session, root)


def today() -> str:
    from datetime import date

    return date.today().isoformat()


def recording_date(file: Path) -> str | None:
    """The local day a video was recorded, from its metadata (None if it does not say)."""
    from mocapapp.core import media

    try:
        when = media.recording_time(media.probe(Path(file)))
    except Exception:  # unreadable here; the video check reports it
        return None
    return when.astimezone().date().isoformat() if when else None


def check_date(value: str | None) -> str | None:
    """YYYY-MM-DD, or None."""
    from datetime import date

    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        raise SetupError(f"Date '{value}' is not a date like 2026-11-25.") from None


def set_trial_layout(session: Session, root: Path | None, label: str, layout: str) -> None:
    """The layout a trial was recorded with (R7). Its videos for cameras not in the new layout are forgotten."""
    trial = ensure_open(session, label)
    lay = session.layout(layout)
    if trial.layout == layout:
        return
    if session.run_state.stage_done.get(label):
        raise SetupError(f"Trial {label} already ran with another layout. Remove its results first, or add a new "
                         "trial for it.")
    trial.layout = layout
    trial.layout_override = False
    trial.videos = {c: f for c, f in trial.videos.items() if c in lay.camera_ids()}
    if root is not None:
        from mocapapp.core.calibration import service

        service._invalidate_trials(session, root, [label])
        save_session(session, root)


def accept_layout(session: Session, root: Path | None, label: str, accept: bool = True) -> None:
    """R8: use the layout although it was recorded on another day (recorded with the trial)."""
    session.trial(label).layout_override = accept
    if root is not None:
        save_session(session, root)


def set_trial_info(session: Session, root: Path | None, label: str, name: str | None = None,
                   date: str | None = None) -> None:
    trial = session.trial(label)
    if name is not None:
        trial.name = name.strip()
    if date is not None:
        trial.date = check_date(date)
        trial.date_manual = bool(date)
    if root is not None:
        save_session(session, root)


def default_participant(session: Session, previous: str | None = None) -> str | None:
    """R10: the previous trial's participant while they have fewer trials than 'trials per participant';
    otherwise the first participant without trials yet (added in Participants beforehand); otherwise None (a new
    participant)."""
    if previous is None and session.trials:
        previous = session.trials[-1].label
    if previous is not None:
        person = session.participant(session.trial(previous).participant)
        if not person.withdrawn_at and len(session.trials_of(person.label)) < session.trials_per_participant:
            return person.label
    return next((p.label for p in session.active_participants if not session.trials_of(p.label)), None)


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
        if cam.intrinsic is not None:
            cam.intrinsic.file = moved(cam.intrinsic.file)
    for lay in session.layouts:
        for lc in lay.cameras:
            if lc.extrinsic is not None:
                lc.extrinsic.file = moved(lc.extrinsic.file)
    for t in session.trials:
        t.videos = {c: moved(f) for c, f in t.videos.items()}
    save_session(session, dest)


def set_trial_participants(session: Session, table: dict[str, str]) -> None:
    """Which participant each trial belongs to (trial label -> participant label)."""
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


def ensure_open(session: Session, label: str) -> Trial:
    """R20: a trial whose raw footage was deleted keeps its results, but its videos and layout cannot change."""
    trial = session.trial(label)
    if trial.frozen:
        why = ("the participant withdrew" if trial.withdrawn_at
               else f"its raw footage was deleted on {trial.raw_deleted_at}")
        raise SetupError(f"Trial {label} ({trial.code}): {why}, so its videos and layout can no longer change. Add a "
                         "new trial for new recordings.")
    return trial


def pick_video(session: Session, camera: str, role: str, file: Path, root: Path | None = None,
               layout: str | None = None) -> None:
    """D3: choose a calibration video from any folder: a camera's intrinsic video, or its extrinsic video in a
    layout (default: the only layout with this camera)."""
    if role == "intrinsic":
        set_intrinsic_video(session, root, camera, file)
        return
    if role != "extrinsic":
        raise SetupError("A calibration video is 'intrinsic' or 'extrinsic'.")
    if layout is None:
        layouts = session.layouts_with_camera(camera)
        if len(layouts) != 1:
            raise SetupError(f"{camera} is in {len(layouts)} layouts: say which layout this extrinsic video is for.")
        layout = layouts[0].id
    set_extrinsic_video(session, root, layout, camera, file)


def _drop_input(path: Path) -> None:
    """Remove a prepared copy (and its source stamp) so it is prepared again from the new source."""
    path.unlink(missing_ok=True)
    path.with_name(path.name + ".source").unlink(missing_ok=True)


def set_mapping(session: Session, table: dict[tuple[str, str], Path], root: Path | None = None) -> None:
    """Trial videos chosen by hand: (camera id, trial label) -> file. With `root`, copies of files that changed are
    removed from inputs/ and the trial folder, so they are prepared again."""
    for (cam, label), file in table.items():
        trial = ensure_open(session, label)
        lay = session.trial_layout(label)
        if lay is None or cam not in lay.camera_ids():
            raise SetupError(f"Trial {label}: camera {cam} is not in its layout.")
        if not Path(file).is_file():
            raise SetupError(f"{file} does not exist.")
        new = str(Path(file).resolve())
        old = trial.videos.get(cam)
        trial.videos[cam] = new
        if not trial.date_manual:  # §16.2: the day the trial was recorded, when the file says so
            trial.date = recording_date(Path(new)) or trial.date
        if root is not None and (old is None or Path(old).resolve() != Path(new)):
            _drop_input(Layout(root).trial_input(label, cam))
            (Layout(root).trial(label) / "videos" / f"{lay.slot(cam)}.mp4").unlink(missing_ok=True)
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
                issues.append(Issue("error", cam, f"Calibration: {state}.", "Finish the cameras and layouts."))
    elif not Layout(root).calib_file.is_file():
        issues.append(Issue("error", "calibration", "The imported calibration file is missing.", ""))
    return issues + runner.preflight(session, root, [t.label for t in session.active_trials])


def inputs_ready(session: Session, root: Path) -> bool:
    """Every video has been copied/normalised into inputs/ and the trial folders exist."""

    # deleted footage is gone on purpose; finished trials no longer need their copies
    trials = [t for t in session.trials if t.raw_present and session.run_state.trial_status.get(t.label) != "done"]
    return all(trial_inputs_ready(session, root, t.label) for t in trials)


def trial_inputs_ready(session: Session, root: Path, label: str) -> bool:
    """A trial's videos (and its layout's calibration videos) are copied in and its folder is assembled."""
    layout = Layout(root)
    cams = session.trial_cameras(label)
    if not cams:
        return False
    if session.calibration_source == "app":
        for _slot, cam, _lc in cams:
            if not layout.camera_input(cam.id).is_file():
                return False
            if not layout.layout_input(session.trial(label).layout, cam.id).is_file():
                return False
    return ((layout.trial(label) / "Config.toml").is_file()
            and all(layout.trial_input(label, cam.id).is_file() for _s, cam, _lc in cams)
            and all((layout.trial(label) / "videos" / f"{slot}.mp4").is_file() for slot, _c, _lc in cams))


def prepare_session(session: Session, root: Path, progress=print, dry_run: bool = False):
    """Validate, then copy/convert every video into inputs/ and build the trial folders.
    Returns (report, plan); the plan is None when validation failed."""
    from mocapapp.core.assemble import assemble_trials, plan_inputs, prepare_inputs
    from mocapapp.core.validate import validate

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


# ---------- projects (PROJECT.md §16): empty project, consent, participant values, per-trial checks ----------

def create_project(out_root: Path, study: str = "", trials_per_participant: int = 1) -> tuple[Path, Session]:
    """An empty project folder: cameras, layouts, participants and trials are added afterwards."""
    issues = [i for i in check_output_root(Path(out_root)) if i.level == "error"]
    if issues:
        raise SetupError("\n".join(str(i) for i in issues))
    if trials_per_participant < 1:
        raise SetupError("Trials per participant must be 1 or more.")
    study = normalise_study(study)
    root = Path(out_root) / session_dir_name(study)
    if root.exists():
        raise SetupError(f"{root} already exists; wait a minute or choose another output folder.")
    session = Session(study_code=study, n_trials=0, trials_per_participant=trials_per_participant)
    fill_versions(session)
    save_session(session, root)
    return root, session


CONSENT_STATES = {"not-signed": "Not signed yet", "delete": "Signed, delete footage",
                  "keep": "Signed, keep footage"}


def consent_state(person: Participant) -> str:
    """R11: one drop-down for the three consent facts."""
    if not person.consent.attested:
        return "not-signed"
    return "keep" if person.consent.raw_footage_storage else "delete"


def set_consent(session: Session, root: Path | None, participant: str, state: str) -> Participant:
    """R11: Not signed yet / Signed, delete footage / Signed, keep footage (same rules as the three 0.1.x columns:
    deletion after the run follows the storage consent, §6.2)."""
    from datetime import datetime

    if state not in CONSENT_STATES:
        raise SetupError(f"Consent is one of: {', '.join(CONSENT_STATES)}.")
    person = session.participant(participant)
    if person.withdrawn_at:
        raise SetupError(f"Participant {participant} ({person.code}) withdrew; their consent cannot change.")
    c = person.consent
    if state == "not-signed":
        c.attested, c.attested_at, c.raw_footage_storage = False, None, False
    else:
        if not c.attested:
            c.attested_at = datetime.now().isoformat(timespec="seconds")
        c.attested, c.raw_footage_storage = True, state == "keep"
    person.delete_raw = session.settings.delete_raw_all or not c.raw_footage_storage
    if root is not None:
        save_session(session, root)
    return person


def set_participant_value(session: Session, root: Path | None, participant: str, key: str, value) -> str:
    """A value in the Participants table: height on the participant, other details written to all of their
    trials (R12: stored per trial; no separate per-trial editor)."""
    person = session.participant(participant)
    if key == "height":
        person.height_m = parse_height(value)
        for t in session.trials_of(person.label):
            t.details.pop("height", None)
        shown = format_height(person.height_m) if person.height_m else ""
    else:
        trials = session.trials_of(person.label)
        if not trials:
            raise SetupError(f"Participant {participant} has no trials to store '{key}' with.")
        shown = ""
        for t in trials:
            shown = set_trial_detail(session, None, t.label, key, value)
    if root is not None:
        save_session(session, root)
    return shown


def check_trials(session: Session, root: Path, labels: list[str], progress=print, dry_run: bool = False):
    """Check and copy the videos of these trials only, and build their folders: one trial never waits for the
    others (§16.4). Returns (report, plan); the plan is None when the check found blocking problems."""
    from mocapapp.core.assemble import assemble_trials, plan_inputs, prepare_inputs
    from mocapapp.core.validate import validate

    for label in labels:
        ensure_open(session, label)
    scoped = session.model_copy(deep=True)
    scoped.trials = [t for t in scoped.trials if t.label in set(labels)]
    scoped.link()
    report = validate(scoped, root)
    # what the check chose (calibration videos found by name, trial videos) goes back into the project
    for cam in scoped.cameras:
        session.camera(cam.id).intrinsic = cam.intrinsic
    for lay in scoped.layouts:
        for lc in lay.cameras:
            session.layout(lay.id).entry(lc.camera).extrinsic = lc.extrinsic
    for t in scoped.trials:
        real = session.trial(t.label)
        real.videos, real.mapping_method = dict(t.videos), t.mapping_method
    save_session(session, root)
    if not report.ok:
        return report, None
    plan = plan_inputs(scoped, root, report, labels)
    if not dry_run:
        prepare_inputs(plan, report, progress)
        assemble_trials(session, root, progress, labels=labels)
        save_session(session, root)
    return report, plan


def check_each_trial(session: Session, root: Path, labels: list[str], progress=print) -> dict[str, tuple]:
    """Check and copy the videos of each trial on its own: a trial with a problem never holds the others back
    (§16.4). Per label: (report, plan, error); plan None = not copied (see the report or the error)."""
    out = {}
    for label in labels:
        progress(f"Trial {label}: checking and copying its videos ...")
        try:
            report, plan = check_trials(session, root, [label], progress)
            out[label] = (report, plan, "")
        except (SetupError, KeyError, OSError) as exc:  # a frozen trial, a full disk, a file that went away
            out[label] = (None, None, str(exc).strip("'\""))
    return out


def prepare_calibration(session: Session, root: Path, cameras: list[str] | None = None,
                        layout: str | None = None, progress=print) -> int:
    """Copy (normalise) calibration videos into the project before calibrating: these cameras' intrinsic videos
    and/or one layout's extrinsic videos. Calibration videos keep their frame rate. Returns how many were copied."""
    from mocapapp.core import media
    from mocapapp.core.assemble import DISK_MARGIN, Plan, prepare_inputs
    from mocapapp.core.validate import Report

    rlayout = Layout(root)
    sources = []
    for cid in cameras or []:
        cam = session.camera(cid)
        if cam.intrinsic is None:
            raise SetupError(f"{cam.name} has no intrinsic video yet.")
        sources.append((Path(cam.intrinsic.file), rlayout.camera_input(cid)))
    if layout is not None:
        lay = session.layout(layout)
        for lc in lay.cameras:
            if lc.extrinsic is not None:
                sources.append((Path(lc.extrinsic.file), rlayout.layout_input(layout, lc.camera)))
    plan, report = Plan(), Report()
    root.mkdir(parents=True, exist_ok=True)
    plan.bytes_free = shutil.disk_usage(root).free
    for src, dst in sources:
        if not src.is_file():
            raise SetupError(f"{src} does not exist.")
        try:
            info = media.probe(src)
        except media.MediaError as exc:
            raise SetupError(str(exc)) from None
        report.probes[str(src)] = info
        how = "transcode" if media.needs_transcode(info) else "link"
        if how == "transcode" or src.resolve().anchor.lower() != Path(root).resolve().anchor.lower():
            plan.bytes_needed += src.stat().st_size
        plan.jobs.append((src, dst, how))
    plan.bytes_needed = int(plan.bytes_needed * DISK_MARGIN)
    done = prepare_inputs(plan, report, progress)
    return sum(1 for how in done.values() if how != "existing")


# ---------- Session settings and calibration from the GUI (PROJECT.md §16.3) ----------

SETTING_FIELDS = {"pose_mode", "keypoints", "cutoff_hz", "use_augmentation", "simple_model", "save_overlay",
                  "keep_keypoints"}


def set_settings(session: Session, root: Path | None, **values) -> list[str]:
    """Processing settings of the project. Finished trials whose results depend on a changed setting become out
    of date (a new run repeats only those stages). Returns the labels that became out of date."""
    from mocapapp.core import runner

    unknown = set(values) - SETTING_FIELDS
    if unknown:
        raise SetupError(f"Unknown setting(s): {', '.join(sorted(unknown))}.")
    st = session.settings
    for key, value in values.items():
        if key == "cutoff_hz":
            if not 1 <= float(value) <= 30:
                raise SetupError("The smoothing cut-off is between 1 and 30 Hz.")
            st.filter.cutoff_hz = float(value)
        else:
            setattr(st, key, value)
    rs, stale = session.run_state, []
    for t in session.active_trials:  # results made with other settings are no longer final
        if rs.trial_status.get(t.label) == "done" and runner.stale_stages(session, t.label):
            rs.trial_status[t.label] = "partial"
            stale.append(t.label)
    if root is not None:
        save_session(session, root)
    return stale


def set_project_info(session: Session, root: Path | None, study_code: str | None = None,
                     trials_per_participant: int | None = None, notes: str | None = None) -> None:
    """Study code (used in new pseudonyms only: existing ones never change), trials per participant (the default
    of the add-trial row, R10) and notes."""
    if study_code is not None:
        session.study_code = normalise_study(study_code)
    if trials_per_participant is not None:
        if trials_per_participant < 1:
            raise SetupError("Trials per participant must be 1 or more.")
        session.trials_per_participant = trials_per_participant
    if notes is not None:
        session.notes = notes
    if root is not None:
        save_session(session, root)


DELETE_WHEN = {"after_processing": "after the trial is processed", "after_export": "after the results are exported"}


def set_delete_raw_all(session: Session, root: Path | None, on: bool, when: str | None = None) -> None:
    """R13: delete the raw footage of every participant, even of those who agreed to its storage, once their trial is
    processed or once its results are exported (`when`). Off again: each participant's consent decides (§6.2) and
    the deletion waits for the export."""
    if when is not None:
        if when not in DELETE_WHEN:
            raise SetupError(f"When is one of: {', '.join(DELETE_WHEN)}.")
        session.settings.delete_raw_when = when
    session.settings.delete_raw_all = on
    for person in session.participants:
        person.delete_raw = on or not person.consent.raw_footage_storage
    if root is not None:
        save_session(session, root)


def set_board(session: Session, root: Path | None, board_type: str, cols: int, rows: int, square_mm: float) -> None:
    """The printed calibration board (squares across and down). Lens results computed with another board stay
    until the cameras are calibrated again."""
    if board_type not in ("charuco", "chess"):
        raise SetupError("The board is 'charuco' or 'chess'.")
    if not (3 <= cols <= 30 and 3 <= rows <= 30) or not 5 <= square_mm <= 200:
        raise SetupError("A board has 3 to 30 squares each way and squares of 5 to 200 mm.")
    st = session.settings
    st.board_type, st.corners_nb, st.square_size_mm = board_type, (rows - 1, cols - 1), float(square_mm)
    if root is not None:
        save_session(session, root)


def calibrate_cameras(session: Session, root: Path, cameras: list[str] | None = None, every_n_sec: float = 1.0,
                      force: bool = False, progress=print) -> list:
    """Copy these cameras' intrinsic videos into the project, compute their lens calibration and write the
    calibration files of trials that became ready. None = every camera with an intrinsic video."""
    from mocapapp.core.calibration import service

    ids = cameras or [c.id for c in session.cameras if c.intrinsic is not None]
    if not ids:
        raise SetupError("No camera has an intrinsic video yet.")
    try:
        prepare_calibration(session, root, ids, progress=progress)
        results = service.run_intrinsics(session, root, ids, every_n_sec, force, progress)
        if session.calibration_source == "app":
            service.write_calibration(session, root)
    finally:
        save_session(session, root)
    return results


def place_camera(session: Session, root: Path, layout: str, camera: str, clicks, frame_index: int = 0,
                 override: bool = False):
    """A camera's position in a layout from its clicked reference points; writes the calibration files of trials
    that became ready."""
    from mocapapp.core.calibration import service

    try:
        result = service.run_extrinsics(session, root, layout, camera, clicks, frame_index, override)
        if session.calibration_source == "app":
            service.write_calibration(session, root)
    finally:
        save_session(session, root)
    return result


def set_layout_points(session: Session, root: Path, layout: str, scene, preset: str = "") -> list[str]:
    """A layout's reference points (from the editor or a preset). Positions are recomputed from the clicks already
    made; returns plain-language notes on them."""
    from mocapapp.core.calibration import service

    try:
        notes = service.set_layout_points(session, root, layout, scene, preset)
        if session.calibration_source == "app":
            service.write_calibration(session, root)
    finally:
        save_session(session, root)
    return notes


def delete_preset(session: Session, root: Path | None, preset: str) -> None:
    """Layouts made from it keep their own copy of the points."""
    session.preset(preset)
    session.presets = [p for p in session.presets if p.id != preset]
    for lay in session.layouts:
        if lay.preset == preset:
            lay.preset = ""
    if root is not None:
        save_session(session, root)
