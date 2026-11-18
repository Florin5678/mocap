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
    from mocapapp.core.workflow import NewSession, SetupError, check_new_session, create_session

    cams = _pairs(args.cam, "cam")
    if not cams:
        raise SystemExit("Give at least two cameras: --cam cam01=<folder> --cam cam02=<folder>.")
    names = sorted(cams, key=lambda n: (len(n), n))
    expected = [f"cam{i:02d}" for i in range(1, len(names) + 1)]
    if names != expected:
        raise SystemExit(f"Name cameras {', '.join(expected)} (got {', '.join(names)}); "
                         "the order must match the calibration file.")
    picks = {(n, "intrinsic"): Path(f) for n, f in _pairs(args.intrinsic, "intrinsic").items()}
    picks |= {(n, "extrinsic"): Path(f) for n, f in _pairs(args.extrinsic, "extrinsic").items()}
    spec = NewSession(out_root=Path(args.out), n_participants=args.participants,
                      camera_dirs=[Path(cams[n]) for n in names], study=args.study or "",
                      heights=args.heights.split(",") if args.heights else [], picks=picks,
                      trials_per_participant=args.trials_per_participant)
    for issue in check_new_session(spec):
        if issue.level != "error":
            print(issue)
    try:
        root, session = create_session(spec)
    except SetupError as exc:
        print(exc)
        return 1
    print(f"Session created: {root}")
    for p in session.participants:
        trials = ", ".join(t.label for t in session.trials_of(p.label))
        print(f"  participant {p.label}: pseudonym {p.code}, height {p.height_m or 'auto (not recommended)'}, "
              f"trial{'s' if session.trials_per_participant > 1 else ''} {trials}")
    print("Next: mocapapp validate --session", root)
    return 0


def _cmd_session_pseudonym(args: argparse.Namespace) -> int:
    from mocapapp.core.session import load_session
    from mocapapp.core.workflow import SetupError, new_pseudonym, rename_participant

    root = Path(args.session)
    session = load_session(root)
    try:
        session.participant(args.participant)
        person = (new_pseudonym(session, root, args.participant) if args.random
                  else rename_participant(session, root, args.participant, args.set))
    except (SetupError, KeyError) as exc:
        print(exc if isinstance(exc, SetupError) else f"There is no participant {args.participant} in this session.")
        return 1
    print(f"Participant {person.label}: pseudonym {person.code}")
    return 0


def _print_mapping(session, report) -> None:
    print(f"\nTrial mapping ({report.mapping_method}):")
    width = max([len(Path(f).name) for c in session.cameras for f in c.trial_files.values()] + [8])
    print("  trial  pseudonym       " + "  ".join(f"{c.name:<{width}}" for c in session.cameras))
    for t in session.trials:
        cells = []
        for c in session.cameras:
            name = Path(c.trial_files.get(t.label, "-")).name
            flag = "?" if (c.name, t.label) in report.low_confidence else " "
            cells.append(f"{name + flag:<{width}}")
        print(f"  {t.label:<6} {t.code:<15} " + "  ".join(cells))
    if report.needs_mapping_review:
        print("  Check this table. Fix it with: mocapapp map --session <dir> --set cam01:<trial>=<file> ...")


def _frozen(session) -> bool:
    from mocapapp.core.workflow import SetupError, ensure_not_frozen

    try:
        ensure_not_frozen(session)
    except SetupError as exc:
        print(exc)
        return True
    return False


def _imported(session) -> bool:
    """Sessions with an imported calibration (the sample) have their videos in place: nothing to check or copy."""
    if session.calibration_source != "imported":
        return False
    print("This session uses an imported calibration and its videos are already in place (for example, the "
          "sample session). Checking or copying videos is not needed; continue with 'mocapapp run'.")
    return True


def _cmd_validate(args: argparse.Namespace) -> int:
    from mocapapp.core.session import load_session, save_session
    from mocapapp.core.validate import validate

    root = Path(args.session)
    session = load_session(root)
    if _frozen(session):
        return 1
    if _imported(session):
        return 0
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
    if _frozen(session):
        return 1
    if _imported(session):
        return 0
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
    from mocapapp.core.session import load_session, save_session
    from mocapapp.core.workflow import SetupError, pick_video

    root = Path(args.session)
    session = load_session(root)
    try:
        pick_video(session, args.camera, args.role, Path(args.file))
    except SetupError as exc:
        raise SystemExit(str(exc)) from exc
    save_session(session, root)
    print(f"{args.camera} {args.role} = {Path(args.file).resolve()}. Run `mocapapp validate` again.")
    return 0


def _cmd_assemble(args: argparse.Namespace) -> int:
    from mocapapp.core.assemble import assemble_trials, plan_inputs, prepare_inputs
    from mocapapp.core.session import load_session, save_session
    from mocapapp.core.validate import validate

    root = Path(args.session)
    session = load_session(root)
    if _frozen(session):
        return 1
    if _imported(session):
        return 0
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
    labels = [args.trial] if args.trial else [t.label for t in session.trials if t.raw_present]
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
    qa = {t.label: trial_qa(session, root, t.label).light for t in session.active_trials
          if not labels or t.label in labels}
    try:
        out, warnings = export(session, root, selection, labels, qa)
    except ValueError as exc:
        print(exc)
        return 1
    for w in warnings:
        print(f"! {w}")
    print(f"Exported to {out}")
    return 0


def _cmd_pdf(args: argparse.Namespace) -> int:
    from mocapapp.core import pdfs

    if args.pdf_command == "board":
        from mocapapp.core.calibration.intrinsics import BoardSpec

        spec = BoardSpec(args.type, (args.rows - 1, args.cols - 1), args.square_mm, args.marker_bits)
        try:
            print(pdfs.board_pdf(Path(args.out), spec))
        except pdfs.PdfError as exc:
            print(exc)
            return 1
    else:
        from mocapapp.core.session import load_session

        session = load_session(Path(args.session)) if args.session else None
        if args.all and session is None:
            raise SystemExit("--all needs --session: the forms are made for that session's participants.")
        codes = [p.code for p in session.active_participants] if args.all else None
        print(pdfs.consent_pdf(Path(args.out), args.code or "", info=session.study_info if session else None,
                               codes=codes))
    return 0


def _cmd_sample(args: argparse.Namespace) -> int:
    from mocapapp.core.sample import SampleError, make_sample_session

    try:
        root, _ = make_sample_session(Path(args.out))
    except SampleError as exc:
        print(exc)
        return 1
    print(f"Sample session ready: {root}")
    print(f'Next: mocapapp confirm --session "{root}" --sync-cue; mocapapp consent --session "{root}" --trial 1 '
          f'--attest; mocapapp run --session "{root}"')
    return 0


def _delete(args: argparse.Namespace, scope: str) -> int:
    from mocapapp.core import deletion
    from mocapapp.core.session import load_session

    root = Path(args.session)
    session = load_session(root)
    try:
        if scope == "raw":
            plan = deletion.plan_raw(session, root, args.trial, args.include_originals, after_run=args.after_run)
        elif args.trial:
            plan = deletion.plan_participant(session, root, args.trial, args.include_originals)
        else:
            plan = deletion.plan_session(session, root, args.include_originals)
    except deletion.DeletionError as exc:
        print(exc)
        return 1
    print(plan.describe())
    if not args.yes:
        confirm = f' --confirm "{plan.confirm_text}"' if plan.confirm_text else ""
        print(f"\nNothing was deleted (dry run). Add --yes{confirm} to delete.")
        return 0
    try:
        result = deletion.execute(plan, session, args.confirm)
    except deletion.DeletionError as exc:
        print(f"\n{exc}")
        return 1
    print(f"\nDeleted {result.deleted} item(s), {deletion.human_size(result.bytes)}.")
    for f in result.failures:
        print(f"Could not delete {f}")
    print("\nThe app cannot do these for you - please check:")
    for line in result.checklist:
        print(f"  [ ] {line}")
    return 1 if result.failures else 0


def _cmd_delete_raw(args: argparse.Namespace) -> int:
    return _delete(args, "raw")


def _cmd_delete_session(args: argparse.Namespace) -> int:
    return _delete(args, "participant")


def _cmd_reminders(args: argparse.Namespace) -> int:
    from mocapapp.core.deletion import session_reminders
    from mocapapp.core.session import SESSION_FILE

    base = Path(args.root)
    roots = [base] if (base / SESSION_FILE).is_file() else sorted(p.parent for p in base.glob(f"*/{SESSION_FILE}"))
    found = 0
    for root in roots:
        for line in session_reminders(root):
            print(f"{root.name}: {line}")
            found += 1
    if not found:
        print("No raw footage waiting to be deleted.")
    return 0


def _cmd_models(args: argparse.Namespace) -> int:
    from mocapapp.core import models

    modes = models.MODES if args.mode == "all" else (args.mode,)
    kp = args.keypoints
    for m in models.models(modes, kp):
        print(f"[{'OK  ' if m.ready else 'MISS'}] {m.mode} {m.role}: {m.onnx.name}")
    gone = models.missing(modes, kp)
    if not gone:
        print("Everything needed is downloaded; processing works offline.")
        return 0
    if not args.download:
        size = f" (about {sum(models.SIZES_MB[m] for m in modes)} MB, once)" if kp == "Body_with_feet" else ""
        print(f"Missing {len(gone)} model(s). Download them{size} with --download.")
        return 1
    try:
        models.download(modes, keypoints=kp)
    except OSError as exc:
        print(exc)
        return 1
    return 0


def _cmd_diagnostics(args: argparse.Namespace) -> int:
    from mocapapp.core.diagnostics import diagnostics_text

    print(diagnostics_text())
    return 0


def _cmd_gui(args: argparse.Namespace) -> int:
    from mocapapp.gui.app import run_gui

    return run_gui(Path(args.session) if args.session else None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mocapapp", description="Markerless mocap with Pose2Sim (research use only).")
    from mocapapp import __version__

    parser.add_argument("--version", action="version", version=f"mocapapp {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="check that everything needed is installed")
    doctor.add_argument("--dir", help="folder where sessions will be stored (for the disk-space check)")
    doctor.set_defaults(func=_cmd_doctor)

    mod = sub.add_parser("models", help="check or download the pose-estimation models (needed once, then offline)")
    mod.add_argument("--mode", choices=["lightweight", "balanced", "performance", "all"], default="balanced")
    mod.add_argument("--keypoints", choices=["Body_with_feet", "Whole_body_wrist", "Whole_body"],
                     default="Body_with_feet", help="keypoint set (Whole_body includes hands and face)")
    mod.add_argument("--download", action="store_true", help="download what is missing")
    mod.set_defaults(func=_cmd_models)

    diag = sub.add_parser("diagnostics", help="versions and checks to paste into a bug report (no personal data)")
    diag.set_defaults(func=_cmd_diagnostics)

    gui = sub.add_parser("gui", help="open the app window")
    gui.add_argument("--session", help="open this session directly")
    gui.set_defaults(func=_cmd_gui)

    pdf = sub.add_parser("pdf", help="printable calibration board and consent form")
    psub = pdf.add_subparsers(dest="pdf_command", required=True)
    pb = psub.add_parser("board", help="calibration board PDF (print at 100 %%)")
    pb.add_argument("--out", required=True)
    pb.add_argument("--type", choices=["charuco", "chess"], default="charuco")
    pb.add_argument("--cols", type=int, default=9, help="squares across (default 9)")
    pb.add_argument("--rows", type=int, default=6, help="squares down (default 6)")
    pb.add_argument("--square-mm", type=float, default=25.0)
    pb.add_argument("--marker-bits", type=int, default=4, help=argparse.SUPPRESS)
    pc = psub.add_parser("consent", help="fillable consent form template")
    pc.add_argument("--out", required=True)
    pc.add_argument("--pseudonym", "--code", dest="code", help="pre-fill this pseudonym")
    pc.add_argument("--session", help="fill in the study details saved in this session folder")
    pc.add_argument("--all", action="store_true", help="one PDF with a form for every participant of --session")
    for p in (pb, pc):
        p.set_defaults(func=_cmd_pdf)

    sample = sub.add_parser("sample", help="create a ready-to-run session from the bundled sample data")
    sample.add_argument("--out", required=True)
    sample.set_defaults(func=_cmd_sample)

    session = sub.add_parser("session", help="create sessions")
    ssub = session.add_subparsers(dest="session_command", required=True)
    new = ssub.add_parser("new", help="create a new session folder")
    new.add_argument("--study", default="", help="optional study label, A-Z/0-9, up to 8 characters")
    new.add_argument("--participants", "--trials", dest="participants", type=int, required=True,
                     help="number of participants")
    new.add_argument("--trials-per-participant", type=int, default=1,
                     help="trials recorded per participant (default 1); at most 10 trials in total")
    new.add_argument("--heights", help="participant heights in participant order, metres or cm, comma separated")
    new.add_argument("--cam", action="append", metavar="camNN=FOLDER", help="camera folder; repeat per camera")
    new.add_argument("--intrinsic", action="append", metavar="camNN=FILE", help="intrinsic video outside the folder")
    new.add_argument("--extrinsic", action="append", metavar="camNN=FILE", help="extrinsic video outside the folder")
    new.add_argument("--out", required=True, help="output root; the session folder is created inside it")
    new.set_defaults(func=_cmd_session_new)
    pn = ssub.add_parser("pseudonym", help="change a participant's pseudonym (before consent and results)")
    pn.add_argument("--session", required=True)
    pn.add_argument("--participant", required=True, help="participant number, e.g. 2")
    group = pn.add_mutually_exclusive_group(required=True)
    group.add_argument("--set", metavar="PSEUDONYM", help="the new pseudonym (letters, digits, - or _)")
    group.add_argument("--random", action="store_true", help="a new random pseudonym")
    pn.set_defaults(func=_cmd_session_pseudonym)

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
    cons.add_argument("--attest", action="store_true", help="signed consent received for this trial's pseudonym")
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

    dr = sub.add_parser("delete-raw", help="delete a trial's raw footage, overlay videos and 2D keypoints "
                                           "(dry run unless --yes)")
    dr.add_argument("--trial", required=True)
    dr.add_argument("--after-run", action="store_true",
                    help="the deletion chosen at setup: refuse unless results are complete and exported")
    dr.set_defaults(func=_cmd_delete_raw)
    ds = sub.add_parser("delete-session", help="delete all participant data: one trial (--trial) or the whole "
                                               "session (dry run unless --yes)")
    ds.add_argument("--trial", help="only this participant (withdrawal); omit to delete the whole session")
    ds.set_defaults(func=_cmd_delete_session)
    for p in (dr, ds):
        p.add_argument("--session", required=True)
        p.add_argument("--include-originals", action="store_true",
                       help="also delete the original recordings in the camera folders")
        p.add_argument("--yes", action="store_true", help="really delete (otherwise only list what would go)")
        p.add_argument("--confirm", help="the pseudonym (or session folder name) when asked to type it")

    rem = sub.add_parser("reminders", help="list sessions whose raw footage is still stored")
    rem.add_argument("--root", required=True, help="output folder holding sessions, or one session folder")
    rem.set_defaults(func=_cmd_reminders)

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
