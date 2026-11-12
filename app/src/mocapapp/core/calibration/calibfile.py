"""Calib_scene.toml, written with upstream's own writer and checked with upstream's own reader."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from Pose2Sim.calibration import toml_write
from Pose2Sim.common import retrieve_calib_params

from mocapapp.core.calibration.extrinsics import ExtrinsicsResult
from mocapapp.core.calibration.intrinsics import IntrinsicsResult


class CalibFileError(Exception):
    pass


def write_calib(path: Path, intrinsics: list[IntrinsicsResult], extrinsics: list[ExtrinsicsResult]) -> Path:
    """Cameras in the given order (cam01, cam02, ...): Pose2Sim matches them to videos by position."""
    names = [i.camera for i in intrinsics]
    if names != [e.camera for e in extrinsics]:
        raise CalibFileError("Intrinsic and extrinsic results are not for the same cameras in the same order.")
    if names != sorted(names, key=lambda n: int("".join(ch for ch in n if ch.isdigit()) or 0)):
        raise CalibFileError(f"Cameras must be in numeric order, got {names}.")
    if any("." in n for n in names):
        raise CalibFileError("Camera names must not contain dots (TOML would read them as nested tables).")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    toml_write(tmp, names,
               [[float(i.size[0]), float(i.size[1])] for i in intrinsics],
               [i.distortions for i in intrinsics],
               [np.array(i.matrix, float) for i in intrinsics],
               [e.rotation for e in extrinsics],
               [e.translation for e in extrinsics])
    tmp.replace(path)
    verify(path, intrinsics, extrinsics)
    return path


def verify(path: Path, intrinsics: list[IntrinsicsResult], extrinsics: list[ExtrinsicsResult]) -> None:
    """Round trip through upstream's reader: what Pose2Sim will use must equal what we computed."""
    calib = retrieve_calib_params(path)
    if len(calib["K"]) != len(intrinsics):
        raise CalibFileError(f"{path.name}: Pose2Sim reads {len(calib['K'])} cameras, expected {len(intrinsics)}.")
    for c, (i, e) in enumerate(zip(intrinsics, extrinsics, strict=True)):
        checks = [(calib["K"][c], np.array(i.matrix)), (calib["dist"][c], np.array(i.distortions)),
                  (calib["R"][c], np.array(e.rotation)), (calib["T"][c], np.array(e.translation))]
        for got, want in checks:
            if not np.allclose(np.asarray(got, float).ravel(), np.asarray(want, float).ravel(), rtol=1e-9, atol=1e-9):
                raise CalibFileError(f"{path.name}: camera {i.camera} does not read back as written.")
