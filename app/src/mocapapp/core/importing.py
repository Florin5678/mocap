"""Import cameras, reference-point presets and camera layouts from another project (R3, PROJECT.md §16.5).

Imports copy into this project (the project folder stays self-contained): a camera with its lens calibration and
the copy of its intrinsic video; a layout with its cameras, reference points, camera positions and the copies of its
extrinsic videos. Participants, trials and results are never copied.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from mocapapp.core.calibration import extrinsics, intrinsics
from mocapapp.core.session import SCHEMA, SESSION_FILE, Layout, Session, atomic_write_text, save_session


class ImportError_(ValueError):  # noqa: N801 - ImportError is a builtin
    pass


@dataclass
class Source:
    root: Path
    session: Session
    name: str = ""


@dataclass
class Imported:
    cameras: dict[str, str] = field(default_factory=dict)  # source id -> id here
    presets: dict[str, str] = field(default_factory=dict)
    layouts: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def open_source(path: Path) -> Source:
    """Another project: its project file (.mocapproj) or its folder. It is only read, never changed."""
    from mocapapp.core.project import SUFFIX, ProjectError, load_project

    path = Path(path)
    if path.suffix.lower() == SUFFIX:
        try:
            root = load_project(path).session
        except ProjectError as exc:
            raise ImportError_(str(exc)) from None
    else:
        root = path
    file = root / SESSION_FILE
    if not file.is_file():
        raise ImportError_(f"{path} is not a project (no {SESSION_FILE}).")
    data = json.loads(file.read_text(encoding="utf-8"))
    if data.get("schema", 1) < SCHEMA:
        raise ImportError_(f"{root.name} was made with mocapapp 0.1. Open it once with this version first (it is "
                           "upgraded then, with a backup of its old file), then import from it.")
    return Source(root, Session.model_validate(data), root.name)


def _copy(src: Path, dst: Path) -> None:
    """A file and its '.source' stamp (the stamp says which original video the copy was made from)."""
    if not src.is_file():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    stamp = src.with_name(src.name + ".source")
    if stamp.is_file():
        shutil.copy2(stamp, dst.with_name(dst.name + ".source"))


def _copy_result(src: Path, dst: Path, camera: str) -> None:
    if not src.is_file():
        return
    data = json.loads(src.read_text(encoding="utf-8"))
    data["camera"] = camera
    dst.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(dst, json.dumps(data, indent=2) + "\n")


def _free(session: Session, kind: str, wanted: str) -> str:
    taken = {"cam": {c.id for c in session.cameras}, "L": {x.id for x in session.layouts},
             "P": {p.id for p in session.presets}}[kind]
    return wanted if wanted not in taken else session.new_id(kind)


def _camera(session: Session, root: Path, src: Source, cid: str, done: Imported) -> str:
    if cid in done.cameras:
        return done.cameras[cid]
    cam = src.session.camera(cid).model_copy(deep=True)
    cam.id = _free(session, "cam", cid)
    if cam.name in {c.name for c in session.cameras}:
        cam.name = f"{cam.name} ({src.name})"
    session.cameras.append(cam)
    here, there = Layout(root), Layout(src.root)
    _copy(there.camera_input(cid), here.camera_input(cam.id))
    _copy_result(intrinsics.result_path(there.cameras_work, cid), intrinsics.result_path(here.cameras_work, cam.id),
                 cam.id)
    done.cameras[cid] = cam.id
    done.notes.append(f"Camera {cam.name} imported as {cam.id}" + (" (with its lens calibration)."
                      if intrinsics.result_path(here.cameras_work, cam.id).is_file() else "."))
    return cam.id


def _preset(session: Session, src: Source, pid: str, done: Imported) -> str:
    if pid in done.presets:
        return done.presets[pid]
    preset = src.session.preset(pid).model_copy(deep=True)
    preset.id = session.new_id("P")
    session.presets.append(preset)
    done.presets[pid] = preset.id
    done.notes.append(f"Reference points '{preset.name}' imported as {preset.id}.")
    return preset.id


def _layout(session: Session, root: Path, src: Source, lid: str, done: Imported) -> str:
    from mocapapp.core.calibration import service

    if lid in done.layouts:
        return done.layouts[lid]
    lay = src.session.layout(lid).model_copy(deep=True)
    lay.id = _free(session, "L", lid)
    lay.imported_from = src.name
    lay.preset = done.presets.get(lay.preset, "")
    old_ids = lay.camera_ids()
    for lc in lay.cameras:
        lc.camera = _camera(session, root, src, lc.camera, done)
    session.layouts.append(lay)
    here, there = Layout(root), Layout(src.root)
    for old, lc in zip(old_ids, lay.cameras, strict=True):
        _copy(there.layout_input(lid, old), here.layout_input(lay.id, lc.camera))
        _copy_result(extrinsics.result_path(there.layout_work(lid), old),
                     extrinsics.result_path(here.layout_work(lay.id), lc.camera), lc.camera)
        review = service.review_image(src.root, lid, old)
        if review.is_file():
            shutil.copy2(review, service.review_image(root, lay.id, lc.camera))
    done.layouts[lid] = lay.id
    done.notes.append(f"Layout {lay.name} ({lay.date or 'no date'}) imported as {lay.id} with cameras "
                      f"{', '.join(lay.camera_ids())}. Trials recorded on another day are warned about it.")
    return lay.id


def import_project(session: Session, root: Path, source: Path, cameras: list[str] | None = None,
                   presets: list[str] | None = None, layouts: list[str] | None = None) -> Imported:
    """Copy these items (ids in the other project; None = none of that kind) into this project. A layout brings
    its cameras along. Saves this project."""
    src = open_source(source)
    if Path(src.root).resolve() == Path(root).resolve():
        raise ImportError_("That is this project.")
    done = Imported()
    try:
        for pid in presets or []:
            _preset(session, src, pid, done)
        for cid in cameras or []:
            _camera(session, root, src, cid, done)
        for lid in layouts or []:
            _layout(session, root, src, lid, done)
    except KeyError as exc:
        raise ImportError_(f"In {src.name}: {str(exc).strip(chr(39))}") from None
    save_session(session, root)
    return done
