"""Project files (.mocapproj): a small JSON file that points to a session folder and remembers the step the
researcher was on. The session's data stays in the session folder (session.json is saved after every change);
the project file is a bookmark to reopen that state later, from anywhere on disk.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mocapapp.core.session import SESSION_FILE, atomic_write_text

SUFFIX = ".mocapproj"
FORMAT = "mocapapp-project"
STEPS = ("setup", "videos", "intrinsics", "extrinsics", "run", "results", "viewer")
OLD_STEPS = {"scene": "extrinsics"}  # 0.1.0rc17: reference points and camera positions are one step


class ProjectError(ValueError):
    pass


@dataclass
class Project:
    path: Path
    session: Path  # the session folder
    step: str = "setup"
    saved_at: str = ""
    app_version: str = ""


def project_path(path: Path) -> Path:
    path = Path(path)
    return path if path.suffix.lower() == SUFFIX else path.with_name(path.name + SUFFIX)


def save_project(path: Path, session_root: Path, step: str = "setup") -> Path:
    """Write the project file. The session folder is stored relative to the file (so both can be moved together)
    and as an absolute path (so the file alone can be moved)."""
    from mocapapp import __version__

    path = project_path(path).resolve()
    root = Path(session_root).resolve()
    if not (root / SESSION_FILE).is_file():
        raise ProjectError(f"{root} is not a session folder (no {SESSION_FILE}).")
    try:
        relative = os.path.relpath(root, path.parent)
    except ValueError:  # another drive on Windows
        relative = None
    data = {"format": FORMAT, "version": 1, "session": str(root), "session_relative": relative,
            "step": step if step in STEPS else "setup",
            "saved_at": datetime.now().isoformat(timespec="seconds"), "app_version": __version__}
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data, indent=2) + "\n")
    return path


def load_project(path: Path) -> Project:
    path = Path(path).resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectError(f"{path.name} could not be read as a project file: {exc}") from None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ProjectError(f"{path.name} is not a mocapapp project file.")
    candidates = []
    if data.get("session_relative"):
        candidates.append((path.parent / data["session_relative"]).resolve())
    if data.get("session"):
        candidates.append(Path(data["session"]))
    for root in candidates:
        if (root / SESSION_FILE).is_file():
            step = OLD_STEPS.get(data.get("step"), data.get("step"))
            step = step if step in STEPS else "setup"
            return Project(path, root, step, data.get("saved_at", ""), data.get("app_version", ""))
    where = " or ".join(str(c) for c in dict.fromkeys(candidates)) or "(none given)"
    raise ProjectError(f"The session folder of {path.name} was not found at {where}. It may have been moved, "
                       "renamed or deleted; open the session folder itself with File > Open session folder.")
