"""Deleting participant data with the safeguards of PROJECT.md §8.4 (D8, D14).

Every deletion is planned first (exact paths and sizes, grouped), then executed only on explicit confirmation.
Files are really deleted (not moved to the Recycle Bin / Trash). Normal deletion does not overwrite the data on
disk; SSD wear-levelling, backups and cloud sync can keep copies (see docs/privacy.md).
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from mocapapp.core.session import Layout, Session, atomic_write_text, save_session
from mocapapp.core.validate import in_cloud_folder

Scope = Literal["raw", "participant", "session"]
Group = Literal["working", "derived", "originals", "results", "session"]

GROUP_TITLES: dict[str, str] = {
    "working": "(a) Working copies of the trial videos",
    "derived": "(b) Material derived from the videos that shows or traces the person: overlay videos/images "
               "(faces are blurred, but not reliably) and 2D keypoint files",
    "originals": "(c) Original recordings in the camera folders",
    "results": "Results of this participant: 3D markers, joint angles, model, QA and exported files",
    "session": "The whole session folder (every trial, results, calibration, logs)",
}
VIDEO_OR_IMAGE = {".mp4", ".mov", ".avi", ".mkv", ".png", ".jpg", ".jpeg"}
KEYPOINT_FOLDERS = ("pose", "pose-sync", "pose-associated")
DELETION_LOG = "manifest.json"
SESSION_ENTRIES = {"session.json", "scene.json", "inputs", "calibration_work", "trials", "qa", "export",
                   "manifest.json"}  # session root (§5.1); mirrored into export/manifest.json

CHECKLIST = [
    "Delete the recordings from the cameras' SD cards and any phones used for filming.",
    "Delete copies in cloud backups or sync folders (OneDrive, Dropbox, Google Drive, iCloud) and in their "
    "online recycle bins.",
    "Delete copies on external drives, USB sticks or other computers.",
    "Empty nothing else: this app already deleted the files directly (not to the Recycle Bin).",
]
KEPT_NOTE = ("Kept: the intrinsic and extrinsic calibration videos, 3D markers (.trc/.c3d), joint angles (.mot), "
             "the scaled model (.osim), animations (.bvh), settings and quality reports. If a person is visible in "
             "a calibration video, delete that video by hand.")


class DeletionError(Exception):
    """A deletion was refused (missing confirmation, unmet safeguard). The message says what to do."""


@dataclass
class Item:
    group: str
    path: Path
    size: int
    is_dir: bool = False
    camera: str = ""  # originals only (the log never stores their paths)


@dataclass
class DeletionPlan:
    scope: Scope
    root: Path
    labels: list[str]
    codes: list[str]
    items: list[Item] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)  # unmet safeguards: execute() refuses
    confirm_text: str | None = None  # must be typed to go ahead (trial code, or the session folder name)
    notes: list[str] = field(default_factory=list)

    @property
    def includes_originals(self) -> bool:
        return any(i.group == "originals" for i in self.items)

    def size(self, group: str | None = None) -> int:
        return sum(i.size for i in self.items if group is None or i.group == group)

    def groups(self) -> list[str]:
        return [g for g in GROUP_TITLES if any(i.group == g for i in self.items)]

    def describe(self) -> str:
        """Plain-text list shown before anything is deleted (CLI; the GUI renders the same items)."""
        who = ", ".join(f"trial {lbl} ({code})" for lbl, code in zip(self.labels, self.codes, strict=True))
        lines = [f"Deletion plan for {who or self.root.name}:"]
        if not self.items:
            lines.append("  Nothing left to delete.")
        for g in self.groups():
            items = [i for i in self.items if i.group == g]
            lines.append(f"{GROUP_TITLES[g]} - {len(items)} item(s), {human_size(self.size(g))}:")
            lines += [f"    {human_size(i.size):>9}  {i.path}{os.sep if i.is_dir else ''}" for i in items]
        lines.append(f"Total: {human_size(self.size())}. Files are deleted permanently, not moved to the Recycle Bin.")
        lines += [f"Note: {n}" for n in self.notes]
        lines += [f"Warning: {w}" for w in self.warnings]
        lines += [f"Cannot delete yet: {b}" for b in self.blockers]
        if self.confirm_text:
            lines.append(f"To confirm, type: {self.confirm_text}")
        return "\n".join(lines)


@dataclass
class DeletionResult:
    deleted: int = 0
    bytes: int = 0
    failures: list[str] = field(default_factory=list)
    checklist: list[str] = field(default_factory=lambda: list(CHECKLIST))


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} GB"


def _size(path: Path) -> int:
    if path.is_dir():
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return path.stat().st_size


def _item(group: str, path: Path, camera: str = "") -> Item | None:
    if not path.exists():
        return None
    return Item(group, path, _size(path), path.is_dir(), camera)


# ---------- what belongs to which group ----------

def _working(session: Session, root: Path, label: str) -> list[Item]:
    layout = Layout(root)
    paths = []
    for cam in session.cameras:
        video = layout.inputs(cam.name) / f"trial_{label}.mp4"
        paths += [video, video.with_name(video.name + ".source")]
    videos = layout.trial(label) / "videos"
    if videos.is_dir():
        paths += sorted(videos.iterdir())
    return [i for p in paths if (i := _item("working", p))]


def _derived(root: Path, label: str) -> list[Item]:
    """Overlay videos/images and 2D keypoint JSONs (D14). Sync correlation plots and logs show no person."""
    tdir = Layout(root).trial(label)
    found: list[Path] = []
    for name in KEYPOINT_FOLDERS:
        folder = tdir / name
        if not folder.is_dir():
            continue
        for p in sorted(folder.iterdir()):
            if p.is_dir() and (p.name.endswith("_json") or p.name.endswith("_img") or name == "pose"):
                found.append(p)
            elif p.is_file() and (name == "pose" or (p.suffix.lower() in VIDEO_OR_IMAGE
                                                     and not p.name.startswith("sync_"))):
                found.append(p)
    return [i for p in found if (i := _item("derived", p))]


def _originals(session: Session, label: str) -> list[Item]:
    if session.calibration_source == "imported":
        return []  # the sample session's "originals" are the demo videos shipped with the app
    items = []
    for cam in session.cameras:
        f = cam.trial_files.get(label)
        if f and (i := _item("originals", Path(f), cam.name)):
            items.append(i)
    return items


def _results(session: Session, root: Path, label: str) -> list[Item]:
    layout = Layout(root)
    code = session.trial(label).code
    paths = [layout.trial(label)]
    if layout.qa.is_dir():
        paths += sorted(layout.qa.glob(f"trial_{label}_*"))
    if layout.export.is_dir():
        paths += sorted(layout.export.glob(f"{code}_*"))
    return [i for p in paths if (i := _item("results", p))]


# ---------- safeguard 1: results must be complete before a planned (setup checkbox) deletion ----------

def results_complete(session: Session, root: Path, label: str) -> list[str]:
    """Problems that block deleting raw footage right after a run (§8.4 safeguard 1); empty = OK."""
    from mocapapp.core.export import find_files, kinds
    from mocapapp.core.runner import STAGES

    tdir = Layout(root).trial(label)
    trial = session.trial(label)
    problems = []
    done = session.run_state.stage_done.get(label, [])
    if session.run_state.trial_status.get(label) != "done" or not all(s in done for s in STAGES):
        problems.append(f"trial {label}: not every processing step has finished successfully.")
        return problems
    table = {k.key: k for k in kinds(session.settings.filter.type)}
    for key, header in (("trc.filt", "PathFileType"), ("mot", "endheader")):
        files = find_files(tdir, table[key])
        if not files:
            problems.append(f"trial {label}: no {key.split('.')[0]} result file was found.")
            continue
        for f in files:
            head = f.read_text(encoding="utf-8", errors="replace")[:4000] if f.stat().st_size else ""
            if header not in head:
                problems.append(f"trial {label}: {f.name} is empty or unreadable.")
    manifest = Layout(root).export / "manifest.json"
    exported = []
    if manifest.is_file():
        try:
            exported = [f for f in json.loads(manifest.read_text(encoding="utf-8")).get("files", [])
                        if f.get("code") == trial.code]
        except ValueError:
            pass
    if not exported:
        problems.append(f"trial {label}: the results have not been exported yet.")
    return problems


# ---------- plans ----------

def _cloud_warning(session: Session, root: Path, with_originals: bool) -> list[str]:
    places = [root] + ([Path(c.source_dir) for c in session.cameras] if with_originals else [])
    if any(in_cloud_folder(p) for p in places):
        return ["Some of these files are inside a cloud-synced folder (OneDrive, Dropbox, Google Drive or iCloud). "
                "Deleting them here may leave copies in the cloud and in its online recycle bin; delete those too."]
    return []


def _check_trial(session: Session, label: str) -> None:
    try:
        trial = session.trial(label)
    except KeyError:
        raise DeletionError(f"There is no trial {label} in this session. Trials: "
                            f"{', '.join(t.label for t in session.trials)}.") from None
    if trial.withdrawn_at:
        raise DeletionError(f"All data of trial {label} ({trial.code}) was already deleted on {trial.withdrawn_at}.")


def plan_raw(session: Session, root: Path, label: str, include_originals: bool = False,
             after_run: bool = False) -> DeletionPlan:
    """Raw footage of one trial: working copies + overlays + 2D keypoints, optionally the originals.

    after_run=True is the deletion chosen at setup (the checkbox): it waits until the results are complete and
    exported. Otherwise (a participant's request, a later decision) it is allowed at any time, with a warning.
    """
    _check_trial(session, label)
    trial = session.trial(label)
    plan = DeletionPlan("raw", root, [label], [trial.code])
    plan.items = _working(session, root, label) + _derived(root, label)
    if include_originals:
        plan.items += _originals(session, label)
        if plan.includes_originals:
            plan.confirm_text = trial.code
    problems = results_complete(session, root, label)
    if after_run:
        plan.blockers = problems
    elif problems and trial.raw_present:
        plan.warnings.append("The results of this trial are not complete. Once the footage is deleted, this trial "
                             "can never be processed again.")
    plan.warnings += _cloud_warning(session, root, include_originals)
    if not include_originals and any(Path(f).exists() for c in session.cameras
                                     if (f := c.trial_files.get(label))):
        plan.notes.append("The original recordings in the camera folders are not included. Include them to delete "
                          "them too (you will be asked to type the pseudonym).")
    plan.notes.append(KEPT_NOTE)
    return plan


def plan_participant(session: Session, root: Path, label: str, include_originals: bool = False) -> DeletionPlan:
    """'Delete all participant data' for one trial (withdrawal): raw footage plus every result of that code."""
    _check_trial(session, label)
    trial = session.trial(label)
    plan = DeletionPlan("participant", root, [label], [trial.code])
    raw = _working(session, root, label) + _derived(root, label)
    raw_paths = {i.path for i in raw}
    results = [r for r in _results(session, root, label) if r.path not in raw_paths]
    for r in results:  # the trial folder holds the raw items: list both, count their bytes once
        if r.is_dir:
            r.size -= sum(i.size for i in raw if r.path in i.path.parents)
    plan.items = raw + results
    if include_originals:
        plan.items += _originals(session, label)
    plan.confirm_text = trial.code  # withdrawal removes results too: always type the code
    plan.warnings += _cloud_warning(session, root, include_originals)
    plan.notes.append("The pseudonym, the date and the list of deleted paths are kept in the deletion log "
                      "(no personal data). The participant's height is removed from session.json.")
    if not include_originals and any(Path(f).exists() for c in session.cameras
                                     if (f := c.trial_files.get(label))):
        plan.notes.append("The original recordings in the camera folders are not included.")
    return plan


def plan_session(session: Session, root: Path, include_originals: bool = False) -> DeletionPlan:
    """Delete the whole session folder (every participant), optionally the original trial recordings."""
    plan = DeletionPlan("session", root, [t.label for t in session.trials], [t.code for t in session.trials])
    if (item := _item("session", Path(root))) is not None:
        plan.items.append(item)
    if include_originals:
        for t in session.trials:
            plan.items += _originals(session, t.label)
    plan.confirm_text = Path(root).name
    entries = list(Path(root).iterdir()) if Path(root).is_dir() else []
    foreign = sorted(p.name for p in entries if p.name not in SESSION_ENTRIES and not p.name.startswith("."))
    if foreign:
        plan.blockers.append(f"The session folder also contains things the app did not make ({', '.join(foreign)}). "
                             "Move them out of the folder first.")
    plan.warnings += _cloud_warning(session, root, include_originals)
    plan.notes.append("The deletion log inside the session is deleted with it; write down the date if your "
                      "procedures require a record.")
    plan.notes.append("Calibration videos in the camera folders are never deleted by the app.")
    return plan


# ---------- execution ----------

def _on_rm_error(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)  # read-only files (common on Windows/SD-card copies)
    func(path)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_on_rm_error)
        else:
            shutil.rmtree(path, onerror=_on_rm_error)
    else:
        try:
            path.unlink()
        except PermissionError:
            os.chmod(path, stat.S_IWRITE)
            path.unlink()


def _confirmed(plan: DeletionPlan, typed: str | None) -> bool:
    if plan.confirm_text is None:
        return True
    return (typed or "").strip().casefold() == plan.confirm_text.casefold()


def execute(plan: DeletionPlan, session: Session, typed: str | None = None) -> DeletionResult:
    """Carry out a plan made by plan_raw/plan_participant/plan_session. Refuses unmet safeguards."""
    if plan.blockers:
        raise DeletionError("Not deleted yet: " + " ".join(plan.blockers) +
                            " Finish and export the results first, or delete on request instead.")
    if not _confirmed(plan, typed):
        what = "the session folder name" if plan.scope == "session" else "the pseudonym"
        raise DeletionError(f"Not deleted: type {what} exactly ({plan.confirm_text}) to confirm.")
    result = DeletionResult()
    deleted: list[Item] = []
    for item in plan.items:
        try:
            if item.path.exists():
                _remove(item.path)
            deleted.append(item)
            result.deleted += 1
            result.bytes += item.size
        except OSError as exc:
            result.failures.append(f"{item.path}: {exc.strerror or exc}. Close any program that has it open "
                                   "(video player, Explorer preview) and delete again.")
    if plan.scope == "session":
        return result
    for name in (*KEYPOINT_FOLDERS, "videos"):  # folders emptied by the deletion
        folder = Layout(plan.root).trial(plan.labels[0]) / name
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()

    now = datetime.now().isoformat(timespec="seconds")
    label = plan.labels[0]
    trial = session.trial(label)
    if any(i.group in ("working", "derived") for i in deleted) or not plan.items:
        trial.raw_deleted_at = trial.raw_deleted_at or now
    if plan.scope == "participant":
        trial.withdrawn_at = now
        trial.height_m = None
        trial.raw_deleted_at = trial.raw_deleted_at or now
        session.run_state.trial_status[label] = "withdrawn"
        session.run_state.stage_done.pop(label, None)
        session.run_state.sync.pop(label, None)
        session.run_state.errors.pop(label, None)
    save_session(session, plan.root)
    log_deletion(plan, deleted, result, now)
    if plan.scope == "participant":
        _forget_exports(plan.root, trial.code)
        _refresh_qa(session, plan.root)
    return result


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def log_deletion(plan: DeletionPlan, deleted: list[Item], result: DeletionResult, when: str) -> dict:
    """§8.4 safeguard 7: what, when, code. Originals are logged by camera only (their paths may name people)."""
    groups = {}
    for g in plan.groups():
        done = [i for i in deleted if i.group == g]
        groups[g] = {"items": len(done), "bytes": sum(i.size for i in done)}
    entry = {
        "at": when, "scope": plan.scope, "trial": plan.labels[0], "code": plan.codes[0], "groups": groups,
        "paths": [_relative(i.path, plan.root) for i in deleted if i.group != "originals"],
        "originals_deleted_for": sorted({i.camera for i in deleted if i.group == "originals"}),
        "failures": len(result.failures),
    }
    for path in (plan.root / DELETION_LOG, Layout(plan.root).export / "manifest.json"):
        if path.parent == Layout(plan.root).export and not path.is_file():
            continue
        data = {}
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                data = {}
        data.setdefault("deletions", []).append(entry)
        atomic_write_text(path, json.dumps(data, indent=2) + "\n")
    return entry


def deletion_log(root: Path) -> list[dict]:
    path = Path(root) / DELETION_LOG
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("deletions", []) if path.is_file() else []
    except ValueError:
        return []


def _forget_exports(root: Path, code: str) -> None:
    path = Layout(root).export / "manifest.json"
    if not path.is_file():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    data["files"] = [f for f in data.get("files", []) if f.get("code") != code]
    atomic_write_text(path, json.dumps(data, indent=2) + "\n")


def _refresh_qa(session: Session, root: Path) -> None:
    if not Layout(root).qa.is_dir():
        return
    from mocapapp.core.qa import write_report

    try:
        write_report(session, root)
    except (OSError, ValueError):
        pass  # the report is rebuilt the next time results are shown


# ---------- reminders (§8.4 item 6) ----------

def reminders(session: Session) -> list[str]:
    """Plain-language reminders about footage that is still stored; shown with the recent sessions."""
    out = []
    kept = [t for t in session.active_trials if t.retention_until and t.raw_present]
    if kept:
        out.append(f"Raw footage kept with consent for {', '.join(t.code for t in kept)}: delete it when the "
                   "study is finished.")
    due = [t for t in session.active_trials if t.delete_raw and t.raw_present
           and session.run_state.trial_status.get(t.label) == "done"]
    if due:
        out.append(f"Raw footage marked for deletion but not deleted yet: {', '.join(t.code for t in due)}.")
    return out


def session_reminders(root: Path) -> list[str]:
    """Reminders for a session folder without failing on unreadable or old sessions."""
    from mocapapp.core.session import load_session

    try:
        return reminders(load_session(root))
    except (OSError, ValueError):
        return []
