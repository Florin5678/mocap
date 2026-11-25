"""Run the Pose2Sim stages for each trial in subprocesses: progress, cancel, resume, failure isolation.

Each stage runs as `Pose2SimPipeline().<stage>()` with cwd = the trial folder (upstream-notes §1) and a UTF-8,
non-interactive environment (§9, §10). Stage output goes to `<trial>/mocap_logs/<stage>.log` (the folder name must
not contain 'calib', upstream-notes §1).
"""

from __future__ import annotations

import functools
import json
import math
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mocapapp.core.config_gen import gui_flags_set
from mocapapp.core.migrate import triangulation_deps
from mocapapp.core.session import Layout, Session, save_session
from mocapapp.core.validate import Issue, is_ascii_path

STAGES = ["poseEstimation", "synchronization", "personAssociation", "triangulation", "filtering",
          "markerAugmentation", "kinematics"]
STAGE_TITLES = {
    "poseEstimation": "Finding body keypoints in every video",
    "synchronization": "Synchronising the cameras",
    "personAssociation": "Matching the person across cameras",
    "triangulation": "Computing 3D positions",
    "filtering": "Smoothing",
    "markerAugmentation": "Adding anatomical markers (LSTM)",
    "kinematics": "Scaling the OpenSim model and computing joint angles",
}
LOG_DIR = "mocap_logs"
KEYPOINT_STAGES = ("poseEstimation", "synchronization", "personAssociation")  # their output is 2D keypoint files


def stage_inputs(session: Session, stage: str, label: str | None = None) -> str:
    """The settings a stage's result depends on. When they change, the stage (and every later one) runs again."""
    st = session.settings
    # settings added later only appear when not at their default, so older records still match
    deps = {"poseEstimation": [st.pose_mode] + ([st.keypoints] if st.keypoints != "Body_with_feet" else []),
            "filtering": [st.filter.type, st.filter.cutoff_hz, st.filter.order],
            "markerAugmentation": [st.use_augmentation],
            "kinematics": [st.use_augmentation, True] + (["simple model"] if st.simple_model else [])}.get(stage, [])
    if stage == "triangulation" and label is not None:  # the trial's cameras and their solved positions (layout)
        deps += triangulation_deps([(slot, cam.id, lc.result) for slot, cam, lc in session.trial_cameras(label)])
    return json.dumps(deps)


def slots(session: Session, label: str) -> list[str]:
    """The camera names in a trial's folder: cam01, cam02, ... in its layout's order."""
    return [slot for slot, _c, _lc in session.trial_cameras(label)]


def stale_stages(session: Session, label: str) -> list[str]:
    """Finished stages whose settings were changed since they ran (sessions from before 0.1.0rc6 count as current)."""
    record = session.run_state.stage_settings.get(label, {})
    return [s for s in session.run_state.stage_done.get(label, [])
            if s in record and record[s] != stage_inputs(session, s, label)]


KEYPOINT_TITLES = {"Body_with_feet": "Body with feet", "Whole_body_wrist": "Whole body with wrists",
                   "Whole_body": "Whole body"}


def _missing_models(mode: str, keypoints: str) -> list:
    from mocapapp.core import models

    try:
        return models.missing((mode,), keypoints)
    except Exception:  # rtmlib missing or changed: Pose2Sim reports the real problem when it runs
        return []


def max_cutoff_hz(fps: float) -> int:
    """Highest whole cut-off a Butterworth filter accepts at this frame rate (it must stay below fps / 2)."""
    return math.ceil(fps / 2) - 1
SYNC_MIN_CORRELATION = 0.5
SYNC_MAX_OFFSET_S = 5.0  # longer offsets are noted, not stopped: cameras started by hand can be seconds apart
CPU_FRAMES_PER_SECOND = 2.0  # pose estimation per video on the owner's laptop CPU (upstream-notes §6), rough


@dataclass
class Event:
    kind: str  # stage_start | progress | line | stage_end | trial_end | needs_sync_fix
    trial: str
    stage: str = ""
    message: str = ""
    fraction: float | None = None
    ok: bool | None = None


Listener = Callable[[Event], None]


class Cancelled(Exception):
    pass


def stage_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "MPLBACKEND": "Agg", "PYTHONUNBUFFERED": "1"})
    return env


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False)
    else:
        os.killpg(proc.pid, signal.SIGKILL)


TQDM_RE = re.compile(r"(\d{1,3})%\|")


def stage_fraction(line: str, bars: dict[str, float]) -> float | None:
    """Progress of a stage from a tqdm line, or None for other lines. Upstream runs some stages per camera in
    parallel, each with its own bar ("Processing cam01.mp4:  35%|"); their lines alternate, so the stage's progress
    is the mean of every bar seen so far, never the last line alone (which made the progress bar jump)."""
    m = TQDM_RE.search(line)
    if not m:
        return None
    bars[line[:m.start()].strip()] = min(int(m.group(1)), 100) / 100
    return sum(bars.values()) / len(bars)


def _read_lines(stream, out: queue.Queue) -> None:
    """Reader thread: split on \\r and \\n (tqdm redraws with \\r) and hand lines over; None marks the end."""
    buf = b""
    try:
        while chunk := stream.read1(4096):
            buf += chunk
            *parts, buf = re.split(rb"[\r\n]", buf)
            for raw in parts:
                out.put(raw.decode("utf-8", errors="replace").rstrip())
        out.put(buf.decode("utf-8", errors="replace").rstrip())
    except (OSError, ValueError):  # pipe closed after a cancel
        pass
    finally:
        out.put(None)


def run_stage(trial_dir: Path, stage: str, on_line: Callable[[str], None], cancel: threading.Event | None = None,
              code: str | None = None) -> int:
    """Run one stage in a subprocess; returns its exit code. Raises Cancelled (after killing the process tree)."""
    code = code or f"from Pose2Sim.Pose2Sim import Pose2SimPipeline; Pose2SimPipeline().{stage}()"
    log_dir = trial_dir / LOG_DIR
    log_dir.mkdir(exist_ok=True)
    kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {
        "start_new_session": True}
    with open(log_dir / f"{stage}.log", "w", encoding="utf-8") as log:
        proc = subprocess.Popen([sys.executable, "-c", code], cwd=trial_dir, env=stage_env(),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
        lines: queue.Queue = queue.Queue()
        threading.Thread(target=_read_lines, args=(proc.stdout, lines), daemon=True).start()
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled(stage)
                try:
                    line = lines.get(timeout=0.2)
                except queue.Empty:
                    continue
                if line is None:
                    break
                if line:
                    log.write(line + "\n")
                    log.flush()
                    on_line(line)
            return proc.wait()
        finally:
            _kill_tree(proc)
            proc.stdout.close()


# ---------- output checks: the exit code alone is not reliable (upstream-notes §7) ----------

def check_outputs(trial_dir: Path, stage: str, cameras: list[str], session: Session) -> str | None:
    """None if the stage produced what later stages need, else a plain-language cause."""
    def jsons(folder):
        return all(any((trial_dir / folder / f"{c}_json").glob("*.json")) for c in cameras)

    p3d = trial_dir / "pose-3d"
    if stage == "poseEstimation":
        return None if jsons("pose") else "No body keypoints were found in at least one camera. Is the person " \
            "fully visible in every video?"
    if stage == "synchronization":
        return None if jsons("pose-sync") else "Synchronisation produced no output."
    if stage == "personAssociation":
        return None if jsons("pose-associated") else "The person could not be matched across cameras " \
            "(check calibration and synchronisation)."
    if stage == "triangulation":
        raw = [f for f in p3d.glob("*.trc") if "_filt_" not in f.name and "_LSTM" not in f.name]
        return None if raw else "No 3D positions were computed (check calibration and synchronisation)."
    if stage == "filtering":
        if list(p3d.glob(f"*_filt_{session.settings.filter.type}.trc")):
            return None
        log = trial_dir / LOG_DIR / "filtering.log"
        if log.is_file() and "critical frequencies" in log.read_text(encoding="utf-8", errors="replace"):
            return ("The smoothing cut-off is too high for this video's frame rate: it must be below half the frame "
                    "rate. Lower 'Smoothing cut-off' under Advanced and press Run all again.")
        return "Filtering produced no file."
    if stage == "markerAugmentation":
        return None if list(p3d.glob("*_LSTM.trc")) else "Marker augmentation failed (it needs hips, knees, " \
            "ankles, heels and toes in every frame, and the participant height)."
    if stage == "kinematics":
        kin = trial_dir / "kinematics"
        missing = [ext for ext in (".osim", ".mot", ".bvh")
                   if not list(kin.glob(f"*{ext}"))]
        log = (trial_dir / LOG_DIR / "kinematics.log")
        text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        if "Error during scaling" in text or "Error during IK" in text:
            return "OpenSim scaling or inverse kinematics failed (see mocap_logs/kinematics.log)."
        return f"OpenSim produced no {', '.join(missing)} file." if missing else None
    return None


# ---------- synchronisation QA (FR-4.4) ----------

SYNC_RE = re.compile(r"--> Camera (\S+) and (\S+): (-?\d+) frames offset(?: \(.*?\))?, "
                     r"correlation (-?\d+(?:\.\d+)?|nan)")


def parse_sync(log_text: str) -> dict[str, dict]:
    out = {}
    for ref, cam, offset, corr in SYNC_RE.findall(log_text):
        out[cam] = {"reference": ref, "offset_frames": int(offset),
                    "correlation": float(corr) if corr != "nan" else float("nan")}
    return out


def sync_issues(offsets: dict[str, dict], fps: float) -> list[str]:
    """Problems that stop the trial before 3D until the sync is checked or accepted."""
    issues = []
    if not offsets:
        return ["No synchronisation result was found in the log."]
    for cam, o in offsets.items():
        c = o["correlation"]
        if not c == c or c < SYNC_MIN_CORRELATION:  # NaN or low
            issues.append(f"{cam}: low confidence (correlation {c:.2f}). Was a clear sync cue visible to all cameras?")
    return issues


def sync_warnings(offsets: dict[str, dict], fps: float) -> list[str]:
    """Worth a look, but the run continues: a long offset is normal when the cameras were started one by one."""
    return [f"{cam}: offset {o['offset_frames'] / fps:.1f} s is longer than usual. Fine if the cameras were started "
            f"that far apart; otherwise check the sync." for cam, o in offsets.items()
            if fps and abs(o["offset_frames"]) / fps > SYNC_MAX_OFFSET_S]


def _judge_sync(info: dict, fps: float) -> None:
    """(Re)compute the issues and warnings of a sync result, so older records follow the current rules."""
    if info.get("offsets"):
        info["issues"] = sync_issues(info["offsets"], fps)
        info["warnings"] = sync_warnings(info["offsets"], fps)


# ---------- pre-run checks ----------

def preflight(session: Session, root: Path, labels: list[str]) -> list[Issue]:
    layout = Layout(root)
    issues: list[Issue] = []
    if not is_ascii_path(root):
        issues.append(Issue("error", str(root), "Session path contains non-ASCII letters; OpenSim cannot save there.",
                            "Move the session to a plain-ASCII folder."))
    gone = _missing_models(session.settings.pose_mode, session.settings.keypoints)
    if gone and labels:
        issues.append(Issue("warning", "pose models", f"The {KEYPOINT_TITLES[session.settings.keypoints].lower()} "
                            f"models ({session.settings.pose_mode}) are not downloaded yet. The run downloads them "
                            "first, which needs an internet connection.",
                            "Connect to the internet for the first run, or choose another keypoint set."))
    for label in labels:
        trial = session.trial(label)
        tdir = layout.trial(label)
        where = f"trial {label} ({trial.code})"
        if trial.withdrawn_at:
            continue
        if not trial.raw_present:
            if session.run_state.trial_status.get(label) != "done":
                issues.append(Issue("error", where, f"The raw footage was deleted on {trial.raw_deleted_at}, so "
                                    "this trial can no longer be processed.", "Leave it out of the run."))
            continue
        if not trial.consent.attested:
            issues.append(Issue("action", where, "Signed consent for this participant has not been confirmed.",
                                f"mocapapp consent --session <dir> --trial {label} --attest"))
        if not (tdir / "Config.toml").is_file():
            issues.append(Issue("error", where, "Trial folder not assembled.", "mocapapp assemble --session <dir>"))
            continue
        flags = gui_flags_set((tdir / "Config.toml").read_text(encoding="utf-8"))
        if flags:
            issues.append(Issue("error", where, f"Interactive settings are on: {', '.join(flags)}.",
                                "Re-assemble the trial (mocapapp assemble) to restore the app's settings."))
        tomls = list((tdir / "calibration").glob("*.toml")) if (tdir / "calibration").is_dir() else []
        if len(tomls) != 1:
            issues.append(Issue("error", where, f"Expected one calibration file, found {len(tomls)}.",
                                "Finish calibration (mocapapp calibrate status) and run mocapapp assemble."))
        names = slots(session, label)
        if not names:
            issues.append(Issue("action", where, "No layout chosen for this trial.", "Choose the trial's layout."))
        for name in names:
            if not (tdir / "videos" / f"{name}.mp4").is_file():
                issues.append(Issue("error", where, f"videos/{name}.mp4 is missing.", "mocapapp assemble"))
        if trial.height_m is None:
            issues.append(Issue("action", where, "No height for this participant.", "Enter the measured height."))
        warning = session.layout_warning(label)
        if warning:
            issues.append(Issue("action", where, warning, "Make a new layout, or accept this one for the trial."))
        flt = session.settings.filter
        fps = _fps(tdir, names) if names else 0.0
        if flt.type.startswith("butterworth") and fps > 0 and flt.cutoff_hz >= fps / 2:
            issues.append(Issue("error", where, f"The smoothing cut-off ({flt.cutoff_hz:g} Hz) is too high for videos "
                                f"at {fps:g} frames per second: it must be below half the frame rate ({fps / 2:g} Hz).",
                                f"Set 'Smoothing cut-off' under Advanced to {max_cutoff_hz(fps)} Hz or less "
                                "(6 Hz suits walking)."))
    need = space_needed(session, root, labels)
    if need:
        free = shutil.disk_usage(root).free
        if need > free:
            kept = ("" if session.settings.keep_keypoints else
                    " Finished trials give most of it back (their 2D keypoint files are removed).")
            issues.append(Issue("warning", "disk space",
                                f"The run may need about {need / 1e9:.0f} GB for working files, and "
                                f"{free / 1e9:.0f} GB are free. A full drive stops the run half-way.{kept}",
                                "Free some space, run a few trials at a time, or move the session to a larger drive "
                                "(Session step > Change output folder)."))
    return issues


# Size of one 2D keypoint file (one camera, one frame), measured on long videos where the tracker kept many lost
# people as empty entries (P&A pilot, 0.1.0rc12): the worst case seen, not the usual one.
KEYPOINT_BYTES_PER_FRAME = {"Body_with_feet": 25_000, "Whole_body_wrist": 110_000, "Whole_body": 110_000}
OVERLAY_FACTOR = 1.5  # overlay video size relative to the trial video
RESULTS_BYTES = 100_000_000  # 3D markers, OpenSim model and angles per trial, generous


def space_needed(session: Session, root: Path, labels: list[str]) -> int:
    """Rough bytes the run still writes for these trials (0 when no pose estimation is left to do).

    Per trial: the keypoint files twice (pose and pose-sync stay until the trial finishes), overlay videos when
    switched on and the results; plus one more keypoint copy (pose-associated) for the largest trial."""
    per_frame = KEYPOINT_BYTES_PER_FRAME.get(session.settings.keypoints, 110_000)
    total = biggest = 0
    for label in labels:
        trial = session.trial(label)
        done = session.run_state.stage_done.get(label, [])
        if not trial.raw_present or ("poseEstimation" in done and "poseEstimation" not in stale_stages(session, label)):
            continue
        frames = video_bytes = 0
        for name in slots(session, label):
            video = Layout(root).trial(label) / "videos" / f"{name}.mp4"
            if video.is_file():
                frames += _probe_frames(str(video), video.stat().st_mtime_ns)
                video_bytes += video.stat().st_size
        keypoints = int(frames * per_frame)
        biggest = max(biggest, keypoints)
        total += 2 * keypoints + RESULTS_BYTES + (int(video_bytes * OVERLAY_FACTOR) if session.settings.save_overlay
                                                  else 0)
    return total + biggest if total else 0


def estimate_seconds(session: Session, root: Path, labels: list[str]) -> float:
    """Rough CPU estimate, dominated by pose estimation (videos run in parallel)."""
    from mocapapp.core.media import probe

    total = 0.0
    for label in labels:
        longest = 0.0
        for name in slots(session, label):
            video = Layout(root).trial(label) / "videos" / f"{name}.mp4"
            if video.is_file():
                info = probe(video)
                longest = max(longest, info.duration_s * info.fps)
        total += longest / CPU_FRAMES_PER_SECOND + 240  # + start-up, sync, OpenSim
    return total


# ---------- trial runner ----------

def _fps(trial_dir: Path, cameras: list[str]) -> float:
    video = trial_dir / "videos" / f"{cameras[0]}.mp4"
    try:
        return _probe_fps(str(video), video.stat().st_mtime_ns)
    except Exception:  # missing or unreadable video: callers treat 0 as unknown
        return 0.0


@functools.lru_cache(maxsize=64)
def _probe_fps(video: str, mtime_ns: int) -> float:  # cached: the Run page checks every trial on each refresh
    from mocapapp.core.media import probe

    return probe(Path(video)).fps


@functools.lru_cache(maxsize=256)
def _probe_frames(video: str, mtime_ns: int) -> int:
    from mocapapp.core.media import probe

    try:
        info = probe(Path(video))
    except Exception:  # unreadable video: the stages report it
        return 0
    return int(info.duration_s * info.fps)


def _sync_gate(session: Session, root: Path, label: str, accept: bool, listener: Listener) -> bool:
    """Stop before triangulation when the sync looks unreliable and nobody accepted it (also on resume)."""
    info = session.run_state.sync.setdefault(label, {"offsets": {}, "issues": [], "accepted": False})
    if info.get("offsets") and slots(session, label):
        _judge_sync(info, _fps(Layout(root).trial(label), slots(session, label)))
    if accept and info.get("issues"):
        info["accepted"] = True
    if info.get("issues") and not info.get("accepted"):
        session.run_state.trial_status[label] = "needs_sync_fix"
        save_session(session, root)
        listener(Event("needs_sync_fix", label, "synchronization", "; ".join(info["issues"]), ok=False))
        return False
    save_session(session, root)
    return True


def run_trial(session: Session, root: Path, label: str, listener: Listener = lambda e: None,
              cancel: threading.Event | None = None, resume: bool = True, stages: list[str] | None = None,
              accept_sync: bool = False) -> bool:
    """Run (or resume) one trial. Returns True when every requested stage succeeded."""
    if not session.trial(label).raw_present:
        raise ValueError(f"Trial {label}: the raw footage was deleted, so it cannot be processed again.")
    layout = Layout(root)
    tdir = layout.trial(label)
    cams = slots(session, label)
    rs = session.run_state
    done = rs.stage_done.setdefault(label, [])
    stale = stale_stages(session, label)
    todo = stages or STAGES
    rs.trial_status[label] = "running"
    rs.errors.pop(label, None)
    cleaned = label in rs.keypoints_removed
    if cleaned and resume and "triangulation" in todo and not (
            "triangulation" in done and "triangulation" not in stale
            and check_outputs(tdir, "triangulation", cams, session) is None):
        # 3D must be computed again but the 2D keypoints were removed after the last run: start from the videos
        rs.stage_done[label] = done = []
        cleaned = False
    save_session(session, root)

    def finished(stage: str) -> bool:
        if cleaned and stage in KEYPOINT_STAGES:  # their output was removed on purpose; the 3D results are kept
            return True
        return check_outputs(tdir, stage, cams, session) is None

    for i, stage in enumerate(todo):
        if resume and stage in done and stage not in stale and finished(stage):
            listener(Event("stage_end", label, stage, "already done", ok=True))
            if stage == "synchronization" and not _sync_gate(session, root, label, accept_sync, listener):
                return False
            continue
        # a re-run stage makes every later stage stale
        rs.stage_done[label] = done = [s for s in done if STAGES.index(s) < STAGES.index(stage)]
        if stage == "poseEstimation":
            rs.keypoints_removed.pop(label, None)
            cleaned = False
        listener(Event("stage_start", label, stage, STAGE_TITLES[stage], fraction=i / len(todo)))
        t0 = time.perf_counter()

        bars: dict[str, float] = {}  # progress bar name (text before the percentage) -> its fraction

        def on_line(line: str, stage=stage, i=i, bars=bars) -> None:
            fraction = stage_fraction(line, bars)
            if fraction is not None:
                listener(Event("progress", label, stage, line, fraction=(i + fraction) / len(todo)))
            else:
                listener(Event("line", label, stage, line))

        try:
            code = run_stage(tdir, stage, on_line, cancel)
        except Cancelled:
            rs.trial_status[label] = "cancelled"
            save_session(session, root)
            listener(Event("trial_end", label, stage, "cancelled", ok=False))
            raise
        problem = check_outputs(tdir, stage, cams, session)
        if code and problem is None and stage != "kinematics":
            problem = f"{STAGE_TITLES[stage]} stopped with an error (see {LOG_DIR}/{stage}.log)."
        if problem:
            rs.trial_status[label] = "failed"
            rs.errors[label] = f"{STAGE_TITLES[stage]}: {problem}"
            save_session(session, root)
            listener(Event("stage_end", label, stage, problem, ok=False))
            listener(Event("trial_end", label, stage, rs.errors[label], ok=False))
            return False
        done.append(stage)
        rs.stage_settings.setdefault(label, {})[stage] = stage_inputs(session, stage, label)
        save_session(session, root)
        listener(Event("stage_end", label, stage, f"{time.perf_counter() - t0:.0f} s", ok=True))

        if stage == "synchronization":
            text = (tdir / LOG_DIR / "synchronization.log").read_text(encoding="utf-8", errors="replace")
            offsets = parse_sync(text)
            rs.sync[label] = {"offsets": offsets, "issues": [], "warnings": [], "accepted": False}
            _judge_sync(rs.sync[label], _fps(tdir, cams))
            if not offsets:
                rs.sync[label]["issues"] = sync_issues(offsets, 0.0)
            if not _sync_gate(session, root, label, accept_sync, listener):
                return False

    rs.trial_status[label] = "done" if all(s in done for s in STAGES) else "partial"
    if rs.trial_status[label] == "done" and not session.settings.keep_keypoints:
        freed = remove_keypoints(tdir)
        if label not in rs.keypoints_removed or freed:
            rs.keypoints_removed[label] = datetime.now().isoformat(timespec="seconds")
        if freed:
            listener(Event("line", label, "", f"2D keypoint files removed ({freed / 1e9:.1f} GB freed)."))
    save_session(session, root)
    listener(Event("trial_end", label, "", rs.trial_status[label], ok=True))
    return True


def remove_keypoints(trial_dir: Path) -> int:
    """Delete a finished trial's 2D keypoint folders (pose, pose-sync, pose-associated). Returns the bytes freed.

    Only the 3D stages read them; they are 70-85 % of a trial folder for long videos."""
    from mocapapp.core.deletion import KEYPOINT_FOLDERS, _remove, _size

    freed = 0
    for name in KEYPOINT_FOLDERS:
        folder = trial_dir / name
        if folder.is_dir():
            size = _size(folder)
            _remove(folder)
            freed += size
    return freed


def run_session(session: Session, root: Path, labels: list[str] | None = None, listener: Listener = lambda e: None,
                cancel: threading.Event | None = None, resume: bool = True, accept_sync: bool = False
                ) -> dict[str, str]:
    """Run every selected trial; one failure never stops the others (FR-4.5)."""
    results = {}
    for trial in session.trials:
        if labels and trial.label not in labels:
            continue
        if not trial.raw_present:  # withdrawn, or footage deleted: nothing can be (re)computed
            results[trial.label] = "withdrawn" if trial.withdrawn_at else session.run_state.trial_status.get(
                trial.label, "raw footage deleted")
            continue
        run_trial(session, root, trial.label, listener, cancel, resume, accept_sync=accept_sync)
        results[trial.label] = session.run_state.trial_status[trial.label]
    return results


def fix_sync(session: Session, root: Path, label: str, listener: Listener = lambda e: None) -> bool:
    """Open upstream's synchronisation player for one trial, then accept its result and continue the run."""
    import tomlkit

    tdir = Layout(root).trial(label)
    cfg_path = tdir / "Config.toml"
    original = cfg_path.read_text(encoding="utf-8")
    doc = tomlkit.parse(original)
    doc["synchronization"]["synchronization_gui"] = True
    env_backend = os.environ.get("MPLBACKEND")
    try:
        cfg_path.write_text(tomlkit.dumps(doc), encoding="utf-8")
        os.environ.pop("MPLBACKEND", None)  # the player needs a real window
        code = subprocess.run([sys.executable, "-c", "from Pose2Sim.Pose2Sim import Pose2SimPipeline; "
                               "Pose2SimPipeline().synchronization()"], cwd=tdir,
                              env={k: v for k, v in stage_env().items() if k != "MPLBACKEND"}, check=False).returncode
    finally:
        cfg_path.write_text(original, encoding="utf-8")
        if env_backend is not None:
            os.environ["MPLBACKEND"] = env_backend
    if code:
        return False
    rs = session.run_state
    rs.sync.setdefault(label, {})["accepted"] = True
    rs.stage_done[label] = [s for s in rs.stage_done.get(label, []) if s in ("poseEstimation", "synchronization")]
    if "synchronization" not in rs.stage_done[label]:
        rs.stage_done[label].append("synchronization")
    save_session(session, root)
    return run_trial(session, root, label, listener, accept_sync=True)
