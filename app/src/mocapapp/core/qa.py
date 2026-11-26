"""Quality report per trial (FR-4.8): metrics from the stage logs and outputs, a traffic light, HTML + JSON, and a
self-contained 3D skeleton preview so obviously wrong results are seen before export.

Thresholds are first guesses from the demo (decisions.md); they are shown in the report so readers can judge.
"""

from __future__ import annotations

import functools
import html
import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from mocapapp.core.runner import LOG_DIR, STAGES, _fps, parse_sync, slots, sync_issues, sync_warnings
from mocapapp.core.session import Layout, Session, atomic_write_text

# (green up to, amber up to); above = red. Correlation is "higher is better".
THRESHOLDS = {
    "reprojection_mm": (20.0, 40.0),
    "missing_fraction": (0.05, 0.20),
    "ik_rms_mm": (30.0, 50.0),
    "floor_offset_m": (0.05, 0.15),
    "sync_min_correlation": (0.6, 0.4),
}
FOOT_MARKERS = ("RHeel", "LHeel", "RBigToe", "LBigToe", "RSmallToe", "LSmallToe")
BONES = [("Hip", "RHip"), ("Hip", "LHip"), ("RHip", "RKnee"), ("RKnee", "RAnkle"), ("RAnkle", "RHeel"),
         ("RAnkle", "RBigToe"), ("RBigToe", "RSmallToe"), ("LHip", "LKnee"), ("LKnee", "LAnkle"), ("LAnkle", "LHeel"),
         ("LAnkle", "LBigToe"), ("LBigToe", "LSmallToe"), ("Hip", "Neck"), ("Neck", "Head"), ("Head", "Nose"),
         ("Neck", "RShoulder"), ("RShoulder", "RElbow"), ("RElbow", "RWrist"), ("Neck", "LShoulder"),
         ("LShoulder", "LElbow"), ("LElbow", "LWrist")]


@dataclass
class Metric:
    name: str
    value: float | None
    unit: str
    light: str  # green | amber | red | grey (not available)
    note: str = ""


@dataclass
class TrialQA:
    label: str
    code: str
    status: str
    light: str
    metrics: list[Metric] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sync: dict = field(default_factory=dict)
    preview: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def light(name: str, value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "grey"
    g, a = THRESHOLDS[name]
    if g > a:  # higher is better
        return "green" if value >= g else "amber" if value >= a else "red"
    return "green" if value <= g else "amber" if value <= a else "red"


def worst(lights: list[str]) -> str:
    order = ["red", "amber", "green"]
    present = [x for x in lights if x != "grey"]
    return min(present, key=order.index) if present else "grey"


# ---------- readers ----------

def number_grid(lines: list[str], width: int) -> np.ndarray:
    """Tab-separated rows of numbers -> floats [rows, width]. Empty, 'nan' or unreadable values are NaN, short rows
    are padded, blank lines skipped. pandas' C parser: a long trial's .trc holds millions of values, which took
    seconds one by one in Python and froze the app (0.2.0rc3)."""
    import csv
    import io

    import pandas as pd

    rows = [r for r in lines if r.strip()]
    if not rows or width <= 0:
        return np.empty((len(rows), max(width, 0)))
    n = max(width, max(r.count("\t") for r in rows) + 1)
    frame = pd.read_csv(io.StringIO("\n".join(rows)), sep="\t", header=None, names=range(n), usecols=range(width),
                        quoting=csv.QUOTE_NONE, low_memory=False)
    text = [c for c in frame.columns if not pd.api.types.is_numeric_dtype(frame[c])]
    if text:  # a column with something that is not a number in it: that becomes NaN
        frame[text] = frame[text].apply(pd.to_numeric, errors="coerce")
    return frame.to_numpy(dtype=float)


def read_trc(path: Path) -> tuple[list[str], np.ndarray, float]:
    """(marker names, data [frames, markers, 3], frame rate). The data is read-only: the last two files read are
    kept, as the report reads a trial's file twice (its floor, then its preview)."""
    st = Path(path).stat()
    names, data, rate = _read_trc_of(str(path), st.st_mtime_ns, st.st_size)
    return list(names), data, rate


@functools.lru_cache(maxsize=2)
def _read_trc_of(path: str, _mtime_ns: int, _size: int) -> tuple[tuple[str, ...], np.ndarray, float]:
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    rate = float(lines[2].split("\t")[0])
    names = [n for n in lines[3].split("\t")[2:] if n.strip()]
    data = number_grid(lines[5:], 2 + 3 * len(names))[:, 2:].reshape(-1, len(names), 3)
    data.flags.writeable = False
    return tuple(names), data, rate


def read_sto(path: Path):
    import pandas as pd

    text = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    start = next(i for i, line in enumerate(text) if line.strip() == "endheader") + 1
    return pd.read_csv(path, sep="\t", skiprows=start)


def _log(trial_dir: Path, stage: str) -> str:
    path = trial_dir / LOG_DIR / f"{stage}.log"
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


REPROJ_RE = re.compile(r"Mean reprojection error for all points on frames (\d+) to (\d+) is ([\d.]+) px, which "
                       r"roughly corresponds to ([\d.]+) mm")
INTERP_RE = re.compile(r"^\s*Frames (.+) were (not )?interpolated\.", re.M)
KEYPOINT_RE = re.compile(r"Mean reprojection error for (\w+) is ([\d.]+) px \(~ ([\d.]+) m\), reached with ([\d.]+) "
                         r"excluded cameras")


def _count_frames(spec: str) -> int:
    n = 0
    for part in spec.split(","):
        nums = [int(x) for x in re.findall(r"\d+", part)]
        n += nums[1] - nums[0] + 1 if len(nums) >= 2 else 1 if nums else 0
    return n


# ---------- metrics ----------

def trial_qa(session: Session, root: Path, label: str) -> TrialQA:
    layout = Layout(root)
    tdir = layout.trial(label)
    trial = session.trial(label)
    status = session.run_state.trial_status.get(label, "pending")
    qa = TrialQA(label, trial.code, status, "grey")
    if label in session.run_state.errors:
        qa.warnings.append(session.run_state.errors[label])
    n_cams = len(session.trial_cameras(label))

    # synchronisation
    sync = parse_sync(_log(tdir, "synchronization"))
    own = tdir / "logs.txt"  # Pose2Sim's own log: its last synchronisation is the latest, also one made in the
    if own.is_file():        # player by an app before 0.2.0rc2 (which did not keep the player's log)
        sync = parse_sync(own.read_text(encoding="utf-8", errors="replace")) or sync
    qa.sync = sync
    if sync:
        min_corr = min(o["correlation"] for o in sync.values())
        qa.metrics.append(Metric("Sync confidence (lowest camera)", round(min_corr, 2), "correlation",
                                 light("sync_min_correlation", min_corr),
                                 ", ".join(f"{c}: {o['offset_frames']:+d} frames" for c, o in sync.items())))
        cams = slots(session, label)
        fps = _fps(tdir, cams) if cams else 0.0
        for issue in sync_issues(sync, fps) + sync_warnings(sync, fps):
            qa.warnings.append(f"Synchronisation: {issue}")

    # triangulation
    tri = _log(tdir, "triangulation")
    m = REPROJ_RE.search(tri)
    if m:
        first, last, px, mm = int(m.group(1)), int(m.group(2)), float(m.group(3)), float(m.group(4))
        qa.metrics.append(Metric("3D reprojection error", mm, "mm", light("reprojection_mm", mm), f"{px} px"))
        n_frames = last - first + 1
        missing = [_count_frames(spec) for spec, _ in INTERP_RE.findall(tri)]
        kp = KEYPOINT_RE.findall(tri)
        if kp:
            frac = sum(missing) / (len(kp) * n_frames) if n_frames else 0.0
            qa.metrics.append(Metric("Frames without 2+ cameras (interpolated or filled)", round(frac, 4), "share",
                                     light("missing_fraction", frac), f"{len(kp)} keypoints x {n_frames} frames"))
            used = np.mean([n_cams - float(x[3]) for x in kp])
            qa.metrics.append(Metric("Cameras used per keypoint (mean)", round(float(used), 2), f"of {n_cams}",
                                     "grey"))
            worst_kp = max(kp, key=lambda x: float(x[3]))
            if float(worst_kp[3]) > n_cams / 2:
                qa.warnings.append(f"{worst_kp[0]} was usually seen by fewer than half the cameras "
                                   f"({float(worst_kp[3]):.1f} of {n_cams} excluded on average).")

    # inverse kinematics
    sto = tdir / "kinematics" / "_ik_marker_errors.sto"
    if sto.is_file():
        df = read_sto(sto)
        rms_mm = float(df["marker_error_RMS"].mean() * 1000)
        qa.metrics.append(Metric("Model fit (IK marker RMS, mean)", round(rms_mm, 1), "mm", light("ik_rms_mm", rms_mm),
                                 f"max {df['marker_error_max'].max() * 1000:.0f} mm"))

    # floor and axes sanity, output frame is Y-up (upstream-notes §4)
    trc = best_trc(tdir)
    if trc is not None:
        offset, up = _floor(trc)
        if offset is not None:
            qa.metrics.append(Metric("Feet height above floor (median of lowest foot point)", round(offset, 3), "m",
                                     light("floor_offset_m", abs(offset)),
                                     "should be near 0 if the floor is Z = 0 in the reference points"))
            if up is not None and up < 0:
                qa.warnings.append("The head is below the feet: the vertical axis is flipped. Check that Z is up in "
                                   "the reference points.")
        preview = layout.qa / f"trial_{label}_preview.html"
        qa.preview = str(preview if _up_to_date(preview, trc) else write_preview(tdir, trc, preview))

    lights = [m.light for m in qa.metrics]
    if status in ("failed", "needs_sync_fix", "cancelled"):
        lights.append("red")
    qa.light = worst(lights)
    return qa


def _floor(trc: Path) -> tuple[float | None, float | None]:
    """(feet height above the floor, head height above the feet) of a trial's .trc file, read once per version
    of it: kept in memory, and in the trial's mocap_logs/floor.json for the next start of the app."""
    st = trc.stat()
    key = {"file": trc.name, "mtime_ns": st.st_mtime_ns, "size": st.st_size}
    saved_at = trc.parent.parent / LOG_DIR / "floor.json"
    try:
        saved = json.loads(saved_at.read_text(encoding="utf-8"))
        if {k: saved.get(k) for k in key} == key:
            return saved["offset"], saved["up"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass  # not saved yet, or for another version of the file
    offset, up = _floor_of(str(trc), st.st_mtime_ns, st.st_size)
    try:
        saved_at.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(saved_at, json.dumps({**key, "offset": offset, "up": up}))
    except OSError:
        pass  # only a shortcut: the next report reads the file again
    return offset, up


@functools.lru_cache(maxsize=64)
def _floor_of(path: str, _mtime_ns: int, _size: int) -> tuple[float | None, float | None]:
    # the report is made again after every change in the project; reading a long trial's .trc takes seconds
    names, data, _rate = read_trc(Path(path))
    feet = [names.index(n) for n in FOOT_MARKERS if n in names]
    if not feet:
        return None, None
    lowest = np.nanmin(data[:, feet, 1], axis=1)
    up = float(np.nanmedian(data[:, names.index("Head"), 1] - lowest)) if "Head" in names else None
    return float(np.nanmedian(lowest)), up


def _up_to_date(preview: Path, trc: Path) -> bool:
    """The 3D preview was made after this version of the .trc file. (If the preview page itself ever changes,
    give the new one another file name, so that the old ones are made again.)"""
    try:
        return preview.stat().st_mtime_ns >= trc.stat().st_mtime_ns
    except OSError:
        return False


def best_trc(trial_dir: Path) -> Path | None:
    p3d = trial_dir / "pose-3d"
    for pattern in ("*_filt_*_LSTM.trc", "*_filt_*.trc", "*.trc"):
        found = sorted(p3d.glob(pattern)) if p3d.is_dir() else []
        if found:
            return found[0]
    return None


# ---------- 3D preview ----------

def write_preview(trial_dir: Path, trc: Path, out: Path, max_fps: float = 30) -> Path:
    names, data, rate = read_trc(trc)
    keep = [i for i, n in enumerate(names) if not n.endswith("_study")]  # body keypoints only
    step = max(1, round(rate / max_fps))
    frames = np.round(data[::step][:, keep, :], 3)
    payload = {"names": [names[i] for i in keep], "fps": rate / step,
               "frames": np.where(np.isnan(frames), None, frames).tolist(),
               "bones": [[a, b] for a, b in BONES if a in names and b in names], "source": trc.name}
    page = PREVIEW_HTML.replace("/*DATA*/", json.dumps(payload, separators=(",", ":")))
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, page)
    return out


PREVIEW_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>3D preview</title>
<style>body{font:14px system-ui,sans-serif;margin:16px;background:#fafafa;color:#222}
canvas{background:#fff;border:1px solid #ccc;max-width:100%}label{margin-right:12px}</style></head><body>
<h1 style="font-size:18px">3D skeleton preview</h1><p id="src"></p>
<canvas id="c" width="720" height="540"></canvas><div>
<button id="play">Pause</button> <label>View angle <input id="yaw" type="range" min="-180" max="180" value="30"></label>
<label>Frame <input id="f" type="range" min="0" value="0"></label> <span id="t"></span></div>
<p>Y is up (OpenSim frame). The grid is the floor (Y = 0), 0.5 m squares. Look for limbs that jump, swap sides or
stretch, and for feet that float above or sink below the floor.</p>
<script>const D=/*DATA*/;const c=document.getElementById('c'),g=c.getContext('2d');
const f=document.getElementById('f'),yaw=document.getElementById('yaw'),t=document.getElementById('t');
f.max=D.frames.length-1;document.getElementById('src').textContent=D.source;let playing=true,i=0;
const idx=Object.fromEntries(D.names.map((n,k)=>[n,k]));
let cx=0,cz=0,n=0;for(const fr of D.frames)for(const p of fr)if(p&&p[0]!==null){cx+=p[0];cz+=p[2];n++}cx/=n;cz/=n;
function P(p){const a=yaw.value*Math.PI/180,x=p[0]-cx,z=p[2]-cz,X=x*Math.cos(a)-z*Math.sin(a),
Z=x*Math.sin(a)+z*Math.cos(a),s=180/(1+0.12*Z);return[c.width/2+X*s,c.height*0.85-p[1]*s]}
function draw(){g.clearRect(0,0,c.width,c.height);g.strokeStyle='#ddd';
function L(a,b){g.beginPath();g.moveTo(...a);g.lineTo(...b);g.stroke()}
for(let k=-4;k<=4;k++){L(P([cx+k*0.5,0,cz-2]),P([cx+k*0.5,0,cz+2]));L(P([cx-2,0,cz+k*0.5]),P([cx+2,0,cz+k*0.5]))}
const fr=D.frames[i];g.lineWidth=3;for(const[a,b]of D.bones){const p=fr[idx[a]],q=fr[idx[b]];
if(!p||!q||p[0]===null||q[0]===null)continue;
g.strokeStyle=(a[0]==='L'||b[0]==='L')?'#1f77b4':(a[0]==='R'||b[0]==='R')?'#d62728':'#444';
g.beginPath();g.moveTo(...P(p));g.lineTo(...P(q));g.stroke()}g.lineWidth=1;
t.textContent=`frame ${i+1}/${D.frames.length}  (blue = left, red = right)`;f.value=i}
function tick(){if(playing){i=(i+1)%D.frames.length;draw()}}setInterval(tick,1000/D.fps);
document.getElementById('play').onclick=e=>{playing=!playing;e.target.textContent=playing?'Pause':'Play'};
f.oninput=()=>{i=+f.value;draw()};yaw.oninput=draw;draw();</script></body></html>
"""


# ---------- report ----------

LIGHT_TEXT = {"green": "GOOD", "amber": "CHECK", "red": "PROBLEM", "grey": "n/a"}
LIGHT_COLOR = {"green": "#2e7d32", "amber": "#b26a00", "red": "#c62828", "grey": "#777"}


def write_report(session: Session, root: Path, labels: list[str] | None = None) -> tuple[Path, list[TrialQA]]:
    layout = Layout(root)
    results = [trial_qa(session, root, t.label) for t in session.active_trials
               if not labels or t.label in labels]
    layout.qa.mkdir(parents=True, exist_ok=True)
    atomic_write_text(layout.qa / "qa.json", json.dumps({
        "thresholds": THRESHOLDS, "trials": [r.to_dict() for r in results]}, indent=2) + "\n")

    def badge(lt):
        return (f'<span style="background:{LIGHT_COLOR[lt]};color:#fff;padding:1px 6px;border-radius:3px">'
                f'{LIGHT_TEXT[lt]}</span>')

    parts = ["<!doctype html><html lang='en'><head><meta charset='utf-8'><title>QA report</title>",
             "<style>body{font:14px system-ui,sans-serif;margin:16px;max-width:900px}td,th{padding:4px 8px;"
             "text-align:left;border-bottom:1px solid #ddd}table{border-collapse:collapse}</style></head><body>",
             f"<h1>Quality report</h1><p>Session {html.escape(root.name)}. Stages: {', '.join(STAGES)}.</p>"]
    for r in results:
        parts.append(f"<h2>Trial {html.escape(r.label)} ({html.escape(r.code)}) {badge(r.light)}</h2>"
                     f"<p>Status: {html.escape(r.status)}</p><table><tr><th>Check</th><th>Value</th><th></th>"
                     "<th>Note</th></tr>")
        for m in r.metrics:
            val = "n/a" if m.value is None else f"{m.value:.1%}" if m.unit == "share" else f"{m.value} {m.unit}"
            parts.append(f"<tr><td>{html.escape(m.name)}</td><td>{html.escape(val)}</td><td>{badge(m.light)}</td>"
                         f"<td>{html.escape(m.note)}</td></tr>")
        parts.append("</table>")
        if r.warnings:
            parts.append("<ul>" + "".join(f"<li>{html.escape(w)}</li>" for w in r.warnings) + "</ul>")
        if r.preview:
            parts.append(f"<p><a href='{Path(r.preview).name}'>Open the 3D preview</a></p>")
    parts.append("<h3>Thresholds (good / check)</h3><ul>" + "".join(
        f"<li>{k}: {v[0]} / {v[1]}</li>" for k, v in THRESHOLDS.items()) + "</ul></body></html>")
    path = layout.qa / "qa_report.html"
    atomic_write_text(path, "\n".join(parts))
    return path, results
