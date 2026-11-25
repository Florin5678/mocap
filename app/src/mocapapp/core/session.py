"""Session model (session.json) and on-disk layout (PROJECT.md §5)."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

from mocapapp import __version__

SCHEMA = 1
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
    label: str
    participant: str = ""  # Participant.label
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


class VideoRef(BaseModel):
    file: str
    origin: Literal["found", "manual"] = "found"


class CalibVideo(VideoRef):
    rms_px: float | None = None
    status: Literal["pending", "ok", "warn", "fail"] = "pending"


class CameraPosition(BaseModel):
    """A further calibration video of a camera, used by the trials listed (0.1.0rc13 for extrinsic videos: the camera
    was moved; 0.1.0rc17 also intrinsic ones). The camera's main video has the key "" and applies to every trial not
    listed in one of its further videos."""
    key: str  # "2", "3", ...
    file: str
    trials: list[str] = Field(default_factory=list)


ROLES = ("intrinsic", "extrinsic")


class Camera(BaseModel):
    name: str  # cam01, cam02, ... (order = calibration order, see upstream-notes §2)
    source_dir: str
    intrinsic: CalibVideo | None = None
    extrinsic: VideoRef | None = None
    trial_files: dict[str, str] = Field(default_factory=dict)  # trial label -> source file
    positions: list[CameraPosition] = Field(default_factory=list)  # further extrinsic videos
    lenses: list[CameraPosition] = Field(default_factory=list)  # further intrinsic videos (0.1.0rc17)

    def extras(self, role: str) -> list[CameraPosition]:
        return self.lenses if role == "intrinsic" else self.positions

    def video_for(self, role: str, trial: str) -> str:
        """The key of the calibration video a trial uses with this camera ("" = the main video)."""
        return next((p.key for p in self.extras(role) if trial in p.trials), "")

    def position_for(self, trial: str) -> str:
        return self.video_for("extrinsic", trial)

    def lens_for(self, trial: str) -> str:
        return self.video_for("intrinsic", trial)

    def extra(self, role: str, key: str) -> CameraPosition:
        for p in self.extras(role):
            if p.key == key:
                return p
        raise KeyError(f"{self.name} has no {role} video {key}.")

    def position(self, key: str) -> CameraPosition:
        return self.extra("extrinsic", key)

    def calib_videos(self, role: str) -> list[tuple[str, str]]:
        """(key, file) of every calibration video for this role, the main one first ("" when it is chosen)."""
        main = getattr(self, role)
        return ([("", main.file)] if main is not None else []) + [(p.key, p.file) for p in self.extras(role)]


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
    n_trials: int
    trials_per_participant: int = 1
    participants: list[Participant] = Field(default_factory=list)
    trials: list[Trial]
    study_info: StudyInfo = Field(default_factory=StudyInfo)
    details: list[DetailField] = Field(default_factory=default_details)  # asked per trial (0.1.0rc13)
    cameras: list[Camera]
    extrinsics: list[dict] = Field(default_factory=list)
    settings: Settings = Field(default_factory=Settings)
    run_state: RunState = Field(default_factory=RunState)
    sync_cue_confirmed: bool = False
    # "app" = computed with the app's calibration steps; "imported" = an existing Calib file (sample data only)
    calibration_source: Literal["app", "imported"] = "app"

    model_config = {"populate_by_name": True}

    @model_validator(mode="before")
    @classmethod
    def _one_participant_per_trial(cls, data):
        """Sessions before 0.1.0rc9 kept pseudonym, height and consent on each trial (one participant per trial)."""
        if not isinstance(data, dict) or data.get("participants"):
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

    def camera(self, name: str) -> Camera:
        for cam in self.cameras:
            if cam.name == name:
                return cam
        raise KeyError(f"No camera named {name} in this session.")

    @property
    def active_trials(self) -> list[Trial]:
        """Trials whose participant has not withdrawn (withdrawn participants keep only their pseudonym and the log)."""
        return [t for t in self.trials if t.withdrawn_at is None]

    @property
    def frozen(self) -> bool:
        """Raw footage was deleted for some trial: the session's videos can no longer be prepared again."""
        return any(not t.raw_present for t in self.trials)

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

    @property
    def scene_json(self) -> Path:
        return self.root / "scene.json"

    def inputs(self, cam: str) -> Path:
        return self.root / "inputs" / cam

    @property
    def calibration_work(self) -> Path:
        return self.root / "calibration_work"

    @property
    def calib_file(self) -> Path:
        return self.calibration_work / "Calib_scene.toml"

    def trial_calib(self, label: str) -> Path:
        """Calibration for a trial that uses a further calibration video of some camera (0.1.0rc13)."""
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
    path = Layout(root).session_json
    if not path.is_file():
        raise FileNotFoundError(f"No {SESSION_FILE} in {root}. Is this a session folder made by mocapapp?")
    return Session.model_validate(json.loads(path.read_text(encoding="utf-8")))
