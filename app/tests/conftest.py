import subprocess
from pathlib import Path

import pytest

from mocapapp.core.media import ffmpeg_exe


def make_video(path: Path, seconds: float = 0.5, fps: int = 60, size: str = "64x48", rotation: int = 0,
               vfr: bool = False, creation_time: str | None = None, pattern: str = "testsrc") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", f"{pattern}=size={size}:rate={fps}",
           "-t", str(seconds)]
    if vfr:  # second half of the frames twice as far apart
        half = int(seconds * fps / 2)
        cmd += ["-vf", f"setpts='if(lt(N,{half}),N,{half}+(N-{half})*2)/{fps}/TB'", "-fps_mode", "vfr"]
    if creation_time:
        cmd += ["-metadata", f"creation_time={creation_time}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True)
    if rotation:
        rotated = path.with_name(path.stem + ".rot" + path.suffix)
        subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-display_rotation", str(rotation), "-i", str(path),
                        "-c", "copy", str(rotated)], check=True)
        rotated.replace(path)
    return path


@pytest.fixture
def video():
    return make_video


@pytest.fixture
def camera_dirs(tmp_path):
    """Factory: camera folders with intrinsic, extrinsic and the given trial file names."""

    def build(n_cams=3, trial_names=("trial1", "trial2"), **kwargs):
        dirs = []
        for c in range(1, n_cams + 1):
            d = tmp_path / "raw" / f"camera {c}"
            make_video(d / "intrinsic.mp4", seconds=0.4 + 0.02 * c, pattern="testsrc2", **kwargs)
            make_video(d / "extrinsic.mp4", seconds=0.4 + 0.02 * c, pattern="smptebars", **kwargs)
            for i, name in enumerate(trial_names):
                make_video(d / f"{name}.mp4", seconds=0.5 + 0.1 * i + 0.01 * c, **kwargs)
            dirs.append(d)
        return dirs

    return build
