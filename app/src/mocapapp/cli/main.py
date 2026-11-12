"""Command line entry point: `mocapapp <command>`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _cmd_doctor(args: argparse.Namespace) -> int:
    from mocapapp.core.env import run_checks

    checks = run_checks(Path(args.dir) if args.dir else None)
    for c in checks:
        mark = "OK  " if c.ok else "FAIL"
        print(f"[{mark}] {c.name}: {c.detail}")
        if not c.ok and c.fix:
            print(f"       -> {c.fix}")
    return 0 if all(c.ok for c in checks) else 1


def _pairs(values: list[str] | None, what: str) -> dict[str, str]:
    out = {}
    for v in values or []:
        key, sep, val = v.partition("=")
        if not sep or not key or not val:
            raise SystemExit(f"--{what} expects NAME=VALUE, got '{v}'.")
        out[key.strip()] = val.strip()
    return out


def _cmd_session_new(args: argparse.Namespace) -> int:
    from mocapapp.core.privacy import make_code, normalise_study
    from mocapapp.core.session import (
        CalibVideo,
        Camera,
        Session,
        Trial,
        VideoRef,
        existing_codes,
        parse_height,
        save_session,
        session_dir_name,
    )
    from mocapapp.core.validate import check_output_root

    study = normalise_study(args.study)
    cams = _pairs(args.cam, "cam")
    if not cams:
        raise SystemExit("Give at least two cameras: --cam cam01=<folder> --cam cam02=<folder>.")
    names = sorted(cams, key=lambda n: (len(n), n))
    expected = [f"cam{i:02d}" for i in range(1, len(names) + 1)]
    if names != expected:
        raise SystemExit(f"Name cameras {', '.join(expected)} (got {', '.join(names)}); "
                         "the order must match the calibration file.")
    heights = [h for h in (args.heights or "").split(",")] if args.heights else []
    if heights and len(heights) != args.trials:
        raise SystemExit(f"--heights has {len(heights)} values but --trials is {args.trials}.")
    try:
        parsed = [parse_height(h) for h in heights] or [None] * args.trials
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    out_root = Path(args.out)
    for issue in check_output_root(out_root):
        print(issue)
        if issue.level == "error":
            return 1
    root = out_root / session_dir_name(study)
    if root.exists():
        raise SystemExit(f"{root} already exists; wait a minute or choose another output folder.")
    existing = existing_codes(out_root)
    codes: list[str] = []
    for _ in range(args.trials):
        codes.append(make_code(study, existing | set(codes)))
    intr, extr = _pairs(args.intrinsic, "intrinsic"), _pairs(args.extrinsic, "extrinsic")
    session = Session(
        study_code=study, n_trials=args.trials,
        trials=[Trial(label=str(i + 1), code=c, height_m=h)
                for i, (c, h) in enumerate(zip(codes, parsed, strict=True))],
        cameras=[Camera(name=n, source_dir=str(Path(cams[n]).resolve()),
                        intrinsic=CalibVideo(file=str(Path(intr[n]).resolve()), origin="manual") if n in intr else None,
                        extrinsic=VideoRef(file=str(Path(extr[n]).resolve()), origin="manual") if n in extr else None)
                 for n in names],
    )
    _fill_versions(session)
    print(f"Session created: {save_session(session, root).parent}")
    for t in session.trials:
        print(f"  trial {t.label}: code {t.code}, height {t.height_m or 'auto (not recommended)'}")
    print("Next: mocapapp validate --session", root)
    return 0


def _fill_versions(session) -> None:
    import subprocess

    import Pose2Sim

    repo = Path(Pose2Sim.__file__).parents[1]
    proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                          check=False)
    session.pose2sim_commit = proc.stdout.strip() or None
    try:
        import opensim

        session.opensim_version = opensim.GetVersion()
    except Exception:  # OpenSim missing is reported by `doctor`
        session.opensim_version = None


def _print_mapping(session, report) -> None:
    print(f"\nTrial mapping ({report.mapping_method}):")
    width = max([len(Path(f).name) for c in session.cameras for f in c.trial_files.values()] + [8])
    print("  trial  code            " + "  ".join(f"{c.name:<{width}}" for c in session.cameras))
    for t in session.trials:
        cells = []
        for c in session.cameras:
            name = Path(c.trial_files.get(t.label, "-")).name
            flag = "?" if (c.name, t.label) in report.low_confidence else " "
            cells.append(f"{name + flag:<{width}}")
        print(f"  {t.label:<6} {t.code:<15} " + "  ".join(cells))
    if report.needs_mapping_review:
        print("  Check this table. Fix it with: mocapapp map --session <dir> --set cam01:<trial>=<file> ...")


def _cmd_validate(args: argparse.Namespace) -> int:
    from mocapapp.core.session import load_session, save_session
    from mocapapp.core.validate import validate

    root = Path(args.session)
    session = load_session(root)
    report = validate(session, root)
    for issue in report.issues:
        print(issue)
    if report.mapping_method:
        _print_mapping(session, report)
    save_session(session, root)
    print("\nValidation passed." if report.ok else "\nValidation found problems that must be fixed (see above).")
    return 0 if report.ok else 1


def _cmd_map(args: argparse.Namespace) -> int:
    from mocapapp.core.session import load_session, save_session

    root = Path(args.session)
    session = load_session(root)
    for item in args.set:
        left, sep, file = item.partition("=")
        cam, sep2, label = left.partition(":")
        if not (sep and sep2):
            raise SystemExit(f"--set expects cam01:<trial>=<file>, got '{item}'.")
        if not Path(file).is_file():
            raise SystemExit(f"{file} does not exist.")
        session.camera(cam).trial_files[label] = str(Path(file).resolve())
    for t in session.trials:
        t.mapping_method = "manual"
    save_session(session, root)
    print("Mapping saved as manual. Run `mocapapp validate` again.")
    return 0


def _cmd_pick(args: argparse.Namespace) -> int:
    from mocapapp.core.session import CalibVideo, VideoRef, load_session, save_session

    root = Path(args.session)
    session = load_session(root)
    path = Path(args.file).resolve()
    if not path.is_file():
        raise SystemExit(f"{path} does not exist.")
    cls = CalibVideo if args.role == "intrinsic" else VideoRef
    setattr(session.camera(args.camera), args.role, cls(file=str(path), origin="manual"))
    save_session(session, root)
    print(f"{args.camera} {args.role} = {path}. Run `mocapapp validate` again.")
    return 0


def _cmd_assemble(args: argparse.Namespace) -> int:
    from mocapapp.core.assemble import assemble_trials, plan_inputs, prepare_inputs
    from mocapapp.core.session import load_session, save_session
    from mocapapp.core.validate import validate

    root = Path(args.session)
    session = load_session(root)
    report = validate(session, root)
    if not report.ok:
        for issue in report.issues:
            print(issue)
        print("\nFix these problems first (mocapapp validate).")
        return 1
    plan = plan_inputs(session, root, report)
    print(plan.describe())
    if args.dry_run:
        return 0
    prepare_inputs(plan, report)
    if args.command == "assemble":
        assemble_trials(session, root)
    save_session(session, root)
    return 0


def _print_issues(issues) -> bool:
    for issue in issues:
        print(issue)
    return not any(i.level in ("error", "action") for i in issues)


def _cmd_scene(args: argparse.Namespace) -> int:
    from mocapapp.core.calibration import scene_points as sp
    from mocapapp.core.calibration.service import get_scene, set_scene

    root = Path(args.session)
    if args.scene_command == "set":
        try:
            scene = get_scene(root)
        except Exception:
            scene = sp.Scene()
        if args.csv:
            scene.points = sp.from_csv(Path(args.csv).read_text(encoding="utf-8-sig"))
        if args.snippet:
            scene.points = sp.from_snippet(Path(args.snippet).read_text(encoding="utf-8"))
        if args.origin is not None:
            scene.origin_text = args.origin
        if args.x_axis is not None:
            scene.x_axis_text = args.x_axis
        if args.y_note is not None:
            scene.y_axis_note = args.y_note
        removed = set_scene(root, scene)
        if removed:
            print("Reference points changed: removed " + ", ".join(removed)
                  + ". Click the points again for each camera.")
        print(f"{len(scene.points)} reference points saved.")
    scene = get_scene(root)
    if args.scene_command == "show":
        print(sp.axes_description(scene))
        print()
        print(sp.to_csv(scene.points) if args.format == "csv" else scene.as_config_snippet())
        return 0
    ok = _print_issues(sp.check_scene(scene))
    print("\nReference points OK." if ok else "\nFix the reference points (see above).")
    return 0 if ok else 1


def _cmd_calibrate(args: argparse.Namespace) -> int:
    from mocapapp.core.calibration import extrinsics as ex
    from mocapapp.core.calibration import service
    from mocapapp.core.session import load_session, save_session

    root = Path(args.session)
    session = load_session(root)
    try:
        if args.calib_command == "intrinsics":
            cams = [args.camera] if args.camera else None
            for r in service.run_intrinsics(session, root, cams, args.every, args.force):
                print(f"{r.camera}: {r.rms_px:.3f} px from {r.n_used}/{r.n_frames} frames, coverage {r.coverage:.0%}, "
                      f"tilt spread {r.tilt_spread_deg:.0f} deg, {r.layout} -> {r.status.upper()}")
                for a in r.advice:
                    print(f"    -> {a}")
        elif args.calib_command == "frame":
            print(service.export_frame(root, args.camera, args.frame))
            print("Measure the pixel position (u = from left, v = from top) of each visible reference point and "
                  "write label,u,v lines to a CSV ('hidden' for points this camera cannot see).")
        elif args.calib_command == "extrinsics":
            clicks = ex.read_clicks_csv(Path(args.points).read_text(encoding="utf-8-sig"))
            r = service.run_extrinsics(session, root, args.camera, clicks, args.frame, args.override)
            pos = ", ".join(f"{v:.2f}" for v in r.camera_position)
            print(f"{r.camera}: {r.rms_px:.2f} px = {r.rms_cm:.2f} cm from {r.n_points} points; camera at ({pos}) m "
                  f"-> {r.status.upper()}{' (overridden)' if r.overridden else ''}")
            for w in r.warnings:
                print(f"    -> {w}")
            if r.status == "fail" and not r.overridden:
                print(f"    Above {ex.WARN_CM} cm this camera blocks the run. "
                      "Fix the points, or accept with --override.")
        elif args.calib_command == "status":
            for cam, state in service.calibration_state(session, root).items():
                print(f"{cam}: {state}")
            return 0
        if args.calib_command in ("intrinsics", "extrinsics"):
            state = service.calibration_state(session, root)
            if all(s == "ready" for s in state.values()):
                print(f"All cameras calibrated: {service.write_calibration(session, root)}")
    except (service.CalibrationStepError, ex.ExtrinsicsError, ValueError) as exc:
        print(exc)
        return 1
    finally:
        save_session(session, root)
    return 0


def _cmd_confirm(args: argparse.Namespace) -> int:
    from mocapapp.core.session import load_session, save_session

    root = Path(args.session)
    session = load_session(root)
    session.sync_cue_confirmed = bool(args.sync_cue)
    save_session(session, root)
    print("Sync cue confirmed for every trial." if args.sync_cue else "Nothing confirmed.")
    return 0


def _cmd_consent(args: argparse.Namespace) -> int:
    from datetime import datetime

    from mocapapp.core.session import load_session, save_session

    root = Path(args.session)
    session = load_session(root)
    trial = session.trial(args.trial)
    if args.attest:
        trial.consent.attested = True
        trial.consent.attested_at = datetime.now().isoformat(timespec="seconds")
    if args.raw_storage is not None:
        trial.consent.raw_footage_storage = args.raw_storage == "yes"
        trial.delete_raw = not trial.consent.raw_footage_storage  # default follows consent (§6.2)
    save_session(session, root)
    print(f"Trial {trial.label} ({trial.code}): consent attested={trial.consent.attested}, "
          f"raw footage storage={trial.consent.raw_footage_storage}, delete raw after run={trial.delete_raw}.")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    import threading

    from mocapapp.core import runner
    from mocapapp.core.session import load_session

    root = Path(args.session)
    session = load_session(root)
    labels = [args.trial] if args.trial else [t.label for t in session.trials]
    if args.fix_sync:
        if not args.trial:
            raise SystemExit("--fix-sync needs --trial.")
        ok = runner.fix_sync(session, root, args.trial, _printer(args.verbose))
        return 0 if ok else 1
    issues = runner.preflight(session, root, labels)
    if not _print_issues(issues):
        print("\nFix the items above before running.")
        return 1
    est = runner.estimate_seconds(session, root, labels)
    print(f"Rough time estimate on CPU: {est / 60:.0f} min. You can stop with Ctrl+C and resume later.\n")
    cancel = threading.Event()
    try:
        results = runner.run_session(session, root, labels, _printer(args.verbose), cancel, resume=not args.restart,
                                     accept_sync=args.accept_sync)
    except (KeyboardInterrupt, runner.Cancelled):
        cancel.set()
        print("\nStopped. Run the same command again to resume.")
        return 130
    print()
    for label, status in results.items():
        msg = session.run_state.errors.get(label) or "; ".join(session.run_state.sync.get(label, {}).get("issues", []))
        print(f"trial {label}: {status}" + (f" - {msg}" if msg and status != "done" else ""))
        if status == "needs_sync_fix":
            print(f"    -> check it with: mocapapp run --session {root} --trial {label} --fix-sync"
                  f"   (or accept as is with --accept-sync)")
    from mocapapp.core.qa import write_report

    report, _ = write_report(session, root, labels)
    print(f"\nQuality report: {report}")
    return 0 if all(s == "done" for s in results.values()) else 1


def _printer(verbose: bool):
    from mocapapp.core.runner import Event

    last = {"pct": -1}

    def show(e: Event) -> None:
        if e.kind == "stage_start":
            print(f"[trial {e.trial}] {e.message} ...", flush=True)
        elif e.kind == "stage_end":
            print(f"[trial {e.trial}]   {'done' if e.ok else 'FAILED'}: {e.message}", flush=True)
        elif e.kind == "progress" and e.fraction is not None:
            pct = int(e.fraction * 100)
            if pct // 5 != last["pct"] // 5:
                last["pct"] = pct
                print(f"[trial {e.trial}]   {pct}% of this trial", flush=True)
        elif e.kind == "needs_sync_fix":
            print(f"[trial {e.trial}]   synchronisation needs checking: {e.message}", flush=True)
        elif e.kind == "line" and verbose:
            print(f"    {e.message}", flush=True)

    return show


def _cmd_qa(args: argparse.Namespace) -> int:
    from mocapapp.core.qa import LIGHT_TEXT, write_report
    from mocapapp.core.session import load_session

    root = Path(args.session)
    session = load_session(root)
    path, results = write_report(session, root, [args.trial] if args.trial else None)
    for r in results:
        print(f"trial {r.label} ({r.code}): {LIGHT_TEXT[r.light]} [{r.status}]")
        for m in r.metrics:
            print(f"    {m.name}: {m.value} {m.unit} -> {LIGHT_TEXT[m.light]}")
        for w in r.warnings:
            print(f"    ! {w}")
    print(f"Report: {path}")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    from mocapapp.core.export import default_selection, export
    from mocapapp.core.qa import trial_qa
    from mocapapp.core.session import load_session

    root = Path(args.session)
    session = load_session(root)
    selection = args.select.split(",") if args.select else default_selection(session.settings.filter.type)
    labels = [args.trial] if args.trial else None
    qa = {t.label: trial_qa(session, root, t.label).light for t in session.trials if not labels or t.label in labels}
    try:
        out, warnings = export(session, root, selection, labels, qa)
    except ValueError as exc:
        print(exc)
        return 1
    for w in warnings:
        print(f"! {w}")
    print(f"Exported to {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mocapapp", description="Markerless mocap with Pose2Sim (research use only).")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="check that everything needed is installed")
    doctor.add_argument("--dir", help="folder where sessions will be stored (for the disk-space check)")
    doctor.set_defaults(func=_cmd_doctor)

    session = sub.add_parser("session", help="create sessions")
    ssub = session.add_subparsers(dest="session_command", required=True)
    new = ssub.add_parser("new", help="create a new session folder")
    new.add_argument("--study", default="", help="optional study label, A-Z/0-9, up to 8 characters")
    new.add_argument("--trials", type=int, required=True, help="number of trials (one participant each), 1-10")
    new.add_argument("--heights", help="participant heights in trial order, metres or cm, comma separated")
    new.add_argument("--cam", action="append", metavar="camNN=FOLDER", help="camera folder; repeat per camera")
    new.add_argument("--intrinsic", action="append", metavar="camNN=FILE", help="intrinsic video outside the folder")
    new.add_argument("--extrinsic", action="append", metavar="camNN=FILE", help="extrinsic video outside the folder")
    new.add_argument("--out", required=True, help="output root; the session folder is created inside it")
    new.set_defaults(func=_cmd_session_new)

    val = sub.add_parser("validate", help="check inputs and show the trial mapping")
    val.add_argument("--session", required=True)
    val.set_defaults(func=_cmd_validate)

    prep = sub.add_parser("prepare", help="convert/copy all videos into the session (needed before calibration)")
    prep.add_argument("--session", required=True)
    prep.add_argument("--dry-run", action="store_true", help="only show what would be copied or converted")
    prep.set_defaults(func=_cmd_assemble)

    scene = sub.add_parser("scene", help="reference points for camera-position calibration")
    scsub = scene.add_subparsers(dest="scene_command", required=True)
    sset = scsub.add_parser("set", help="set points (CSV label,x,y,z in metres, or a Config.toml snippet) and axes")
    sset.add_argument("--session", required=True)
    sset.add_argument("--csv")
    sset.add_argument("--snippet", help="text file containing object_coords_3d = [[x,y,z], ...]")
    sset.add_argument("--origin", help="where the origin is, e.g. 'tape cross by the door'")
    sset.add_argument("--x-axis", help="what +X points toward, e.g. 'the window'")
    sset.add_argument("--y-note", help="optional: your own description of +Y")
    for name, helptext in (("show", "print axes and points"), ("check", "validate the points")):
        p = scsub.add_parser(name, help=helptext)
        p.add_argument("--session", required=True)
        if name == "show":
            p.add_argument("--format", choices=["toml", "csv"], default="toml")
    for p in scsub.choices.values():
        p.set_defaults(func=_cmd_scene)

    cal = sub.add_parser("calibrate", help="lens (intrinsic) and position (extrinsic) calibration")
    csub = cal.add_subparsers(dest="calib_command", required=True)
    ci = csub.add_parser("intrinsics", help="calibrate lenses from the intrinsic videos")
    ci.add_argument("--camera")
    ci.add_argument("--every", type=float, default=1.0, help="use one frame every N seconds (default 1)")
    ci.add_argument("--force", action="store_true", help="recompute even if a result exists")
    cf = csub.add_parser("frame", help="save a frame of the extrinsic video as PNG for clicking")
    cf.add_argument("--camera", required=True)
    cf.add_argument("--frame", type=int, default=0)
    ce = csub.add_parser("extrinsics", help="camera position from clicked points (CSV label,u,v)")
    ce.add_argument("--camera", required=True)
    ce.add_argument("--points", required=True)
    ce.add_argument("--frame", type=int, default=0)
    ce.add_argument("--override", action="store_true", help="accept an error above 2.5 cm (recorded in the session)")
    cs = csub.add_parser("status", help="what is calibrated and what is missing")
    for p in (ci, cf, ce, cs):
        p.add_argument("--session", required=True)
        p.set_defaults(func=_cmd_calibrate)

    conf = sub.add_parser("confirm", help="pre-run confirmations")
    conf.add_argument("--session", required=True)
    conf.add_argument("--sync-cue", action="store_true", help="I recorded a sync cue at the start of every trial")
    conf.set_defaults(func=_cmd_confirm)

    cons = sub.add_parser("consent", help="record that signed consent was received for a trial's participant")
    cons.add_argument("--session", required=True)
    cons.add_argument("--trial", required=True)
    cons.add_argument("--attest", action="store_true", help="signed consent received for this trial's code")
    cons.add_argument("--raw-storage", choices=["yes", "no"], help="participant consented to keeping raw footage")
    cons.set_defaults(func=_cmd_consent)

    run = sub.add_parser("run", help="run Pose2Sim on every trial (resumes by default)")
    run.add_argument("--session", required=True)
    run.add_argument("--trial")
    run.add_argument("--restart", action="store_true", help="ignore finished stages and start over")
    run.add_argument("--accept-sync", action="store_true", help="continue even if synchronisation looks unreliable")
    run.add_argument("--fix-sync", action="store_true", help="open the synchronisation player for --trial")
    run.add_argument("--verbose", action="store_true", help="show Pose2Sim's own output")
    run.set_defaults(func=_cmd_run)

    qa = sub.add_parser("qa", help="quality report (HTML + JSON) and 3D previews")
    qa.add_argument("--session", required=True)
    qa.add_argument("--trial")
    qa.set_defaults(func=_cmd_qa)

    exp = sub.add_parser("export", help="copy selected results to export/ with README and manifest")
    exp.add_argument("--session", required=True)
    exp.add_argument("--trial")
    exp.add_argument("--select", help="comma list, e.g. config,trc.filt_lstm,c3d.filt_lstm,osim,mot,bvh "
                                      "(default: the ticked-by-default items)")
    exp.set_defaults(func=_cmd_export)

    mp = sub.add_parser("map", help="set the trial mapping by hand")
    mp.add_argument("--session", required=True)
    mp.add_argument("--set", action="append", required=True, metavar="camNN:TRIAL=FILE")
    mp.set_defaults(func=_cmd_map)

    pick = sub.add_parser("pick", help="choose a calibration video from any folder")
    pick.add_argument("--session", required=True)
    pick.add_argument("--camera", required=True)
    pick.add_argument("--role", choices=["intrinsic", "extrinsic"], required=True)
    pick.add_argument("--file", required=True)
    pick.set_defaults(func=_cmd_pick)

    asm = sub.add_parser("assemble", help="convert videos and build one Pose2Sim project per trial")
    asm.add_argument("--session", required=True)
    asm.add_argument("--dry-run", action="store_true", help="only show what would be copied or converted")
    asm.set_defaults(func=_cmd_assemble)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "trials", None) is not None and not 1 <= args.trials <= 10:
        raise SystemExit("--trials must be between 1 and 10.")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
