"""A trial's results as data, for the Viewer step and `mocapapp data`: 3D marker positions (.trc) and joint angles
(.mot), read from the trial folders (no export needed). Plain numpy; no plotting here.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from mocapapp.core.qa import BONES
from mocapapp.core.session import Layout, LinkedResults, Session, Trial

# (label, glob in pose-3d/, name parts to exclude): best first
MARKER_SOURCES = [("smoothed + LSTM", "*_filt_*_LSTM.trc", ()),
                  ("smoothed", "*_filt_*.trc", ("_LSTM",)),
                  ("raw", "*.trc", ("_filt_", "_LSTM"))]
TRANSLATIONS = ("_tx", "_ty", "_tz")


class OutputError(ValueError):
    pass


@dataclass
class Table:
    """Values over time: one row per frame, one column per quantity."""
    title: str
    path: Path
    time: np.ndarray  # seconds, (frames,)
    columns: list[str]
    values: np.ndarray  # (frames, columns)
    units: list[str]  # per column

    def column(self, name: str) -> np.ndarray:
        return self.values[:, self.columns.index(name)]

    def unit(self, name: str) -> str:
        return self.units[self.columns.index(name)]


@dataclass
class Markers:
    path: Path
    names: list[str]
    time: np.ndarray  # (frames,)
    xyz: np.ndarray  # (frames, markers, 3), OpenSim frame: Y up
    rate: float
    units: str = "m"

    @property
    def bones(self) -> list[tuple[str, str]]:
        return [(a, b) for a, b in BONES if a in self.names and b in self.names]

    def table(self) -> Table:
        cols = [f"{n} {axis}" for n in self.names for axis in "XYZ"]
        return Table("Marker positions", self.path, self.time, cols,
                     self.xyz.reshape(len(self.time), -1), [self.units] * len(cols))


# ---------- finding files ----------

def marker_files(trial_dir: Path) -> dict[str, Path]:
    """Marker files of a trial by source label ('smoothed + LSTM', 'smoothed', 'raw'), best first."""
    folder = Path(trial_dir) / "pose-3d"
    out = {}
    for label, pattern, exclude in MARKER_SOURCES:
        found = sorted(p for p in folder.glob(pattern) if not any(x in p.name for x in exclude)) \
            if folder.is_dir() else []
        if found:
            out[label] = found[0]
    return out


def angle_file(trial_dir: Path) -> Path | None:
    folder = Path(trial_dir) / "kinematics"
    found = sorted(folder.glob("*.mot")) if folder.is_dir() else []
    return found[0] if found else None


def trials_with_results(session: Session, root: Path) -> list[Trial]:
    layout = Layout(root)
    return [t for t in session.active_trials
            if marker_files(layout.trial(t.label)) or angle_file(layout.trial(t.label))]


def has_results(folder: Path) -> bool:
    return bool(marker_files(folder) or angle_file(folder))


# ---------- Pose2Sim results made elsewhere (Results > Import Pose2Sim data) ----------

def find_result_folders(folder: Path, depth: int = 3) -> list[Path]:
    """The folder itself when it holds Pose2Sim results (pose-3d/*.trc or kinematics/*.mot), otherwise every such
    folder below it (a Pose2Sim project with its trial folders, up to `depth` levels down)."""
    folder = Path(folder)
    if has_results(folder):
        return [folder]
    if depth <= 0:
        return []
    try:
        subs = sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return []
    return [f for sub in subs for f in find_result_folders(sub, depth - 1)]


def link_results(session: Session, root: Path, folder: Path) -> list[LinkedResults]:
    """Remember the Pose2Sim results in `folder` (a trial folder, or a project with several) for Results. Nothing
    is copied or changed there. Returns the new links; folders linked before are skipped."""
    folder = Path(folder).resolve()
    if folder == Path(root).resolve() or Path(root).resolve() in folder.parents:
        raise OutputError("This folder is part of this project: its trials are in Results already.")
    found = find_result_folders(folder)
    if not found:
        raise OutputError(f"No Pose2Sim results in {folder}: expected a pose-3d folder with .trc files or a "
                          "kinematics folder with .mot files (in this folder, or in its trial folders).")
    known = {str(Path(x.path).resolve()) for x in session.linked}
    n = max((int(x.id[1:]) for x in session.linked if x.id[1:].isdigit()), default=0)
    new = []
    for f in found:
        if str(f) in known:
            continue
        n += 1
        name = folder.name if f == folder else f"{folder.name}/{f.relative_to(folder).as_posix()}"
        new.append(LinkedResults(id=f"i{n}", name=name, path=str(f)))
    session.linked += new
    return new


def find_link(session: Session, key: str) -> LinkedResults | None:
    """A link by its id ("i2") or its name."""
    return next((x for x in session.linked if key in (x.id, x.name)), None)


def unlink_results(session: Session, key: str) -> LinkedResults:
    """Forget a link (its folder is not touched)."""
    link = find_link(session, key)
    if link is None:
        raise OutputError(f"No imported Pose2Sim data called {key}.")
    session.linked.remove(link)
    return link


def result_dir(session: Session, root: Path, key: str) -> Path:
    """The folder with the results of a trial (its label) or of imported Pose2Sim data (its id)."""
    link = find_link(session, key) if key.startswith("i") else None
    return Path(link.path) if link else Layout(root).trial(key)


def viewable(session: Session, root: Path) -> list[tuple[str, str]]:
    """(key, title) of everything the viewer can show: the trials with results, then the imported data whose
    folder is still there."""
    out = [(t.label, f"Trial {t.label} ({t.code})") for t in trials_with_results(session, root)]
    return out + [(x.id, f"Imported: {x.name}") for x in session.linked if has_results(Path(x.path))]


# ---------- readers ----------

def _number(text: str) -> float:
    text = text.strip()
    try:
        return float(text) if text and text.lower() != "nan" else np.nan
    except ValueError:
        return np.nan


def read_markers(path: Path) -> Markers:
    """A .trc file (Pose2Sim/OpenSim): header of 5 lines, then Frame#, Time, X1, Y1, Z1, ..."""
    path = Path(path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    try:
        keys, vals = lines[1].split("\t"), lines[2].split("\t")
        meta = dict(zip((k.strip() for k in keys), (v.strip() for v in vals), strict=False))
        rate = float(meta.get("DataRate") or meta.get("CameraRate") or 0)
        names = [n.strip() for n in lines[3].split("\t")[2:] if n.strip()]
        rows = [r.split("\t") for r in lines[5:] if r.strip()]
        time = np.array([_number(r[1]) for r in rows])
        data = np.array([[_number(v) for v in (r[2:2 + 3 * len(names)] + [""] * 3 * len(names))[:3 * len(names)]]
                         for r in rows], dtype=float)
    except (IndexError, ValueError) as exc:
        raise OutputError(f"{path.name} is not a readable marker file (.trc): {exc}") from None
    if not names or not rows:
        raise OutputError(f"{path.name} has no markers or no frames.")
    return Markers(path, names, time, data.reshape(len(rows), len(names), 3), rate, meta.get("Units") or "m")


def read_angles(path: Path) -> Table:
    """A .mot/.sto file (OpenSim): header up to 'endheader', then a row of column names starting with 'time'.
    Rotations are in degrees when the header says inDegrees=yes; pelvis_tx/ty/tz are in metres."""
    path = Path(path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    try:
        end = next(i for i, line in enumerate(lines) if line.strip().lower() == "endheader")
    except StopIteration:
        raise OutputError(f"{path.name} is not a readable joint-angle file (.mot): no 'endheader' line.") from None
    degrees = any(line.replace(" ", "").lower() == "indegrees=yes" for line in lines[:end])
    header = [c.strip() for c in lines[end + 1].split("\t")] if len(lines) > end + 1 else []
    if not header or header[0].lower() != "time":
        raise OutputError(f"{path.name}: the first column after the header should be 'time'.")
    rows = [[_number(v) for v in (r.split("\t") + [""] * len(header))[:len(header)]]
            for r in lines[end + 2:] if r.strip()]
    if not rows:
        raise OutputError(f"{path.name} has no frames.")
    data = np.array(rows, dtype=float)
    cols = header[1:]
    units = ["m" if c.endswith(TRANSLATIONS) else "deg" if degrees else "rad" for c in cols]
    return Table("Joint angles", path, data[:, 0], cols, data[:, 1:], units)


# ---------- numbers ----------

def rate_unit(unit: str) -> str:
    return f"{unit}/s"


def speed(table: Table, name: str) -> np.ndarray:
    """Rate of change over time (deg/s for angles, m/s for positions); central differences."""
    y, t = table.column(name), table.time
    if len(t) < 2:
        return np.full_like(y, np.nan)
    return np.gradient(y, t)


def summary(table: Table, columns: list[str] | None = None) -> list[dict]:
    """Per column: minimum, maximum, range and the peak absolute rate of change, ignoring missing frames."""
    out = []
    for name in columns or table.columns:
        y = table.column(name)
        ok = ~np.isnan(y)
        if not ok.any():
            out.append({"column": name, "unit": table.unit(name), "min": None, "max": None, "range": None,
                        "peak_speed": None})
            continue
        v = speed(table, name)
        lo, hi = float(np.nanmin(y)), float(np.nanmax(y))
        peak = float(np.nanmax(np.abs(v))) if (~np.isnan(v)).any() else None
        out.append({"column": name, "unit": table.unit(name), "min": lo, "max": hi, "range": hi - lo,
                    "peak_speed": peak})
    return out


def write_csv(table: Table, out: Path, columns: list[str] | None = None) -> Path:
    """Comma-separated, one header row with units, for Excel/R/Python. Missing values are empty cells."""
    columns = columns or table.columns
    unknown = [c for c in columns if c not in table.columns]
    if unknown:
        raise OutputError(f"Unknown column(s): {', '.join(unknown)}. Columns: {', '.join(table.columns)}")
    idx = [table.columns.index(c) for c in columns]
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time (s)"] + [f"{c} ({table.units[i]})" for c, i in zip(columns, idx, strict=True)])
        for r, t in enumerate(table.time):
            w.writerow([f"{t:.5f}"] + ["" if np.isnan(table.values[r, i]) else f"{table.values[r, i]:.6g}"
                                       for i in idx])
    return out
