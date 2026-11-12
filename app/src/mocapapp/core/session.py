"""Session model (session.json) and on-disk layout (PROJECT.md §5)."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

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


class Consent(BaseModel):
    attested: bool = False
    attested_at: str | None = None
    raw_footage_storage: bool = False
    template_version: str = "1"


class Trial(BaseModel):
    label: str
    mapping_method: Literal["digit", "chronological", "manual"] | None = None
    code: str
    height_m: float | None = None  # None = 'auto'
    consent: Consent = Field(default_factory=Consent)
    delete_raw: bool = True  # follows consent: no storage consent -> delete (PROJECT.md §6.2)
    # PROJECT.md §8.4: set by save_session when raw footage is kept with consent ("analysis finished")
    retention_until: str | None = None
    raw_deleted_at: str | None = None  # working copies + overlays + 2D keypoints deleted (§8.4)
    withdrawn_at: str | None = None  # "Delete all participant data": every file of this trial is gone

    @property
    def raw_present(self) -> bool:
        return self.raw_deleted_at is None and self.withdrawn_at is None

    @field_validator("height_m", mode="before")
    @classmethod
    def _height(cls, v):
        return parse_height(v)


class VideoRef(BaseModel):
    file: str
    origin: Literal["found", "manual"] = "found"


class CalibVideo(VideoRef):
    rms_px: float | None = None
    status: Literal["pending", "ok", "warn", "fail"] = "pending"


class Camera(BaseModel):
    name: str  # cam01, cam02, ... (order = calibration order, see upstream-notes §2)
    source_dir: str
    intrinsic: CalibVideo | None = None
    extrinsic: VideoRef | None = None
    trial_files: dict[str, str] = Field(default_factory=dict)  # trial label -> source file


class FilterSettings(BaseModel):
    type: str = "butterworth"
    cutoff_hz: float = 6.0
    order: int = 4


class Settings(BaseModel):
    pose_mode: Literal["lightweight", "balanced", "performance"] = "balanced"
    device: str = "auto"
    filter: FilterSettings = Field(default_factory=FilterSettings)
    use_augmentation: bool = True
    make_bvh: bool = True
    board_type: Literal["charuco", "chess"] = "charuco"
    corners_nb: tuple[int, int] = (5, 8)  # internal corners [rows-1, cols-1]
    square_size_mm: float = 25.0
    marker_resolution: int = 4


class RunState(BaseModel):
    stage_done: dict[str, list[str]] = Field(default_factory=dict)  # trial label -> finished stages
    trial_status: dict[str, str] = Field(default_factory=dict)  # label -> pending|running|needs_sync_fix|failed|done
    sync: dict[str, dict] = Field(default_factory=dict)  # label -> {"offsets", "issues", "accepted"}
    errors: dict[str, str] = Field(default_factory=dict)  # label -> plain-language failure cause
    last_error: str | None = None


class Session(BaseModel):
    schema_: int = Field(SCHEMA, alias="schema")
    app_version: str = __version__
    pose2sim_commit: str | None = None
    opensim_version: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    study_code: str = ""
    n_trials: int
    trials: list[Trial]
    cameras: list[Camera]
    extrinsics: list[dict] = Field(default_factory=list)
    settings: Settings = Field(default_factory=Settings)
    run_state: RunState = Field(default_factory=RunState)
    sync_cue_confirmed: bool = False
    # "app" = computed with the app's calibration steps; "imported" = an existing Calib file (sample data only)
    calibration_source: Literal["app", "imported"] = "app"

    model_config = {"populate_by_name": True}

    def camera(self, name: str) -> Camera:
        for cam in self.cameras:
            if cam.name == name:
                return cam
        raise KeyError(f"No camera named {name} in this session.")

    @property
    def active_trials(self) -> list[Trial]:
        """Trials whose participant has not withdrawn (withdrawn trials keep only their code and the log)."""
        return [t for t in self.trials if t.withdrawn_at is None]

    @property
    def frozen(self) -> bool:
        """Raw footage was deleted for some trial: the session's videos can no longer be prepared again."""
        return any(not t.raw_present for t in self.trials)

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


RETENTION_TEXT = "analysis finished"
CONSENT_FILE = "consent.json"


def _sync_retention(trial: Trial) -> None:
    """§8.4 item 6: storage consented and deletion declined -> keep until the analysis is finished."""
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
        codes.update(t.get("code", "") for t in data.get("trials", []))
    return codes


def load_session(root: Path) -> Session:
    path = Layout(root).session_json
    if not path.is_file():
        raise FileNotFoundError(f"No {SESSION_FILE} in {root}. Is this a session folder made by mocapapp?")
    return Session.model_validate(json.loads(path.read_text(encoding="utf-8")))
