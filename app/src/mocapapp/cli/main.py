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
    assemble_trials(session, root)
    save_session(session, root)
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
