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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mocapapp", description="Markerless mocap with Pose2Sim (research use only).")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="check that everything needed is installed")
    doctor.add_argument("--dir", help="folder where sessions will be stored (for the disk-space check)")
    doctor.set_defaults(func=_cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
