"""Export selected outputs with self-explanatory names, a README and a manifest (PROJECT.md §6.7)."""

from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from importlib import metadata
from pathlib import Path

from mocapapp import CITATION, __version__
from mocapapp.core.session import Layout, Session, atomic_write_text


@dataclass(frozen=True)
class Kind:
    key: str
    default: bool
    folder: str
    pattern: str
    exclude: tuple[str, ...]
    name: str  # used in the export file name
    describe: str


def kinds(filter_type: str = "butterworth") -> list[Kind]:
    f = filter_type
    lstm_note = "LSTM-augmented markers (anatomical markers predicted from the keypoints)"
    return [
        Kind("config", True, ".", "Config.toml", (), "config", "Pose2Sim settings used for this trial."),
        Kind("trc.raw", False, "pose-3d", "*.trc", ("_filt_", "_LSTM"), "markers_raw",
             "3D keypoints straight from triangulation (no smoothing). Y up, metres."),
        Kind("trc.filt", False, "pose-3d", f"*_filt_{f}.trc", ("_LSTM",), f"markers_{f}",
             f"3D keypoints after outlier rejection and {f} filtering. Y up, metres."),
        Kind("trc.filt_lstm", True, "pose-3d", f"*_filt_{f}_LSTM.trc", (), f"markers_{f}-lstm",
             f"Filtered keypoints plus {lstm_note}. Y up, metres."),
        Kind("c3d.raw", False, "pose-3d", "*.c3d", ("_filt_", "_LSTM"), "markers_raw", "Same as markers_raw, C3D."),
        Kind("c3d.filt", False, "pose-3d", f"*_filt_{f}.c3d", ("_LSTM",), f"markers_{f}", "Same, C3D."),
        Kind("c3d.filt_lstm", True, "pose-3d", f"*_filt_{f}_LSTM.c3d", (), f"markers_{f}-lstm", "Same, C3D."),
        Kind("osim", True, "kinematics", "*.osim", (), "model_scaled",
             "OpenSim model scaled to this participant."),
        Kind("mot", True, "kinematics", "*.mot", (), "joint_angles",
             "Joint angles over time (OpenSim inverse kinematics)."),
        Kind("bvh", False, "kinematics", "*.bvh", (), "skeleton_animation",
             "Skeleton animation for Blender/MotionBuilder, converted from the OpenSim results."),
    ]


def default_selection(filter_type: str = "butterworth") -> list[str]:
    return [k.key for k in kinds(filter_type) if k.default]


def find_files(trial_dir: Path, kind: Kind) -> list[Path]:
    folder = trial_dir / kind.folder
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob(kind.pattern) if not any(x in p.name for x in kind.exclude))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check_bvh(bvh: Path, mot: Path | None = None) -> list[str]:
    """Structural check (Blender's importer needs HIERARCHY, MOTION, a frame count and matching data rows)."""
    text = bvh.read_text(encoding="utf-8", errors="replace")
    problems = []
    if not text.startswith("HIERARCHY"):
        problems.append("does not start with HIERARCHY")
    m = re.search(r"^MOTION\s*\nFrames:\s*(\d+)\s*\nFrame Time:\s*([\d.eE+-]+)\s*\n", text, re.M)
    if not m:
        return problems + ["MOTION header missing or malformed"]
    n_frames = int(m.group(1))
    channels = sum(int(x) for x in re.findall(r"CHANNELS\s+(\d+)", text))
    rows = [r for r in text[m.end():].splitlines() if r.strip()]
    if len(rows) != n_frames:
        problems.append(f"header says {n_frames} frames but {len(rows)} rows follow")
    if rows and len(rows[0].split()) != channels:
        problems.append(f"{len(rows[0].split())} values per frame, {channels} channels declared")
    if mot is not None and mot.is_file():
        nrows = re.search(r"nRows=(\d+)", mot.read_text(encoding="utf-8", errors="replace")[:2000])
        if nrows and abs(int(nrows.group(1)) - n_frames) > 1:
            problems.append(f"{n_frames} frames vs {nrows.group(1)} rows in {mot.name}")
    return problems


def _versions() -> dict:
    out = {"mocapapp": __version__, "python": platform.python_version(), "platform": platform.platform()}
    for pkg in ("pose2sim", "opensim", "onnxruntime", "rtmlib", "opencv-python", "numpy", "torch"):
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            pass
    return out


def export(session: Session, root: Path, selection: list[str], labels: list[str] | None = None,
           qa_summary: dict | None = None) -> tuple[Path, list[str]]:
    """Copy the selected files to export/. Returns (folder, warnings)."""
    layout = Layout(root)
    out = layout.export
    out.mkdir(parents=True, exist_ok=True)
    table = {k.key: k for k in kinds(session.settings.filter.type)}
    unknown = [s for s in selection if s not in table]
    if unknown:
        raise ValueError(f"Unknown export items: {', '.join(unknown)}. Choose from: {', '.join(table)}.")
    warnings: list[str] = []
    files: list[dict] = []
    for trial in session.trials:
        if labels and trial.label not in labels:
            continue
        tdir = layout.trial(trial.label)
        for key in selection:
            kind = table[key]
            found = find_files(tdir, kind)
            if not found:
                warnings.append(f"trial {trial.label}: no {key} file found.")
                continue
            for n, src in enumerate(found):
                suffix = f"_{n + 1}" if len(found) > 1 else ""
                ext = src.suffix if key != "config" else ".toml"
                dst = out / f"{trial.code}_trial{trial.label}_{kind.name}{suffix}{ext}"
                shutil.copy2(src, dst)
                entry = {"file": dst.name, "kind": key, "trial": trial.label, "code": trial.code,
                         "source": str(src.relative_to(root)), "sha256": sha256(dst)}
                if key in ("osim", "mot", "bvh"):
                    entry["kinematics_source"] = ("LSTM-augmented markers" if session.settings.use_augmentation
                                                  else "filtered keypoints (no augmentation)")
                if key == "bvh":
                    mot = next(iter(find_files(tdir, table["mot"])), None)
                    problems = check_bvh(dst, mot)
                    entry["bvh_check"] = problems or "ok"
                    warnings += [f"{dst.name}: {p}" for p in problems]
                files.append(entry)
    _write_readme(out, session, selection, table)
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "session": root.name,
        "versions": {**_versions(), "pose2sim_commit": session.pose2sim_commit},
        "settings": session.settings.model_dump(),
        "files": files,
        "qa": qa_summary or {},
        "citation": CITATION,
        "deletions": _previous_deletions(out),
    }
    atomic_write_text(out / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    return out, warnings


def _previous_deletions(out: Path) -> list:
    path = out / "manifest.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("deletions", [])
        except ValueError:
            return []
    return []


def _write_readme(out: Path, session: Session, selection: list[str], table: dict[str, Kind]) -> None:
    src = "LSTM-augmented markers" if session.settings.use_augmentation else "filtered keypoints"
    lines = [
        "Exported motion capture results (research use only)",
        "=" * 52, "",
        "File names: <trial code>_trial<label>_<content>.<ext>. The trial code is the participant's pseudonym",
        "(pseudonymised, not anonymous); the key linking it to a person is kept separately by the experimenter.", "",
        "Coordinate frame of .trc/.c3d: Y up, metres. Scene axes map as X_out = scene Y, Y_out = scene Z (up),",
        "Z_out = scene X.", "",
        f"Kinematics (.osim, .mot, .bvh) were computed from {src}.", "",
        "Contents:",
    ]
    for key in selection:
        k = table[key]
        lines.append(f"  *_{k.name}{'.toml' if key == 'config' else '.' + key.split('.')[0]}: {k.describe}")
    lines += ["", "manifest.json lists every file with its SHA-256 hash, the software versions and settings.", "",
              "Please cite:", CITATION]
    atomic_write_text(out / "README.txt", "\n".join(lines) + "\n")
