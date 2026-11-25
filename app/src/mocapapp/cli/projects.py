"""CLI commands of the project-based layout (PROJECT.md §16.5): project, camera, points, layout, participant, trial.

Every command takes --session: a project folder or its project file (.mocapproj). The commands only call core
functions; nothing here decides anything the GUI would have to repeat.
"""

from __future__ import annotations

import argparse
import functools
from pathlib import Path

CAMERA_OPTIONS = {"name": "name", "folder": "source_dir", "resolution": "resolution", "zoom": "zoom",
                  "focus": "focus", "stabilisation": "stabilisation", "fps": "fps", "notes": "notes"}


# ---------- helpers ----------

def _root(path: str) -> Path:
    from mocapapp.core.project import SUFFIX, load_project

    p = Path(path)
    return load_project(p).session if p.suffix.lower() == SUFFIX else p


def _open(args: argparse.Namespace):
    from mocapapp.core.session import load_session

    root = _root(args.session)
    return root, load_session(root)


def _guard(func):
    """Plain-language errors instead of tracebacks (§0.3): print the message, exit code 1."""

    @functools.wraps(func)
    def run(args: argparse.Namespace) -> int:
        from mocapapp.core.calibration.extrinsics import ExtrinsicsError
        from mocapapp.core.calibration.service import CalibrationStepError
        from mocapapp.core.importing import ImportError_
        from mocapapp.core.media import MediaError
        from mocapapp.core.project import ProjectError
        from mocapapp.core.workflow import SetupError

        try:
            return func(args) or 0
        except KeyError as exc:
            print(str(exc).strip("'\""))
        except (SetupError, CalibrationStepError, ExtrinsicsError, ImportError_, ProjectError, MediaError,
                FileNotFoundError, ValueError) as exc:
            print(exc)
        return 1

    return run


def _list(values: list[str] | None) -> list[str]:
    """--x a,b --x c -> [a, b, c]."""
    return [v.strip() for item in values or [] for v in item.split(",") if v.strip()]


def _pairs(values: list[str] | None, what: str) -> dict[str, str]:
    out = {}
    for v in values or []:
        key, sep, val = v.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"--{what} expects NAME=VALUE, got '{v}'.")
        out[key.strip()] = val.strip()
    return out


def _scene_from_args(args: argparse.Namespace, scene=None):
    from mocapapp.core.calibration import scene_points as sp

    scene = scene or sp.Scene()
    if getattr(args, "unit", None):
        scene.unit = args.unit
    if getattr(args, "csv", None):
        scene.points = sp.from_csv(Path(args.csv).read_text(encoding="utf-8-sig"), scene.unit)
    if getattr(args, "snippet", None):
        scene.points = sp.from_snippet(Path(args.snippet).read_text(encoding="utf-8"))
    for attr, field in (("origin", "origin_text"), ("x_axis", "x_axis_text"), ("y_note", "y_axis_note")):
        if getattr(args, attr, None) is not None:
            setattr(scene, field, getattr(args, attr))
    return scene


def _print_issues(issues) -> bool:
    for issue in issues:
        print(issue)
    return not any(i.level in ("error", "action") for i in issues)


def _write_calibration(session, root: Path) -> None:
    from mocapapp.core.calibration import service

    if session.calibration_source == "app":
        written = service.write_calibration(session, root)
        if written:
            print(f"Calibration written for {len(written)} trial(s).")


# ---------- project ----------

@_guard
def _project_new(args: argparse.Namespace) -> int:
    from mocapapp.core.project import save_project
    from mocapapp.core.workflow import create_project

    root, _session = create_project(Path(args.out), args.study or "", args.trials_per_participant)
    print(f"Project created: {root}")
    if args.file:
        print(f"Project file: {save_project(Path(args.file), root)}")
    print(f'Next: add cameras with mocapapp camera add --session "{root}" --folder <camera folder>')
    return 0


def _print_info(root: Path, session) -> None:
    from collections import Counter

    from mocapapp.core.calibration import service
    from mocapapp.core.readiness import trial_status
    from mocapapp.core.workflow import consent_state

    lenses = sum(1 for c in session.cameras if service.lens_result(root, c.id) is not None)
    print(f"Project {root.name}: study {session.study_code or '-'}, {session.trials_per_participant} trial(s) per "
          f"participant")
    print(f"  cameras: {len(session.cameras)} ({lenses} with lens calibration); reference-point presets: "
          f"{len(session.presets)}; layouts: {len(session.layouts)}")
    unsigned = sum(1 for p in session.active_participants if consent_state(p) == "not-signed")
    print(f"  participants: {len(session.participants)}" + (f" ({unsigned} without signed consent)"
                                                              if unsigned else ""))
    counts = Counter(trial_status(session, root, t.label) for t in session.trials)
    print(f"  trials: {len(session.trials)}" + (" (" + ", ".join(f"{n} {s}" for s, n in counts.items()) + ")"
                                                 if counts else ""))


@_guard
def _project_info(args: argparse.Namespace) -> int:
    root, session = _open(args)
    _print_info(root, session)
    return 0


def _on_off(value: str | None) -> bool | None:
    return None if value is None else value == "on"


@_guard
def _project_settings(args: argparse.Namespace) -> int:
    """Session settings (§16.3): project details, processing, raw-footage deletion and the board."""
    from mocapapp.core import workflow
    from mocapapp.core.runner import KEYPOINT_TITLES

    root, s = _open(args)
    if any(v is not None for v in (args.study, args.trials_per_participant, args.notes)):
        workflow.set_project_info(s, root, args.study, args.trials_per_participant, args.notes)
    values = {k: v for k, v in {"pose_mode": args.pose_mode, "keypoints": args.keypoints, "cutoff_hz": args.cutoff,
                                "use_augmentation": _on_off(args.augmentation),
                                "simple_model": None if args.body_model is None else args.body_model == "simple",
                                "save_overlay": _on_off(args.overlay),
                                "keep_keypoints": _on_off(args.keep_keypoints)}.items() if v is not None}
    if values:
        stale = workflow.set_settings(s, root, **values)
        if stale:
            print(f"Trial(s) {', '.join(stale)} ran with the old settings and are now out of date.")
    if args.delete_raw_all is not None or args.delete_raw_when is not None:
        on = s.settings.delete_raw_all if args.delete_raw_all is None else args.delete_raw_all == "on"
        workflow.set_delete_raw_all(s, root, on, args.delete_raw_when)
    if args.board or args.squares or args.square_mm:
        st = s.settings
        cols, rows = st.corners_nb[1] + 1, st.corners_nb[0] + 1
        if args.squares:
            try:
                cols, rows = (int(n) for n in args.squares.lower().split("x"))
            except ValueError:
                raise ValueError("--squares is ACROSSxDOWN, e.g. 9x6.") from None
        workflow.set_board(s, root, args.board or st.board_type, cols, rows, args.square_mm or st.square_size_mm)
    st = s.settings
    print(f"Project {root.name}: study {s.study_code or '-'}, {s.trials_per_participant} trial(s) per participant"
          + (f"; notes: {s.notes}" if s.notes else ""))
    print(f"  processing: {st.pose_mode}, {KEYPOINT_TITLES[st.keypoints].lower()}, cut-off {st.filter.cutoff_hz:g} Hz, "
          f"{'LSTM augmentation' if st.use_augmentation else 'no augmentation'}, "
          f"{'simple' if st.simple_model else 'full'} body model, overlay videos {'on' if st.save_overlay else 'off'}, "
          f"keep 2D keypoints {'on' if st.keep_keypoints else 'off'}")
    print("  raw footage: " + (f"deleted for all participants {workflow.DELETE_WHEN[st.delete_raw_when]}"
                               if st.delete_raw_all else "follows each participant's consent"))
    print(f"  board: {st.board_type}, {st.corners_nb[1] + 1}x{st.corners_nb[0] + 1} squares of "
          f"{st.square_size_mm:g} mm")
    return 0


@_guard
def _project_open(args: argparse.Namespace) -> int:
    from mocapapp.core.project import load_project
    from mocapapp.core.session import load_session

    p = load_project(Path(args.file))
    print(f"Project folder: {p.session} (last step: {p.step})")
    _print_info(p.session, load_session(p.session))
    return 0


@_guard
def _project_import(args: argparse.Namespace) -> int:
    from mocapapp.core.importing import import_project, open_source

    src = open_source(Path(args.source))
    if args.list:
        print(f"In {src.name}:")
        for c in src.session.cameras:
            print(f"  camera {c.id}: {c.name}")
        for p in src.session.presets:
            print(f"  reference points {p.id}: {p.name}")
        for lay in src.session.layouts:
            print(f"  layout {lay.id}: {lay.name} ({lay.date or 'no date'}), cameras {', '.join(lay.camera_ids())}")
        return 0
    everything = not (args.cameras or args.points or args.layouts)
    root, session = _open(args)
    done = import_project(
        session, root, Path(args.source),
        cameras=[c.id for c in src.session.cameras] if everything or args.cameras else None,
        presets=[p.id for p in src.session.presets] if everything or args.points else None,
        layouts=[x.id for x in src.session.layouts] if everything or args.layouts else None)
    for note in done.notes or ["Nothing to import."]:
        print(note)
    return 0


# ---------- cameras ----------

def _camera_fields(args: argparse.Namespace) -> dict[str, str]:
    return {field: getattr(args, opt) for opt, field in CAMERA_OPTIONS.items() if getattr(args, opt) is not None}


@_guard
def _camera(args: argparse.Namespace) -> int:
    from mocapapp.core import workflow
    from mocapapp.core.calibration import service

    root, s = _open(args)
    cmd = args.camera_command
    if cmd == "add":
        cam = workflow.add_camera(s, root, Path(args.intrinsic) if args.intrinsic else None, **_camera_fields(args))
        if cam.intrinsic is None and cam.source_dir:  # an 'intrinsic' video in its folder is taken as obvious
            from mocapapp.core.validate import autofill

            autofill(s, trials=False)
            workflow.save_session(s, root)
        print(f"Camera {cam.id} added" + (f": intrinsic video {Path(cam.intrinsic.file).name}." if cam.intrinsic
                                          else ". Choose its intrinsic video with camera edit --intrinsic FILE."))
    elif cmd == "list":
        for cam in s.cameras:
            extra = ", ".join(f"{k} {getattr(cam, k)}" for k in ("resolution", "zoom", "focus", "stabilisation",
                                                                  "fps") if getattr(cam, k))
            used = sum(len(s.trials_with_layout(lay.id)) for lay in s.layouts_with_camera(cam.id))
            print(f"{cam.id}  {cam.name}" + (f" ({extra})" if extra else "")
                  + f"  intrinsic: {Path(cam.intrinsic.file).name if cam.intrinsic else '-'}"
                  + f"  {service._intrinsic_state(root, cam)}  used by {used} trial(s)"
                  + ("  [locked: a trial ran]" if s.camera_used(cam.id) else ""))
        if not s.cameras:
            print("No cameras yet: mocapapp camera add --session <project> --folder <camera folder>")
    elif cmd == "edit":
        workflow.edit_camera(s, root, args.camera, **_camera_fields(args))
        if args.intrinsic:
            workflow.set_intrinsic_video(s, root, args.camera, Path(args.intrinsic))
        print(f"Camera {args.camera} saved.")
    elif cmd == "duplicate":
        cam = workflow.duplicate_camera(s, root, args.camera)
        print(f"Camera {args.camera} duplicated as {cam.id} ({cam.name}).")
    elif cmd == "delete":
        workflow.delete_camera(s, root, args.camera)
        print(f"Camera {args.camera} deleted.")
    elif cmd == "calibrate":
        ids = _list(args.camera) or [c.id for c in s.cameras if c.intrinsic is not None]
        if not ids:
            print("No camera has an intrinsic video yet (camera edit --intrinsic FILE).")
            return 1
        workflow.prepare_calibration(s, root, ids)
        try:
            for r in service.run_intrinsics(s, root, ids, args.every, args.force):
                print(f"{r.camera}: {r.rms_px:.3f} px from {r.n_used}/{r.n_frames} frames, coverage {r.coverage:.0%}, "
                      f"tilt spread {r.tilt_spread_deg:.0f} deg, {r.layout} -> {r.status.upper()}")
                for a in r.advice:
                    print(f"    -> {a}")
            _write_calibration(s, root)
        finally:
            workflow.save_session(s, root)
    return 0


# ---------- reference points ----------

@_guard
def _points(args: argparse.Namespace) -> int:
    from mocapapp.core.calibration import scene_points as sp
    from mocapapp.core.calibration import service
    from mocapapp.core.workflow import save_session

    root, s = _open(args)
    cmd = args.preset_command
    if cmd == "save":
        scene = service.layout_scene(s, args.layout) if args.layout else None
        scene = _scene_from_args(args, scene)
        if not scene.points:
            raise ValueError("Give the points: --csv FILE (label,x,y,z), --snippet FILE or --layout L.")
        preset = service.save_preset(s, args.name, scene)
        save_session(s, root)
        ok = _print_issues(sp.check_scene(scene))
        print(f"Reference points saved as {preset.id} ({preset.name}, {len(scene.points)} points)."
              + ("" if ok else " Fix the items above before using them."))
        return 0 if ok else 1
    if cmd == "list":
        for p in s.presets:
            n = len((p.points or {}).get("points", []))
            users = [lay.name for lay in s.layouts if lay.preset == p.id]
            print(f"{p.id}  {p.name}  {n} points" + (f"  used by {', '.join(users)}" if users else ""))
        if not s.presets:
            print("No reference-point presets yet: mocapapp points preset save --name ... --csv FILE")
        return 0
    scene = sp.Scene.model_validate(s.preset(args.preset).points)
    if cmd == "show":
        print(sp.axes_description(scene))
        print()
        print(sp.to_csv(scene.points, scene.unit) if args.format == "csv" else scene.as_config_snippet())
        return 0
    ok = _print_issues(sp.check_scene(scene))
    print("\nReference points OK." if ok else "\nFix the reference points (see above).")
    return 0 if ok else 1


# ---------- layouts ----------

def _print_layout(session, root: Path, lay) -> None:
    from mocapapp.core.calibration import service

    trials = [t.label for t in session.trials_with_layout(lay.id)]
    print(f"{lay.id}  {lay.name}  date {lay.date or '-'}  cameras {', '.join(lay.camera_ids()) or '-'}"
          + (f"  imported from {lay.imported_from}" if lay.imported_from else "")
          + f"  trials {', '.join(trials) or '-'}")
    for what, state in service.layout_state(session, root, lay.id).items():
        print(f"    {what}: {state}")


@_guard
def _layout(args: argparse.Namespace) -> int:
    from mocapapp.core import workflow
    from mocapapp.core.calibration import extrinsics as ex
    from mocapapp.core.calibration import service
    from mocapapp.core.calibration.scene_points import Scene

    root, s = _open(args)
    cmd = args.layout_command
    if cmd == "new":
        lay = workflow.add_layout(s, root, args.name or "", _list(args.cameras), args.preset or "", args.date)
        print(f"Layout {lay.id} ({lay.name}) made with cameras {', '.join(lay.camera_ids())}"
              + (f" and reference points {args.preset}." if args.preset else "."))
        print(f'Next: choose the extrinsic videos: mocapapp layout edit --session "{root}" --layout {lay.id} '
              "--video camNN=FILE ...")
    elif cmd in ("list", "status"):
        layouts = [s.layout(args.layout)] if getattr(args, "layout", None) else s.layouts
        for lay in layouts:
            _print_layout(s, root, lay)
        if not layouts:
            print("No layouts yet: mocapapp layout new --session <project> --name ... --cameras cam01,cam02,...")
    elif cmd == "edit":
        cameras = _list(args.cameras) or None
        workflow.edit_layout(s, root, args.layout, name=args.name, date=args.date, cameras=cameras)
        if args.preset:
            preset = s.preset(args.preset)
            for note in service.set_layout_points(s, root, args.layout, Scene.model_validate(preset.points),
                                                  preset=args.preset):
                print(note)
        for cam, file in _pairs(args.video, "video").items():
            workflow.set_extrinsic_video(s, root, args.layout, cam, Path(file))
        _write_calibration(s, root)
        workflow.save_session(s, root)
        _print_layout(s, root, s.layout(args.layout))
    elif cmd == "duplicate":
        lay = workflow.duplicate_layout(s, root, args.layout)
        print(f"Layout {args.layout} duplicated as {lay.id} ({lay.name}): same cameras and reference points; "
              "choose its extrinsic videos.")
    elif cmd == "delete":
        workflow.delete_layout(s, root, args.layout)
        print(f"Layout {args.layout} deleted.")
    elif cmd == "click":
        lc = s.layout(args.layout).entry(args.camera)
        if lc.extrinsic is None:
            raise workflow.SetupError(f"Choose the extrinsic video of {args.camera} first: mocapapp layout edit "
                                      f"--layout {args.layout} --video {args.camera}=FILE")
        workflow.prepare_calibration(s, root, layout=args.layout)
        if not args.points:
            print(service.export_frame(root, args.layout, args.camera, args.frame))
            print("Measure the pixel position (u = from left, v = from top) of each visible reference point and "
                  "write label,u,v lines to a CSV ('hidden' for points this camera cannot see); then run this "
                  "command again with --points CSV.")
            return 0
        clicks = ex.read_clicks_csv(Path(args.points).read_text(encoding="utf-8-sig"))
        try:
            r = service.run_extrinsics(s, root, args.layout, args.camera, clicks, args.frame, args.override)
        finally:
            workflow.save_session(s, root)
        pos = ", ".join(f"{v:.2f}" for v in r.camera_position)
        print(f"{args.camera} in {s.layout(args.layout).name}: {r.rms_px:.2f} px = {r.rms_cm:.2f} cm from "
              f"{r.n_points} points; camera at ({pos}) m -> {r.status.upper()}"
              f"{' (overridden)' if r.overridden else ''}")
        for w in r.warnings:
            print(f"    -> {w}")
        if r.status == "fail" and not r.overridden:
            print(f"    Above {ex.WARN_CM} cm this camera blocks the run. Fix the points, or accept with --override.")
        _write_calibration(s, root)
        workflow.save_session(s, root)
    return 0


# ---------- participants ----------

@_guard
def _participant(args: argparse.Namespace) -> int:
    from mocapapp.core import workflow
    from mocapapp.core.session import format_height

    root, s = _open(args)
    cmd = args.participant_command
    if cmd == "add":
        person = workflow.add_participant(s, root, args.height)
        trials = ", ".join(t.label for t in s.trials_of(person.label))
        print(f"Participant {person.label} added: pseudonym {person.code}" + (f", trial(s) {trials}" if trials else
                                                                               "") + ".")
    elif cmd == "list":
        for p in s.participants:
            trials = ", ".join(t.label for t in s.trials_of(p.label)) or "-"
            state = "withdrawn" if p.withdrawn_at else workflow.CONSENT_STATES[workflow.consent_state(p)]
            print(f"{p.label}  {p.code}  height {format_height(p.height_m) if p.height_m else 'missing'}  "
                  f"consent: {state}  trials {trials}")
        if not s.participants:
            print("No participants yet: they are added with their first trial (mocapapp trial add).")
    elif cmd == "set":
        person = s.participant(args.participant)
        if args.random:
            person = workflow.new_pseudonym(s, root, args.participant, args.confirm)
        elif args.code:
            person = workflow.rename_participant(s, root, args.participant, args.code, args.confirm)
        if args.height is not None:
            workflow.set_participant_value(s, root, args.participant, "height", args.height)
        for key, value in _pairs(args.set, "set").items():
            workflow.set_participant_value(s, root, args.participant, key, value)
        print(f"Participant {person.label} ({person.code}) saved.")
    elif cmd == "consent":
        person = workflow.set_consent(s, root, args.participant, args.state)
        print(f"Participant {person.label} ({person.code}): {workflow.CONSENT_STATES[args.state]}.")
    elif cmd == "form":
        from mocapapp.core import pdfs

        if args.all:
            codes = [p.code for p in s.active_participants]
            print(pdfs.consent_pdf(Path(args.out), "", info=s.study_info, codes=codes))
        else:
            if not args.participant:
                raise ValueError("Give --participant N, or --all for every participant.")
            print(pdfs.consent_pdf(Path(args.out), s.participant(args.participant).code, info=s.study_info))
    return 0


# ---------- trials ----------

@_guard
def _trial(args: argparse.Namespace) -> int:
    from mocapapp.core import workflow
    from mocapapp.core.readiness import trial_missing, trial_status

    root, s = _open(args)
    cmd = args.trial_command
    if cmd == "add":
        who = args.participant or "previous"
        trial = workflow.add_trial(s, root, participant=None if who == "new" else who, layout=args.layout,
                                   like=args.like, name=args.name or "", date=args.date)
        lay = s.trial_layout(trial.label)
        print(f"Trial {trial.label} added: participant {trial.participant} ({trial.code}), layout "
              f"{lay.name if lay else '-'}, date {trial.date or '-'}.")
        if s.layout_warning(trial.label):
            print(s.layout_warning(trial.label))
            print(f"    -> make a new layout, or: mocapapp trial edit --trial {trial.label} --accept-layout")
        print(f'Next: mocapapp trial videos --session "{root}" --trial {trial.label} --set camNN=FILE ... '
              "(or --suggest), then trial check.")
    elif cmd == "list":
        for t in s.trials:
            lay = s.trial_layout(t.label)
            print(f"{t.label:>3}  {t.name or '-'}  {t.code}  {lay.name if lay else 'no layout'}  {t.date or '-'}  "
                  f"{trial_status(s, root, t.label)}")
        if not s.trials:
            print("No trials yet: mocapapp trial add --session <project>")
    elif cmd == "edit":
        if args.layout:
            workflow.set_trial_layout(s, root, args.trial, args.layout)
        if args.participant:
            workflow.assign_trial(s, root, args.trial, args.participant)
        workflow.set_trial_info(s, root, args.trial, name=args.name, date=args.date)
        if args.accept_layout:
            workflow.accept_layout(s, root, args.trial)
        warning = s.layout_warning(args.trial)
        print(f"Trial {args.trial} saved." + (f"\n{warning}" if warning else ""))
    elif cmd == "duplicate":
        trial = workflow.duplicate_trial(s, root, args.trial)
        print(f"Trial {args.trial} duplicated as trial {trial.label} (same participant, layout and name; choose its "
              "videos).")
    elif cmd == "remove":
        try:
            workflow.remove_empty_trial(s, root, args.trial)
        except workflow.SetupError as exc:
            print(str(exc).replace("Delete it with its data instead (you will be asked to type the pseudonym).",
                                   f'Delete it with its data: mocapapp delete-trial --session "{root}" '
                                   f"--trial {args.trial}"))
            return 1
        print(f"Trial {args.trial} removed.")
    elif cmd == "videos":
        trial = s.trial(args.trial)
        lay = s.trial_layout(args.trial)
        if lay is None:
            raise workflow.SetupError(f"Trial {args.trial} has no layout yet: trial edit --layout L.")
        chosen = {(cam, args.trial): Path(f) for cam, f in _pairs(args.set, "set").items()}
        if args.suggest:
            from mocapapp.core.validate import suggest_trials

            for (cam, label), hit in suggest_trials(s).items():
                if label == args.trial and cam not in trial.videos and (cam, label) not in chosen:
                    chosen[(cam, label)] = hit
        if chosen:
            workflow.set_mapping(s, chosen, root)
            workflow.save_session(s, root)
        for cid in lay.camera_ids():
            f = trial.videos.get(cid)
            print(f"  {cid} ({s.camera(cid).name}): {f or '-'}")
    elif cmd == "check":
        labels = _list(args.trial) or [t.label for t in s.trials if t.raw_present
                                       and s.run_state.trial_status.get(t.label) != "done"]
        if not labels:
            print("No trial needs checking.")
            return 0
        report, plan = workflow.check_trials(s, root, labels, dry_run=args.dry_run)
        ok = _print_issues(report.issues)
        if plan is None or not ok:
            print("\nFix the items above first.")
            return 1
        if args.dry_run:
            print(plan.describe())
        print(f"Trial(s) {', '.join(labels)}: videos checked" + ("" if args.dry_run else " and copied") + ".")
    elif cmd == "status":
        labels = _list(args.trial) or [t.label for t in s.trials]
        ready = True
        for label in labels:
            state = trial_status(s, root, label)
            print(f"trial {label}: {state}")
            if state == "not ready":
                ready = False
                for item in trial_missing(s, root, label):
                    print(f"    - {item}")
        return 0 if ready else 1
    return 0


# ---------- parsers ----------

def _session_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--session", required=True, help="the project folder, or its project file (.mocapproj)")


def register(sub, project_save_show) -> None:
    """Add the project commands to the main parser. project_save_show(prsub) adds 0.1.x's project save/show."""
    proj = sub.add_parser("project", help="projects: new, open, info, import from another project; project files")
    prsub = proj.add_subparsers(dest="project_command", required=True)
    pn = prsub.add_parser("new", help="create an empty project folder")
    pn.add_argument("--out", required=True, help="the folder projects are stored in")
    pn.add_argument("--study", help="study code, e.g. PA26 (used in pseudonyms)")
    pn.add_argument("--trials-per-participant", type=int, default=1)
    pn.add_argument("--file", help="also write a project file (.mocapproj) here")
    pn.set_defaults(func=_project_new)
    po = prsub.add_parser("open", help="show what a project file points to and where the project stands")
    po.add_argument("file")
    po.set_defaults(func=_project_open)
    pi = prsub.add_parser("info", help="where the project stands: cameras, layouts, participants, trials")
    _session_arg(pi)
    pi.set_defaults(func=_project_info)
    pse = prsub.add_parser("settings", help="Session settings: show them, or change the ones given")
    _session_arg(pse)
    pse.add_argument("--study", help="study code for new pseudonyms (existing ones never change)")
    pse.add_argument("--trials-per-participant", type=int)
    pse.add_argument("--notes")
    pse.add_argument("--pose-mode", choices=["lightweight", "balanced", "performance"])
    pse.add_argument("--keypoints", choices=["Body_with_feet", "Whole_body_wrist", "Whole_body"])
    pse.add_argument("--cutoff", type=float, metavar="HZ", help="smoothing cut-off, 1-30 Hz")
    pse.add_argument("--augmentation", choices=["on", "off"], help="LSTM-augmented markers for the kinematics")
    pse.add_argument("--body-model", choices=["full", "simple"])
    pse.add_argument("--overlay", choices=["on", "off"], help="save overlay videos")
    pse.add_argument("--keep-keypoints", choices=["on", "off"], help="keep the 2D keypoint files of finished trials")
    pse.add_argument("--delete-raw-all", choices=["on", "off"],
                     help="delete the raw footage of every participant, also with storage consent (R13)")
    pse.add_argument("--delete-raw-when", choices=["after_processing", "after_export"],
                     help="once the trial is processed, or once its results are exported")
    pse.add_argument("--board", choices=["charuco", "chess"])
    pse.add_argument("--squares", metavar="ACROSSxDOWN", help="squares of the printed board, e.g. 9x6")
    pse.add_argument("--square-mm", type=float)
    pse.set_defaults(func=_project_settings)
    pim = prsub.add_parser("import", help="copy cameras, reference points and layouts from another project "
                                          "(never participants or trials)")
    _session_arg(pim)
    pim.add_argument("--from", dest="source", required=True, help="the other project's folder or project file")
    pim.add_argument("--cameras", action="store_true", help="all its cameras")
    pim.add_argument("--points", action="store_true", help="all its reference-point presets")
    pim.add_argument("--layouts", action="store_true", help="all its layouts (with their cameras)")
    pim.add_argument("--list", action="store_true", help="only show what the other project has")
    pim.set_defaults(func=_project_import)
    project_save_show(prsub)

    cam = sub.add_parser("camera", help="cameras and their lens (intrinsic) calibration")
    csub = cam.add_subparsers(dest="camera_command", required=True)
    for name, helptext in (("add", "a new camera"), ("edit", "change a camera"), ("list", "all cameras"),
                           ("duplicate", "copy a camera (to recalibrate one already used)"),
                           ("delete", "delete a camera that no layout uses"),
                           ("calibrate", "lens calibration from the intrinsic videos")):
        p = csub.add_parser(name, help=helptext)
        _session_arg(p)
        p.set_defaults(func=_camera)
        if name in ("edit", "duplicate", "delete"):
            p.add_argument("--camera", required=True, help="camera id, e.g. cam01")
        if name in ("add", "edit"):
            p.add_argument("--intrinsic", metavar="FILE", help="its intrinsic calibration video (any folder)")
            for opt in CAMERA_OPTIONS:
                p.add_argument(f"--{opt}", help="the folder with its videos" if opt == "folder" else None)
        if name == "calibrate":
            p.add_argument("--camera", action="append", help="only these cameras (default: all with a video)")
            p.add_argument("--every", type=float, default=1.0, help="use one frame every N seconds (default 1)")
            p.add_argument("--force", action="store_true", help="recompute even if a result exists")

    pts = sub.add_parser("points", help="reference-point presets (the measured points for camera positions)")
    ptsub = pts.add_subparsers(dest="points_command", required=True)
    pre = ptsub.add_parser("preset", help="save, list, show or check a preset")
    presub = pre.add_subparsers(dest="preset_command", required=True)
    for name, helptext in (("save", "save points as a preset (CSV label,x,y,z, a Config.toml snippet, or a "
                                    "layout's points)"),
                           ("list", "all presets"), ("show", "print a preset"), ("check", "validate a preset")):
        p = presub.add_parser(name, help=helptext)
        _session_arg(p)
        p.set_defaults(func=_points)
        if name == "save":
            p.add_argument("--name", required=True)
            p.add_argument("--csv")
            p.add_argument("--unit", choices=["m", "cm", "in", "ft"], help="unit of the CSV numbers (default m)")
            p.add_argument("--snippet", help="text file containing object_coords_3d = [[x,y,z], ...]")
            p.add_argument("--layout", help="start from this layout's points")
            p.add_argument("--origin", help="where the origin is, e.g. 'tape cross by the door'")
            p.add_argument("--x-axis", help="what +X points toward, e.g. 'the window'")
            p.add_argument("--y-note", help="optional: your own description of +Y")
        if name in ("show", "check"):
            p.add_argument("--preset", required=True, help="preset id, e.g. P1")
        if name == "show":
            p.add_argument("--format", choices=["toml", "csv"], default="toml")

    lay = sub.add_parser("layout", help="camera layouts: which cameras, where they stood (extrinsic calibration)")
    lsub = lay.add_subparsers(dest="layout_command", required=True)
    for name, helptext in (("new", "a new layout"), ("list", "all layouts and their state"),
                           ("status", "what a layout still needs"), ("edit", "change a layout"),
                           ("duplicate", "same cameras and points, new extrinsic videos"),
                           ("delete", "delete a layout no trial uses"),
                           ("click", "a camera's position from the reference points seen in its extrinsic video")):
        p = lsub.add_parser(name, help=helptext)
        _session_arg(p)
        p.set_defaults(func=_layout)
        if name in ("edit", "duplicate", "delete", "click"):
            p.add_argument("--layout", required=True, help="layout id, e.g. L1")
        if name == "status":
            p.add_argument("--layout")
        if name in ("new", "edit"):
            p.add_argument("--name", required=name == "new")
            p.add_argument("--cameras", action="append", help="camera ids in calibration order, e.g. cam01,cam02")
            p.add_argument("--preset", help="reference points from this preset")
            p.add_argument("--date", help="YYYY-MM-DD the extrinsic videos were recorded (default: read from them)")
        if name == "edit":
            p.add_argument("--video", action="append", metavar="CAM=FILE", help="a camera's extrinsic video")
        if name == "click":
            p.add_argument("--camera", required=True)
            p.add_argument("--points", help="CSV label,u,v of the clicked points (without it: save the frame)")
            p.add_argument("--frame", type=int, default=0)
            p.add_argument("--override", action="store_true",
                           help="accept an error above 2.5 cm (recorded in the project)")

    par = sub.add_parser("participant", help="participants: pseudonyms, height, details, consent")
    pasub = par.add_subparsers(dest="participant_command", required=True)
    for name, helptext in (("add", "a new participant (random pseudonym) with their trials"),
                           ("list", "all participants"), ("set", "pseudonym, height or details"),
                           ("consent", "Not signed yet / Signed, delete footage / Signed, keep footage"),
                           ("form", "consent form PDF")):
        p = pasub.add_parser(name, help=helptext)
        _session_arg(p)
        p.set_defaults(func=_participant)
        if name in ("set", "consent"):
            p.add_argument("--participant", required=True, help="participant number, e.g. 2")
        if name in ("add", "set"):
            p.add_argument("--height", help="measured height, e.g. 1.75 or 175 cm")
        if name == "set":
            p.add_argument("--set", action="append", metavar="KEY=VALUE",
                           help="a detail for all of their trials, e.g. weight=70")
            p.add_argument("--code", help="a new pseudonym")
            p.add_argument("--random", action="store_true", help="a new random pseudonym")
            p.add_argument("--confirm", action="store_true", help="confirm changing a pseudonym already used")
        if name == "consent":
            from mocapapp.core.workflow import CONSENT_STATES

            p.add_argument("--state", required=True, choices=list(CONSENT_STATES))
        if name == "form":
            p.add_argument("--out", required=True)
            p.add_argument("--participant")
            p.add_argument("--all", action="store_true", help="one PDF with a form for every participant")

    tr = sub.add_parser("trial", help="trials: add, videos, check, status")
    tsub = tr.add_subparsers(dest="trial_command", required=True)
    for name, helptext in (("add", "a new trial, pre-filled from the previous one"), ("list", "all trials"),
                           ("edit", "name, date, layout or participant of a trial"),
                           ("duplicate", "a new trial like this one"),
                           ("remove", "remove a trial without videos or results"),
                           ("videos", "show or choose a trial's videos"),
                           ("check", "check and copy trial videos, build the trial folders"),
                           ("status", "ready, or what is missing")):
        p = tsub.add_parser(name, help=helptext)
        _session_arg(p)
        p.set_defaults(func=_trial)
        if name in ("edit", "duplicate", "remove", "videos"):
            p.add_argument("--trial", required=True)
        if name in ("check", "status"):
            p.add_argument("--trial", action="append", help="only these trials (default: all)")
        if name == "add":
            p.add_argument("--participant", help="participant number, 'new', or 'previous' (default: the previous "
                                                 "trial's participant until they have their trials)")
            p.add_argument("--like", help="copy layout (and participant) from this trial (default: the last one)")
        if name in ("add", "edit"):
            p.add_argument("--layout")
            p.add_argument("--name", help="a name or note")
            p.add_argument("--date", help="YYYY-MM-DD it was recorded (default: read from its videos)")
        if name == "edit":
            p.add_argument("--participant")
            p.add_argument("--accept-layout", action="store_true",
                           help="use the layout although it was recorded on another day")
        if name == "videos":
            p.add_argument("--set", action="append", metavar="CAM=FILE", help="a camera's video for this trial")
            p.add_argument("--suggest", action="store_true", help="take the videos named with the trial number")
        if name == "check":
            p.add_argument("--dry-run", action="store_true", help="only show what would be copied or converted")
