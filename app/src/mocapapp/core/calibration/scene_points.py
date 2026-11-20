"""Reference points for extrinsic calibration and the world axes they define (PROJECT.md §6.4).

Pose2Sim's scene frame is right-handed and Z-up. In exported TRC/OpenSim files X_osim = Y, Y_osim = Z (up),
Z_osim = X (upstream-notes §4).
"""

from __future__ import annotations

import ast
import csv
import io
import json
import math
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

from mocapapp.core.session import atomic_write_text
from mocapapp.core.validate import Issue

MIN_POINTS = 10
MIN_CLICKED = 6  # upstream minimum per camera
TYPO_LIMIT_M = 20.0
SMALL_SPREAD_M = 1.0
COPLANAR_RATIO = 0.05  # smallest/largest spread (singular values) below this = nearly flat
COLLINEAR_RATIO = 0.01
BELOW_FLOOR_M = -0.02

Y_AXIS_TEXT = ("Seen from above, +Y is 90 degrees counter-clockwise from +X (right-hand rule): standing at the "
               "origin facing +X with Z up, +Y is on your left.")
# the unit the points are entered in; they are always stored (and given to Pose2Sim) in metres
UNITS = {"m": 1.0, "cm": 0.01, "in": 0.0254, "ft": 0.3048}
UNIT_NAMES = {"m": "metres (m)", "cm": "centimetres (cm)", "in": "inches (in)", "ft": "feet (ft)"}
Unit = Literal["m", "cm", "in", "ft"]


def to_metres(value: float, unit: str) -> float:
    return value * UNITS[unit]


def from_metres(value: float, unit: str) -> float:
    return round(value / UNITS[unit], 9)  # no 71.50000000000001 for 0.715 m in cm


OPENSIM_NOTE = "In exported .trc/OpenSim files the axes become X = this Y, Y = this Z (up), Z = this X."


class ScenePoint(BaseModel):
    label: str
    x: float
    y: float
    z: float

    @property
    def xyz(self) -> list[float]:
        return [self.x, self.y, self.z]


class Scene(BaseModel):
    origin_text: str = ""  # "The origin is at ____"
    x_axis_text: str = ""  # "+X points from the origin toward ____ (horizontal)"
    y_axis_note: str = ""  # optional, the user's own description of +Y
    unit: Unit = "m"  # the unit the points were entered in (0.1.0rc15); `points` are in metres regardless
    points: list[ScenePoint] = Field(default_factory=list)

    def array(self) -> np.ndarray:
        return np.array([p.xyz for p in self.points], dtype=float).reshape(-1, 3)

    def labels(self) -> list[str]:
        return [p.label for p in self.points]

    def as_config_snippet(self) -> str:
        rows = ",\n".join(f"    [{p.x:g}, {p.y:g}, {p.z:g}],  # {p.label}" for p in self.points)
        return f"object_coords_3d = [\n{rows}\n]"


# ---------- import / export ----------

def from_csv(text: str, unit: str = "m") -> list[ScenePoint]:
    """CSV with columns label,x,y,z (header optional) in `unit`; the points come back in metres."""
    rows = [r for r in csv.reader(io.StringIO(text)) if r and any(c.strip() for c in r)]
    if rows and not _is_number(rows[0][1] if len(rows[0]) > 1 else ""):
        rows = rows[1:]  # header
    points = []
    for n, r in enumerate(rows, 1):
        if len(r) < 4:
            raise ValueError(f"Row {n}: expected label,x,y,z but got {','.join(r)}.")
        try:
            x, y, z = (float(v.strip().replace(",", ".")) for v in r[1:4])
        except ValueError:
            raise ValueError(f"Row {n} ({r[0]}): x, y and z must be numbers.") from None
        points.append(ScenePoint(label=r[0].strip() or f"P{n}", x=to_metres(x, unit), y=to_metres(y, unit),
                                 z=to_metres(z, unit)))
    return points


def to_csv(points: list[ScenePoint], unit: str = "m") -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["label", f"x_{unit}", f"y_{unit}", f"z_{unit}"])
    for p in points:
        w.writerow([p.label] + [from_metres(v, unit) for v in p.xyz])
    return out.getvalue()


def from_snippet(text: str) -> list[ScenePoint]:
    """Paste of `object_coords_3d = [[x,y,z], ...]` from a Pose2Sim Config.toml; '# comment' becomes the label."""
    body = text.split("=", 1)[1] if "=" in text.split("[", 1)[0] else text
    labels = []
    for line in body.splitlines():
        if "[" in line and "#" in line:
            labels.append(line.split("#", 1)[1].strip())
    clean = "\n".join(line.split("#", 1)[0] for line in body.splitlines())
    try:
        values = ast.literal_eval(clean.strip())
    except (SyntaxError, ValueError):
        raise ValueError("Could not read the pasted points. Expected object_coords_3d = [[x, y, z], ...].") from None
    points = []
    for i, v in enumerate(values):
        if len(v) != 3:
            raise ValueError(f"Point {i + 1} has {len(v)} values; each point needs x, y, z.")
        label = labels[i] if i < len(labels) and labels[i] else f"P{i + 1}"
        points.append(ScenePoint(label=label, x=float(v[0]), y=float(v[1]), z=float(v[2])))
    return points


def _is_number(s: str) -> bool:
    try:
        float(s.strip().replace(",", "."))
        return True
    except ValueError:
        return False


def save_scene(scene: Scene, path: Path) -> None:
    atomic_write_text(path, scene.model_dump_json(indent=2) + "\n")


def load_scene(path: Path) -> Scene:
    return Scene.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


# ---------- validation ----------

def shape(points: np.ndarray) -> tuple[float, float, float]:
    """Singular values of the centred point cloud: spread along its 3 main directions (metres-ish)."""
    if len(points) < 2:
        return (0.0, 0.0, 0.0)
    s = np.linalg.svd(points - points.mean(axis=0), compute_uv=False) / math.sqrt(len(points))
    s = np.pad(s, (0, 3 - len(s)))
    return float(s[0]), float(s[1]), float(s[2])


def check_scene(scene: Scene) -> list[Issue]:
    issues: list[Issue] = []

    def add(level, where, what, fix=""):
        issues.append(Issue(level, where, what, fix))

    if not scene.origin_text.strip():
        add("error", "origin", "Describe where the origin (0, 0, 0) is.",
            "Example: 'tape cross on the floor by the door'.")
    if not scene.x_axis_text.strip():
        add("error", "+X axis", "Describe where +X points from the origin.", "Example: 'toward the window'.")
    n = len(scene.points)
    if n < MIN_POINTS:
        add("error", "points", f"{n} reference points; at least {MIN_POINTS} are needed.",
            "Add more points spread through the capture area, some at different heights.")
    seen_labels: dict[str, int] = {}
    seen_xyz: dict[tuple, str] = {}
    for p in scene.points:
        where = f"point {p.label}"
        if not all(math.isfinite(v) for v in p.xyz):
            add("error", where, "Coordinates must be finite numbers.")
            continue
        if any(abs(v) > TYPO_LIMIT_M for v in p.xyz):
            add("warning", where, f"{p.xyz} is more than {TYPO_LIMIT_M:g} m from the origin.",
                "Check the unit chosen for the points: numbers measured in centimetres need the unit "
                "'centimetres', not 'metres'.")
        if p.z < BELOW_FLOOR_M:
            add("warning", where, f"Z = {p.z:g} m is below the floor (Z is up, the floor should be Z = 0).",
                "Check the sign of Z.")
        if p.label in seen_labels:
            add("error", where, "Two points have the same label.", "Give every point a unique label.")
        seen_labels[p.label] = 1
        key = tuple(round(v, 4) for v in p.xyz)
        if key in seen_xyz:
            add("error", where, f"Same coordinates as point {seen_xyz[key]}.", "Every point must be a different spot.")
        seen_xyz[key] = p.label
    if n >= 3:
        s0, s1, s2 = shape(scene.array())
        if s0 == 0 or s1 / s0 < COLLINEAR_RATIO:
            add("error", "points", "All points lie on one line; the camera position cannot be computed.",
                "Add points away from that line, on the floor and at different heights.")
        elif s2 / s0 < COPLANAR_RATIO:
            add("warning", "points", "The points are nearly flat (all on the floor or all on one wall). "
                "Camera positions will be poorly determined.",
                "Add points at different heights (box or chair corners) inside the capture area.")
        pts = scene.array()
        extent = pts.max(axis=0) - pts.min(axis=0)
        if sum(e >= SMALL_SPREAD_M for e in extent) < 2:
            add("warning", "points", f"Points span only {extent[0]:.2f} x {extent[1]:.2f} x {extent[2]:.2f} m.",
                "Spread them over at least 1 m in two directions, covering where the participant moves.")
    add("info", "cameras", f"Each camera must later see at least {MIN_CLICKED} of these points "
        f"({MIN_POINTS}+ recommended), spread over its picture.")
    return issues


def axes_description(scene: Scene) -> str:
    return "\n".join([
        f"Origin (0, 0, 0): {scene.origin_text or '(not described)'}",
        f"+X: points from the origin toward {scene.x_axis_text or '(not described)'} (horizontal)",
        f"+Y: {Y_AXIS_TEXT}" + (f" ({scene.y_axis_note})" if scene.y_axis_note else ""),
        "+Z: up, away from the floor (floor = 0)",
        OPENSIM_NOTE,
    ])
