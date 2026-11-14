"""Pose-model weights: download once, then work offline (PROJECT.md FR-1.2).

Pose2Sim downloads RTMPose/YOLOX ONNX models through rtmlib the first time a mode is used. This module fetches
the same files into the same cache ahead of time, using rtmlib's own downloader (with its mirror fallback), so
processing never needs the network. A model is downloaded completely or not at all; an interrupted download
restarts that model only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

MODES = ("lightweight", "balanced", "performance")
# approximate download sizes (MB), shown before downloading
SIZES_MB = {"lightweight": 40, "balanced": 145, "performance": 555}  # zips, measured 2026-11


@dataclass(frozen=True)
class Model:
    mode: str
    role: str  # "person detector" | "pose"
    url: str

    @property
    def onnx(self) -> Path:
        return cache_dir() / (Path(self.url).name.split(".")[0] + ".onnx")

    @property
    def ready(self) -> bool:
        return self.onnx.is_file() and self.onnx.stat().st_size > 0


def cache_dir() -> Path:
    from rtmlib.tools.file import _get_rtmhub_dir

    return Path(_get_rtmhub_dir()) / "checkpoints"


KEYPOINTS = ("Body_with_feet", "Whole_body_wrist", "Whole_body")  # Pose2Sim pose_model values the app offers


def _solution(keypoints: str):
    if keypoints == "Body_with_feet":  # HALPE_26
        from rtmlib.tools.solution.body_with_feet import BodyWithFeet

        return BodyWithFeet
    if keypoints in ("Whole_body_wrist", "Whole_body"):  # COCO_133: the same models, fewer points kept
        from rtmlib.tools.solution.wholebody import Wholebody

        return Wholebody
    raise ValueError(f"Unknown keypoint set '{keypoints}'. Choose from: {', '.join(KEYPOINTS)}.")


def models(modes: tuple[str, ...] | list[str] = ("balanced",), keypoints: str = "Body_with_feet") -> list[Model]:
    """The person detector and pose model of each mode for a keypoint set."""
    solution = _solution(keypoints)
    out = []
    for mode in modes:
        if mode not in MODES:
            raise ValueError(f"Unknown mode '{mode}'. Choose from: {', '.join(MODES)}.")
        spec = solution.MODE[mode]
        out += [Model(mode, "person detector", spec["det"]), Model(mode, "pose", spec["pose"])]
    return out


def missing(modes=("balanced",), keypoints: str = "Body_with_feet") -> list[Model]:
    return [m for m in models(modes, keypoints) if not m.ready]


def download(modes=("balanced",), progress: Callable[[str], None] = print,
             keypoints: str = "Body_with_feet") -> list[Path]:
    """Download every missing model of `modes`. Raises OSError with a plain-language message when offline."""
    from rtmlib.tools.file import download_checkpoint

    todo = missing(modes, keypoints)
    done = []
    for i, m in enumerate(todo, 1):
        progress(f"[{i}/{len(todo)}] Downloading the {m.mode} {m.role} model ...")
        try:
            done.append(Path(download_checkpoint(m.url, str(cache_dir()), progress=False)))
        except Exception as exc:  # urllib/HTTP/zip errors from rtmlib
            raise OSError(f"Could not download the {m.mode} {m.role} model ({type(exc).__name__}: {exc}). "
                          "Check the internet connection and try again; finished models are kept.") from exc
    _cleanup()
    progress("All pose models are ready." if not missing(modes, keypoints) else "Some models are still missing.")
    return done


def _cleanup() -> None:
    """rtmlib leaves the downloaded zips and an extraction folder next to the .onnx files."""
    import shutil

    folder = cache_dir()
    for z in folder.glob("*.zip"):
        if (folder / (z.name.split(".")[0] + ".onnx")).is_file():
            z.unlink(missing_ok=True)
    tmp = folder / "tmp"
    if tmp.is_dir():
        shutil.rmtree(tmp, ignore_errors=True)

