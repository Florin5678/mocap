"""Environment check (`mocapapp doctor`): every failure comes with a plain-language fix."""

from __future__ import annotations

import importlib
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

MIN_FREE_GB = 10


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""


def _import_check(module: str, label: str, fix: str) -> Check:
    try:
        mod = importlib.import_module(module)
    except Exception as exc:  # any import failure is reported, never raised
        return Check(label, False, f"cannot import {module}: {exc}", fix)
    version = getattr(mod, "__version__", None) or getattr(mod, "GetVersion", lambda: "")()
    return Check(label, True, f"{module} {version}".strip())


def _ffmpeg_check() -> Check:
    exe = shutil.which("ffmpeg")
    if exe:
        return Check("ffmpeg", True, exe)
    try:
        import imageio_ffmpeg

        return Check("ffmpeg", True, imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass
    # Pose2Sim reads videos through OpenCV and PyAV, so ffmpeg is only needed for normalisation (M1).
    return Check("ffmpeg", False, "not found",
                 "Install ffmpeg and make sure it is on PATH (only needed to convert videos).")


def _accelerator_check() -> Check:
    try:
        import onnxruntime as ort

        providers = ort.get_available_providers()
    except Exception as exc:
        return Check("Pose backend", False, f"onnxruntime unavailable: {exc}",
                     "Reinstall the environment: `uv sync` in the app folder.")
    gpu = [p for p in providers if p in ("CUDAExecutionProvider", "ROCMExecutionProvider",
                                          "CoreMLExecutionProvider", "DmlExecutionProvider")]
    detail = f"GPU: {', '.join(gpu)}" if gpu else "CPU only (works, but pose estimation is slower)"
    return Check("Pose backend", True, detail)


def _models_check() -> Check:
    try:
        from mocapapp.core import models

        gone = models.missing(("balanced",))
    except Exception as exc:
        return Check("Pose models", False, f"cannot check: {exc}", "Reinstall the environment.")
    if not gone:
        return Check("Pose models", True, f"downloaded ({models.cache_dir()})")
    return Check("Pose models", False, "not downloaded yet (needed once, about "
                 f"{models.SIZES_MB['balanced']} MB)", "Run `mocapapp models --download` while online, or let the "
                 "app download them at first start.")


def _existing(path: Path) -> Path:
    """The folder itself, or its nearest parent that exists (a projects folder may not be made yet)."""
    path = Path(path).absolute()
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


def _disk_check(projects: Path | None) -> Check:
    """Processing writes into the project folders, so their drive is the one that needs the space. Before any
    project exists, the drive the app is installed on is checked instead."""
    path = _existing(projects if projects else Path(sys.prefix))
    free_gb = shutil.disk_usage(path).free / 1e9
    ok = free_gb >= MIN_FREE_GB
    drive = path.anchor or str(path)
    where = "where the projects are" if projects else "where the app is installed; no projects folder yet"
    if ok:
        fix = ""
    elif projects:
        fix = (f"Free up space on {drive}, or keep new projects on a drive with more space (Help > Settings): "
               f"processing needs roughly {MIN_FREE_GB} GB or more.")
    else:
        fix = (f"Processing needs roughly {MIN_FREE_GB} GB or more on the drive where the projects are stored: "
               f"put your first project on a drive with enough space, or free up space on {drive}.")
    return Check("Free disk", ok, f"{free_gb:.1f} GB free on {drive} ({where})", fix)


def run_checks(work_dir: Path | None = None) -> list[Check]:
    """`work_dir`: the folder where projects are stored, for the disk-space check (None: not known yet)."""
    py_ok = (3, 11) <= sys.version_info[:2] <= (3, 13)
    checks = [
        Check("Python", py_ok, f"{platform.python_version()} ({sys.executable})",
              "" if py_ok else "Use Python 3.11, 3.12 or 3.13."),
        _import_check("Pose2Sim", "Pose2Sim", "Run `uv sync` in the app folder to install Pose2Sim."),
        _import_check("opensim", "OpenSim", "Run `uv sync`; OpenSim's PyPI package needs Python 3.11-3.13."),
        _import_check("cv2", "OpenCV", "Run `uv sync` in the app folder."),
        _accelerator_check(),
        _ffmpeg_check(),
        _models_check(),
        _disk_check(work_dir),
    ]
    return checks
