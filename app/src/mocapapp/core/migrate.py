"""Upgrade a 0.1.x session (schema 1) to a 0.2 project (schema 2, PROJECT.md §16.2).

Schema 1 had per-session cameras, each with a main intrinsic and extrinsic video plus further ones assigned to
trials (0.1.0rc13/rc17), one set of reference points (scene.json) and one Calib_scene.toml. Schema 2 has:

- a camera per intrinsic video: the main one keeps the camera's name as id (cam01); a further intrinsic video k of
  cam01 becomes camera "cam01-k";
- a layout per distinct combination of videos used together by the trials (the main videos give "Layout 1");
- reference points as preset "Reference points" and copied into every layout;
- one calibration file per trial.

Inside a trial's Pose2Sim folder the cameras are named cam01, cam02, ... by their order in the layout, as before, so
finished trials stay valid. `upgrade_v1` converts the data only; `migrate_dir` also moves the files on disk, after
saving `session.v1.json` (and `scene.v1.json`) as a backup.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

BACKUP = "session.v1.json"


@dataclass
class Upgrade:
    data: dict
    # (old path, [new paths], rewrite 'camera' in the JSON to this id or "") relative to the session folder
    moves: list[tuple[str, list[str], str]] = field(default_factory=list)


def _extra_for(cam: dict, kind: str, label: str) -> str:
    return next((p["key"] for p in cam.get(kind) or [] if label in p.get("trials", [])), "")


def _entry_id(cam: dict, lens: str) -> str:
    return f"{cam['name']}-{lens}" if lens else cam["name"]


def triangulation_deps(cameras: list[tuple[str, str, dict | None]]) -> list[str]:
    """What a trial's 3D positions depend on: each camera's slot, id and solved position (same as runner)."""
    return [f"{slot}:{cid}:{(result or {}).get('error_px', '')}" for slot, cid, result in cameras]


def _old_deps(v1: dict, label: str) -> str:
    """runner.stage_inputs('triangulation') of 0.1.0rc17."""
    deps = []
    for cam in v1.get("cameras", []):
        key, lens = _extra_for(cam, "positions", label), _extra_for(cam, "lenses", label)
        if key or lens:
            entry = next((e for e in v1.get("extrinsics", []) if e.get("camera") == cam["name"]
                          and e.get("position", "") == key and e.get("lens", "") == lens), {})
            deps.append(f"{cam['name']}:{key}:{lens + ':' if lens else ''}{entry.get('error_px', '')}")
    return json.dumps(deps)


def upgrade_v1(v1: dict) -> Upgrade:
    old_cams = v1.get("cameras", [])
    data = {k: v for k, v in v1.items() if k not in ("cameras", "extrinsics", "sync_cue_confirmed")}
    data["schema"] = 2
    up = Upgrade(data)

    cameras = []
    for cam in old_cams:
        cameras.append({"id": cam["name"], "name": cam["name"], "source_dir": cam.get("source_dir", ""),
                        "intrinsic": cam.get("intrinsic")})
        up.moves.append((f"inputs/{cam['name']}/intrinsic.mp4", [f"inputs/cameras/{cam['name']}/intrinsic.mp4"], ""))
        up.moves.append((f"calibration_work/intrinsics_{cam['name']}.json",
                         [f"calibration_work/cameras/intrinsics_{cam['name']}.json"], cam["name"]))
        for lens in cam.get("lenses") or []:
            cid = _entry_id(cam, lens["key"])
            cameras.append({"id": cid, "name": f"{cam['name']} (intrinsic video {lens['key']})",
                            "source_dir": cam.get("source_dir", ""),
                            "intrinsic": {"file": lens["file"], "origin": "manual"}})
            up.moves.append((f"inputs/{cam['name']}/intrinsic_{lens['key']}.mp4",
                             [f"inputs/cameras/{cid}/intrinsic.mp4"], ""))
            up.moves.append((f"calibration_work/intrinsics_{cam['name']}_lens{lens['key']}.json",
                             [f"calibration_work/cameras/intrinsics_{cid}.json"], cid))
    data["cameras"] = cameras

    # layouts: the combinations of (intrinsic, extrinsic) videos per camera used together by trials
    trials = [dict(t) for t in v1.get("trials", [])]
    combos: list[tuple[tuple[str, str], ...]] = []
    main = tuple(("", "") for _ in old_cams)
    per_trial = {}
    for t in trials:
        combo = tuple((_extra_for(c, "lenses", t["label"]), _extra_for(c, "positions", t["label"])) for c in old_cams)
        per_trial[t["label"]] = combo
        if combo not in combos:
            combos.append(combo)
    if main in combos:
        combos.remove(main)
    if old_cams:
        combos.insert(0, main)
    layouts = []
    ext_dsts: dict[str, list[str]] = {}  # old extrinsic input -> new paths
    res_dsts: dict[tuple[str, str], list[str]] = {}  # (old result file, new camera id) -> new paths
    review_dsts: dict[str, list[str]] = {}
    for n, combo in enumerate(combos, 1):
        lid = f"L{n}"
        lay_cams = []
        for cam, (lens, pos) in zip(old_cams, combo, strict=True):
            cid = _entry_id(cam, lens)
            if pos:
                video = next((p for p in cam.get("positions") or [] if p["key"] == pos), None)
                extrinsic = {"file": video["file"], "origin": "manual"} if video else None
            else:
                extrinsic = cam.get("extrinsic")
            result = next((e for e in v1.get("extrinsics", []) if e.get("camera") == cam["name"]
                           and e.get("position", "") == pos and e.get("lens", "") == lens), None)
            if result is not None:
                result = {k: v for k, v in result.items() if k not in ("camera", "position", "lens")}
            lay_cams.append({"camera": cid, "extrinsic": extrinsic, "result": result})
            ext_dsts.setdefault(f"inputs/{cam['name']}/extrinsic{'_' + pos if pos else ''}.mp4", []).append(
                f"inputs/layouts/{lid}/{cid}.mp4")
            old_res = (f"calibration_work/extrinsics_{cam['name']}{'_pos' + pos if pos else ''}"
                       f"{'_lens' + lens if lens else ''}.json")
            res_dsts.setdefault((old_res, cid), []).append(f"calibration_work/layouts/{lid}/extrinsics_{cid}.json")
            old_review = f"calibration_work/extrinsic_{cam['name']}{'_pos' + pos if pos else ''}_review.jpg"
            review_dsts.setdefault(old_review, []).append(f"calibration_work/layouts/{lid}/extrinsic_{cid}_review.jpg")
        layouts.append({"id": lid, "name": f"Layout {n}", "cameras": lay_cams})
    data["layouts"] = layouts
    data["presets"] = []
    up.moves += [(src, dsts, "") for src, dsts in ext_dsts.items()]
    up.moves += [(src, dsts, cid) for (src, cid), dsts in res_dsts.items()]
    up.moves += [(src, dsts, "") for src, dsts in review_dsts.items()]

    lay_of = {combo: f"L{n}" for n, combo in enumerate(combos, 1)}
    stage_settings = (data.get("run_state") or {}).get("stage_settings") or {}
    for t in trials:
        label = t["label"]
        combo = per_trial[label]
        lid = lay_of.get(combo, "")  # "" only for a session without cameras yet
        t["layout"] = lid
        t["videos"] = {}
        for cam, (lens, _pos) in zip(old_cams, combo, strict=True):
            cid = _entry_id(cam, lens)
            if label in (cam.get("trial_files") or {}):
                t["videos"][cid] = cam["trial_files"][label]
            up.moves.append((f"inputs/{cam['name']}/trial_{label}.mp4", [f"inputs/trials/{label}/{cid}.mp4"], ""))
        record = stage_settings.get(label, {})
        if lid and "triangulation" in record and record["triangulation"] == _old_deps(v1, label):
            lay = layouts[int(lid[1:]) - 1]
            record["triangulation"] = json.dumps(triangulation_deps(
                [(f"cam{i + 1:02d}", lc["camera"], lc["result"]) for i, lc in enumerate(lay["cameras"])]))
    data["trials"] = trials
    # 0.1.0rc13 asked the height with every trial: a participant without its own takes its last trial's
    for person in data.get("participants", []):
        if person.get("height_m") in (None, "", "auto"):
            heights = [t.get("details", {}).get("height") for t in trials if t.get("participant") == person["label"]]
            if any(heights):
                person["height_m"] = [h for h in heights if h][-1]
    return up


def _place(src: Path, dsts: list[Path], camera: str) -> None:
    """Put a file at every destination (hard link or copy; JSON results get their new camera id), then remove it."""
    for dst in dsts:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.unlink(missing_ok=True)
        if camera:
            data = json.loads(src.read_text(encoding="utf-8"))
            data["camera"] = camera
            dst.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            continue
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
    src.unlink()


def migrate_dir(root: Path) -> list[str]:
    """Upgrade a session folder in place. Returns plain-language notes. The old session.json is kept as
    session.v1.json; nothing else is deleted except files that were moved."""
    from mocapapp.core.session import SESSION_FILE, _one_participant_per_trial, atomic_write_text

    root = Path(root)
    path = root / SESSION_FILE
    v1 = json.loads(path.read_text(encoding="utf-8"))
    if v1.get("schema", 1) >= 2:
        return []
    shutil.copy2(path, root / BACKUP)
    up = upgrade_v1(_one_participant_per_trial(v1))
    notes = [f"Upgraded to a 0.2 project; the old file is kept as {BACKUP}."]
    for src, dsts, camera in up.moves:
        s = root / src
        if s.is_file():
            _place(s, [root / d for d in dsts], camera)
            stamp = s.with_name(s.name + ".source")
            if stamp.is_file():
                _place(stamp, [(root / d).with_name(Path(d).name + ".source") for d in dsts], "")
    scene = root / "scene.json"
    if scene.is_file():
        points = json.loads(scene.read_text(encoding="utf-8"))
        up.data["presets"] = [{"id": "P1", "name": "Reference points", "points": points}]
        for lay in up.data["layouts"]:
            lay["points"], lay["preset"] = points, "P1"
        scene.replace(root / "scene.v1.json")
    work = root / "calibration_work"
    shared = work / "Calib_scene.toml"
    if up.data.get("calibration_source", "app") == "app":
        for t in up.data["trials"]:
            own = work / f"Calib_scene_trial_{t['label']}.toml"
            if shared.is_file() and not own.is_file():
                shutil.copy2(shared, own)
        shared.unlink(missing_ok=True)
    inputs = root / "inputs"
    for d in sorted(inputs.iterdir()) if inputs.is_dir() else []:
        if d.is_dir() and d.name not in ("cameras", "layouts", "trials") and not any(d.iterdir()):
            d.rmdir()
    atomic_write_text(path, json.dumps(up.data, indent=2) + "\n")
    return notes
