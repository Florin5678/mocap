"""Video probing (PyAV) and normalisation to constant-frame-rate, upright H.264 copies (bundled ffmpeg)."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import av

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv"}
VFR_TOLERANCE = 0.02  # relative spread of frame intervals above which a video counts as variable frame rate
VFR_SAMPLE_PACKETS = 300


class MediaError(Exception):
    """A video cannot be used. The message says what is wrong and what to do."""


@dataclass
class ProbeInfo:
    path: str
    codec: str
    width: int
    height: int
    fps: float
    duration_s: float
    rotation: int  # degrees the player rotates the picture (display matrix)
    vfr: bool
    creation_time: str | None
    container: str

    @property
    def display_size(self) -> tuple[int, int]:
        return (self.height, self.width) if self.rotation % 180 else (self.width, self.height)

    def to_dict(self) -> dict:
        return asdict(self)


def is_video(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTS and not path.name.startswith(".")


def _frame_intervals_spread(container, stream) -> float:
    pts = []
    for packet in container.demux(stream):
        if packet.pts is not None:
            pts.append(packet.pts)
        if len(pts) >= VFR_SAMPLE_PACKETS:
            break
    pts.sort()  # B-frames: packets come in decode order
    deltas = [b - a for a, b in zip(pts, pts[1:], strict=False) if b > a]
    if len(deltas) < 10:
        return 0.0
    deltas.sort()
    median = deltas[len(deltas) // 2]
    # robust spread: share of intervals that differ from the median by more than 1 %
    off = sum(1 for d in deltas if abs(d - median) > 0.01 * median)
    return off / len(deltas)


def probe(path: Path) -> ProbeInfo:
    path = Path(path)
    if not path.is_file():
        raise MediaError(f"{path} does not exist.")
    if path.stat().st_size == 0:
        raise MediaError(f"{path.name} is empty (0 bytes). Copy it again from the camera.")
    try:
        with av.open(str(path)) as container:
            if not container.streams.video:
                raise MediaError(f"{path.name} has no video stream.")
            stream = container.streams.video[0]
            rate = stream.average_rate or stream.guessed_rate or stream.base_rate
            fps = float(rate) if rate else 0.0
            if container.duration:
                duration = container.duration / av.time_base
            elif stream.duration and stream.time_base:
                duration = float(stream.duration * stream.time_base)
            else:
                duration = 0.0
            created = stream.metadata.get("creation_time") or container.metadata.get("creation_time")
            first = next(container.decode(stream), None)
            if first is None:
                raise MediaError(f"{path.name} contains no decodable frames.")
            rotation = int(getattr(first, "rotation", 0) or 0) % 360
            container.seek(0)
            spread = _frame_intervals_spread(container, stream)
            info = ProbeInfo(
                path=str(path), codec=stream.codec_context.name, width=stream.width, height=stream.height,
                fps=round(fps, 3), duration_s=round(duration, 3), rotation=rotation,
                vfr=spread > VFR_TOLERANCE, creation_time=created, container=path.suffix.lower().lstrip("."),
            )
    except MediaError:
        raise
    except Exception as exc:  # PyAV raises many types for corrupt files
        raise MediaError(f"{path.name} cannot be read as a video ({exc}). It may be corrupt or incomplete.") from exc
    if info.fps <= 0:
        raise MediaError(f"{path.name}: frame rate could not be determined.")
    return info


def recording_time(info: ProbeInfo) -> datetime | None:
    if not info.creation_time:
        return None
    try:
        return datetime.fromisoformat(info.creation_time.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------- normalisation ----------

def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def needs_transcode(info: ProbeInfo) -> bool:
    return info.vfr or info.rotation != 0 or info.codec != "h264" or info.container != "mp4"


def normalise(src: Path, dst: Path, info: ProbeInfo | None = None, target_fps: float | None = None) -> str:
    """Make `dst` a constant-frame-rate, upright H.264 .mp4 of `src`. Never touches `src`.

    Returns how: 'hardlink', 'copy' or 'transcode'.
    """
    src, dst = Path(src), Path(dst)
    info = info or probe(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    tmp.unlink(missing_ok=True)
    fps = target_fps or info.fps
    if not needs_transcode(info) and (target_fps is None or abs(target_fps - info.fps) < 0.01):
        dst.unlink(missing_ok=True)
        try:
            os.link(src, dst)
            return "hardlink"
        except OSError:  # other drive or file system without hard links
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
            return "copy"
    # ffmpeg applies the rotation metadata by default (autorotate) and drops it from the output.
    cmd = [ffmpeg_exe(), "-v", "error", "-y", "-i", str(src), "-map", "0:v:0", "-an",
           "-vf", f"fps={fps:g}", "-fps_mode", "cfr", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "17", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if proc.returncode:
        tmp.unlink(missing_ok=True)
        raise MediaError(f"Converting {src.name} failed: {proc.stderr.strip()[-400:]}")
    os.replace(tmp, dst)
    return "transcode"
