"""Project model (session.json) and on-disk layout (PROJECT.md §5, §16).

Schema 2 (0.2): a project holds cameras (each with its lens calibration), reference-point presets, camera layouts
(which cameras stood where, with their reference points and extrinsic videos), participants and trials. A trial
uses one layout, and with it every camera of that layout. Older session files are upgraded by `migrate.py`."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

from mocapapp import __version__

SCHEMA = 2
SESSION_FILE = "session.json"
HEIGHT_MIN_M, HEIGHT_MAX_M = 1.00, 2.30


def parse_height(value: str | float | None) -> float | None:
    """Height in metres from metres or centimetres input; None means 'auto' (not recommended).

    Raises ValueError with a plain-language message when out of range.
    """
    if value is None or (isinstance(value, str) and value.strip().lower() in ("", "auto")):
        return None
    try:
        h = float(str(value).strip().replace(",", ".").removesuffix("cm").removesuffix("m").strip())
    except ValueError:
        raise ValueError(f"Height '{value}' is not a number. Enter it in metres (1.78) or centimetres (178).") from None
    if h > 10:  # centimetres
        h = h / 100
    if not HEIGHT_MIN_M <= h <= HEIGHT_MAX_M:
        raise ValueError(f"Height {h:.2f} m is outside {HEIGHT_MIN_M:.2f}-{HEIGHT_MAX_M:.2f} m. Check the value.")
    return round(h, 3)


def format_height(h: float) -> str:
    """A height in metres with two or more decimals: 1.70, 1.705."""
    whole, _, dec = f"{h:.3f}".rstrip("0").partition(".")
    return f"{whole}.{dec.ljust(2, '0')}"


class DetailField(BaseModel):
    """A participant detail asked for every trial (0.1.0rc13): height, weight, handedness, ... or a custom one."""
    key: str  # stable key in Trial.details
    label: str
    kind: Literal["number", "text", "choice"] = "text"
    unit: str = ""
    choices: list[str] = Field(default_factory=list)
    enabled: bool = False
    builtin: bool = False


def _is_v1(data: dict) -> bool:
    """Every saved session.json carries its schema; data built in code without one is old only if it looks old
    (cameras named but without ids, or the session-wide extrinsics of 0.1.x)."""
    if "schema" in data:
        return data["schema"] < SCHEMA
    cams = data.get("cameras") or []
    return "extrinsics" in data or any(isinstance(c, dict) and "id" not in c for c in cams)


def _one_participant_per_trial(data: dict) -> dict:
    """Sessions before 0.1.0rc9 kept pseudonym, height and consent on each trial (one participant per trial)."""
    if data.get("participants"):
        return data
    data = dict(data)
    people, trials = [], []
    for t in data.get("trials", []):
        if not isinstance(t, dict) or "code" not in t:
            trials.append(t)
            continue
        t = dict(t)
        people.append({"label": t["label"], **{k: t.pop(k) for k in PARTICIPANT_FIELDS if k in t}})
        t["participant"] = t["label"]
        trials.append(t)
    data["participants"], data["trials"] = people, trials
    return data


def default_details() -> list[DetailField]:
    return [
        DetailField(key="height", label="Height", kind="number", unit="m", enabled=True, builtin=True),
        DetailField(key="weight", label="Weight", kind="number", unit="kg", builtin=True),
        DetailField(key="handedness", label="Handedness", kind="choice", choices=["right", "left", "both"],
                    builtin=True),
        DetailField(key="age", label="Age", kind="number", unit="years", builtin=True),
        DetailField(key="sex", label="Gender", kind="choice", choices=["female", "male", "other"], builtin=True),
    ]


class Consent(BaseModel):
    attested: bool = False
    attested_at: str | None = None
    raw_footage_storage: bool = False
    template_version: str = "1"


class Participant(BaseModel):
    """One person: pseudonym, height and consent. A participant has one or more trials."""
    label: str  # "1", "2", ... (stable: not renumbered when a participant is removed)
    code: str  # the pseudonym
    height_m: float | None = None  # None = 'auto'
    consent: Consent = Field(default_factory=Consent)
    delete_raw: bool = True  # follows consent: no storage consent -> delete (PROJECT.md §6.2)
    withdrawn_at: str | None = None  # "Delete all participant data": every file of every trial is gone
    former_codes: list[str] = Field(default_factory=list)  # pseudonyms before a change (0.1.0rc16), oldest first

    @field_validator("height_m", mode="before")
    @classmethod
    def _height(cls, v):
        return parse_height(v)


PARTICIPANT_FIELDS = ("code", "height_m", "consent", "delete_raw", "withdrawn_at")


def _delegate(name: str) -> property:
    """A trial reads and writes its participant's pseudonym, height and consent."""
    return property(lambda self: getattr(self.person, name), lambda self, v: setattr(self.person, name, v))


class Trial(BaseModel):
    label: str  # fixed: folders and results are named after it
    participant: str = ""  # Participant.label
    name: str = ""  # free-text name or note (R9)
    date: str | None = None  # YYYY-MM-DD the trial was recorded (compared with its layout's date, R8)
    date_manual: bool = False  # the researcher set the date: videos chosen later do not change it
    layout: str = ""  # CameraLayout.id
    videos: dict[str, str] = Field(default_factory=dict)  # Camera.id -> source video of this trial
    layout_override: bool = False  # the researcher accepted the 'layout from another day' warning (R8)
    mapping_method: Literal["digit", "name", "chronological", "manual"] | None = None
    # PROJECT.md §8.4: set by save_session when raw footage is kept with consent ("study finished")
    retention_until: str | None = None
    raw_deleted_at: str | None = None  # working copies + overlays + 2D keypoints deleted (§8.4)
    details: dict[str, str] = Field(default_factory=dict)  # DetailField.key -> value for this trial (0.1.0rc13)
    _person: Participant | None = PrivateAttr(default=None)

    @property
    def height_m(self) -> float | None:
        """This trial's height if one was entered for it, else the participant's (the pipeline uses this)."""
        own = self.details.get("height")
        return parse_height(own) if own else self.person.height_m

    @height_m.setter
    def height_m(self, value) -> None:
        self.person.height_m = value

    @property
    def person(self) -> Participant:
        if self._person is None:
            raise RuntimeError(f"Trial {self.label} is not linked to a participant (Session.link).")
        return self._person

    code = _delegate("code")
    consent = _delegate("consent")
    delete_raw = _delegate("delete_raw")
    withdrawn_at = _delegate("withdrawn_at")

    @property
    def raw_present(self) -> bool:
        return self.raw_deleted_at is None and self.withdrawn_at is None

    @property
    def frozen(self) -> bool:
        """Raw footage deleted (or the participant withdrew): this trial's videos can no longer change (R20)."""
        return not self.raw_present


class VideoRef(BaseModel):
    file: str
    origin: Literal["found", "manual"] = "found"


class CalibVideo(VideoRef):
    rms_px: float | None = None
    status: Literal["pending", "ok", "warn", "fail"] = "pending"


class Camera(BaseModel):
    """A camera with its lens (intrinsic) calibration, reused by every layout that includes it (R5). Locked once a
    trial using it has started running (R6): to recalibrate, duplicate the camera and calibrate the copy."""
    id: str  # cam01, cam02, ...: files are named after it, it never changes
    name: str = ""  # shown to the researcher; 'camNN' by default
    source_dir: str = ""  # folder holding this camera's videos (used to suggest files)
    resolution: str = ""  # optional, for organisation (R5)
    zoom: str = ""
    focus: str = ""
    stabilisation: str = ""
    fps: str = ""
    notes: str = ""
    intrinsic: CalibVideo | None = None

    @model_validator(mode="after")
    def _named(self):
        if not self.name:
            self.name = self.id
        return self


class LayoutCamera(BaseModel):
    """One camera of a layout: its extrinsic video and, once the points were clicked, the solved position."""
    camera: str  # Camera.id
    extrinsic: VideoRef | None = None
    # summary of the solved position: frame_index, n_points, error_cm, error_px, status, overridden
    result: dict | None = None


class PointsPreset(BaseModel):
    """Named reference points, reusable for new layouts and importable from another project (R3)."""
    id: str  # P1, P2, ...
    name: str
    points: dict  # scene_points.Scene as JSON


class CameraLayout(BaseModel):
    """Where the cameras stood: reference points plus one extrinsic video per camera (R7)."""
    id: str  # L1, L2, ...
    name: str
    date: str | None = None  # YYYY-MM-DD the extrinsic videos were recorded (R8)
    date_manual: bool = False  # the researcher set the date: videos chosen later do not change it
    points: dict | None = None  # scene_points.Scene as JSON (copied from a preset, editable)
    preset: str = ""  # PointsPreset.id the points were copied from
    cameras: list[LayoutCamera] = Field(default_factory=list)  # in calibration order: cam01, cam02, ... in trials
    imported_from: str = ""  # another project's name, when imported

    def camera_ids(self) -> list[str]:
        return [c.camera for c in self.cameras]

    def slot(self, camera: str) -> str:
        """The camera's name inside this layout's trial folders and calibration files: cam01, cam02, ..."""
        return f"cam{self.camera_ids().index(camera) + 1:02d}"

    def entry(self, camera: str) -> LayoutCamera:
        for c in self.cameras:
            if c.camera == camera:
                return c
        raise KeyError(f"Layout {self.name} has no camera {camera}.")


class FilterSettings(BaseModel):
    type: str = "butterworth"
    cutoff_hz: float = 6.0
    order: int = 4


class Settings(BaseModel):
    pose_mode: Literal["lightweight", "balanced", "performance"] = "balanced"
    keypoints: Literal["Body_with_feet", "Whole_body_wrist", "Whole_body"] = "Body_with_feet"  # Pose2Sim pose_model
    device: str = "auto"
    filter: FilterSettings = Field(default_factory=FilterSettings)
    use_augmentation: bool = True
    make_bvh: bool = True  # always made since 0.1.0rc7; kept so that older session files load
    simple_model: bool = False  # OpenSim: Model_Pose2Sim_simple (fast IK, no muscles) instead of the full model
    save_overlay: bool = False  # videos with the detected skeleton drawn on them (0.1.0rc13: off by default, large)
    keep_keypoints: bool = False  # keep the 2D keypoint files of finished trials (they are only needed to re-run)
    board_type: Literal["charuco", "chess"] = "charuco"
    corners_nb: tuple[int, int] = (5, 8)  # internal corners [rows-1, cols-1]
    square_size_mm: float = 25.0
    marker_resolution: int = 4
    # R13: raw footage of every participant is deleted (even with storage consent). When: once the trial is
    # processed (results complete and readable) or once they are also exported. Always offered, never automatic.
    delete_raw_all: bool = False
    delete_raw_when: Literal["after_processing", "after_export"] = "after_export"


class RunState(BaseModel):
    stage_done: dict[str, list[str]] = Field(default_factory=dict)  # trial label -> finished stages
    trial_status: dict[str, str] = Field(default_factory=dict)  # label -> pending|running|needs_sync_fix|failed|done
    sync: dict[str, dict] = Field(default_factory=dict)  # label -> {"offsets", "issues", "warnings", "accepted"}
    errors: dict[str, str] = Field(default_factory=dict)  # label -> plain-language failure cause
    stage_settings: dict[str, dict[str, str]] = Field(default_factory=dict)  # label -> stage -> settings it ran with
    keypoints_removed: dict[str, str] = Field(default_factory=dict)  # label -> when its 2D keypoint files were removed
    last_error: str | None = None


class StudyInfo(BaseModel):
    """What the researcher entered for the consent forms; the same for every participant of the session."""
    study_name: str = ""
    purpose: str = ""
    researchers: list[tuple[str, str]] = Field(default_factory=list)  # (name, contact)
    storage_place: str = ""
    access: str = ""
    usage: str = ""
    retention: str = ""
    risks: str = ""
    compensation: str = ""


class Session(BaseModel):
    schema_: int = Field(SCHEMA, alias="schema")
    app_version: str = __version__
    pose2sim_commit: str | None = None
    opensim_version: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    study_code: str = ""
    notes: str = ""  # the researcher's notes on the project (Session settings)
    n_trials: int
    trials_per_participant: int = 1
    participants: list[Participant] = Field(default_factory=list)
    trials: list[Trial]
    study_info: StudyInfo = Field(default_factory=StudyInfo)
    details: list[DetailField] = Field(default_factory=default_details)  # asked per trial (0.1.0rc13)
    cameras: list[Camera] = Field(default_factory=list)
    presets: list[PointsPreset] = Field(default_factory=list)
    layouts: list[CameraLayout] = Field(default_factory=list)
    settings: Settings = Field(default_factory=Settings)
    run_state: RunState = Field(default_factory=RunState)
    # "app" = computed with the app's calibration steps; "imported" = an existing Calib file (sample data only)
    calibration_source: Literal["app", "imported"] = "app"

    model_config = {"populate_by_name": True}

    @model_validator(mode="before")
    @classmethod
    def _upgrade(cls, data):
        """Older session files: one participant per trial (before 0.1.0rc9), then schema 1 -> 2 (0.2)."""
        if not isinstance(data, dict):
            return data
        data = _one_participant_per_trial(data)
        if _is_v1(data):
            from mocapapp.core.migrate import upgrade_v1

            data = upgrade_v1(data).data
        return data


    @model_validator(mode="after")
    def _linked(self):
        self.link()
        for d in self.details:  # 0.1.0rc17: height is always a column; 'Sex' is shown as 'Gender'
            if d.builtin and d.key == "height":
                d.enabled = True
            if d.builtin and d.key == "sex":
                d.label = "Gender"
        return self

    def link(self) -> None:
        """Connect every trial to its participant; call after changing trials or participants."""
        by_label = {p.label: p for p in self.participants}
        for t in self.trials:
            if t.participant not in by_label:
                raise ValueError(f"Trial {t.label} belongs to participant {t.participant!r}, which does not exist.")
            t._person = by_label[t.participant]
        self.n_trials = len(self.trials)

    def participant(self, label: str) -> Participant:
        for p in self.participants:
            if p.label == label:
                return p
        raise KeyError(f"No participant {label} in this session.")

    def trials_of(self, participant: str) -> list[Trial]:
        return [t for t in self.trials if t.participant == participant]

    @property
    def active_participants(self) -> list[Participant]:
        return [p for p in self.participants if p.withdrawn_at is None]

    def camera(self, camera_id: str) -> Camera:
        for cam in self.cameras:
            if cam.id == camera_id:
                return cam
        raise KeyError(f"No camera {camera_id} in this project.")

    def layout(self, layout_id: str) -> CameraLayout:
        for lay in self.layouts:
            if lay.id == layout_id:
                return lay
        raise KeyError(f"No layout {layout_id} in this project.")

    def preset(self, preset_id: str) -> PointsPreset:
        for p in self.presets:
            if p.id == preset_id:
                return p
        raise KeyError(f"No reference-point preset {preset_id} in this project.")

    def trial_layout(self, label: str) -> CameraLayout | None:
        t = self.trial(label)
        try:
            return self.layout(t.layout) if t.layout else None
        except KeyError:
            return None

    def trial_cameras(self, label: str) -> list[tuple[str, Camera, LayoutCamera]]:
        """(slot name cam01.., camera, its entry in the layout) for every camera a trial uses, in calibration order."""
        lay = self.trial_layout(label)
        if lay is None:
            return []
        return [(lay.slot(lc.camera), self.camera(lc.camera), lc) for lc in lay.cameras]

    def trials_with_layout(self, layout_id: str) -> list[Trial]:
        return [t for t in self.trials if t.layout == layout_id]

    def layouts_with_camera(self, camera_id: str) -> list[CameraLayout]:
        return [lay for lay in self.layouts if camera_id in lay.camera_ids()]

    def camera_used(self, camera_id: str) -> bool:
        """R6: a trial whose layout includes this camera has started running (some stage finished)."""
        done = self.run_state.stage_done
        return any(done.get(t.label) for lay in self.layouts_with_camera(camera_id)
                   for t in self.trials_with_layout(lay.id))

    def layout_warning(self, label: str) -> str:
        """R8: the trial was recorded on another day than its layout (and the researcher did not accept that)."""
        t = self.trial(label)
        lay = self.trial_layout(label)
        if lay is None or t.layout_override or not (lay.date and t.date) or lay.date == t.date:
            return ""
        return (f"Trial {t.label} was recorded on {t.date}, its layout {lay.name} on {lay.date}. Were the cameras set "
                "up again? Then record new extrinsic videos and make a new layout.")

    def new_id(self, kind: str) -> str:
        """The next free id: kind 'cam' -> cam01, cam02...; 'L' -> L1...; 'P' -> P1..."""
        taken = {"cam": {c.id for c in self.cameras}, "L": {lay.id for lay in self.layouts},
                 "P": {p.id for p in self.presets}}[kind]
        n = 1
        while (f"cam{n:02d}" if kind == "cam" else f"{kind}{n}") in taken:
            n += 1
        return f"cam{n:02d}" if kind == "cam" else f"{kind}{n}"

    @property
    def active_trials(self) -> list[Trial]:
        """Trials whose participant has not withdrawn (withdrawn participants keep only their pseudonym and the log)."""
        return [t for t in self.trials if t.withdrawn_at is None]

    @property
    def asked_details(self) -> list[DetailField]:
        return [d for d in self.details if d.enabled]

    def detail(self, key: str) -> DetailField:
        for d in self.details:
            if d.key == key:
                return d
        raise KeyError(f"No participant detail '{key}' in this session.")

    def trial(self, label: str) -> Trial:
        for t in self.trials:
            if t.label == label:
                return t
        raise KeyError(f"No trial labelled {label} in this session.")


# ---------- layout ----------

def session_dir_name(study: str, when: datetime | None = None) -> str:
    stamp = (when or datetime.now()).strftime("%Y%m%d-%H%M")
    return f"{study}_{stamp}" if study else f"session_{stamp}"


class Layout:
    """Paths inside one session folder (PROJECT.md §5.1)."""

    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def session_json(self) -> Path:
        return self.root / SESSION_FILE

    def camera_input(self, camera: str) -> Path:
        """Normalised copy of a camera's intrinsic video."""
        return self.root / "inputs" / "cameras" / camera / "intrinsic.mp4"

    def layout_input(self, layout: str, camera: str) -> Path:
        """Normalised copy of a camera's extrinsic video in a layout."""
        return self.root / "inputs" / "layouts" / layout / f"{camera}.mp4"

    def trial_input(self, label: str, camera: str) -> Path:
        """Normalised copy of a trial's video from one camera (by camera id)."""
        return self.root / "inputs" / "trials" / label / f"{camera}.mp4"

    @property
    def cameras_work(self) -> Path:
        return self.calibration_work / "cameras"  # intrinsics_<camera>.json

    def layout_work(self, layout: str) -> Path:
        return self.calibration_work / "layouts" / layout  # extrinsics_<camera>.json, frames, review images

    @property
    def calibration_work(self) -> Path:
        return self.root / "calibration_work"

    @property
    def calib_file(self) -> Path:
        """Imported calibration (sample data): used by every trial."""
        return self.calibration_work / "Calib_scene.toml"

    def trial_calib(self, label: str) -> Path:
        """A trial's calibration, made from its layout and that layout's cameras."""
        return self.calibration_work / f"Calib_scene_trial_{label}.toml"

    @property
    def trials(self) -> Path:
        return self.root / "trials"  # must never contain a Config.toml (upstream-notes §1)

    def trial(self, label: str) -> Path:
        return self.trials / f"trial_{label}"

    @property
    def qa(self) -> Path:
        return self.root / "qa"

    @property
    def export(self) -> Path:
        return self.root / "export"


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


RETENTION_TEXT = "study finished"
CONSENT_FILE = "consent.json"


def _sync_retention(trial: Trial) -> None:
    """§8.4 item 6: storage consented and deletion declined -> keep until the study is finished."""
    keep = trial.raw_present and not trial.delete_raw and trial.consent.raw_footage_storage
    trial.retention_until = RETENTION_TEXT if keep else None


def consent_record(trial: Trial) -> dict:
    """The per-trial consent.json of PROJECT.md §8.3 (no personal data: the code is a pseudonym)."""
    c = trial.consent
    return {"code": trial.code, "attested": c.attested, "attested_at": c.attested_at,
            "raw_footage_storage": c.raw_footage_storage, "template_version": c.template_version,
            "delete_raw": trial.delete_raw, "retention_until": trial.retention_until}


def save_session(session: Session, root: Path) -> Path:
    """Write session.json and each trial's consent.json (folders of withdrawn trials stay deleted)."""
    layout = Layout(root)
    for t in session.trials:
        _sync_retention(t)
    path = layout.session_json
    atomic_write_text(path, session.model_dump_json(by_alias=True, indent=2) + "\n")
    for t in session.active_trials:
        if not layout.trial(t.label).is_dir():
            continue  # written once the trial folder is assembled (save_session runs again then)
        target = layout.trial(t.label) / CONSENT_FILE
        text = json.dumps(consent_record(t), indent=2) + "\n"
        if not target.is_file() or target.read_text(encoding="utf-8") != text:
            atomic_write_text(target, text)
    return path


def existing_codes(output_root: Path) -> set[str]:
    """Trial codes already used by sessions in this output folder (collision check, no registry needed)."""
    codes: set[str] = set()
    for path in Path(output_root).glob(f"*/{SESSION_FILE}"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        codes.update(p.get("code", "") for p in data.get("participants", []))
        codes.update(t["code"] for t in data.get("trials", []) if "code" in t)  # sessions before 0.1.0rc9
    return codes


def load_session(root: Path) -> Session:
    """Read a project. An older session folder is upgraded first, on disk (migrate.migrate_dir, with a backup)."""
    path = Layout(root).session_json
    if not path.is_file():
        raise FileNotFoundError(f"No {SESSION_FILE} in {root}. Is this a session folder made by mocapapp?")
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and data.get("schema", 1) < SCHEMA:
        from mocapapp.core.migrate import migrate_dir

        migrate_dir(root)
        data = json.loads(path.read_text(encoding="utf-8"))
    return Session.model_validate(data)
