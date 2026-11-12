"""Run the Pose2Sim stages for each trial in subprocesses: progress, cancel, resume, failure isolation.

Each stage runs as `Pose2SimPipeline().<stage>()` with cwd = the trial folder (upstream-notes §1) and a UTF-8,
non-interactive environment (§9, §10). Stage output goes to `<trial>/mocap_logs/<stage>.log` (the folder name must
not contain 'calib', upstream-notes §1).
"""

from __future__ import annotations

import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mocapapp.core.config_gen import gui_flags_set
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
SYNC_MIN_CORRELATION = 0.5
SYNC_MAX_OFFSET_S = 5.0
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
        return None if list(p3d.glob(f"*_filt_{session.settings.filter.type}.trc")) else "Filtering produced no file."
    if stage == "markerAugmentation":
        return None if list(p3d.glob("*_LSTM.trc")) else "Marker augmentation failed (it needs hips, knees, " \
            "ankles, heels and toes in every frame, and the participant height)."
    if stage == "kinematics":
        kin = trial_dir / "kinematics"
        missing = [ext for ext in ([".osim", ".mot"] + ([".bvh"] if session.settings.make_bvh else []))
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
    issues = []
    if not offsets:
        return ["No synchronisation result was found in the log."]
    for cam, o in offsets.items():
        c = o["correlation"]
        if not c == c or c < SYNC_MIN_CORRELATION:  # NaN or low
            issues.append(f"{cam}: low confidence (correlation {c:.2f}). Was a clear sync cue visible to all cameras?")
        if fps and abs(o["offset_frames"]) / fps > SYNC_MAX_OFFSET_S:
            issues.append(f"{cam}: offset {o['offset_frames'] / fps:.1f} s looks implausible.")
    return issues


# ---------- pre-run checks ----------

def preflight(session: Session, root: Path, labels: list[str]) -> list[Issue]:
    layout = Layout(root)
    issues: list[Issue] = []
    if not is_ascii_path(root):
        issues.append(Issue("error", str(root), "Session path contains non-ASCII letters; OpenSim cannot save there.",
                            "Move the session to a plain-ASCII folder."))
    if not session.sync_cue_confirmed:
        issues.append(Issue("action", "sync cue", "Confirm that a sync cue was recorded at the start of every trial.",
                            "mocapapp confirm --session <dir> --sync-cue"))
    for label in labels:
        trial = session.trial(label)
        tdir = layout.trial(label)
        where = f"trial {label} ({trial.code})"
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
        for cam in session.cameras:
            if not (tdir / "videos" / f"{cam.name}.mp4").is_file():
                issues.append(Issue("error", where, f"videos/{cam.name}.mp4 is missing.", "mocapapp assemble"))
        if trial.height_m is None:
            issues.append(Issue("warning", where, "No participant height: it will be estimated (less accurate).",
                                "Enter the measured height."))
    return issues


def estimate_seconds(session: Session, root: Path, labels: list[str]) -> float:
    """Rough CPU estimate, dominated by pose estimation (videos run in parallel)."""
    from mocapapp.core.media import probe

    total = 0.0
    for label in labels:
        longest = 0.0
        for cam in session.cameras:
            video = Layout(root).trial(label) / "videos" / f"{cam.name}.mp4"
            if video.is_file():
                info = probe(video)
                longest = max(longest, info.duration_s * info.fps)
        total += longest / CPU_FRAMES_PER_SECOND + 240  # + start-up, sync, OpenSim
    return total


# ---------- trial runner ----------

def _fps(trial_dir: Path, cameras: list[str]) -> float:
    from mocapapp.core.media import probe

    try:
        return probe(trial_dir / "videos" / f"{cameras[0]}.mp4").fps
    except Exception:  # fps is only needed to judge sync offsets
        return 0.0


def _sync_gate(session: Session, root: Path, label: str, accept: bool, listener: Listener) -> bool:
    """Stop before triangulation when the sync looks unreliable and nobody accepted it (also on resume)."""
    info = session.run_state.sync.setdefault(label, {"offsets": {}, "issues": [], "accepted": False})
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
    layout = Layout(root)
    tdir = layout.trial(label)
    cams = [c.name for c in session.cameras]
    rs = session.run_state
    done = rs.stage_done.setdefault(label, [])
    todo = stages or STAGES
    rs.trial_status[label] = "running"
    rs.errors.pop(label, None)
    save_session(session, root)

    for i, stage in enumerate(todo):
        if resume and stage in done and check_outputs(tdir, stage, cams, session) is None:
            listener(Event("stage_end", label, stage, "already done", ok=True))
            if stage == "synchronization" and not _sync_gate(session, root, label, accept_sync, listener):
                return False
            continue
        # a re-run stage makes every later stage stale
        rs.stage_done[label] = done = [s for s in done if STAGES.index(s) < STAGES.index(stage)]
        listener(Event("stage_start", label, stage, STAGE_TITLES[stage], fraction=i / len(todo)))
        t0 = time.perf_counter()

        def on_line(line: str, stage=stage, i=i) -> None:
            m = TQDM_RE.search(line)
            if m:
                listener(Event("progress", label, stage, line, fraction=(i + int(m.group(1)) / 100) / len(todo)))
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
        save_session(session, root)
        listener(Event("stage_end", label, stage, f"{time.perf_counter() - t0:.0f} s", ok=True))

        if stage == "synchronization":
            text = (tdir / LOG_DIR / "synchronization.log").read_text(encoding="utf-8", errors="replace")
            offsets = parse_sync(text)
            rs.sync[label] = {"offsets": offsets, "issues": sync_issues(offsets, _fps(tdir, cams)), "accepted": False}
            if not _sync_gate(session, root, label, accept_sync, listener):
                return False

    rs.trial_status[label] = "done" if all(s in done for s in STAGES) else "partial"
    save_session(session, root)
    listener(Event("trial_end", label, "", rs.trial_status[label], ok=True))
    return True


def run_session(session: Session, root: Path, labels: list[str] | None = None, listener: Listener = lambda e: None,
                cancel: threading.Event | None = None, resume: bool = True, accept_sync: bool = False
                ) -> dict[str, str]:
    """Run every selected trial; one failure never stops the others (FR-4.5)."""
    results = {}
    for trial in session.trials:
        if labels and trial.label not in labels:
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
